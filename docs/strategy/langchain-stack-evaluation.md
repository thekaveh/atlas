# LangChain Stack Evaluation

Generated for issue #532, "Evaluate the langchain-ai stack (LangChain / LangGraph / DeepAgents / open_deep_research / openwiki) as Atlas libraries or services".

This is an **evaluation artifact**: it records per-item GO / NO-GO / DEFER verdicts and the evidence, contracts, and follow-up scoping that *separate future implementation tickets* must build against. It is deliberately not an implementation — no requirements bump, no `service.yml`, and no compose fragment ship with it. It mirrors the existing Atlas evaluation deliverables [`docs/strategy/blender-mcp-container-source-evaluation.md`](./blender-mcp-container-source-evaluation.md) and [`docs/strategy/rag-evaluation-matrix-evaluation.md`](./rag-evaluation-matrix-evaluation.md).

## 1. Decision

**The framing question is not "add these libraries" — Atlas already runs LangChain and LangGraph today.** The spike confirms this is an *upgrade/consolidation* exercise plus one optional service addition:

| Item | Verdict | Shape |
|---|---|---|
| LangChain | **GO** | Library upgrade (backend + JupyterHub floors → 1.x), no new service |
| LangGraph (MIT core) | **GO** | Make the dead backend pin real + add to JupyterHub; ELv2 server stays quarantined |
| open_deep_research | **GO (conditional)** | New `services/open-deep-research/`, disabled-by-default, **opt-in second research engine complementing LDR** — gated on the boot criteria in §6 |
| DeepAgents | **DEFER** | Beta + Python ≥3.11 floor + LDR overlap; revisit on ≥1.0 + a concrete use-case |
| openwiki | **NO-GO as a service** | Identity **confirmed live**: a TypeScript docs CLI, not a runtime/research surface |

Two invariants hold for every item: **LiteLLM stays the sole LLM gateway** (all model calls via `http://litellm:4000/v1`), and **the Elastic-Licensed `langgraph-api` server stays quarantined** in dev-mode service containers (LDR today; a future ODR service) — never embedded as a general library.

## 2. Verified upstream baseline (live, 2026-07-12)

Verified against PyPI + the GitHub API at spike time (not research snapshots):

| Package / repo | Version | License | Python floor |
|---|---|---|---|
| `langchain` | 1.3.13 | MIT | ≥3.10, <4.0 |
| `langchain-core` | 1.4.9 | MIT | ≥3.10, <4.0 |
| `langgraph` (core) | 1.2.9 | **MIT** | ≥3.10 |
| `langgraph-cli` | 0.4.31 | MIT | ≥3.10 |
| `langgraph-api` (server runtime) | 0.11.0 | **Elastic-2.0 (ELv2)** | — |
| `deepagents` | 0.6.12 | MIT | **≥3.11**, <4.0 |
| `langchain-ai/open_deep_research` | repo (no pip pkg) | MIT | clone-and-run |
| `langchain-ai/openwiki` | repo (npm CLI) | MIT | TypeScript |
| `langchain-ai/local-deep-researcher` | repo | MIT | — |

- **The LangGraph license split is load-bearing and clean at the dependency level:** the §4 PoC confirmed `pip install langgraph` pulls **zero** ELv2 packages (`langgraph-api` absent from the environment). MIT-core is safe as a first-class library; only the `langgraph dev`/`langgraph up` server path drags in ELv2.
- **DeepAgents' `>=3.11` floor is confirmed** — above the bootstrapper's `>=3.10` floor; it could only live in the 3.11+ images (backend/JupyterHub), never the bootstrapper.
- **Neither LDR nor ODR is archived or superseded** (LDR pushed 2026-06-28, ODR 2026-06-26; no deprecation banners). They are two distinct, simultaneously-maintained projects — see [`#525`-adjacent analysis in the ticket thread]: LDR = single-agent, local-first, key-free; ODR = multi-agent supervisor, provider-agnostic, heavier.
- **openwiki identity resolved** (live repo check): `langchain-ai/openwiki` — *"a CLI that writes and maintains agent documentation for your codebase"* (TypeScript, MIT, active). It is a docs tool, **not** a deep-research agent; the earlier identity caveat is closed.

## 3. Current Atlas state (confirmed at spike time, develop `8a725e13`)

All §3 facts from the ticket re-verified; **no material drift** from the ticket snapshot:

