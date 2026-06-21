from core import exo_loader

exos = exo_loader.get_exercises('physique', chapter='', n=110)
chapters = set(ex.get('chapter', 'Unknown') for ex in exos)
print(f'Chapters found: {sorted(chapters)}')
print()

# Try specific filters
print('With chapter="Démonstrations":')
demos_exos = exo_loader.get_exercises('physique', chapter='Démonstrations', n=10)
print(f'  Count: {len(demos_exos)}')
if demos_exos:
    print(f'  First: {demos_exos[0].get("intro", demos_exos[0].get("enonce", "N/A"))[:60]}')

print()
print('Non-demonstrations (first 2):')
non_demos = [ex for ex in exos if 'Démonstr' not in str(ex.get('chapter', ''))]
for ex in non_demos[:2]:
    chapter = ex.get('chapter', 'N/A')
    intro = ex.get('intro', ex.get('enonce', '')[:50])
    print(f'  [{chapter}] {intro}')
