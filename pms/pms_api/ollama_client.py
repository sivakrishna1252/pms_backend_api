import json
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


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
        raise OllamaClientError(
            "Cannot reach the Ollama server. Check OLLAMA_BASE_URL and that Ollama is running.",
            original=e,
        ) from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaClientError("Invalid JSON from Ollama.", original=e) from e
    text = (data.get("message") or {}).get("content") or ""
    return _strip_thinking_block(text).strip()


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
