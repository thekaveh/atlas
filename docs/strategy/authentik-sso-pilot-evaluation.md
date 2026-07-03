# Authentik SSO Pilot Evaluation

## 1. Recommendation

Atlas should use an **Authentik-first** SSO pilot and keep **Keycloak as the heavier enterprise alternative**. This is not a recommendation to migrate the whole stack to one identity provider in a single pass. The safe first slice is a route-level pilot that protects one non-critical route, proves login/logout behavior through Kong, and documents service-by-service implications before any broad auth migration.

The first implementation should be optional, disabled by default, and reversible. Existing service-native credentials, generated `.env` secrets, Supabase Auth, and Kong dashboard basic-auth must keep working while the pilot is off.

## 2. Current Atlas Problem

Atlas currently has a fragmented auth model:

- Open WebUI has its own user/account flow.
- JupyterHub has notebook-user auth and optional token behavior.
- n8n has first-owner setup and its own credential store.
- MinIO has root/user policies and optional OIDC support.
- Neo4j has its own database credentials and optional OIDC settings.
- Supabase Auth is present as an application-auth substrate, but it is not the stack-wide login surface for all browser UIs.
- Kong currently applies a mix of pass-through, `key-auth`, dashboard `basic-auth`/`acl`, service-native bearer tokens, and unauthenticated local/dev routes.

That is acceptable for a single-user local platform, but it does not scale into multi-user, LAN, shared lab, trading, or high-risk MCP workflows.

## 3. Upstream Research Snapshot

Research checked on 2026-07-03:

- Authentik latest GitHub release: `version/2026.5.3`, published 2026-06-10.
- Authentik `ghcr.io/goauthentik/server:latest` image index digest checked locally: `sha256:36233579415aa2e2e52a6b0c45736cb871fe71460bfe0cf95d83f67528fb1182`.
- Keycloak latest GitHub release: `26.6.4`, published 2026-06-26.
- Keycloak `quay.io/keycloak/keycloak:latest` image index digest checked locally: `sha256:0aae0de7fca85525f727d3354df17896092de8bb26ae4c12d89c77e5df8cbce4`.

Official docs used:

