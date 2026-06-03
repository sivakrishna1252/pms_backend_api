from django.core.management.base import BaseCommand

from pms_api.ollama_client import OllamaClientError, get_ollama_settings, ollama_health


class Command(BaseCommand):
    help = "Verify PMS can reach the Ollama server configured in OLLAMA_BASE_URL."

    def handle(self, *args, **options):
        cfg = get_ollama_settings()
        self.stdout.write(f"OLLAMA_BASE_URL={cfg['base_url']}")
        self.stdout.write(f"OLLAMA_MODEL={cfg['model']}")
        try:
            health = ollama_health(base_url=str(cfg["base_url"]))
        except OllamaClientError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            raise SystemExit(1) from e
        self.stdout.write(self.style.SUCCESS("Ollama is reachable."))
        self.stdout.write(f"Models: {', '.join(health.get('models') or [])}")
        if health.get("model_available"):
            self.stdout.write(self.style.SUCCESS(f"Configured model {cfg['model']} is available."))
        else:
            self.stderr.write(
                self.style.ERROR(
                    f"Configured model {cfg['model']} is NOT in the list. Run: ollama pull {cfg['model']}"
                )
            )
            raise SystemExit(1)
