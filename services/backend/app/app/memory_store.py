"""
Dual vector backend for LangMem memory storage.

Supports Weaviate (preferred) with automatic fallback to pgvector
when Weaviate is unavailable or disabled.
"""

import os
import asyncio
import logging
from typing import Optional, List, Dict, Any, Union
from uuid import UUID

import httpx

from db_connection import connect_postgres


def _to_uuid(value: Union[str, UUID, None]) -> Optional[UUID]:
    """Convert a string or UUID to a UUID object, or return None."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(value)

logger = logging.getLogger("memory_store")

WEAVIATE_COLLECTION_NAME = "Memory"


class MemoryStore:
    """Abstraction over Weaviate and pgvector for memory vector storage."""

    def __init__(
        self,
        database_url: str,
        weaviate_url: Optional[str] = None,
        litellm_url: Optional[str] = None,
        litellm_api_key: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        self.database_url = database_url
        self.weaviate_url = weaviate_url
        self.litellm_url = litellm_url or "http://litellm:4000"
        self.litellm_api_key = litellm_api_key or os.getenv("LITELLM_API_KEY", "")
        # Embedding model identifier in LiteLLM's model_list
        # (e.g. "ollama/nomic-embed-text", "openai/text-embedding-3-small").
        self.embedding_model = embedding_model or os.getenv(
            "LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text"
        )
        self.backend: Optional[str] = None  # "weaviate" or "pgvector"
        self._weaviate_client = None
        self._initialized = False
        # Self-guards the one-shot init so the destructive collection heal
        # in _ensure_weaviate_collection can never race itself, regardless
        # of caller. (MemoryService also serializes its single init call,
        # but a direct MemoryStore user would otherwise be unprotected.)
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        """Detect available vector backend and initialize."""
        if self._initialized:
            return

        async with self._init_lock:
            # Double-check under the lock: a concurrent caller may have
            # completed init (incl. the collection heal) while we waited.
            if self._initialized:
                return

            # Try Weaviate first
            if self.weaviate_url:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(
                            f"{self.weaviate_url}/v1/.well-known/ready"
                        )
                        if resp.status_code == 200:
                            self.backend = "weaviate"
                            await self._ensure_weaviate_collection()
                            logger.info(
                                "Memory store initialized with Weaviate backend "
                                f"at {self.weaviate_url}"
                            )
                            self._initialized = True
                            return
                except Exception as e:
                    logger.warning(
                        f"Weaviate not available ({e}), falling back to pgvector"
                    )

            # Fall back to pgvector
            self.backend = "pgvector"
            logger.info("Memory store initialized with pgvector backend")
            self._initialized = True

    async def _ensure_weaviate_collection(self):
        """Create the Memory collection in Weaviate if it doesn't exist."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if collection exists
            resp = await client.get(
                f"{self.weaviate_url}/v1/schema/{WEAVIATE_COLLECTION_NAME}"
            )
            if resp.status_code == 200:
                # Retroactive heal: stacks deployed before the /v1/v1 fix
                # carry the broken baseURL in the EXISTING class config.
                # Weaviate 1.27.5 rejects vectorizer moduleConfig updates
                # via PUT (immutables validation), so the only remediation
                # is delete + recreate — safe here because the broken
                # baseURL meant every embedding call 404'd: the class
                # never held valid vectors.
                try:
                    existing = resp.json()
                    mod = (existing.get("moduleConfig") or {}).get("text2vec-openai") or {}
                    bad = (mod.get("baseURL") or "").rstrip("/")
                    if bad.endswith("/v1"):
                        logger.warning(
                            "Memory collection carries the broken /v1 baseURL "
                            "(%s) — deleting and recreating it (it could never "
                            "store vectors).", bad,
                        )
                        await client.delete(
                            f"{self.weaviate_url}/v1/schema/{WEAVIATE_COLLECTION_NAME}"
                        )
                        # fall through to the creation path below
                    else:
                        return  # Already exists and healthy
                except Exception as exc:  # noqa: BLE001 — heal is best-effort
                    logger.warning("Memory collection baseURL check failed: %s", exc)
                    return  # keep existing class; don't block startup

            # Create collection
            schema = {
                "class": WEAVIATE_COLLECTION_NAME,
                "description": "LangMem persistent memory facts",
                "vectorizer": "text2vec-openai",
                "moduleConfig": {
                    "text2vec-openai": {
                        # Strip the LiteLLM provider prefix (text2vec-openai
                        # expects an OpenAI-style model name; LiteLLM resolves
                        # the actual provider from its model_list).
                        "model": self.embedding_model.split("/", 1)[-1],
                        # No /v1 suffix: Weaviate's openai module joins
                        # "/v1/embeddings" onto baseURL itself, so a /v1
                        # here produced /v1/v1/embeddings → 404 on every
                        # Weaviate-backed memory insert/search.
                        "baseURL": self.litellm_url,
                        "vectorizeClassName": False,
                    }
                },
                "properties": [
                    {
                        "name": "content",
                        "dataType": ["text"],
                        "description": "Memory fact content",
                    },
                    {
                        "name": "userId",
                        "dataType": ["text"],
                        "description": "User ID who owns this memory",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "namespace",
                        "dataType": ["text"],
                        "description": "Memory namespace",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "factType",
                        "dataType": ["text"],
                        "description": "Type of fact",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "confidence",
                        "dataType": ["number"],
                        "description": "Confidence score",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "pgFactId",
                        "dataType": ["text"],
                        "description": "Reference to PostgreSQL memory_facts.id",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                    {
                        "name": "isActive",
                        "dataType": ["boolean"],
                        "description": "Whether the memory is active",
                        "moduleConfig": {
                            "text2vec-openai": {
                                "skip": True,
                                "vectorizePropertyName": False,
                            }
                        },
                    },
                ],
            }
            resp = await client.post(
                f"{self.weaviate_url}/v1/schema", json=schema
            )
            if resp.status_code in (200, 201):
                logger.info("Created Weaviate Memory collection")
            else:
                logger.error(
                    f"Failed to create Weaviate collection: "
                    f"{resp.status_code} {resp.text}"
                )

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate an embedding vector through the LiteLLM gateway.

        Uses LiteLLM's OpenAI-compatible /v1/embeddings endpoint. The model
        identifier should already include LiteLLM's provider prefix (e.g.
        "ollama/nomic-embed-text") so LiteLLM can route to the right upstream.
        """
        headers = {}
        if self.litellm_api_key:
            headers["Authorization"] = f"Bearer {self.litellm_api_key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.litellm_url}/v1/embeddings",
                json={"model": self.embedding_model, "input": text},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def store_embedding(
        self,
        fact_id: str,
        content: str,
        user_id: str,
        namespace: str,
        fact_type: str,
        confidence: float,
        metadata: Dict[str, Any],
    ) -> Optional[str]:
        """Store an embedding for a memory fact. Returns weaviate_id or None."""
        await self.initialize()

        if self.backend == "weaviate":
            return await self._store_weaviate(
                fact_id, content, user_id, namespace, fact_type, confidence
            )
        else:
            await self._store_pgvector(fact_id, content)
            return None

    async def _store_weaviate(
        self,
        fact_id: str,
        content: str,
        user_id: str,
        namespace: str,
        fact_type: str,
        confidence: float,
    ) -> str:
        """Store embedding in Weaviate."""
        obj = {
            "class": WEAVIATE_COLLECTION_NAME,
            "properties": {
                "content": content,
                "userId": user_id,
                "namespace": namespace,
                "factType": fact_type,
                "confidence": confidence,
                "pgFactId": fact_id,
                "isActive": True,
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.weaviate_url}/v1/objects", json=obj
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def _store_pgvector(self, fact_id: str, content: str):
        """Store embedding in pgvector column."""
        embedding = await self._generate_embedding(content)
        conn = await connect_postgres(self.database_url)
        try:
            await conn.execute(
                "UPDATE public.memory_facts SET embedding = $1 WHERE id = $2",
                str(embedding),
                _to_uuid(fact_id),
            )
        finally:
            await conn.close()

    async def search_similar(
        self,
        query: str,
        user_id: str,
        namespace: str = "default",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for semantically similar memories."""
        await self.initialize()

        if self.backend == "weaviate":
            return await self._search_weaviate(query, user_id, namespace, limit)
        else:
            return await self._search_pgvector(query, user_id, namespace, limit)

    @staticmethod
    def _escape_graphql_string(value: str) -> str:
        """Escape a string for safe inclusion in GraphQL string literals."""
        return (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    async def _search_weaviate(
        self, query: str, user_id: str, namespace: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Search Weaviate for similar memories."""
        safe_query = self._escape_graphql_string(query)
        safe_user_id = self._escape_graphql_string(user_id)
        safe_namespace = self._escape_graphql_string(namespace)
        graphql = {
            "query": f"""{{
                Get {{
                    {WEAVIATE_COLLECTION_NAME}(
                        nearText: {{concepts: ["{safe_query}"]}}
                        where: {{
                            operator: And
                            operands: [
                                {{path: ["userId"], operator: Equal, valueText: "{safe_user_id}"}},
                                {{path: ["namespace"], operator: Equal, valueText: "{safe_namespace}"}},
                                {{path: ["isActive"], operator: Equal, valueBoolean: true}}
                            ]
                        }}
                        limit: {limit}
                    ) {{
                        content
                        userId
                        namespace
                        factType
                        confidence
                        pgFactId
                        _additional {{
                            distance
                            id
                        }}
                    }}
                }}
            }}"""
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.weaviate_url}/v1/graphql", json=graphql
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        objects = (
            data.get("data", {})
            .get("Get", {})
            .get(WEAVIATE_COLLECTION_NAME, [])
        )
        for obj in objects:
            results.append(
                {
                    "pg_fact_id": obj.get("pgFactId"),
                    "content": obj.get("content"),
                    "fact_type": obj.get("factType"),
                    "confidence": obj.get("confidence"),
                    "distance": obj.get("_additional", {}).get("distance"),
                    "weaviate_id": obj.get("_additional", {}).get("id"),
                }
            )
        return results

    async def _search_pgvector(
        self, query: str, user_id: str, namespace: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Search pgvector for similar memories using cosine similarity."""
        embedding = await self._generate_embedding(query)
        conn = await connect_postgres(self.database_url)
        try:
            rows = await conn.fetch(
                """
                SELECT id, content, fact_type, confidence,
                       embedding <=> $1::vector AS distance
                FROM public.memory_facts
                WHERE user_id = $2
                  AND namespace = $3
                  AND is_active = true
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $4
                """,
                str(embedding),
                _to_uuid(user_id),
                namespace,
                limit,
            )
            return [
                {
                    "pg_fact_id": str(row["id"]),
                    "content": row["content"],
                    "fact_type": row["fact_type"],
                    "confidence": row["confidence"],
                    "distance": row["distance"],
                    "weaviate_id": None,
                }
                for row in rows
            ]
        finally:
            await conn.close()

    async def delete_embedding(self, fact_id: str, weaviate_id: Optional[str] = None):
        """Delete an embedding from the vector store."""
        await self.initialize()

        if self.backend == "weaviate" and weaviate_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{self.weaviate_url}/v1/objects/"
                    f"{WEAVIATE_COLLECTION_NAME}/{weaviate_id}"
                )
                # 404 = already gone (fine). Any other non-2xx must be
                # observable: update_embedding deletes-then-recreates, so a
                # silently-failed delete leaves a stale vector that diverges
                # from the PG source of truth and pollutes recall.
                if resp.status_code not in (200, 204, 404):
                    logger.warning(
                        "Failed to delete Weaviate object %s (status %s); "
                        "stale vector may remain for fact %s",
                        weaviate_id,
                        resp.status_code,
                        fact_id,
                    )
        # pgvector: embedding is in the row, deleted when the row is deleted/updated

    async def update_embedding(
        self,
        fact_id: str,
        content: str,
        user_id: str = "",
        namespace: str = "default",
        fact_type: str = "observation",
        confidence: float = 1.0,
        weaviate_id: Optional[str] = None,
    ) -> Optional[str]:
        """Update an embedding after fact content changes. Returns new weaviate_id."""
        await self.initialize()

        if self.backend == "weaviate" and weaviate_id:
            # Delete old, create new
            await self.delete_embedding(fact_id, weaviate_id)
            return await self._store_weaviate(
                fact_id, content, user_id, namespace, fact_type, confidence
            )
        elif self.backend == "pgvector":
            await self._store_pgvector(fact_id, content)
            return None
        return None
