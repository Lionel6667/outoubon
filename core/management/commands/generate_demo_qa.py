"""
Management command: generate (or refresh) the public demo Q&A for the chat page.

Usage:
    python manage.py generate_demo_qa              # all subjects
    python manage.py generate_demo_qa --matiere svt maths
    python manage.py generate_demo_qa --force      # overwrite existing entries
"""
from django.core.management.base import BaseCommand
from core.models import PublicDemoQA
from core import gemini

DEMO_QUESTIONS = {
    'svt':         "Explique la différence entre la mitose et la méiose, en soulignant leurs rôles dans l'organisme.",
    'maths':       "Comment calculer la dérivée de f(x) = x³ + 2x² − 5x + 1 et trouver ses points critiques ?",
    'physique':    "Comment appliquer la 2ᵉ loi de Newton pour résoudre un problème de plan incliné avec frottement ?",
    'chimie':      "Explique le principe d'une réaction d'oxydoréduction et donne un exemple concret.",
    'philosophie': "En quoi consiste l'impératif catégorique de Kant et comment s'applique-t-il dans la vie quotidienne ?",
    'francais':    "Ki diferans ki genyen ant yon tèks naratif ak yon tèks agimantativ ? Bay egzanp chak.",
    'anglais':     "What are the main challenges Haiti faces today, and what solutions could help address them?",
    'histoire':    "Quelles sont les principales conséquences de l'occupation américaine d'Haïti (1915-1934) ?",
    'economie':    "Comment mesure-t-on le PIB d'un pays et quelle est sa différence avec le PNB ?",
}


class Command(BaseCommand):
    help = 'Generate (or refresh) public demo Q&A for the chat page via the real AI.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--matiere', nargs='+', default=list(DEMO_QUESTIONS.keys()),
            help='Subjects to generate (default: all)'
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Overwrite existing entries (default: skip if already present)'
        )

    def handle(self, *args, **options):
        targets = options['matiere']
        force   = options['force']
        ok = 0

        for matiere in targets:
            question = DEMO_QUESTIONS.get(matiere)
            if not question:
                self.stdout.write(self.style.WARNING(f'  ⚠ Matière inconnue : {matiere} — ignorée'))
                continue

            if not force and PublicDemoQA.objects.filter(matiere=matiere).exists():
                self.stdout.write(f'  → {matiere} déjà présent (utilisez --force pour écraser)')
                continue

            self.stdout.write(f'  Génération IA pour {matiere}…')
            try:
                answer = gemini.get_chat_response(
                    message=question, history=[], subject=matiere,
                    db_context='', user_profile=None, user_lang='fr',
                )
                PublicDemoQA.objects.update_or_create(
                    matiere=matiere,
                    defaults={'question': question, 'answer': answer},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✓ {matiere}'))
                ok += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  ✗ {matiere} : {exc}'))

        self.stdout.write(self.style.SUCCESS(f'\n{ok}/{len(targets)} Q&A générée(s) avec succès.'))
