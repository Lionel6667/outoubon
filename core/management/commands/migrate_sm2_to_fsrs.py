"""
Management command: Migre les MistakeTracker (SM-2) existants vers MemoryCard (FSRS).
"""
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import MemoryCard, MistakeTracker, TopicNode


class Command(BaseCommand):
    help = "Migre les enregistrements MistakeTracker (SM-2) existants vers MemoryCard (FSRS)"

    def handle(self, *args, **options):
        mistakes = MistakeTracker.objects.all()
        total = mistakes.count()
        self.stdout.write(f"Migration de {total} enregistrements MistakeTracker vers FSRS...")

        created_cnt = 0
        now = timezone.now()

        for m in mistakes:
            # Topic canonique
            topic_str = (m.theme or m.subject or "general").strip()
            topic_slug = f"{m.subject}.{topic_str.lower().replace(' ', '_')[:40]}"
            topic, _ = TopicNode.objects.get_or_create(
                topic_id=topic_slug,
                defaults={"label": topic_str, "subject": m.subject},
            )

            # Conversion heuristique SM-2 -> FSRS
            # ease_factor (1.3 à 2.5) -> difficulty (1 à 10)
            ef = max(1.3, min(2.5, float(m.ease_factor or 2.5)))
            difficulty = 10.0 - ((ef - 1.3) / 1.2) * 9.0
            difficulty = max(1.0, min(10.0, difficulty))

            # stability approximée par interval_days
            stability = max(0.5, float(m.interval_days or 1))

            # next_review
            if m.next_review:
                next_review_dt = timezone.make_aware(
                    datetime.combine(m.next_review, datetime.min.time())
                ) if timezone.is_naive(datetime.combine(m.next_review, datetime.min.time())) else m.next_review
            else:
                next_review_dt = now + timedelta(days=1)

            card, created = MemoryCard.objects.get_or_create(
                user=m.user,
                topic=topic,
                item_uid=str(m.question_id or ""),
                defaults={
                    "stability": stability,
                    "difficulty": difficulty,
                    "retrievability": 1.0,
                    "reps": m.correct_streak + 1 if m.correct_streak else 1,
                    "lapses": m.wrong_count if m.wrong_count else 1,
                    "last_review": m.last_wrong_at or now,
                    "next_review": next_review_dt,
                },
            )
            if created:
                created_cnt += 1

        self.stdout.write(self.style.SUCCESS(f"Migration terminée : {created_cnt} MemoryCards créées sur {total}."))
