from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

import chromadb
from FlagEmbedding import BGEM3FlagModel

from app.db.connection import SessionLocal
from app.db.models import RagChunk


CHROMA_PATH = "data/chroma"
COLLECTION_NAME = "kap_reports_bge_m3"
MODEL_NAME = "BAAI/bge-m3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="rag_chunks tablosundaki metinleri BGE-M3 ile embed edip ChromaDB'ye kaydeder."
    )

    parser.add_argument(
        "--chroma-path",
        default=CHROMA_PATH,
        help="ChromaDB local persist klasörü.",
    )

    parser.add_argument(
        "--collection-name",
        default=COLLECTION_NAME,
        help="Chroma collection adı.",
    )

    parser.add_argument(
        "--model-name",
        default=MODEL_NAME,
        help="Embedding modeli.",
    )

    parser.add_argument(
        "--db-batch-size",
        type=int,
        default=32,
        help="DB'den tek seferde alınacak chunk sayısı.",
    )

    parser.add_argument(
        "--model-batch-size",
        type=int,
        default=4,
        help="Modelin tek seferde embed edeceği metin sayısı. Bellek yetmezse 1 veya 2 yap.",
    )

    parser.add_argument(
        "--max-length",
        type=int,
        default=8192,
        help="BGE-M3 max token uzunluğu.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Test için en fazla kaç chunk işlenecek.",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Sadece pending değil, tüm chunkları yeniden ChromaDB'ye yazar.",
    )

    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help="Chroma collection'ı silip yeniden oluşturur.",
    )

    return parser.parse_args()


def detect_fp16() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def sanitize_metadata(value: Any) -> Any:
    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return value

    return json.dumps(value, ensure_ascii=False)


def build_chroma_metadata(chunk: RagChunk) -> dict[str, Any]:
    metadata = chunk.metadata_json or {}

    result = {
        "rag_chunk_id": chunk.id,
        "report_id": chunk.report_id,
        "ticker": chunk.ticker,
        "period": chunk.period or "",
        "chunk_type": chunk.chunk_type or "",
        "table_type": chunk.table_type or "",
        "metric": chunk.metric or "",
        "parent_section": chunk.parent_section or "",
        "page": chunk.page if chunk.page is not None else -1,
    }

    for key, value in metadata.items():
        safe_key = f"meta_{key}"

        if safe_key not in result:
            result[safe_key] = sanitize_metadata(value)

    return {
        key: sanitize_metadata(value)
        for key, value in result.items()
    }


def get_collection(
    chroma_path: str,
    collection_name: str,
    reset_collection: bool,
):
    client = chromadb.PersistentClient(path=chroma_path)

    if reset_collection:
        try:
            client.delete_collection(collection_name)
            print(f"Eski collection silindi: {collection_name}")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={
            "hnsw:space": "cosine",
            "model": MODEL_NAME,
        },
    )

    return collection


def fetch_chunks(session, db_batch_size: int, only_pending: bool, limit: int | None):
    query = session.query(RagChunk)

    if only_pending:
        query = query.filter(RagChunk.embedding_status == "pending")

    query = query.order_by(RagChunk.id.asc())

    if limit is not None:
        query = query.limit(limit)

    return query.limit(db_batch_size).all()


def embed_texts(
    model: BGEM3FlagModel,
    texts: list[str],
    model_batch_size: int,
    max_length: int,
) -> list[list[float]]:
    output = model.encode(
        texts,
        batch_size=model_batch_size,
        max_length=max_length,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )

    vectors = output["dense_vecs"]

    if hasattr(vectors, "tolist"):
        return vectors.tolist()

    return list(vectors)


def main() -> None:
    args = parse_args()

    use_fp16 = detect_fp16()

    print("BGE-M3 embedding başlıyor")
    print(f"Model: {args.model_name}")
    print(f"Chroma path: {args.chroma_path}")
    print(f"Collection: {args.collection_name}")
    print(f"use_fp16: {use_fp16}")
    print(f"only_pending: {not args.all}")
    print("-" * 80)

    collection = get_collection(
        chroma_path=args.chroma_path,
        collection_name=args.collection_name,
        reset_collection=args.reset_collection,
    )

    model = BGEM3FlagModel(
        args.model_name,
        use_fp16=use_fp16,
    )

    session = SessionLocal()

    processed = 0

    try:
        with tqdm(total=args.limit, desc="Embedding") as pbar:
            while True:
                remaining_limit = None

                if args.limit is not None:
                    remaining_limit = args.limit - processed

                    if remaining_limit <= 0:
                        break

                current_batch_size = args.db_batch_size

                if remaining_limit is not None:
                    current_batch_size = min(current_batch_size, remaining_limit)

                chunks = fetch_chunks(
                    session=session,
                    db_batch_size=current_batch_size,
                    only_pending=not args.all,
                    limit=current_batch_size,
                )

                if not chunks:
                    break

                texts = [chunk.text for chunk in chunks]
                embeddings = embed_texts(
                    model=model,
                    texts=texts,
                    model_batch_size=args.model_batch_size,
                    max_length=args.max_length,
                )

                ids = [f"rag_chunk_{chunk.id}" for chunk in chunks]
                metadatas = [build_chroma_metadata(chunk) for chunk in chunks]
                documents = texts

                collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=documents,
                )

                for chunk in chunks:
                    chunk.embedding_status = "done"

                session.commit()

                processed += len(chunks)
                pbar.update(len(chunks))

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()

    print("-" * 80)
    print("Embedding tamamlandı.")
    print(f"İşlenen chunk: {processed}")
    print(f"Chroma collection count: {collection.count()}")


if __name__ == "__main__":
    main()