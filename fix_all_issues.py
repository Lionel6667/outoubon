#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script pour corriger tous les problèmes :
1. Nettoyer les chapitres non-pédagogiques
2. Mettre à jour les comptages dans l'index
3. Préparer le filetrage des coaching cards (à faire dans views.py)
"""
import json
import os
from pathlib import Path

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
    'database/note_economie.json': [
        "SUJETS COMPLÉMENTAIRES FRÉQUENT AU BAC",
    ],
}

def clean_chapters():
    """Nettoie les chapitres non-pédagogiques."""
    results = {}
    
    for file_path, titles_to_remove in CHAPTERS_TO_REMOVE.items():
        if not os.path.exists(file_path):
            print(f"❌ Fichier manquant: {file_path}")
            continue
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            print(f"❌ JSON invalide: {file_path}")
            continue
        
        if 'chapitres' not in data:
            print(f"❌ Pas de clé 'chapitres' dans {file_path}")
            continue
        
        original_count = len(data['chapitres'])
        
        # Filtre les chapitres à garder
        chapters_before = {ch.get('titre', ''): ch for ch in data['chapitres']}
        filtered_chapters = [
            ch for ch in data['chapitres']
            if ch.get('titre', '') not in titles_to_remove
        ]
        removed_count = original_count - len(filtered_chapters)
        
        data['chapitres'] = filtered_chapters
        
        # Sauvegarde
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        results[file_path] = {
            'original': original_count,
            'final': len(filtered_chapters),
            'removed': removed_count,
        }
        
        print(f"✓ {file_path}")
        print(f"  Avant:  {original_count} chapitres")
        print(f"  Après:  {len(filtered_chapters)} chapitres")
        print(f"  Supprimés: {removed_count}")
        if removed_count > 0:
            removed_titles = [ch.get('titre', '?') for ch in data['chapitres'] 
                             if ch.get('titre', '') in titles_to_remove]
            for t in titles_to_remove:
                print(f"    • {t}")
        print()
    
    return results

def get_final_counts():
    """Récupère les comptages finaux de tous les fichiers de cours."""
    counts = {}
    
    for file_path in Path('database').glob('note_*.json'):
        if file_path.name.endswith('_ai.json'):
            continue  # Skip AI versions
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            count = len(data.get('chapitres', []))
            
            # Map filename to subject
            subject_map = {
                'note_anglais.json': 'Anglais',
                'note_espagnol.json': 'Espagnol',
                'note_art.json': 'Art',
                'note_kreyol.json': 'Kreyòl (non-JSON)',
                'note_economie.json': 'Économie',
                'note_philosophie.json': 'Philosophie',
                'note_histoire.json': 'Histoire/Sc. Sociales',
                'note_chimie.json': 'Chimie',
                'note_math.json': 'Mathématiques',
                'note_physique.json': 'Physique',
                'note_SVT.json': 'SVT',
                'note_informatique.json': 'Informatique',
            }
            
            subject = subject_map.get(file_path.name, file_path.name)
            counts[subject] = count
        except Exception as e:
            print(f"⚠ Erreur pour {file_path.name}: {e}")
    
    return counts

if __name__ == '__main__':
    print("=" * 70)
    print("NETTOYAGE DES COURS — Suppression des chapitres non-pédagogiques")
    print("=" * 70)
    print()
    
    results = clean_chapters()
    
    print("\n" + "=" * 70)
    print("COMPTAGES FINAUX — À reporter dans l'index")
    print("=" * 70)
    print()
    
    final_counts = get_final_counts()
    for subject in sorted(final_counts.keys()):
        count = final_counts[subject]
        print(f"• {subject}: {count} chapitres")
    
    print("\n" + "=" * 70)
    print("✅ Nettoyage terminé!")
    print("=" * 70)
    print("\nLes fichiers ont été mis à jour. Les comptages ci-dessus doivent")
    print("être utilisés pour mettre à jour le fichier d'index (si disponible).")
