#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os

# Configuration des chapitres à supprimer
CHAPTERS_TO_REMOVE = {
    'database/note_anglais.json': [
        "Sujets Complets Type Bac avec Corrigés",
        "Quiz Final — Questions Tirées des Vrais Examens 2019-2025",
    ],
    'database/note_espagnol.json': [
        "FICHES RÉCAPITULATIVES ESSENTIELLES",
    ],
    'database/note_art.json': [
        "SUJET COMPLET TYPE BAC – AVEC CORRECTION",
    ],
    'database/note_kreyol.json': [
        "Sijè Kiltirèl ak Sosyal",
        "Estrateji pou Egzamen",
    ],
    'database/note_economie.json': [
        "SUJETS COMPLÉMENTAIRES FRÉQUENTS AU BAC",
    ],
}

def clean_chapters():
    results = {}
    
    for file_path, titles_to_remove in CHAPTERS_TO_REMOVE.items():
        if not os.path.exists(file_path):
            print(f"❌ File not found: {file_path}")
            continue
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if 'chapitres' not in data:
            print(f"❌ No 'chapitres' key in {file_path}")
            continue
        
        original_count = len(data['chapitres'])
        
        # Filter out chapters with matching titles
        filtered_chapters = [
            ch for ch in data['chapitres']
            if ch.get('titre', '') not in titles_to_remove
        ]
        
        removed_count = original_count - len(filtered_chapters)
        data['chapitres'] = filtered_chapters
        
        # Save back
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        results[file_path] = {
            'original': original_count,
            'final': len(filtered_chapters),
            'removed': removed_count,
            'titles_removed': [ch.get('titre', '?') for ch in data['chapitres']
                             if ch.get('titre', '') in titles_to_remove]
        }
        
        print(f"✓ {file_path}")
        print(f"  Before: {original_count} chapters")
        print(f"  After:  {len(filtered_chapters)} chapters")
        print(f"  Removed: {removed_count}")
        print()
    
    return results

if __name__ == '__main__':
    print("=" * 60)
    print("CLEANING UP COURSE CHAPTERS")
    print("=" * 60)
    print()
    
    results = clean_chapters()
    
    print("\n" + "=" * 60)
    print("FINAL COUNTS FOR INDEX UPDATE")
    print("=" * 60)
    
    subject_mapping = {
        'database/note_anglais.json': 'Anglais',
        'database/note_espagnol.json': 'Espagnol',
        'database/note_art.json': 'Art',
        'database/note_kreyol.json': 'Kreyol',
        'database/note_economie.json': 'Économie',
    }
    
    for file_path, counts in results.items():
        subject = subject_mapping.get(file_path, file_path)
        print(f"{subject}: {counts['final']} chapters")
