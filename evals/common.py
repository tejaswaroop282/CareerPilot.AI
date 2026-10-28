"""Small helpers shared by the evals: statistics, tables, and a way to build throwaway tracks for graph runs."""
import statistics
import uuid


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return round(statistics.mean(values), 3), round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0


def fmt(m: tuple[float, float]) -> str:
    return f"{m[0]:.3f} ±{m[1]:.3f}"


def print_table(title: str, rows: dict[str, dict]) -> None:
    if not rows:
        return
    cols = list(next(iter(rows.values())))
    w = max(len(k) for k in rows) + 2
    print(f"\n{title}")
    print("".ljust(w) + "".join(str(c).rjust(14) for c in cols))
    for label, r in rows.items():
        print(label.ljust(w) + "".join(str(r.get(c, "")).rjust(14) for c in cols))


def ndcg_at_k(relevances: list[int], k: int) -> float:
    import math

    gains = [r / math.log2(i + 2) for i, r in enumerate(relevances[:k])]
    ideal = sorted(relevances, reverse=True)[:k]
    best = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return sum(gains) / best if best else 0.0


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation without scipy (average ranks for ties)."""

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    if len(xs) < 3:
        return 0.0
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else 0.0


def new_run_ids() -> tuple[str, str]:
    """(user_id, track_id) for a throwaway eval run."""
    return f"eval-user-{uuid.uuid4().hex[:8]}", f"eval-track-{uuid.uuid4().hex[:8]}"
