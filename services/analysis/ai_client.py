from claude_client import call_claude
from gemini_client import call_gemini
from shared.enums import AIProvider, ClaudeModel, GeminiModel


def call_ai(
    snapshot: dict,
    config: dict,
    anthropic_api_key: str = "",
    gemini_api_key: str = "",
    position_direction: str = None,
) -> dict:
    """
    Unified AI dispatcher — routes to Claude or Gemini based on config.

    Parameters:
        snapshot:           market snapshot dict (from stream:market_snapshot)
        config:             loaded config dict (from shared.config.load_config())
        anthropic_api_key:  required when ai_provider is AIProvider.CLAUDE
        gemini_api_key:     required when ai_provider is AIProvider.GEMINI
        position_direction: None (not held), "long", or "short" — shapes the prompt

    Returns a dict with keys: decision (str), confidence (float),
        reasoning (str), model (str).

    Raises ValueError for unknown provider values.
    """
    analysis_cfg = config.get("analysis", {})
    raw = analysis_cfg.get("ai_provider", AIProvider.CLAUDE)
    try:
        provider = AIProvider(raw)
    except ValueError:
        raise ValueError(f"Unknown AI provider '{raw}' — must be 'claude' or 'gemini'")

    if provider == AIProvider.CLAUDE:
        model = analysis_cfg.get("claude_model", ClaudeModel.HAIKU)
        return call_claude(snapshot, anthropic_api_key, model=model,
                           position_direction=position_direction)

    model = analysis_cfg.get("gemini_model", GeminiModel.FLASH)
    return call_gemini(snapshot, gemini_api_key, model=model,
                       position_direction=position_direction)
