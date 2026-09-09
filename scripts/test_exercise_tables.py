#!/usr/bin/env python
"""Regression: tous les formats de tableaux d'énoncés → HTML <table>."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.exercise_display import format_exercise_display_local, _tabularize


def html_ok(text: str, min_rows: int = 2) -> bool:
    if '<table' not in text.lower() or '</table>' not in text.lower():
        return False
    if 'tbl-wrap' not in text:
        return False
    rows = len(re.findall(r'<tr>', text, re.I))
    return rows >= min_rows


def leftover_pipes(text: str) -> bool:
    """Pipes restants hors HTML (tableau markdown non converti)."""
    stripped = re.sub(r'<table[\s\S]*?</table>', '', text, flags=re.I)
    lines = [ln.strip() for ln in stripped.split('\n') if '|' in ln]
    pipe_rows = 0
    prev = False
    for ln in lines:
        cells = [c for c in ln.strip('|').split('|')]
        is_row = ln.count('|') >= 1 and len(cells) >= 2
        if is_row and prev:
            return True
        prev = is_row
        if is_row:
            pipe_rows += 1
    return False


CASES = {
    'gfm_with_sep': (
        'Loi de X :\n\n| $x$ | 1 | 2 | 3 |\n|---|---|---|---|\n| $p(x)$ | a | 1/2 | b |\n'
    ),
    'gfm_no_sep': (
        'Mesures :\n\n| Masse $x$ (kg) | 4.0 | 5.4 | 10.2 |\n| Taille $y$ (cm) | 53 | 61 | 72 |\n'
    ),
    'gfm_vertical': (
        '| Enfant | $m$ (kg) | $h$ (cm) |\n|--------|----------|----------|\n'
        '| 1 | 4.0 | 53 |\n| 2 | 5.4 | 61 |\n| 3 | 10.2 | 72 |\n'
    ),
    'gfm_align': (
        '| Left | Center | Right |\n|:-----|:------:|------:|\n| a | b | c |\n| d | e | f |\n'
    ),
    'loose_eco': (
        'Tableau de consommation sur plusieurs années :\n'
        'Année | C | Yd\n2013 | 2700 | 3200\n2014 | 3000 | 3600\n'
        '2015 | 3310 | 4000\n2016 | 3623 | 4400\n'
        'a) Calculer la Pmc entre 2013–2014.'
    ),
    'loose_wide': (
        'Année | Consommation (C) | Pmc | Revenu disponible (Rd)\n'
        '2013 | 2 700 | – | 3 200\n'
        '2014 | 3 000 | 3 600 | 3 600\n'
        '2015 | 3 310 | 4 000 | 4 000\n'
    ),
    'loose_with_sep': (
        'Pays | Recettes | Dépenses\n------|----------|----------\n'
        'X | 144 | 144\nY | 96 | 120\n'
    ),
    'missing_outer_pipes': (
        'X | Y\n---|---\n0 | 0\n1 | 1\n2 | 1.5\n'
    ),
    'collapsed_gfm': (
        'Série : | x | 1 | 2 | 3 | |---|---|---|---| | y | 10 | 20 | 30 |'
    ),
    'latex_series': (
        'Série : \\(x=1,2,3,4\\) ; \\(y=4,9,11,12\\). Calculer le point moyen.'
    ),
    'french_decimals': (
        'Tableau : \\(x=36,42,48,54,60,66\\) ; \\(y=15,1;15,5;14,4;14;13,2;11,8\\).'
    ),
    'range_dots': (
        '\\(x=1..10\\), \\(y=25,40,42,48,55,69,66,70,79,86\\).'
    ),
    'plain_xy': (
        'heures x = 2,2,6,8 ; notes y = 5,10,12,15. Tracer le nuage.'
    ),
    'xy_et': (
        'On donne x = 1, 2, 3 et y = 10, 20, 30. Calculer a.'
    ),
    'dollar_series': (
        'Série : $x=1,2,3,4$ ; $y=10,20,30,40$.'
    ),
    'points': (
        'Les points (3,3), (5,5), (6,11) et (8,15) sont donnés.'
    ),
    'classes_open': (
        'Répartition : classes [4;8[, [8;12[, [12;16[, [16;20[, [20;24[ ; effectifs 8,14,6,10,2.'
    ),
    'classes_closed': (
        'classes [0;400], [400;800], [800;1200] ; effectifs 190,410,520.'
    ),
    'valeurs_effectifs': (
        'Série : valeurs 20,19,18,17,16 ; effectifs 2,8,9,15,4.'
    ),
    'valeurs_avec': (
        'valeurs 1, 2, 3, 4 avec effectifs 5, 6, 7, 8.'
    ),
    'machine': (
        'Machine X : 48,95 ; 49,55 ; 49,99 ; 50,01\n'
        'Machine Y : 50,10 ; 49,90 ; 48,90 ; 50,00'
    ),
    'annee_pop': (
        'Évolution de la population : année 1960,1970,1980,1990,2000 ; '
        'population (millions) 2,5;3;3,6;4,4;5,2.'
    ),
    'tsv': (
        'x\t1\t2\t3\t4\ny\t10\t20\t30\t40'
    ),
    'spaces_xy': (
        'x  1  2  3\ny  10  20  30'
    ),
    'colonne_ab': (
        'Associez.\nColonne A\na) froid\nb) mince\n\nColonne B\n1. cold\n2. slim\n'
    ),
    'html_passthrough': (
        'Déjà formaté : <div class="tbl-wrap"><table><thead><tr><th>A</th></tr></thead>'
        '<tbody><tr><td>1</td></tr></table></div>'
    ),
    'uneven_cols': (
        '| A | B | C |\n| 1 | 2 |\n| 3 | 4 | 5 |\n'
    ),
    'mention_only': (
        'Dresser un tableau d\'avancement. Calculer le réactif limitant.'
    ),
    'physique_units_not_table': (
        'On maintient entre les bornes d’une prise de courant une d.d.p. sinusoïdale '
        'de valeur efficace \\(U_e = 220\\,\\text{V}\\) et de fréquence \\(50\\,\\text{Hz}\\). '
        'L’intensité efficace du courant est \\(2\\,\\text{A}\\) et la quantité de chaleur '
        'dégagée est \\(13\\,200\\,\\text{J}\\). Calculer :'
    ),
    'chimie_pile_ions': (
        'On considère la pile Zn/Zn²⁺ et Cu²⁺/Cu (E°(Cu²⁺/Cu)=+0,34V ; E°(Zn²⁺/Zn)=−0,76V). '
        'Calculer la force électromotrice.'
    ),
}


def run_cases():
    failed = []
    for name, src in CASES.items():
        out = _tabularize(src)
        if name == 'mention_only':
            if '<table' in out.lower():
                failed.append((name, 'false positive table', out[:200]))
            continue
        if name == 'physique_units_not_table':
            if '<table' in out.lower():
                failed.append((name, 'physics units became a table', out[:280]))
            elif r'\text{V}' in out and '|' in out.split('\n')[0]:
                failed.append((name, 'broken latex cells', out[:280]))
            continue
        if name == 'chimie_pile_ions':
            out = format_exercise_display_local('chimie', src, [])['intro']
            if '<table' in out.lower():
                failed.append((name, 'pile became a table', out[:280]))
            elif '<sup>2+</sup>' not in out:
                failed.append((name, 'ions not converted to html', out[:280]))
            elif re.search(r'\ba\)\s', out):
                failed.append((name, 'questions still in intro', out[:280]))
            continue
        if name == 'html_passthrough':
            if '<table' not in out.lower():
                failed.append((name, 'lost html table', out[:200]))
            continue
        if not html_ok(out):
            failed.append((name, 'no html table', out[:280]))
            continue
        if leftover_pipes(out):
            failed.append((name, 'leftover pipes', out[:280]))
    return failed


def run_real_json():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'database'
    leftover = []
    converted = 0
    scanned = 0
    texts = []

    def collect(obj, src):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ('enonce', 'intro', 'texte', 'text', 'question', 'q') and isinstance(v, str):
                    if '|' in v or 'tableau' in v.lower() or re.search(r'x\s*=', v):
                        texts.append((src, v))
                else:
                    collect(v, src)
        elif isinstance(obj, list):
            for v in obj:
                collect(v, src)

    for p in list(root.glob('exo_*.json')) + [root / 'json' / 'exams_maths.json', root / 'json' / 'exams_economie.json']:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        collect(data, p.name)

    for src, raw in texts:
        scanned += 1
        out = format_exercise_display_local('maths', raw, [])['intro']
        if '<table' in out.lower():
            converted += 1
        if leftover_pipes(out):
            leftover.append((src, raw[:120].replace('\n', ' / '), out[:180].replace('\n', ' / ')))
            if len(leftover) >= 12:
                break
    return scanned, converted, leftover


if __name__ == '__main__':
    failed = run_cases()
    if failed:
        print('FAILED CASES:')
        for item in failed:
            print(' -', item[0], ':', item[1])
            print('   ', item[2][:240])
            print()
        sys.exit(1)
    print(f'OK {len(CASES)} synthetic formats')

    from core.exercise_tutor import opening_message
    one = opening_message({'theme': 'T', 'questions': ['q1']}, 'Herby')
    four = opening_message({'theme': 'T', 'questions': ['a', 'b', 'c', 'd']}, 'Herby')
    if 'Il y a **4 questions** (a, b, c, d)' not in four:
        print('FAILED opening_message 4q:', four)
        sys.exit(1)
    if 'Il y a **' in one:
        print('FAILED opening_message 1q should not count:', one)
        sys.exit(1)
    print('OK opening_message is dynamic')

    from core.exo_loader import _extract_sub_questions
    pile = (
        'On considère la pile Zn/Zn²⁺ et Cu²⁺/Cu (E°(Cu²⁺/Cu)=+0,34V). '
        'a) Donner le schéma conventionnel et la polarité. '
        'b) Écrire l\'équation de la réaction d\'oxydoréduction. '
        'c) Calculer la force électromotrice.'
    )
    intro, qs = _extract_sub_questions(pile)
    if len(qs) != 3 or 'a) Donner' in intro or 'schéma' not in qs[0].lower():
        print('FAILED inline question split:', intro, qs)
        sys.exit(1)
    print('OK inline a) b) c) stripped from intro')

    from core.exo_loader import _prefer_fuller_questions, _sanitize_exercise
    from core.exercise_display import format_exercise_display_local
    short = ['a) Schéma et polarité.', 'b) Équation.', 'c) FEM.']
    fuller = _prefer_fuller_questions(short, qs)
    if 'Donner le schéma' not in fuller[0] or 'Équation.' in fuller[1]:
        print('FAILED prefer fuller questions:', fuller)
        sys.exit(1)
    sanitized = _sanitize_exercise({
        'theme': 'Oxydoréduction',
        'intro': pile,
        'enonce': pile,
        'questions': short,
    })
    if 'Donner le schéma' not in sanitized['questions'][0]:
        print('FAILED sanitize fuller questions:', sanitized['questions'])
        sys.exit(1)
    if 'a) Donner' in sanitized.get('intro', '') or 'a) Donner' in sanitized.get('enonce', ''):
        print('FAILED sanitize still has questions in stem:', sanitized.get('intro'))
        sys.exit(1)
    formatted = format_exercise_display_local('chimie', pile, short)
    if 'Donner le schéma' not in formatted['questions'][0]:
        print('FAILED format_exercise_display fuller questions:', formatted['questions'])
        sys.exit(1)
    if 'a) Donner' in formatted['intro']:
        print('FAILED format still has questions in intro:', formatted['intro'])
        sys.exit(1)
    print('OK full BAC wording preferred over short JSON titles')

    scanned, converted, leftover = run_real_json()
    print(f'Real JSON: scanned={scanned} converted_to_html={converted} leftover_pipe_blocks={len(leftover)}')
    if leftover:
        print('LEFTOVER:')
        for src, raw, out in leftover:
            print(f'  {src} :: {raw}')
            print(f'    OUT :: {out}')
        sys.exit(1)
    print('All table tests passed.')
