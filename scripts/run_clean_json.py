#!/usr/bin/env python3
"""Exécute le nettoyage JSON sans Django."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.json_cleaner import (  # noqa: E402
    clean_chapters_data,
    clean_exams_data,
    clean_exo_file,
    file_size_mb,
    read_text_auto,
    repair_exo_chimie,
    write_json,
)

db = ROOT / 'database'
json_dir = db / 'json'

print('=== chapters_*.json ===')
for path in sorted(json_dir.glob('chapters_*.json')):
    before = file_size_mb(path)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    nb = len(data.get('chapters', []))
    cleaned = clean_chapters_data(data)
    write_json(path, cleaned)
    after = file_size_mb(path)
    na = len(cleaned['chapters'])
    print(f'  {path.name}: {nb} -> {na} ch, {before:.2f}MB -> {after:.2f}MB')

print('\n=== exams_*.json ===')
for path in sorted(json_dir.glob('exams_*.json')):
    before = file_size_mb(path)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    cleaned = clean_exams_data(data)
    write_json(path, cleaned)
    after = file_size_mb(path)
    print(f'  {path.name}: {before:.2f}MB -> {after:.2f}MB')

print('\n=== exo_*.json ===')
for path in sorted(db.glob('exo_*.json')):
    before = file_size_mb(path)
    if path.name == 'exo_chimie.json':
        repaired = repair_exo_chimie(path)
        n_ex = sum(len(ch.get('exercices', [])) for ch in repaired.get('chapitres', []))
        write_json(path, repaired)
        after = file_size_mb(path)
        print(f'  {path.name}: REPAIRED {n_ex} exercices, {before:.2f}MB -> {after:.2f}MB')
        continue
    try:
        data = json.loads(read_text_auto(path))
        cleaned = clean_exo_file(data)
        write_json(path, cleaned)
        after = file_size_mb(path)
        print(f'  {path.name}: {before:.2f}MB -> {after:.2f}MB')
    except json.JSONDecodeError:
        print(f'  {path.name}: SKIP (markdown, pas du JSON)')

# Markdown exercices : nettoyage léger du texte
for name in ('exo_math.json', 'exo_physique.json'):
    path = db / name
    if not path.exists():
        continue
    from core.json_cleaner import clean_ocr_text
    before = file_size_mb(path)
    text = clean_ocr_text(read_text_auto(path))
    path.write_text(text, encoding='utf-8')
    after = file_size_mb(path)
    print(f'  {name}: markdown cleaned {before:.2f}MB -> {after:.2f}MB')

eq = db / 'equation_chimique.json'
if eq.exists():
    before = file_size_mb(eq)
    data = json.loads(read_text_auto(eq))
    write_json(eq, clean_exo_file(data))
    print(f'  equation_chimique.json: {before:.2f}MB -> {file_size_mb(eq):.2f}MB')

print('\nDone.')
