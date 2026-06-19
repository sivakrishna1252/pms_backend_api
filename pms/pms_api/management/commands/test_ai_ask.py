from django.core.management.base import BaseCommand

from pms_api.ai_readonly_context import build_readonly_context_text
from pms_api.llm_client import LLMClientError, llm_chat


class Command(BaseCommand):
    help = "Ask a read-only question against the current DB snapshot via Sarvam/Ollama (admin AI flow)."

    def add_arguments(self, parser):
        parser.add_argument("question", nargs="?", default="How many tasks are delayed?")

    def handle(self, *args, **options):
        question = options["question"]
        self.stdout.write(f"Question: {question}")
        context_text = build_readonly_context_text()
        system = (
            "You are a read-only admin assistant for PMS and attendance/leave. "
            "Answer using only the JSON snapshot. Use task_status_counts and attendance_snapshot. "
            "Do not invent data or perform writes."
        )
        user_msg = f"Data snapshot (JSON, read-only):\n{context_text}\n\nAdmin question: {question}"
        try:
            answer, model, provider = llm_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            )
        except LLMClientError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            raise SystemExit(1) from e

        self.stdout.write(self.style.SUCCESS(f"\n[{provider} / {model}]\n{answer}"))
