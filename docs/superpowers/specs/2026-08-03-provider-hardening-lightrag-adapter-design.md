# Provider Hardening and LightRAG–Docling Compatibility

## 1. Context

Atlas exposes Docling and Parakeet as model-backed HTTP providers. Their
conversion and transcription routes currently have four related defects:

1. expensive routes accept unauthenticated requests;
2. container ports bind to every host interface when `HOST_BIND_IP` is empty;
3. uploads are spooled before inference admission, allowing an unbounded set of
   queued temporary files; and
4. native inference runs in threads without a killable completion deadline, so
   one hung CUDA, MPS, NeMo, or Docling call can retain the only permit forever.

There is also a separate compatibility defect. Atlas passes its custom Docling
endpoint to LightRAG v1.5.4, but the pinned LightRAG client calls the Docling
Serve asynchronous submit, poll, and zip-result API. Atlas exposes only the
synchronous `/v1/document/convert` route. The pinned client does not provide a
Docling authentication-header setting, so simply requiring a bearer token on
the existing endpoint would preserve a broken integration and add a second
failure mode.

## 2. Goals and non-goals

### 2.1 Goals

- Require generated bearer credentials on all model-provider routes except
  health checks.
- Reject excess expensive work before multipart parsing or temporary-file
  creation.
- Bound inference and lazy model-loading time, and terminate native work that
  cannot be cancelled safely.
- Make published provider ports loopback-only unless the operator explicitly
  supplies a different `HOST_BIND_IP`.
- Implement the exact asynchronous Docling contract used by pinned LightRAG
  without exposing an unauthenticated provider route to the shared backend
  network or host.
- Preserve Backend's synchronous Docling contract and the OpenAI-compatible
  Parakeet contract.
- Keep generated secrets stable across subsequent bootstrapper runs.

### 2.2 Non-goals

- Replacing Docling, Parakeet, LightRAG, or their pinned base images.
- Adding a general-purpose job queue or external broker.
- Supporting arbitrary Docling Serve endpoints beyond the subset exercised by
  LightRAG v1.5.4.
- Changing model selection, output quality, chunking semantics, or provider
  concurrency defaults.
- Treating an asyncio timeout as cancellation of a native inference thread.

## 3. Selected architecture

### 3.1 Authenticated provider boundary

Docling and Parakeet each receive an independently generated secret:

- `DOCLING_API_TOKEN`
- `PARAKEET_API_TOKEN`

Their API applications use an HTTP middleware that runs before FastAPI body
parsing. `/health` remains public for Docker and bootstrapper readiness probes.
Every other route requires `Authorization: Bearer <token>`. Authentication is
fail-closed: required mode with an empty token returns 503, and a missing or
incorrect credential returns 401. Comparisons use constant-time byte comparison
and safely handle non-ASCII input.

`DOCLING_AUTH_MODE` and `PARAKEET_AUTH_MODE` accept `required` or `disabled`.
The manifest default is `required`; `disabled` is an explicit emergency or
local-development rollback, not an implicit fallback when a token is absent.

Browser CORS is disabled by default because these are server-to-server APIs.
`DOCLING_CORS_ORIGINS` and `PARAKEET_CORS_ORIGINS` may contain an explicit
comma-separated origin allowlist. Wildcard origins are rejected when
authentication is required, and CORS credential mode remains disabled because
bearer headers do not require browser cookie credentials.

### 3.2 Admission before upload parsing

Each provider owns a non-blocking admission counter whose capacity equals its
existing concurrency setting (`DOCLING_CONCURRENCY` or
`PARAKEET_CONCURRENCY`). The authentication middleware attempts admission only
for expensive POST routes and does so before calling the downstream ASGI
application. If capacity is unavailable, it returns 429 with `Retry-After: 1`
without reading the request body.

The permit covers request parsing, bounded spooling, model loading, inference,
response construction, and temporary-file cleanup. It is released in a
`finally` block for success, validation errors, disconnects, and ordinary
exceptions. The inner processor semaphores remain as defense in depth during
the migration but must use the same configured capacity so they cannot create
an additional queue.

### 3.3 Killable completion deadlines

`DOCLING_INFERENCE_TIMEOUT_SECONDS` and
`PARAKEET_INFERENCE_TIMEOUT_SECONDS` default to 900 seconds and must be finite
integers from 1 through 3600. The deadline covers lazy model loading and native
inference, not merely HTTP response serialization.

Python cannot safely kill a thread executing CUDA, MPS, NeMo, PyTorch, or a
native Docling extension. On deadline expiry the API therefore:

1. records a structured timeout event without filenames, paths, content, or
   exception text;
2. returns a generic HTTP 504 response; and
3. runs a response-background callback that terminates the provider process
   with a dedicated non-zero exit code after the response bytes are sent.

Docker's existing `restart: unless-stopped` policy then starts a clean process,
which is the killable boundary for the abandoned native thread and its model
state. Native localhost servers use the same behavior: the process exits after
the 504 and the operator's service manager restarts it. Documentation must make
that requirement explicit.

