"""Reset les compteurs quotidiens de tous les utilisateurs pour aujourd'hui."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from accounts.models import DailyUsage
from datetime import date

today = date.today()
usages = DailyUsage.objects.filter(date=today)

print(f"Usages d'aujourd'hui ({today}):")
for u in usages:
    print(f"  user_id={u.user_id}  chat={u.chat_count}  quiz={u.quiz_count}  extra_bet={u.extra_bet_count}  ai={u.ai_request_count}")

if usages.exists():
    updated = usages.update(chat_count=0, quiz_count=0, extra_bet_count=0, ai_request_count=0)
    print(f"\n✅ {updated} enregistrement(s) remis à zéro.")
else:
    print("\nAucun usage trouvé pour aujourd'hui.")
