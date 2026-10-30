---
category-fit: infra
decided: 2026-07-03
generated: 2026-05-19
license: Apache-2.0
lifecycle: deferred
name: Keycloak
public-summary: Deferred pending an identity-provider choice. Consumer configuration, credential migration and route authentication require a coordinated design.
referenced-by: [kong]
slug: keycloak
superseded-by: docs/strategy/authentik-sso-pilot-evaluation.md
type: external-service
upstream: https://www.keycloak.org/
---

# Keycloak

## 1. Headline
Self-hosted OpenID Connect / OAuth2 / SAML identity provider that replaces the stack's ad-hoc per-service basic-auth with a single SSO layer fronted by Kong.

## 2. Problem it solves
The stack has separate credential surfaces, including Supabase GoTrue, Kong dashboard basic-auth, JupyterHub PAM, MinIO, Neo4j, and the n8n owner account. It has no unified single sign-on. Kong already protects selected aliases, including Langfuse, TrueForge, and MLflow, with basic-auth and an ACL. Gateway ports bind to loopback by default; LAN access depends on `HOST_BIND_IP` and separate access controls.

Keycloak provides a single OIDC issuer that Kong's `openid-connect`-style flows (via the OSS-compatible community `kong-oidc` plugin or Kong's built-in `jwt` plugin pointed at Keycloak's JWKs) can validate, and that JupyterHub/Open WebUI/n8n/MinIO/Neo4j all natively support.

## 3. Stack wiring sketch
- kong → keycloak via `https://keycloak:8443/realms/atlas/.well-known/openid-configuration` (JWKs for the `jwt` plugin)
- jupyterhub → keycloak via the official `oauthenticator` package (already a JupyterHub dep)
- open-webui → keycloak via Open WebUI's `OAUTH_*` env vars
- n8n → keycloak via n8n's SSO config (`N8N_SSO_*`)
- minio → keycloak via MinIO's OIDC config (`MINIO_IDENTITY_OPENID_*`)
- neo4j → keycloak via Neo4j's `dbms.security.oidc.*` settings
- openclaw → keycloak via OAuth2 client-credentials for platform integrations
- backend → keycloak via the FastAPI middleware validating bearer tokens against Keycloak JWKs

## 4. Effort
large — Keycloak itself is one compose service backed by Supabase Postgres (or its own db), but every consumer requires an OIDC client config and a doc update. The real cost is migrating existing service credentials and writing a hosts-bootstrap script that pre-seeds the `atlas` realm + clients on first boot.

## 5. Risks & open questions
- Keycloak is JVM-based — ~512 MB RAM floor; non-trivial on a 16 GB laptop alongside Ollama.
- Authelia is a lighter alternative (Go, ~30 MB) but lacks the broader OIDC ecosystem coverage of Keycloak. Evaluate both.
- Realm bootstrap (clients, roles, redirect URIs) needs an init container — Keycloak's `kc.sh import` from a JSON realm file is the supported path.
- Forward-auth vs. JWT-bearer for browser SPAs — `kong-oidc` is community-maintained (not in Kong OSS); the JWT plugin works but requires each SPA to do the login dance itself.

## 6. Why now (and why not sooner)
The stack has matured from a single-user dev environment to something operators expect to deploy on a LAN or small cluster. Per-service credentials don't scale past one user. Earlier, the cost of Keycloak outweighed the benefit because the stack only had Supabase auth to integrate with.

## 7. Upstream evidence
- https://www.keycloak.org/getting-started/getting-started-docker
- https://docs.konghq.com/hub/kong-inc/jwt/ — Kong OSS JWT plugin (validates Keycloak JWTs via JWKs)
- https://oauthenticator.readthedocs.io/en/latest/topics/install.html#general-keycloak
- https://min.io/docs/minio/linux/operations/external-iam/configure-openid-identity-management.html