Parakeet GPU startup loading moves behind a non-blocking startup task governed
by the same deadline. A watchdog terminates the process if loading hangs before
the HTTP application becomes responsive. Readiness stays 503 until loading
finishes.

### 3.4 LightRAG–Docling adapter

A lightweight `docling-lightrag-adapter` container implements only the pinned
LightRAG v1.5.4 contract:

- `POST /v1/convert/file/async`
- `GET /v1/status/poll/{task_id}`
- `GET /v1/result/{task_id}`
- `GET /health`

The adapter does not import Docling or load a model. It accepts a bounded number
of outstanding jobs, spools each admitted upload, and calls an authenticated
internal bundle route on the selected Docling provider. That route converts
the document once and exports the JSON and Markdown artifacts needed by
LightRAG into a zip response. The adapter stores the completed zip in a private
temporary directory until LightRAG downloads it, then deletes it. Failed,
expired, and cancelled jobs remove all temporary files.

Task identifiers are unguessable random values. Poll responses use only the
states expected by the pinned client: `pending`, `started`, `success`, or
`failure`. Result download is available only for successful jobs. Failure
payloads remain generic and never expose upstream response bodies or local
paths.

`DOCLING_ADAPTER_MAX_JOBS` defaults to two. Its middleware reserves an
outstanding-job slot before multipart parsing and transfers ownership of the
slot to the job only after successful creation. The slot is released when the
job fails, when a successful result is downloaded, or when
`DOCLING_ADAPTER_RESULT_TTL_SECONDS` expires. A lightweight terminal job record
may remain until expiry so LightRAG can observe the failure without consuming a
work slot. This bounds queued uploads and completed artifacts independently of
Docling's single active conversion permit.

### 3.5 Network isolation

The adapter has no published host port and is not attached to the shared
`backend-network`. A dedicated project-scoped network connects only LightRAG,
the adapter, and Docling. LightRAG receives
`DOCLING_ENDPOINT=http://docling-lightrag-adapter:8000`; other consumers retain
the authenticated provider endpoint.

Docling joins both the dedicated adapter network and `backend-network` because
Backend still uses its synchronous API. Its main API remains bearer-protected
on both networks. The adapter receives `DOCLING_API_TOKEN` and forwards it to
the provider; LightRAG never receives that secret.

For `docling-localhost`, the adapter reaches the host provider through the
existing `host.docker.internal` mapping and still supplies the bearer token.
The adapter scale follows an enabled Docling source and an enabled LightRAG
source; it is zero when either side is disabled.

Published Docling and Parakeet ports use
`${HOST_BIND_IP:-127.0.0.1:}`. An operator may deliberately set
`HOST_BIND_IP=0.0.0.0:` or another address, but application authentication
remains required. Native servers separately use `DOCLING_LOCALHOST_BIND_HOST`
and `PARAKEET_LOCALHOST_BIND_HOST`, both defaulting to `127.0.0.1`; binding a
native server to another interface is likewise explicit.

### 3.6 Secret generation and consumers

The bootstrapper generates both provider tokens when absent and preserves
existing non-empty values. The tokens are declared as manifest-owned secrets,
appear as empty placeholders in `.env.example`, and are never written to logs,
generated documentation, endpoint exports, or status tables.

Token propagation is explicit at every current direct client:

- Backend sends `DOCLING_API_TOKEN` on synchronous conversions and receives
  both provider token variables for future proxy work.
- Open WebUI supplies `PARAKEET_API_TOKEN` through its OpenAI-compatible STT
  API-key setting.
- Hermes renders `PARAKEET_API_TOKEN` into the server-side STT provider block.
- n8n and JupyterHub receive the two provider tokens as server-side environment
  variables for workflows and notebooks.
- The LightRAG adapter alone receives `DOCLING_API_TOKEN`; LightRAG itself does
  not.

No provider token is exposed to browser JavaScript or notebook output by
default.

## 4. Data flow

### 4.1 Backend document conversion

1. Backend authenticates its caller under the existing Backend identity policy.
2. Backend sends the document and Docling bearer token to the synchronous
   provider route.
3. Docling authenticates and admits the request before parsing multipart data.
4. Docling spools within the existing byte limit, converts under the deadline,
   returns the existing response shape, and removes the temporary file.

### 4.2 LightRAG document conversion

1. LightRAG submits a multipart document to the isolated adapter.
2. The adapter reserves a job slot before parsing, stores the bounded upload,
   returns a task identifier, and starts the job.
3. The adapter calls Docling's authenticated bundle route.
4. Docling converts once and returns a zip containing canonical `<stem>.json`
   and `<stem>.md` entries plus any safely named referenced image assets
   required by the JSON/Markdown bundle.
5. The adapter marks the job successful and serves the zip when LightRAG polls
   and downloads it.