- [Authentik Docker Compose installation](https://docs.goauthentik.io/install-config/install/docker-compose)
- [Authentik proxy provider](https://docs.goauthentik.io/add-secure-apps/providers/proxy/)
- [Authentik forward auth](https://docs.goauthentik.io/add-secure-apps/providers/proxy/forward_auth/)
- [Authentik OAuth2/OIDC provider](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/)
- [Authentik outposts](https://docs.goauthentik.io/add-secure-apps/outposts/)
- [Keycloak container guide](https://www.keycloak.org/server/containers)
- [Keycloak reverse proxy guide](https://www.keycloak.org/server/reverseproxy)
- [Kong OpenID Connect plugin](https://developer.konghq.com/plugins/openid-connect/)
- [Kong JWT plugin](https://developer.konghq.com/plugins/jwt/)

Important constraint: the Kong OpenID Connect plugin is Enterprise only in current Kong docs. Atlas currently ships Kong OSS-style plugins in `services/kong/compose.yml`, so the first Authentik pilot must not assume first-class Kong OIDC is available at the edge.

## 4. Authentik vs Keycloak

Authentik is the better Atlas-first pilot because it supports both OIDC and proxy/forward-auth patterns, is designed around outposts, and fits the "protect one route first" approach better than a full realm migration. Its Docker Compose docs are explicit about small-scale deployments, Postgres, Redis, generated secret keys, and optional Docker-socket-backed outpost management.

Keycloak remains the stronger enterprise alternative when Atlas needs a more conventional realm/client model, mature admin patterns, large identity-team familiarity, or deep enterprise federation. It is also heavier: Keycloak is JVM-based, expects careful hostname/proxy settings, and its container docs recommend optimized images and production-mode care. It should stay documented as the fallback for teams that already operate Keycloak or need its ecosystem, not the first local-stack default.

## 5. Recommended Pilot Architecture

Use Authentik as the identity provider and Authentik proxy/forward auth as the edge-auth integration path for the first slice.

First pilot:

1. Add Authentik as a disabled-by-default infra service.
2. Add or configure an Authentik proxy outpost for one route-level pilot.
3. Protect one non-critical route through forward auth.
4. Keep all existing per-service auth in place.
5. Validate login, logout, session expiry, redirect URLs, headers, and recovery behavior.

Preferred route choice:

- Use a low-risk dashboard surface that is already non-core and disabled by default, such as `ray.localhost` or `flower.localhost`, if that service is enabled in the test stack.
- If choosing an existing service makes verification too brittle, add a tiny static `sso-smoke.localhost` route for the pilot only. That route should prove the mechanics without putting a production data surface in the blast radius.

Do not use the root Atlas dashboard, Supabase Studio, Open WebUI, n8n, MinIO, or Neo4j as the first pilot. Those are important enough that a failed SSO rollout would interrupt real work or hide recovery paths.

## 6. Service Admission Contract

Future service contract:

- Source values: `AUTHENTIK_SOURCE=container|disabled`.
- Default: `disabled by default`.
- Service category: `infra`; category: `infra` is required for the manifest and route table.
- Roadmap track: `identity-security`.
- Runtime track placement: do not add to existing AI/data tracks by default. Expose through `all` and a future runtime `identity-security` track if that track is added.
- Containers: at minimum `authentik-server` and `authentik-worker`; a proxy outpost may be embedded, managed, or explicit depending on the chosen implementation.
- Backing services: Postgres and Redis. Prefer reusing Atlas Supabase Postgres and Redis only after schema/database isolation, backup, startup-order, and credential boundaries are reviewed. A dedicated Postgres database/schema is safer than mixing Authentik state into application schemas.
- Init companion: likely required for first-boot app/provider/outpost bootstrap. It must be idempotent, safe on restart, and must not require browser-only setup for CI validation.
- Kong alias: `auth.localhost`.
- Optional additional alias: `sso-smoke.localhost` if the pilot uses a synthetic route.
- Ports: allocate an infra-category port through `bootstrapper/services/topology.py`; do not hard-code a host port outside the BASE_PORT allocator.
- Authentik internal HTTP port: current upstream docs use 9000 for HTTP and 9443 for HTTPS.
- Secrets: generate `AUTHENTIK_SECRET_KEY`, database password, bootstrap admin password, and outpost token through Atlas' existing credential rotation path or a future Infisical integration. Do not commit default admin credentials.
- Kong posture: expose the Authentik UI only on localhost/Kong in the first slice. Do not publish it through Cloudflare or any remote tunnel until TLS, cookies, issuer URLs, and recovery docs are written.
- Topology: Kong calls Authentik/outpost; Authentik calls Postgres and Redis; the protected pilot route depends on Kong and Authentik but must remain recoverable if Authentik is disabled.
- Data flow: record Kong -> Authentik/outpost, Authentik -> Supabase/Postgres, Authentik -> Redis, and the selected protected route's dependency on Authentik.
- Wizard placement: show after core infra and before high-risk services. Copy should clearly say the pilot is optional, local-first, disabled by default, and does not replace service-native auth yet.
- Source validator: reject unknown values and keep disabled behavior clean.
- Docs: include setup, recovery, password reset, disable path, cookie/hostname notes, and route-specific verification.

## 7. Current Service Implications

Open WebUI:

- Treat as a later native OIDC candidate, not the first route-level pilot.
- Existing local account behavior must remain available while Authentik is disabled.
- Future integration should define whether Open WebUI trusts Authentik directly or remains behind forward auth with headers.

JupyterHub:

- Native OIDC is plausible through JupyterHub authenticator plugins, but it affects notebook user identity, volumes, and permissions.
- Do not migrate notebook auth in the first slice.
- Future work needs explicit user-volume mapping and admin recovery guidance.

n8n:

- n8n has its own owner setup and credential model.
- Putting it behind forward auth is not the same as migrating n8n users.
- Future work should decide whether Authentik only gates access or becomes an actual n8n identity provider where supported.

MinIO:

- MinIO supports external identity patterns, but it is a data-control surface.
- Do not choose MinIO Console as the first route-level pilot.
- Future work must map policies, groups, service accounts, and S3 API behavior separately from the browser UI.

Neo4j:

- Neo4j Browser and database auth are distinct.
- Protecting `graph.localhost` with SSO does not replace Bolt/database credentials.
- Future work must document Browser login, database roles, and application credentials separately.

Kong:

- Kong remains the edge router and route generator.
- Atlas should not assume the Kong Enterprise OIDC plugin is available.
- Forward auth through Authentik outpost is the likely first path; Kong JWT validation can remain a later alternative for token-bearing API clients.
- Existing `basic-auth`/`acl` guards for local dashboards should remain until the pilot proves an SSO replacement route-by-route.

Supabase Auth:

- Supabase Auth remains Atlas' application auth substrate for Supabase APIs and apps that already depend on it.
- Do not replace or bypass Supabase Auth in this ticket.
- A later decision should define whether Authentik federates into Supabase Auth, Supabase Auth remains app-local, or both coexist with clear ownership boundaries.

## 8. Failure And Recovery Rules

The first implementation must include a clear recovery path:

- Setting `AUTHENTIK_SOURCE=disabled` removes Authentik routes and removes SSO gating from the pilot route.
- The Atlas root dashboard remains reachable without Authentik.
- Supabase Studio remains reachable through its current dashboard basic-auth path unless it is explicitly selected in a later migration ticket.
- Direct host ports should be documented honestly: SSO at Kong does not protect direct ports unless those ports are bound only to loopback or the upstream service also enforces auth.
- A failed Authentik bootstrap must not block the whole stack from starting.
- The pilot must have a testable logout/sign-out URL and a documented session reset path.

## 9. What Not To Do First

Do not:

- Migrate every browser service to SSO in one PR.
- Replace Supabase Auth without a separate identity-ownership decision.
- Put Authentik in front of the Atlas root dashboard before recovery paths are proven.
- Rely on Kong Enterprise-only OIDC features in the OSS Compose profile.
- Mount the Docker socket into Authentik worker by default without a documented risk decision; upstream supports Docker-managed outposts, but the Docker socket is a meaningful privilege boundary.
- Expose Authentik or protected routes through Cloudflare before TLS, cookie domain, issuer URL, and remote-hostname behavior are tested.
- Treat forward auth headers as sufficient application authorization for data-plane APIs.

## 10. Acceptance Criteria For The Future Implementation Ticket

- Authentik is added as a SOURCE-configurable service with `AUTHENTIK_SOURCE=container|disabled`.
- Authentik is disabled by default and categorized as `infra`.
- The service is associated with track: `identity-security` in roadmap docs.
- The setup wizard clearly explains that this is an optional SSO pilot, not a full-stack auth migration.
- Ports are assigned through the topology allocator and appear in `.env.example`, README topology, and route docs.
- Kong route `auth.localhost` appears only when Authentik is enabled.
- Authentik server/worker dependencies on Postgres and Redis are represented in manifest topology and generated docs.
- Any init companion is idempotent and can create the first provider/application/outpost configuration without manual browser-only setup.
- One non-critical route is protected with Authentik forward auth or an equivalent documented route-level mechanism.
- The pilot route has automated tests proving the Kong route includes the expected auth integration only when Authentik is enabled.
- The pilot route remains ungated or removed when `AUTHENTIK_SOURCE=disabled`, according to its documented recovery behavior.
- Open WebUI, JupyterHub, n8n, MinIO, Neo4j, Kong, and Supabase Auth implications are documented before any of them are migrated.
- The implementation explicitly blocks no broad migration until the route-level pilot passes.
- Docs include operator recovery steps, local-only warnings, direct-port limitations, logout/session-reset guidance, and upstream links.
- Local verification includes the bootstrapper pytest suite, docs drift check, link check, compose source-dependency check, Kong route check, manifest fragment validation, `docker compose --env-file .env.example -f docker-compose.yml config -q`, and `git diff --check`.

## 11. Suggested Follow-up Issues

1. Build Next implementation: Authentik Route-Level SSO Pilot.
2. Decision: Supabase Auth vs external IdP ownership model.
3. Watchlist: Keycloak Enterprise SSO Alternative.
4. Hardening: Direct-port exposure and loopback binding audit for protected browser routes.
5. Later implementation: native OIDC integration for Open WebUI or JupyterHub after the route-level pilot passes.
