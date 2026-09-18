"""
Management command: Génère automatiquement les TopicNodes canoniques
depuis les fichiers de cours et de quiz JSON existants.
"""
import glob
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import TopicNode


class Command(BaseCommand):
    help = "Génère les TopicNodes canoniques depuis les fichiers JSON de cours et quiz"

    def handle(self, *args, **options):
        db_dir = Path(settings.BASE_DIR) / "database"
        if not db_dir.exists():
            self.stderr.write(f"Dossier {db_dir} introuvable.")
            return

        count = 0

        # 1. Parsing des fichiers quiz_*.json
        quiz_files = glob.glob(str(db_dir / "quiz_*.json"))
        for qf in quiz_files:
            filename = Path(qf).stem
            subject = filename.replace("quiz_", "").replace("_9e", "")
            try:
                with open(qf, "r", encoding="utf-8") as f:
                    items = json.load(f)
                if isinstance(items, list):
                    for item in items:
                        theme = item.get("theme") or item.get("chapitre") or "general"
                        slug = f"{subject}.{theme.lower().replace(' ', '_')[:40]}"
                        _, created = TopicNode.objects.get_or_create(
                            topic_id=slug,
                            defaults={"label": theme, "subject": subject},
                        )
                        if created:
                            count += 1
            except Exception as e:
                self.stderr.write(f"Erreur lecture {qf}: {e}")

        # 2. Parsing des notes de cours note_*.json
        note_files = glob.glob(str(db_dir / "note_*.json"))
        for nf in note_files:
            filename = Path(nf).stem
            subject = filename.replace("note_", "").replace("_ai", "").replace("de_", "").lower()
            try:
                with open(nf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chapters = data.get("chapters", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for ch in chapters:
                    title = ch.get("title") or ch.get("chapitre") or "Chapitre"
                    slug = f"{subject}.{title.lower().replace(' ', '_')[:40]}"
                    _, created = TopicNode.objects.get_or_create(
                        topic_id=slug,
                        defaults={"label": title, "subject": subject},
                    )
                    if created:
                        count += 1
            except Exception as e:
                self.stderr.write(f"Erreur lecture {nf}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Backfill terminé : {count} nouveaux TopicNodes créés."))
