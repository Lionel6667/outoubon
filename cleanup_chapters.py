#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script pour nettoyer les chapitres non-pédagogiques identifiés.
Supprime les examens complets et fiches de révision — ce ne sont pas des cours.
"""
import json

CHAPTERS_TO_REMOVE = {
    'database/note_espagnol.json': [
        'SUJETS COMPLETS TYPE BAC AVEC CORRECTIONS',
    ],
    'database/note_art.json': [
        'RÉSUMÉS ET FICHES DE RÉVISION RAPIDE',
    ],
}

def remove_chapters(filepath, titles_to_remove):
    """Supprime les chapitres spécifiés d'un fichier JSON."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Erreur lors de la lecture {filepath}: {e}")
        return None
    
    if 'chapitres' not in data:
        print(f"❌ Pas de clé 'chapitres' dans {filepath}")
        return None
    
    original_count = len(data['chapitres'])
    removed_titles_found = []
    
    # Filtre les chapitres
    filtered = []
    for ch in data['chapitres']:
        titre = ch.get('titre', '')
        if titre in titles_to_remove:
            removed_titles_found.append(titre)
        else:
            filtered.append(ch)
    
    removed_count = len(removed_titles_found)
    data['chapitres'] = filtered
    
    # Sauvegarde
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return {
        'file': filepath,
        'original': original_count,
        'final': len(filtered),
        'removed': removed_count,
        'removed_titles': removed_titles_found,
    }

print("=" * 70)
print("NETTOYAGE DES CHAPITRES NON-PÉDAGOGIQUES")
print("=" * 70)
print()

results = {}
for filepath, titles in CHAPTERS_TO_REMOVE.items():
    result = remove_chapters(filepath, titles)
    if result:
        results[filepath] = result
        print(f"✓ {filepath.split('/')[-1]}")
        print(f"  Avant: {result['original']} chapitres")
        print(f"  Après: {result['final']} chapitres")
        if result['removed'] > 0:
            print(f"  Supprimés: {result['removed']}")
            for title in result['removed_titles']:
                print(f"    • {title}")
        else:
            print(f"  Aucun chapitre supprimé (titres non trouvés)")
        print()

print("\n" + "=" * 70)
print("COMPTAGES FINAUX PAR MATIÈRE")
print("=" * 70)

