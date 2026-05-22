import json
import google.generativeai as genai
from google.generativeai import protos

from claude_client import build_prompt  # reuse the same prompt — same task, same format

MODEL_FLASH = "gemini-2.5-flash"
MODEL_PRO   = "gemini-2.5-pro"

# Gemini 2.5 models use thinking tokens that count against max_output_tokens.
# Budget must cover thinking (~500-1500 tokens) + visible output (~100 tokens).
_MAX_OUTPUT_TOKENS = 2048

# Structured output schema — Gemini will always emit valid JSON matching this shape,
# eliminating truncation-induced parse failures.
_RESPONSE_SCHEMA = protos.Schema(
    type=protos.Type.OBJECT,
    properties={
        "decision":   protos.Schema(type=protos.Type.STRING,  enum=["buy", "sell", "hold"]),
        "confidence": protos.Schema(type=protos.Type.NUMBER),
        "reasoning":  protos.Schema(type=protos.Type.STRING),
    },
    required=["decision", "confidence", "reasoning"],
)


def call_gemini(snapshot: dict, api_key: str, model: str = MODEL_FLASH) -> dict:
    """
    Call Gemini with the market snapshot and return a parsed decision dict.

    Parameters:
        snapshot: market snapshot dict (from stream:market_snapshot)
        api_key: Google Gemini API key
        model: Gemini model ID (default: MODEL_FLASH)

    Returns a dict with keys: decision (str), confidence (float),
        reasoning (str), model (str).

    Raises ValueError if response cannot be parsed, missing required fields,
        or decision value is not one of buy/sell/hold.
    """
    genai.configure(api_key=api_key)
    prompt = build_prompt(snapshot)

    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
        ),
    )

    raw = response.text.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini response was not valid JSON: {exc}\nRaw response: {raw}")

    for field in ("decision", "confidence", "reasoning"):
        if field not in parsed:
            raise ValueError(f"Gemini response missing field '{field}': {parsed}")

    if parsed["decision"] not in ("buy", "sell", "hold"):
        raise ValueError(f"Invalid decision value '{parsed['decision']}' — must be buy, sell, or hold")

    parsed["confidence"] = float(parsed["confidence"])
    if not (0.0 <= parsed["confidence"] <= 1.0):
        raise ValueError(f"Confidence {parsed['confidence']} out of range [0.0, 1.0]")

    parsed["model"] = model
    return parsed
