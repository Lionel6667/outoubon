import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'bacia.settings'
import django
django.setup()

from core import exo_loader

# Check demo structure
demos = exo_loader.get_exercises('physique', chapter='Démonstrations', n=2)
print("DEMO STRUCTURE:")
for ex in demos[:1]:
    print(f"Keys: {list(ex.keys())}")
    print(f"Enonce: {ex.get('enonce', 'N/A')[:100]}")
    print(f"Text: {ex.get('text', 'N/A')[:100]}")
    print(f"Intro: {ex.get('intro', 'N/A')[:100]}")
    print(f"Questions: {ex.get('questions', 'N/A')}")
    print(f"Answer: {ex.get('answer', 'N/A')[:100]}")
