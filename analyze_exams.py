import json

with open('database/json/exams_physique.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print("\n" + "="*70)
print(f"TOTAL EXAMS: {len(data['exams'])}")
print("="*70)

# Map examens par thèmes
themes = {}
for exam in data['exams']:
    file = exam['file']
    # Extract keywords from filename
    keywords = file.lower()
    
    for theme in ['condensateur', 'inductance', 'induction', 'laplace', 'courant', 
                  'oscillation', 'pendule', 'onde', 'projectile', 'balistique', 
                  'cinématique', 'mouvement', 'solenoid', 'bobine']:
        if theme in keywords:
            if theme not in themes:
                themes[theme] = []
            themes[theme].append(file)
            break

print("\nExams par thème:")
for theme in sorted(themes.keys()):
    print(f"\n{theme.upper()}: {len(themes[theme])} exams")
    for f in themes[theme][:3]:
        print(f"  - {f}")
    if len(themes[theme]) > 3:
        print(f"  ... et {len(themes[theme]) - 3} autres")
