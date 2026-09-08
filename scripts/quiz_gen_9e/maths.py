"""Programmatic maths QCM for 9e AF."""
from __future__ import annotations

import math
import random
from fractions import Fraction

from .common import make_q, shuffle_wrong_options


def _pct(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for p in [5, 10, 12, 15, 20, 25, 30, 40, 50, 60, 75, 80]:
        base = rng.choice([40, 50, 60, 80, 100, 120, 200, 250, 400, 500])
        val = round(base * p / 100)
        if val == 0:
            continue
        qtext = f"Quel est {p} % de {base} ?"
        opts, ci = shuffle_wrong_options(
            str(val),
            [str(val + rng.randint(1, 15)), str(max(1, val - rng.randint(1, 12))), str(val + p)],
            rng,
        )
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"{p}% de {base} = ({p}/100)×{base} = {val}.",
                "Pourcentages",
                "facile",
            )
        )
    return out


def _fractions(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for _ in range(80):
        a, b = rng.randint(1, 9), rng.randint(2, 12)
        c, d = rng.randint(1, 9), rng.randint(2, 12)
        op = rng.choice(["+", "-", "×"])
        fa, fb = Fraction(a, b), Fraction(c, d)
        if op == "+":
            r = fa + fb
        elif op == "-":
            r = fa - fb
        else:
            r = fa * fb
        qtext = f"Calcule : {a}/{b} {op} {c}/{d} = ?"
        correct = f"{r.numerator}/{r.denominator}" if r.denominator != 1 else str(r.numerator)
        wrongs = [
            f"{fa.numerator + fb.numerator}/{fa.denominator + fb.denominator}",
            f"{a + c}/{b + d}",
            f"{rng.randint(1, 20)}/{rng.randint(2, 9)}",
        ]
        opts, ci = shuffle_wrong_options(correct, wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"Résultat exact : {correct}.",
                "Fractions",
                "moyen",
            )
        )
    return out


def _equations(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for _ in range(60):
        x = rng.randint(2, 25)
        a = rng.randint(2, 9)
        b = rng.randint(1, 30)
        # ax + b = c
        c = a * x + b
        qtext = f"Résous : {a}x + {b} = {c}. Quelle est la valeur de x ?"
        wrongs = [str(x + rng.randint(1, 5)), str(x - rng.randint(1, 4)), str(a + b)]
        opts, ci = shuffle_wrong_options(str(x), wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"{a}x = {c - b} donc x = {(c - b)}/{a} = {x}.",
                "Équations",
                "moyen",
            )
        )
    return out


def _pythagoras(rng: random.Random, seen: set[str]) -> list[dict]:
    triples = [(3, 4, 5), (5, 12, 13), (6, 8, 10), (8, 15, 17), (9, 12, 15)]
    out = []
    for a, b, c in triples:
        k = rng.randint(1, 4)
        a, b, c = a * k, b * k, c * k
        if rng.random() < 0.5:
            qtext = f"Dans un triangle rectangle, les côtés de l'angle droit mesurent {a} cm et {b} cm. Quelle est la longueur de l'hypoténuse ?"
            correct = str(c)
            wrongs = [str(a + b), str(c + 2), str(abs(a - b))]
        else:
            qtext = f"Dans un triangle rectangle, l'hypoténuse mesure {c} cm et un côté mesure {a} cm. Quelle est la longueur de l'autre côté ?"
            correct = str(b)
            wrongs = [str(c - a), str(c + a), str(a)]
        opts, ci = shuffle_wrong_options(correct, wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                "Théorème de Pythagore : a² + b² = c².",
                "Géométrie",
                "moyen",
            )
        )
    return out


