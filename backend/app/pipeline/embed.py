"""Embeddings.

Used for two things: finding facts that might be about the same thing, and deciding
whether a newly seen measure name is one the registry already holds.

The model runs locally through ONNX. Candidate generation compares every new fact against
the existing corpus, so this has to be free and offline: paying an API per comparison
would make the linking stage the most expensive part of the system by a wide margin, and
would make the whole pipeline unusable without credentials.

Vectors are stored as raw float32 and loaded into a single contiguous matrix. At corpus
sizes this system will realistically see, a vectorised dot product over that matrix beats
an approximate index and has no build step, no extra dependency and no tuning.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import numpy as np

from app.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_dimensions: int | None = None


def _load_model():
    global _model, _dimensions
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        import os

        from fastembed import TextEmbedding

        settings = get_settings()
        name = settings.embedding_model
        # ONNX Runtime otherwise picks a conservative thread count and this model ends up
        # running near single-threaded, which makes the registry stage the slowest part of an
        # ingest. Capped rather than set to the core count: beyond about eight threads the
        # per-batch coordination costs more than it saves on a model this small.
        threads = settings.embedding_threads or max(1, min(8, os.cpu_count() or 1))
        logger.info("loading embedding model %s with %d threads", name, threads)
        _model = TextEmbedding(model_name=name, threads=threads)
        _dimensions = len(next(iter(_model.embed(["dimension probe"]))))
        return _model


def dimensions() -> int:
    if _dimensions is None:
        _load_model()
    return int(_dimensions or 0)


def embed_texts(texts: list[str]) -> NDArray[np.float32]:
    """Embed a batch and return L2-normalised rows.

    Normalising here means cosine similarity is a plain dot product everywhere downstream,
    which keeps the search code short and avoids recomputing norms on every query.
    """
    if not texts:
        return np.zeros((0, dimensions()), dtype=np.float32)

    model = _load_model()
    vectors = np.asarray(list(model.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def embed_text(text: str) -> NDArray[np.float32]:
    return embed_texts([text])[0]


def to_blob(vector: NDArray[np.float32]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes, dims: int) -> NDArray[np.float32]:
    return np.frombuffer(blob, dtype=np.float32, count=dims)


def stack(blobs: list[bytes], dims: int) -> NDArray[np.float32]:
    if not blobs:
        return np.zeros((0, dims), dtype=np.float32)
    return np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), dims)


def top_matches(
    query: NDArray[np.float32],
    matrix: NDArray[np.float32],
    *,
    limit: int,
    threshold: float,
) -> list[tuple[int, float]]:
    """Indices and scores of the closest rows, best first."""
    if matrix.size == 0 or limit <= 0:
        return []

    scores = matrix @ query
    count = min(limit, scores.shape[0])
    # argpartition finds the top-k without sorting the whole array, which matters once the
    # corpus is large and this runs once per new fact.
    candidates = np.argpartition(-scores, count - 1)[:count]
    ranked = candidates[np.argsort(-scores[candidates])]
    return [(int(index), float(scores[index])) for index in ranked if scores[index] >= threshold]
