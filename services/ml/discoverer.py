"""services/ml/discoverer.py — Phase 3: Pattern discovery via DT + k-means.

Two parallel models find market conditions that predict profitable 10-bar
forward returns:
  - DecisionTreeClassifier (per-symbol + cross-symbol, 1yr data, max_depth=4)
  - KMeans clustering (all symbols, 5yr data, k=10)

The top-N patterns by Sharpe ratio are returned as CandidatePattern objects.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
from sklearn.tree import DecisionTreeClassifier, _tree
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from features import FEATURE_NAMES

log = logging.getLogger("ml.discoverer")

_DT_MAX_DEPTH   = 4
_KMEANS_K       = 10
_KMEANS_MIN_EXAMPLES = 50  # Spec requires ≥ 50 for cluster candidates (vs 30 for DT)
_FORWARD_RETURN_BARS = 10


@dataclass
class CandidatePattern:
    pattern_type:           str   # "decision_tree" | "cluster"
    rule_description:       str   # human-readable rule or cluster profile
    example_count:          int
    avg_forward_return_pct: float
    win_rate_pct:           float
    sharpe:                 float
    symbol:                 Optional[str] = None  # None means cross-symbol
    rows:                   list[dict] = field(default_factory=list)


# ── Label helpers ─────────────────────────────────────────────────────────────

def _label_binary(rows: list[dict]) -> list[dict]:
    """Add binary label: 1 if fwd_return_10 is in the top 30%, else 0."""
    returns = [r["fwd_return_10"] for r in rows]
    threshold = np.percentile(returns, 70)
    for r in rows:
        r["label"] = 1 if r["fwd_return_10"] >= threshold else 0
    return rows


# ── Feature matrix ────────────────────────────────────────────────────────────

def _to_matrix(rows: list[dict]) -> np.ndarray:
    """Convert feature rows to numpy matrix (shape: n_rows × 26)."""
    return np.array([[r[f] for f in FEATURE_NAMES] for r in rows])


# ── Rule extraction from decision tree ───────────────────────────────────────

def _profile_leaf(rows: list[dict], tree: DecisionTreeClassifier,
                  X: np.ndarray) -> list[tuple[str, list[float], list[dict]]]:
    """For each leaf, collect the forward returns, rule path, and source rows."""
    tree_ = tree.tree_
    leaf_ids = tree.apply(X)

    # Map each leaf node → list of forward returns and source rows
    leaf_returns: dict[int, list[float]] = {}
    leaf_rows_map: dict[int, list[dict]] = {}

    def collect_leaves(node: int) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            leaf_returns[node] = []
            leaf_rows_map[node] = []
        else:
            collect_leaves(tree_.children_left[node])
            collect_leaves(tree_.children_right[node])

    collect_leaves(0)

    for i, leaf_id in enumerate(leaf_ids):
        if leaf_id in leaf_returns:
            leaf_returns[leaf_id].append(rows[i]["fwd_return_10"])
            leaf_rows_map[leaf_id].append(rows[i])

    # Re-traverse to get the rule path per leaf
    rule_returns: list[tuple[str, list[float], list[dict]]] = []

    def recurse_with_rule(node: int, conditions: list[str]) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            if node in leaf_returns:
                rule = " AND ".join(conditions) if conditions else "all bars"
                rule_returns.append((rule, leaf_returns[node], leaf_rows_map[node]))
        else:
            fname = FEATURE_NAMES[tree_.feature[node]]
            threshold = tree_.threshold[node]
            recurse_with_rule(
                tree_.children_left[node],
                conditions + [f"{fname} <= {threshold:.4f}"],
            )
            recurse_with_rule(
                tree_.children_right[node],
                conditions + [f"{fname} > {threshold:.4f}"],
            )

    recurse_with_rule(0, [])
    return rule_returns


def _sharpe(returns: list[float]) -> float:
    """Annualised Sharpe ratio from daily returns. Returns -inf if insufficient."""
    if len(returns) < 5:
        return float("-inf")
    arr = np.array(returns)
    std = arr.std()
    if std == 0:
        return 0.0
    return float(arr.mean() / std * np.sqrt(252))


# ── Decision-tree model ───────────────────────────────────────────────────────

def _extract_dt_patterns(rows: list[dict], cfg: dict,
                          symbol: Optional[str] = None) -> list[CandidatePattern]:
    """Train a decision tree and extract candidate leaf patterns."""
    if len(rows) < max(cfg["min_examples"] * 2, 60):
        return []

    rows = _label_binary(list(rows))  # copy to avoid mutating caller's list
    X = _to_matrix(rows)
    y = np.array([r["label"] for r in rows])

    clf = DecisionTreeClassifier(max_depth=_DT_MAX_DEPTH, random_state=42)
    clf.fit(X, y)

    rule_returns = _profile_leaf(rows, clf, X)
    candidates = []

    for rule, returns, pattern_rows in rule_returns:
        if not returns:
            continue
        n = len(returns)
        avg_ret_pct = float(np.mean(returns)) * 100
        win_rate    = float(np.mean([r > 0 for r in returns])) * 100
        sh          = _sharpe(returns)

        if (n >= cfg["min_examples"]
                and avg_ret_pct >= cfg["min_forward_return_pct"]
                and win_rate >= cfg["min_win_rate_pct"]):
            candidates.append(CandidatePattern(
                pattern_type="decision_tree",
                rule_description=rule,
                example_count=n,
                avg_forward_return_pct=avg_ret_pct,
                win_rate_pct=win_rate,
                sharpe=sh,
                symbol=symbol,
                rows=pattern_rows,
            ))

    log.info("DT (%s): %d candidates from %d rows", symbol or "cross", len(candidates), len(rows))
    return candidates


# ── K-Means clustering model ─────────────────────────────────────────────────

def _extract_cluster_patterns(rows: list[dict], k: int,
                               cfg: dict) -> list[CandidatePattern]:
    """Fit k-means and profile each cluster. Returns candidate patterns."""
    if len(rows) < k * 10:
        return []

    X = _to_matrix(rows)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X_scaled)

    candidates = []
    for cluster_id in range(k):
        mask    = labels == cluster_id
        n       = int(mask.sum())
        returns = [rows[i]["fwd_return_10"] for i in range(len(rows)) if mask[i]]

        if not returns:
            continue

        avg_ret_pct = float(np.mean(returns)) * 100
        win_rate    = float(np.mean([r > 0 for r in returns])) * 100
        sh          = _sharpe(returns)

        if (n >= _KMEANS_MIN_EXAMPLES
                and avg_ret_pct >= cfg["min_forward_return_pct"]
                and win_rate >= cfg["min_win_rate_pct"]):
            # Describe cluster by top-3 most-deviated centroid features
            centroid = km.cluster_centers_[cluster_id]
            top_idx  = np.argsort(np.abs(centroid))[-3:][::-1]
            profile_parts = [
                f"{FEATURE_NAMES[i]} ≈ {scaler.mean_[i] + centroid[i] * scaler.scale_[i]:.4f}"
                for i in top_idx
            ]
            description = (
                f"Cluster {cluster_id}: {n} bars, avg_fwd={avg_ret_pct:.2f}%, "
                f"win={win_rate:.1f}% | {', '.join(profile_parts)}"
            )
            cluster_rows = [rows[i] for i in range(len(rows)) if mask[i]]
            candidates.append(CandidatePattern(
                pattern_type="cluster",
                rule_description=description,
                example_count=n,
                avg_forward_return_pct=avg_ret_pct,
                win_rate_pct=win_rate,
                sharpe=sh,
                symbol=None,  # clusters are cross-symbol
                rows=cluster_rows,
            ))

    log.info("K-Means: %d candidate clusters (k=%d, %d rows)", len(candidates), k, len(rows))
    return candidates


# ── Top-level orchestration ───────────────────────────────────────────────────

def _filter_rows_by_lookback(rows: list[dict], lookback_days: int) -> list[dict]:
    """Keep only rows within the last lookback_days calendar days."""
    cutoff = date.today() - timedelta(days=lookback_days)
    return [r for r in rows if r["bar_date"] >= cutoff]


def discover_patterns(
    features_by_symbol: dict[str, list[dict]],
    cfg: dict,
) -> list[CandidatePattern]:
    """Run DT (1yr) and k-means (5yr) discovery. Return top-N by Sharpe.

    Args:
        features_by_symbol: symbol → list of feature rows (from features.py)
        cfg: ML config dict with keys: lookback_days_momentum, lookback_days_regime,
             max_strategies_per_run, min_examples, min_forward_return_pct, min_win_rate_pct
    Returns:
        Up to cfg["max_strategies_per_run"] CandidatePattern objects, sorted by Sharpe.
    """
    all_candidates: list[CandidatePattern] = []

    # ── Decision tree: per-symbol + cross-symbol (1yr data) ──────────────────
    momentum_rows_all: list[dict] = []
    for symbol, rows in features_by_symbol.items():
        momentum_rows = _filter_rows_by_lookback(rows, cfg["lookback_days_momentum"])
        if momentum_rows:
            per_sym = _extract_dt_patterns(momentum_rows, cfg, symbol=symbol)
            all_candidates.extend(per_sym)
            momentum_rows_all.extend(momentum_rows)

    if momentum_rows_all:
        cross_sym = _extract_dt_patterns(momentum_rows_all, cfg, symbol=None)
        all_candidates.extend(cross_sym)

    # ── K-Means: all symbols, 5yr data ───────────────────────────────────────
    regime_rows_all: list[dict] = []
    for rows in features_by_symbol.values():
        regime_rows = _filter_rows_by_lookback(rows, cfg["lookback_days_regime"])
        regime_rows_all.extend(regime_rows)

    if regime_rows_all:
        cluster_candidates = _extract_cluster_patterns(
            regime_rows_all, k=_KMEANS_K, cfg=cfg
        )
        all_candidates.extend(cluster_candidates)

    # ── Top-N by Sharpe ──────────────────────────────────────────────────────
    all_candidates.sort(key=lambda p: p.sharpe, reverse=True)
    top_n = all_candidates[: cfg["max_strategies_per_run"]]
    log.info(
        "Discovery complete: %d candidates total, returning top %d",
        len(all_candidates), len(top_n),
    )
    return top_n
