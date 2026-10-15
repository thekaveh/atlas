# 10.9. Candidate Decisions

Recorded decisions about proposed additions identify prerequisites for future integration. A proposal does not enable a service.

Dates identify recorded decisions, not fresh upstream qualification. See the [Supported service catalog](../services.md) for supported Atlas configuration.

<a id="candidate-browserless"></a>

## 1. Browserless

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred pending a reproducible workflow that needs persistent browser sessions. Resource limits, authentication and license acceptance need a design before adoption.

<a href="https://github.com/browserless/browserless">Upstream project</a>.

<a id="candidate-docling-mcp"></a>

## 2. Docling MCP Server

Lifecycle: planned. Recorded decision date: 2026-07-03.

Planned as a document-tool bridge for agents. Transport, authentication, tool names and use of the existing Docling service need an integration design.

<a href="https://github.com/docling-project/docling-mcp">Upstream project</a>.

<a id="candidate-firecrawl"></a>

## 3. Firecrawl

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a reproducible extraction gap justifies an additional crawler deployment. Operational ownership and license acceptance must be decided before adoption.

<a href="https://github.com/firecrawl/firecrawl">Upstream project</a>.

<a id="candidate-honcho"></a>

## 4. Honcho

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until existing memory options cannot meet a named cross-agent workflow. User isolation, consent, retention and deletion need an explicit design.

<a href="https://github.com/plastic-labs/honcho">Upstream project</a>.

<a id="candidate-imgproxy"></a>

## 5. imgproxy

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until an asset-browser or media workflow can issue signed preview URLs. The initial design must keep source access and transformation requests controlled.

<a href="https://github.com/imgproxy/imgproxy">Upstream project</a>.

<a id="candidate-keycloak"></a>

## 6. Keycloak

Lifecycle: deferred. Recorded decision date: 2026-07-03.

Deferred pending an identity-provider choice. Consumer configuration, credential migration and route authentication require a coordinated design.

<a href="https://www.keycloak.org/">Upstream project</a>.

<a id="candidate-mcpo"></a>

## 7. mcpo (MCP-to-OpenAPI Proxy)

Lifecycle: deferred. Recorded decision date: 2026-07-03.

Deferred pending a curated tool set and an authentication model. Secret delivery, token scope and writable tool storage need an integration design.

<a href="https://github.com/open-webui/mcpo">Upstream project</a>.

<a id="candidate-nocodb"></a>

## 8. NocoDB

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a named human-review workflow needs editable operational rows. Review ownership, provenance and authentication must be defined before adding another user interface.

<a href="https://github.com/nocodb/nocodb">Upstream project</a>.

<a id="candidate-omnivoice"></a>

## 9. OmniVoice (k2-fsa)

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a maintained serving endpoint can support Atlas integration. A library proposal does not establish a supported HTTP service or selectable TTS source.

<a href="https://github.com/k2-fsa/OmniVoice">Upstream project</a>.

<a id="candidate-openlit"></a>

## 10. OpenLIT

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a named observability gap justifies another platform. Collector, storage, user-interface and retention ownership must be clear before adoption.

<a href="https://github.com/openlit/openlit">Upstream project</a>.

<a id="candidate-perplexica"></a>

## 11. Perplexica (Vane)

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until Atlas chooses a distinct interface for cited web answers. Provider setup, route authentication, upload storage and overlap with existing applications need review.

<a href="https://github.com/ItzCrazyKns/Vane">Upstream project</a>.

<a id="candidate-redis-stack"></a>

## 12. Redis Stack (redis-stack-server)

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a consumer demonstrates a tested need for module commands. Persistence, memory costs and distribution terms need review before changing the Redis variant.

<a href="https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/">Upstream project</a>.

<a id="candidate-redisinsight"></a>

## 13. RedisInsight

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until an operator workflow justifies another administration interface. Authentication, data exposure, credential handling and distribution terms require review.

<a href="https://github.com/RedisInsight/RedisInsight">Upstream project</a>.

<a id="candidate-supabase-edge-functions"></a>

## 14. Supabase Edge Functions (Deno runtime)

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until an edge-specific workflow needs another execution surface. Upgrade behavior, authentication and secret ownership need a design before adoption.

<a href="https://supabase.com/docs/guides/functions">Upstream project</a>.

<a id="candidate-unmute"></a>

## 15. Unmute (Kyutai)

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred pending client compatibility and resource qualification. Authentication, session behavior and coexistence with existing speech providers need a design before adoption.

<a href="https://github.com/kyutai-labs/unmute">Upstream project</a>.

<a id="candidate-whisperx"></a>

## 16. WhisperX

Lifecycle: deferred. Recorded decision date: 2026-07-04.

Deferred until a named audio-ingestion workflow needs speaker labels and word timings. Model access, resource limits, retention and transcript provenance need an integration design.

<a href="https://github.com/m-bain/whisperx">Upstream project</a>.
