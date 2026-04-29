from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Print Django DB settings and list public tables. "
        "Use this when pgAdmin looks empty but migrate succeeded — compare HOST/PORT/NAME to pgAdmin."
    )

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        self.stdout.write(self.style.NOTICE("Django DATABASES['default']:"))
        for key in ("ENGINE", "NAME", "USER", "HOST", "PORT"):
            self.stdout.write(f"  {key}: {db.get(key)!r}")

        engine = db.get("ENGINE", "")
        if "sqlite" in engine:
            self.stdout.write(
                self.style.WARNING(
                    "\nUsing SQLite — data is in db.sqlite3 on disk, not in PostgreSQL / pgAdmin."
                )
            )
            return

        if "postgresql" not in engine:
            self.stdout.write(self.style.WARNING(f"\nNot PostgreSQL ({engine}); skipping table list."))
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();"
            )
            row = cursor.fetchone()
            self.stdout.write(self.style.NOTICE("\nPostgreSQL session (what Django is connected to):"))
            self.stdout.write(f"  current_database: {row[0]!r}")
            self.stdout.write(f"  current_user: {row[1]!r}")
            self.stdout.write(f"  server address (TCP): {row[2]!r}")
            self.stdout.write(f"  server port: {row[3]!r}")

            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            )
            tables = [r[0] for r in cursor.fetchall()]

        self.stdout.write(self.style.NOTICE(f"\npublic schema tables: {len(tables)}"))
        for name in tables:
            self.stdout.write(f"  - {name}")
        if not tables:
            self.stdout.write(
                self.style.WARNING(
                    "No tables in public — migrations may not have run against this database, "
                    "or tables live in another schema."
                )
            )
