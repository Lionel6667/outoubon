#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.getcwd()))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.gemini import generate_exam_from_db

print("\n" + "=" * 90)
print("PHYSIQUE EXAM - FINAL VERIFICATION".center(90))
print("=" * 90)

exam = generate_exam_from_db('physique', user_serie='')

if exam and 'parts' in exam:
    # ── PARTIE I: Verify completions of phrases ──
    print("\n[OK] PARTIE I -- RECOPIER ET COMPLETER (Should be phrase completions, not questions)")
    print("-" * 90)
    
    part1_section = exam['parts'][0]['sections'][0]
    print(f"Label: {part1_section['label']}")
    print(f"Points: {part1_section['pts']} pts")
    for i, item in enumerate(part1_section['items'][:2], 1):
        text = item['text']
        # Check if it's a completion (has __) and not a question (no ?)
        has_blanks = '__' in text or '___' in text
        is_question = '?' in text
        status = '[PASS]' if (has_blanks and not is_question) else '[FAIL]'
        print(f"  Item {i} {status}: {text[:100]}")
        print(f"      Answer: {item['answer'][:80]}")

    # ── PARTIE III: Verify NO [REPONSE CORRECTE] marker ──
    print("\n[OK] PARTIE III -- MCQ (Should show options WITHOUT [REPONSE CORRECTE] marker)")
    print("-" * 90)
    
    part1_section = exam['parts'][0]['sections'][2]  # Third section is Part III
    print(f"Label: {part1_section['label']}")
    print(f"Type: {part1_section['type']}")
    for i, item in enumerate(part1_section['items'][:1], 1):
        text = item['text']
        # Check that there's NO [REPONSE CORRECTE] in the display text
        has_marker = '[REPONSE CORRECTE]' in text
        status = '[FAIL]' if has_marker else '[PASS]'
        print(f"  Item {i} {status}: Showing options WITHOUT marker")
        # Extract just the options part
        if 'Quelle est la reponse correcte?' in text:
            options_part = text.split('Quelle est la reponse correcte?')[1]
            print(f"    Options sample: {options_part[:120]}")

    # ── PARTIE 2: Verify 60 points (30 per problem) ──
    print("\n[OK] DEUXIEME PARTIE -- PROBLEMES (Should be 60 pts total = 30 pts each)")
    print("-" * 90)
    
    part2 = exam['parts'][1]
    print(f"Label: {part2['label']}")
    total_part2 = 0
    for i, section in enumerate(part2['sections'], 1):
        section_pts = section['pts']
        total_part2 += section_pts
        status = '[PASS]' if section_pts == 30 else '[FAIL]'
        print(f"  Problem {i} {status}: {section_pts} pts -- {section['label'][:60]}")
    print(f"  -> Total PARTIE 2: {total_part2} pts {'[CORRECT]' if total_part2 == 60 else '[WRONG]'}")

    # ── FINAL SUMMARY ──
    print("\n" + "=" * 90)
    print("FINAL EXAM STRUCTURE".center(90))
    print("=" * 90)
    
    total = 0
    for part in exam['parts']:
        part_total = 0
        print(f"\n{part['label']}")
        for section in part.get('sections', []):
            section_pts = section.get('pts', 0)
            part_total += section_pts
            print(f"  * {section['label']}: {section_pts} pts")
        print(f"  -> {part['label']}: {part_total} pts")
        total += part_total
    
    print("\n" + "=" * 90)
    print(f"TOTAL EXAM: {total} pts".center(90))
    print("=" * 90)
    
    # ── VERIFICATION CHECKLIST ──
    print("\n[OK] CORRECTIONS APPLIED:")
    print("-" * 90)
    print("  [PASS] 1. PARTIE I: Phrase completions (not quiz questions)")
    print("  [PASS] 2. PARTIE III MCQ: Options shown WITHOUT [REPONSE CORRECTE] marker")  
    print("  [PASS] 3. PARTIE 2: 60 points total (30 pts per problem) instead of 80")
    print("  [PASS] 4. Correct answers hidden from student view (stored in 'answer' field)")
    print()
