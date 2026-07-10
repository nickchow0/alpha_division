import json
import requests
from claude_client import build_prompt, _VALID_DECISIONS

_DEFAULT_TIMEOUT = 60


def call_ollama(
    snapshot: dict,
    base_url: str,
    model: str,
    position_direction: str = None,
) -> dict:
    prompt = build_prompt(snapshot, position_direction=position_direction)
    resp = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ollama response was not valid JSON: {exc}\nRaw: {raw}")

    for field in ("decision", "confidence", "reasoning"):
        if field not in parsed:
            raise ValueError(f"Ollama response missing field '{field}': {parsed}")

    if parsed["decision"] not in _VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision '{parsed['decision']}' — must be one of {sorted(_VALID_DECISIONS)}"
        )

    parsed["confidence"] = float(parsed["confidence"])
    if not (0.0 <= parsed["confidence"] <= 1.0):
        raise ValueError(f"Confidence {parsed['confidence']} out of range [0.0, 1.0]")

    parsed["model"] = f"ollama/{model}"
    return parsed
