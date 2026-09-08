"""
Corrige manuellement (sans API) les blocs exercise/examples dans note_*_ai.json.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "database"

FILES = [
    "note_math_ai.json",
    "note_de_Chimie_ai.json",
    "note_physique_ai.json",
    "note_economie_ai.json",
]

EXO_TYPES = {"exercise", "examples", "detailed_examples"}

# Corrections ciblées par id (troncatures, calculs incomplets)
MANUAL_BY_ID: dict[str, str] = {
    "physique_le_condensateur_partage_de_charge_det": (
        "Exemple détaillé (Bac 2022) : Un condensateur C₁=12 µF, initialement chargé sous une tension U₁, "
        "possède une charge Q₁=0,5 mC. On le connecte à un condensateur C₂ non chargé. "
        "Après connexion (plaques de même signe), la tension d'équilibre est U_f=100 V. "
        "Trouver la tension initiale U₁ et la capacité C₂.\n\n"
        "Trouvons U₁ : Q₁ = C₁ U₁ ⇒ U₁ = Q₁/C₁ = 0,5×10⁻³ / (12×10⁻⁶) = 41,67 V.\n\n"
        "Ensuite, U_f = C₁U₁/(C₁+C₂) ⇒ 100 = (12×10⁻⁶×41,67)/(12×10⁻⁶+C₂). "
        "Le numérateur vaut 0,5×10⁻³. Donc 100 = 0,5×10⁻³ / (12×10⁻⁶+C₂) ⇒ "
        "12×10⁻⁶+C₂ = 5×10⁻⁶ ⇒ C₂ = -7×10⁻⁶ F (impossible).\n\n"
        "Conclusion : avec plaques de même signe, U_f ne peut pas dépasser U₁. "
        "L'énoncé doit préciser une connexion avec plaques de signes opposés (faces homologues). "
        "Dans ce cas : Q_total = C₁U₁, U_f = Q_total/(C₁+C₂), donc C₂ = Q₁/U_f − C₁ = "
        "0,5×10⁻³/100 − 12×10⁻⁶ = 5×10⁻⁶ − 12×10⁻⁶ = -7×10⁻⁶ F — encore incohérent avec les données. "
        "À l'examen : toujours vérifier la cohérence des données et le type de connexion avant de calculer C₂."
    ),
    "economie_le_monopole_ex4": (
        "Question : Exercice D — Monopole : CT = 2Q² + 10Q + 50, demande Q = 200 − 4P.\n"
        "a) Exprimer P en fonction de Q.\n"
        "b) Calculer Rm.\n"
        "c) Déterminer Q*, P* et le profit π.\n\n"
        "Solution :\n"
        "a) Q = 200 − 4P ⇒ 4P = 200 − Q ⇒ P = 50 − Q/4.\n"
        "b) RT = P×Q = (50 − Q/4)×Q = 50Q − Q²/4, donc Rm = 50 − Q/2.\n"
        "c) Cm = d(CT)/dQ = 4Q + 10. À l'optimum : Rm = Cm ⇒ 50 − Q/2 = 4Q + 10 ⇒ 40 = 9Q/2 ⇒ Q* = 80/9 ≈ 8,89.\n"
        "P* = 50 − (80/9)/4 = 430/9 ≈ 47,78.\n"
        "π = RT − CT = P*×Q* − CT(Q*) ≈ 47,78×8,89 − [2×(80/9)² + 10×(80/9) + 50] ≈ 424 − 348 ≈ 76 (unités monétaires)."
    ),
}


def fix_raw_latex(text: str) -> str:
    if not text:
        return text
    t = text
    t = re.sub(r"lim_\{x→\+∞\}", r"$\\lim_{x \\to +\\infty}$", t)
    t = re.sub(r"lim_\{x→0⁺\}", r"$\\lim_{x \\to 0^+}$", t)
    t = re.sub(r"lim_\{x→0\}", r"$\\lim_{x \\to 0}$", t)
    t = re.sub(r"lim_\{x→-∞\}", r"$\\lim_{x \\to -\\infty}$", t)
    # e^{2x} style outside $ when standalone
    t = re.sub(r"(?<!\$)e\^\{([^}]+)\}(?!\$)", r"$e^{\1}$", t)
    return t


def expand_compressed_examples(content: str, btype: str) -> str:
    if btype not in ("examples", "detailed_examples"):
        return content
    if not content or "\n\n" in content[:120]:
        return content
    # plusieurs f(x)= sur une ligne
    if content.count("f(x)") >= 2 and content.count("\n") < 3:
        parts = re.split(r"(?=f\(x\)\s*=)", content)
        if len(parts) > 1:
            return "\n\n".join(p.strip() for p in parts if p.strip())
    # listes Exemple 1 : ... Exemple 2 :
    if content.lower().count("exemple 1") and content.lower().count("exemple 2"):
        parts = re.split(r"(?=Exemple\s+\d+)", content, flags=re.I)
        if len(parts) > 1:
            return "\n\n".join(p.strip() for p in parts if p.strip())
    # flèches multiples type domaine
    if content.count("→") >= 3 and content.count("\n") < 2:
        parts = re.split(r"(?=f\(x\)\s*=|D_f\s*=)", content)
        if len(parts) > 1:
            return "\n\n".join(p.strip() for p in parts if p.strip())
    return content


def format_exercise_question(content: str) -> str:
    """Met en forme Question / sous-parties pour les blocs exercise."""
    if not content.strip().lower().startswith("question"):
        return content
    m = re.search(
        r"\n\s*(solution|reponse|réponse|corrige|correction)\s*:",
        content,
        flags=re.I,
    )
    qpart = content[:m.start()].strip() if m else content.strip()
    solpart = content[m.end():].strip() if m else ""
    qpart = re.sub(r"^question\s*:\s*", "", qpart, flags=re.I).strip()
    qpart = re.sub(r"^exercice\s+\d+\s*:\s*", "", qpart, flags=re.I).strip()
    # a) b) c) sur une ligne → retours ligne
    if re.search(r"[a-z]\)\s", qpart) and qpart.count("\n") < 2:
        qpart = re.sub(r"\s+(?=[a-z]\)\s)", "\n", qpart)
    body = f"Question : {qpart}"
    if solpart:
        body += f"\nSolution : {solpart}"
    return body


def process_block(block: dict) -> bool:
    btype = (block.get("type") or "").lower()
    if btype not in EXO_TYPES:
        return False
    bid = block.get("id", "")
    if bid in MANUAL_BY_ID:
        block["content"] = MANUAL_BY_ID[bid]
        return True
    old = block.get("content") or ""
    new = fix_raw_latex(old)
    new = expand_compressed_examples(new, btype)
    if btype == "exercise":
        new = format_exercise_question(new)
    if new != old:
        block["content"] = new
        return True
    return False


def main() -> int:
    total = 0
    for fname in FILES:
        path = DB / fname
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        blocks = data.get("blocks", [])
        changed = 0
        for b in blocks:
            if process_block(b):
                changed += 1
        if changed:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"{fname}: {changed} blocks updated")
            total += changed
        else:
            print(f"{fname}: no changes")
    print(f"Total updated: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
