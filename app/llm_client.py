"""Local Ollama client -- isolates "how to call the model" from what
tier 3 asks it and how it validates the answer (app/matching/adjudicate.py).

Uses Ollama's `format: "json"` structured-output mode to constrain the
response to valid JSON, and sets `num_predict`/`num_ctx` explicitly
rather than relying on defaults -- a small context/token budget silently
truncating a longer response was a real, previously-hit failure mode with
this exact model in a separate project, not a hypothetical concern.
"""

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


def call_ollama(prompt: str, model: str = DEFAULT_MODEL, timeout: float = 60.0) -> str:
    """Sends `prompt` to a locally running Ollama server and returns the
    raw response text (expected to be a JSON string -- parsing/validating
    it is the caller's job, not this function's).

    Raises `requests.RequestException` if Ollama isn't reachable (e.g.
    not running locally) or the request fails -- callers should expect
    this and decide how to degrade, not assume it always succeeds.
    """
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_predict": 1024, "num_ctx": 8192},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
