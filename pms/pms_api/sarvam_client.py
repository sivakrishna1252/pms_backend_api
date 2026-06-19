import json
import logging
import re
import time
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

_SARVAM_RETRY_ATTEMPTS = 5
_SARVAM_RETRY_DELAY_SEC = 1.5


def get_sarvam_settings() -> dict[str, str | int]:
    """Read Sarvam connection from Django settings / environment."""
    return {
        "base_url": getattr(settings, "SARVAM_BASE_URL", "https://api.sarvam.ai/v1").rstrip("/"),
        "api_key": getattr(settings, "SARVAM_API_KEY", "") or "",
        "model": getattr(settings, "SARVAM_MODEL", "sarvam-30b"),
        "timeout": int(getattr(settings, "SARVAM_TIMEOUT", 120)),
    }


class SarvamClientError(Exception):
    def __init__(self, message: str, *, original: Exception | None = None):
        super().__init__(message)
        self.original = original


def _network_error_message(url: str, reason: str | BaseException) -> str:
    text = str(reason)
    if "getaddrinfo failed" in text or "11001" in text or "Name or service not known" in text:
        return (
            f"Cannot reach Sarvam at {url}. DNS/internet lookup failed ({text}). "
            "Check that this server has internet access and can resolve api.sarvam.ai "
            "(try: ping api.sarvam.ai). If you are on VPN or corporate network, allow outbound HTTPS to api.sarvam.ai."
        )
    if "timed out" in text.lower() or "timeout" in text.lower():
        return (
            f"Cannot reach Sarvam at {url}. Connection timed out. "
            "Check firewall/proxy settings or increase SARVAM_TIMEOUT in pms/.env."
        )
    return f"Cannot reach Sarvam at {url}. Original: {text}"


def sarvam_chat(
    messages,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> str:
    """
    Call Sarvam POST /v1/chat/completions (OpenAI-compatible). Returns assistant text.
    """
    cfg = get_sarvam_settings()
    base_url = (base_url or str(cfg["base_url"])).rstrip("/")
    api_key = api_key if api_key is not None else str(cfg["api_key"])
    model = model or str(cfg["model"])
    timeout = int(timeout if timeout is not None else cfg["timeout"])

    if not api_key.strip():
        raise SarvamClientError(
            "SARVAM_API_KEY is not configured. Add it to pms/.env (see pms/.env.example)."
        )

    url = f"{base_url}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        method="POST",
    )

    last_error: SarvamClientError | None = None
    raw = ""
    for attempt in range(1, _SARVAM_RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise SarvamClientError(
                f"Sarvam returned HTTP {e.code}. {detail[:500]}",
                original=e,
            ) from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            last_error = SarvamClientError(_network_error_message(url, reason), original=e)
            if attempt < _SARVAM_RETRY_ATTEMPTS:
                logger.warning("Sarvam request attempt %s/%s failed, retrying: %s", attempt, _SARVAM_RETRY_ATTEMPTS, reason)
                time.sleep(_SARVAM_RETRY_DELAY_SEC)
                continue
            raise last_error from e
    else:
        if last_error:
            raise last_error
        raise SarvamClientError(f"Cannot reach Sarvam at {url}.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SarvamClientError("Invalid JSON from Sarvam.", original=e) from e

    choices = data.get("choices") or []
    if not choices:
        raise SarvamClientError("Sarvam returned no choices.")
    message = choices[0].get("message") or {}
    text = message.get("content") or ""
    return _strip_thinking_block(text).strip()


def sarvam_health(*, base_url: str | None = None, api_key: str | None = None, timeout: int = 30) -> dict:
    """
    Verify Sarvam API key and connectivity with a minimal chat completion.
    """
    cfg = get_sarvam_settings()
    base_url = (base_url or str(cfg["base_url"])).rstrip("/")
    api_key = api_key if api_key is not None else str(cfg["api_key"])
    model = str(cfg["model"])

    if not api_key.strip():
        return {
            "reachable": False,
            "configured": False,
            "base_url": base_url,
            "configured_model": model,
            "model_available": False,
            "message": "SARVAM_API_KEY is not set.",
        }

    try:
        answer = sarvam_chat(
            [{"role": "user", "content": "Reply with OK only."}],
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    except SarvamClientError as e:
        raise SarvamClientError(str(e), original=e.original) from e

    return {
        "reachable": True,
        "configured": True,
        "base_url": base_url,
        "configured_model": model,
        "model_available": True,
        "probe_reply_excerpt": (answer or "")[:80],
    }


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
