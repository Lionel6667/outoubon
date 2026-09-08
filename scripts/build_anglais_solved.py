"""Build curated anglais grammar MCQs and append solved entries."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database"
CLEAN = DB / "_mcq_clean_9e.json"
SOLVED = DB / "_gemini_responses" / "all_solved.json"
SOURCE = "exam_anglais_anglais-2011-2023-9e-af.pdf"
YEAR = "2011-2023"

# (question, options, correct letter, explanation, category, difficulty)
GRAMMAR: list[tuple[str, list[str], str, str, str, str]] = [
    (
        "Pepita ___ her sister from the US last week.",
        ["call", "called", "will call"],
        "B",
        "Le marqueur « last week » exige le past simple. On dit donc « Pepita called her sister ».",
        "Past simple",
        "facile",
    ),
    (
        "Could you tell us ___?",
        [
            "where is the office",
            "where does the office",
            "where the office is",
            "when is the office",
        ],
        "C",
        "Après « tell us », la proposition complétive doit garder l'ordre sujet-verbe. La forme correcte est « where the office is ».",
        "Indirect questions",
        "moyen",
    ),
    (
        "My mother ___ her hair in the bathroom now.",
        ["washes", "is washing", "washed", "washing"],
        "B",
        "Avec « now », l'action est en cours au moment présent : on utilise le present continuous « is washing ».",
        "Present continuous",
        "facile",
    ),
    (
        "If you walked faster, you ___ on time.",
        ["will be", "would be", "are"],
        "B",
        "C'est une conditionnelle de type 2 (If + past simple, would + base verbale). La réponse correcte est « would be ».",
        "Second conditional",
        "moyen",
    ),
    (
        "Simon speaks English fluently, ___",
        ["so does Peter", "so did Peter", "nor Peter"],
        "A",
        "Pour accorder une réponse positive au présent simple, on emploie « so + auxiliaire + sujet » : « so does Peter ».",
        "Agreement",
        "moyen",
    ),
    (
        "I don't have ___ to say about it.",
        ["Something", "Nothing", "Anything"],
        "C",
        "Dans une phrase négative, on utilise « anything » et non « something ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "Billy has lived here ___ 3 years.",
        ["ago", "for", "since"],
        "B",
        "Avec le present perfect, une durée se exprime avec « for » : « has lived here for 3 years ».",
        "For / since",
        "facile",
    ),
    (
        "Do you have ___ to eat?",
        ["something", "anything", "nothing"],
        "B",
        "Dans une question ouverte, on demande « anything » : « Do you have anything to eat? ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "Nothing ___ replace someone you truly love.",
        ["can't", "can"],
        "B",
        "La phrase signifie « Rien ne peut remplacer... ». Avec « Nothing », on emploie « can », pas « can't ».",
        "Modals",
        "moyen",
    ),
    (
        "Marc is ___ than Peter.",
        ["lot", "hotter", "hottest"],
        "B",
        "La présence de « than » indique un comparatif. La forme correcte est « hotter ».",
        "Comparatives",
        "facile",
    ),
    (
        "My parents have ___ me a wonderful gift for my birthday.",
        ["give", "gave", "given"],
        "C",
        "Avec « have », on forme le present perfect avec le participe passé : « have given ».",
        "Present perfect",
        "facile",
    ),
    (
        "He ___ get a better grade if he studied.",
        ["can", "could", "cans"],
        "B",
        "La conditionnelle de type 2 exige « could » après « if + past simple ».",
        "Second conditional",
        "moyen",
    ),
    (
        "My friend and I ___ 15 years old?",
        ["have", "are", "am"],
        "B",
        "Le sujet « My friend and I » vaut « we » ; le verbe « to be » au présent est « are ».",
        "Subject-verb agreement",
        "facile",
    ),
    (
        "I can't say ___ about that.",
        ["nothing", "anything", "something"],
        "B",
        "Après « can't », on utilise « anything » : « I can't say anything about that ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "The man ___ wrote the book was rewarded.",
        ["who", "whom", "what"],
        "A",
        "« Who » est le pronom relatif sujet qui remplace « the man » devant le verbe « wrote ».",
        "Relative pronouns",
        "moyen",
    ),
    (
        "___ water do you put in the container?",
        ["How much", "How many"],
        "A",
        "« Water » est un nom indénombrable ; on emploie donc « How much » et non « How many ».",
        "Quantifiers",
        "facile",
    ),
    (
        "Dad hasn't called me ___ three years.",
        ["since", "for", "ago"],
        "B",
        "Avec le present perfect négatif, une durée s'exprime avec « for » : « for three years ».",
        "For / since",
        "facile",
    ),
    (
        "You can't be successful without ___.",
        ["working", "to work", "worked"],
        "A",
        "Après « without », le verbe se met en forme -ing : « without working ».",
        "Gerund",
        "moyen",
    ),
    (
        "You don't like turnips, ___ you?",
        ["did", "don't", "do"],
        "C",
        "La phrase principale est négative au présent simple ; la question tag est positive : « do you? ».",
        "Question tags",
        "moyen",
    ),
    (
        "If I saw him I ___ with him.",
        ["would go", "will go", "went"],
        "A",
        "Dans une conditionnelle de type 2, la principale prend « would + base verbale » : « would go ».",
        "Second conditional",
        "moyen",
    ),
    (
        "Do you have ___ to declare?",
        ["something", "nothing", "anything"],
        "C",
        "Dans ce type de question en douane, on emploie « anything » : « anything to declare? ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "Peter went to the beach, ___?",
        ["didn't he", "Did he", "Doesn't he"],
        "A",
        "La phrase est au past simple affirmatif ; la question tag négative est « didn't he? ».",
        "Question tags",
        "facile",
    ),
    (
        "Your English ___ perfect if you listened to the tapes.",
        ["is", "Will be", "Would be"],
        "C",
        "Avec « if you listened » (type 2), le résultat s'exprime avec « would be ».",
        "Second conditional",
        "moyen",
    ),
    (
        "Timmy's work is ___ than Paul's.",
        ["worse", "bad", "worst"],
        "A",
        "Devant « than », on emploie le comparatif de « bad », soit « worse ».",
        "Comparatives",
        "facile",
    ),
    (
        "Bob and Billy are your neighbors, ___?",
        ["Are they", "aren't they", "Will they"],
        "B",
        "La phrase principale est affirmative ; la question tag négative est « aren't they? ».",
        "Question tags",
        "facile",
    ),
    (
        "Jerry went to the beach, ___",
        ["I do too", "so did I", "so do I"],
        "B",
        "La première action est au passé (« went ») ; l'accord se fait avec « so did I ».",
        "Agreement",
        "moyen",
    ),
    (
        "I bring my umbrella, it ___ rain.",
        ["might", "must", "is"],
        "A",
        "« Might rain » exprime une possibilité future. « Must » serait trop fort et « is rain » est incorrect.",
        "Modals",
        "facile",
    ),
    (
        "Mrs. Diane sees a nice cat on the roof, ___ she?",
        ["did", "hasn't", "doesn't"],
        "C",
        "La phrase principale est au présent simple affirmatif ; la tag question est « doesn't she? ».",
        "Question tags",
        "facile",
    ),
    (
        "No one ___ do it? It wasn't easy.",
        ["can", "could", "can't"],
        "B",
        "Le contexte passif « It wasn't easy » indique le passé ; on emploie donc « could ».",
        "Modals",
        "moyen",
    ),
    (
        "Have you already ___ the book?",
        ["seen", "see", "saw"],
        "A",
        "Après « Have you already », on utilise le participe passé : « seen ».",
        "Present perfect",
        "facile",
    ),
    (
        "If you walked faster you ___ the train.",
        ["will catch", "would catch", "catching"],
        "B",
        "Avec « If you walked » (type 2), le résultat est « would catch ».",
        "Second conditional",
        "moyen",
    ),
    (
        "Last year, we ___ the garden.",
        ["are visiting", "visit", "visited"],
        "C",
        "« Last year » impose le past simple : « visited ».",
        "Past simple",
        "facile",
    ),
    (
        "The box is light. There is ___ in it.",
        ["something", "nothing", "anything"],
        "B",
        "Si la boîte est légère, elle est vide : « There is nothing in it ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "Jeff has been sick ___ three weeks.",
        ["since", "for", "ago"],
        "B",
        "Avec « has been sick », une durée se marque avec « for three weeks ».",
        "For / since",
        "facile",
    ),
    (
        "The box is so heavy. There must be ___ in it.",
        ["anything", "nothing", "something"],
        "C",
        "Une boîte lourde contient probablement quelque chose : « something ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "___ here likes English.",
        ["Nobody", "everybody", "anybody"],
        "B",
        "Le sens positif « aime l'anglais » correspond à « everybody here likes English ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "___ can live without water.",
        ["Some one", "no one", "everyone"],
        "B",
        "Personne ne peut vivre sans eau : la forme correcte est « No one can live without water ».",
        "Indefinite pronouns",
        "facile",
    ),
    (
        "Dave speaks English fluently, ___",
        ["so does Garry", "so did Garry", "nor Garry"],
        "A",
        "Au présent simple, l'accord positif s'exprime par « so does + sujet ».",
        "Agreement",
        "moyen",
    ),
]


def to_clean(q: str, opts: list[str]) -> dict:
    return {
        "question": q,
        "options": opts,
        "source": SOURCE,
        "year": YEAR,
    }


def to_solved(q: str, opts: list[str], letter: str, expl: str, cat: str, diff: str) -> dict:
    return {
        "question": q,
        "options": opts,
        "correct": letter,
        "explanation": expl,
        "category": cat,
        "difficulty": diff,
        "timer_seconds": 30,
    }


def main() -> None:
    clean_items = [to_clean(q, o) for q, o, *_ in GRAMMAR]
    solved_items = [to_solved(q, o, c, e, cat, d) for q, o, c, e, cat, d in GRAMMAR]

    all_clean = json.loads(CLEAN.read_text(encoding="utf-8"))
    all_clean["anglais"] = clean_items
    CLEAN.write_text(json.dumps(all_clean, ensure_ascii=False, indent=2), encoding="utf-8")

    existing = json.loads(SOLVED.read_text(encoding="utf-8")) if SOLVED.exists() else {}
    prev = existing.get("anglais", [])
    seen = {(x.get("question") or "")[:70].lower() for x in prev}
    merged = list(prev)
    added = 0
    for item in solved_items:
        k = item["question"][:70].lower()
        if k in seen:
            continue
        seen.add(k)
        merged.append(item)
        added += 1
    existing["anglais"] = merged
    SOLVED.parent.mkdir(parents=True, exist_ok=True)
    SOLVED.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"clean anglais: {len(clean_items)}")
    print(f"solved anglais appended: {added} (total {len(merged)})")


if __name__ == "__main__":
    main()
