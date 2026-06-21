import os
import sys
import django

# Add root folder to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
django.setup()

from core.views import _search_ai_blocks

query = "qui fut le premier premier ministre en Haiti"
subject = "histoire"

print("Searching AI blocks...")
context = _search_ai_blocks(subject, 0, query, max_blocks=12)
print("--- CONTEXT MATCHED ---")
print(context[:1500])
print("...")
