from core import exo_loader

exos = exo_loader.get_exercises('physique')
print(f'Total exercises: {len(exos)}')
print(f'First exercise keys: {list(exos[0].keys())}')
print(f'First exercise type: {exos[0].get("type")}')
print()

types = set(ex.get('type') for ex in exos)
print(f'Types found: {types}')
print()

demo_count = len([ex for ex in exos if ex.get('type') == 'Démonstration'])
non_demo_count = len([ex for ex in exos if ex.get('type') != 'Démonstration'])
print(f'Demonstrations: {demo_count}')
print(f'Non-Demonstrations: {non_demo_count}')
print()

# Show first few of each type
print("First 2 Demonstrations:")
for ex in [ex for ex in exos if ex.get('type') == 'Démonstration'][:2]:
    print(f"  - {ex.get('intro', ex.get('enonce', 'N/A'))[:60]}")