def _stats(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for _ in range(50):
        data = sorted([rng.randint(5, 30) for _ in range(rng.randint(4, 7))])
        mean = round(sum(data) / len(data), 1)
        median = data[len(data) // 2]
        qtext = f"La série {', '.join(map(str, data))} : quelle est la moyenne ?"
        wrongs = [str(median), str(max(data)), str(min(data) + max(data))]
        opts, ci = shuffle_wrong_options(str(mean), wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"Moyenne = somme/n = {sum(data)}/{len(data)} = {mean}.",
                "Statistiques",
                "moyen",
            )
        )
    return out


def _powers(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for base in [2, 3, 4, 5, 10]:
        for exp in [2, 3, 4, -1, -2]:
            if exp < 0 and base == 0:
                continue
            val = base ** exp
            qtext = f"Calcule : {base}^{exp}"
            wrongs = [str(base * exp), str(base + exp), str(val + rng.randint(1, 5))]
            opts, ci = shuffle_wrong_options(str(val), wrongs, rng)
            out.append(
                make_q(
                    qtext,
                    opts,
                    ci,
                    f"{base}^{exp} = {val}.",
                    "Puissances",
                    "facile",
                )
            )
    return out


def _divisors(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for n in range(12, 120):
        divs = [d for d in range(1, n + 1) if n % d == 0]
        if len(divs) < 3:
            continue
        d = rng.choice(divs[1:])
        qtext = f"Le nombre {n} est divisible par :"
        wrongs = []
        for w in range(2, 15):
            if n % w != 0:
                wrongs.append(str(w))
        if len(wrongs) < 3:
            continue
        opts, ci = shuffle_wrong_options(str(d), wrongs[:3], rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"{n} ÷ {d} = {n // d}, donc {d} est un diviseur de {n}.",
                "Arithmétique",
                "facile",
            )
        )
    return out


def _areas(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for _ in range(40):
        shape = rng.choice(["rectangle", "triangle", "cercle"])
        if shape == "rectangle":
            l, w = rng.randint(3, 20), rng.randint(2, 15)
            area = l * w
            qtext = f"Un rectangle mesure {l} cm sur {w} cm. Quelle est son aire (cm²) ?"
            wrongs = [str(l + w), str(2 * (l + w)), str(l * w + 5)]
        elif shape == "triangle":
            b, h = rng.randint(4, 18), rng.randint(3, 12)
            area = b * h // 2
            qtext = f"Un triangle a une base de {b} cm et une hauteur de {h} cm. Aire (cm²) ?"
            wrongs = [str(b * h), str(b + h), str(area + 3)]
        else:
            r = rng.randint(2, 10)
            area = round(math.pi * r * r, 1)
            qtext = f"Un cercle a un rayon de {r} cm. Aire ≈ ? (π ≈ 3,14)"
            correct = f"{round(3.14 * r * r, 1)}"
            wrongs = [str(2 * 3.14 * r), str(r * r), str(round(3.14 * r, 1))]
            opts, ci = shuffle_wrong_options(correct, wrongs, rng)
            out.append(
                make_q(
                    qtext,
                    opts,
                    ci,
                    f"Aire = πr² ≈ 3,14 × {r}².",
                    "Géométrie",
                    "moyen",
                )
            )
            continue
        opts, ci = shuffle_wrong_options(str(area), wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"Aire calculée = {area} cm².",
                "Géométrie",
                "facile",
            )
        )
    return out


def _proportion(rng: random.Random, seen: set[str]) -> list[dict]:
    out = []
    for _ in range(40):
        a, b = rng.randint(2, 8), rng.randint(3, 12)
        c = rng.randint(2, 10)
        x = round(b * c / a, 1) if a else 0
        qtext = f"Proportion : {a} kg coûtent {b} $. Quel est le prix de {c} kg ?"
        wrongs = [str(b + c), str(a * c), str(round(b / c, 1))]
        opts, ci = shuffle_wrong_options(str(x), wrongs, rng)
        out.append(
            make_q(
                qtext,
                opts,
                ci,
                f"Prix = ({b}/{a}) × {c} = {x} $.",
                "Proportionnalité",
                "moyen",
            )
        )
    return out


def generate_maths(rng: random.Random, seen: set[str]) -> list[dict]:
    chunks = [
        _pct,
        _fractions,
        _equations,
        _pythagoras,
        _stats,
        _powers,
        _divisors,
        _areas,
        _proportion,
    ]
    out = []
    for fn in chunks:
        out.extend(fn(rng, seen))
        rng.shuffle(out)
    return out
