import logging
from claude_client import call_claude
from gemini_client import call_gemini
from ollama_client import call_ollama
from shared.enums import AIProvider, ClaudeModel, GeminiModel
from alerter import send_alert

log = logging.getLogger("analysis.ai_client")


def call_ai(
    snapshot: dict,
    config: dict,
    anthropic_api_key: str = "",
    gemini_api_key: str = "",
    position_direction: str = None,
) -> dict:
    """
    Unified AI dispatcher — routes to Claude, Gemini, or Ollama based on config.
    Falls back to Ollama when the primary provider fails, if ollama_model is set.

    Parameters:
        snapshot:           market snapshot dict (from stream:market_snapshot)
        config:             loaded config dict (from shared.config.load_config())
        anthropic_api_key:  required when ai_provider is AIProvider.CLAUDE
        gemini_api_key:     required when ai_provider is AIProvider.GEMINI
        position_direction: None (not held), "long", or "short" — shapes the prompt

    Returns a dict with keys: decision (str), confidence (float),
        reasoning (str), model (str).
    When the result comes from a fallback, includes _via_fallback: True.

    Raises ValueError for unknown provider values or missing ollama_model.
    """
    analysis_cfg = config.get("analysis", {})
    raw = analysis_cfg.get("ai_provider", AIProvider.CLAUDE)
    try:
        provider = AIProvider(raw)
    except ValueError:
        raise ValueError(
            f"Unknown AI provider '{raw}' — must be one of {[p.value for p in AIProvider]}"
        )

    ollama_model = analysis_cfg.get("ollama_model", "")
    ollama_base_url = analysis_cfg.get("ollama_base_url", "http://localhost:11434")

    if provider == AIProvider.OLLAMA:
        if not ollama_model:
            raise ValueError(
                "ollama_model must be configured in config.toml when ai_provider is 'ollama'"
            )
        return call_ollama(snapshot, ollama_base_url, ollama_model, position_direction)

    try:
        if provider == AIProvider.CLAUDE:
            model = analysis_cfg.get("claude_model", ClaudeModel.HAIKU)
            return call_claude(snapshot, anthropic_api_key, model=model,
                               position_direction=position_direction)
        model = analysis_cfg.get("gemini_model", GeminiModel.FLASH)
        return call_gemini(snapshot, gemini_api_key, model=model,
                           position_direction=position_direction)
    except Exception as primary_exc:
        if not ollama_model:
            raise
        log.info(
            "Primary AI (%s) failed: %s — falling back to ollama", provider.value, primary_exc
        )
        send_alert(
            f"[analysis] Primary AI ({provider.value}) failed, using ollama fallback: {primary_exc}"
        )
        result = call_ollama(snapshot, ollama_base_url, ollama_model, position_direction)
        result["_via_fallback"] = True
        return result
