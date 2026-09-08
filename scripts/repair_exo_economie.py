#!/usr/bin/env python3
"""Répare exo_economie.json via extraction raw_decode."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.json_cleaner import deep_fix_strings, write_json

path = ROOT / 'database' / 'exo_economie.json'
raw = path.read_bytes().decode('utf-8', errors='replace')
raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

decoder = json.JSONDecoder(strict=False)
exercices: list[dict] = []
seen: set[str] = set()

# Essai parse complet
try:
    data = decoder.decode(raw)
    for ex in data.get('exercices', []) + data.get('exercises', []):
        if isinstance(ex, dict) and ex.get('enonce'):
            exercices.append(ex)
    for ch in data.get('chapitres', []):
        if isinstance(ch, dict):
            for ex in ch.get('exercices', []):
                if isinstance(ex, dict) and ex.get('enonce'):
                    exercices.append(ex)
    print(f'Full parse OK: {len(exercices)} exercices')
except json.JSONDecodeError as e:
    print(f'Full parse failed at char {e.pos}: {e.msg}')

# Extraction itérative des objets exercice
if not exercices:
    for m in re.finditer(r'\{\s*"(?:num|id)"\s*:', raw):
        try:
            obj, end = decoder.raw_decode(raw, m.start())
            if not isinstance(obj, dict):
                continue
            enonce = (obj.get('enonce') or obj.get('intro') or '').strip()
            if not enonce or len(enonce) < 20:
                continue
            key = enonce[:100]
            if key in seen:
                continue
            seen.add(key)
            exercices.append(obj)
        except json.JSONDecodeError:
            continue

# Fallback: blocs avec "enonce" seul
if len(exercices) < 5:
    for m in re.finditer(r'\{\s*"enonce"\s*:', raw):
        try:
            obj, _ = decoder.raw_decode(raw, m.start())
            if isinstance(obj, dict) and obj.get('enonce'):
                key = str(obj['enonce'])[:100]
                if key not in seen:
                    seen.add(key)
                    exercices.append(obj)
        except json.JSONDecodeError:
            continue

print(f'Extracted: {len(exercices)} exercices')

# Normaliser structure sortie
chapitres_map: dict[str, list] = {}
for i, ex in enumerate(exercices, 1):
    ch = (ex.get('chapitre') or ex.get('theme') or ex.get('titre') or 'Économie générale').strip()
    if ch not in chapitres_map:
        chapitres_map[ch] = []
    item = {
        'num': ex.get('num') or len(chapitres_map[ch]) + 1,
        'type': ex.get('type', 'real'),
        'source': ex.get('source'),
        'enonce': ex.get('enonce', ''),
        'questions': ex.get('questions', []),
        'reponses': ex.get('reponses', ex.get('reponse', {})),
    }
    chapitres_map[ch].append(item)

chapitres = [
    {'id': idx, 'titre': titre, 'description': '', 'exercices': exos}
    for idx, (titre, exos) in enumerate(chapitres_map.items(), 1)
]

out = {
    'metadata': {
        'titre': "Banque d'exercices Économie – Baccalauréat Haïti",
        'source': 'Réparé depuis exo_economie.json',
        'chapitres_couverts': len(chapitres),
        'repaired': True,
    },
    'exercices': exercices,
    'chapitres': chapitres,
}

write_json(path, deep_fix_strings(out))
print(f'Saved {path} — {sum(len(c["exercices"]) for c in chapitres)} exercices in {len(chapitres)} chapitres')
