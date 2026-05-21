from claude_client import call_claude, MODEL_HAIKU
from gemini_client import call_gemini, MODEL_FLASH


def call_ai(
    snapshot: dict,
    config: dict,
    anthropic_api_key: str = "",
    gemini_api_key: str = "",
) -> dict:
    """
    Unified AI dispatcher — routes to Claude or Gemini based on config.

    Parameters:
        snapshot:          market snapshot dict (from stream:market_snapshot)
        config:            loaded config dict (from shared.config.load_config())
        anthropic_api_key: required when ai_provider is "claude"
        gemini_api_key:    required when ai_provider is "gemini"

    Returns a dict with keys: decision (str), confidence (float),
        reasoning (str), model (str).

    Raises ValueError for unknown provider values.
    """
    analysis_cfg = config.get("analysis", {})
    provider = analysis_cfg.get("ai_provider", "claude")

    if provider == "claude":
        model = analysis_cfg.get("claude_model", MODEL_HAIKU)
        return call_claude(snapshot, anthropic_api_key, model=model)

    if provider == "gemini":
        model = analysis_cfg.get("gemini_model", MODEL_FLASH)
        return call_gemini(snapshot, gemini_api_key, model=model)

    raise ValueError(f"Unknown AI provider '{provider}' — must be 'claude' or 'gemini'")
