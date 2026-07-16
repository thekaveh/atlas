---
category-fit: media
generated: 2026-05-19
license: MIT
name: imgproxy
referenced-by: [supabase]
slug: imgproxy
type: external-service
upstream: https://github.com/imgproxy/imgproxy
---

# imgproxy

## 1. Headline
The exact image-transformation sidecar Supabase Storage upstream is designed to talk to — resizes, re-encodes, and signs image URLs on the fly so ComfyUI outputs and user uploads can be served at multiple sizes from one source object.

## 2. Watchlist decision (2026-07-04)

Keep imgproxy on the watchlist for now: Atlas needs thumbnailing, but this repo **must not add `services/imgproxy/service.yml` yet** until the root dashboard has a concrete asset browser or backend media endpoint that can mint backend-generated signed URLs.

This is a decision/spec update, not a service addition. The key product need is cheap previews for generated and uploaded media: root dashboard asset browser cards, ComfyUI outputs, MinIO artifact buckets, Open WebUI attachments, JupyterHub previews, and future Blender MCP or glTF-adjacent creative-3D assets. That need is real, but a generic image transformer is also a CPU, fetch, and authorization surface. The first implementation should therefore be internal-only and backend-mediated.

Current upstream docs reinforce the conservative route:

- imgproxy 4.0.x still disables URL signature checking by default, while recommending `IMGPROXY_KEY` and `IMGPROXY_SALT` in production.
- Processing URLs support direct browser embedding, so public exposure must assume any reachable route can be hit repeatedly by a browser or LAN peer.
- S3/MinIO source support is opt-in through `IMGPROXY_USE_S3=true`, `IMGPROXY_S3_ENDPOINT`, path-style addressing, and bucket restrictions such as `IMGPROXY_S3_ALLOWED_BUCKETS`.
- Local-file and S3 source access are disabled unless explicitly enabled, which fits Atlas' disabled-by-default pattern.

Future service shape, if a later asset-browser ticket promotes this:

- Track membership: `gen-ai-creative` and `all`; optionally `gen-ai-rag` only if document or image-preview flows need it. No new track is needed for imgproxy alone.
- Service category: `media`.
- Source values/default: `IMGPROXY_SOURCE=disabled|container`, disabled by default.
- Wizard placement: the creative/media section, after MinIO and ComfyUI, with prompt copy that says it enables signed thumbnail and image-preview generation for stored Atlas artifacts.
- Topology and port strategy: allocate one `media` topology slot only when the service manifest is introduced. This watchlist ticket assigns no port.
- Kong alias and route behavior: no public `imgproxy.localhost` route in the first implementation. Prefer internal-only `http://imgproxy:8080` called by backend/dashboard server code.
- Direct URL expectations: no host direct port unless a future debug-only source explicitly needs it; sibling containers should use Docker DNS.
- Required dependencies: none for a disabled default. A useful `container` mode depends on MinIO/S3 source access and generated signing secrets.
- Optional dependencies: ComfyUI for generated images, Open WebUI for attachment previews, JupyterHub for notebook previews, Blender MCP/glTF workflows for creative-3D asset thumbnails, and Supabase Storage only after ownership/RLS semantics are clear.
- Downstream consumers: root dashboard and asset browser first; backend media endpoints second; ComfyUI/Open WebUI/JupyterHub only after URL ownership, TTL, and auth rules are defined.
- `data_flow.calls` topology edges for a future service: `backend -> imgproxy`, `imgproxy -> minio`, and optionally root dashboard UI -> backend. Avoid browser -> imgproxy as the default architecture.
- Init companion: none expected. The transformer is stateless; the bootstrapper may still need generated secret material for `IMGPROXY_KEY` and `IMGPROXY_SALT`.
- Volumes: none by default.
- Secrets and credentials: generated hex `IMGPROXY_KEY` and `IMGPROXY_SALT`; read-only MinIO credentials scoped to approved image buckets instead of MinIO root credentials.
- Source restrictions: set `IMGPROXY_S3_ALLOWED_BUCKETS` to approved media buckets such as `comfyui`, `backend`, or a future `assets` bucket. Do not allow arbitrary remote HTTP sources by default.
- Presets: consider `IMGPROXY_ONLY_PRESETS=true` with named thumbnail sizes so callers cannot request arbitrary expensive transforms.
- Tests required for a future service PR: manifest validation, env assembly, source validation, topology slot/category coverage, route-generation tests proving the public Kong route is absent unless deliberately enabled, compose source-permutation coverage, docs drift, and a focused URL-signing/config-generation test.
- Edge cases: disabled MinIO, missing signing secrets, stale `.env`, custom `BASE_PORT`, prod profile restrictions, localhost-only dashboard previews, large/animated source images, AVIF/WebP capability choices, CORS, and generated-doc drift.

## 3. Problem it solves
Today `supabase-storage` and `minio` hold raw images (ComfyUI generations, user uploads, Open WebUI attachments) and serve them at their original dimensions. Anything consuming them (Open WebUI thumbnails, future frontend galleries, JupyterHub previews) has to download the full file. imgproxy is the canonical companion the Supabase Storage `IMGPROXY_URL` env var was built for; turning it on unlocks transform URLs like `/render/image/resize/width/256/...` without touching the source bytes.

## 4. Stack wiring sketch
- supabase-storage -> imgproxy via `IMGPROXY_URL=http://imgproxy:8080`
- imgproxy -> supabase-storage (or minio) over S3 for source reads
- open-webui -> kong -> imgproxy (for chat-message thumbnails)
- backend -> imgproxy (for any image-list endpoints it exposes)
- comfyui -> supabase-storage (writes original) -> imgproxy (serves derivatives)

## 5. Effort
small — one stateless container, one env var on `supabase-storage`, one Kong route. No DB schema changes.

## 6. Risks & open questions
- imgproxy's signing-key model needs a new secret in `.env` (`IMGPROXY_KEY`, `IMGPROXY_SALT`) — bootstrapper would need to auto-generate them.
- CPU-bound on large images; resource limits matter on small hosts.
- Format support: JPEG/PNG/WebP/AVIF out of the box; HEIC/RAW need the Pro build.

## 7. Why now (and why not sooner)
With ComfyUI producing 1024x1024+ images and Open WebUI displaying them inline, the bandwidth cost of un-resized serving is now visible. imgproxy is also a prerequisite for any future "image gallery" or "asset browser" UI that wants snappy thumbnails. The service should still wait for the asset-browser route and signing design, because adding imgproxy before that would expose transformation machinery before Atlas knows who owns the URLs.

## 8. Upstream evidence
- https://github.com/imgproxy/imgproxy
- https://supabase.com/docs/guides/storage/serving/image-transformations
- https://docs.imgproxy.net/configuration/options
- https://docs.imgproxy.net/usage/signing_url
- https://docs.imgproxy.net/usage/processing
- https://docs.imgproxy.net/image_sources/amazon_s3
