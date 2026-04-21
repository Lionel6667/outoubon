import json

files_to_check = [
    'database/note_kreyol.json',
    'database/note_kreyol_ai.json'
]

for filepath in files_to_check:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        chapters = len(data.get('chapitres', []))
        print(f'✓ {filepath}: {chapters} chapters')
        if chapters > 0:
            for i, ch in enumerate(data['chapitres'], 1):
                title = ch.get('titre', '?')
                print(f'  {i}. {title}')
    except json.JSONDecodeError as e:
        print(f'✗ {filepath}: JSON Error')
    except Exception as e:
        print(f'✗ {filepath}: {type(e).__name__}')
