# 1. Atlas Documentation

<div class="md-content--atlas-wide"></div>

<div class="atlas-home">
  <section class="atlas-home__hero">
    <div class="atlas-home__copy">
      <p class="atlas-kicker">Source-configurable local AI, data, and engineering stack</p>
      <p>Atlas is a self-hosted engineering platform for generative AI, RAG, creative AI, ML engineering, and data engineering workloads. Docker Compose fragments, service manifests, SOURCE values, tracks, and Kong routes combine into one configurable local platform.</p>
      <div class="atlas-home__actions">
        <a href="quick-start/">Quick Start</a>
        <a href="services/">Service Catalog</a>
        <a href="architecture/">Architecture</a>
      </div>
    </div>
    <figure class="atlas-home__media">
      <img src="assets/atlas-poster-blue.png" alt="Atlas platform poster">
    </figure>
  </section>
</div>

## 1. Capabilities

Atlas organizes 59 service families into 7 tracks. Each track pre-selects a working subset of the platform for one class of workload; the setup wizard prompts for track-scoped services and force-disables the rest.

<div class="atlas-home__grid" markdown="1">

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Generative AI · RAG</p>
<p class="atlas-card__body">Retrieval-augmented generation — vectors, graph, reranker, doc ingest, web search, workflow automation.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Generative AI · Engineering</p>
<p class="atlas-card__body">Agentic apps + workflows with voice, vision, and search.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Generative AI · Creative</p>
<p class="atlas-card__body">Multimodal generation — image, voice, vision, doc.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">ML Engineering</p>
<p class="atlas-card__body">Distributed training/inference + notebooks + experiment storage.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Data Engineering</p>
<p class="atlas-card__body">Batch + lakehouse + graph + vector with orchestration.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Trading / Financial Research</p>
<p class="atlas-card__body">Read-only financial research and paper portfolios in notebooks; no live trading.</p>

[View track services →](reference/tracks.md){: .atlas-card__link}
</div>

</div>

## 2. Quick Start

<div class="atlas-home__quickstart" markdown="1">

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --llm-provider-source ollama-container-gpu
```

Interactive wizard by default; CLI flags skip prompts for the values you set. Full flow, flags, and troubleshooting: [Quick Start](quick-start/index.md){: .atlas-home__quickstart-link}.
</div>

## 3. Platform Topology

<div class="atlas-home__topology" markdown="1">

![Atlas platform topology: entrypoints, Kong gateway, apps and agents, LLM core, data stores, and cloud-provider boundary](diagrams/architecture.html)

<p class="atlas-home__caption">Kong routes every *.localhost host; LiteLLM is the single path for local and cloud model traffic.</p>

</div>

Per-flow diagrams (data/RAG, LLM provider routing, observability, security boundary, bootstrapper lifecycle): [Architecture](architecture/index.md).

## 4. Documentation Map

<div class="atlas-home__grid" markdown="1">

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Quick Start</p>
<p class="atlas-card__body">Run the wizard, pick a track, launch the stack.</p>

[Start here →](quick-start/index.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Core Concepts</p>
<p class="atlas-card__body">SOURCE values, tracks, adaptive services, Kong routing.</p>

[Read concepts →](core-concepts.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Service Catalog</p>
<p class="atlas-card__body">Every service family, its SOURCE variants, and dependencies.</p>

[Browse services →](services.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Architecture</p>
<p class="atlas-card__body">Platform, data-flow, and lifecycle diagrams.</p>

[View architecture →](architecture/index.md){: .atlas-card__link}
</div>

<div class="atlas-card" markdown="1">
<p class="atlas-card__title">Reference</p>
<p class="atlas-card__body">Env vars, ports, manifest fields, and the 53 SOURCE-configurable service surfaces.</p>

[Open reference →](reference/index.md){: .atlas-card__link}
</div>

</div>

## 5. Setup Surface

<div class="atlas-screenshot">
  <img src="screenshots/wizard-running.png" alt="Atlas setup wizard running the launch phase">
</div>
