"""
Génère database/chapter_plans.json — plans pédagogiques statiques (zéro IA au runtime).

Usage:
  python manage.py generate_chapter_plans
  python manage.py generate_chapter_plans --subject svt
  python manage.py generate_chapter_plans --with-ai   # optionnel: 1 appel IA/chapitre (cap 4K)
"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core import pdf_loader


class Command(BaseCommand):
    help = 'Build static chapter pedagogical plans into database/chapter_plans.json'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='', help='Single subject key (e.g. svt)')
        parser.add_argument(
            '--with-ai',
            action='store_true',
            help='Use IA once per chapter to refine plans (offline only, capped context)',
        )

    def handle(self, *args, **options):
        out_path = Path(settings.BASE_DIR) / 'database' / pdf_loader.CHAPTER_PLANS_FILENAME
        existing: dict = {}
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding='utf-8-sig'))
            except Exception:
                existing = {}

        only = (options.get('subject') or '').strip().lower()
        subjects = [only] if only else list(pdf_loader._SUBJECT_NOTE_CONFIG.keys())

        updated = 0
        for subj in subjects:
            chapters = pdf_loader.get_chapters_from_note_json(subj)
            if not chapters:
                self.stdout.write(self.style.WARNING(f'  skip {subj}: no chapters'))
                continue

            subj_plans = dict(existing.get(subj) or {})
            for ch in chapters:
                num = int(ch.get('num') or ch.get('id') or 0)
                if num <= 0:
                    continue
                title = (ch.get('title') or '').strip()

                plan = pdf_loader.get_chapter_plan_from_note(subj, num)
                if options.get('with_ai') and len(plan) < 5:
                    try:
                        from core import gemini
                        ctx = pdf_loader.get_note_chapter_ai_context(subj, num, max_chars=4000)
                        if ctx:
                            ai_plan = gemini.generate_chapter_task_list(subj, title, ctx[:4000])
                            if ai_plan and len(ai_plan) >= 3:
                                plan = ai_plan
                    except Exception as exc:
                        self.stdout.write(self.style.WARNING(f'    IA skip {subj} ch{num}: {exc}'))

                if plan:
                    subj_plans[str(num)] = plan
                    updated += 1
                    self.stdout.write(f'  {subj} ch{num}: {len(plan)} steps')

            if subj_plans:
                existing[subj] = subj_plans

        out_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self.stdout.write(self.style.SUCCESS(f'Done — {updated} chapter plans → {out_path}'))
