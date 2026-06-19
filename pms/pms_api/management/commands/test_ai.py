from django.core.management.base import BaseCommand

from pms_api.llm_client import LLMClientError, get_ai_provider, llm_health


class Command(BaseCommand):
    help = "Verify PMS can reach the configured AI provider (Sarvam or Ollama)."

    def handle(self, *args, **options):
        provider = get_ai_provider()
        self.stdout.write(f"AI_PROVIDER={provider}")
        try:
            health = llm_health()
        except LLMClientError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            raise SystemExit(1) from e

        if not health.get("configured", True):
            self.stderr.write(self.style.ERROR(health.get("message") or "AI not configured."))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"{provider.title()} is reachable."))
        self.stdout.write(f"Model: {health.get('configured_model')}")
        if health.get("probe_reply_excerpt"):
            self.stdout.write(f"Probe reply: {health.get('probe_reply_excerpt')}")
        if health.get("models"):
            self.stdout.write(f"Models: {', '.join(health.get('models') or [])}")
