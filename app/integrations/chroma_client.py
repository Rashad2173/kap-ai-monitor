from __future__ import annotations

from typing import Any

import chromadb
from FlagEmbedding import BGEM3FlagModel

from app.core.config import settings


_chroma_collection = None
_embedding_model: BGEM3FlagModel | None = None


def get_chroma_collection():
    global _chroma_collection

    if _chroma_collection is not None:
        return _chroma_collection

    client = chromadb.PersistentClient(path=settings.CHROMA_PATH)

    _chroma_collection = client.get_collection(
        settings.CHROMA_COLLECTION_NAME
    )

    return _chroma_collection


def get_embedding_model() -> BGEM3FlagModel:
    global _embedding_model

    if _embedding_model is not None:
        return _embedding_model

    _embedding_model = BGEM3FlagModel(
        settings.EMBEDDING_MODEL_NAME,
        use_fp16=False,
    )

    return _embedding_model


def embed_query(question: str) -> list[float]:
    model = get_embedding_model()

    output = model.encode(
        [question],
        batch_size=1,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )

    vector = output["dense_vecs"][0]

    if hasattr(vector, "tolist"):
        return vector.tolist()

    return list(vector)


def search_chroma(
    question: str,
    bank: str = "ALL",
    n_results: int = 6,
) -> list[dict[str, Any]]:
    collection = get_chroma_collection()
    query_embedding = embed_query(question)

    where_filter = None

    if bank != "ALL":
        where_filter = {"ticker": bank}

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    sources: list[dict[str, Any]] = []

    for document, metadata, distance in zip(documents, metadatas, distances):
        metadata = metadata or {}

        sources.append(
            {
                "type": "rag_chunk",
                "ticker": metadata.get("ticker", ""),
                "period": metadata.get("period", ""),
                "chunk_type": metadata.get("chunk_type", ""),
                "table_type": metadata.get("table_type", ""),
                "metric": metadata.get("metric", ""),
                "page": metadata.get("page", ""),
                "distance": distance,
                "preview": document[:500],
                "text": document,
            }
        )

    return sources