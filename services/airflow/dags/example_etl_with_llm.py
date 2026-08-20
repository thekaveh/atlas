"""Example DAG demonstrating Spark + MinIO + LiteLLM integrations.

Runs daily. Three PythonOperator steps exercising the cross-stack
integrations:

1. ``spark_smoke`` calls the Spark cluster via Spark Connect (the modern
   gRPC client; no JAR submission needed for a smoke test) at
   ``sc://spark-connect:15002``. This confirms the in-stack Spark cluster
   is reachable end-to-end. Note: it does NOT exercise the seeded
   ``spark_default`` Connection (host: spark-master:7077) — that one is
   for user DAGs that use ``SparkSubmitOperator``. The Connect endpoint
   is a separate sidecar; both are seeded for user use, but the smoke
   step happens to call the Connect path.
2. ``summarize_via_litellm`` calls LiteLLM's chat-completions endpoint
   through OpenAIHook (which routes via ``litellm_default``). The openai
   provider exports only ``OpenAIEmbeddingOperator`` and
   ``OpenAITriggerBatchOperator``; there is no ``OpenAIOperator``, so we
   use the Hook directly. For richer chains see the commented LangChain
   block at the bottom of this file.
3. ``list_minio_buckets`` calls ``S3Hook.list_buckets()`` against
   ``minio_default``.

This DAG is intentionally tiny — replace with your own DAGs.

The smoke-test design rationale: this daily DAG stays lightweight and uses
Spark Connect so it can confirm the cluster is reachable without launching a
batch JAR. For the full `SparkSubmitOperator` + S3A + Iceberg validation path,
trigger the manual `lakehouse_spark_submit_smoke` DAG.

Model selection: the LLM step defaults to ``ollama/qwen3.8:latest``,
which assumes the stack's default Ollama-mode catalog. If you run with
``--llm-provider-source none`` + ``CLOUD_OPENAI_SOURCE=enabled`` (cloud-
only mode), swap ``model="ollama/qwen3.8:latest"`` for a cloud model id
like ``gpt-4o-mini``. See services/litellm/README.md for the available
model ids in each provider configuration.
"""
from __future__ import annotations

from airflow import DAG
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.openai.hooks.openai import OpenAIHook
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator  # noqa: F401  # available for user DAGs
# Airflow 3.x canonical path for core operators (was airflow.operators.python in 2.x).
from airflow.providers.standard.operators.python import PythonOperator
from datetime import datetime, timedelta


default_args = {
    "owner": "atlas",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


def spark_smoke(**_ctx):
    """Confirm spark://spark-master:7077 + sc://spark-connect:15002 are reachable.

    Uses Spark Connect (modern gRPC client) — no JAR submission, no
    cluster-mode dependencies. Returns the master version.

    Note: the gRPC endpoint lives on the dedicated `spark-connect` sidecar
    container (it runs apache/spark's start-connect-server.sh against
    `spark://spark-master:7077`), NOT on spark-master itself.
    """
    from pyspark.sql import SparkSession
    spark = (
        SparkSession.builder
        .remote("sc://spark-connect:15002")
        .appName("airflow-smoke")
        .getOrCreate()
    )
    print(f"Spark Connect connected; version={spark.version}")
    spark.stop()


def summarize_via_litellm(**_ctx):
    """Call LiteLLM's chat-completions endpoint via OpenAIHook.

    OpenAIHook builds the OpenAI SDK client with base_url=conn.host. We
    seeded litellm_default with conn.host=http://litellm:4000/v1 (the
    /v1 lives in conn.host because OpenAIHook ignores the `api_base`
    extra; see init-airflow.sh).
    """
    hook = OpenAIHook(conn_id="litellm_default")
    client = hook.get_conn()
    response = client.chat.completions.create(
        # Default Ollama model that ships in the stack's LiteLLM catalog
        # (see bootstrapper/utils/llm_catalog.py + services/litellm/README.md).
        model="ollama/qwen3.8:latest",
        messages=[{"role": "user", "content": "Reply with the single word 'ok'."}],
    )
    print(f"litellm reply: {response.choices[0].message.content}")


def list_minio(**_ctx):
    s3 = S3Hook(aws_conn_id="minio_default")
    buckets = s3.get_conn().list_buckets()["Buckets"]
    print(f"buckets: {[b['Name'] for b in buckets]}")


with DAG(
    "example_etl_with_llm",
    description="Smoke test: Spark + MinIO + LiteLLM Connections",
    default_args=default_args,
    schedule="@daily",
    # Stable past start_date — using a date near "today" creates a year's
    # worth of skipped runs the scheduler has to evaluate when users pull
    # this DAG much later. catchup=False prevents backfill anyway.
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["smoke", "stack-internal"],
) as dag:

    spark_step = PythonOperator(
        task_id="spark_smoke",
        python_callable=spark_smoke,
    )

    llm_step = PythonOperator(
        task_id="summarize_via_litellm",
        python_callable=summarize_via_litellm,
    )

    minio_step = PythonOperator(
        task_id="list_minio_buckets",
        python_callable=list_minio,
    )

    spark_step >> llm_step >> minio_step


# ─── LangChain example (commented; uncomment + adapt as needed) ───
# Notes for adapters / model ids:
#   - Always route through LiteLLM (base_url=http://litellm:4000/v1) so the
#     model id can be any LiteLLM alias (Ollama, OpenAI, Anthropic, etc).
#   - LiteLLM's Ollama adapter is `ollama_chat/<name>` for chat models
#     (services/litellm/README.md "Ollama adapter choice"); `ollama/<name>`
#     hits the embeddings/generate routes instead.
#   - Use whatever model id appears in `curl http://litellm:4000/v1/models`.
#   - There is NO `apache-airflow-providers-langchain` (PyPI 404). Use
#     `pip install langchain-openai langchain-core` in your image or via
#     a venv-equipped PythonOperator. The pattern below wraps a chain in
#     a plain Python callable.
#
# import os
# from langchain_core.runnables import RunnablePassthrough
# from langchain_openai import ChatOpenAI
#
# def run_chain(**_ctx):
#     llm = ChatOpenAI(
#         # Use the SAME model id clients pass through the LiteLLM proxy:
#         # `ollama/qwen3.8:latest` (registered by litellm-init). The
#         # `ollama_chat/` prefix mentioned above is LiteLLM's INTERNAL
#         # adapter name (config.yaml litellm_params.model), NOT what
#         # clients use to call /v1/chat/completions.
#         model="ollama/qwen3.8:latest",
#         base_url="http://litellm:4000/v1",
#         api_key=os.environ["LITELLM_MASTER_KEY"],
#     )
#     chain = RunnablePassthrough() | llm
#     return chain.invoke({"input": "Reply with the single word 'ok'."})
#
# with DAG("langchain_example", ...) as dag2:
#     PythonOperator(task_id="run_chain", python_callable=run_chain)
