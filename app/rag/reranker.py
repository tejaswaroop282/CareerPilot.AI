"""Cross-encoder reranker (sentence-transformers, CPU only). Loaded lazily, once per process."""
import os
from functools import lru_cache

from app.config import get_settings


@lru_cache
def _model():
    os.environ.setdefault("HF_HOME", get_settings().hf_home)
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().reranker_model, device="cpu")


def score_pairs(query: str, docs: list[str]) -> list[float]:
    """Relevance score of each doc for the query (higher is better)."""
    if not docs:
        return []
    return [float(s) for s in _model().predict([(query, d) for d in docs], show_progress_bar=False)]
