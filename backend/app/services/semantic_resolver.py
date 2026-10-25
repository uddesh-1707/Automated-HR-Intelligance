"""Embedding-based similarity used to resolve job and interview-round references in HR instructions.

Falls back to ``None`` (callers then use lexical matching only) when embeddings are not configured or
the provider is unavailable, so scheduling never hard-fails because of the embedding service.
"""

import logging
import math
from collections import OrderedDict

from app.core.config import settings
from app.services.semantic_matching import MatchingNotConfigured, OpenAIEmbeddingClient

logger = logging.getLogger(__name__)
_CACHE_LIMIT = 512
_vector_cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def semantic_scores(query: str, candidates: list[str]) -> list[float] | None:
    """Cosine similarity between ``query`` and each candidate text, or ``None`` if unavailable."""
    query = query.strip()
    texts = [item.strip() for item in candidates]
    if not query or not texts:
        return None
    model = settings.embedding_model
    try:
        client = OpenAIEmbeddingClient()
        missing = [text for text in dict.fromkeys(texts) if (model, text) not in _vector_cache]
        to_embed = [query, *missing]
        vectors = client.embed(to_embed)
        if len(vectors) != len(to_embed):
            return None
        query_vector = vectors[0]
        for text, vector in zip(missing, vectors[1:]):
            _vector_cache[(model, text)] = vector
            while len(_vector_cache) > _CACHE_LIMIT:
                _vector_cache.popitem(last=False)
        return [_cosine(query_vector, _vector_cache[(model, text)]) for text in texts]
    except MatchingNotConfigured:
        return None
    except Exception as exc:
        logger.warning("Semantic resolution unavailable (%s); using lexical matching only", type(exc).__name__)
        return None
