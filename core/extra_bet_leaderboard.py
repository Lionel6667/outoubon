"""Classement créateurs Extra bèt — reset chaque semaine (lundi → dimanche)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

from django.db.models import Count
from django.utils import timezone

_MONTHS_FR = (
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
)


def _week_bounds() -> Tuple[datetime, datetime, datetime]:
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    start_dt = timezone.make_aware(datetime.combine(week_start, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(week_end, datetime.max.time()))
    return start_dt, end_dt, week_start


def format_week_label_fr(week_start_date) -> str:
    week_end = week_start_date + timedelta(days=6)
    if week_start_date.month == week_end.month:
        return (
            f"Semaine du {week_start_date.day} au {week_end.day} "
            f"{_MONTHS_FR[week_start_date.month - 1]} {week_end.year}"
        )
    return (
        f"Semaine du {week_start_date.day} {_MONTHS_FR[week_start_date.month - 1]} "
        f"au {week_end.day} {_MONTHS_FR[week_end.month - 1]} {week_end.year}"
    )


def get_week_top_creators(limit: int = 3) -> Tuple[List[dict], str]:
    from core.models import ExtraBetPost

    start_dt, _, week_start = _week_bounds()
    creators = list(
        ExtraBetPost.objects.filter(created_at__gte=start_dt)
        .values('user__id', 'user__username', 'user__profile__first_name')
        .annotate(
            post_count=Count('id', distinct=True),
            total_likes=Count('likes', distinct=True),
        )
        .order_by('-post_count', '-total_likes')[:limit]
    )
    return creators, format_week_label_fr(week_start)
