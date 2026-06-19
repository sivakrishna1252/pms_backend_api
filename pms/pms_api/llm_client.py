"""Unified LLM client: Sarvam (cloud) with optional Ollama fallback."""

import logging

from django.conf import settings

from .ollama_client import OllamaClientError, get_ollama_settings, ollama_chat, ollama_health
from .sarvam_client import SarvamClientError, get_sarvam_settings, sarvam_chat, sarvam_health

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    def __init__(self, message: str, *, original: Exception | None = None):
        super().__init__(message)
        self.original = original


def get_ai_provider() -> str:
    return (getattr(settings, "AI_PROVIDER", "sarvam") or "sarvam").strip().lower()


def _fallback_to_ollama_enabled() -> bool:
    return bool(getattr(settings, "AI_FALLBACK_TO_OLLAMA", True))


def _try_ollama_chat(messages: list[dict]) -> tuple[str, str, str]:
    cfg = get_ollama_settings()
    answer = ollama_chat(
        messages,
        base_url=str(cfg["base_url"]),
        model=str(cfg["model"]),
        timeout=int(cfg["timeout"]),
    )
    return answer, str(cfg["model"]), "ollama"


def llm_chat(messages: list[dict]) -> tuple[str, str, str]:
    """
    Send messages to the configured provider.
    Returns (answer_text, model_name, provider_id).
    """
    provider = get_ai_provider()
    if provider == "ollama":
        try:
            return _try_ollama_chat(messages)
        except OllamaClientError as e:
            raise LLMClientError(str(e), original=e.original) from e

    cfg = get_sarvam_settings()
    try:
        answer = sarvam_chat(
            messages,
            base_url=str(cfg["base_url"]),
            api_key=str(cfg["api_key"]),
            model=str(cfg["model"]),
            timeout=int(cfg["timeout"]),
        )
        return answer, str(cfg["model"]), "sarvam"
    except SarvamClientError as e:
        if _fallback_to_ollama_enabled():
            logger.warning("Sarvam unavailable, falling back to Ollama: %s", e)
            try:
                answer, model, _prov = _try_ollama_chat(messages)
                return answer, model, "ollama-fallback"
            except OllamaClientError as ollama_err:
                raise LLMClientError(
                    f"Sarvam failed ({e}). Ollama fallback also failed ({ollama_err}).",
                    original=ollama_err.original,
                ) from ollama_err
        raise LLMClientError(str(e), original=e.original) from e


def llm_health() -> dict:
    """Provider-specific connectivity check; may report Ollama fallback when Sarvam is down."""
    provider = get_ai_provider()
    if provider == "ollama":
        cfg = get_ollama_settings()
        try:
            health = ollama_health(
                base_url=str(cfg["base_url"]),
                timeout=min(int(cfg["timeout"]), 30),
            )
        except OllamaClientError as e:
            raise LLMClientError(str(e), original=e.original) from e
        return {**health, "provider": "ollama"}

    cfg = get_sarvam_settings()
    if not str(cfg["api_key"]).strip():
        return {
            "provider": "sarvam",
            "reachable": False,
            "configured": False,
            "base_url": cfg["base_url"],
            "configured_model": cfg["model"],
            "model_available": False,
            "message": "SARVAM_API_KEY is not set.",
        }

    try:
        health = sarvam_health(
            base_url=str(cfg["base_url"]),
            api_key=str(cfg["api_key"]),
            timeout=min(int(cfg["timeout"]), 45),
        )
        return {**health, "provider": "sarvam", "fallback_available": _fallback_to_ollama_enabled()}
    except SarvamClientError as e:
        if not _fallback_to_ollama_enabled():
            raise LLMClientError(str(e), original=e.original) from e
        ocfg = get_ollama_settings()
        try:
            ohealth = ollama_health(
                base_url=str(ocfg["base_url"]),
                timeout=min(int(ocfg["timeout"]), 30),
            )
        except OllamaClientError as oerr:
            raise LLMClientError(
                f"Sarvam unreachable ({e}). Ollama fallback also unreachable ({oerr}).",
                original=oerr.original,
            ) from oerr
        return {
            **ohealth,
            "provider": "ollama-fallback",
            "sarvam_reachable": False,
            "sarvam_error": str(e),
            "reachable": True,
            "configured": True,
            "model_available": ohealth.get("model_available", True),
            "message": "Sarvam cloud is unreachable; using local Ollama fallback.",
            "fallback_active": True,
        }
