"""services/ml/codegen.py — Phase 4: Generate strategy code via Claude API.

For each CandidatePattern, builds a prompt and calls Claude to produce a
generate_signal() function. The output is validated (AST parse, function
exists, dry-run on 3 snapshots) before being returned. One retry is allowed.
"""
import ast
import hashlib
import logging
import os
import re
from typing import Optional

import anthropic
import google.generativeai as genai

from discoverer import CandidatePattern
from ollama_codegen import call_ollama_codegen
from shared.config import load_config
from shared.enums import AIProvider, ClaudeModel

log = logging.getLogger("ml.codegen")

_MODEL = ClaudeModel.SONNET
_MAX_TOKENS = 1024
_GEMINI_TEMPERATURE = 0.2

# Three synthetic snapshots used for dry-run validation
_DRY_RUN_SNAPSHOTS = [
    {
        "price": 150.0, "volume": 1_500_000,
        "rsi_7": 32.0, "rsi_14": 35.0, "rsi_21": 38.0,
        "mom_5d": -0.02, "mom_10d": -0.03, "mom_20d": -0.05,
        "sma_10": 148.0, "sma_20": 148.0, "sma_50": 145.0, "sma_200": 140.0,
        "dist_sma10": 0.014, "dist_sma20": 0.014, "dist_sma50": 0.034, "dist_sma200": 0.071,
        "atr_14": 3.2, "bb_width": 0.08, "dist_bb_upper": 0.04, "dist_bb_lower": 0.03,
        "vol_zscore": 1.2, "vol_ratio": 1.25,
        "macd_line": -0.5, "macd_signal": -0.3, "macd_hist": -0.2,
        "dist_52w_high": 0.15, "dist_52w_low": 0.05, "day_of_week": 1,
    },
    {
        "price": 200.0, "volume": 800_000,
        "rsi_7": 68.0, "rsi_14": 65.0, "rsi_21": 62.0,
        "mom_5d": 0.03, "mom_10d": 0.05, "mom_20d": 0.08,
        "sma_10": 197.0, "sma_20": 195.0, "sma_50": 190.0, "sma_200": 180.0,
        "dist_sma10": 0.015, "dist_sma20": 0.026, "dist_sma50": 0.053, "dist_sma200": 0.111,
        "atr_14": 4.5, "bb_width": 0.06, "dist_bb_upper": 0.01, "dist_bb_lower": 0.05,
        "vol_zscore": -0.5, "vol_ratio": 0.73,
        "macd_line": 1.2, "macd_signal": 0.9, "macd_hist": 0.3,
        "dist_52w_high": 0.02, "dist_52w_low": 0.25, "day_of_week": 3,
    },
    {
        "price": 100.0, "volume": 1_000_000,
        "rsi_7": 52.0, "rsi_14": 50.0, "rsi_21": 49.0,
        "mom_5d": 0.0, "mom_10d": 0.01, "mom_20d": 0.02,
        "sma_10": 101.0, "sma_20": 101.0, "sma_50": 99.0, "sma_200": 95.0,
        "dist_sma10": -0.01, "dist_sma20": -0.01, "dist_sma50": 0.01, "dist_sma200": 0.053,
        "atr_14": 2.0, "bb_width": 0.05, "dist_bb_upper": 0.03, "dist_bb_lower": 0.02,
        "vol_zscore": 0.0, "vol_ratio": 1.0,
        "macd_line": 0.1, "macd_signal": 0.05, "macd_hist": 0.05,
        "dist_52w_high": 0.08, "dist_52w_low": 0.12, "day_of_week": 2,
    },
]

_VALID_DECISIONS = {"buy", "sell", "hold"}

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "float": float, "int": int, "isinstance": isinstance, "len": len,
    "list": list, "max": max, "min": min, "range": range,
    "round": round, "str": str, "tuple": tuple, "zip": zip,
}


def _extract_code_block(text: str) -> str:
    """Strip markdown fences if present, return raw code."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _validate_code(code: str) -> list:
    """Return a list of validation error strings. Empty list = valid.

    Checks:
      1. AST parse succeeds
      2. generate_signal function is defined
      3. Dry-run on 3 synthetic snapshots returns valid schema
    """
    errors = []

    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"Syntax parse error: {exc}")
        return errors  # Can't continue without a valid AST

    # 2. Function name check
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    if "generate_signal" not in function_names:
        errors.append("generate_signal function not found in generated code")
        return errors

    # 3. Dry-run check
    namespace: dict = {"__builtins__": _SAFE_BUILTINS}
    try:
        exec(compile(tree, "<string>", "exec"), namespace)  # noqa: S102
    except Exception as exc:
        errors.append(f"Code execution error: {exc}")
        return errors

    fn = namespace.get("generate_signal")
    if not callable(fn):
        errors.append("generate_signal is not callable after exec")
        return errors

    for i, snapshot in enumerate(_DRY_RUN_SNAPSHOTS):
        try:
            result = fn(snapshot)
        except Exception as exc:
            errors.append(f"Dry-run snapshot {i} raised: {exc}")
            continue

        if not isinstance(result, dict):
            errors.append(f"Snapshot {i}: expected dict, got {type(result).__name__}")
            continue
        if result.get("decision") not in _VALID_DECISIONS:
            errors.append(
                f"Snapshot {i}: decision must be buy/sell/hold, got {result.get('decision')!r}"
            )
        if not isinstance(result.get("confidence"), (int, float)):
            errors.append(f"Snapshot {i}: confidence must be numeric")
        if not isinstance(result.get("reasoning"), str):
            errors.append(f"Snapshot {i}: reasoning must be a string")

    return errors


def _build_prompt(pattern: CandidatePattern) -> str:
    """Build the Claude prompt for a given candidate pattern."""
    sym_context = f"originating symbol: {pattern.symbol}" if pattern.symbol else "cross-symbol pattern"
    return f"""You are generating a trading strategy function for an algorithmic trading system.

