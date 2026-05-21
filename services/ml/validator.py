"""services/ml/validator.py — Pre-backtest replay gate.

Replays generated strategy code against the historical rows that defined the
pattern. Rejects code that fires on fewer than min_replay_signal_rate of rows
or has fewer than min_replay_buy_rate buy signals among its fires.
"""
import ast
import logging

log = logging.getLogger("ml.validator")

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "float": float, "int": int, "isinstance": isinstance, "len": len,
    "list": list, "max": max, "min": min, "range": range,
    "round": round, "str": str, "tuple": tuple, "zip": zip,
}


def validate_against_pattern(code: str, pattern_rows: list[dict], cfg: dict) -> bool:
    """Replay code against pattern rows. Returns True if signal and buy rates pass.

    Args:
        code:         Generated strategy code string (from codegen).
        pattern_rows: Historical feature rows that defined the pattern.
        cfg:          ML config dict with optional keys:
                        min_replay_signal_rate (default 0.20)
                        min_replay_buy_rate    (default 0.40)
    """
    min_signal_rate = cfg.get("min_replay_signal_rate", 0.20)
    min_buy_rate    = cfg.get("min_replay_buy_rate", 0.40)

    if not pattern_rows:
        log.warning("Replay gate: no rows to replay against — rejecting")
        return False

    try:
        tree = ast.parse(code)
        namespace: dict = {"__builtins__": _SAFE_BUILTINS}
        exec(compile(tree, "<string>", "exec"), namespace)  # noqa: S102
        fn = namespace.get("generate_signal")
        if not callable(fn):
            log.error("Replay gate: generate_signal not callable after exec")
            return False
    except Exception as exc:
        log.error("Replay gate: code exec failed: %s", exc)
        return False

    fires = 0
    buy_fires = 0

    for row in pattern_rows:
        try:
            result = fn(row)
            decision = result.get("decision") if isinstance(result, dict) else None
            if decision in ("buy", "sell"):
                fires += 1
                if decision == "buy":
                    buy_fires += 1
        except Exception:
            pass  # individual row failures do not fail the gate

    n = len(pattern_rows)
    signal_rate = fires / n
    buy_rate    = buy_fires / fires if fires > 0 else 0.0

    passed = signal_rate >= min_signal_rate and buy_rate >= min_buy_rate
    log.info(
        "Replay gate: signal_rate=%.2f (min=%.2f) buy_rate=%.2f (min=%.2f) -> %s",
        signal_rate, min_signal_rate, buy_rate, min_buy_rate,
        "PASS" if passed else "FAIL",
    )
    return passed
