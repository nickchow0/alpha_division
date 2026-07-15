import json
import logging
import re
import requests

log = logging.getLogger("watchdog.error_classifier")

_FALLBACK = {"action": "alert_only", "target": None, "reasoning": "classification failed", "confidence": 0.0}

_SYSTEM_PROMPT = """You are a monitoring agent for AlphaDivision, an algorithmic trading system.
You receive an error from one of these Docker services and must classify it and choose an action.

Services:
- analysis: reads market data, calls AI (Claude/Gemini/Ollama) to make trading decisions
- data: fetches market data from Alpaca/Finnhub/FRED, publishes to Redis stream
- execution: places real orders on Alpaca — NEVER restart this service automatically
- dashboard: Flask web UI showing trades and decisions
- ml: generates trading strategies using AI codegen
- alerts: sends Discord/email notifications
- research: research data service

Available actions (respond with exactly one):
- restart_service: restart a crashed or stale container (target = service name)
- rebuild_service: service is running old code after a git push (target = service name)
- restart_ollama: Ollama is not running or crashed (target = null)
- alert_only: code bug, unknown error, or execution service issue — human must fix (target = null)
- no_action: transient/noisy error like a scanner probe or rate-limit warning (target = null)

Rules:
- NEVER choose restart_service or rebuild_service with target="execution"
- If unsure, choose alert_only with a low confidence

Respond with valid JSON only:
{"action": "...", "target": "...", "reasoning": "one sentence", "confidence": 0.0-1.0}"""


def classify_error(service: str, message: str, base_url: str, model: str) -> dict:
    user_msg = f"Service: {service}\nError: {message[:800]}"
    try:
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {"type": "json_object"},
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # Strip <think>...</think> blocks that reasoning models emit before JSON
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        parsed = json.loads(content)
        if "action" not in parsed:
            log.warning("Ollama response missing 'action' field")
            return {**_FALLBACK, "reasoning": "response missing action field"}
        return {
            "action": parsed.get("action", "alert_only"),
            "target": parsed.get("target"),
            "reasoning": parsed.get("reasoning", ""),
            "confidence": float(parsed.get("confidence", 0.0)),
        }
    except Exception as exc:
        log.warning(f"Error classification failed: {exc}")
        return {**_FALLBACK, "reasoning": str(exc)}
