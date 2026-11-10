# RAG quality regression starter

Follow [Reusing Atlas §6.8](../../docs/operations/reusing-atlas.md#68-gating-rag-quality-changes-examplesrag-quality) for the fixtures, the offline check, opt-in scoring, the run report and the MLflow export.

Follow [Reusing Atlas §6.9](../../docs/operations/reusing-atlas.md#69-comparing-models-on-your-own-tasks-examplesrag-qualitycomparepy) to compare model aliases on the same task file with `compare.py`.

This consumer-owned starter uses only the Python standard library. Scored metrics come from Atlas's `POST /api/rag/evaluate`. Copy it into your project and replace the fixtures with your own.
