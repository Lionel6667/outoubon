"""Génère 300 QCM par matière 9e AF (sans API)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.quiz_gen_9e.art_info import generate_art, generate_informatique
from scripts.quiz_gen_9e.common import (
    DB,
    OUT,
    fill_to_target,
    load_existing,
    save_subject,
    TARGET,
)
from scripts.quiz_gen_9e.histoire import generate_histoire
from scripts.quiz_gen_9e.langues import generate_anglais, generate_espagnol, generate_kreyol
from scripts.quiz_gen_9e.maths import generate_maths
from scripts.quiz_gen_9e.svt import generate_svt

GENERATORS = {
    "maths": generate_maths,
    "francais": generate_kreyol,
    "anglais": generate_anglais,
    "histoire": generate_histoire,
    "informatique": generate_informatique,
    "art": generate_art,
    "espagnol": generate_espagnol,
    "svt": generate_svt,
}

SEEDS = {
    "maths": 42,
    "francais": 43,
    "anglais": 44,
    "histoire": 45,
    "informatique": 46,
    "art": 47,
    "espagnol": 48,
    "svt": 49,
}


def main() -> None:
    print("Generation quiz 9e AF - 300 QCM/matiere (local, sans API)")
    for subject, gen in GENERATORS.items():
        fname, kind, _, _ = OUT[subject]
        path = DB / fname
        existing = load_existing(path, kind)
        print(f"\n{subject}: {len(existing)} existantes")
        filled = fill_to_target(existing, gen, TARGET, SEEDS[subject])
        save_subject(subject, filled)
        if len(filled) < TARGET:
            print(f"  ATTENTION: seulement {len(filled)}/{TARGET}")
    print("\nDone.")


if __name__ == "__main__":
    main()
