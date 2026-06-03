import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def get_ollama_settings() -> dict[str, str | int]:
    """Read Ollama connection from Django settings / environment."""
    return {
        "base_url": getattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        "model": getattr(settings, "OLLAMA_MODEL", "gemma4:e2b"),
        "timeout": int(getattr(settings, "OLLAMA_TIMEOUT", 120)),
    }


class OllamaClientError(Exception):
    def __init__(self, message: str, *, original: Exception | None = None):
        super().__init__(message)
        self.original = original


def ollama_chat(
    messages,
    *,
    base_url: str,
    model: str,
    timeout: int = 120,
) -> str:
    """
    Call Ollama POST /api/chat (non-streaming). Returns assistant message text.
    """
    url = f"{base_url.rstrip('/')}/api/chat"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise OllamaClientError(
            f"Ollama returned HTTP {e.code}. {detail[:500]}",
            original=e,
        ) from e
    except urllib.error.URLError as e:
        hint = (
            "If Ollama runs on a remote server, set OLLAMA_HOST=0.0.0.0:11434 on that server "
            "and open firewall port 11434, or use an SSH tunnel. See pms/OLLAMA_SERVER.md."
        )
        raise OllamaClientError(
            f"Cannot reach Ollama at {url}. {hint} Original: {e.reason}",
            original=e,
        ) from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaClientError("Invalid JSON from Ollama.", original=e) from e
    text = (data.get("message") or {}).get("content") or ""
    return _strip_thinking_block(text).strip()


def ollama_health(*, base_url: str, timeout: int = 15) -> dict:
    """
    Call Ollama GET /api/tags. Returns {reachable, models, configured_model, model_available}.
    Raises OllamaClientError when the server cannot be reached or returns invalid JSON.
    """
    url = f"{base_url.rstrip('/')}/api/tags"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise OllamaClientError(
            f"Ollama returned HTTP {e.code}. {detail[:500]}",
            original=e,
        ) from e
    except urllib.error.URLError as e:
        hint = (
            "If Ollama runs on a remote server, set OLLAMA_HOST=0.0.0.0:11434 on that server "
            "and open firewall port 11434. See pms/OLLAMA_SERVER.md."
        )
        raise OllamaClientError(
            f"Cannot reach Ollama at {url}. {hint} Original: {e.reason}",
            original=e,
        ) from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaClientError("Invalid JSON from Ollama.", original=e) from e

    models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
    cfg = get_ollama_settings()
    configured = str(cfg["model"])
    model_available = configured in models or any(
        configured.split(":")[0] == (name or "").split(":")[0] for name in models
    )
    return {
        "reachable": True,
        "base_url": base_url.rstrip("/"),
        "models": models,
        "configured_model": configured,
        "model_available": model_available,
    }


# Some chat models return a hidden "thinking" block before the user-visible answer.
def _build_thinking_strip_patterns():
    open_t = "<" + "think" + ">"
    close_t = "<" + "/think" + ">"
    open_r = "<" + "redacted_thinking" + ">"
    close_r = "<" + "/redacted_thinking" + ">"
    return [
        re.compile("(?is)" + re.escape(open_t) + ".*?" + re.escape(close_t) + r"\s*"),
        re.compile("(?is)" + re.escape(open_r) + ".*?" + re.escape(close_r) + r"\s*"),
        re.compile(r"(?is)```thinking\s*.*?```\s*"),
    ]


_THINKING_PATTERNS = _build_thinking_strip_patterns()


def _strip_thinking_block(text: str) -> str:
    if not text:
        return text
    cleaned = text
    for pat in _THINKING_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return cleaned.strip() or text
