import requests

_DEFAULT_TIMEOUT = 120


def call_ollama_codegen(prompt: str, base_url: str, model: str) -> str:
    """
    Call a local Ollama model for strategy code generation.
    Returns the raw text response; caller extracts and validates the code block.
    """
    resp = requests.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
