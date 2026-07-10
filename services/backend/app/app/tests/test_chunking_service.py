from __future__ import annotations

import numpy as np

from chonkie.embeddings import BaseEmbeddings


class FakeEmbeddings(BaseEmbeddings):
    @property
    def dimension(self):
        return 4

    def embed(self, text: str):
        return np.array(
            [
                float(len(text)),
                float(sum(char.lower() in "aeiou" for char in text)),
                float(text.count(".")),
                1.0,
            ],
            dtype=np.float32,
        )

    def embed_batch(self, texts):
        return [self.embed(text) for text in texts]

    def similarity(self, left, right):
        return np.float32(np.dot(left, right.T) / (np.linalg.norm(left) * np.linalg.norm(right)))

    def get_tokenizer(self):
        return "character"


def test_token_chunking_returns_offsets_content_and_overlap_metadata():
    from chunking_service import ChunkRequest, chunk_text

    response = chunk_text(
        ChunkRequest(
            text="alpha beta gamma delta epsilon zeta eta theta",
            strategy="token",
            chunk_size=4,
            overlap=1,
            tokenizer="gpt2",
        )
    )

    assert response.strategy == "token"
    assert response.chunk_count >= 2
    assert response.chunks[0].index == 0
    assert response.chunks[0].start_char == 0
    assert response.chunks[0].end_char > response.chunks[0].start_char
    assert response.chunks[0].content == response.chunks[0].content.strip()
    assert response.chunks[0].token_count > 0
    assert response.metadata["overlap"] == 1
    assert response.metadata["tokenizer"] == "gpt2"


def test_chunk_request_defaults_to_recursive_strategy():
    from chunking_service import ChunkRequest

    assert ChunkRequest(text="Default chunking stays lightweight.").strategy == "recursive"


def test_recursive_chunking_preserves_sentence_or_paragraph_boundaries():
    from chunking_service import ChunkRequest, chunk_text

    text = (
        "Atlas parses documents for retrieval. "
        "Chunk boundaries should stay readable. "
        "Recursive splitting should avoid chopping every sentence in half."
    )
    response = chunk_text(
        ChunkRequest(
            text=text,
            strategy="recursive",
            chunk_size=55,
            overlap=10,
            tokenizer="character",
        )
    )

    assert response.strategy == "recursive"
    assert response.chunk_count >= 2
    assert response.metadata["overlap_applied"] is False
    assert response.metadata["overlap_ignored_reason"]
    assert all(chunk.content for chunk in response.chunks)
    assert any(chunk.content.endswith(".") for chunk in response.chunks)


def test_semantic_chunking_uses_injected_embeddings_without_model_download():
    from chunking_service import ChunkRequest, chunk_text

    response = chunk_text(
        ChunkRequest(
            text=(
                "Cats sit together. Quantum fields fluctuate. "
                "Cats chase yarn. Quantum particles spin."
            ),
            strategy="semantic",
            chunk_size=64,
            overlap=4,
            semantic_threshold=0.4,
            min_characters_per_sentence=1,
            tokenizer="character",
        ),
        semantic_embedding_model=FakeEmbeddings(),
    )

    assert response.strategy == "semantic"
    assert response.chunk_count >= 1
    assert response.metadata["overlap_applied"] is False
    assert response.metadata["semantic_threshold"] == 0.4
    assert [chunk.index for chunk in response.chunks] == list(range(response.chunk_count))
    assert all(chunk.start_char < chunk.end_char for chunk in response.chunks)