- **Backend pins** (`services/backend/app/app/requirements.txt`): `langchain>=0.1.14`, `langchain-core>=0.1.22`, `langchain-community>=0.3.0,<0.4` (capped for Ragas 0.4.3's `vertexai` import), `langchain-openai>=0.0.8`, `langchain-groq>=0.1.5`, `langgraph>=0.0.15`, `langchain-neo4j>=0.1.0`. Floors are pre-1.0 — generations behind the 1.x line.
- **Partly live, not wholly dead:** `rag_eval_service.py:162` imports `langchain_openai.ChatOpenAI/OpenAIEmbeddings` (the Ragas RAG-eval path), so LangChain **is** exercised in the backend. **`langgraph>=0.0.15` is the one true dead pin** — zero `langgraph` imports in backend code.
- **JupyterHub pins** (`services/jupyterhub/build/requirements.txt`): `langchain>=0.3.0`, `langchain-community>=0.3.0,<0.4`, `langchain-openai>=0.2.0`, exercised by `02_langchain_rag.ipynb`; `llama-index>=0.11.0` ships alongside by design. **No `langgraph`.**
- **LangGraph already runs as a service:** LDR's entrypoint executes `uvx --from "langgraph-cli[inmem]" --python 3.11 langgraph dev --port 2024` on every container start — so Atlas **already depends on the ELv2 `langgraph-api`/inmem runtime**, confined to that container. The backend drives it over the stock dev-server API via `research_client.py` (`/threads`, `/threads/{id}/runs/stream` SSE) with Supabase persistence.
- **LiteLLM sole gateway:** consumer wiring points at `http://litellm:4000` (17 references across LDR/backend/JupyterHub compose+config alone).

## 4. Disposable PoC (headless, non-shipping)

Run in a throwaway scratch venv (Python 3.12; **nothing merged** — no requirements change, no service, no compose):

- **Setup:** `uv pip install langgraph langchain-openai` → `langgraph==1.2.9`, `langgraph-checkpoint==4.1.1`, `langgraph-sdk==0.4.2`, `langchain-core==1.4.9`, `langchain-openai==1.3.5`; **`langgraph-api` absent** (MIT-core only).
- **Test:** an in-process OpenAI-compatible `/chat/completions` stub stands in for `http://litellm:4000/v1`; a two-node `StateGraph` agent (`ask` → `summarize`) calls `ChatOpenAI(base_url=<stub>, api_key="sk-noauth")` and the compiled graph is invoked headless.
- **Result: PASS — full round trip in 33 ms.** A custom LangGraph graph runs against a gateway-shaped OpenAI endpoint with no server runtime, no live terminal, no Docker — exactly the shape a backend `WizardVM`-style agent or a JupyterHub notebook would use against LiteLLM.

This is the evidence for the two library GOs: the MIT-core stack is importable, gateway-compatible, and unit-testable in CI.

## 5. Verdicts and rationale

- **LangChain — GO (library upgrade).** Already pinned and exercised on both surfaces via LiteLLM. The action is bumping pre-1.0 floors to the 1.x line and auditing for `langchain-classic` symbols (`LLMChain`/`AgentExecutor`/legacy chains, maintained only through Dec 2026). The Ragas 0.4.3 `<0.4` cap on `langchain-community` must be revisited in the same pass (upgrade Ragas or keep the cap deliberately).
- **LangGraph MIT core — GO (make it real).** The highest-leverage, lowest-risk item: promote the dead backend pin to a used dependency (custom `StateGraph` agents in FastAPI; optional `langgraph-checkpoint` on Supabase/pgvector) and add `langgraph` + a starter notebook to JupyterHub. **Never** embed `langgraph-api`; Atlas never runs `langgraph up` (commercial key) without a deliberate documented decision.
- **open_deep_research — GO, conditional, as a complement.** Same runtime shape Atlas already operates for LDR (`langgraph dev` service). Slots in as `services/open-deep-research/`, **disabled-by-default**, reusing the backend `/research/*` plumbing as a selectable second engine (LDR = fast/local/key-free tier; ODR = deep/multi-agent/heavier tier). **Not a replacement** — ODR's chat-`messages` input / `final_report` output differs from LDR's `research_topic`/`running_summary`, so the client must branch per engine, and ODR needs `--allow-blocking`. Gate: a scratch build must boot key-free via LiteLLM + SearXNG at acceptable cost before the implementation ticket proceeds (that live boot needs the running stack, out of this headless spike's scope).
- **DeepAgents — DEFER.** MIT but **beta** (0.6.x, churning API, a yanked alpha) with a **≥3.11 floor**, and as a second LangGraph agent harness it overlaps LDR's slot. Revisit when (a) the API stabilizes at ≥1.0 **and** (b) a concrete backend agent use-case emerges that plain LangGraph-core doesn't cover.
- **openwiki — NO-GO as a service (identity confirmed).** A TypeScript **docs CLI** (`npm i -g openwiki`) that generates/maintains repo documentation (`AGENTS.md`-style) — no server, no runtime surface, nothing to compose. At most a separately-evaluated optional repo-docs CI helper adjacent to Atlas's regen tooling; off the research shortlist.

## 6. Scoped follow-up tickets (for the GO items)

1. **"Bump backend + JupyterHub LangChain floors to 1.x"** — raise `langchain`/`langchain-core`/`langchain-openai` floors; audit `langchain-classic` imports; resolve the Ragas 0.4.3 ↔ `langchain-community<0.4` cap (bump Ragas or document the hold). Effort S–M; CI-provable (backend unit venv).
2. **"Make the backend `langgraph` pin real + JupyterHub starter notebook"** — a first in-backend `StateGraph` use (candidate: the RAG-eval orchestration or a memory-consolidation graph), `langgraph` added to JupyterHub with a `03_langgraph_agent.ipynb` mirroring the PoC shape against LiteLLM. Effort M; CI-provable headless (per §4).
3. **"Add `services/open-deep-research/` as the opt-in second research engine"** — LDR-pattern packaging (pinned clone, `langgraph dev`, `--allow-blocking`, `depends_on` litellm+searxng, disabled-by-default, no default track activation), per-engine branch in `research_client.py`, `RESEARCH_ENGINE=ldr|odr` selection. Effort L; gated on the §5 live-boot criterion. Complements — never replaces — LDR.

## 7. Where it lives

- **This evaluation:** `docs/strategy/langchain-stack-evaluation.md` (this file).
- **PoC:** throwaway scratch venv, recorded in §4; intentionally not committed.
- **Related:** the shipped LDR service ([`services/local-deep-researcher/README.md`](https://github.com/thekaveh/atlas/blob/main/services/local-deep-researcher/README.md)), the LiteLLM gateway contract, and the #535 TUI/MVVM ticket (which consumes the same "library over the gateway" pattern).
