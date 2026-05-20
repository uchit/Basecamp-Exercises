"""Local sentence-transformers embeddings with on-disk caching.

First load downloads the MiniLM model (~22MB) and embeds every chunk. The
embeddings are normalized for cosine = dot product, then cached to a .npz
file keyed by KB-content hash so subsequent runs are instant.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np


CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _kb_hash(chunks) -> str:
    """Stable hash over chunk_ids + content so cache invalidates on KB edit."""
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(c.content.encode())
    return h.hexdigest()[:16]


@dataclass
class EmbeddedChunk:
    chunk_id: str
    vector: np.ndarray  # shape (dim,), already normalized


class Embedder:
    """Singleton-ish wrapper around the local sentence-transformer model."""
    _model = None
    _model_name: str = ""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        from sentence_transformers import SentenceTransformer  # heavy import
        if Embedder._model is None or Embedder._model_name != model_name:
            Embedder._model = SentenceTransformer(model_name)
            Embedder._model_name = model_name

    def encode(self, texts: list[str]) -> np.ndarray:
        """Embed and L2-normalize. Returns shape (N, dim)."""
        vecs = Embedder._model.encode(texts, convert_to_numpy=True,
                                       normalize_embeddings=True,
                                       show_progress_bar=False)
        return vecs.astype(np.float32)


def embed_chunks(chunks: list, model_name: str = DEFAULT_MODEL,
                 cache_dir: Path | None = None) -> list[EmbeddedChunk]:
    """Embed every chunk; cache by (model, kb_hash) on disk.

    Returns chunks paired with their normalized vector — cosine similarity
    is just a dot product.
    """
    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"emb_{model_name.split('/')[-1]}_{_kb_hash(chunks)}.npz"

    if cache_path.exists():
        z = np.load(cache_path, allow_pickle=False)
        ids = list(z["ids"].astype(str))
        vecs = z["vectors"]
        by_id = {cid: vecs[i] for i, cid in enumerate(ids)}
        return [EmbeddedChunk(chunk_id=c.chunk_id, vector=by_id[c.chunk_id]) for c in chunks]

    # Cache miss — embed everything.
    embedder = Embedder(model_name)
    texts = [c.content for c in chunks]
    vecs = embedder.encode(texts)
    np.savez_compressed(cache_path,
                        ids=np.array([c.chunk_id for c in chunks]),
                        vectors=vecs)
    return [EmbeddedChunk(chunk_id=c.chunk_id, vector=vecs[i])
            for i, c in enumerate(chunks)]


def cosine_topk(query_vec: np.ndarray, embedded: list[EmbeddedChunk], k: int
                 ) -> list[tuple[str, float]]:
    """Returns [(chunk_id, score), ...] sorted descending by cosine sim."""
    matrix = np.stack([e.vector for e in embedded])  # (N, dim)
    scores = matrix @ query_vec  # (N,) — both normalized so dot = cosine
    top_idx = np.argsort(-scores)[:k]
    return [(embedded[i].chunk_id, float(scores[i])) for i in top_idx]
