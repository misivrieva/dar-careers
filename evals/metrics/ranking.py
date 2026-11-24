"""Ranking metrics over graded relevance labels (0 = irrelevant ... 3 = highly relevant)."""
from __future__ import annotations

import math


def precision_at_k(ranked_ids: list, rel: dict, k: int = 10, threshold: int = 2) -> float:
    """Over the first k positions (missing positions count as non-relevant)."""
    top = ranked_ids[:k]
    return sum(1 for i in top if rel.get(i, 0) >= threshold) / k


def precision_among_returned(ranked_ids: list, rel: dict, k: int = 10, threshold: int = 2) -> float | None:
    top = ranked_ids[:k]
    return (sum(1 for i in top if rel.get(i, 0) >= threshold) / len(top)) if top else None


def recall(ranked_ids: list, rel: dict, k: int = 10, threshold: int = 2) -> float | None:
    relevant = {i for i, r in rel.items() if r >= threshold}
    return (len(relevant & set(ranked_ids[:k])) / len(relevant)) if relevant else None


def ndcg_at_k(ranked_ids: list, rel: dict, k: int = 10) -> float:
    def dcg(ids):
        return sum((2 ** rel.get(i, 0) - 1) / math.log2(p + 2) for p, i in enumerate(ids[:k]))
    ideal = sorted(rel, key=lambda i: -rel[i])
    idcg = dcg(ideal)
    return dcg(ranked_ids) / idcg if idcg else 0.0