6. The adapter deletes the upload and result and releases the job slot.

### 4.3 Parakeet transcription

1. Open WebUI, Hermes, n8n, a notebook, or another trusted server-side client
   sends an OpenAI-compatible request with the Parakeet bearer token.
2. Parakeet authenticates and admits before multipart parsing.
3. Parakeet spools within the existing byte limit and loads/runs the selected
   backend under one completion deadline.
4. It returns the existing JSON, verbose JSON, or text response and deletes the
   temporary file.

## 5. Error and recovery contracts

| Condition | HTTP result | Process/job behavior |
|---|---:|---|
| Required token is not configured | 503 | No body parsing or work starts |
| Missing or invalid token | 401 | No body parsing or work starts |
| Provider or adapter capacity full | 429 | `Retry-After: 1`; body remains unread |
| Upload exceeds byte limit | 413 | Partial temporary file is deleted |
| Empty or invalid upload | 400/422 | Permit and temporary state are released |
| Native inference deadline | 504 | Response completes, then provider exits and restarts |
| Adapter upstream receives 429 | internal retry | Bounded retry within the job deadline |
| Adapter upstream 4xx/5xx or disconnect | failure state | Generic poll failure; artifacts deleted |
| Adapter result expires | 404 | Artifact and outstanding-job slot are removed |

Client disconnect does not cause a second inference request to enter while the
native thread is still running. The provider waits for ordinary cancellable
work to settle; if the native call cannot settle within the configured
deadline, it follows the same fatal-restart path.

## 6. Compatibility and migration

- Existing endpoint paths and successful response bodies remain unchanged for
  Backend and OpenAI-compatible STT clients.
- `/health` remains unauthenticated, so existing Docker health checks continue
  to work.
- Existing `.env` files gain stable generated tokens through normal backfill.
- Required authentication activates only after token generation has run; an
  empty token fails closed rather than silently opening the provider.
- LightRAG's endpoint changes to the adapter only when both LightRAG and
  Docling are enabled. Disabled and non-Docling parser configurations remain
  unchanged.
- Operators running native Docling or Parakeet must update their local server
  environment and restart policy before enabling the corresponding localhost
  source.

## 7. Verification strategy

Implementation follows test-driven development. The first failing contracts
must cover:

1. fail-closed bearer authentication and public health checks for every API
   variant;
2. rejection before request-body reads when admission is full;
3. exact permit release on success, validation failure, disconnect, and error;
4. deadline validation, 504 response shape, and post-response fatal callback
   without actually exiting the test process;
5. Parakeet startup loading readiness and watchdog behavior;
6. exact LightRAG v1.5.4 submit, poll, and result payloads;
7. canonical, traversal-safe zip entries and cleanup on download or expiry;
8. adapter outstanding-job bounds before upload parsing;
9. idempotent secret generation and manifest/env-example parity;
10. token propagation to every named server-side consumer;
11. Compose rendering that proves loopback host binds, no adapter host port,
    and dedicated-network membership; and
12. source-permutation behavior for container, localhost, and disabled modes.

Focused unit and contract suites are followed by the full bootstrapper suite,
Backend suite with its coverage floor, ShellCheck, Compose merge and source
permutation matrices, docs drift checks, and build validation for each changed
provider image. A lightweight fake converter/transcriber is used for HTTP and
adapter integration tests so CI never downloads models or requires GPUs.

## 8. Documentation and generated artifacts

The Docling, document-processor, Parakeet, STT-provider, LightRAG, Backend,
Open WebUI, Hermes, n8n, and JupyterHub documentation must describe the token,
admission, deadline, restart, and network contracts relevant to that service.
Manifest `data_flow.calls` must represent the adapter path accurately.

After manifest or architecture changes, the repository generators regenerate
service dependency sections, service SVG/HTML diagrams, canonical references,
MkDocs/wiki surfaces, and PNG derivatives. No generated artifact is edited by
hand.

## 9. Rejected alternatives

### 9.1 Loopback-only without authentication

This limits remote host exposure but leaves lateral access from every container
on `backend-network` and does not meet the authenticated-provider goal.

### 9.2 Mandatory authentication with LightRAG integration removed

This is simpler but converts an advertised integration defect into a deliberate
feature regression instead of repairing the pinned contract.

### 9.3 Credentials embedded in the Docling endpoint URL

HTTP Basic user-info might make the pinned client emit a credential, but it
places a reusable secret in environment values, endpoint signatures, errors,
and diagnostic output. It is rejected as an avoidable secret-leak risk.

### 9.4 `asyncio.wait_for()` without process termination

It returns control to the event loop but cannot stop a native inference thread.
Releasing the permit afterward would admit overlapping work while the original
model call still runs, so this does not provide a real completion bound.

### 9.5 A model-loading adapter process

Loading Docling in both the provider and adapter duplicates model memory and
GPU state. The selected adapter is lightweight and delegates the single real
conversion to the authenticated provider.
