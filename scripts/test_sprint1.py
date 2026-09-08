#!/usr/bin/env python
"""Tests Sprint 1 — ExamItemRegistry + format local."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.exam_item_registry import ExamItemRegistry, hash_item_text, extract_exam_item_hashes
from core.exercise_display import format_exercise_display_local


def test_pick_no_duplication():
    pool = [{'text': f'Question {i} unique content here'} for i in range(5)]
    reg = ExamItemRegistry()
    picked = reg.pick(pool, 10)  # demande plus que le pool
    assert len(picked) == 5, f'Expected 5, got {len(picked)}'
    texts = [p['text'] for p in picked]
    assert len(texts) == len(set(texts)), 'Duplicates found!'
    print('OK test_pick_no_duplication')


def test_exclude_hashes():
    pool = [{'text': 'Alpha question content'}, {'text': 'Beta question content'}]
    h = hash_item_text('Alpha question content')
    reg = ExamItemRegistry(exclude_hashes={h})
    picked = reg.pick(pool, 2)
    assert len(picked) == 1
    assert picked[0]['text'].startswith('Beta')
    print('OK test_exclude_hashes')


def test_hash_stable():
    t = 'Écrire et équilibrer l\'équation de combustion'
    assert hash_item_text(t) == hash_item_text(t)
    assert len(hash_item_text(t)) == 64
    print('OK test_hash_stable')


def test_extract_hashes():
    exam = {
        'parts': [{
            'sections': [{
                'items': [
                    {'text': 'Question A avec assez de contenu pour hash'},
                    {'text': 'Question B avec assez de contenu pour hash'},
                ]
            }]
        }]
    }
    hashes = extract_exam_item_hashes(exam)
    assert len(hashes) == 2
    print('OK test_extract_hashes')


def test_format_local_no_api():
    intro = 'x  1  2  3\ny  10  20  30'
    result = format_exercise_display_local('maths', intro, ['E(X) et Var(X)'])
    assert '|' in result['intro'] or 'x' in result['intro']
    assert result['questions']
    print('OK test_format_local_no_api')


def test_generate_exam_no_dup_in_exam():
    from core.gemini import generate_exam_from_db
    from core.exam_item_registry import extract_exam_item_hashes

    for subject in ('chimie', 'economie', 'histoire'):
        exam = generate_exam_from_db(subject)
        if not exam.get('parts'):
            print(f'SKIP {subject} — empty exam')
            continue
        hashes = extract_exam_item_hashes(exam)
        assert len(hashes) == len(set(hashes)), f'{subject}: duplicate items in same exam!'
        print(f'OK generate_exam_no_dup — {subject} ({len(hashes)} items)')


if __name__ == '__main__':
    test_pick_no_duplication()
    test_exclude_hashes()
    test_hash_stable()
    test_extract_hashes()
    test_format_local_no_api()
    test_generate_exam_no_dup_in_exam()
    print('\nAll Sprint 1 tests passed.')
