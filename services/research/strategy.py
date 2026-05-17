# services/research/strategy.py
import ast
import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable

_BLOCKED_NAMES = {"__import__", "open", "exec", "eval", "os", "sys", "socket", "subprocess"}
_BLOCKED_MODULES = {"os", "sys", "socket", "subprocess"}
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_TIMEOUT_SECONDS = 2.0


def validate_strategy_code(code: str) -> None:
    """
    Walk the AST of code and raise ValueError if any blocked construct is present.
    Raises ValueError on syntax errors too.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Import statements are not allowed in strategy code")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_NAMES:
                raise ValueError(f"Blocked function call: {node.func.id}()")
        if isinstance(node, ast.Name) and node.id in _BLOCKED_MODULES:
            raise ValueError(f"Blocked name reference: {node.id}")


def compute_code_hash(code: str) -> str:
    """Return SHA256 hex digest of the strategy source."""
    return hashlib.sha256(code.encode()).hexdigest()


def load_strategy(code: str) -> Callable:
    """
    Compile and exec strategy code in a fresh namespace.
    Returns the generate_signal function.
    Raises ValueError if generate_signal is not defined.
    """
    namespace: dict = {}
    exec(compile(code, "<strategy>", "exec"), namespace)  # noqa: S102
    if "generate_signal" not in namespace:
        raise ValueError("Strategy code must define a 'generate_signal' function")
    return namespace["generate_signal"]


def execute_strategy(fn: Callable, snapshot: dict) -> dict:
    """
    Execute fn(snapshot) inside a thread-pool with a 2-second timeout.
    - On timeout: returns hold with reasoning.
    - On exception: returns hold with reasoning.
    - On invalid schema: raises ValueError (backtest aborts).
    """
    future = _EXECUTOR.submit(fn, snapshot)
    try:
        result = future.result(timeout=_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        return {
            "decision": "hold",
            "confidence": 0.5,
            "reasoning": "Strategy execution timed out",
        }
    except Exception as e:
        return {
            "decision": "hold",
            "confidence": 0.5,
            "reasoning": f"Strategy raised exception: {e}",
        }

    if not isinstance(result, dict):
        raise ValueError(f"Strategy returned {type(result).__name__}, expected dict")

    missing = {"decision", "confidence", "reasoning"} - set(result.keys())
    if missing:
        raise ValueError(f"Strategy result missing keys: {missing}")

    if result["decision"] not in ("buy", "sell", "hold"):
        raise ValueError(f"Invalid decision: {result['decision']!r}")

    confidence = float(result["confidence"])
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"Confidence {confidence} out of range [0, 1]")

    return result