Pattern type: {pattern.pattern_type}
Rule/profile: {pattern.rule_description}
Historical performance: {pattern.example_count} examples, avg 10-bar return {pattern.avg_forward_return_pct:.2f}%, win rate {pattern.win_rate_pct:.1f}%
Context: {sym_context}

Write a Python function named `generate_signal` that takes a single argument `snapshot` (a dict) and implements trading logic based on the pattern above.

You MUST use ONLY these snapshot keys (no others exist):
  price, volume,
  rsi_7, rsi_14, rsi_21,
  mom_5d, mom_10d, mom_20d,
  sma_10, sma_20, sma_50, sma_200,
  dist_sma10, dist_sma20, dist_sma50, dist_sma200,
  atr_14, bb_width, dist_bb_upper, dist_bb_lower,
  vol_zscore, vol_ratio,
  macd_line, macd_signal, macd_hist,
  dist_52w_high, dist_52w_low, day_of_week

Note: sma_10/sma_20/sma_50/sma_200 are raw price values. Prefer dist_sma* variants (normalised % distance) for cross-symbol strategies.
Note: snapshot values may be None if the indicator could not be computed — guard against this where needed.

Return format — return a dict with exactly these keys:
  {{"decision": "buy" | "sell" | "hold", "confidence": 0.0–1.0, "reasoning": "short explanation"}}

Rules:
- No imports
- No external calls
- No global state
- Handle None values gracefully (use `or 0` or guard with `if x is not None`)
- Use only the snapshot keys listed above

Output ONLY the Python function, wrapped in ```python ... ``` fences. No explanation."""


def _call_claude(prompt: str, client: anthropic.Anthropic) -> str:
    """Call Claude API and return the raw text response."""
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_gemini(prompt: str, api_key: str, model: str) -> str:
    """Call Gemini API and return the raw text response."""
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=_MAX_TOKENS,
            temperature=_GEMINI_TEMPERATURE,
        ),
    )
    return response.text


def generate_strategy_code(
    pattern: CandidatePattern,
    client=None,
    provider: str = AIProvider.CLAUDE,
    model: str = _MODEL,
    gemini_api_key: Optional[str] = None,
) -> Optional[str]:
    """Generate and validate a generate_signal() function for the given pattern.

    Returns the validated code string, or None if both attempts fail.
    The caller is responsible for saving the code to the database.
    """
    provider = AIProvider(provider)

    if provider == AIProvider.CLAUDE:
        if client is None:
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    elif provider == AIProvider.GEMINI:
        if gemini_api_key is None:
            gemini_api_key = os.environ["GEMINI_API_KEY"]

    prompt = _build_prompt(pattern)

    for attempt in range(2):
        log.info("Codegen attempt %d for pattern: %.60s...", attempt + 1, pattern.rule_description)
        try:
            if provider == AIProvider.GEMINI:
                raw_text = _call_gemini(prompt, gemini_api_key, model)
            else:
                raw_text = _call_claude(prompt, client)
        except Exception as exc:  # noqa: BLE001
            log.error("API call failed (attempt %d): %s", attempt + 1, exc)
            break

        code = _extract_code_block(raw_text)
        errors = _validate_code(code)

        if not errors:
            log.info("Codegen succeeded on attempt %d", attempt + 1)
            return code

        log.warning("Codegen attempt %d invalid: %s", attempt + 1, "; ".join(errors))
        if attempt == 0:
            prompt += f"\n\nYour previous response had these errors:\n" + "\n".join(
                f"- {e}" for e in errors
            ) + "\n\nPlease fix them and try again."

    log.error("Codegen failed after 2 attempts for pattern: %.60s...", pattern.rule_description)

    cfg = load_config()
    ml_cfg = cfg.get("ml", {})
    ollama_codegen_model = ml_cfg.get("ollama_codegen_model", "")
    ollama_base_url = ml_cfg.get("ollama_base_url", "http://localhost:11434")

    if not ollama_codegen_model:
        return None

    log.info(
        "Primary codegen (%s) exhausted — falling back to ollama/%s",
        provider.value, ollama_codegen_model,
    )
    try:
        raw_text = call_ollama_codegen(prompt, ollama_base_url, ollama_codegen_model)
        code = _extract_code_block(raw_text)
        errors = _validate_code(code)
        if not errors:
            log.info("Ollama codegen succeeded")
            return code
        log.warning("Ollama codegen output invalid: %s", "; ".join(errors))
    except Exception as exc:
        log.error("Ollama codegen failed: %s", exc)

    return None


def code_hash(code: str) -> str:
    """Return a short SHA-256 hash of the code for deduplication."""
    return hashlib.sha256(code.encode()).hexdigest()[:16]
