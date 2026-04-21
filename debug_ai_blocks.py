#!/usr/bin/env python
"""Debug AI JSON blocks to understand structure and search issues."""
import json
from pathlib import Path

db_dir = Path(__file__).parent / "database"

for subject_file in ["note_kreyol_ai.json", "note_francais_ai.json"]:
    path = db_dir / subject_file
    if not path.exists():
        print(f"[SKIP] {subject_file} not found\n")
        continue
    
    data = json.loads(path.read_text(encoding='utf-8'))
    blocks = data.get("blocks", [])
    print(f"\n{'='*70}")
    print(f"{subject_file}: {len(blocks)} total blocks")
    print(f"{'='*70}")
    
    # Show structure
    for i, b in enumerate(blocks[:10]):
        print(f"\n[{i}] ID: {b.get('id')}")
        print(f"    Chapter: {b.get('chapter')}")
        print(f"    Subchapter: {b.get('subchapter')[:50]}")
        print(f"    Type: {b.get('type')}")
        print(f"    Content preview: {b.get('content', '')[:80]}...")
    
    # Search test
    print(f"\n{'='*70}")
    print("SEARCH TEST: 'etude de texte'")
    print(f"{'='*70}")
    query = "etude de texte"
    query_tokens = set(w for w in query.lower().split() if len(w) > 2)
    print(f"Query tokens: {query_tokens}")
    
    scored = []
    for b in blocks:
        text = (b.get('content', '') + ' ' + b.get('subchapter', '')).lower()
        score = sum(1 for tok in query_tokens if tok in text)
        if score > 0:
            scored.append((score, b))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    print(f"\nMatching blocks (top 5):")
    for score, b in scored[:5]:
        print(f"\n  [score={score}] {b.get('subchapter')[:50]}")
        print(f"  Type: {b.get('type')}")
        print(f"  Content: {b.get('content')[:120]}...")
    
    if not scored:
        print(f"\n  ⚠️  No blocks matched 'etude de texte'")
        print(f"\n  Sample subchapter titles:")
        for b in blocks[:8]:
            print(f"    - {b.get('subchapter')}")
