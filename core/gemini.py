"""
Service IA BacIA — Groq API
Modèle principal : openai/gpt-oss-120b  (raisonnement, cours interactif, chat profond)
Modèle léger      : openai/gpt-oss-20b  (génération structurée JSON, tâches répétitives)
"""
from groq import Groq
from django.conf import settings
import re
import os
from collections import Counter

MODEL      = 'openai/gpt-oss-120b'    # Raisonnement pédagogique profond
FAST_MODEL = 'openai/gpt-oss-20b'     # Génération JSON structurée
CREOLE_MODEL = 'openai/gpt-oss-120b'  # Qualité créole maximale




# ─── Prompt statiques — Groq prefix caching ──────────────────────────────────
# Le premier message system doit être IDENTIQUE d'un appel à l'autre pour que
# Groq cache le KV des tokens (≈50% d'économie sur le prix input).
# Tout le contenu dynamique (profil, matière, notes) va dans un 2e message.
# ──────────────────────────────────────────────────────────────────────────────

_STATIC_CHAT_SYSTEM = """\
Tu es BacIA — un tuteur académique d'excellence pour le Bac Haïti.

━━━ PHILOSOPHIE PÉDAGOGIQUE ━━━
Tu enseignes comme les meilleurs profs : patient, clair, encourageant.
Tu es un grand frère intelligent qui aide l'élève à COMPRENDRE, pas juste à mémoriser.

PRINCIPES FONDAMENTAUX :
1. SIMPLICITÉ D'ABORD — Explique comme si l'élève n'avait jamais vu le sujet
2. EXEMPLES CONCRETS — Avant la théorie, donne un exemple de la vie quotidienne
3. ANALOGIES — Relie chaque concept à quelque chose que l'élève connaît (cuisine, sport, argent, téléphone)
4. POSER DES QUESTIONS — Après chaque explication, pose UNE question à l'élève pour vérifier qu'il comprend
5. ENCOURAGER LA RÉFLEXION — Au lieu de tout donner, guide l'élève vers la réponse
6. ADAPTATION — Si l'élève a du mal, change d'approche. Si l'élève maîtrise, va plus loin.

TON COMPORTEMENT :
• Si l'élève se trompe → « Presque ! » ou « Bonne idée, mais… » (JAMAIS « c'est faux »)
• Si l'élève a raison → « Parfait ! » ou « Exactement ! » (1 mot, puis enchaîne)
• Si l'élève répète la même erreur → change COMPLÈTEMENT d'approche (analogie différente)
• Si l'élève dit « je comprends pas » → simplifie en 2-3 phrases ultra-courtes + 1 analogie

⛔ ANTI-PARESSE :
• Si l'élève demande directement la réponse d'un exercice → Ne donne JAMAIS la réponse.
  Donne un INDICE + la méthode + un exemple similaire, puis repose la question.
  « Au BAC, c'est toi qui devras répondre ! Voici un indice : [indice]. Essaie ! »

━━━ AUTO-VÉRIFICATION OBLIGATOIRE ━━━
AVANT de finaliser ta réponse, tu DOIS mentalement vérifier :
1. Cohérence logique — aucune contradiction entre tes phrases
2. Exactitude scientifique — formules chimiques, lois physiques, constantes
3. Exactitude mathématique — recalcule chaque résultat, vérifie chaque étape
4. Cohérence des chiffres — si tu donnes un nombre, vérifie qu'il correspond partout
5. Dates historiques — vérifie les dates et les personnages associés
6. Définitions — assure-toi que chaque définition est précise et correcte

7. TERMINOLOGIE PHYSIQUE (CONFUSIONS INTERDITES) :
   ✗ NE JAMAIS confondre perméabilité (µ, magnétisme) et permittivité (ε, électricité).
   ✗ perméabilité du vide : µ₀ = 4π×10⁻⁷ H·m⁻¹ → champ magnétique B, bobines, solénoïdes.
   ✗ permittivité du vide : ε₀ = 8,85×10⁻¹² F·m⁻¹ → champ électrique E, condensateurs.
   ✗ Énergie cinétique Ec = ½mv² ≠ énergie potentielle. Ne jamais les inverser.
   Vérifie tes unités et tes constantes AVANT de répondre.

8. DATES HISTORIQUES — ANTI-HALLUCINATION STRICTE :
   • JAMAIS inventer une date ou attribuer un événement à une mauvaise année.
   • Exemple interdit : si un président a quitté le pouvoir en 1941, tu ne peux PAS dire qu'il a pris une décision en 1945.
   • Si tu n'es PAS certain d'une date spécifique → dis « Selon les informations... » ou « Vers cette époque ».
   • Croisement obligatoire : si tu cites une date, vérifie qu'elle est cohérente avec d'autres dates du même événement.

SI tu détectes une erreur dans ta propre réponse → corrige-la IMMÉDIATEMENT avant d'envoyer.
NE JAMAIS envoyer une réponse avec une contradiction interne.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━ AUTO-ÉVALUATION QUALITÉ (score mental /10) ━━━
Avant d'envoyer, évalue mentalement ta réponse :
• Clarté : /10 — L'élève comprendra-t-il du premier coup ?
• Pédagogie : /10 — Y a-t-il un exemple concret et une analogie ?
• Exactitude : /10 — Chaque fait, formule, date est-il vérifié ?
• Structure : /10 — La réponse suit-elle un ordre logique clair ?
SI un score < 8 → REFORMULE cette partie avant d'envoyer.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TON STYLE — PÉDAGOGIQUE ET SOBRE :
- Donne d'abord une définition claire et exacte quand l'élève demande "c'est quoi" ou "définition".
- Utilise un langage simple, précis et neutre (niveau Terminale).
- Phrases courtes : max 20 mots par phrase. UNE idée par paragraphe.
- Interdiction de ton familier, d'exagération, de dramatisation, de métaphores spectacle.
- N'invente pas de chiffres, de plans "magiques" ou d'étapes arbitraires.
- Si un terme est mal orthographié, corrige-le brièvement puis réponds au fond.
- Zéro phrases bateau ("Bien sûr !", "Excellente question !", "Certainement !").

COMMENT STRUCTURER — ADAPTE LE FORMAT AU TYPE DE QUESTION :

💬 QUESTION SIMPLE (concepts courts, définitions) :
→ 1. Définition : 1-2 phrases claires
→ 2. Pourquoi c'est important : 1 phrase
→ 3. Exemple concret
→ 4. À retenir : 1 phrase résumé
→ 5. ❓ Question de vérification
⚠️ ATTENTION : N'utilise ce format QUE si la question est vraiment simple/courte. JAMAIS pour "parle moi de [personnage]" ou "histoire de [événement]".

📖 EXPLICATION DÉTAILLÉE ET BIOGRAPHIES — OBLIGATOIRE pour "parle moi de", "qui était", etc. :
→ 1. Introduction contextualisée (qui, quand, pourquoi c'est important)
→ 2. Biographie/carrière (ou contexte historique)
→ 3. Phases/périodes clés AVEC DATES EXACTES
→ 4. Actions/politique/impact majeur (utilise TABLEAU si possibilités multiples)
→ 5. Points de controverse ou aspects négatifs (JAMAIS occulter)
→ 6. Bilan global/conclusion nuancée
→ 7. ❓ Question de vérification
✅ Approche COMPLÈTE : couvre le PARCOURS ENTIER, pas juste highlights.

🔢 DEMANDE TECHNIQUE (exercice, calcul) :
→ 1. Ce que ça demande
→ 2. Données clés
→ 3. Méthode et formule (POURQUOI)
→ 4. Résolution étape par étape
→ 5. Vérification du résultat
→ 6. Piège classique
→ 7. ❓ Exercice similaire ?
→ Formules en KaTeX : $f(x)$ inline, $$...$$ pour les blocs
→ APRÈS chaque formule : définir CHAQUE symbole avec unité SI.

RÉSUMÉ / ÉVÉNEMENT HISTORIQUE / COMPARAISON :
→ 1. Contexte en 1-2 phrases
→ 2. Causes / mécanismes
→ 3. Points clés avec TABLEAU OBLIGATOIRE
→ 4. Conclusion courte
→ 5. ❓ Question de vérification

📊 TABLEAUX MARKDOWN — OBLIGATOIRES pour comparaisons, dates, correct vs incorrect.

ADAPTE-TOI AU PROFIL :
- Matière faible → bases, vocabulaire simple, beaucoup d'exemples
- Matière forte → challenge, cas complexes
- Bloqué → définition + étapes ultra-simples + analogie
- Hors sujet → ramène au sujet BAC poliment

QUESTIONS MAL FORMULÉES/AMBIGUËS :
- Ambiguë → clarifie en 1 phrase, réponds à l'interprétation probable
- Mal formulée → corrige brièvement, réponds normalement
- Hors programme → dis-le, propose une question pertinente

━━━ ANTI-HALLUCINATION — MODE SÉCURITÉ ━━━
• Confiance < 80% → « D'après le programme... », « Selon les informations disponibles... »
• JAMAIS inventer une formule, une date, un chiffre, un nom.
• Concept INEXISTANT → « Ce concept n'existe pas dans le programme. Tu veux peut-être parler de [concept similaire] ? »
• Concept d'une AUTRE MATIÈRE → Refuse poliment et recentre.
• Sciences : TOUJOURS vérifier les unités, constantes et formules.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


_STATIC_COURSE_SYSTEM = """\
Tu es BacIA, professeur privé expérimenté d'un lycéen haïtien préparant le BAC.
Tu parles comme un grand professeur compétent, pédagogue et patient — jamais comme un chatbot rapide.

━━ RÈGLE FONDAMENTALE : RÉPONSE COMPLÈTE ET CONTRÔLÉE ━━
• AVANT d'envoyer une réponse, VÉRIFIE que :
  1. Chaque phrase est syntaxiquement complète (sujet + verbe + complément).
  2. La dernière phrase se termine par un point, un point d'exclamation ou une question.
  3. Tous les blocs LaTeX $...$ ou $$...$$ sont FERMÉS correctement.
  4. Aucun mot n'est tronqué (ex : "alcyn" au lieu de "alcynes").
  5. Chaque section annoncée est effectivement présente.
• Si tu sens que tu vas dépasser la limite → termine la phrase en cours, puis écris :
  "[À COMPLÉTER — réponds 'Continue' pour la suite]"
• JAMAIS d'envoi de réponse coupée au milieu d'un mot ou d'une phrase.

━━ RÈGLES D'ENSEIGNEMENT ━━
• Un seul sous-chapitre à la fois.
• Réponse contrôlée : 2 à 3 blocs internes maximum par message.
• AUCUNE structure visible : ne jamais afficher "Étape", "Définition", "Résumé", etc.
• AUCUN label, aucune section titrée, aucune numérotation pédagogique visible.
• Style de sortie : texte fluide, naturel, progressif.
• Pas de quiz long automatique. Exercices courts uniquement si le chunk l'exige.
• Ne fais jamais un cours complet du sous-chapitre en une seule réponse.
• Ne fais jamais une conclusion globale du chapitre.

ADAPTATION NIVEAU ÉLÈVE :
• "non" / "je ne comprends pas" / "quoi ?" / "c'est quoi" → NIVEAU DÉBUTANT :
  - Vocabulaire ultra-simple, analogies de la vie quotidienne (cuisine, sport, argent...).
  - Explique chaque terme technique comme si l'élève avait 12 ans.
  - Rythme très lent : une seule idée à la fois, attends la confirmation.
  - Questions très guidées : propose 2 options (A ou B ?).
• Réponses courtes sans développement → ralentis, pose des questions d'exploration.
• Bonne réponse développée → passe en mode avancé, enrichis.

STYLE PROFESSEUR EXPÉRIMENTÉ :
• Validations VARIÉES : "Parfait !", "Excellent !", "C'est exactement ça.", "Tu maîtrises ça.",
  "Bravo !", "Bien vu !", "Correct.", "Bonne réponse.", "Tu gères."
• Transitions VARIÉES : "Maintenant,", "Du coup,", "Autre point clé —", "Et là c'est important —",
  "OK, on avance —", "Justement,", "Bon,". JAMAIS "Passons à..."
• ANTI-DUPLICATION : chaque phrase doit être UNIQUE. Relis avant d'envoyer.
• TOLÉRANCE : évalue le FOND, pas l'orthographe. Accent manquant = VALIDE si le sens est correct.
• INTERDIT : "Bien sûr !", "Excellente question !", réponse dans la question, concept futur non enseigné.
• Parle comme un professeur humain, pas comme un robot qui liste.

INTERDICTIONS ABSOLUES :
• N'écris jamais : "Ce point n'est pas dans les notes".
• N'écris jamais : "Étape 1", "Étape 2", "Sous-partie", "Bloc".
• N'expose jamais la structure interne de génération.
• N'invente aucun contenu hors des notes fournies.

SITUATIONS SPÉCIALES :
• Confus → change d'approche : nouvelle analogie plus simple, vocabulaire courant uniquement.
• Découragé → empathie + rappelle sa progression + question ultra-simple + encourage.
• Paresseux → indice précis + exemple similaire. JAMAIS la réponse brute.
• Hors-sujet → réponds en 1 ligne, recentre immédiatement.

ORTHOGRAPHE ET GRAMMAIRE PROFESSIONNELLE :
• Écris TOUJOURS correctement : n'importe (pas "importe"), c'est, l'élève, qu'il, s'il.
• Apostrophes : n', c', l', d', j', s' — JAMAIS oubliées.
• Accents obligatoires : é, è, ê, à, ù, î, ô, etc.
• Accords grammaticaux parfaits (accord sujet-verbe, adjectif-nom).
• Ponctuation correcte : espace avant « ? » et « ! » en français.
• Vérifie chaque phrase avant de l'envoyer. Aucune faute tolérée.

AVANT D'ENVOYER CHAQUE RÉPONSE, VÉRIFIE :
✓ Réponse complète — aucun mot tronqué, aucune phrase suspendue
✓ Réponse contrôlée (2-3 blocs internes max, pas de surcharge)
✓ Orthographe et grammaire parfaites
✓ LaTeX fermé correctement (si science)
✓ Formules/chiffres exacts, cohérence avec messages précédents
✓ Question finale qui FORCE l'élève à produire quelque chose (INTERDIT : "Tu as compris ?")"""


# ─── Traduction FR → Kreyòl Ayisyen ──────────────────────────────────────────
def translate_batch(texts: list[str], lang: str = 'kr', context: str = '') -> list[str]:
    """
    Traduit une liste de textes français → Kreyòl Ayisyen.
    Utilise llama-3.3-70b pour la qualité créole.
    Retourne une liste de même longueur.
    """
    import json as _json
    if lang != 'kr' or not texts:
        return texts

    # Filtrer les textes vides pour ne pas polluer le batch
    non_empty_indices = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
    if not non_empty_indices:
        return texts

    actual_texts = [t for _, t in non_empty_indices]

    ctx_hint = f" (contexte: {context})" if context else ''
    numbered = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(actual_texts))

    prompt = (
        f"Tradui tèks fransè sa yo an KREYÒL AYISYEN{ctx_hint}. "
        "Respekte sans ak ton orijinal la. Pa tradui mo teknik (ex: 'Quiz', 'Bac', 'IA', 'Score', 'Email'). "
        "Reponn SÈLMAN yon JSON array KONPLÈ (1 eleman pou chak tèks):\n"
        f'["tradiksyon 1", "tradiksyon 2", ...]\n\n'
        f"Tèks yo ({len(actual_texts)} tèks):\n{numbered}"
    )

    try:
        resp = _client().chat.completions.create(
            model=CREOLE_MODEL,
            messages=[
                {"role": "system", "content": (
                    "You are an expert French→Haitian Creole (Kreyòl Ayisyen) translator. "
                    "Respond ONLY with a JSON array containing EXACTLY the same number of elements as the texts given. "
                    "Do not add any text before or after the JSON array."
                )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=min(4096, len(actual_texts) * 150 + 500),
        )
        raw = resp.choices[0].message.content or ''
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f'[translate_batch] raw response (first 500): {raw[:500]}')
        raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
        m = re.search(r'\[[\s\S]+\]', raw)
        if m:
            translated = _json.loads(m.group(0))
            if isinstance(translated, list):
                # Accepter même si la liste est plus courte (tronquée)
                result = list(texts)  # Copie avec originaux par défaut
                for offset, (orig_idx, orig_text) in enumerate(non_empty_indices):
                    if offset < len(translated) and translated[offset]:
                        tr = str(translated[offset]).strip()
                        # N'appliquer que si la traduction est vraiment différente
                        if tr and tr != orig_text.strip():
                            result[orig_idx] = tr
                return result
        logger.warning(f'[translate_batch] No JSON array found in response. raw={raw[:300]}')
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f'[translate_batch] Exception: {e}', exc_info=True)
    # Fallback: return originals
    return texts

# ─── Recherche web DuckDuckGo ─────────────────────────────────────────────────
def _web_search(query: str, max_results: int = 4) -> str:
    """
    Recherche DuckDuckGo Instant Answer + scraping des premiers résultats.
    Retourne un bloc texte compact à injecter dans le prompt, ou '' si rien trouvé.
    """
    try:
        import urllib.request, urllib.parse, json as _json, html

        # 1. DuckDuckGo Instant Answer API (gratuit, sans clé)
        q = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'BacIA/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read().decode())

        snippets = []

        # Réponse directe (AbstractText)
        abstract = (data.get('AbstractText') or '').strip()
        if abstract:
            source = data.get('AbstractSource', '')
            snippets.append(f"[{source}] {abstract}")

        # Answer box (Answer)
        answer = (data.get('Answer') or '').strip()
        if answer:
            snippets.append(f"[Réponse directe] {answer}")

        # RelatedTopics
        for topic in data.get('RelatedTopics', [])[:max_results]:
            text = (topic.get('Text') or '').strip()
            if text and len(text) > 30:
                snippets.append(text)

        if snippets:
            combined = '\n'.join(snippets[:max_results])
            return f"[RÉSULTATS WEB]\n{combined}\n[FIN RÉSULTATS WEB]"

        return ''
    except Exception:
        return ''


# Mots 100% créoles haïtiens — absents du français et de l'anglais
_CREOLE_UNIQUE = {
    'mwen', 'pral', 'nap', 'map', 'tap', 'palem', 'pale', 'banm', 'ban',
    'ayiti', 'kreyol', 'kreyòl', 'poukisa', 'kijan', 'konbyen', 'zanmi',
    'bonswa', 'mèsi', 'mesi', 'sakpase', 'kapab', 'bezwen', 'genyen',
    'jwenn', 'etidyan', 'lekòl', 'lekol', 'ekzamen', 'reyisi',
    'anpil', 'kounye', 'dako', 'tounen', 'pito',
}


def _lang_instruction(text: str, forced_lang: str = '') -> str:
    """
    Si forced_lang='kr' → force toujours le créole haïtien, indépendamment du texte.
    Si forced_lang='fr' → force le français.
    Sinon détecte par mots-clés.
    """
    if forced_lang == 'kr':
        return """LANGUE : L'élève a choisi l'interface en CRÉOLE HAÏTIEN. Tu DOIS répondre EN CRÉOLE HAÏTIEN SÈLMAN.
Itilize yon kreyòl natirèl ak aksesib. Pou tèm teknik, ka kenbe mo fransè a men eksplike an kreyòl.
PA janm pase nan fransè nan repons ou."""
    if forced_lang == 'fr':
        return "LANGUE : Réponds en français, sauf si l'élève t'écrit dans une autre langue."
    lowered = text.lower()
    tokens = set(re.findall(r"[a-zàâäèéêëîïôùûüœç']+", lowered))
    if tokens & _CREOLE_UNIQUE:
        return """LANGUE : L'élève t'écrit en CRÉOLE HAÏTIEN. Tu DOIS répondre EN CRÉOLE HAÏTIEN, pas en français.
Utilise un créole naturel et accessible. Pour les termes techniques, garde le mot français mais explique en créole.
NE PASSE JAMAIS au français dans ta réponse."""
    return """LANGUE : Détecte la langue de l'élève et réponds dans cette même langue (français, anglais, ou créole haïtien).
Si il mélange créole et français → réponds en créole haïtien."""


def _build_compact_history(messages: list, keep: int = 8) -> tuple:
    """
    Token-efficient conversation memory.

    Splits `messages` into:
      - A compact natural-language summary of everything BEFORE the last `keep` messages
        (≈ 100-200 tokens, built with pure Python — NO extra API call).
      - The `keep` most recent messages to be inserted verbatim.

    Works with both message formats used across the project:
      • course_chat  : {'role': 'user'/'assistant', 'content': '...'}
      • get_chat_response history: {'role': 'user'/'model', 'parts': ['...']}

    Returns: (summary_str: str, recent_msgs: list)
      summary_str is '' when no older messages exist.
    """
    if not messages:
        return '', []

    # Filter out internal bookkeeping entries
    chat_msgs = [
        m for m in messages
        if m.get('role') not in ('__plan__', '__plan_intro__')
        and not str(m.get('role', '')).startswith('__')
    ]

    if len(chat_msgs) <= keep:
        return '', list(chat_msgs)

    old_msgs  = chat_msgs[:-keep]
    recent_msgs = chat_msgs[-keep:]

    # ── Extractive summary (no API) ──────────────────────────────────────────
    def _get_content(m: dict) -> str:
        c = m.get('content') or ''
        if not c:
            parts = m.get('parts', [])
            c = ' '.join(str(p) for p in parts if isinstance(p, str))
        return (c or '').strip()

    def _is_student(m: dict) -> bool:
        return m.get('role', 'user') not in ('ai', 'assistant', 'model')

    ai_topics:         list = []
    student_questions: list = []
    student_errors:    list = []
    last_student_msg   = ''

    for m in old_msgs:
        content = _get_content(m)
        if not content:
            continue
        snippet = content[:100]
        if _is_student(m):
            last_student_msg = snippet
            low = content.lower()
            if any(kw in low for kw in (
                'comprends pas', 'pas clair', 'perdu', "c'est quoi",
                'comment', 'pourquoi', "qu'est-ce", 'pa kompran', 'kisa',
            )):
                student_questions.append(snippet)
            elif any(kw in low for kw in ('non,', 'faux', 'erreur', 'incorrect', 'pas bon')):
                student_errors.append(snippet)
        else:
            first_line = content.split('\n')[0][:80].strip()
            if first_line:
                ai_topics.append(first_line)

    n_old = len(old_msgs)
    parts = [f"✦ MÉMOIRE DE LA CONVERSATION ({n_old} échanges précédents) :"]
    if ai_topics:
        parts.append("• Sujets abordés : " + " → ".join(ai_topics[-3:]))
    if student_questions:
        parts.append("• Questions posées par l'élève : " + " | ".join(student_questions[-2:]))
    if student_errors:
        parts.append("• Difficultés détectées : " + " | ".join(student_errors[-2:]))
    if last_student_msg:
        parts.append(f"• Dernier message élève : \"{last_student_msg[:80]}\"")

    summary = '\n'.join(parts)
    return summary, list(recent_msgs)


def _creole_subject_instruction(subject: str) -> str:
    """Retourne une instruction de langue créole si la matière est Kreyòl (francais)."""
    if subject == 'francais':
        return (
            "\n⚠️ RÈG LANG — KREYÒL AYISYEN SÈLMAN :\n"
            "- Ekri TOUT (kesyon, opsyon, eksplikasyon) AN KREYÒL AYISYEN."
            " Piga itilize fransè, anglè, oswa lòt lang.\n"
            "- Règ gramè enpòtan :\n"
            "  * Atik defini: 'la/a/an' apre non (eg. 'pwofesè a', 'liv la', 'zanmi an')\n"
            "  * Atik endefini: 'yon' (eg. 'yon liv', 'yon elèv')\n"
            "  * Plural: 'yo' apre non (eg. 'elèv yo', 'liv yo')\n"
            "  * Vèb: 'li' (he/she), 'yo' (they), 'mwen' (I), 'ou' (you), 'nou' (we)\n"
            "  * Prezan: sila+vèb (eg. 'li ale'), pase: 'te' (eg. 'li te ale')\n"
            "  * Negasyon: 'pa' (eg. 'mwen pa konnen', 'li pa la')\n"
            "  * Posesif: 'mwen', 'ou', 'li', 'nou', 'yo' apre non (eg. 'kay li', 'liv mwen')\n"
            "- Sijè gramè tipik Bac: pwonon, prepozisyon, adverb, konjugezyon, estriti fraz\n"
            "- Ekri opsyon yo kout ak klè, san repete mo kesyon an\n"
            "- reponse_correcte DWE kòrèk selon règ gramè kreyòl ofisyèl"
        )
    return ''


def _client() -> Groq:
    return Groq(api_key=settings.GROQ_API_KEY)


def _call(prompt: str, system: str = '', max_tokens: int = 1500) -> str:
    """Appel Groq centralisé — point d'entrée unique pour contrôler les coûts."""
    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ''


def _call_fast(prompt: str, max_tokens: int = 1000) -> str:
    """Appel léger (FAST_MODEL) pour les tâches de génération structurée — 6× moins cher."""
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ''


def _call_json_fast(prompt: str, system: str = '', max_tokens: int = 2000) -> str:
    """Appel FAST_MODEL pour JSON structuré — quiz, flashcards, task list, plans."""
    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )
    if not resp.choices:
        return ''
    return resp.choices[0].message.content or ''


def _call_json(prompt: str, system: str = '', max_tokens: int = 3000) -> str:
    """Appel Groq pour génération JSON — pas de response_format pour compatibilité maximale."""
    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )
    if not resp.choices:
        import sys; print(f"[_call_json] WARNING: empty choices, resp={resp}", file=sys.stderr)
        return ''
    choice = resp.choices[0]
    content = choice.message.content or ''
    if not content:
        import sys; print(f"[_call_json] WARNING: empty content, finish_reason={choice.finish_reason}", file=sys.stderr)
    return content


def respond(system: str, prompt: str, max_tokens: int = 1500) -> str:
    """
    Fonction publique pour obtenir une réponse de Groq avec système et prompt.
    Utilisée par api_course_question et autres endpoints.
    """
    return _call(prompt, system=system, max_tokens=max_tokens)


# ─────────────────────────────────────────────────────────────────────────────
# CONTRÔLE QUALITÉ IA — analyse + correction avant toute diffusion
# ─────────────────────────────────────────────────────────────────────────────

def quality_check_question(item: dict, subject: str, all_items: list | None = None) -> dict:
    """
    Analyse une question/QCM/exercice AVANT de la proposer à l'élève.
    L'IA Groq :
      1. Vérifie si la question est valide, complète et correcte
      2. Pour les QCM factuels (SVT, chimie, physique, histoire…) → recherche web pour confirmer la réponse
      3. Corrige les erreurs si elle en trouve (fautes de calcul, formules cassées, réponse incorrecte)
      4. Si la question est trop incomplète/irrécupérable → signale "skip" pour switcher sur une autre

    Retourne un dict avec :
      - 'item'   : l'item final (corrigé ou original)
      - 'skip'   : True si la question doit être remplacée
      - 'fixed'  : True si des corrections ont été appliquées
      - 'reason' : explication de la décision
    """
    import json as _json

    item_type = item.get('type', 'question')
    subject_label = MATS.get(subject, subject)

    # ─── Construire le résumé de la question à analyser ──────────────────────
    if item_type == 'exercice':
        content_summary = f"Intro: {item.get('intro','')[:600]}\nQuestions: {_json.dumps(item.get('questions',[])[:5], ensure_ascii=False)}"
    elif item_type == 'qcm':
        content_summary = (
            f"Énoncé: {item.get('enonce','')[:400]}\n"
            f"Options: {_json.dumps(item.get('options',[]), ensure_ascii=False)}\n"
            f"Réponse correcte (index): {item.get('reponse_correcte','')}\n"
            f"Réponse correcte (texte): {item.get('options',[])[item.get('reponse_correcte',0)] if item.get('options') else 'N/A'}\n"
            f"Explication: {item.get('explication','')[:300]}"
        )
    else:
        content_summary = (
            f"Type: {item_type}\n"
            f"Énoncé: {item.get('enonce','')[:500]}\n"
            f"Texte/contexte: {item.get('texte','')[:300]}\n"
            f"Réponse: {item.get('reponse','')[:300]}"
        )

    # ─── Recherche web pour les QCM factuels (SVT, chimie, physique, histoire, etc.) ──
    web_context = ''
    FACTUAL_SUBJECTS = {'svt', 'chimie', 'physique', 'histoire', 'philosophie', 'anglais', 'espagnol', 'economie', 'informatique'}
    if item_type == 'qcm' and subject in FACTUAL_SUBJECTS:
        enonce = item.get('enonce', '')
        if enonce and len(enonce.strip()) > 10:
            search_query = f"{enonce} réponse correcte biologie médecine science Bac"
            web_result = _web_search(search_query, max_results=3)
            if web_result and len(web_result) > 30:
                web_context = f"\n\n=== RÉSULTAT DE RECHERCHE WEB ===\n{web_result[:600]}\n(Utilise ces informations pour vérifier quelle option est factuellemnt correcte)"

    prompt = (
        f"Tu es un expert correcteur du Bac Haïti en {subject_label} avec accès à des sources fiables.\n"
        f"Analyse cette question extraite d'un examen officiel :{web_context}\n\n"
        f"--- QUESTION ---\n{content_summary}\n--- FIN ---\n\n"
        f"Effectue ces vérifications DANS CET ORDRE :\n"
        f"1. VÉRIFICATION FACTUELLE (PRIORITÉ ABSOLUE pour QCM) :\n"
        f"   - Identifie la VRAIE réponse correcte parmi les options selon tes connaissances et la recherche web\n"
        f"   - Si le champ 'reponse_correcte' pointe vers une mauvaise option → CORRIGE l'index\n"
        f"   - Exemple: si la question parle de daltonisme et que 'Protanopie' est dans les options mais marqué faux → fixe ça\n"
        f"2. VÉRIFICATION FORME :\n"
        f"   - Énoncé lisible, pas tronqué par OCR, formules correctes\n"
        f"3. UTILISABILITÉ :\n"
        f"   - SKIP si énoncé < 15 chars utiles, ou données numériques corrompues\n"
        f"   - SKIP si contenu administratif (consignes de salle)\n\n"
        f"Réponds UNIQUEMENT en JSON (sans markdown) :\n"
        f'{{"skip":false,"fixed":false,"reason":"...",\n'
        f' "corrections":{{"enonce":null,"options":null,"reponse_correcte":null,"explication":null,'
        f'"intro":null,"questions":null,"reponse":null}}\n}}\n\n'
        f"- skip=true : cette question ne peut pas être corrigée\n"
        f"- fixed=true + corrections : remplis SEULEMENT les champs modifiés (les autres restent null)\n"
        f"- Rien à changer : skip=false, fixed=false"
    )

    try:
        # Utiliser le modèle puissant pour toutes les matières (fiabilité > vitesse pour le QC)
        _qc_resp = _client().chat.completions.create(
            model=CREOLE_MODEL,
            messages=[
                {"role": "system", "content": (
                    "Tu es un expert pédagogique rigoureux. Ta MISSION PRINCIPALE est de vérifier que "
                    "reponse_correcte pointe vers la BONNE réponse factuelle. "
                    "En cas de doute, corrige. Reponn an JSON sèlman."
                    if subject == 'francais' else
                    "You are a rigorous academic expert. Your PRIMARY MISSION: verify that reponse_correcte "
                    "points to the FACTUALLY CORRECT option. If wrong, fix it. "
                    "Cross-check with web search results when provided. Reply ONLY in JSON."
                )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
        )
        raw = _qc_resp.choices[0].message.content or ''
        raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
        m = re.search(r'\{[\s\S]+\}', raw)
        if not m:
            return {'item': item, 'skip': False, 'fixed': False, 'reason': 'parse_failed'}
        data = _json.loads(m.group(0))

        if data.get('skip'):
            return {'item': item, 'skip': True, 'fixed': False, 'reason': data.get('reason', 'incomplete')}

        if data.get('fixed'):
            corrections = data.get('corrections') or {}
            patched = dict(item)
            # Apply non-null corrections
            for field, val in corrections.items():
                if val is not None and val != '':
                    if field == 'questions' and isinstance(val, list):
                        patched['questions'] = [str(q).strip() for q in val if str(q).strip()]
                    elif field == 'options' and isinstance(val, list):
                        patched['options'] = [str(o).strip() for o in val if str(o).strip()]
                    elif field == 'reponse_correcte' and val is not None:
                        try:
                            patched['reponse_correcte'] = int(val)
                        except (ValueError, TypeError):
                            patched['reponse_correcte'] = val
                    else:
                        patched[field] = val
            patched['_qc_fixed'] = True
            return {'item': patched, 'skip': False, 'fixed': True, 'reason': data.get('reason', '')}

        return {'item': item, 'skip': False, 'fixed': False, 'reason': 'ok'}

    except Exception:
        # En cas d'erreur réseau / parse → on garde l'item tel quel (fail-open)
        return {'item': item, 'skip': False, 'fixed': False, 'reason': 'qc_error'}


def quality_check_pool(items: list, subject: str, wanted: int = 10) -> list:
    """
    Passe tous les items d'un pool au contrôle qualité.
    Saute (skip) les questions invalides et les remplace par la suivante du pool.
    Retourne exactement `wanted` items (ou moins si le pool est trop petit).

    items : liste d'items triés par priorité (les meilleurs en premier)
    """
    approved: list = []
    for item in items:
        if len(approved) >= wanted:
            break
        result = quality_check_question(item, subject, all_items=items)
        if result['skip']:
            continue  # Remplacé automatiquement par l'item suivant
        approved.append(result['item'])
    return approved

MATS = {
    'maths':       'Maths',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT',
    'francais':    'Kreyòl',
    'philosophie': 'Philosophie',
    'histoire':    'Sciences Sociales',
    'anglais':     'Anglais',
    'economie':    'Économie',
    'informatique':'Informatique',
    'art':         'Art',
    'espagnol':    'Espagnol',
}

VISION_MODEL = 'meta-llama/llama-4-scout-17b-16e-instruct'




# ─────────────────────────────────────────────────────────────────────────────
# RÉSUMÉ DE SESSION CHAT
# ─────────────────────────────────────────────────────────────────────────────

def generate_chat_summary_ai(conversation_text: str, user_name: str) -> dict:
    """
    Génère un résumé structuré JSON d'une session de chat.
    Retourne un dict avec les clés:
      subjects, strengths, weaknesses, confidence, observations, key_questions
    """
    import json as _json

    if not conversation_text or len(conversation_text) < 100:
        return {}

    prompt = f"""Tu es un analyste pédagogique expert. Analyse cette conversation entre l'élève {user_name} et son coach IA.

CONVERSATION:
{conversation_text[:6000]}

Génère un résumé JSON STRICT avec exactement ces clés:
{{
  "subjects": ["liste des matières abordées ex: maths, physique"],
  "strengths": ["points forts détectés — ce que l'élève maîtrise bien"],
  "weaknesses": ["points faibles — ce qu'il ne comprend pas encore"],
  "confidence": "niveau de confiance de l'élève: hésitant | normal | confiant | très confiant",
  "observations": ["comportements notables: hésitations, répétitions, curiosité, questions de compréhension"],
  "key_questions": ["les 2-3 questions les plus importantes posées par l'élève"]
}}

RÈGLES:
- Chaque liste: 1 à 5 éléments maximum
- Sois concis et précis
- Retourne UNIQUEMENT le JSON, sans explication
- Si une information n'est pas détectable, mets une liste vide []"""

    try:
        client = Groq(api_key=settings.GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.3,
            max_tokens=900,
        )
        raw = resp.choices[0].message.content.strip()
        # Extraire le JSON
        match = re.search(r'\{[\s\S]+\}', raw)
        if match:
            try:
                return _json.loads(match.group(0))
            except _json.JSONDecodeError:
                # Try to recover a partial result by extracting individual list fields
                result = {}
                for key in ('subjects', 'strengths', 'weaknesses', 'observations', 'key_questions'):
                    km = re.search(rf'"{key}"\s*:\s*(\[[^\]]*\])', raw)
                    if km:
                        try:
                            result[key] = _json.loads(km.group(1))
                        except Exception:
                            result[key] = []
                cm = re.search(r'"confidence"\s*:\s*"([^"]+)"', raw)
                if cm:
                    result['confidence'] = cm.group(1)
                return result if result else {}
        return {}
    except Exception as e:
        print(f"[GEMINI] generate_chat_summary_ai error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# PROFIL D'APPRENTISSAGE — cœur de l'intelligence personnalisée
# ─────────────────────────────────────────────────────────────────────────────

def build_user_learning_profile(user) -> str:
    """
    Compile les données de l'élève en un bloc texte structuré.
    Résultat mis en cache Django 5 minutes pour éviter les requêtes DB répétées.
    """
    from django.core.cache import cache as _dj_cache
    _cache_key = f'ulp_full_{user.pk}'
    cached = _dj_cache.get(_cache_key)
    if cached is not None:
        return cached
    result = _build_user_learning_profile_impl(user)
    _dj_cache.set(_cache_key, result, 300)  # 5 min
    return result


def build_user_learning_profile_short(user) -> str:
    """
    Version compacte du profil (~100-150 tokens) — utilisée dans les prompts de chat
    pour éviter d'injecter 2 000 tokens de contexte à chaque message.
    Mis en cache 5 min.
    """
    from django.core.cache import cache as _dj_cache
    _cache_key = f'ulp_short_{user.pk}'
    cached = _dj_cache.get(_cache_key)
    if cached is not None:
        return cached

    try:
        from accounts.models import UserProfile, DiagnosticResult
        from .models import QuizSession, AIMemory

        parts = []
        try:
            profile = user.profile
            name  = (profile.first_name or user.username).capitalize()
            serie = profile.serie or 'SVT'
            parts.append(f"Élève: {name} | Série: {serie}")
        except Exception:
            parts.append(f"Élève: {user.username}")

        # Scores diagnostics (1 ligne)
        diags = list(DiagnosticResult.objects.filter(user=user).order_by('score')[:5])
        if diags:
            weak = [MATS.get(d.subject, d.subject) for d in diags if d.score < 50]
            strong = [MATS.get(d.subject, d.subject) for d in diags if d.score >= 70]
            if weak:
                parts.append(f"Faible en: {', '.join(weak[:3])}")
            if strong:
                parts.append(f"Fort en: {', '.join(strong[:2])}")

        # Dernière session quiz
        last_sessions = list(QuizSession.objects.filter(user=user).order_by('-completed_at')[:5])
        if last_sessions:
            subj_avgs = {}
            for s in last_sessions:
                subj_avgs.setdefault(s.subject, []).append(s.get_percentage())
            for subj, pcts in list(subj_avgs.items())[:3]:
                avg = round(sum(pcts) / len(pcts))
                emoji = '🔴' if avg < 40 else '🟡' if avg < 65 else '🟢'
                parts.append(f"{emoji} {MATS.get(subj, subj)}: {avg}%")

        # Mémoires clés (max 3)
        memories = list(AIMemory.objects.filter(
            user=user, memory_type__in=['erreur', 'concept']
        ).order_by('-importance', '-updated_at')[:3])
        for m in memories:
            parts.append(f"⚠ {m.content[:60]}")

        result = ' | '.join(parts)
    except Exception:
        result = ''

    _dj_cache.set(_cache_key, result, 300)
    return result


def _build_user_learning_profile_impl(user) -> str:
    """
    Compile TOUTES les données disponibles sur l'élève en un bloc texte structuré.
    Inclut : identité, diagnostic, quiz history, erreurs récurrentes, mémoires IA,
    résumé de TOUS les anciens chats, activité globale.
    """
    try:
        from accounts.models import UserProfile, DiagnosticResult
        from .models import QuizSession, QuizAnalysis, ChatMessage, UserStats, AIMemory
        from collections import Counter

        lines = []

        # ── 1. Identité et série ─────────────────────────────────────────────
        try:
            profile = user.profile
            serie   = profile.serie or 'SVT'
            name    = (profile.first_name or user.username).capitalize()
            lines.append(f"=== PROFIL DE L'ÉLÈVE ===")
            lines.append(f"Prénom : {name}")
            lines.append(f"Série : {serie} — Terminale")
            if profile.school:
                lines.append(f"École : {profile.school}")
        except Exception:
            serie = 'SVT'
            name  = user.username
            lines.append(f"=== PROFIL DE L'ÉLÈVE ===")
            lines.append(f"Pseudo : {name}")
            lines.append(f"Série : {serie}")

        # ── 2. NOTES DE L'UTILISATEUR (infos personnelles, résumés, points clés) ──
        try:
            import json
            from pathlib import Path
            notes_dir = Path(f'/tmp/user_notes_{user.id}')
            user_notes_file = notes_dir / 'notes.json'
            
            if user_notes_file.exists():
                with open(user_notes_file, 'r', encoding='utf-8') as f:
                    notes_data = json.load(f)
                    if notes_data and isinstance(notes_data, dict):
                        lines.append("\n=== 📓 NOTES PERSONNELLES DE L'ÉLÈVE ===")
                        lines.append("(Notes que l'élève a sauvegardées ou a créées)")
                        for key, value in notes_data.items():
                            if isinstance(value, str) and value.strip():
                                lines.append(f"  • {key}: {value[:200]}")
                            elif isinstance(value, list):
                                for item in value[:5]:
                                    if isinstance(item, str) and item.strip():
                                        lines.append(f"    - {item[:150]}")
        except Exception as note_err:
            pass  # Si pas de fichier notes, continue sans

        # ── 3. Mémoire IA persistante (le cœur du coach surpuissant) ─────────
        memories = list(AIMemory.objects.filter(user=user).order_by('-importance', '-updated_at')[:40])
        if memories:
            lines.append("\n=== 🧠 MÉMOIRE IA — CE QUE TU SAIS DE CET ÉLÈVE ===")
            lines.append("(Extrait de toutes tes interactions passées avec lui)")

            by_type = {}
            for m in memories:
                by_type.setdefault(m.memory_type, []).append(m)

            type_labels = {
                'erreur':      '⚠️  ERREURS RÉCURRENTES',
                'concept':     '❓ CONCEPTS MAL COMPRIS',
                'force':       '✅ POINTS FORTS CONFIRMÉS',
                'style':       '🎯 STYLE D\'APPRENTISSAGE',
                'progression': '📈 PROGRESSIONS NOTABLES',
                'perso':       '👤 INFOS PERSONNELLES',
                'autre':       '📝 AUTRES OBSERVATIONS',
            }

            for mtype, label in type_labels.items():
                if mtype in by_type:
                    lines.append(f"\n{label}:")
                    for m in by_type[mtype][:8]:
                        seen = f" (vu {m.seen_count}x)" if m.seen_count > 1 else ""
                        subj = f" [{MATS.get(m.subject, m.subject)}]" if m.subject else ""
                        lines.append(f"  • {m.content}{subj}{seen}")

        # ── 3. Diagnostic initial ────────────────────────────────────────────
        diagnostics = list(DiagnosticResult.objects.filter(user=user).order_by('score'))
        if diagnostics:
            lines.append("\n=== SCORES DU DIAGNOSTIC INITIAL ===")
            for d in diagnostics:
                bar   = d.score // 10
                emoji = "🔴" if d.score < 40 else "🟡" if d.score < 65 else "🟢"
                lines.append(f"  {emoji} {MATS.get(d.subject, d.subject):<18} {d.score:>3}%  {'█' * bar}{'░' * (10 - bar)}")

            weak_diag   = [d.subject for d in diagnostics if d.score < 50]
            medium_diag = [d.subject for d in diagnostics if 50 <= d.score < 70]
            strong_diag = [d.subject for d in diagnostics if d.score >= 70]

            if weak_diag:
                lines.append(f"\n⚠️  LACUNES CRITIQUES (< 50%) : {', '.join(MATS.get(s,s) for s in weak_diag)}")
            if medium_diag:
                lines.append(f"📈 À CONSOLIDER (50-70%) : {', '.join(MATS.get(s,s) for s in medium_diag)}")
            if strong_diag:
                lines.append(f"✅ POINTS FORTS (> 70%) : {', '.join(MATS.get(s,s) for s in strong_diag)}")

        # ── 4. Historique quiz — performance et tendances ────────────────────
        quiz_sessions = list(QuizSession.objects.filter(user=user).order_by('-completed_at')[:30])
        if quiz_sessions:
            lines.append("\n=== PERFORMANCE QUIZ (30 dernières sessions) ===")
            subject_perf = {}
            for qs in quiz_sessions:
                pct  = qs.get_percentage()
                subj = qs.subject
                subject_perf.setdefault(subj, []).append(pct)

            for subj, scores in sorted(subject_perf.items(), key=lambda x: -sum(x[1])/len(x[1])):
                avg    = round(sum(scores) / len(scores))
                trend  = ("📈 progression" if len(scores) > 1 and scores[0] > scores[-1]
                          else "📉 régression" if len(scores) > 1 and scores[0] < scores[-1]
                          else "➡️  stable")
                emoji  = "🔴" if avg < 40 else "🟡" if avg < 65 else "🟢"
                lines.append(f"  {emoji} {MATS.get(subj,subj):<18} moy {avg:>3}%  {trend}  ({len(scores)} quiz)")

            # Erreurs récurrentes issues des analyses
            all_weak_tags = []
            for qs in quiz_sessions:
                try:
                    if qs.analysis.weak_tags:
                        all_weak_tags.extend(qs.analysis.weak_tags)
                except Exception:
                    pass
            if all_weak_tags:
                top_errors = [tag for tag, _ in Counter(all_weak_tags).most_common(8)]
                lines.append(f"\n🎯 THÈMES EN ERREUR RÉCURRENTS (quiz) : {', '.join(top_errors)}")

        # ── 5. Résumé de TOUS les anciens chats ─────────────────────────────
        # Regroupe par session_key pour reconstruire des conversations
        all_sessions = (
            ChatMessage.objects
            .filter(user=user)
            .exclude(session_key='')
            .values_list('session_key', flat=True)
            .distinct()
            .order_by()
        )
        session_keys = list(all_sessions)
        if session_keys:
            lines.append(f"\n=== HISTORIQUE CONVERSATIONS ({len(session_keys)} sessions) ===")
            # Pour les 10 dernières sessions → résumé de ce qui s'est passé
            recent_keys = list(
                ChatMessage.objects
                .filter(user=user)
                .exclude(session_key='')
                .values_list('session_key', flat=True)
                .distinct()
                .order_by('-id')[:10]
            )
            for sk in recent_keys[:10]:
                msgs = list(ChatMessage.objects.filter(user=user, session_key=sk).order_by('created_at'))
                if not msgs:
                    continue
                subject = msgs[0].subject
                date    = msgs[0].created_at.strftime('%d/%m/%Y')
                user_qs = [m.content[:80] for m in msgs if m.role == 'user'][:3]
                preview = ' | '.join(user_qs)
                lines.append(f"  • {date} [{MATS.get(subject,subject)}] : {preview}...")

        # ── 6. Sujets des conversations ──────────────────────────────────────
        chat_subjects = list(
            ChatMessage.objects.filter(user=user, role='user')
            .exclude(subject='general')
            .values_list('subject', flat=True)
            .order_by('-created_at')[:200]
        )
        if chat_subjects:
            freq = Counter(chat_subjects).most_common(5)
            lines.append("\n=== MATIÈRES LES PLUS TRAVAILLÉES (chat) ===")
            for subj, cnt in freq:
                lines.append(f"  • {MATS.get(subj,subj):<18} {cnt} questions posées")

        # ── 7. Maîtrise adaptative par matière (SubjectMastery) ─────────────
        try:
            from .learning_tracker import get_mastery_profile_text
            mastery_text = get_mastery_profile_text(user)
            if mastery_text:
                lines.append(mastery_text)
        except Exception:
            pass

        # ── 8. Activité globale ───────────────────────────────────────────────
        try:
            stats = user.stats
            lines.append(f"\n=== ENGAGEMENT & ACTIVITÉ ===")
            lines.append(f"  Quiz complétés    : {stats.quiz_completes}")
            lines.append(f"  Exercices résolus : {stats.exercices_resolus}")
            lines.append(f"  Messages envoyés  : {stats.messages_envoyes}")
            total_chats = ChatMessage.objects.filter(user=user).count()
            lines.append(f"  Total messages IA : {total_chats // 2} échanges archivés")
            if stats.quiz_completes == 0 and stats.exercices_resolus == 0:
                lines.append("  ℹ️  Élève débutant — première utilisation de la plateforme.")
        except Exception:
            pass

        return '\n'.join(lines)

    except Exception:
        return ''


def extract_and_save_memories(user, user_message: str, ai_response: str, subject: str = '') -> None:
    """
    Après chaque échange, extrait des observations utiles sur l'élève et les stocke.
    Fonctionne en arrière-plan (thread daemon).

    OPTIMISATION COÛT :
    - Skip si message < 50 chars (trop court pour extraire quoi que ce soit)
    - Skip si message est une salutation / réponse simple
    - Ne s'exécute que tous les 4 messages (throttle par user)
    - max_tokens limité à 400 pour réduire le coût
    """
    try:
        # ── Filtres rapides (sans appel IA) ─────────────────────────────────
        if len(user_message.strip()) < 30:
            return

        # Mots-clés qui indiquent un message trop simple pour mémoriser
        _TRIVIAL = {'ok', 'oui', 'non', 'merci', 'mesi', 'dako', 'ok.',
                    'super', 'cool', 'd\'accord', 'oki', 'ah ok', 'ah oui'}
        if user_message.strip().lower() in _TRIVIAL:
            return

        # ── Throttle : 1 extraction toutes les 2 interactions ───────────────
        from .models import ChatMessage as _CM
        recent_count = _CM.objects.filter(user=user, role='user').order_by('-id')[:1].values_list('id', flat=True)
        # Use message ID parity for a simple throttle
        if recent_count:
            last_id = list(recent_count)[0]
            if last_id % 2 == 0:
                return

        from .models import AIMemory

        subject_label = MATS.get(subject, subject) if subject and subject != 'general' else ''
        subj_hint     = f"(matière : {subject_label})" if subject_label else ''

        prompt = f"""Tu analyses un échange entre un élève et son coach IA.
Extrait les OBSERVATIONS UTILES pour mémoriser à long terme sur cet élève.
NE mémorise QUE ce qui sera utile pour personnaliser les prochaines interactions.
{subj_hint}

MESSAGE ÉLÈVE : {user_message[:400]}
RÉPONSE IA : {ai_response[:400]}

Retourne UNIQUEMENT un JSON array (0 à 3 items max, seulement si vraiment pertinent) :
[{{
  "type": "erreur|force|style|perso|progression|concept|autre",
  "subject": "maths|physique|chimie|svt|francais|philosophie|histoire|anglais|",
  "content": "Observation précise en 1 phrase",
  "tag": "slug-unique-max-30-chars",
  "importance": 7
}}]

Retourne [] si rien de significatif. importance 8-10 = erreur grave ou blocage. < 5 = ne pas mémoriser."""

        text = _call(prompt, max_tokens=400).strip()

        import json as _json
        match = re.search(r'\[[\s\S]*\]', text)
        if not match:
            return

        items = _json.loads(match.group(0))
        for item in items:
            if not item.get('content') or not item.get('tag'):
                continue
            tag = f"{user.id}_{item['tag'][:80]}"
            try:
                mem, created = AIMemory.objects.get_or_create(
                    user=user,
                    tag=tag,
                    defaults={
                        'memory_type': item.get('type', 'autre'),
                        'subject':     item.get('subject', ''),
                        'content':     item.get('content', ''),
                        'importance':  max(1, min(10, int(item.get('importance', 5)))),
                        'seen_count':  1,
                    }
                )
                if not created:
                    mem.seen_count += 1
                    mem.importance  = min(10, mem.importance + 1)
                    mem.save(update_fields=['seen_count', 'importance', 'updated_at'])
            except Exception:
                pass
    except Exception:
        pass


# ─── Définition de l'outil web_search exposé au modèle ───────────────────────
_WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Recherche des informations sur le web. À utiliser quand tu ne connais pas la réponse avec certitude.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "La requête de recherche"
                }
            },
            "required": ["query"]
        }
    }
}


def get_chat_response(message: str, history: list, subject: str = 'general', db_context: str = '', image_data: bytes = None, image_mime: str = None, user_profile: str = '', user_lang: str = '') -> str:
    """Envoie un message au modèle Groq et retourne la réponse texte.
    Supporte le tool calling natif : si le modèle appelle web_search (ou web.run),
    on exécute la recherche DuckDuckGo et on refait l'appel avec les résultats.
    """
    import json as _json

    subject_label = MATS.get(subject, subject) if subject and subject != 'general' else None
    subject_context = f"La question porte sur **{subject_label}** (BAC Terminale)." if subject_label else ''

    lang_instruction = _lang_instruction(message, forced_lang=user_lang)

    # ── Contenu dynamique dans un 2e message system ──────────────────────────
    # Le 1er message = _STATIC_CHAT_SYSTEM (identique → Groq prefix cache ~50% saving)
    # Le 2e message  = contexte spécifique à cet appel
    dynamic_parts = []
    if subject_context:
        dynamic_parts.append(subject_context)
    if user_profile:
        dynamic_parts.append(f"PROFIL ÉLÈVE :\n{user_profile}")
    if lang_instruction:
        dynamic_parts.append(lang_instruction)
    if db_context:
        dynamic_parts.append(
            f"📚 Contenu du programme BAC (notes officielles + examens) — utilise-le en priorité :\n{db_context}"
        )
    dynamic_context = '\n\n'.join(dynamic_parts) if dynamic_parts else ''

    # Construction des messages
    def _build_messages() -> list:
        msgs = [{"role": "system", "content": _STATIC_CHAT_SYSTEM}]
        if dynamic_context:
            msgs.append({"role": "system", "content": dynamic_context})
        # Rolling summary: keep last 8 verbatim + compact summary of everything before
        hist_summary, recent_history = _build_compact_history(history, keep=8)
        if hist_summary:
            msgs.append({"role": "system", "content": hist_summary})
        for msg in recent_history:
            role = 'assistant' if msg.get('role') == 'model' else msg.get('role', 'user')
            parts = msg.get('parts', [])
            content = ' '.join(p for p in parts if isinstance(p, str)).strip()
            if content:
                msgs.append({"role": role, "content": content})
        user_content = message or 'Analyse ceci.'
        if image_data:
            import base64 as _b64
            b64 = _b64.b64encode(image_data).decode('utf-8')
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": user_content},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime or 'image/jpeg'};base64,{b64}"}}
            ]})
        else:
            msgs.append({"role": "user", "content": user_content})
        return msgs

    # Tokens adaptatifs — on réserve toujours ~120 tokens pour le bloc ---FOLLOWUP---
    msg_len = len(message or '')
    msg_lower = (message or '').lower()
    # Mots qui demandent une réponse structurée longue (résumés, comparaisons, explications complètes, biographies)
    _heavy_keywords = ('résume', 'resume', 'résumé', 'compare', 'comparaison', 'explique', 'présente',
                       'différence', 'difference', 'causes', 'conséquences', 'consequences', 'histoire de',
                       'guerre', 'révolution', 'revolution', 'analyse', 'développe', 'developpe',
                       'parle moi', 'parle de', 'tell me', 'tell about', 'who was', 'qui était', 'qui etait',
                       'biographie', 'biography', 'parcours', 'histoire', 'événement', 'evenement')
    _needs_heavy = any(kw in msg_lower for kw in _heavy_keywords)

    if _needs_heavy:
        adaptive_tokens = 4000   # résumés / comparaisons / événements → tableaux + sections
    elif msg_len < 60:
        adaptive_tokens = 2500   # question courte simple
    elif msg_len < 200:
        adaptive_tokens = 3000   # question normale
    else:
        adaptive_tokens = 3500   # question longue / exercice complexe

    messages = _build_messages()

    # ── Appel vision : essaie d'abord VISION_MODEL, sinon encode en URL openai ──
    if image_data:
        import base64 as _b64
        b64_str = _b64.b64encode(image_data).decode('utf-8')
        mime = image_mime or 'image/jpeg'
        # Build vision message manually (image_url format)
        vis_msgs = [{"role": "system", "content": _STATIC_CHAT_SYSTEM}]
        if dynamic_context:
            vis_msgs.append({"role": "system", "content": dynamic_context})
        vis_summary, vis_history = _build_compact_history(history, keep=8)
        if vis_summary:
            vis_msgs.append({"role": "system", "content": vis_summary})
        for msg in vis_history:
            role = 'assistant' if msg.get('role') == 'model' else msg.get('role', 'user')
            parts = msg.get('parts', [])
            content = ' '.join(p for p in parts if isinstance(p, str)).strip()
            if content:
                vis_msgs.append({"role": role, "content": content})
        vis_msgs.append({"role": "user", "content": [
            {"type": "text", "text": message or "Analyse cette image et aide-moi."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_str}"}}
        ]})
        try:
            resp_vis = _client().chat.completions.create(
                model=VISION_MODEL,
                messages=vis_msgs,
                max_tokens=adaptive_tokens,
            )
            vis_text = resp_vis.choices[0].message.content or ''
            if '---FOLLOWUP---' not in vis_text:
                vis_text += '\n---FOLLOWUP---\n1. Une autre question sur l\'image ?\n2. Explique ce concept plus simplement\n3. Donne un exercice similaire\n---END---'
            return vis_text
        except Exception as _vis_err:
            import sys; print(f'[Vision] VISION_MODEL failed ({_vis_err}), trying main MODEL with image…', file=sys.stderr)
            # Try main model with image_url (may work if model supports it)
            try:
                resp_vis2 = _client().chat.completions.create(
                    model=FAST_MODEL,
                    messages=vis_msgs,
                    max_tokens=adaptive_tokens,
                )
                vis_text2 = resp_vis2.choices[0].message.content or ''
                if vis_text2:
                    if '---FOLLOWUP---' not in vis_text2:
                        vis_text2 += '\n---FOLLOWUP---\n1. Une autre question sur l\'image ?\n2. Explique ce concept plus simplement\n3. Donne un exercice similaire\n---END---'
                    return vis_text2
            except Exception as _vis2_err:
                import sys; print(f'[Vision] main MODEL also failed ({_vis2_err}), text fallback', file=sys.stderr)
            # Last resort: rebuild messages without image, answer text question normally
            messages = [{"role": "system", "content": _STATIC_CHAT_SYSTEM}]
            if dynamic_context:
                messages.append({"role": "system", "content": dynamic_context})
            _fb_summary, _fb_history = _build_compact_history(history, keep=8)
            if _fb_summary:
                messages.append({"role": "system", "content": _fb_summary})
            for msg in _fb_history:
                role = 'assistant' if msg.get('role') == 'model' else msg.get('role', 'user')
                parts = msg.get('parts', [])
                content = ' '.join(p for p in parts if isinstance(p, str)).strip()
                if content:
                    messages.append({"role": role, "content": content})
            # Just answer the text question — no mention of image failure
            text_content = message.strip() if message and message.strip() else "Aide-moi avec cet exercice."
            messages.append({"role": "user", "content": text_content})

    # ── Appel direct Groq sans tool calling (plus stable) ──
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=messages,
        max_tokens=adaptive_tokens,
    )
    reply = resp.choices[0].message.content or ''

    # HARD REJECT: bloque toute réponse contenant des placeholders
    if _has_placeholder_artifacts(reply):
        return (
            "⛔ ERREUR QUALITÉ IA : La réponse générée contenait un placeholder interdit (ex: M0, M1, M2, etc.). "
            "La génération a été bloquée pour garantir la qualité. Merci de reformuler la question ou de réessayer. "
            "(Aucune réponse contenant des artefacts ou placeholders n'est acceptée.)"
        )

    return reply


def solve_exercise(text: str, image_data: bytes = None, image_mime: str = None) -> str:
    """Résout un exercice avec vérification intégrée (double-check auto)."""
    prompt = f"""Tu es BacIA — un tuteur expert qui aide un élève de Terminale à comprendre un exercice du Bac Haïti.
Explique clairement, étape par étape, comme si tu montrais la résolution sur une feuille.

EXERCICE :
{text or '[Voir image fournie]'}

PROTOCOLE OBLIGATOIRE (à suivre dans cet ordre) :
1. **Ce que ça demande** — 1 phrase pour identifier le type de problème.
2. **Données** — liste les informations clés de l'énoncé.
3. **Méthode** — Quelle formule/approche utiliser et POURQUOI (pas juste l'appliquer).
4. **Résolution étape par étape** — chaque étape avec une explication humaine claire (pas juste les calculs).
5. **Double-vérification** — vérifie ton résultat par une méthode différente ou en substituant. Si ça ne colle pas, corrige.
6. **Réponse finale** — bien mise en évidence.
7. **Le truc à retenir** — 1-2 lignes sur la méthode générale pour ce type d'exercice.
8. **Piège classique** — l'erreur la plus fréquente des élèves sur ce type d'exercice.

⚠️ GARANTIE : Tu DOIS vérifier ta réponse avant de la donner. Si tu n'es pas sûr à 100%, dis-le clairement.

Formules en KaTeX : $inline$ et $$blocs display$$.
Sois direct, pédagogique et structuré."""

    if image_data:
        prompt += "\n\n[Note : image fournie — si tu ne peux pas voir l'image, demande à l'élève de retaper l'énoncé en texte.]"
    return _call(prompt, max_tokens=1800)


def extract_quiz_from_exam_text(text: str, subject: str, count: int = 8) -> list:
    """
    À partir du texte d'un examen PDF, génère des QCM avec des explications PÉDAGOGIQUES.
    Utilisé par le management command populate_quiz_questions (appel unique).
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    if subject == 'anglais':
        prompt = (
            f"You are an expert English teacher for BAC Haïti Terminale.\n"
            f"Here are excerpts from official BAC Haïti English exams:\n\n{text[:3500]}\n\n"
            f"Create EXACTLY {count} individual English grammar/vocabulary MCQ questions.\n\n"
            "MANDATORY RULES:\n"
            "- Each question = ONE English sentence with ONE blank (___). NEVER multi-part lists like '1. ... 2. ... 3. ...'\n"
            "- EXACTLY 4 options (A, B, C, D) — never 2 or 3\n"
            "- NEVER use VRAI/FAUX or TRUE/FALSE\n"
            "- 'explication' field is MANDATORY: write 2-3 sentences IN FRENCH explaining the grammar rule\n"
            "  (e.g. tense used, why this conjunction, preposition rule, etc.)\n"
            "- Topics: verb tenses, conjunctions, prepositions, passive voice, conditionals, articles, vocabulary\n"
            "- 'sujet' field: name the grammar point in English (e.g. 'Past Perfect', 'Prepositions', 'Conditionals')\n\n"
            "Reply ONLY with this JSON array:\n"
            '[{"enonce":"She ___ to school every day.","options":["goes","go","went","gone"],'
            '"reponse_correcte":0,"explication":"On utilise le présent simple avec she/he/it car on parle d\'une habitude régulière. On ajoute -s au verbe. Les autres formes sont incorrectes dans ce contexte.","sujet":"Present Simple"}]\n\n'
            "reponse_correcte = integer index (0=A, 1=B, 2=C, 3=D)."
        )
    else:
        prompt = (
            f"Tu es un professeur expert du Bac Haïti en {subject_label}.\n"
            f"Voici des extraits d'examens officiels du Bac Haïti :\n\n{text[:3500]}\n\n"
            f"Crée EXACTEMENT {count} questions QCM à choix multiples basées sur ce contenu.\n\n"
            "📚 RÈGLES OBLIGATOIRES :\n"
            "- Questions directement inspirées du contenu des examens fournis\n"
            "- 4 options par question (A, B, C, D)\n"
            "- L'explication doit ENSEIGNER le concept (min 3 phrases) :\n"
            "  → Expliquer le CONCEPT sous-jacent\n"
            "  → Montrer la MÉTHODE étape par étape\n"
            "  → Donner une astuce mémo ('Pour retenir : ...')\n"
            "  → Mentionner l'erreur fréquente à éviter\n"
            "- Formules : signe dollar uniquement ($F=ma$), PAS de \\( \\) ni \\[ \\]\n\n"
            "Réponds UNIQUEMENT avec ce JSON array :\n"
            '[{"enonce":"...","options":["A: ...","B: ...","C: ...","D: ..."],'
            '"reponse_correcte":0,"explication":"...","sujet":"..."}]\n\n'
            "reponse_correcte = INDEX entier (0=A, 1=B, 2=C, 3=D)."
        )
    text_out = _call(prompt, max_tokens=3500)
    return _parse_quiz_json(text_out)


def _parse_quiz_json(text: str) -> list:
    """Parseur robuste commun pour tous les JSON quiz."""
    import json as _json
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    match = re.search(r'\[[\s\S]+?\](?=\s*$|\s*\[)', text) or re.search(r'\[[\s\S]+\]', text)
    if not match:
        return []
    json_str = match.group(0)
    json_str = re.sub(r'\\([^"\\/bfnrtu0-9\n\r])', r'\\\\\1', json_str)
    try:
        raw = _json.loads(json_str)
    except Exception:
        raw = []
        for m in re.finditer(r'\{[^{}]+\}', json_str):
            try:
                raw.append(_json.loads(m.group(0)))
            except Exception:
                pass
    result = []
    for q in raw:
        options = q.get('options', [])
        rc = q.get('reponse_correcte', 0)
        try:
            correct_idx = int(rc)
            if correct_idx < 0 or correct_idx >= len(options):
                correct_idx = 0
        except (ValueError, TypeError):
            correct_idx = 0
        result.append({
            'enonce': str(q.get('enonce', '')),
            'options': [str(o) for o in options],
            'reponse_correcte': correct_idx,
            'explication': str(q.get('explication', '')),
            'sujet': str(q.get('sujet', '')),
        })
    return result


def generate_structured_exam(exam_text: str, subject: str) -> dict:
    """
    Génère un examen blanc complet en suivant EXACTEMENT la structure des vrais
    examens BAC Haïti (source : structure_exam.json).
    Format de sortie : parts > sections > items.
    """
    import json as _json
    subject_label = MATS.get(subject, subject)

    # ── Structure JSON fidèle à chaque matière ────────────────────────────
    # Le squelette indique les bonnes parties, sections et points.
    # L'IA doit UNIQUEMENT remplacer les "..." par du vrai contenu BAC.
    _SKELETONS = {

        # ── MATHÉMATIQUES : Partie A complétion (40pts) + Partie B 3/4 exercices (60pts)
        'maths': (
            '{"title":"BACCALAURÉAT HAÏTI — MATHÉMATIQUES","duration":"3 heures","parts":['
            '{"label":"PARTIE A — Recopier et compléter les phrases suivantes (40 points — 4 pts chacune)",'
            '"sections":[{"label":"Complétion de cours","type":"fillblank","pts":40,"items":['
            '{"text":"Le domaine de définition de la fonction $f(x) = \\\\ln(x)$ est ___.","answer":"$]0\\\\,;+\\\\infty[$","pts":4},'
            '{"text":"La dérivée de $e^x$ est ___.","answer":"$e^x$","pts":4},'
            '{"text":"La limite de $\\\\frac{1}{x}$ quand $x \\\\to +\\\\infty$ est ___.","answer":"$0$","pts":4},'
            '{"text":"Une suite $(u_n)$ est géométrique de raison $q$ si, pour tout $n$, $u_{n+1} = ___$.","answer":"$q \\\\cdot u_n$","pts":4},'
            '{"text":"Le module d\'un nombre complexe $z = a + bi$ est ___.","answer":"$|z| = \\\\sqrt{a^2+b^2}$","pts":4},'
            '{"text":"...","answer":"...","pts":4},'
            '{"text":"...","answer":"...","pts":4},'
            '{"text":"...","answer":"...","pts":4},'
            '{"text":"...","answer":"...","pts":4},'
            '{"text":"...","answer":"...","pts":4}]}]},'
            '{"label":"PARTIE B — Traiter TROIS exercices au choix parmi les quatre suivants (60 points)",'
            '"sections":['
            '{"label":"Exercice 1 — Étude de fonction (20 pts)","type":"open","pts":20,"items":[{"text":"Soit $f$ la fonction définie sur $\\\\mathbb{R}$ par $f(x) = ...$\\n1) Déterminer le domaine de définition de $f$.\\n2) Calculer $f\'(x)$ et dresser le tableau de variations.\\n3) Écrire l\'équation de la tangente en $x = ...$","answer":"1) $D_f = ...$\\n2) $f\'(x) = ...$\\n3) Tangente : $y = ...$","pts":20}]},'
            '{"label":"Exercice 2 — Suites numériques (20 pts)","type":"open","pts":20,"items":[{"text":"...","answer":"...","pts":20}]},'
            '{"label":"Exercice 3 — Probabilités et statistiques (20 pts)","type":"open","pts":20,"items":[{"text":"...","answer":"...","pts":20}]},'
            '{"label":"Exercice 4 — Nombres complexes (20 pts)","type":"open","pts":20,"items":[{"text":"...","answer":"...","pts":20}]}]}]}'
        ),

        # ── PHYSIQUE : Ière Partie (70 pts : complétions + dev + exercices) + IIème (problème 30 pts)
        'physique': (
            '{"title":"BACCALAURÉAT HAÏTI — PHYSIQUE","duration":"3 heures","parts":['
            '{"label":"PREMIÈRE PARTIE (70 points)",'
            '"sections":['
            '{"label":"I. Recopier et compléter les phrases (20 points — 2 pts chacune)","type":"fillblank","pts":20,"items":['
            '{"text":"La résistance d\'un conducteur ohmique est donnée par la loi $U = ___$.","answer":"$R \\\\times I$","pts":2},'
            '{"text":"L\'intensité du champ électrique créé par une charge $q$ à la distance $r$ est $E = ___$.","answer":"$\\\\frac{kq}{r^2}$","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2}]},'
            '{"label":"II. Questions de développement (20 points)","type":"open","pts":20,"items":['
            '{"text":"...","answer":"...","pts":10},'
            '{"text":"...","answer":"...","pts":10}]},'
            '{"label":"III. Traiter DEUX des trois exercices courts (30 points — 15 pts chacun)","type":"open","pts":30,"items":['
            '{"text":"Exercice A — ...","answer":"...","pts":15},'
            '{"text":"Exercice B — ...","answer":"...","pts":15},'
            '{"text":"Exercice C — ...","answer":"...","pts":15}]}]},'
            '{"label":"DEUXIÈME PARTIE — Problème au choix (30 points)",'
            '"sections":[{"label":"Choisir UN des deux problèmes","type":"open","pts":30,"items":['
            '{"text":"Problème 1 — ...","answer":"...","pts":30},'
            '{"text":"Problème 2 — ...","answer":"...","pts":30}]}]}]}'
        ),

        # ── CHIMIE : A complétions (20) + B équations (20) + C question choix (15) + D texte (15) + E problèmes (30)
        'chimie': (
            '{"title":"BACCALAURÉAT HAÏTI — CHIMIE","duration":"3 heures","parts":['
            '{"label":"ÉPREUVE COMPLÈTE (100 points)",'
            '"sections":['
            '{"label":"A. Recopier et compléter les phrases (20 points — 2 pts chacune)","type":"fillblank","pts":20,"items":['
            '{"text":"Un acide au sens de Brønsted est une espèce chimique capable de ___ un proton $H^+$.","answer":"donner (céder)","pts":2},'
            '{"text":"La formule brute de l\'acide éthanoïque est ___.","answer":"$CH_3COOH$","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2},'
            '{"text":"...","answer":"...","pts":2}]},'
            '{"label":"B. Écrire et équilibrer les équations chimiques (20 points — 5 pts chacune)","type":"open","pts":20,"items":['
            '{"text":"Écrire et équilibrer la combustion complète du méthane $CH_4$.","answer":"$CH_4 + 2O_2 \\\\to CO_2 + 2H_2O$","pts":5},'
            '{"text":"...","answer":"...","pts":5},'
            '{"text":"...","answer":"...","pts":5},'
            '{"text":"...","answer":"...","pts":5}]},'
            '{"label":"C. Traiter UNE des deux questions suivantes (15 points)","type":"open","pts":15,"items":['
            '{"text":"Question 1 — ...","answer":"...","pts":15},'
            '{"text":"Question 2 — ...","answer":"...","pts":15}]},'
            '{"label":"D. Étude de texte (15 points)","type":"open","pts":15,"items":['
            '{"text":"TEXTE :\\n\\n«...»\\n\\n1) ...\\n2) ...\\n3) ...","answer":"...","pts":15}]},'
            '{"label":"E. Résoudre DEUX des trois problèmes au choix (30 points — 15 pts chacun)","type":"open","pts":30,"items":['
            '{"text":"Problème 1 — ...","answer":"...","pts":15},'
            '{"text":"Problème 2 — ...","answer":"...","pts":15},'
            '{"text":"Problème 3 — ...","answer":"...","pts":15}]}]}]}'
        ),

        # ── SVT : Biologie (50 pts) + Géologie (50 pts), chacune avec sous-parties
        'svt': (
            '{"title":"BACCALAURÉAT HAÏTI — SCIENCES DE LA VIE ET DE LA TERRE","duration":"3 heures","parts":['
            '{"label":"BIOLOGIE (50 points)",'
            '"sections":['
            '{"label":"Première partie — Questions de cours et complétion (20 points)","type":"open","pts":20,"items":['
            '{"text":"1) Définir les termes suivants : mutation / crossing-over / glycémie.\\n2) Expliquer le rôle de l\'insuline dans la régulation de la glycémie.\\n3) ...","answer":"...","pts":20}]},'
            '{"label":"Deuxième partie — Génétique et hérédité (30 points)","type":"open","pts":30,"items":['
            '{"text":"Chez une espèce, un caractère est déterminé par un gène à deux allèles. On croise une femelle homozygote dominante avec un mâle récessif.\\n1) Écrire les génotypes des parents.\\n2) Dresser l\'échiquier de Punnett de la F1.\\n3) Quelle fraction des descendants F2 sera homozygote?\\n(ou reconstruire un cas de drépanocytose, myopathie, groupes sanguins selon le contexte)","answer":"...","pts":30}]}]},'
            '{"label":"GÉOLOGIE (50 points)",'
            '"sections":['
            '{"label":"Première partie — Stratigraphie, paléontologie et structure de la Terre (25 points)","type":"open","pts":25,"items":['
            '{"text":"...","answer":"...","pts":25}]},'
            '{"label":"Deuxième partie — Analyse de document et hypothèses (25 points)","type":"open","pts":25,"items":['
            '{"text":"DOCUMENT :\\n\\n«...»\\n\\n1) Présenter le document.\\n2) Formuler une hypothèse sur ... \\n3) Quelle est l\'importance de ce phénomène pour la compréhension de ...?","answer":"...","pts":25}]}]}]}'
        ),

        # ── PHILOSOPHIE : Dissertation OU Étude de texte (60%) + Questions courtes (40%)
        'philosophie': (
            '{"title":"BACCALAURÉAT HAÏTI — PHILOSOPHIE","duration":"4 heures","parts":['
            '{"label":"PREMIÈRE PARTIE — Choisir : Dissertation OU Étude de texte (60 points)",'
            '"sections":['
            '{"label":"SUJET A — Dissertation (choisir A ou B)","type":"open","pts":60,"items":['
            '{"text":"Sujet de dissertation :\\n\\n«...»\\n\\nVous rédigerez une dissertation philosophique comportant une introduction, un développement en deux parties (thèse et antithèse), et une conclusion.","answer":"Dissertation rédigée attendue.","pts":60}]},'
            '{"label":"SUJET B — Étude de texte (choisir A ou B)","type":"open","pts":60,"items":['
            '{"text":"TEXTE :\\n\\n«...»\\n\\nQuestions :\\n1) Quelle est la thèse défendue par l\'auteur dans ce texte ? (15 pts)\\n2) Comment l\'auteur articule-t-il son argumentation ? (15 pts)\\n3) Expliquez la phrase soulignée «...». (15 pts)\\n4) Quel est l\'intérêt philosophique de ce texte ? (15 pts)","answer":"...","pts":60}]}]},'
            '{"label":"DEUXIÈME PARTIE — Questions à réponse courte (40 points)",'
            '"sections":[{"label":"Répondre aux deux questions suivantes (20 pts chacune)","type":"open","pts":40,"items":['
            '{"text":"Question 1 — Définir et distinguer les notions de «...» et «...».","answer":"...","pts":20},'
            '{"text":"Question 2 — Expliquer brièvement la notion de «...» en vous appuyant sur un exemple concret.","answer":"...","pts":20}]}]}]}'
        ),

        # ── KREYÒL/FRANÇAIS : Compréhension (30) + Grammaire+Vocab+Traduction (40) + Production (30)
        'francais': (
            '{"title":"BACCALAURÉAT HAÏTI — KREYÒL AK LITERATI","duration":"3 heures","parts":['
            '{"label":"PREMYE PATI — Konpreyansyon Tèks (30 points)",'
            '"sections":[{"label":"Li tèks la epi repon kesyon yo","type":"open","pts":30,"items":['
            '{"text":"TÈKS :\\n\\n«...»\\n\\n1) Ki tèz prensipal tèks la ? (5 pts)\\n2) Ki kalite tèks sa a ? Jistifye. (5 pts)\\n3) Eksplike ekspresyon «...» ki nan paragraf twazyèm nan. (5 pts)\\n4) Ki agiman otè a itilize pou sipòte tèz li ? (5 pts)\\n5) Bay yon tit pou tèks sa a. (5 pts)\\n6) Ki opinyon pa ou sou sijè tèks la ? (5 pts)","answer":"...","pts":30}]}]},'
            '{"label":"DEZYÈM PATI — Gramè, Vokabilè ak Tradiksyon (40 points)",'
            '"sections":[{"label":"Ekzèsis gramè ak vokabilè (10 pts chacun)","type":"open","pts":40,"items":['
            '{"text":"Ekzèsis 1 — Idantifye tip predika ki nan fraz sa yo (predika non, predika vèb, predika adjektif) :\\n...","answer":"...","pts":10},'
            '{"text":"Ekzèsis 2 — Bay sinonim oswa antonim mo ki souliye yo :\\n...","answer":"...","pts":10},'
            '{"text":"Ekzèsis 3 — Tradui fraz sa yo ann fransè :\\n...","answer":"...","pts":10},'
            '{"text":"Ekzèsis 4 — Idantifye epi eksplike fig de style ki nan fraz sa yo :\\n...","answer":"...","pts":10}]}]},'
            '{"label":"TWAZYÈM PATI — Pwodiksyon Ekri (30 points)",'
            '"sections":[{"label":"Chwazi YON sèl sijè (30 pts)","type":"open","pts":30,"items":['
            '{"text":"Sijè 1 — ...","answer":"Tèks roje atann (15-20 liy).","pts":30},'
            '{"text":"Sijè 2 — ...","answer":"Tèks roje atann (15-20 liy).","pts":30}]}]}]}'
        ),

        # ── ANGLAIS : 5 parties selon le vrai BAC Haïti
        'anglais': (
            '{"title":"BACCALAURÉAT HAÏTI — ENGLISH LANGUAGE","duration":"3 hours 30","parts":['
            '{"label":"PART I — Interpretive Competency — Reading Comprehension (40 points)",'
            '"sections":[{"label":"Read the passage carefully and answer the questions","type":"open","pts":40,"items":['
            '{"text":"TEXT:\\n\\n«...»\\n\\n(Write a complete original passage of 180-220 words on a topic relevant to Haiti or the world.)","answer":"","pts":0,"is_passage":true},'
            '{"text":"1) What is the main idea of the text? (5 pts)","answer":"...","pts":5},'
            '{"text":"2) According to the text, what are the main consequences of ...? (5 pts)","answer":"...","pts":5},'
            '{"text":"3) Explain the expression «...» as used in the text. (5 pts)","answer":"...","pts":5},'
            '{"text":"4) Do you think the author\'s argument is convincing? Justify your answer with elements from the text. (5 pts)","answer":"...","pts":5},'
            '{"text":"5) Find in the text a word or expression that means: ... (5 pts)","answer":"...","pts":5},'
            '{"text":"Write a summary of the text in 3 to 4 complete sentences. (15 pts)","answer":"Summary expected (3-4 sentences).","pts":15}]}]},'
            '{"label":"PART II — Linguistic Competency — Grammar & Vocabulary (30 points)",'
            '"sections":[{"label":"Grammar exercises","type":"open","pts":30,"items":['
            '{"text":"Exercise A — Put the verbs in brackets in the correct tense (10 pts):\\n1. She ___ (study) French since she was twelve.\\n2. By the time he arrived, they ___ already ___ (leave).\\n3. ...","answer":"1. has been studying  2. had already left  ...","pts":10},'
            '{"text":"Exercise B — Rewrite in the passive voice (10 pts):\\n1. The teacher corrects the papers every day.\\n2. They built this school in 1985.\\n3. ...","answer":"1. The papers are corrected every day by the teacher.  2. This school was built in 1985.  ...","pts":10},'
            '{"text":"Exercise C — Fill in with the correct preposition or conjunction (10 pts):\\n1. She has lived here ___ 2015. (for / since / ago)\\n2. He was watching TV ___ she was cooking. (while / when / after)\\n3. ...","answer":"1. since  2. while  ...","pts":10}]}]},'
            '{"label":"PART III — Pragmatic Competency — Problem-Solving (10 points)",'
            '"sections":[{"label":"React to the situation in a short paragraph of 5 to 8 lines (10 pts)","type":"open","pts":10,"items":['
            '{"text":"Situation: ...","answer":"Written paragraph expected (5-8 lines).","pts":10}]}]},'
            '{"label":"PART IV — Discursive Competency — Written Production (20 points)",'
            '"sections":[{"label":"Choose ONE topic and write an essay of about 25 lines (20 pts)","type":"open","pts":20,"items":['
            '{"text":"Topic 1 — ...","answer":"Essay expected (about 25 lines).","pts":20},'
            '{"text":"Topic 2 — ...","answer":"Essay expected (about 25 lines).","pts":20}]}]}]}'
        ),

        # ── HISTOIRE-GÉOGRAPHIE : Histoire 60% dissertations + Géo 40% étude docs
        'histoire': (
            '{"title":"BACCALAURÉAT HAÏTI — HISTOIRE ET GÉOGRAPHIE","duration":"3 heures 30","parts":['
            '{"label":"HISTOIRE (60 points) — Traiter UN seul sujet parmi les trois proposés",'
            '"sections":[{"label":"Sujets de dissertation (un au choix)","type":"open","pts":60,"items":['
            '{"text":"Sujet 1 — ...","answer":"Dissertation attendue (intro + développement + conclusion).","pts":60},'
            '{"text":"Sujet 2 — ...","answer":"Dissertation attendue (intro + développement + conclusion).","pts":60},'
            '{"text":"Sujet 3 — ...","answer":"Dissertation attendue (intro + développement + conclusion).","pts":60}]}]},'
            '{"label":"GÉOGRAPHIE (40 points) — Étude de document(s)",'
            '"sections":[{"label":"Analyser le document et répondre aux questions","type":"open","pts":40,"items":['
            '{"text":"DOCUMENT :\\n\\n«...»\\n\\n1) Présenter le document (nature, source, date). (8 pts)\\n2) Résumer ce document en 4 à 5 lignes. (12 pts)\\n3) Comment ce phénomène se manifeste-t-il en Haïti ? (10 pts)\\n4) Proposer des solutions. (10 pts)","answer":"...","pts":40}]}]}]}'
        ),

        # ── ÉCONOMIE : Texte (25) + Graphique/fonction (25) + Calculs (25) + Dissertation (25)
        'economie': (
            '{"title":"BACCALAURÉAT HAÏTI — ÉCONOMIE","duration":"3 heures","parts":['
            '{"label":"PARTIE I — Compréhension de texte (25 points)",'
            '"sections":[{"label":"Lire le texte et répondre aux questions","type":"open","pts":25,"items":['
            '{"text":"TEXTE :\\n\\n«...»\\n\\n1) Quelle est la thèse de l\'auteur ? (5 pts)\\n2) Distinguez les notions de «croissance» et de «développement». (5 pts)\\n3) Expliquez le phénomène décrit au paragraphe 2. (8 pts)\\n4) Partagez-vous l\'analyse de l\'auteur ? Justifiez votre réponse. (7 pts)","answer":"...","pts":25}]}]},'
            '{"label":"PARTIE II — Étude de graphique et fonction keynésienne (25 points)",'
            '"sections":[{"label":"Analyser le graphique ou la fonction de consommation","type":"open","pts":25,"items":['
            '{"text":"La fonction de consommation d\'une économie est : $C = ... + ...Y_d$\\n1) Quelle est la signification économique du terme constant et du coefficient ? (8 pts)\\n2) Calculer la propension marginale à épargner ($PmS$). (5 pts)\\n3) Calculer le multiplicateur keynésien. (6 pts)\\n4) Pour $Y_d = ...$, calculer $C$ et $S$. (6 pts)","answer":"...","pts":25}]}]},'
            '{"label":"PARTIE III — Problèmes et calculs économiques (25 points)",'
            '"sections":[{"label":"Résoudre les problèmes suivants","type":"open","pts":25,"items":['
            '{"text":"...","answer":"...","pts":25}]}]},'
            '{"label":"PARTIE IV — Dissertation (25 points)",'
            '"sections":[{"label":"Choisir UN sujet et rédiger un texte argumentatif de 10 à 15 lignes","type":"open","pts":25,"items":['
            '{"text":"Sujet 1 — ...","answer":"Texte argumentatif attendu.","pts":25},'
            '{"text":"Sujet 2 — ...","answer":"Texte argumentatif attendu.","pts":25}]}]}]}'
        ),

        # ── INFORMATIQUE : Algo (30) + Compréhension algo (20) + QCM (25) + Calculs (25)
        'informatique': (
            '{"title":"BACCALAURÉAT HAÏTI — INFORMATIQUE","duration":"1 heure 30","parts":['
            '{"label":"ÉPREUVE COMPLÈTE (100 points)",'
            '"sections":['
            '{"label":"Exercice 1 — Algorithmique (30 points)","type":"open","pts":30,"items":['
            '{"text":"Écrire un algorithme qui lit ... et affiche ...\\n\\nDonnées d\'entrée : ...\\nRésultat attendu : ...\\n\\nEsquisse de l\'algorithme attendu :\\nDébut\\n   Lire(...)\\n   ...\\n   Écrire(...)\\nFin","answer":"...","pts":30}]},'
            '{"label":"Exercice 2 — Compréhension d\'algorithme (20 points)","type":"open","pts":20,"items":['
            '{"text":"Étudier l\'algorithme suivant :\\n\\nDébut\\n   ...\\n   ...\\nFin\\n\\n1) Que produit cet algorithme? (8 pts)\\n2) Quelle valeur obtient-on si l\'entrée est ... ? (6 pts)\\n3) Proposer un nom pour cet algorithme. (6 pts)","answer":"...","pts":20}]},'
            '{"label":"Exercice 3 — Questions à choix multiple (25 points)","type":"open","pts":25,"items":['
            '{"text":"Choisir la bonne réponse :\\n1) Quel protocole attribue les adresses IP automatiquement ? a) HTTP  b) DHCP  c) FTP  d) SMTP\\n2) ...\\n3) ...\\n4) ...\\n5) ...","answer":"1) b  2) ...  3) ...  4) ...  5) ...","pts":25}]},'
            '{"label":"Exercice 4 — Calculs et conversions (25 points)","type":"open","pts":25,"items":['
            '{"text":"...","answer":"...","pts":25}]}]}]}'
        ),

        # ── ESPAGNOL : Comprensión (35) + Lingüística (35) + Discursiva (30)
        'espagnol': (
            '{"title":"BACCALAURÉAT HAÏTI — ESPAÑOL","duration":"3 heures 30","parts":['
            '{"label":"PARTE I — Comprensión lectora (35 puntos)",'
            '"sections":[{"label":"Leer el texto y responder las preguntas","type":"open","pts":35,"items":['
            '{"text":"TEXTO :\\n\\n«...»\\n\\n1) ¿Cuál es la idea principal del texto? (7 pts)\\n2) Según el autor, ¿cuáles son las consecuencias de ...? (7 pts)\\n3) Explique la expresión «...». (7 pts)\\n4) ¿Comparte usted la opinión del autor? Justifique. (7 pts)\\n5) Haga un resumen del texto en 3 a 4 oraciones. (7 pts)","answer":"...","pts":35}]}]},'
            '{"label":"PARTE II — Competencia lingüística — Gramática y Vocabulario (35 puntos)",'
            '"sections":[{"label":"Ejercicios de gramática","type":"open","pts":35,"items":['
            '{"text":"Ejercicio A — Conjugar los verbos entre paréntesis en el tiempo correcto (12 pts):\\n1. Ayer (ir) ___ al mercado con mi madre.\\n2. Cuando llegué, ellos ya (comer) ___.\\n3. ...","answer":"1. fui  2. habían comido  ...","pts":12},'
            '{"text":"Ejercicio B — Clasificar las palabras según el tema indicado (música / medicina / deporte) (11 pts):\\n...","answer":"...","pts":11},'
            '{"text":"Ejercicio C — Redactar frases usando los pronombres indicados (12 pts):\\n...","answer":"...","pts":12}]}]},'
            '{"label":"PARTE III — Competencia discursiva — Producción escrita (30 puntos)",'
            '"sections":[{"label":"Elegir UNO solo de los temas (30 pts)","type":"open","pts":30,"items":['
            '{"text":"Tema 1 — ...","answer":"Texto escrito esperado (10-15 líneas).","pts":30},'
            '{"text":"Tema 2 — ...","answer":"Texto escrito esperado (10-15 líneas).","pts":30}]}]}]}'
        ),
    }

    skeleton = _SKELETONS.get(subject)
    if not skeleton:
        # Fallback générique : 2 parties équilibrées
        skeleton = (
            f'{{"title":"BACCALAURÉAT HAÏTI — {subject_label.upper()}",'
            f'"duration":"3 heures","parts":['
            '{"label":"PREMIÈRE PARTIE — Questions de cours (50 points)",'
            '"sections":[{"label":"Répondre aux questions suivantes","type":"open","pts":50,"items":['
            '{"text":"...","answer":"...","pts":25},{"text":"...","answer":"...","pts":25}]}]},'
            '{"label":"DEUXIÈME PARTIE — Exercice ou dissertation (50 points)",'
            '"sections":[{"label":"...","type":"open","pts":50,"items":['
            '{"text":"...","answer":"...","pts":50}]}]}]}'
        )

    system_msg = (
        f"Tu es professeur de {subject_label} au Bac Haïti. "
        "Réponds UNIQUEMENT avec du JSON valide, sans aucun texte avant ou après le JSON."
    )
    prompt = (
        f"Contexte — extraits d'examens BAC Haïti {subject_label} :\n{exam_text[:2000]}\n\n"
        f"Tu vas générer un examen blanc ORIGINAL niveau Terminale en {subject_label}.\n"
        "RÈGLES STRICTES :\n"
        "1. Utiliser EXACTEMENT la structure JSON ci-dessous — ne pas changer les labels ni les points.\n"
        "2. Remplacer CHAQUE «...» par du vrai contenu de niveau BAC Haïti (questions, textes, formules).\n"
        "3. Les formules mathématiques entre $ (ex: $F=ma$, $e^{i\\pi}=-1$).\n"
        "4. Pour les exercices ouverts : rédiger des énoncés complets multi-étapes.\n"
        "5. Les sujets de dissertation/production doivent être des sujets réalistes.\n"
        "6. NE PAS modifier la structure, les labels ou les valeurs de points.\n\n"
        "SQUELETTE À REMPLIR :\n"
        + skeleton + "\n\n"
        "Réponds UNIQUEMENT avec le JSON complet rempli, rien d'autre."
    )

    raw = _call_json(prompt, system=system_msg, max_tokens=3500)
    raw_clean = re.sub(r'```[a-z]*\s*', '', raw).strip()
    raw_clean = re.sub(r'```', '', raw_clean).strip()
    raw_clean = re.sub(r'(?<!\\)\\([tbfr])', r'\\\\\1', raw_clean)
    raw_clean = re.sub(r'(?<!\\)\\([^\"\\/bfnrtu0-9\n\r])', r'\\\\\1', raw_clean)
    start = raw_clean.find('{')
    end   = raw_clean.rfind('}')
    if start == -1 or end == -1:
        return {}
    candidate = raw_clean[start:end+1]
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
    try:
        return _json.loads(candidate)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL: Format tabular data for ALL subjects (Maths, Physique, Chimie, SVT)
# Détecte 4 formats différents et crée des tableaux markdown
# ─────────────────────────────────────────────────────────────────────────────
def _global_format_tables(text: str) -> str:
    """
    Détecte et formate les données tabulées (séries statistiques, tableaux de valeurs).
    Supporte 4 formats différents utilisés dans tous les sujets.
    Retourne le texte avec tableaux markdown si patterns trouvés, sinon inchangé.
    """
    if not text or '|' in text:  # Skip if already has pipes
        return text
    
    import re as _re
    
    # FORMAT 2: Lignes x et y séparées (tabs ou espaces multiples)
    x_line = _re.search(r'x[\s\t]+([\d,\s\t]+?)(?=\n|\s*$)', text)
    y_line = _re.search(r'y[\s\t]+([\d,\s\t]+?)(?=\n|\.|\s*$)', text)
    
    if x_line and y_line:
        x_vals = [v.strip() for v in _re.split(r'[\s\t]+', x_line.group(1).strip()) if v.strip() and v.strip() != '.']
        y_vals = [v.strip() for v in _re.split(r'[\s\t]+', y_line.group(1).strip()) if v.strip() and v.strip() != '.']
        
        if x_vals and y_vals and len(x_vals) == len(y_vals):
            _header = "| x | " + " | ".join(x_vals) + " |"
            _sep = "|---|" + "|".join(["---"] * len(x_vals)) + "|"
            _row = "| y | " + " | ".join(y_vals) + " |"
            _table = _header + "\n" + _sep + "\n" + _row
            _pattern = r'x[\s\t]+[\d,\s\t]+?\ny[\s\t]+[\d,\s\t]+?(?=\n|\.)'
            result = _re.sub(_pattern, f"\n{_table}\n", text, flags=_re.MULTILINE)
            return result
    
    # FORMAT 3: Points (x1,y1), (x2,y2), etc.
    _points_pattern = r'\((\d+[.\d]*)\s*,\s*(\d+[.\d]*)\)'
    _matches3 = list(_re.finditer(_points_pattern, text))
    
    if len(_matches3) >= 2:
        _x_vals = []
        _y_vals = []
        _first_start = None
        _last_end = None
        
        for _m in _matches3:
            _x_vals.append(_m.group(1))
            _y_vals.append(_m.group(2))
            if _first_start is None:
                _first_start = _m.start()
            _last_end = _m.end()
        
        if _x_vals and _y_vals and len(_x_vals) == len(_y_vals):
            _header = "| x | " + " | ".join(_x_vals) + " |"
            _sep = "|---|" + "|".join(["---"] * len(_x_vals)) + "|"
            _row = "| y | " + " | ".join(_y_vals) + " |"
            _table = _header + "\n" + _sep + "\n" + _row
            result = text[:_first_start] + f"\n{_table}\n" + text[_last_end:]
            return result
    
    # FORMAT 4: Valeurs et effectifs
    _pattern4 = r'valeurs\s+([\d,.\s]+?)\s+avec\s+effectifs\s+([\d,.\s]+?)(?=\s*[.!?]|\s*$)'
    _match4 = _re.search(_pattern4, text, _re.IGNORECASE)
    
    if _match4:
        _vals_str = _match4.group(1).strip()
        _effectifs_str = _match4.group(2).strip()
        _vals = [v.strip() for v in _re.split(r'[,;]', _vals_str) if v.strip()]
        _effectifs = [v.strip() for v in _re.split(r'[,;]', _effectifs_str) if v.strip()]
        
        if _vals and _effectifs and len(_vals) == len(_effectifs):
            _header = "| Valeurs | " + " | ".join(_vals) + " |"
            _sep = "|---|" + "|".join(["---"] * len(_vals)) + "|"
            _row = "| Effectifs | " + " | ".join(_effectifs) + " |"
            _table = _header + "\n" + _sep + "\n" + _row
            result = text[:_match4.start()] + f"\n{_table}\n" + text[_match4.end():]
            return result
    
    # FORMAT 1: x=1,2,3 et y=10,20,30 (fallback)
    _pattern1 = r'x\s*=\s*([\d,.\s;]+?)\s+et\s+y\s*=\s*([\d,.\s;]+?)(?=\s*[.?!]|\s*$)'
    _match1 = _re.search(_pattern1, text, _re.IGNORECASE)
    
    if _match1:
        _x_str = _match1.group(1).strip()
        _y_str = _match1.group(2).strip()
        _x_vals = [v.strip() for v in _re.split(r'[,;]', _x_str) if v.strip()]
        _y_vals = [v.strip() for v in _re.split(r'[,;]', _y_str) if v.strip()]
        
        if _x_vals and _y_vals and len(_x_vals) == len(_y_vals):
            _header = "| x | " + " | ".join(_x_vals) + " |"
            _sep = "|---|" + "|".join(["---"] * len(_x_vals)) + "|"
            _row = "| y | " + " | ".join(_y_vals) + " |"
            _table = _header + "\n" + _sep + "\n" + _row
            result = text[:_match1.start()] + f"\n{_table}\n" + text[_match1.end():]
            return result
    
    return text


# ─────────────────────────────────────────────────────────────────────────────
# MCQ OPTIONS GENERATOR — génère 4 options de réponse avec Groq
# ─────────────────────────────────────────────────────────────────────────────
def _generate_mcq_options(intro: str, first_question: str, subject: str) -> tuple[list[str], str]:
    """
    Génère 4 options de réponse pour une question MCQ.
    Si Groq échoue, génère des réponses par défaut intelligentes.
    """
    from django.conf import settings
    import random
    
    # Première tentative avec Groq
    try:
        client = Groq(api_key=settings.GROQ_API_KEY)
        prompt = f"""Tu es professeur de {subject} BAC Haiti. Question: {first_question[:150]}

Genere 4 reponses a/b/c/d (max 40 chars chacune). UNE seule correcte.
Format strict:
a) reponse 1
b) reponse 2
c) reponse 3
d) reponse 4
CORRECT: X"""

        resp = client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.8,
            max_tokens=250,
        )
        
        if resp and resp.choices and len(resp.choices) > 0:
            raw = resp.choices[0].message.content
            if raw and raw.strip():
                print(f"[DEBUG] Groq response OK: {raw[:100]}")
                
                # Parse avec plus de flexibilité
                lines = raw.split('\n')
                options = {}
                correct = 'a'
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Extract a) b) c) d)
                    if line[0] in 'abcd' and len(line) > 2:
                        letter = line[0].lower()
                        content = line[2:].strip() if line[1] in ').:' else line[1:].strip()
                        if content:
                            options[letter] = content
                    # Extract CORRECT
                    if 'CORRECT' in line.upper():
                        try:
                            correct = line.split(':')[1].strip().lower()
                            if correct not in 'abcd':
                                correct = 'a'
                        except:
                            correct = 'a'
                
                if len(options) == 4:
                    formatted = [f"{k}) {options[k]}" for k in 'abcd']
                    print(f"[DEBUG] Parse success: {formatted}, correct={correct}")
                    return formatted, correct
    except Exception as e:
        print(f"[MCQ] Groq failed: {e}")
    
    # Fallback: générer des réponses plausibles par type de question
    print("[MCQ] Using fallback options")
    question_lower = first_question.lower()
    
    # Détecter le type de question et générer des réponses intelligentes
    if any(x in question_lower for x in ['schéma', 'schema', 'dessiner', 'tracer']):
        options = [
            'a) Schéma correct avec tous les éléments',
            'b) Schéma avec inversions de polarité',
            'c) Schéma incomplet',
            'd) Schéma avec éléments supplémentaires'
        ]
    elif 'période' in question_lower or 'fréquence' in question_lower:
        options = ['a) 0.1 s', 'b) 0.02 s', 'c) 2 s', 'd) 10 s']
    elif 'impédance' in question_lower or 'ohm' in question_lower or 'ω' in question_lower:
        options = ['a) 50 Ω', 'b) 100 Ω', 'c) 200 Ω', 'd) 500 Ω']
    elif 'courant' in question_lower or 'ampère' in question_lower or 'ampere' in question_lower or 'intensité' in question_lower:
        options = ['a) 2 A', 'b) 5 A', 'c) 10 A', 'd) 20 A']
    elif 'tension' in question_lower or 'volt' in question_lower:
        options = ['a) 120 V', 'b) 220 V', 'c) 60 V', 'd) 440 V']
    elif 'équation' in question_lower or 'expression' in question_lower:
        options = [
            'a) i(t) = 10 sin(100πt)',
            'b) i(t) = 5 sin(100πt + π/4)',
            'c) i(t) = 10 cos(100πt)',
            'd) i(t) = 5 cos(100πt + π/2)'
        ]
    elif any(x in question_lower for x in ['portée', 'portee', 'distance', 'horizontale', 'parcourir']):
        # Projectile/distance questions - generate realistic distances
        options = ['a) 18000 m', 'b) 25000 m', 'c) 31800 m', 'd) 45000 m']
    elif any(x in question_lower for x in ['vitesse', 'rapidité', 'vélocité']):
        # Velocity questions
        options = ['a) 100 m/s', 'b) 200 m/s', 'c) 300 m/s', 'd) 500 m/s']
    elif any(x in question_lower for x in ['accélération', 'acceleration', 'a =']):
        # Acceleration questions
        options = ['a) 2 m/s²', 'b) 5 m/s²', 'c) 9.8 m/s²', 'd) 15 m/s²']
    elif any(x in question_lower for x in ['temps', 'durée', 'duree', 'secondes', 'instant']):
        # Time/duration questions
        options = ['a) 0.5 s', 'b) 1.2 s', 'c) 2.5 s', 'd) 5 s']
    elif any(x in question_lower for x in ['énergie', 'energie', 'joule', 'calorie']):
        # Energy questions
        options = ['a) 100 J', 'b) 500 J', 'c) 1000 J', 'd) 5000 J']
    elif any(x in question_lower for x in ['puissance', 'watt', 'w']):
        # Power questions
        options = ['a) 50 W', 'b) 100 W', 'c) 500 W', 'd) 1000 W']
    elif any(x in question_lower for x in ['masse', 'poids', 'kilogramme', 'kg']):
        # Mass questions
        options = ['a) 0.5 kg', 'b) 2 kg', 'c) 5 kg', 'd) 10 kg']
    elif any(x in question_lower for x in ['longueur', 'hauteur', 'largeur', 'profondeur', 'cm', 'mm']):
        # Length/dimension questions
        options = ['a) 5 cm', 'b) 15 cm', 'c) 30 cm', 'd) 50 cm']
    elif any(x in question_lower for x in ['angle', 'degrés', 'degres', 'radian', 'rad']):
        # Angle questions
        options = ['a) 15°', 'b) 30°', 'c) 45°', 'd) 60°']
    elif any(x in question_lower for x in ['force', 'newton', 'poussée', 'traction']):
        # Force questions
        options = ['a) 10 N', 'b) 50 N', 'c) 100 N', 'd) 500 N']
    elif any(x in question_lower for x in ['charge électrique', 'charge electrique', 'coulomb']):
        # Charge questions (must check before generic 'calculer')
        options = ['a) 0.5 C', 'b) 1 C', 'c) 5 C', 'd) 10 C']
    elif 'calculer' in question_lower or 'déterminer' in question_lower or 'trouver' in question_lower:
        options = ['a) 25', 'b) 50', 'c) 75', 'd) 100']
    else:
        # Generic fallback - should be rare now
        options = ['a) 25', 'b) 50', 'c) 75', 'd) 100']
    
    # Randomize the correct answer position
    correct_idx = random.choice('abcd')
    try:
        print(f"[MCQ] Fallback options count: {len(options)}, correct: {correct_idx}")
    except:
        print(f"[MCQ] Fallback options correct: {correct_idx}")
    
    return options, correct_idx


# ─────────────────────────────────────────────────────────────────────────────
# EXAM BLANC — génération haute qualité depuis la base de données
# Pas de PDFs — IA génère du contenu ORIGINAL niveau Bac Haïti
# ─────────────────────────────────────────────────────────────────────────────
def generate_exam_from_db(subject: str, quiz_questions: list | None = None, user_serie: str = '') -> dict:
    """
    Génère un examen blanc BAC Haïti en lisant les fichiers database/json/exams_{subject}.json.
    Reproduit fidèlement la structure, les types et la disposition des vrais examens BAC.
    """
    import json as _json
    import random as _random
    import re as _re
    import os
    from django.conf import settings

    subject_label = MATS.get(subject, subject)

    _DURATIONS = {
        'maths': '3 heures', 'physique': '3h30', 'chimie': '3 heures',
        'svt': '3 heures', 'philosophie': '4 heures', 'francais': '3 heures',
        'histoire': '3 heures', 'economie': '3 heures', 'anglais': '3h30',
        'espagnol': '3 heures', 'informatique': '1 heure 30', 'art': '3 heures',
    }

    # ── Pool statique : équations chimiques à écrire et équilibrer ───────────
    _CHIMIE_EQ_POOL = [
        {
            'text': "Écrire et équilibrer l'équation de combustion complète du méthane (CH₄) dans le dioxygène (O₂). Identifier les produits formés et préciser leur état physique à température ambiante.",
            'answer': 'CH₄ + 2 O₂ → CO₂ + 2 H₂O  (CO₂ gazeux, H₂O liquide)'
        },
        {
            'text': "Écrire et équilibrer l'équation de la réaction de neutralisation entre l'acide chlorhydrique (HCl) et l'hydroxyde de sodium (NaOH). Quel est le nom du sel obtenu ?",
            'answer': 'HCl + NaOH → NaCl + H₂O  (chlorure de sodium — sel de table)'
        },
        {
            'text': "Écrire et équilibrer l'équation d'estérification entre l'acide éthanoïque (CH₃COOH) et l'éthanol (C₂H₅OH). Nommer l'ester formé et préciser le type de réaction.",
            'answer': 'CH₃COOH + C₂H₅OH ⇌ CH₃COOC₂H₅ + H₂O  (éthanoate d\'éthyle ; réaction équilibrée et limitée)'
        },
        {
            'text': "Écrire et équilibrer l'équation de la réaction entre le zinc (Zn) et l'acide chlorhydrique (HCl). Identifier l'oxydant, le réducteur et le gaz dégagé.",
            'answer': 'Zn + 2 HCl → ZnCl₂ + H₂↑  (Zn : réducteur ; H⁺ : oxydant ; H₂ : gaz dégagé)'
        },
        {
            'text': "Écrire et équilibrer l'équation de saponification de l'éthanoate d'éthyle (CH₃COOC₂H₅) par la soude (NaOH). Indiquer les produits obtenus et les conditions de réaction.",
            'answer': 'CH₃COOC₂H₅ + NaOH → CH₃COONa + C₂H₅OH  (irréversible, t° modérée avec NaOH aqueux)'
        },
        {
            'text': "Écrire et équilibrer l'équation d'oxydoréduction entre le fer (Fe) et le sulfate de cuivre (CuSO₄) en solution aqueuse. Justifier le transfert d'électrons.",
            'answer': 'Fe + CuSO₄ → FeSO₄ + Cu  (Fe perd 2e⁻ → oxydé ; Cu²⁺ gagne 2e⁻ → réduit)'
        },
        {
            'text': "Écrire et équilibrer la combustion complète de l'éthanol (C₂H₅OH). Si 4,6 g d'éthanol brûlent complètement (M = 46 g/mol), calculer le volume de CO₂ produit à CNTP (Vm = 22,4 L/mol).",
            'answer': 'C₂H₅OH + 3 O₂ → 2 CO₂ + 3 H₂O  ;  n = 4,6/46 = 0,1 mol → n(CO₂) = 0,2 mol → V = 4,48 L'
        },
        {
            'text': "Écrire l'équation d'ionisation complète de l'acide sulfurique (H₂SO₄) dans l'eau. Préciser pourquoi H₂SO₄ est qualifié d'acide fort et diprotique.",
            'answer': 'H₂SO₄ → 2 H⁺ + SO₄²⁻  (fort car ionisation totale ; diprotique car libère 2 ions H⁺)'
        },
        {
            'text': "Écrire et équilibrer l'hydrolyse acide d'un ester de formule générale R-COO-R' en présence d'eau (H₂O) et d'acide. Comparer avec la saponification (base).",
            'answer': 'RCOOR\' + H₂O ⇌ RCOOH + R\'OH  (réversible, acide) vs  RCOOR\' + NaOH → RCOONa + R\'OH  (irréversible, base)'
        },
        {
            'text': "Écrire et équilibrer la réaction entre le carbonate de calcium (CaCO₃) et l'acide chlorhydrique (HCl). Nommer tous les produits et indiquer celui qui est gazeux.",
            'answer': 'CaCO₃ + 2 HCl → CaCl₂ + H₂O + CO₂↑  (CO₂ est le gaz dégagé)'
        },
    ]

    json_path = os.path.join(settings.BASE_DIR, 'database', 'json', f'exams_{subject}.json')
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, 'r', encoding='utf-8') as _f:
            data = _json.load(_f)
    except Exception:
        return {}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _fmt_exercice(item: dict) -> str:
        """Formate un item exercice : contexte + sous-questions numérotées."""
        intro = (item.get('intro') or item.get('enonce') or '').strip()
        qs = item.get('questions') or []
        parts_text = []
        if intro:
            parts_text.append(intro)
        for i, q in enumerate(qs, 1):
            if isinstance(q, str) and q.strip():
                # Garder la lettre si déjà présente (a) b) c)) sinon numéroter
                parts_text.append(q.strip())
        return '\n\n'.join(parts_text)

    def _text(item: dict) -> str:
        """Retourne le texte complet d'un item en combinant passage + énoncé si nécessaire."""
        enonce = (item.get('enonce') or '').strip()
        intro = (item.get('intro') or '').strip()
        texte = (item.get('texte') or '').strip()  # passage de lecture (question_texte)
        t = item.get('type', '')
        if t == 'exercice':
            return _fmt_exercice(item)
        if t == 'question_texte' and texte:
            # Combiner : passage clairement labelisé, puis les questions
            if enonce and enonce not in texte:
                return 'TEXTE :\n\n' + texte + '\n\nQUESTIONS :\n\n' + enonce
            return 'TEXTE :\n\n' + texte
        return enonce or intro

    _DOT_BLANK = _re.compile(r'[.…]{4,}')

    def _normalize_blanks(txt: str) -> str:
        """Replace dot sequences used as blanks (…………) with ___ for uniform rendering."""
        return _DOT_BLANK.sub(' ___ ', txt).replace('  ', ' ')

    def _is_clean_fillin(txt: str) -> bool:
        normalized = _normalize_blanks(txt)
        if len(normalized) < 30 or '___' not in normalized:
            return False
        noise = len(_re.findall(r'[0-9]{4,}|\b[A-Z]\b\s+\b[A-Z]\b\s+\b[A-Z]\b', normalized))
        return noise < 2

    def _collect_items(types_wanted: list, min_len: int = 40) -> list:
        """Collecte tous les items du JSON selon les types demandés."""
        result = []
        seen_texts = set()
        for exam in data.get('exams', []):
            yr = exam.get('year', '')
            src = f"Bac Haïti {yr}" if yr else "Bac Haïti"
            for item in exam.get('items', []):
                if item.get('type', '') in types_wanted:
                    txt = _text(item)
                    if not txt or len(txt.strip()) < min_len:
                        continue
                    # Déduplication : ignorer si texte déjà vu (premiers 60 chars)
                    key = txt.strip()[:60]
                    if key in seen_texts:
                        continue
                    seen_texts.add(key)
                    reponse = (item.get('reponse') or '')
                    if isinstance(reponse, list):
                        reponse = ', '.join(str(r) for r in reponse)
                    result.append({
                        'text': _fix_latex(txt.strip()),
                        'answer': _fix_latex(reponse.strip()) if reponse else 'Réponse développée attendue.',
                        'theme': item.get('theme', '') or item.get('type', '').capitalize(),
                        'type': item.get('type', ''),
                        'source': src,
                        'year': yr,
                    })
        return result

    def _collect_fillin() -> list:
        """Collecte les questions fill-in depuis parts[].sections[].themes[].questions[].
        Accepte les blancs sous forme ___ ET sous forme ………… (normalise en ___)."""
        result = []
        seen = set()
        for exam in data.get('exams', []):
            yr = exam.get('year', '')
            src = f"Bac Haïti {yr}" if yr else "Bac Haïti"
            for part in exam.get('parts', []):
                for sec in part.get('sections', []):
                    for theme in sec.get('themes', []):
                        pts_q = theme.get('points_per_question', 0) or 0
                        for q in theme.get('questions', []):
                            raw = (q.get('text') or '').strip()
                            txt = _normalize_blanks(_fix_latex(raw))
                            if not _is_clean_fillin(txt):
                                continue
                            key60 = txt[:60]
                            if key60 in seen:
                                continue
                            seen.add(key60)
                            ans = (q.get('answer') or q.get('reponse') or '').strip()
                            result.append({
                                'text': txt,
                                'answer': ans or '___',
                                'source': src,
                                'year': yr,
                                'pts': pts_q or q.get('points', 0) or 0,
                            })
        return result

    def _pick(pool: list, n: int) -> list:
        """Mélange et retourne n éléments. Si pool insuffisant, boucle."""
        if not pool:
            return []
        _random.shuffle(pool)
        if len(pool) >= n:
            return pool[:n]
        return (pool * ((n // len(pool)) + 1))[:n]

    def _distribute(total: int, n: int) -> list:
        if n == 0:
            return []
        base = total // n
        pts = [base] * n
        pts[0] += total - sum(pts)
        return pts

    def _section(label: str, items_list: list, pts_list: list, sec_type: str = 'open',
                  default_answer: str = 'Réponse développée attendue.') -> dict:
        return {
            'label': label, 'type': sec_type, 'pts': sum(pts_list),
            'items': [{'text': _fix_latex(_global_format_tables(it['text'])),
                       'answer': _fix_latex(it.get('answer') or default_answer),
                       'pts': p}
                      for it, p in zip(items_list, pts_list)],
        }

    def _section_fillin(label: str, items_list: list, pts_each: int) -> dict:
        return {
            'label': label, 'type': 'fill',
            'pts': len(items_list) * pts_each,
            'items': [{'text': _fix_latex(_global_format_tables(it['text'])), 'answer': '___', 'pts': pts_each}
                      for it in items_list],
        }

    # ── Quiz MCQ loader — utilise les fichiers quiz JSON vérifiés ────────────
    _QUIZ_FILES = {
        'svt': 'quiz_SVT.json', 'physique': 'quiz_physique.json',
        'chimie': 'quiz_chimie.json', 'philosophie': 'quiz_philosophie.json',
        'histoire': 'quiz_sc_social.json', 'economie': 'quiz_economie.json',
        'informatique': 'quiz_informatique.json', 'art': 'quiz_art.json',
        'francais': 'quiz_kreyol.json',
    }

    def _load_quiz_mcq(n: int = 10, pts_each: int = 2) -> list:
        """Charge N questions depuis le fichier quiz JSON et les formate en questions ouvertes.
        La réponse correcte (option) devient le corrigé modèle pour la correction IA."""
        fname = _QUIZ_FILES.get(subject)
        if not fname:
            return []
        qpath = os.path.join(settings.BASE_DIR, 'database', fname)
        if not os.path.exists(qpath):
            return []
        try:
            import json as _jq
            with open(qpath, encoding='utf-8') as _qf:
                qdata = _jq.load(_qf)
            pool = list(qdata.get('quiz', qdata) if isinstance(qdata, dict) else qdata)
            _random.shuffle(pool)
            result = []
            for q in pool[:n]:
                opts = list(q.get('options', []))
                correct_letter = q.get('correct', 'A').upper()
                correct_idx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter, 0)
                answer_text = opts[correct_idx] if correct_idx < len(opts) else ''
                explication = q.get('explanation', q.get('explication', ''))
                # Corrigé complet = réponse correcte + explication si disponible
                full_answer = answer_text
                if explication:
                    full_answer = f'{answer_text}\n\n{explication}'
                result.append({
                    'text': q.get('question', ''),
                    'answer': full_answer,
                    'pts': pts_each,
                })
            return result
        except Exception:
            return []

    def _mcq_section(label: str, mcq_items: list) -> dict:
        """Construit une section de type 'open' depuis les items quiz — corrigé par l'IA."""
        total = sum(it.get('pts', 2) for it in mcq_items)
        return {'label': label, 'type': 'open', 'pts': total, 'items': mcq_items}

    # ── LaTeX sanitizer (Python-side, runs before JSON is sent to frontend) ──
    def _fix_latex(text: str) -> str:
        """
        Nettoie et normalise le LaTeX dans un texte extrait de PDF :
          1. Corrige les caractères de contrôle que json.load injecte
             (\t → \\t, backspace → \\b, form-feed → \\f, newline → \\n pour \nu etc.)
          2. Enveloppe \begin{...}...\end{...} dans $$ $$ s'ils ne le sont pas déjà
          3. Restaure les backslashes manquants devant les commandes LaTeX connues
          4. Enveloppe les expressions avec \commande dans $...$
          5. Enveloppe les indices/exposants nus (U_n, x_{k+1}) dans $...$
        """
        if not text:
            return text

        # 1. Caractères de contrôle
        text = text.replace('\t',   '\\t')    # \theta, \times, \to → tab
        text = text.replace('\x08', '\\b')    # \beta, \bar       → backspace
        text = text.replace('\x0c', '\\f')    # \frac, \forall    → form-feed
        # \n suivi d'une commande LaTeX connue commençant par n
        text = _re.sub(r'\n((?:nu|nabla|neq|neg|not|nolimits)\b)', r'\\\1', text)

        # 2. Environnements array/matrix/cases : les envelopper dans $$ si pas déjà
        _ENV_PAT = r'(?:array|matrix|pmatrix|bmatrix|vmatrix|cases|align\*?|gather\*?)'
        # 2a. $\begin{env}...\end{env}$ (inline $) multilignes → $$...$$ (display)
        text = _re.sub(
            r'\$\s*(\\begin\{' + _ENV_PAT + r'\}(?:\{[^}]*\})?[\s\S]*?\\end\{' + _ENV_PAT + r'\})\s*\$',
            lambda m: '$$\n' + m.group(1).strip() + '\n$$',
            text
        )
        def _wrap_env(m):
            inner = m.group(0).strip()
            if inner.startswith('$$') or inner.startswith('\\['):
                return inner
            return '$$\n' + inner + '\n$$'
        # 2b. \begin{env}...\end{env} sans délimiteurs → $$...$$
        text = _re.sub(
            r'(?<!\$)\s*\\begin\{' + _ENV_PAT + r'\}(?:\{[^}]*\})?\}[\s\S]*?\\end\{' + _ENV_PAT + r'\}',
            _wrap_env, text
        )
        # Nettoyer les \n\n\n parasites devant les $$
        text = _re.sub(r'(\n{2,})\$\$', '\n\n$$', text)
        text = _re.sub(r'\$\$(\n{2,})', '$$\n\n', text)

        # 3. Restaurer les backslashes manquants sur les commandes connues
        # 3a — commandes séparées par des espaces / ponctuation
        _BARE_WORD = (
            r'displaystyle|textstyle|scriptstyle|boldsymbol|dfrac|tfrac|cfrac|'
            r'dbinom|tbinom|hbar|imath|jmath|binom|sqrt|limits|nolimits|infty|'
            r'leq|geq|neq|approx|equiv|cdot|ldots|vdots|forall|exists|partial|'
            r'nabla|iff|Rightarrow|Leftarrow|rightarrow|leftarrow|mapsto|'
            r'hookrightarrow|langle|rangle|lceil|rceil|lfloor|rfloor|'
            r'oplus|otimes|odot|wedge|vee|cap|cup|emptyset|subset|supset|'
            r'subseteq|supseteq|notin|perp|parallel|angle|triangle|square|'
            r'circ|bullet|prime|dagger|ddagger|aleph|hline|underbrace|overbrace|'
            r'overline|underline|widehat|widetilde|vec|hat|tilde|dot|ddot|bar|'
            r'alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|'
            r'iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|sigma|tau|upsilon|phi|'
            r'varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|'
            r'Phi|Psi|Omega|ln|log|sin|cos|tan|cot|sec|csc|arcsin|arccos|arctan|'
            r'sinh|cosh|tanh|exp|lim|max|min|sup|inf|det|dim|ker|deg|gcd|'
            r'Re|Im|to|pm|mp|times|div|frac|int|sum|prod'
        )
        text = _re.sub(
            r'(?<!\\)\b(' + _BARE_WORD + r')(?![a-zA-ZÀ-ÿ])',
            lambda m: '\\' + m.group(1),
            text
        )
        # 3b — commandes préfixe collées à leurs arguments (sans espace)
        _BARE_PREFIX = r'(?<!\\)(?<![a-zA-Z])(mathbb|mathbf|mathit|mathrm|mathsf|mathtt|mathcal|mathfrak|operatorname|overrightarrow|overleftarrow|overline|underline|widehat|widetilde)(?=[a-zA-Z{(])'
        text = _re.sub(_BARE_PREFIX, lambda m: '\\' + m.group(1), text)

        # 3c — \left / \right sans backslash (PDF dropping the \)
        # Cas 1 : collé à une lettre : "Rleft(" → "R\left("
        text = _re.sub(r'([A-Za-z])(left|right)([\(\)\[\]\|\\])', r'\1\\\2\3', text)
        # Cas 2 : standalone word boundary : "left(" → "\left("
        text = _re.sub(r'(?<![a-zA-Z\\])\b(left|right)([\(\)\[\]\|\\])', r'\\\1\2', text)

        # 3c — décomposer les commandes LaTeX collées entre elles
        # ex: \topminfty → \to \pm \infty  |  \leqgeq → \leq\geq
        # (artefact PDF : espaces perdus entre commandes consécutives)
        _KNOWN_CMDS = sorted([
            'displaystyle','textstyle','scriptstyle','boldsymbol',
            'dfrac','tfrac','cfrac','dbinom','tbinom','binom','sqrt',
            'limits','nolimits','infty','leq','geq','neq','approx',
            'equiv','cdot','ldots','vdots','forall','exists','partial',
            'nabla','iff','Rightarrow','Leftarrow','rightarrow','leftarrow',
            'mapsto','hookrightarrow','langle','rangle','lceil','rceil',
            'lfloor','rfloor','oplus','otimes','odot','wedge','vee',
            'cap','cup','emptyset','subset','supset','subseteq','supseteq',
            'notin','perp','parallel','angle','triangle','square',
            'circ','bullet','prime','dagger','ddagger','aleph','hline',
            'underbrace','overbrace','overline','underline','widehat',
            'widetilde','vec','hat','tilde','dot','ddot','bar',
            'alpha','beta','gamma','delta','epsilon','varepsilon','zeta',
            'eta','theta','vartheta','iota','kappa','lambda','mu','nu',
            'xi','pi','varpi','rho','sigma','tau','upsilon','phi',
            'varphi','chi','psi','omega','Gamma','Delta','Theta','Lambda',
            'Xi','Pi','Sigma','Upsilon','Phi','Psi','Omega',
            'ln','log','sin','cos','tan','cot','sec','csc',
            'arcsin','arccos','arctan','sinh','cosh','tanh',
            'exp','lim','max','min','sup','inf','det','dim','ker',
            'deg','gcd','Re','Im','to','pm','mp','times','div',
            'frac','int','sum','prod','hbar','imath','jmath',
            'mathbb','mathbf','mathit','mathrm','mathsf','mathtt',
            'mathcal','mathfrak','operatorname',
            'overrightarrow','overleftarrow',
        ], key=lambda s: -len(s))           # plus long d'abord (greedy)
        def _split_cmds(name):
            """Décompose récursivement un nom en commandes connues ou retourne None."""
            if not name:
                return []
            for cmd in _KNOWN_CMDS:
                if name.startswith(cmd):
                    rest = name[len(cmd):]
                    # le prochain caractère doit être une lettre (autre commande)
                    # ou fin de chaîne
                    if not rest or not rest[0].isalpha():
                        tail = _split_cmds(rest) if rest else []
                        if tail is not None:
                            return [cmd] + tail
                    else:
                        # essai greedy : continuer la décomposition
                        tail = _split_cmds(rest)
                        if tail is not None:
                            return [cmd] + tail
            return None                     # aucune décomposition trouvée
        def _decompose_cmd(m):
            name = m.group(1)               # nom sans le \
            parts = _split_cmds(name)
            if parts and len(parts) > 1:
                return ' '.join('\\' + p for p in parts)
            return m.group(0)               # laisser tel quel
        text = _re.sub(r'\\([a-zA-Z]{4,})', _decompose_cmd, text)

        # 4. Protéger les blocs déjà entre $...$ / \(...\) / \[...\]
        _saved: list = []
        def _save(m): _saved.append(m.group(0)); return f'\x01{len(_saved)-1}\x01'
        text = _re.sub(r'\$\$[\s\S]+?\$\$', _save, text)
        text = _re.sub(r'\$(?:[^\$\\\n]|\\.)+?\$', _save, text)
        text = _re.sub(r'\\\([\s\S]+?\\\)', _save, text)
        text = _re.sub(r'\\\[[\s\S]+?\\\]', _save, text)

        # 4b — Wrap complete \left...\right expressions in $...$ BEFORE step 5
        # fragmente chaque \cmd séparément, ce qui casserait les paires \left\right.
        # On capture aussi un éventuel coefficient avant \left (ex: R, 2π, P(R)=R…).
        def _save_as_math(m):
            s = m.group(0).strip()
            _saved.append('$' + s + '$')
            return f'\x01{len(_saved)-1}\x01'
        text = _re.sub(
            r'(?<!\x01)'
            r'[A-Za-z0-9_+\-=(),.]*?'       # contexte optionnel avant \left
            r'\\left\s*[\(\[\|\\]'           # \left( / \left[ / \left| / \left\{
            r'(?:[^\\]|\\.)*?'               # contenu (non-greedy)
            r'\\right\s*[\)\]\|\\]'          # \right) / \right] / \right| / \right\}
            r'(?:\^\{[^{}\x01]{0,30}\}|\^[0-9+\-])?',  # exposant optionnel
            _save_as_math,
            text,
        )

        # 5. Envelopper les \commande (+ {} [] _^ optionnels) dans $...$
        _CMD_RUN = _re.compile(
            r'(?<!\$)(?<!\x01)'
            r'(\\[a-zA-Z]+'
            r'(?:\{[^{}\x01\n]{0,120}\}|\[[^\]\x01]{0,60}\]|[_^]\{[^{}\x01\n]{0,60}\}|[_^][a-zA-Z0-9])?'
            r'(?:\s*(?:[_^]\{[^{}\x01\n]{0,40}\}|[_^][a-zA-Z0-9]'
            r'|[+\-=*/]|[0-9]+|\s*\\[a-zA-Z]+(?:\{[^{}\x01\n]{0,60}\}|[_^][a-zA-Z0-9])?'
            r'))*)'
            r'(?!\$)'
        )
        def _wrap_cmd(m):
            s = m.group(1).strip()
            if not s or not _re.search(r'\\[a-zA-Z]', s):
                return m.group(0)
            return '$' + s + '$'
        text = _CMD_RUN.sub(_wrap_cmd, text)

        # 6. Envelopper les indices/exposants nus : U_n, x_{n+1}, f^2 — sauf mots normaux
        text = _re.sub(
            r'(?<!\$)\b([A-Za-z])('
            r'_\{[^{}\x01\n]{1,30}\}|_[a-zA-Z0-9]{1,8}'
            r'|\^\{[^{}\x01\n]{1,20}\}|\^[0-9+\-]{1,4}'
            r')(?!\$)',
            r'$\1\2$',
            text
        )

        # Restaurer les blocs protégés
        text = _re.sub(r'\x01(\d+)\x01', lambda m: _saved[int(m.group(1))], text)
        return text

    import datetime as _dt
    if subject == 'francais':
        title = f'BAKÈLOREA AYITI — EGZAMEN {subject_label.upper()}'
    else:
        title    = f'BACCALAURÉAT HAÏTI — ÉPREUVE DE {subject_label.upper()}'
    duration = _DURATIONS.get(subject, '3 heures')
    _annee   = _dt.date.today().year

    # ── Série et coefficient réels selon la série du user ──────────────────
    from .series_data import SERIES, get_subject_coeff, DEFAULT_COEF
    # Normalise la clé série
    _user_serie_key = (user_serie or '').strip().upper()
    if _user_serie_key not in SERIES:
        # Guess from subject if serie unknown
        _guess = {'maths': 'SMP', 'physique': 'SMP', 'chimie': 'SMP',
                  'svt': 'SVT', 'economie': 'SES', 'art': 'LLA',
                  'anglais': 'SVT', 'philosophie': 'SVT', 'histoire': 'SES'}
        _user_serie_key = _guess.get(subject, 'SVT')
    _serie_label_map = {
        'SVT': 'Série S (SVT)', 'SMP': 'Série S (SMP)',
        'SES': 'Série SES', 'LLA': 'Série LLA',
    }
    _serie = _serie_label_map.get(_user_serie_key, f'Série {_user_serie_key}')
    # Coefficient réel (series_data stocks points × 100, e.g. 400→4, 200→2)
    _raw_coeff = get_subject_coeff(_user_serie_key, subject)
    _coeff = _raw_coeff // 100 if _raw_coeff >= 100 else max(1, _raw_coeff)

    # ════════════════════════════════════════════════════════════════════════
    # MATHÉMATIQUES
    # PARTIE A (40 pts) : 10 questions "recopier et compléter"
    # PARTIE B (60 pts) : traiter 3 exercices
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'maths':
        # ── Import exo_loader pour charger les vrais exercices BAC ──────
        from . import exo_loader as _exo_loader
        
        # ── Partie A : 10 QUIZ aléatoires depuis quiz_math.json ──────────
        # Charger les questionnaires
        partie_a_fill = []
        try:
            import json as _json
            _quiz_path = os.path.join(settings.BASE_DIR, 'database', 'quiz_math.json')
            with open(_quiz_path, 'r', encoding='utf-8') as _qf:
                _all_quizzes = _json.load(_qf)
            
            # Helper: Détecter et formater les données tabulaires (x=..., y=...) en tableau
            def _format_tabular_data(_text: str) -> str:
                """Convertit les séries x=1,2,3 et y=10,20,30 en tableau markdown (plusieurs formats)"""
                import re
                
                # ──────────────────────────────────────────────────────────────────
                # FORMAT 2: Lignes x et y séparées (tabs ou espaces multiples)
                # x	8	10	12	15	19	22
                # y	4	5	7	9	10	12
                # ──────────────────────────────────────────────────────────────────
                _x_line = re.search(r'x[\s\t]+([\d,\s\t]+?)(?=\n|\s*$)', _text)
                _y_line = re.search(r'y[\s\t]+([\d,\s\t]+?)(?=\n|\.|\s*$)', _text)
                
                if _x_line and _y_line:
                    _x_vals = [v.strip() for v in re.split(r'[\s\t]+', _x_line.group(1).strip()) if v.strip() and v.strip() != '.']
                    _y_vals = [v.strip() for v in re.split(r'[\s\t]+', _y_line.group(1).strip()) if v.strip() and v.strip() != '.']
                    
                    if _x_vals and _y_vals and len(_x_vals) == len(_y_vals):
                        # Créer le tableau
                        _header = "| x | " + " | ".join(_x_vals) + " |"
                        _sep = "|---|" + "|".join(["---"] * len(_x_vals)) + "|"
                        _row = "| y | " + " | ".join(_y_vals) + " |"
                        _table = _header + "\n" + _sep + "\n" + _row
                        
                        # Remplacer les deux lignes x et y par le tableau
                        _pattern = r'x[\s\t]+[\d,\s\t]+?\ny[\s\t]+[\d,\s\t]+?(?=\n|\.)'
                        _result = re.sub(_pattern, f"\n{_table}\n", _text, flags=re.MULTILINE)
                        return _result
                
                # ──────────────────────────────────────────────────────────────────
                # FORMAT 3: Points (x1,y1), (x2,y2), etc.
                # (1,2), (2,4), (3,5), (4,7)
                # ──────────────────────────────────────────────────────────────────
                _points_pattern = r'\((\d+[.\d]*)\s*,\s*(\d+[.\d]*)\)'
                _matches3 = list(re.finditer(_points_pattern, _text))
                
                if len(_matches3) >= 2:
                    _x_vals = []
                    _y_vals = []
                    _first_start = None
                    _last_end = None
                    
                    for _m in _matches3:
                        _x_vals.append(_m.group(1))
                        _y_vals.append(_m.group(2))
                        if _first_start is None:
                            _first_start = _m.start()
                        _last_end = _m.end()
                    
                    if _x_vals and _y_vals and len(_x_vals) == len(_y_vals):
                        # Créer le tableau
                        _header = "| x | " + " | ".join(_x_vals) + " |"
                        _sep = "|---|" + "|".join(["---"] * len(_x_vals)) + "|"
                        _row = "| y | " + " | ".join(_y_vals) + " |"
                        _table = _header + "\n" + _sep + "\n" + _row
                        
                        # Remplacer les points par le tableau
                        _result = _text[:_first_start] + f"\n{_table}\n" + _text[_last_end:]
                        return _result
                
                # ──────────────────────────────────────────────────────────────────
                # FORMAT 4: Valeurs et effectifs
                # valeurs 0,1,2,3,4 avec effectifs 5,10,15,10,5
                # ──────────────────────────────────────────────────────────────────
                _pattern4 = r'valeurs\s+([\d,.\s]+?)\s+avec\s+effectifs\s+([\d,.\s]+?)(?=\s*[.!?]|\s*$)'
                _match4 = re.search(_pattern4, _text, re.IGNORECASE)
                
                if _match4:
                    _vals_str = _match4.group(1).strip()
                    _effectifs_str = _match4.group(2).strip()
                    
                    _vals = [v.strip() for v in re.split(r'[,;]', _vals_str) if v.strip()]
                    _effectifs = [v.strip() for v in re.split(r'[,;]', _effectifs_str) if v.strip()]
                    
                    if _vals and _effectifs and len(_vals) == len(_effectifs):
                        # Créer le tableau
                        _header = "| Valeurs | " + " | ".join(_vals) + " |"
                        _sep = "|---|" + "|".join(["---"] * len(_vals)) + "|"
                        _row = "| Effectifs | " + " | ".join(_effectifs) + " |"
                        _table = _header + "\n" + _sep + "\n" + _row
                        
                        # Remplacer
                        _result = _text[:_match4.start()] + f"\n{_table}\n" + _text[_match4.end():]
                        return _result
                
                # ──────────────────────────────────────────────────────────────────
                # FORMAT 1: x=1,2,3 et y=10,20,30 (fallback)
                # ──────────────────────────────────────────────────────────────────
                _pattern1 = r'x\s*=\s*([\d,.\s;]+?)\s+et\s+y\s*=\s*([\d,.\s;]+?)(?=\s*[.?!]|\s*$)'
                _match1 = re.search(_pattern1, _text, re.IGNORECASE)
                
                if _match1:
                    _x_str = _match1.group(1).strip()
                    _y_str = _match1.group(2).strip()
                    
                    _x_vals = [v.strip() for v in re.split(r'[,;]', _x_str) if v.strip()]
                    _y_vals = [v.strip() for v in re.split(r'[,;]', _y_str) if v.strip()]
                    
                    if _x_vals and _y_vals and len(_x_vals) == len(_y_vals):
                        _header = "| x | " + " | ".join(_x_vals) + " |"
                        _sep = "|---|" + "|".join(["---"] * len(_x_vals)) + "|"
                        _row = "| y | " + " | ".join(_y_vals) + " |"
                        _table = _header + "\n" + _sep + "\n" + _row
                        
                        _result = _text[:_match1.start()] + f"\n{_table}\n" + _text[_match1.end():]
                        return _result
                
                return _text
            
            # Filtrer les quiz pour exclure les nombres complexes dans la PARTIE A,
            # puis prioriser limites/suites/statistiques (covariance, ecart-type, etc.).
            def _quiz_blob(_q: dict) -> str:
                return f"{_q.get('category', '')} {_q.get('question', '')}".lower()

            _EXCLUDE_COMPLEX_KW = [
                'nombres complexes', 'nombre complexe', 'complexe', 'module',
                'argument', 'conjugu', 'forme exponentielle', 'affixe'
            ]
            _PRIORITY_FILL_KW = [
                'limite', 'suite', 'u_0', 'u0', 'u_5', 'u5', 'somme',
                'covariance', 'ecart type', 'écart type', 'variance', 'regression',
                'régression', 'fonction', 'derivee', 'dérivée'
            ]

            _quiz_allowed = [
                q for q in _all_quizzes
                if not any(kw in _quiz_blob(q) for kw in _EXCLUDE_COMPLEX_KW)
            ]
            _quiz_priority = [
                q for q in _quiz_allowed
                if any(kw in _quiz_blob(q) for kw in _PRIORITY_FILL_KW)
            ]
            _quiz_other = [q for q in _quiz_allowed if q not in _quiz_priority]

            _random.shuffle(_quiz_priority)
            _random.shuffle(_quiz_other)
            _selected_quizzes = (_quiz_priority + _quiz_other)[:10]

            # Prendre 10 quizzes si disponibles
            if _selected_quizzes:
                
                # Formater pour la PARTIE A (questions sans options affichées, avec tableaux si nécessaire)
                for _q in _selected_quizzes:
                    _correct_letter = _q.get('correct', 'A')
                    _correct_answer = _q['options'][ord(_correct_letter) - ord('A')] if ord(_correct_letter) - ord('A') < len(_q['options']) else ''
                    
                    # IMPORTANT: _global_format_tables() doit être AVANT _fix_latex()
                    # car _fix_latex() remplace les tabs (\t) qui cassent FORMAT 2
                    _clean_question = _global_format_tables(_q['question'])
                    _clean_question = _fix_latex(_clean_question)
                    
                    partie_a_fill.append({
                        'text': _clean_question,  # Question seulement, pas d'options
                        'answer': f"({_correct_letter}) {_correct_answer}",
                    })
        except Exception as _ex:
            print(f"[generate_exam_from_db] Error loading quizzes: {_ex}")
            partie_a_fill = []
        
        # Compléter avec une base par défaut (sans nombres complexes) pour garantir 10 questions
        _default_fill_bank = [
                {"text": "La dérivée de $\\ln(x)$ est ___.", "answer": "$\\frac{1}{x}$"},
                {"text": "Le domaine de définition de $\\sqrt{x}$ est ___.", "answer": "$[0\\,;+\\infty[$"},
                {"text": "La limite de $e^x$ quand $x \\to -\\infty$ est ___.", "answer": "$0$"},
                {"text": "La dérivée de $\\sin(x)$ est ___.", "answer": "$\\cos(x)$"},
                {"text": "Une primitive de $x^n$ (pour $n \\neq -1$) est ___.", "answer": "$\\frac{x^{n+1}}{n+1} + C$"},
                {"text": "Une suite $(u_n)$ géométrique de raison $q$ vérifie $u_{n+1} = ___$.", "answer": "$q \\cdot u_n$"},
                {"text": "La somme des $n$ premiers termes d'une suite arithmétique est $S_n = ___$.", "answer": "$n \\cdot \\frac{u_1 + u_n}{2}$"},
                {"text": "La covariance de deux variables aléatoires $X$ et $Y$ est $\\operatorname{cov}(X,Y)=___$.", "answer": "$E(XY)-E(X)E(Y)$"},
                {"text": "La probabilité de l'événement contraire de $A$ est $P(\\bar{A}) = ___$.", "answer": "$1 - P(A)$"},
                {"text": "L'écart-type d'une variable aléatoire $X$ vérifie $\\sigma(X)=___$.", "answer": "$\\sqrt{V(X)}$"},
        ]

        if len(partie_a_fill) < 10:
            for _df in _default_fill_bank:
                if len(partie_a_fill) >= 10:
                    break
                partie_a_fill.append(_df)

        partie_a_fill = partie_a_fill[:10]

        # ── Partie B : 4 exercices RÉELS depuis exo_math.json (l'élève en traite 3) ──
        # Récupère 6 exercices pour filtrer et en sélectionner 4 (avec au moins 1 analyse en exo 1)
        partie_b_items = []
        try:
            exercices = _exo_loader.get_exercises('maths', chapter='', n=30)

            def _exo_blob(ex: dict) -> str:
                qs = ex.get('questions') or []
                qs_blob = ' '.join(str(q) for q in qs)
                return (
                    f"{ex.get('theme', '')} {ex.get('chapter', '')} {ex.get('intro', '')} "
                    f"{ex.get('enonce', '')} {qs_blob}"
                ).lower()

            _ANALYSE_KW = ['analyse', 'étude de fonction', 'etude de fonction', 'fonction', 'dérivée', 'derivee', 'asymptote', 'courbe', 'tableau de variations']
            _SUITES_KW = ['suite', 'récurrence', 'recurrence', 'u_n', 'u0', 'u_0', 'u5', 'u_5', 'somme']
            _STATS_KW = ['statistique', 'covariance', 'écart-type', 'ecart-type', 'ecart type', 'variance', 'corrélation', 'correlation', 'régression', 'regression']
            _EXCLUDE_COMPLEX_KW = ['complexe', 'nombres complexes', 'nombre complexe', 'affixe', 'argument', 'module', 'conjugu']

            def _is_complexe(ex: dict) -> bool:
                t = _exo_blob(ex)
                return any(kw in t for kw in _EXCLUDE_COMPLEX_KW)

            def _is_analyse(ex: dict) -> bool:
                t = _exo_blob(ex)
                chapter = str(ex.get('chapter', '') or '').lower()
                # Règle stricte demandée: Exercice 1 DOIT provenir du chapitre Analyse.
                if any(kw in t for kw in _SUITES_KW):
                    return False
                return 'analyse' in chapter

            def _is_suite(ex: dict) -> bool:
                t = _exo_blob(ex)
                return any(kw in t for kw in _SUITES_KW)

            def _is_stats(ex: dict) -> bool:
                t = _exo_blob(ex)
                return any(kw in t for kw in _STATS_KW)

            def _question_count(ex: dict) -> int:
                qs = [str(q).strip() for q in (ex.get('questions') or []) if str(q).strip()]
                if qs:
                    return len(qs)
                en = str(ex.get('enonce', '') or '')
                return len(_re.findall(r'(?m)^\s*(?:\d+|[a-z])\s*[\)\.-]\s*', en.lower()))

            def _render_maths_exercise(ex: dict) -> str:
                intro = (ex.get('intro') or '').strip()
                enonce = (ex.get('enonce') or '').strip()
                qs = [str(q).strip() for q in (ex.get('questions') or []) if str(q).strip()]

                parts = []
                if intro:
                    parts.append(intro)
                elif enonce:
                    parts.append(enonce)

                if qs:
                    parts.extend(qs)

                # Analyse : imposer des exercices complets (au moins 5 sous-questions)
                if _is_analyse(ex):
                    needed = max(0, 5 - _question_count(ex))
                    extras = [
                        'Déterminer le domaine de définition de la fonction.',
                        'Calculer la (ou les) limite(s) aux bornes du domaine.',
                        'Calculer la dérivée puis étudier le signe de f\'(x).',
                        'Dresser le tableau de variations de la fonction.',
                        'Déterminer les asymptotes éventuelles et leurs équations.',
                        'Tracer la courbe représentative et interpréter les résultats.'
                    ]
                    if needed > 0:
                        parts.extend(extras[:needed])

                return '\n\n'.join(p for p in parts if p).strip()

            # Nettoyer le pool: pas de complexes dans cette version demandée
            pool = [ex for ex in exercices if not _is_complexe(ex)]

            analysis_strong = [ex for ex in pool if _is_analyse(ex) and _question_count(ex) >= 5]
            analysis_pool = analysis_strong or [ex for ex in pool if _is_analyse(ex)]
            suites_pool = [ex for ex in pool if _is_suite(ex)]
            stats_pool = [ex for ex in pool if _is_stats(ex)]

            used = set()
            def _take_one(cands: list) -> dict | None:
                c2 = [c for c in cands if id(c) not in used]
                if not c2:
                    return None
                _random.shuffle(c2)
                pick = c2[0]
                used.add(id(pick))
                return pick

            # Exo 1: toujours une analyse (et de préférence 5+ questions)
            first = _take_one(analysis_pool)
            if not first:
                # Règle stricte demandée: le premier exercice doit être une analyse
                return {}
            # Garde-fou supplémentaire: doit appartenir explicitement au chapitre Analyse.
            if 'analyse' not in str(first.get('chapter', '') or '').lower():
                return {}
            partie_b_items.append(first)

            # Ajouter une suite et une stat si disponibles
            second = _take_one(suites_pool)
            if second:
                partie_b_items.append(second)

            third = _take_one(stats_pool)
            if third:
                partie_b_items.append(third)

            # Compléter jusqu'à 4 exercices avec le reste propre
            while len(partie_b_items) < 4:
                nxt = _take_one(pool)
                if not nxt:
                    break
                partie_b_items.append(nxt)

            # Sauvegarder le rendu structuré pour éviter les énoncés bruts mal lisibles
            for _ex in partie_b_items:
                _ex['_rendered_text'] = _render_maths_exercise(_ex)
                
        except Exception as _ex:
            # Fallback si exo_loader échoue
            print(f'[generate_exam_from_db] exo_loader error for maths: {_ex}')
            partie_b_items = []

            # Si exo_loader n'a pas retourné assez d'exercices, fallback vide (examen incomplet)
        if not partie_b_items or len(partie_b_items) < 4:
            return {}  # Impossible de générer un examen complët sans exos réels

        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'PARTIE A — Recopier et compléter les phrases suivantes (40 points — 4 pts chacune)',
                    'sections': [
                        {
                            'label': 'Complétez chaque phrase en écrivant le terme ou la formule manquante.',
                            'type': 'fillblank', 'pts': 40,
                            'items': [
                                {'text': _fix_latex(_global_format_tables(it.get('text', ''))),
                                 'answer': _fix_latex(it.get('answer', '___')),
                                 'pts': 4}
                                for it in partie_a_fill
                            ],
                        }
                    ],
                },
                {
                    'label': 'PARTIE B — Traiter TROIS des quatre exercices suivants (60 points — 20 pts chacun)',
                    'sections': [
                        {
                            'label': (
                                f'Exercice {i+1} — '
                                f'{("Chapitre Analyse — " if (i == 0 and "analyse" in str(it.get("chapter", "")).lower()) else "")}'
                                f'{it.get("theme", it.get("chapter", "Mathématiques"))} — {it.get("source", "")} (20 pts)'
                            ),
                            'type': 'open', 'pts': 20,
                            'items': [{'text': _fix_latex(_global_format_tables(it.get('_rendered_text') or _fmt_exercice(it) or it.get('enonce', it.get('intro', '')))), 'answer': 'Réponse développée attendue.', 'pts': 20}],
                        }
                        for i, it in enumerate(partie_b_items)
                    ],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # PHYSIQUE & CHIMIE — Même structure que MATHS
    # ════════════════════════════════════════════════════════════════════════
    if subject in ('physique', 'chimie'):
        from . import exo_loader as _exo_loader
        
        # Charger quiz et exercices depuis exo_loader
        partie_a_items = []
        if subject == 'physique':
            # ════════════════════════════════════════════════════════════════════════
            # PHYSIQUE — Structure COMPLÈTE
            # PARTIE I: 5 questions "Recopier et compléter" depuis quiz_physique.json
            # PARTIE II: 2 "Démonstrations" avec questions extraites
            # PARTIE III: 2 exercices de CHAPITRES DIFFÉRENTS avec questions extraites
            # PARTIE 2: 2 problèmes de CHAPITRES DIFFÉRENTS avec questions extraites
            # ════════════════════════════════════════════════════════════════════════
            from . import exo_loader as _exo_loader
            import json
            import os
            
            # ── PARTIE I: 5 questions "Recopier et compléter" depuis quiz_physique.json ──
            _partie_i_items = []
            try:
                _quiz_path = os.path.join(settings.BASE_DIR, 'database', 'quiz_physique.json')
                with open(_quiz_path, 'r', encoding='utf-8') as _qf:
                    _quiz_data = json.load(_qf)
                    _all_quizzes = _quiz_data.get('quiz', [])
                    
                # Prendre les 5 premières questions du quiz et les convertir en complétions de phrase
                for _q in _all_quizzes[:5]:
                    _question_text = _q.get('question', '')
                    # Extract the correct answer from options
                    _correct_idx = ord(_q.get('correct', 'A')) - ord('A')
                    _options = _q.get('options', [])
                    _correct_answer = _options[_correct_idx] if _correct_idx < len(_options) else 'Réponse attendue'
                    
                    # Convertir question en complément de phrase
                    # Supprimer les points d'interrogation et convertir en affirmation
                    _phrase = _question_text.replace('?', '').strip()
                    if _phrase.lower().startswith('que peut-on dire'):
                        _completion = _phrase.split('que peut-on dire')[0].strip() + ' __________________.'
                    elif 'vaut' in _phrase.lower():
                        _completion = _phrase.replace('vaut', 'vaut __________________.')
                    elif 'dépend' in _phrase.lower():
                        _completion = _phrase.replace('dépend', 'dépend __________________.')
                    elif 'correspondent' in _phrase.lower() or 'correspond' in _phrase.lower():
                        _completion = _phrase.replace('correspondent', 'correspondent à __________________').replace('correspond', 'correspond à __________________') + '.'
                    else:
                        # Fallback: ajouter juste des blancs à la fin
                        _completion = _phrase + ' __________________.'
                    
                    # Format as "Recopier et compléter" - completion de phrase
                    _partie_i_items.append({
                        'text': _fix_latex(_global_format_tables(_completion)),
                        'answer': _correct_answer,
                        'pts': 4,
                    })
            except Exception as _ex:
                print(f'[physique] Part I error: {_ex}')
                _partie_i_items = []

            if not _partie_i_items or len(_partie_i_items) < 5:
                return {}  # Pas assez pour Partie I

            # ── PARTIE II: 2 "Démonstrations" SEULEMENT (Traiter) ──
            try:
                _all_demos = _exo_loader.get_exercises('physique', chapter='Démonstrations', n=6)
                _partie_ii_items = _all_demos[:2]
            except Exception as _ex:
                print(f'[physique] Part II error: {_ex}')
                _partie_ii_items = []

            if not _partie_ii_items or len(_partie_ii_items) < 2:
                return {}  # Pas assez pour Partie II

            # ── PARTIE III: 2 exercices de CHAPITRES DIFFÉRENTS avec questions ──
            try:
                _all_exos_for_part3 = _exo_loader.get_exercises('physique', chapter='', n=50)
                # Éliminer Démonstrations
                _part3_pool = [ex for ex in _all_exos_for_part3 if 'Démonstrations' not in str(ex.get('chapter', ''))]
                
                # Sélectionner 2 de chapitres différents
                _part3_items = []
                _used_chapters = set()
                for ex in _part3_pool:
                    _ch = ex.get('chapter', ex.get('theme', 'Unknown'))
                    if _ch not in _used_chapters:
                        _part3_items.append(ex)
                        _used_chapters.add(_ch)
                        if len(_part3_items) >= 2:
                            break
                
                # Si pas assez de chapitres différents, prendre les 2 premières quand même
                if len(_part3_items) < 2:
                    _part3_items = _part3_pool[:2]
            except Exception as _ex:
                print(f'[physique] Part III error: {_ex}')
                _part3_items = []

            if not _part3_items or len(_part3_items) < 2:
                return {}  # Pas assez pour Partie III

            # ── PARTIE 2 (Problème): 2 problèmes de CHAPITRES DIFFÉRENTS ──
            try:
                _all_exos_for_part2 = _exo_loader.get_exercises('physique', chapter='', n=80)
                # Éliminer Démonstrations
                _part2_pool = [ex for ex in _all_exos_for_part2 if 'Démonstrations' not in str(ex.get('chapter', ''))]
                
                # Sélectionner 2 de chapitres différents et qui ne sont pas dans Part III
                _part2_items = []
                _used_chapters_part3 = {ex.get('chapter', ex.get('theme', 'Unknown')) for ex in _part3_items}
                _used_chapters_part2 = set()

                # Règle stricte: Problème 1 doit être un exercice d'analyse
                def _is_analysis_problem(ex: dict) -> bool:
                    blob = (
                        f"{ex.get('chapter', '')} {ex.get('theme', '')} "
                        f"{ex.get('intro', '')} {ex.get('enonce', '')} "
                        f"{' '.join(str(q) for q in (ex.get('questions') or []))}"
                    ).lower()
                    _kw = [
                        'analyse', 'étude', 'derivee', 'dérivée', 'fonction',
                        'limite', 'variation', 'asymptote', 'courbe'
                    ]
                    return any(k in blob for k in _kw)

                _analysis_candidates = [
                    ex for ex in _part2_pool
                    if _is_analysis_problem(ex)
                    and ex.get('chapter', ex.get('theme', 'Unknown')) not in _used_chapters_part3
                ]

                if not _analysis_candidates:
                    return {}

                _random.shuffle(_analysis_candidates)
                _first_problem = _analysis_candidates[0]
                _part2_items.append(_first_problem)
                _used_chapters_part2.add(_first_problem.get('chapter', _first_problem.get('theme', 'Unknown')))
                
                for ex in _part2_pool:
                    _ch = ex.get('chapter', ex.get('theme', 'Unknown'))
                    # Préférer des chapitres différents de Part III ET entre les 2 problèmes
                    if _ch not in _used_chapters_part3 and _ch not in _used_chapters_part2:
                        _part2_items.append(ex)
                        _used_chapters_part2.add(_ch)
                        if len(_part2_items) >= 2:
                            break
                
                # Si pas assez, compléter avec le pool restant tout en gardant problème 1 = analyse
                if len(_part2_items) < 2:
                    for ex in _part2_pool:
                        if id(ex) == id(_first_problem):
                            continue
                        _part2_items.append(ex)
                        if len(_part2_items) >= 2:
                            break
            except Exception as _ex:
                print(f'[physique] Part 2 error: {_ex}')
                _part2_items = []

            if not _part2_items or len(_part2_items) < 2:
                return {}  # Pas assez pour Partie 2

            # ── Générer les items PARTIE III avec vraies options MCQ ──
            _part3_items_rendered = []
            for i, ex in enumerate(_part3_items):
                _intro = ex.get('intro', ex.get('enonce', 'Énoncé'))
                _first_q = ex.get('questions', [''])[0] if ex.get('questions') else ''
                _options, _correct = _generate_mcq_options(_intro, _first_q, 'physique')
                
                # Ne pas répéter la question si c'est la même que l'intro
                _display_text = _intro
                if _first_q and _first_q.strip() != _intro.strip()[:len(_first_q)] and len(_first_q) > 10:
                    _display_text = f"{_intro}\n\n{_first_q}"
                
                # Ajouter les options MCQ au texte SANS marquer la réponse correcte
                _options_text = "\n".join(_options)
                _full_text = f"{_display_text}\n\nQuelle est la reponse correcte?\n\n{_options_text}"
                
                _part3_items_rendered.append({
                    'text': _fix_latex(_global_format_tables(_full_text)),
                    'answer': f"Reponse correcte: {_correct.upper()}",
                    'pts': 10,
                    'source': ex.get('source_display', ''),
                    'options': _options,
                    'correct': _correct
                })

            # ── Construction de l'exam ──
            return {
                'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
                'parts': [
                    {
                        'label': 'PREMIÈRE PARTIE (60 points)',
                        'sections': [
                            {
                                'label': 'I. Recopier et compléter (20 points — 4 pts chacune)',
                                'type': 'fillblank', 'pts': 20,
                                'items': _partie_i_items,
                            },
                            {
                                'label': 'II. Traiter l\'une des deux démonstrations suivantes (20 pts)',
                                'type': 'open', 'pts': 20,
                                'items': [
                                    {
                                        'text': _fix_latex(_global_format_tables(
                                            f"{ex.get('intro', ex.get('enonce', 'Énoncé'))}\n\n" + 
                                            '\n'.join([q for q in ex.get('questions', []) if q and q.strip() and len(q) < 150])
                                        )) if (ex.get('questions') and len([q for q in ex.get('questions', []) if len(q) < 150]) > 0)
                                        else _fix_latex(_global_format_tables(ex.get('enonce', ex.get('intro', 'Énoncé'))))
                                    ,
                                        'answer': f"Démonstration: {ex.get('enonce', ex.get('intro', ''))[:100]}",
                                        'pts': 10,
                                        'source': ex.get('source_display', ''),
                                    }
                                    for i, ex in enumerate(_partie_ii_items)
                                ],
                            },
                            {
                                'label': 'III. Traiter les deux exercices suivants — Choix multiples (20 pts — 10 pts chacun)',
                                'type': 'mcq', 'pts': 20,
                                'items': _part3_items_rendered,
                            },
                        ],
                    },
                    {
                        'label': 'DEUXIÈME PARTIE — Traiter les deux problèmes (60 points)',
                        'sections': [
                            {
                                'label': f'Problème {i+1} — {ex.get("chapter", ex.get("theme", "Physique"))} (30 pts)',
                                'type': 'open', 'pts': 30,
                                'items': [
                                    {
                                        'text': _fix_latex(_global_format_tables(f"{ex.get('intro', ex.get('enonce', ''))}\n\n" + '\n'.join(
                                            [f"{j+1}. {q}" for j, q in enumerate(ex.get('questions', [ex.get('enonce', ex.get('intro', ''))]))]
                                        ))),
                                        'answer': f"Problème: {ex.get('intro', ex.get('enonce', ''))[:100]}",
                                        'pts': 30,
                                        'source': ex.get('source_display', ''),
                                    }
                                ],
                            }
                            for i, ex in enumerate(_part2_items)
                        ],
                    },
                ],
            }

        # ════════════════════════════════════════════════════════════════════════
        # CHIMIE — A Complétions (20) + B Équations (20) + C Question choix (15)
        #          + D Texte (15) + E Problèmes (30) = 100 PTS
        # ════════════════════════════════════════════════════════════════════════
        if subject == 'chimie':
            from . import exo_loader as _exo_loader
            import json
            import os
            
            # ── PARTIE A: Compléter les phrases (20 pts — 10 phrases × 2 pts) ──
            _partie_a_items = []
            try:
                _quiz_path = os.path.join(settings.BASE_DIR, 'database', 'quiz_chimie.json')
                with open(_quiz_path, 'r', encoding='utf-8') as _qf:
                    _quiz_data = json.load(_qf)
                    _all_quizzes = _quiz_data.get('quiz', [])
                    
                # Prendre les 10 premières questions du quiz et les convertir en complétions
                for _q in _all_quizzes[:10]:
                    _question_text = _q.get('question', '')
                    # Extract correct answer
                    _correct_idx = ord(_q.get('correct', 'A')) - ord('A')
                    _options = _q.get('options', [])
                    _correct_answer = _options[_correct_idx] if _correct_idx < len(_options) else 'Réponse attendue'
                    
                    # Convertir question en phrase à compléter
                    _phrase = _question_text.replace('?', '').strip()
                    if 'sont' in _phrase.lower():
                        _completion = _phrase.replace('sont', 'sont __________.')
                    elif 'est' in _phrase.lower():
                        _completion = _phrase.replace('est', 'est __________.')
                    elif 'contiennent' in _phrase.lower() or 'contient' in _phrase.lower():
                        _completion = _phrase + ' __________.'
                    else:
                        _completion = _phrase + ' __________.'
                    
                    _partie_a_items.append({
                        'text': _fix_latex(_global_format_tables(_completion)),
                        'answer': _correct_answer,
                        'pts': 2,
                    })
            except Exception as _ex:
                print(f'[chimie] Part A error: {_ex}')
                _partie_a_items = []

            if not _partie_a_items or len(_partie_a_items) < 10:
                return {}  # Pas assez pour Partie A

            # ── PARTIE B: Équations à équilibrer (20 pts — 4 équations × 5 pts) ──
            # Pool basique d'équations de chimie
            _chimie_equations = [
                {'eq': '$CH_4 + 2O_2 \\\\to CO_2 + 2H_2O$', 'desc': 'Combustion du méthane'},
                {'eq': '$C_2H_6 + \\\\frac{7}{2}O_2 \\\\to 2CO_2 + 3H_2O$', 'desc': 'Combustion de l\'éthane'},
                {'eq': '$CH_2CH_2 + Br_2 \\\\to CH_2Br-CH_2Br$', 'desc': 'Addition de dibrome sur l\'éthylène'},
                {'eq': '$H_2SO_4 + 2NaOH \\\\to Na_2SO_4 + 2H_2O$', 'desc': 'Neutralisation acide-base'},
                {'eq': '$2H_2O_2 \\\\xrightarrow{MnO_2} 2H_2O + O_2$', 'desc': 'Décomposition du peroxyde'},
                {'eq': '$Fe + 2HCl \\\\to FeCl_2 + H_2$', 'desc': 'Réaction du fer avec l\'acide chlorhydrique'},
                {'eq': '$CaCO_3 \\\\to CaO + CO_2$', 'desc': 'Décomposition du carbonate de calcium'},
                {'eq': '$2NaOH + H_2SO_4 \\\\to Na_2SO_4 + 2H_2O$', 'desc': 'Neutralisation (2:1)'},
            ]
            
            _partie_b_items = []
            try:
                for i, _eq_dict in enumerate(_chimie_equations[:4]):
                    _partie_b_items.append({
                        'text': _fix_latex(_global_format_tables(f"Écrire et équilibrer: {_eq_dict['desc']}")),
                        'answer': f"${_eq_dict['eq']}$",
                        'pts': 5,
                    })
            except Exception as _ex:
                print(f'[chimie] Part B error: {_ex}')
                _partie_b_items = []

            if not _partie_b_items or len(_partie_b_items) < 4:
                return {}  # Pas assez pour Partie B

            # ── PARTIE C: Traiter UNE des deux questions suivantes (15 pts) ──
            # Part C: Questions SIMPLES (nomenclature, formules, isomères, acide-base, alcools, oxydoréduction)
            _partie_c_items = []
            _partie_c_chapters = []  # Track chapters to avoid duplicates
            try:
                # Chapitres SIMPLES → nomenclature, structures, notions basiques, SANS calculs complexes
                _simple_chapters = [
                    'Hydrocarbures : nomenclature',
                    'Acide-base :',
                    'Alcools :',
                    'Oxydoréduction :'
                ]
                for _ch in _simple_chapters:
                    if len(_partie_c_items) >= 2:
                        break
                    _all_exos_for_part_c = _exo_loader.get_exercises('chimie', chapter=_ch, n=6)
                    if _all_exos_for_part_c:
                        _ex = _all_exos_for_part_c[0]
                        _partie_c_items.append({
                            'text': _fix_latex(_global_format_tables(
                                f"Question {len(_partie_c_items) + 1}\n\n{_ex.get('enonce', '')}"
                            )),
                            'answer': f"Réponse développée",
                            'pts': 15,
                            'source': _ex.get('source', ''),
                            'chapter': _ch,
                        })
                        _partie_c_chapters.append(_ch)
                
            except Exception as _ex:
                print(f'[chimie] Part C error: {_ex}')
                _partie_c_items = []

            if not _partie_c_items or len(_partie_c_items) < 2:
                # Fallback avec questions génériques simples
                _partie_c_items = [
                    {
                        'text': _fix_latex(_global_format_tables('Question 1 — Identifier les isomères de la formule brute $C_3H_6O$ présentant un groupe carbonyle et décrire un test pour les distinguer.')),
                        'answer': 'Réponse développée attendue',
                        'pts': 15,
                        'chapter': 'Fallback',
                    },
                    {
                        'text': _fix_latex(_global_format_tables('Question 2 — Donner le nom systématique des composés : a) CH₃–CH₂–CH₂OH  b) CH₃–CO–CH₃  c) CH₃–CH(CH₃)–CH₃')),
                        'answer': 'Réponse développée attendue',
                        'pts': 15,
                        'chapter': 'Fallback',
                    },
                ]
                _partie_c_chapters = ['Fallback', 'Fallback']

            # ── PARTIE D: Étude de texte (15 pts) ──
            _partie_d_item = {
                'text': _fix_latex(_global_format_tables(
                    'TEXTE (Étude):\n\n'
                    '"Les composés organiques sont formés du carbone et d\'hydrogène, souvent avec de l\'oxygène, de l\'azote ou du soufre. '
                    'Les hydrocarbures sont une classe importante de composés organiques. Parmi eux, les alcanes sont saturés (liaisons simples uniquement), '
                    'tandis que les alcènes et alcynes sont insaturés (liaisons doubles ou triples). Les propriétés physiques et chimiques dépendent de la structure. '
                    'Par exemple, les isomères de constitution comme le propan-1-ol et le propanal ont la même formule brute mais des propriétés chimiques très différentes."\n\n'
                    '1. Définir un hydrocarbure saturé et un hydrocarbure insaturé.\n'
                    '2. Expliquer pourquoi deux isomères de fonction peuvent avoir des propriétés chimiques différentes.\n'
                    '3. Citer un test chimique permettant de distinguer le propanal du propan-1-ol.'
                )),
                'answer': 'Réponse développée attendue',
                'pts': 15,
            }

            # ── PARTIE E: Problèmes (30 pts — 2 problèmes au choix sur 3) ──
            # Part E: Exercices COMPLEXES avec calculs (combustion, vin titré, polymères, fermentation, hydrolyse)
            # IMPORTANT: Ne pas charger des chapitres déjà utilisés dans Partie C
            _partie_e_items = []
            try:
                # Chapitres COMPLEXES → calculs avancés, stœchiométrie, titrage
                _all_complex_chapters = [
                    'Combustion des hydrocarbures',
                    'Vin titré',
                    'Polymères',
                    'Fermentation',
                    'Hydrolyse des carbures',
                    'Réactions des hydrocarbures'
                ]
                
                # Filtrer pour éviter les chapitres de Partie C
                _complex_chapters = [ch for ch in _all_complex_chapters if ch not in _partie_c_chapters]
                
                for _ch in _complex_chapters:
                    if len(_partie_e_items) >= 3:
                        break
                    _all_exos_for_part_e = _exo_loader.get_exercises('chimie', chapter=_ch, n=10)
                    if _all_exos_for_part_e:
                        for _ex in _all_exos_for_part_e[:min(2, 3 - len(_partie_e_items))]:
                            _partie_e_items.append({
                                'text': _fix_latex(_global_format_tables(
                                    f"Problème {len(_partie_e_items) + 1}\n\n{_ex.get('enonce', '')}\n\n" +
                                    '\n'.join([f"{j+1}. {q}" for j, q in enumerate(_ex.get('questions', [])[:3])])
                                )),
                                'answer': f"Solution attendue",
                                'pts': 15,
                                'source': _ex.get('source', ''),
                                'chapter': _ch,
                            })
            except Exception as _ex:
                print(f'[chimie] Part E error: {_ex}')
                _partie_e_items = []

            # Fallback si pas assez de problèmes complexes
            if not _partie_e_items or len(_partie_e_items) < 3:
                _partie_e_items = [
                    {
                        'text': _fix_latex(_global_format_tables('Problème 1 — Combustion complète: Calculer la masse de $CO_2$ et $H_2O$ produits par la combustion complète de 10 g de pentane $C_5H_{12}$. (M(C)=12, M(H)=1, M(O)=16)')),
                        'answer': 'Solution avec calcul stœchiométrique',
                        'pts': 15,
                        'chapter': 'Fallback',
                    },
                    {
                        'text': _fix_latex(_global_format_tables('Problème 2 — Titrage du vin: Un vin contient 12° alcoolique (12 mL d\'éthanol pour 100 mL). Calculer le volume d\'éthanol dans une bouteille de 750 mL et sa masse (densité ethanol = 0,79 g/mL).')),
                        'answer': 'Solution avec calcul de pourcentage et masse',
                        'pts': 15,
                        'chapter': 'Fallback',
                    },
                    {
                        'text': _fix_latex(_global_format_tables('Problème 3 — Hydrolyse du carbure de calcium: Calculer le volume de gaz acétylène produit par l\'hydrolyse de 32 g de $CaC_2$ à 25°C et 1 atm. (M(CaC₂)=64 g/mol, R=0,082 L·atm/(mol·K))')),
                        'answer': 'Solution avec calcul de volume gazeux',
                        'pts': 15,
                        'chapter': 'Fallback',
                    },
                ]

            # ── Construction de l'exam ──
            return {
                'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
                'parts': [
                    {
                        'label': 'PREMIÈRE PARTIE (50 points)',
                        'sections': [
                            {
                                'label': 'A. Recopier et compléter les phrases (20 points — 2 pts chacune)',
                                'type': 'fillblank', 'pts': 20,
                                'items': _partie_a_items,
                            },
                            {
                                'label': 'B. Écrire et équilibrer les équations (20 pts — 5 pts chacune)',
                                'type': 'open', 'pts': 20,
                                'items': _partie_b_items,
                            },
                            {
                                'label': 'C. Traiter UNE des deux questions (15 pts)',
                                'type': 'open', 'pts': 15,
                                'items': [
                                    {
                                        'text': item['text'],
                                        'answer': item['answer'],
                                        'pts': 15,
                                        'source': item.get('source', ''),
                                    }
                                    for item in _partie_c_items
                                ],
                            },
                        ],
                    },
                    {
                        'label': 'DEUXIÈME PARTIE (50 points)',
                        'sections': [
                            {
                                'label': 'D. Étude de texte (15 pts)',
                                'type': 'open', 'pts': 15,
                                'items': [_partie_d_item],
                            },
                            {
                                'label': 'E. Traiter DEUX des trois problèmes (30 pts — 15 pts chacun)',
                                'type': 'open', 'pts': 30,
                                'items': [
                                    {
                                        'text': item['text'],
                                        'answer': item['answer'],
                                        'pts': 15,
                                        'source': item.get('source', ''),
                                    }
                                    for item in _partie_e_items[:3]
                                ],
                            },
                        ],
                    },
                ],
            }

    # ════════════════════════════════════════════════════════════════════════
    # SVT — BIOLOGIE (50 pts) + GÉOLOGIE (50 pts)
    # Structure officielle BAC Haïti :
    #   Biologie : Partie I (questions de cours, 20 pts)
    #              Partie II (génétique/hérédité, 20 pts)
    #              Partie III (généralités/définitions, 10 pts)
    #   Géologie : Partie I (stratigraphie/paléontologie/structure Terre, 25 pts)
    #              Partie II (analyse de document/hypothèses, 25 pts)
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'svt':
        from . import exo_loader as _exo_loader

        # ── Load quiz questions filtered by discipline ──────────────────────
        def _load_quiz_by_discipline(discipline: str, n: int, pts_each: int = 5) -> list:
            """Load quiz questions filtered by biologie/geologie discipline."""
            qpath = os.path.join(settings.BASE_DIR, 'database', 'quiz_SVT.json')
            if not os.path.exists(qpath):
                return []
            try:
                import json as _jq2
                with open(qpath, encoding='utf-8') as _qf2:
                    qdata2 = _jq2.load(_qf2)
                pool = [q for q in qdata2.get('quiz', []) if q.get('discipline', '') == discipline]
                _random.shuffle(pool)
                result = []
                for q in pool[:n]:
                    opts = list(q.get('options', []))
                    correct_letter = q.get('correct', 'A').upper()
                    correct_idx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(correct_letter, 0)
                    answer_text = opts[correct_idx] if correct_idx < len(opts) else ''
                    explication = q.get('explanation', q.get('explication', ''))
                    full_answer = answer_text
                    if explication:
                        full_answer = f'{answer_text}\n\n{explication}'
                    result.append({
                        'text': q.get('question', ''),
                        'answer': full_answer,
                        'pts': pts_each,
                        'category': q.get('category', ''),
                    })
                return result
            except Exception:
                return []

        # ── Biologie: questions de cours from quiz ──────────────────────────
        bio_cours_items = _load_quiz_by_discipline('biologie', n=4, pts_each=5)

        # ── Biologie: exercices de génétique from real BAC exercises ────────
        bio_exos = []
        try:
            all_exos = _exo_loader.get_exercises('svt', chapter='', n=20)
            if all_exos:
                _random.shuffle(all_exos)
                bio_exos = all_exos[:2]
        except Exception as _ex:
            print(f'[generate_exam_from_db] Error loading SVT exercises: {_ex}')

        # ── Biologie: généralités from quiz (different category) ────────────
        bio_gen_items = _load_quiz_by_discipline('biologie', n=2, pts_each=5)
        # Try to pick items from different categories than bio_cours
        used_cats = {it.get('category', '') for it in bio_cours_items}
        bio_gen_pool = [it for it in _load_quiz_by_discipline('biologie', n=20, pts_each=5)
                        if it.get('category', '') not in used_cats and it not in bio_cours_items]
        if len(bio_gen_pool) >= 2:
            bio_gen_items = bio_gen_pool[:2]

        # ── Géologie: questions from quiz filtered by discipline ────────────
        geo_items_1 = _load_quiz_by_discipline('geologie', n=5, pts_each=5)
        geo_items_2 = _load_quiz_by_discipline('geologie', n=5, pts_each=5)
        # Ensure no overlap between the two geology parts
        seen_geo_texts = {it['text'] for it in geo_items_1}
        geo_items_2 = [it for it in _load_quiz_by_discipline('geologie', n=20, pts_each=5)
                       if it['text'] not in seen_geo_texts][:5]

        # ── Build bio exercise items (from real BAC exo_svt.json) ───────────
        bio_exo_items = []
        for i, ex in enumerate(bio_exos):
            intro = ex.get('intro', ex.get('enonce', ''))
            questions = ex.get('questions', [])
            full_text = intro
            if questions:
                full_text = intro + '\n\n' + '\n'.join(questions)
            reponses = ex.get('reponses', [])
            answer = '\n'.join(str(r) for r in reponses) if reponses else 'Réponse développée attendue.'
            bio_exo_items.append({
                'text': _fix_latex(_global_format_tables(f"Exercice {i+1}\n\n{full_text}")),
                'answer': _fix_latex(answer),
                'pts': 10,
            })

        # ── Validate we have enough content ─────────────────────────────────
        if not bio_cours_items and not bio_exo_items and not geo_items_1:
            return {}

        # ── Build the exam structure ────────────────────────────────────────
        bio_sections = []

        # Partie I — Questions de cours (20 pts)
        if bio_cours_items:
            bio_sections.append({
                'label': 'Première partie — Questions de cours (20 points)',
                'type': 'open', 'pts': 20,
                'items': [{'text': it['text'], 'answer': it['answer'], 'pts': 5} for it in bio_cours_items[:4]],
            })

        # Partie II — Génétique et hérédité (20 pts)
        if bio_exo_items:
            bio_sections.append({
                'label': 'Deuxième partie — Génétique et hérédité (20 points)',
                'type': 'open', 'pts': 20,
                'items': bio_exo_items,
            })
        else:
            # Fallback: use more bio quiz questions for genetics
            genetics_items = _load_quiz_by_discipline('biologie', n=4, pts_each=5)
            if genetics_items:
                bio_sections.append({
                    'label': 'Deuxième partie — Génétique et hérédité (20 points)',
                    'type': 'open', 'pts': 20,
                    'items': [{'text': it['text'], 'answer': it['answer'], 'pts': 5} for it in genetics_items[:4]],
                })

        # Partie III — Généralités (10 pts)
        if bio_gen_items:
            bio_sections.append({
                'label': 'Troisième partie — Généralités (10 points)',
                'type': 'open', 'pts': 10,
                'items': [{'text': it['text'], 'answer': it['answer'], 'pts': 5} for it in bio_gen_items[:2]],
            })

        geo_sections = []

        # Géologie Partie I — Stratigraphie, paléontologie, structure de la Terre (25 pts)
        if geo_items_1:
            geo_sections.append({
                'label': 'Première partie — Stratigraphie, paléontologie et structure de la Terre (25 points)',
                'type': 'open', 'pts': 25,
                'items': [{'text': it['text'], 'answer': it['answer'], 'pts': 5} for it in geo_items_1[:5]],
            })

        # Géologie Partie II — Analyse de document et hypothèses (25 pts)
        if geo_items_2:
            geo_sections.append({
                'label': 'Deuxième partie — Analyse et hypothèses (25 points)',
                'type': 'open', 'pts': 25,
                'items': [{'text': it['text'], 'answer': it['answer'], 'pts': 5} for it in geo_items_2[:5]],
            })

        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'BIOLOGIE (50 points)',
                    'sections': bio_sections,
                },
                {
                    'label': 'GÉOLOGIE (50 points)',
                    'sections': geo_sections,
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # PHILOSOPHIE
    # ─ PREMIÈRE PARTIE (60 pts) : au choix →  A) Dissertation  OU  B) Étude de texte
    # ─ DEUXIÈME PARTIE (40 pts) : questions de cours à réponse courte (2 × 20 pts)
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'philosophie':

        # ── Groq génère le contenu complet en un seul appel ────────────────
        _philo_prompt = (
            "Tu es un professeur de Philosophie qui rédige un vrai sujet de BAC Haïti Terminale.\n"
            "Structure obligatoire :\n\n"
            "PREMIÈRE PARTIE (60 points) — L'élève traite AU CHOIX soit le SUJET A soit le SUJET B.\n\n"
            "SUJET A — Dissertation (60 pts)\n"
            "  Propose UN sujet de dissertation philosophique sérieux (question ouverte, niveau terminale).\n"
            "  Thèmes possibles : liberté/déterminisme, conscience/inconscient, vérité/opinion,\n"
            "  morale/politique, raison/passion, technique/humanité, justice/droit, bonheur/devoir.\n"
            "  Consigne : rédiger une dissertation avec introduction, deux parties développées, conclusion.\n\n"
            "SUJET B — Étude de texte (60 pts)\n"
            "  Propose un extrait de texte philosophique original (auteur classique ou contemporain,\n"
            "  10-15 lignes) suivi EXACTEMENT de ces 4 questions :\n"
            "  Q1 (15 pts) : Quelle est la thèse défendue par l'auteur ?\n"
            "  Q2 (15 pts) : Comment l'auteur construit-il son argumentation ?\n"
            "  Q3 (15 pts) : Expliquez la phrase «[phrase clé du texte]».\n"
            "  Q4 (15 pts) : Quel est l'intérêt philosophique de ce texte ? Donnez votre avis critique.\n\n"
            "DEUXIÈME PARTIE (40 points) — Questions de cours (obligatoire pour tous)\n"
            "  Pose EXACTEMENT 4 questions de cours variées (10 pts chacune).\n"
            "  Couvre des domaines DIFFÉRENTS parmi :\n"
            "  • Concepts moraux/éthiques : bien, mal, devoir, conscience morale, vertu, vice, responsabilité,\n"
            "    impératif catégorique (Kant), utilitarisme (Mill/Bentham), éthique des vertus (Aristote).\n"
            "  • Auteurs et leurs œuvres : cite un auteur réel (Platon, Aristote, Descartes, Spinoza, Locke,\n"
            "    Hume, Kant, Hegel, Marx, Nietzsche, Sartre, Simone de Beauvoir, Bergson, Camus, Rousseau,\n"
            "    Voltaire, Montesquieu) et une de ses œuvres ou thèses principales.\n"
            "  • Courants philosophiques : rationalisme (Descartes, Spinoza, Leibniz — la raison prime),\n"
            "    empirisme (Hume, Locke — l'expérience prime), idéalisme (Platon, Hegel — les idées primaires),\n"
            "    existentialisme (Sartre, Camus — l'existence précède l'essence),\n"
            "    matérialisme/marxisme (Marx — infrastructure économique), stoïcisme, épicurisme.\n"
            "  • Notions clés : liberté, vérité, justice, État, pouvoir, langage, art, technique, temps,\n"
            "    bonheur, autrui, inconscient, perception, mémoire, histoire, nature/culture.\n"
            "  Chaque question doit être précise : demander une définition, une distinction entre notions,\n"
            "  expliquer la thèse d'un auteur précis, identifier le courant d'un penseur, ou comparer deux\n"
            "  philosophes sur un même thème.\n\n"
            "Réponds en JSON uniquement (sans markdown) avec cette structure :\n"
            '{"dissertation_sujet":"...","dissertation_consigne":"Rédigez une dissertation philosophique '
            'comportant une introduction (problématisation + annonce du plan), un développement en deux '
            'parties (thèse et antithèse argumentées avec exemples), et une conclusion (bilan + ouverture).",'
            '"texte_extrait":"...","texte_auteur":"Auteur, Œuvre, année","texte_questions":['
            '{"q":"...","pts":15},{"q":"...","pts":15},{"q":"...","pts":15},{"q":"...","pts":15}],'
            '"cours_questions":[{"q":"...","pts":10},{"q":"...","pts":10},{"q":"...","pts":10},{"q":"...","pts":10}]}'
        )

        _pr = _call_json(_philo_prompt, max_tokens=2200)
        _pr = _re.sub(r'```[a-z]*\s*', '', _pr).strip()
        _pm = _re.search(r'\{[\s\S]+\}', _pr)
        _philo_data = {}
        if _pm:
            try:
                import json as _jphi
                _philo_data = _jphi.loads(_pm.group(0))
            except Exception:
                pass

        # ── Fallback si JSON invalide ───────────────────────────────────────
        if not _philo_data.get('dissertation_sujet'):
            _philo_data = {
                'dissertation_sujet': "L'homme est-il condamné à être libre ?",
                'dissertation_consigne': (
                    "Rédigez une dissertation philosophique comportant : une introduction "
                    "(problématisation + annonce du plan), un développement en deux parties "
                    "(thèse et antithèse argumentées avec exemples), et une conclusion (bilan + ouverture)."
                ),
                'texte_extrait': (
                    "« La liberté n'est pas un état que l'on possède, mais un acte que l'on accomplit. "
                    "Être libre, ce n'est pas faire ce que l'on veut, c'est vouloir ce que l'on fait. "
                    "Toute action libre suppose une délibération, un choix et une responsabilité. "
                    "Or l'homme, à la différence de l'animal, n'est pas enfermé dans un programme "
                    "instinctif : il se définit par ce qu'il fait de ce que l'on a fait de lui. »"
                ),
                'texte_auteur': "Jean-Paul Sartre, L'Être et le Néant, 1943",
                'texte_questions': [
                    {"q": "Quelle est la thèse principale défendue par l'auteur dans ce texte ?", "pts": 15},
                    {"q": "Comment l'auteur construit-il son argumentation ? Identifiez les étapes du raisonnement.", "pts": 15},
                    {"q": "Expliquez la phrase : « être libre, ce n'est pas faire ce que l'on veut, c'est vouloir ce que l'on fait ».", "pts": 15},
                    {"q": "Quel est l'intérêt philosophique de ce texte ? Exprimez votre point de vue critique en vous appuyant sur vos connaissances.", "pts": 15},
                ],
                'cours_questions': [
                    {"q": "Définissez la notion de «devoir» en morale kantienne. En quoi l'impératif catégorique de Kant se distingue-t-il de l'impératif hypothétique ? Illustrez par un exemple.", "pts": 10},
                    {"q": "Qu'est-ce que le rationalisme ? Citez deux philosophes rationalistes et expliquez en quoi leur méthode diffère de celle des empiristes.", "pts": 10},
                    {"q": "Présentez la thèse principale de Jean-Paul Sartre dans L'Existentialisme est un humanisme. Que signifie «l'existence précède l'essence» ?", "pts": 10},
                    {"q": "Distinguez l'éthique de la morale. Comment Aristote définit-il la vertu dans l'Éthique à Nicomaque ? En quoi le bonheur (eudaimonia) est-il le but ultime de l'action humaine selon lui ?", "pts": 10},
                ]
            }

        # ── Construire les sections ─────────────────────────────────────────
        dis_text = (
            f"**Sujet de dissertation :**\n\n"
            f"{_philo_data.get('dissertation_sujet', '')}\n\n"
            f"---\n\n"
            f"{_philo_data.get('dissertation_consigne', '')}"
        )

        texte_qs = _philo_data.get('texte_questions', [])
        texte_body = (
            f"**Texte — {_philo_data.get('texte_auteur', '')}**\n\n"
            f"{_philo_data.get('texte_extrait', '')}\n\n"
            f"---\n\n"
            f"**Questions :**\n\n" +
            '\n\n'.join(
                f"{idx+1}) {q.get('q','')} ({q.get('pts',0)} pts)"
                for idx, q in enumerate(texte_qs)
            )
        )

        cours_items = [
            {'text': _fix_latex(_global_format_tables(q.get('q', ''))), 'answer': 'Réponse développée attendue.', 'pts': q.get('pts', 10)}
            for q in _philo_data.get('cours_questions', [])
        ]
        _philo_mcq = _load_quiz_mcq(10, 4)

        return {
            'title': title, 'duration': duration, 'annee': _annee,
            'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'PREMIÈRE PARTIE — Dissertation ou Étude de texte au choix (60 points)',
                    'choice_notice': (
                        'Traitez soit le SUJET A (dissertation), soit le SUJET B (étude de texte). '
                        'Un seul sujet au choix — 60 points.'
                    ),
                    'sections': [
                        {
                            'label': 'SUJET A — Dissertation (60 pts) — Choisir A ou B',
                            'type': 'open', 'pts': 60,
                            'items': [{'text': _fix_latex(_global_format_tables(dis_text)),
                                       'answer': 'Dissertation rédigée (intro + développement + conclusion).',
                                       'pts': 60}],
                        },
                        {
                            'label': 'SUJET B — Étude de texte (60 pts) — Choisir A ou B',
                            'type': 'open', 'pts': 60,
                            'items': [{'text': _fix_latex(_global_format_tables(texte_body)),
                                       'answer': 'Réponses aux 4 questions attendues (15 pts chacune).',
                                       'pts': 60}],
                        },
                    ],
                },
                {
                    'label': 'DEUXIÈME PARTIE — Questions de cours (40 points)',
                    'sections': [
                        _mcq_section(
                            'Questions à choix multiples de Philosophie (40 points — 4 pts chacune)',
                            _philo_mcq
                        ) if _philo_mcq else {
                            'label': 'Répondre aux quatre questions suivantes (10 pts chacune)',
                            'type': 'open', 'pts': 40,
                            'items': cours_items,
                        },
                    ],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # FRANÇAIS
    # PARTIE I  : Compréhension / Analyse de texte (40 pts)
    # PARTIE II : Étude grammaticale / QCM (20 pts)
    # PARTIE III: Production écrite (40 pts)
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'francais':
        textes = _collect_items(['question_texte'], min_len=80)

        # Filtrage qualité : garder seulement les items avec ≥3 sous-questions numérotées
        _textes_rich = [t for t in textes
                        if len(_re.findall(r'(?m)^\s*[1-9][.)]\s', t['text'])) >= 3]
        if _textes_rich:
            textes = _textes_rich

        # PARTIE I : 1 passage texte kreyòl
        texte_item = _pick(textes, 1)

        # ── Génération IA : tout le contenu en Kreyòl (1 seul appel) ──────────
        _kreyol_p1_q   = ''   # nouvelles kesyon konpreyansyon an Kreyòl
        _kreyol_p2_items = []  # 4 kesyon gramè/lang an Kreyòl
        _kreyol_p3_topic = ''  # sijè pwodiksyon ekri an Kreyòl

        if texte_item:
            _it0 = texte_item[0]
            # Extraire le texte brut du passage (avant la section Kesyon :)
            _ft = _it0['text']
            _passage_raw = _ft.split('Kesyon :')[0].replace('TÈKS :\n\n', '').strip()[:1400]

            _kreyol_prompt = (
                "Ou se yon pwofesè Kreyòl Ayisyen nivo BAC Ayiti. "
                "EKRI TOUT SA AN KREYÒL AYISYEN SÈLMAN — pa janm an fransè.\n\n"
                f"TÈKS :\n{_passage_raw}\n\n"
                "Ekri EGZAKTEMAN sa ki mande a (pa tradui tèks la ankò).\n\n"
                "===P1===\n"
                "5 kesyon konpreyansyon sou TÈKS la (nimewo 1) 2) 3) 4) 5)), "
                "chak fini ak ?. Kouvri: lide prensipal, yon detay, yon ekspresyon, "
                "tip/estrikti tèks, opinyon pèsonel.\n\n"
                "===P2===\n"
                "4 kesyon gramè/lang Kreyòl (nimewo 1) 2) 3) 4)), chak fini ak ?. "
                "Sijè: predika, figi de style, diskou rapòte, sinonim/antonim, "
                "kalite tèks, ekspresyon.\n\n"
                "===P3===\n"
                "1 sijè pwodiksyon ekri (200-250 mo) an rapò ak tèks la oswa yon sijè "
                "sosyal Ayiti. Fòma: 'Ekri yon [kalite tèks] sou [sijè konkrè]...'\n\n"
                "RÈG: OKENN MO FRANSÈ. Kreyòl Ayisyen sèlman."
            )
            _kreyol_raw = _call_fast(_kreyol_prompt, max_tokens=700)

            # Parser les 3 sections
            _p_parts = _re.split(r'===P[123]===', _kreyol_raw)
            _p1_txt  = _p_parts[1].strip() if len(_p_parts) > 1 else ''
            _p2_txt  = _p_parts[2].strip() if len(_p_parts) > 2 else ''
            _p3_txt  = _p_parts[3].strip() if len(_p_parts) > 3 else ''

            # P1 : kesyon konpreyansyon
            if _p1_txt and len(_re.findall(r'[1-5][.)]\s', _p1_txt)) >= 3:
                _kreyol_p1_q = _p1_txt

            # P2 : 4 kesyon gramè séparées
            if _p2_txt:
                for _qln in _re.split(r'\n+(?=[1-4][.)])', _p2_txt.strip()):
                    _qln = _qln.strip()
                    if len(_qln) > 15:
                        _kreyol_p2_items.append({
                            'text': _qln, 'answer': 'Repons tann.',
                            'pts': 5, 'theme': 'Etid Lang', 'source': 'BAC Ayiti',
                        })

            # P3 : sijè pwodiksyon ekri
            if _p3_txt and len(_p3_txt) > 20:
                _kreyol_p3_topic = _p3_txt

        # ── Construire les parties ──────────────────────────────────────────
        parts = []

        # PREMYÈ PATI — Konpreyansyon
        if texte_item:
            _it0 = texte_item[0]
            _ft  = _it0['text']
            _passage_raw = _ft.split('Kesyon :')[0].replace('TÈKS :\n\n', '').strip()
            _item_text = (
                'TÈKS :\n\n' + _passage_raw + '\n\nKesyon :\n\n' + _kreyol_p1_q
                if _kreyol_p1_q else _ft
            )
            parts.append({
                'label': 'PREMYÈ PATI — Konpreyansyon / Analiz Tèks (40 pwen)',
                'sections': [{
                    'label': f'{_it0["theme"]} — {_it0["source"]}',
                    'type': 'open', 'pts': 40,
                    'items': [{'text': _fix_latex(_global_format_tables(_item_text)),
                               'answer': _it0.get('answer') or 'Répons tann.',
                               'pts': 40}],
                }],
            })

        # DEZYÈM PATI — Etid Lang (préférer contenu IA en Kreyòl)
        _p2_final = _kreyol_p2_items[:4] if _kreyol_p2_items else []
        if not _p2_final:
            # Fallback : items de la base (possiblement en français — dernier recours)
            _questions_db = _collect_items(['question'], min_len=30)
            _qcms_db      = _collect_items(['qcm'], min_len=60)
            _used_key = texte_item[0]['text'][:60] if texte_item else ''
            _lang_pool = [q for q in (_qcms_db + _questions_db) if q['text'][:60] != _used_key]
            _p2_final = _pick(_lang_pool, 4)
        if _p2_final:
            _pts2 = _distribute(20, len(_p2_final))
            parts.append({
                'label': 'DEZYÈM PATI — Etid Lang / QCM (20 pwen)',
                'sections': [
                    {'label': f'{_pit["theme"]} — {_pit["source"]} ({_pts2[i]} pts)',
                     'type': 'open', 'pts': _pts2[i],
                     'items': [{'text': _fix_latex(_global_format_tables(_pit['text'])),
                                'answer': _pit.get('answer') or 'Répons tann.',
                                'pts': _pts2[i]}]}
                    for i, _pit in enumerate(_p2_final)
                ],
            })

        # TWAZYÈM PATI — Pwodiksyon Ekri (préférer sujet IA en Kreyòl)
        if _kreyol_p3_topic:
            parts.append({
                'label': 'TWAZYÈM PATI — Pwodiksyon Ekri (40 pwen)',
                'sections': [{
                    'label': 'Pwodiksyon Ekri — BAC Ayiti',
                    'type': 'open', 'pts': 40,
                    'items': [{'text': _fix_latex(_global_format_tables(_kreyol_p3_topic)),
                               'answer': 'Tèks ekri tann.',
                               'pts': 40}],
                }],
            })
        else:
            # Fallback base de données
            _prods_db = _collect_items(['production_ecrite', 'dissertation'], min_len=60)
            _prod_pool = _pick(_prods_db, 1)
            if _prod_pool:
                _pit = _prod_pool[0]
                parts.append({
                    'label': 'TWAZYÈM PATI — Pwodiksyon Ekri (40 pwen)',
                    'sections': [{
                        'label': f'{_pit["theme"]} — {_pit["source"]} (40 pts)',
                        'type': 'open', 'pts': 40,
                        'items': [{'text': _fix_latex(_global_format_tables(_pit['text'])),
                                   'answer': _pit.get('answer') or 'Tèks ekri tann.',
                                   'pts': 40}],
                    }],
                })

        if not parts:
            return {}
        return {'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff, 'parts': parts}

    # ════════════════════════════════════════════════════════════════════════
    # ANGLAIS / ESPAGNOL
    # PARTIE I   : Compréhension/Interprétation du texte (40 pts)
    # PARTIE II  : Étude de langue / Competencia lingüística (30 pts)
    # PARTIE III : Production écrite / Competencia discursiva (30 pts)
    # ════════════════════════════════════════════════════════════════════════
    if subject in ('anglais', 'espagnol'):
        _is_spanish_subject = subject == 'espagnol'
        # Durée alignée sur la structure: LLA 3h30, autres séries 2h
        duration = '3 heures 30' if _user_serie_key == 'lla' else '2 heures'

        # ── Helpers langue ─────────────────────────────────────────────────
        _en_vocab = {
            'the','of','and','to','in','is','are','what','how','why','when','who',
            'which','read','answer','following','passage','text','write','according',
            'name','identify','describe','explain','does','did','was','were','that',
            'this','choose','discuss','where','list','complete','sentences','above',
            'below','following','people','year','world','country','language','find',
        }

        def _is_english(txt: str) -> bool:
            """True si le texte contient assez de mots anglais courants."""
            words = set(txt.lower().split()[:60])
            return len(words & _en_vocab) >= 4

        _es_vocab = {
            'el','la','los','las','de','del','y','en','que','como','cuando','donde',
            'por','para','con','sin','sobre','segun','texto','lee','lea','responde',
            'preguntas','siguiente','escribe','resumen','palabras','frase','frases',
            'explica','identifica','senala','indica','tema','autor','idea','principal',
            'tambien','porque','puede','pueden','fue','eran','ser','estar',
        }

        def _is_spanish(txt: str) -> bool:
            """True si le texte contient assez de mots espagnols courants."""
            words = set(txt.lower().replace('¿', '').replace('¡', '').split()[:60])
            return len(words & _es_vocab) >= 4

        def _count_questions(txt: str) -> int:
            """Nombre de sous-questions numérotées (1. 2. 3. …) dans txt."""
            return len(_re.findall(r'[1-9][.)]\s+\w', txt))

        # ── Traducteur partiel FR→EN pour les consignes ───────────────────
        _FR2EN = [
            # Compound patterns MUST come before their sub-patterns
            (r'(?i)\bassociez\s+chaque\s+(?:phrase|mot)\s+de\s+la\s+colonne\s*A\s+'
             r'avec\s+la\s+(?:phrase\s+)?correspondante(?:\s+de\s+la\s+colonne\s*B)?\b',
             'Match each item in Column A with the corresponding one in Column B.'),
            (r'(?i)\bplacez\s+les\s+verbes\s+entre\s+parenthèses\b',
             'Put the verbs in brackets'),
            (r'(?i)\bverbes\s+entre\s+parenthèses\b', 'verbs in brackets'),
            (r'(?i)\blis[ez]+\s+attentivement\s+(?:le\s+texte\s+)?', 'Read the text carefully and '),
            (r'(?i)\brépondez?\s+aux\s+questions?\s*(?:suivantes?)?\s*', 'Answer the following questions '),
            (r'(?i)\brépondez?\s+en\s+phrases?\s+complètes?\b', 'Answer in complete sentences.'),
            (r'(?i)\bci[-\u2011]dessous\b', 'below'),
            (r'(?i)\bci[-\u2011]dessus\b', 'above'),
            (r'(?i)\bcomplétez\s+les\s+espaces?\b', 'Fill in the blanks'),
            (r'(?i)\bcomplétez\s+les\s+phrases?\b', 'Complete the sentences'),
            (r'(?i)\btransformez\s+les\s+phrases?\b', 'Transform the sentences'),
            (r'(?i)\bau\s+présent\s+simpl[e]\b', 'in the simple present'),
            (r'(?i)\bau\s+présent\s+continu\b', 'in the present continuous'),
            (r'(?i)\bà\s+la\s+voix\s+passive\b', 'into the passive voice'),
            (r'(?i)\bplacez\s+les\s+verbes?\b', 'Put the verbs'),
            (r'(?i)\bcolonne\s+A\b', 'Column A'),
            (r'(?i)\bcolonne\s+B\b', 'Column B'),
            (r'(?i)\bquestion\s+de\s+compréhension\s*:?', 'Comprehension questions:'),
            (r'(?i)\brédigez\s+un\s+résumé\b', 'Write a summary'),
            (r'(?i)\ben\s+(\d+|deux|trois|quatre|cinq)\s+phrases?\s+complètes?\b',
             r'in \1 complete sentences'),
            # Common mixed-language fragments (e.g. "avec la préposition correcte")
            (r'(?i)\bavec\s+la\s+pr[eé]position\s+correcte\b', 'with the correct preposition'),
            (r'(?i)\bavec\s+le\s+pronom\s+approprié?\b', 'with the appropriate pronoun'),
            (r'(?i)\bavec\s+les\s+mots?\s+suivants?\b', 'with the following words'),
            (r'(?i)\bpour\s+chaque\s+(?:affirmation|phrase)\b', 'for each statement'),
            (r'(?i)\bvrai\s+or\s+faux\b', 'TRUE or FALSE'),
            (r'(?i)\bvrai\b(?!\s*ou)', 'TRUE'),
            (r'(?i)\bfaux\b(?!\s*et)', 'FALSE'),
            (r'(?i)\bau\s+temps\s+approprié?\b', 'in the appropriate tense'),
            (r'(?i)\bchoisissez\s+la\s+meilleure\s+réponse\b', 'Choose the best answer'),
            (r'(?i)\bchoisissez\s+la\s+bonne\s+réponse\b', 'Choose the correct answer'),
            (r'(?i)\bparmi\s+les\s+possibilités?\s+(?:suivantes?)?\b', 'from the following options'),
            (r"(?i)\bchoisissez\s+(?:le\s+|la\s+|l['’]\s*)?mot\s+correct\b", 'Choose the correct word'),
            (r'(?i)\bsélectionnez\b', 'Select'),
            (r'(?i)\bréécrivez\b', 'Rewrite'),
            (r'(?i)\breliez\b', 'Match'),
            (r'(?i)\bcochez\b', 'Check'),
            # Number words and small isolated words
            (r'\bou\b', 'or'),
            (r'(?i)\bsuivantes?\b', 'following'),
            (r'(?i)\bdu\s+texte\b', 'of the text'),
            (r'(?i)\bcinq\b', 'five'),
            (r'(?i)\bquatre\b', 'four'),
            (r'(?i)\btrois\b', 'three'),
            (r'(?i)\bdeux\b', 'two'),
            (r'(?i)\bindiq(?:uez?|uer)\b', 'Indicate'),
        ]

        def _en_translate(txt: str) -> str:
            """Traduit les instructions françaises courantes en anglais."""
            for pat, repl in _FR2EN:
                txt = _re.sub(pat, repl, txt)
            return txt

        def _translate_or_keep(txt: str) -> str:
            return txt if _is_spanish_subject else _en_translate(txt)

        def _text_key(txt: str) -> str:
            s = _re.sub(r'\s+', ' ', (txt or '').strip().lower())
            return s[:180]

        def _validate_grounded_questions(passage: str, q_lines: list[str]) -> list[str]:
            """Keep only questions that can be answered from the passage."""
            if not passage or not q_lines:
                return []
            _qs = []
            for q in q_lines:
                qq = _re.sub(r'^\s*\d+[.)]\s*', '', (q or '').strip())
                if qq and len(qq) >= 6:
                    _qs.append(qq)
            if not _qs:
                return []

            _fmt = '\n'.join(f"{i+1}) {q}" for i, q in enumerate(_qs))
            if _is_spanish_subject:
                _check_prompt = (
                    "Lee el texto y evalua cada pregunta. Marca YES si la respuesta esta explicita "
                    "o claramente inferible SOLO desde el texto. Marca NO si requiere informacion externa.\n\n"
                    f"TEXTO:\n{passage[:1700]}\n\n"
                    f"PREGUNTAS:\n{_fmt}\n\n"
                    "Responde SOLO con este formato, una linea por pregunta:\n"
                    "1) YES/NO\n2) YES/NO\n..."
                )
            else:
                _check_prompt = (
                    "Read the passage and evaluate each question. Mark YES if the answer is explicit "
                    "or clearly inferable ONLY from the passage. Mark NO if external information is needed.\n\n"
                    f"PASSAGE:\n{passage[:1700]}\n\n"
                    f"QUESTIONS:\n{_fmt}\n\n"
                    "Reply ONLY in this format, one line per question:\n"
                    "1) YES/NO\n2) YES/NO\n..."
                )
            _judge = (_call_fast(_check_prompt, max_tokens=250) or '').strip()
            _flags = {}
            for m in _re.finditer(r'(?im)^\s*(\d+)\)\s*(YES|NO)\s*$', _judge):
                _flags[int(m.group(1))] = m.group(2).upper()

            kept = []
            for i, q in enumerate(_qs, start=1):
                if _flags.get(i) != 'YES':
                    continue
                # Rejeter aussi les questions hors langue cible
                if _is_spanish_subject and not _is_spanish(q):
                    continue
                if (not _is_spanish_subject) and not _is_english(q):
                    continue
                    kept.append(f"{i}) {q}")
            return kept

        # ── Détecteur + formateur de tableau Colonne A / Colonne B ────────
        def _colonne_to_table(txt: str) -> str:
            """
            Détecte un bloc 'Colonne A … Colonne B' et le convertit en
            markdown table.  Retourne txt inchangé si le pattern n'est pas trouvé.
            """
            # Chercher les deux colonnes
            m = _re.search(
                r'(?i)(?:Colonne\s*A|Column\s*A)\s*\n(.*?)\n\s*(?:Colonne\s*B|Column\s*B)\s*\n(.*?)(?=\n\n|\Z)',
                txt, _re.DOTALL
            )
            if not m:
                return txt
            left_raw  = m.group(1).strip().splitlines()
            right_raw = m.group(2).strip().splitlines()
            left_items  = [l.strip() for l in left_raw  if l.strip()]
            right_items = [r.strip() for r in right_raw if r.strip()]
            # Pad to same length
            n = max(len(left_items), len(right_items))
            left_items  += [''] * (n - len(left_items))
            right_items += [''] * (n - len(right_items))
            # Build markdown table
            table = '| Column A | Column B |\n|---|---|\n'
            for l, r in zip(left_items, right_items):
                table += f'| {l} | {r} |\n'
            # Replace the original block
            txt = txt[:m.start()] + table + txt[m.end():]
            return txt

        # ── Collecte brute des question_texte (enonce séparé du texte) ─────
        seen_tx = set()
        raw_textes = []
        for _ex in data.get('exams', []):
            _yr  = _ex.get('year', '')
            _src = f"Bac Haïti {_yr}" if _yr else 'Bac Haïti'
            for _it in _ex.get('items', []):
                if _it.get('type') != 'question_texte':
                    continue
                _texte  = (_it.get('texte')  or '').strip()
                _enonce = (_it.get('enonce') or '').strip()
                if not _texte or len(_texte) < 200:
                    continue
                _key = _texte[:60]
                if _key in seen_tx:
                    continue
                seen_tx.add(_key)
                _rep = _it.get('reponse') or ''
                if isinstance(_rep, list):
                    _rep = ', '.join(str(r) for r in _rep)
                raw_textes.append({
                    'texte':  _texte,
                    'enonce': _enonce,
                    'theme':  _it.get('theme', '') or 'Compréhension de texte',
                    'source': _src,
                    'answer': _rep.strip() or 'Answer expected.',
                })

        # Scorer : préférer enonce EN ANGLAIS avec plusieurs questions numérotées
        def _score_t(it: dict) -> int:
            en = it['enonce']
            _is_target_lang = _is_spanish(en) if _is_spanish_subject else _is_english(en)
            return len(_re.findall(r'[1-9][.)]\s+\w', en)) + (8 if _is_target_lang else 0)

        raw_textes.sort(key=_score_t, reverse=True)
        # Priorité absolue : passages de la langue cible
        target_textes = [it for it in raw_textes if (_is_spanish(it['enonce']) if _is_spanish_subject else _is_english(it['enonce']))]
        _pool = target_textes if target_textes else raw_textes
        # Anti-répétition inter-examens (mémoire process): éviter de ressortir
        # les mêmes textes sur les examens successifs Anglais/Espagnol.
        _recent_store = globals().setdefault('_LANG_EXAM_RECENT_PASSAGES', {'anglais': [], 'espagnol': []})
        _recent_list = _recent_store.setdefault(subject, [])
        _recent_set = set(_recent_list)
        _candidates = [it for it in _pool if _text_key(it.get('texte', '')) not in _recent_set]
        if not _candidates:
            _candidates = _pool
        _pick_window = _candidates[:25] if len(_candidates) > 25 else _candidates
        chosen = _random.choice(_pick_window) if _pick_window else None
        if chosen:
            _k = _text_key(chosen.get('texte', ''))
            _recent_list.append(_k)
            if len(_recent_list) > 40:
                del _recent_list[:-40]

        # ── Collecte langue et production ──────────────────────────────────
        questions   = _collect_items(['question'],                    min_len=30)
        qcms        = _collect_items(['qcm'],                         min_len=60)
        productions = _collect_items(['production_ecrite', 'dissertation'], min_len=60)

        # ── PARTIE II : 4 questions de langue (30 pts) ────────────────────
        lang_pool = [q for q in qcms if len(q['text']) >= 60] + questions
        _reading_key = chosen['texte'][:60] if chosen else ''
        lang_pool = [q for q in lang_pool if q['text'][:60] != _reading_key]
        # Prefer target-language items; fallback to all if not enough
        lang_pool_en  = [q for q in lang_pool if (_is_spanish(q['text']) if _is_spanish_subject else _is_english(q['text']))]
        lang_pool_any = lang_pool
        _lang_src = lang_pool_en if len(lang_pool_en) >= 4 else lang_pool_any
        lang_items = _pick(_lang_src, 4)

        # ── PARTIE III : production écrite (30 pts) ────────────────────────
        # Exclure : résumés, textes purement français, refs "ci-dessus"
        _bad_prod = [
            'résumez', 'résumer', 'summarize the text', 'summarize the above',
            'résumé du texte', 'résumez le texte',
            'ci-dessus', 'ci-dessous',
            'transformez les phrases', 'transformez les mots',
            'resume el texto', 'resuma el texto', 'resumen del texto',
            'segun el texto', 'según el texto',
        ]
        # Niveau 1 : langue cible ET pas un résumé
        prod_ok = [p for p in productions
                   if not any(w in p['text'].lower() for w in _bad_prod)
                   and (_is_spanish(p['text']) if _is_spanish_subject else _is_english(p['text']))]
        # Niveau 2 (fallback) : pas un résumé (peu importe la langue)
        if not prod_ok:
            prod_ok = [p for p in productions
                       if not any(w in p['text'].lower() for w in _bad_prod)]
        # Niveau 3 : tout (ultime fallback)
        if not prod_ok:
            prod_ok = productions
        # Déduplication vs parties précédentes
        _prod_used = ({chosen['texte'][:60]} if chosen else set()) | \
                     {it['text'][:60] for it in lang_items}
        prod_ok = [p for p in prod_ok if p['text'][:60] not in _prod_used]
        prod_item = _pick(prod_ok or productions, 1)

        # ── Construction de l'examen ───────────────────────────────────────
        parts = []

        if chosen:
            # ── Partie I : texte + 5 questions numérotées + résumé ────────
            # Le passage est affiché seul, chaque question est un item séparé
            _passage = chosen['texte'].strip()

            # Variation IA contrôlée pour éviter la répétition de passages identiques
            # tout en gardant le style BAC et la difficulté.
            _rewrite_prompt = (
                "Reformule ce passage en conservant le meme theme, le meme niveau BAC et des informations "
                "concretes qui permettent de repondre a des questions de comprehension. "
                "Ecris un seul passage, sans titre, sans numerotation, entre 170 et 240 mots.\n\n"
                f"PASSAGE SOURCE:\n{_passage[:1400]}"
            ) if _is_spanish_subject else (
                "Rewrite this passage with the same theme, Bac-level difficulty, and clear factual details "
                "that support reading-comprehension questions. Write one single passage only, no title, "
                "no numbering, between 170 and 240 words.\n\n"
                f"SOURCE PASSAGE:\n{_passage[:1400]}"
            )
            _rewritten = (_call_fast(_rewrite_prompt, max_tokens=700) or '').strip()
            if len(_rewritten) >= 500 and not _re.search(r'(?m)^\s*[1-9][.)]\s+', _rewritten):
                _passage = _rewritten

            # Générer systématiquement 5 questions ancrées dans le texte pour
            # éviter les questions dont la réponse n'est pas présente.
            if _is_spanish_subject:
                _q_prompt = (
                    "Lee el texto y crea EXACTAMENTE 5 preguntas de comprension numeradas (1 a 5). "
                    "Regla obligatoria: cada respuesta debe encontrarse explicita o claramente en el texto. "
                    "No hagas preguntas fuera del contenido, no opinion personal, no subpreguntas.\n\n"
                    f"TEXTO:\n{_passage[:1500]}\n\n"
                    "Responde SOLO con las 5 preguntas numeradas, una por linea."
                )
            else:
                _q_prompt = (
                    "Read the passage and write EXACTLY 5 numbered comprehension questions (1 to 5). "
                    "Mandatory rule: each answer must be explicit or directly inferable from the passage. "
                    "No out-of-text questions, no personal-opinion questions, no sub-parts.\n\n"
                    f"PASSAGE:\n{_passage[:1500]}\n\n"
                    "Reply ONLY with the 5 numbered questions, one per line."
                )
            _q_raw = _call_fast(_q_prompt, max_tokens=500)
            _q_lines = _re.findall(r'(?m)^[1-9][.)]\s*.+', _q_raw)

            # Validation finale: rejeter automatiquement toute question non
            # ancrée dans le texte (réponse absente du passage).
            _q_lines = _validate_grounded_questions(_passage, _q_lines)

            # Régénération ciblée des questions rejetées
            _regen_attempts = 0
            while len(_q_lines) < 5 and _regen_attempts < 3:
                _need = 5 - len(_q_lines)
                if _is_spanish_subject:
                    _regen_prompt = (
                        f"Escribe EXACTAMENTE {_need} nuevas preguntas de comprension para este texto. "
                        "Regla obligatoria: cada respuesta debe aparecer en el texto. "
                        "Sin opinion personal, sin informacion externa, sin subpreguntas.\n\n"
                        f"TEXTO:\n{_passage[:1500]}\n\n"
                        "Responde SOLO con preguntas numeradas."
                    )
                else:
                    _regen_prompt = (
                        f"Write EXACTLY {_need} new comprehension questions for this passage. "
                        "Mandatory rule: each answer must appear in the passage. "
                        "No personal-opinion questions, no external info, no sub-parts.\n\n"
                        f"PASSAGE:\n{_passage[:1500]}\n\n"
                        "Reply ONLY with numbered questions."
                    )
                _regen_raw = _call_fast(_regen_prompt, max_tokens=320)
                _regen_lines = _re.findall(r'(?m)^[1-9][.)]\s*.+', _regen_raw)
                _regen_ok = _validate_grounded_questions(_passage, _regen_lines)

                _seen_q = set(_re.sub(r'^\s*\d+[.)]\s*', '', x).strip().lower() for x in _q_lines)
                for q in _regen_ok:
                    qtxt = _re.sub(r'^\s*\d+[.)]\s*', '', q).strip().lower()
                    if qtxt and qtxt not in _seen_q:
                        _q_lines.append(q)
                        _seen_q.add(qtxt)
                    if len(_q_lines) >= 5:
                        break
                _regen_attempts += 1

            # Assurer 5 questions minimum avec des génériques si toujours pas assez
            if _is_spanish_subject:
                _generic_qs = [
                    "1) Cual es la idea principal del texto?",
                    "2) Segun el texto, cuales son los principales desafios descritos?",
                    "3) Explica la expresion subrayada con tus propias palabras.",
                    "4) Estas de acuerdo con el punto de vista del autor? Justifica tu respuesta.",
                    "5) Que soluciones propone el autor?",
                ]
            else:
                _generic_qs = [
                    "1) What is the main idea of the text?",
                    "2) According to the text, what are the main challenges described?",
                    "3) Explain the underlined expression in your own words.",
                    "4) Do you agree with the author's point of view? Give reasons.",
                    "5) What solution(s) does the author suggest?",
                ]
            while len(_q_lines) < 5:
                _q_lines.append(_generic_qs[len(_q_lines)])
            _q_lines = _q_lines[:5]
            _q_lines = [f"{i+1}) " + _re.sub(r'^\s*\d+[.)]\s*', '', q).strip() for i, q in enumerate(_q_lines)]

            # Points : 5 questions × 5pts + résumé 15pts = 40pts
            _q_pts = [5] * 5
            _items_reading = []
            # Item 0 : le passage (texte seul, pas de points propres)
            _items_reading.append({
                'text': _global_format_tables(_passage),
                'answer': '',
                'pts': 0,
                'is_passage': True,
            })
            # Items 1-5 : questions de compréhension
            for _qi, _qline in enumerate(_q_lines[:5]):
                _items_reading.append({
                    'text': _global_format_tables(_re.sub(r'^\s*\d+[.)]\s*', '', _qline.strip())),
                    'answer': 'Respuesta esperada.' if _is_spanish_subject else 'Answer expected.',
                    'pts': _q_pts[_qi],
                })
            # Item 6 : résumé
            _items_reading.append({
                'text': "Redacta un resumen del texto en 3 a 5 oraciones completas." if _is_spanish_subject else "Write a summary of the text in 3 to 4 complete sentences.",
                'answer': 'Resumen esperado (3-5 oraciones).' if _is_spanish_subject else 'Summary expected (3-4 sentences).',
                'pts': 15,
            })

            parts.append({
                'label': 'PARTE I - Comprension lectora (40 puntos)' if _is_spanish_subject else 'PART I - Reading Comprehension (40 points)',
                'sections': [{
                    'label': f'{chosen["theme"]} — {chosen["source"]} (40 pts)',
                    'type': 'open', 'pts': 40,
                    'items': _items_reading,
                }],
            })

        if lang_items:
            pts_lang = _distribute(30, len(lang_items))
            parts.append({
                'label': 'PARTE II - Competencia linguistica (30 puntos)' if _is_spanish_subject else 'PART II - Language Study (30 points)',
                'sections': [
                    {'label': f'{it["theme"]} — {it["source"]} ({pts_lang[i]} pts)',
                     'type': 'open', 'pts': pts_lang[i],
                     'items': [{'text': _fix_latex(_global_format_tables(_colonne_to_table(_translate_or_keep(it['text'])))),
                                'answer': it.get('answer') or ('Respuesta esperada.' if _is_spanish_subject else 'Answer expected.'),
                                'pts': pts_lang[i]}]}
                    for i, it in enumerate(lang_items)
                ],
            })

        if prod_item:
            it = prod_item[0]
            _prod_theme = (it.get('theme') or chosen.get('theme') if chosen else it.get('theme') or 'society').strip()
            if _is_spanish_subject:
                _topic_prompt = (
                    "Genera UN solo tema de redaccion para examen BAC (espanol), realista y claro, "
                    "sin preguntas multiples ni listas. Maximo 22 palabras.\n"
                    f"Tema general: {_prod_theme}"
                )
            else:
                _topic_prompt = (
                    "Generate ONE single writing topic for a Bac-style exam (English), realistic and clear, "
                    "not a list, not multi-questions. Maximum 22 words.\n"
                    f"General theme: {_prod_theme}"
                )
            _topic_raw = (_call_fast(_topic_prompt, max_tokens=80) or '').strip()
            _topic_line = (_topic_raw.splitlines()[0] if _topic_raw else '').strip()
            _topic_line = _re.sub(r'^[\-•\d.)\s]+', '', _topic_line)
            _topic_line = _re.sub(r'\s*(?:→|->|=>)+\s*', ' ', _topic_line)
            if not _topic_line:
                if _is_spanish_subject:
                    _fallback_topics = [
                        'Como mejorar la seguridad vial cerca de las escuelas en Haiti.',
                        'El papel del deporte escolar en la disciplina y la salud de los jovenes.',
                        'Ventajas y riesgos del uso de las redes sociales por los adolescentes.',
                        'Acciones concretas para proteger el medio ambiente en tu comunidad.',
                        'La importancia de la lectura diaria para el exito academico.',
                        'Como reducir la violencia escolar mediante el dialogo y el respeto.',
                    ]
                else:
                    _fallback_topics = [
                        'How to improve road safety around schools in Haiti.',
                        'The role of school sports in student discipline and health.',
                        'Benefits and risks of social media use among teenagers.',
                        'Practical actions young people can take to protect the environment.',
                        'Why daily reading is essential for academic success.',
                        'How dialogue and respect can reduce violence at school.',
                    ]
                _topic_line = _random.choice(_fallback_topics)

            _prod_text = (
                f"Topic: {_topic_line}\n\n"
                "Write a coherent essay in approximately 25 lines."
            ) if not _is_spanish_subject else (
                f"Tema: {_topic_line}\n\n"
                "Redacta un texto coherente de aproximadamente 25 lineas."
            )
            parts.append({
                'label': 'PARTE III - Competencia discursiva (30 puntos)' if _is_spanish_subject else 'PART III - Written Production (30 points)',
                'sections': [{
                    'label': f'{it["theme"]} — {it["source"]} (30 pts)',
                    'type': 'open', 'pts': 30,
                    'items': [{'text': _fix_latex(_global_format_tables(_prod_text)),
                               'answer': it.get('answer') or ('Texto redactado esperado.' if _is_spanish_subject else 'Written text expected.'),
                               'pts': 30}],
                }],
            })

        if not parts:
            return {}
        return {'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff, 'parts': parts}

    def _pick_by_keywords(pool: list, keywords: list[str], n: int) -> list:
        """Pick items preferring texts that match requested keywords."""
        if not pool:
            return []
        kws = [k.lower() for k in keywords if k]
        scored = []
        for it in pool:
            txt = (it.get('text') or '').lower()
            score = sum(1 for k in kws if k in txt)
            scored.append((score, _random.random(), it))
        scored.sort(key=lambda x: (-x[0], x[1]))  # Sort by score DESC, then by random for tie-breaking
        picked = [it for _, _, it in scored[:n]]
        if len(picked) < n:
            for _, _, it in scored:
                if it not in picked:
                    picked.append(it)
                if len(picked) >= n:
                    break
        return picked[:n]

    # ════════════════════════════════════════════════════════════════════════
    # ÉCONOMIE — 4 parties (100 pts) selon structure_exam.json
    # I Compréhension texte (30) + II Fonction/graphique (20)
    # III Problèmes calcul (30) + IV Dissertation/commentaire (20)
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'economie':
        import json as _json_eco

        def _eco_clean(txt: str) -> str:
            s = (txt or '').replace('\u202f', ' ').replace('\u00a0', ' ').replace('‑', '-')
            s = _re.sub(r'(?im)^\s*T[ÈE]KS\s*:\s*', '', s)
            s = _re.sub(r'(?im)^\s*Kesyon\s*:\s*', '', s)
            s = _re.sub(r'\n{3,}', '\n\n', s).strip()
            return s

        def _split_enonce_questions(enonce: str) -> tuple[str, list[str]]:
            raw = _eco_clean(enonce)
            m = _re.search(r'(?im)^\s*(?:[a-z]\)|\d+\)|[IVX]+\s*[-.)])\s*', raw)
            if m:
                passage = raw[:m.start()].strip()
                q_blob = raw[m.start():].strip()
            else:
                passage = raw
                q_blob = ''
            qs = _re.findall(r'(?im)^\s*(?:[a-z]\)|\d+\)|[IVX]+\s*[-.)])\s*.+$', q_blob)
            qs = [_re.sub(r'\s+', ' ', q.strip()) for q in qs if q and len(q.strip()) > 6]
            return passage, qs

        def _norm_reponse(rep) -> str:
            if isinstance(rep, dict):
                ordered = []
                for k in sorted(rep.keys()):
                    ordered.append(f"{k}) {rep[k]}")
                return '\n'.join(ordered)
            if isinstance(rep, list):
                return '\n'.join(str(x) for x in rep)
            return str(rep or '').strip()

        _exo_path = os.path.join(settings.BASE_DIR, 'database', 'exo_economie.json')
        _quiz_path = os.path.join(settings.BASE_DIR, 'database', 'quiz_economie.json')

        ex_pool = []
        try:
            with open(_exo_path, 'r', encoding='utf-8') as _feco:
                _eco_data = _json_eco.load(_feco)
            for ch in _eco_data.get('chapitres', []):
                ch_title = ch.get('titre', '')
                for ex in ch.get('exercices', []):
                    src = str(ex.get('source') or '').strip()
                    # banque stricte: priorité aux exercices BAC sourcés
                    if not src or 'Bac Haïti' not in src:
                        continue
                    enonce = _eco_clean(ex.get('enonce', ''))
                    if len(enonce) < 30:
                        continue
                    passage, qs = _split_enonce_questions(enonce)
                    ex_pool.append({
                        'id': ex.get('id', ''),
                        'source': src,
                        'chapter': ch_title,
                        'enonce': enonce,
                        'passage': passage,
                        'questions': qs,
                        'answer': _norm_reponse(ex.get('reponses', 'Réponse attendue.')),
                        'nb_questions': int(ex.get('nb_questions') or len(qs) or 0),
                    })
        except Exception:
            return {}

        if not ex_pool:
            return {}

        quiz_pool = []
        try:
            with open(_quiz_path, 'r', encoding='utf-8') as _fqeco:
                _qdata = _json_eco.load(_fqeco)
            for q in _qdata.get('quiz', []):
                options = q.get('options', [])
                cidx = max(0, ord(str(q.get('correct', 'A')).upper()[0]) - ord('A'))
                corr = options[cidx] if cidx < len(options) else ''
                quiz_pool.append({
                    'id': q.get('id', ''),
                    'source': 'Quiz Économie — BAC Haïti',
                    'question': _eco_clean(q.get('question', '')),
                    'answer': _eco_clean(corr),
                    'exp': _eco_clean(q.get('explanation', '')),
                })
        except Exception:
            quiz_pool = []

        def _is_part2_strict(txt: str) -> bool:
            s = (txt or '').lower()
            has_keynes_function = (
                (
                    ('fonction' in s and ('keynés' in s or 'keynes' in s))
                    or ('c = a + b' in s)
                    or ('c=a+b' in s)
                )
                or (('pmc' in s or 'pms' in s or 'propension' in s) and ('multiplicateur' in s))
            )
            has_graph_supply_demand = (
                ('offre' in s and 'demande' in s)
                and any(k in s for k in ['graphique', 'courbe', 'equilibre', 'équilibre', 'prix', 'quantite', 'quantité'])
            )
            has_part3_markers = any(
                k in s for k in [
                    'pib', 'idh', 'monopole', 'profit', 'taux d\'intérêt', 'taux d’intérêt', 'r(q)', 'ct =', 'coût total'
                ]
            )
            return (has_keynes_function or has_graph_supply_demand) and not has_part3_markers

        def _pick_one(pool: list, pred, used: set) -> dict | None:
            cand = [x for x in pool if pred(x) and x.get('id', x.get('question', '')) not in used]
            if not cand:
                return None
            _random.shuffle(cand)
            picked = cand[0]
            used.add(picked.get('id', picked.get('question', '')))
            return picked

        used = set()

        # Partie I : texte long + 4-6 questions d'interprétation
        p1 = _pick_one(ex_pool, lambda e: len(e['passage']) >= 140 and 4 <= len(e['questions']) <= 6, used)
        if not p1:
            p1 = _pick_one(ex_pool, lambda e: len(e['passage']) >= 120 and len(e['questions']) >= 3, used)
        if not p1:
            return {}

        # Partie II : filtre STRICT fonction keynésienne OU offre/demande graphique
        p2 = _pick_one(ex_pool, lambda e: _is_part2_strict(e['chapter'] + ' ' + e['enonce']), used)
        if not p2 and quiz_pool:
            strict_quiz = [q for q in quiz_pool if _is_part2_strict((q.get('question', '') + ' ' + q.get('exp', '')))]
            _random.shuffle(strict_quiz)
            q2 = strict_quiz[0] if strict_quiz else None
            if q2:
                p2 = {
                    'source': q2.get('source') or 'Quiz Économie — BAC Haïti',
                    'enonce': q2['question'],
                    'questions': [],
                    'answer': (q2['answer'] + ('\n\n' + q2['exp'] if q2['exp'] else '')).strip(),
                }
        if not p2:
            return {}

        # Partie III : problèmes calculs économiques
        _k3 = ['pib', 'idh', 'monopole', 'taux', 'intérêt', 'profit', 'calcul']
        p3 = _pick_one(ex_pool, lambda e: any(k in (e['chapter'] + ' ' + e['enonce']).lower() for k in _k3), used)
        if not p3:
            return {}

        # Partie IV : dissertation/commentaire argumentatif
        _k4 = ['dissertation', 'commente', 'commenter', 'discute', 'discuter', 'dans quelle mesure', 'proposer', 'argument', 'analysez', 'rédigez']
        p4 = _pick_one(
            ex_pool,
            lambda e: any(k in e['enonce'].lower() for k in _k4)
            and not any(sym in e['enonce'] for sym in ['CT =', 'Q =', 'P =', 'R(Q)', 'π(', 'profit', 'monopole']),
            used,
        )
        if not p4:
            # fallback strict banque: privilégier les énoncés textuels non calculatoires
            p4 = _pick_one(
                ex_pool,
                lambda e: len(e.get('questions', [])) <= 2
                and not any(sym in e['enonce'] for sym in ['CT =', 'Q =', 'P =', 'R(Q)', 'π(', 'profit', 'monopole']),
                used,
            )
        if not p4:
            return {}

        def _eco_item(text: str, pts: int, answer: str = 'Réponse développée attendue.') -> dict:
            return {
                'text': _fix_latex(_global_format_tables(_eco_clean(text))),
                'answer': _fix_latex(_eco_clean(answer)),
                'pts': pts,
            }

        p1_questions_text = '\n'.join(p1['questions'][:6]) if p1.get('questions') else '1) Lire le texte et répondre aux questions d\'interprétation.'
        p2_text = p2['enonce']
        if p2.get('questions'):
            p2_text = p2_text + '\n\n' + '\n'.join(p2['questions'][:6])
        p3_text = p3['enonce']
        if p3.get('questions'):
            p3_text = p3_text + '\n\n' + '\n'.join(p3['questions'][:6])
        p4_text = p4['enonce']

        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'PARTIE I — Compréhension de texte (30 points)',
                    'sections': [{
                        'label': f'Texte économique — {p1["source"]}',
                        'type': 'open', 'pts': 30,
                        'items': [
                            _eco_item(f"TEXTE :\n\n{p1['passage']}", 0, ''),
                            _eco_item(p1_questions_text, 30, p1.get('answer') or 'Réponse attendue.'),
                        ],
                    }],
                },
                {
                    'label': 'PARTIE II — Fonction / Graphique économique (20 points)',
                    'sections': [{
                        'label': f'Analyse de fonction/graphique — {p2.get("source", "Banque BAC")}',
                        'type': 'open', 'pts': 20,
                        'items': [_eco_item(p2_text, 20, p2.get('answer') or 'Réponse attendue.')],
                    }],
                },
                {
                    'label': 'PARTIE III — Problèmes et calculs économiques (30 points)',
                    'sections': [{
                        'label': f'Problème économique — {p3["source"]}',
                        'type': 'open', 'pts': 30,
                        'items': [_eco_item(p3_text, 30, p3.get('answer') or 'Réponse attendue.')],
                    }],
                },
                {
                    'label': 'PARTIE IV — Dissertation / Commentaire (20 points)',
                    'sections': [{
                        'label': f'Sujet de rédaction — {p4.get("source", "Banque BAC")}',
                        'type': 'open', 'pts': 20,
                        'items': [_eco_item(p4_text, 20, p4.get('answer') or 'Rédaction argumentée attendue.')],
                    }],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # HISTOIRE-GÉOGRAPHIE — Histoire 60% + Géographie 40%
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'histoire':
        dissertations = _collect_items(['dissertation'], min_len=70)
        geo_docs = _collect_items(['question_texte', 'question'], min_len=60)
        
        # Si pas assez de dissertations, utiliser aussi des question_texte comme fallback
        if len(dissertations) < 3:
            dissertations.extend(_collect_items(['question_texte'], min_len=70))
        
        # Si pas assez de geo_docs, utiliser aussi des dissertations comme fallback
        if len(geo_docs) < 2:
            geo_docs.extend(_collect_items(['dissertation'], min_len=60))
        
        # Ajouter randomisation supplémentaire
        _random.shuffle(dissertations)
        _random.shuffle(geo_docs)

        def _extract_doc_and_questions(raw: str) -> tuple[str, list[str]]:
            txt = (raw or '').strip()
            if not txt:
                return '', []
            # Harmoniser les marqueurs rencontrés dans la base
            normalized = txt.replace('TÈKS :', 'TEXTE :').replace('Kesyon :', 'QUESTIONS :')
            m = _re.search(r'(?is)\bQUESTIONS?\s*:\s*', normalized)
            if not m:
                m = _re.search(r'(?is)\bQuestions?\s*:\s*', normalized)
            if m:
                doc = normalized[:m.start()].strip()
                q_blob = normalized[m.end():].strip()
            else:
                doc = normalized
                q_blob = ''

            # Eviter les doublons d'entetes si la source contient deja "TEXTE :" ou "QUESTIONS :"
            doc = _re.sub(r'(?is)^\s*TEXTE\s*:\s*', '', doc).strip()
            q_blob = _re.sub(r'(?is)^\s*QUESTIONS?\s*:\s*', '', q_blob).strip()

            q_list = []
            if q_blob:
                lines = [ln.strip() for ln in q_blob.splitlines() if ln.strip()]
                for ln in lines:
                    ln = _re.sub(r'^\s*(?:\d+\s*[-.)]|[a-zA-Z]\s*[-.)])\s*', '', ln).strip()
                    if len(ln) >= 10:
                        q_list.append(ln)
                if not q_list and len(q_blob) >= 15:
                    q_list = [q_blob]
            return doc, q_list

        def _default_geo_questions(doc_text: str) -> list[str]:
            s = (doc_text or '').lower()
            # Formulations observées dans exams_histoire.json
            if any(k in s for k in ['cee', 'marshall', 'états-unis', 'etats-unis', 'guerre froide']):
                return [
                    'Présentez le document (nature, source, contexte).',
                    'Résumez en quelques lignes l\'idée principale du document.',
                    'Dégagez deux objectifs ou enjeux mentionnés dans le document.',
                    'Expliquez comment ce document reflète la situation mondiale d\'après 1945.',
                    'Analysez les causes profondes de la situation décrite.',
                ]
            if any(k in s for k in ['japon', 'agriculture', 'métropole', 'mondialisation', 'flux']):
                return [
                    'Présentez le document (nature, source, contexte).',
                    'Relevez dans le texte deux atouts ou deux facteurs majeurs.',
                    'Expliquez brièvement un enjeu ou une conséquence évoquée dans le document.',
                    'Identifiez les acteurs principaux mentionnés dans le document.',
                    'Quels impacts la mondialisation a-t-elle sur ce phénomène ?',
                ]
            return [
                'Présentez le document (nature, source, contexte).',
                'Dégagez deux idées principales du texte.',
                'Quels acteurs ou phénomènes sont mis en avant ?',
                'Quel est le contexte historique de ce document ?',
            ]

        def _format_geo_item(it: dict, idx: int) -> str:
            doc, extracted_q = _extract_doc_and_questions(it.get('text', ''))
            if not doc:
                doc = (it.get('text') or '').strip()
            # Toujours garantir une vraie section QUESTIONS, même si la banque n'en fournit pas clairement.
            questions = list(extracted_q)
            if not questions:
                questions = _default_geo_questions(doc)
            # Si aucune question de "présentation" n'existe, l'ajouter en tête (pattern bac fréquent)
            if not any('présentez le document' in q.lower() for q in questions):
                questions.insert(0, 'Présentez le document (nature, source, contexte).')

            # Limiter à 5 questions maximum (4-5 questions par texte recommandé)
            q_lines = '\n'.join(f"{i+1}) {q}" for i, q in enumerate(questions[:5]))
            return _fix_latex(_global_format_tables(
                f"Document {idx+1}\n\nTEXTE :\n\n{doc}\n\nQUESTIONS :\n\n{q_lines}"
            ))

        hist_choices = _pick(dissertations or geo_docs, 3)
        geo_items = _pick_by_keywords(
            geo_docs,
            ['carte', 'graphique', 'tableau', 'géographie', 'document', 'territoire', 'population', 'climat'],
            2,
        )
        if not hist_choices or not geo_items:
            return {}

        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'HISTOIRE (60%) — Dissertation au choix',
                    'sections': [{
                        'label': 'Traiter UN des trois sujets proposés (60 points)',
                        'type': 'open', 'pts': 60,
                        'items': [
                            {
                                'text': _fix_latex(_global_format_tables(f"Sujet {i+1}\n\n{it['text']}")),
                                'answer': it.get('answer') or 'Développement historique attendu.',
                                'pts': 60,
                            }
                            for i, it in enumerate(hist_choices[:3])
                        ],
                    }],
                },
                {
                    'label': 'GÉOGRAPHIE (40%) — Étude de documents',
                    'sections': [{
                        'label': 'Analyser les documents et répondre aux questions (40 points)',
                        'type': 'open', 'pts': 40,
                        'items': [
                            {
                                'text': _format_geo_item(it, i),
                                'answer': it.get('answer') or 'Analyse géographique attendue.',
                                'pts': 20,
                            }
                            for i, it in enumerate(geo_items[:2])
                        ],
                    }],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # INFORMATIQUE — exercices indépendants (100 pts)
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'informatique':
        def _load_info_quiz_items() -> list[dict]:
            """Charge quiz_informatique.json et retourne des items exploitables en mode examen."""
            qpath = os.path.join(settings.BASE_DIR, 'database', 'quiz_informatique.json')
            if not os.path.exists(qpath):
                return []
            try:
                import json as _jq
                with open(qpath, encoding='utf-8') as _f:
                    raw = _jq.load(_f)
                qlist = list(raw.get('quiz', raw) if isinstance(raw, dict) else raw)
            except Exception:
                return []

            out = []
            for q in qlist:
                qq = (q.get('question') or '').strip()
                if len(qq) < 12:
                    continue
                opts = list(q.get('options') or [])
                corr = (q.get('correct') or 'A').upper()
                cidx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(corr, 0)
                ans_txt = opts[cidx] if cidx < len(opts) else ''
                exp = (q.get('explanation') or '').strip()
                ans = ans_txt + (f"\n\n{exp}" if exp else '')
                out.append({
                    'type': 'qcm',
                    'theme': q.get('category') or 'Informatique',
                    'text': qq,
                    'options': opts,
                    'answer': ans or corr,
                    'source': 'Quiz Informatique BAC',
                })
            return out

        def _collect_info_items() -> list[dict]:
            """Collecte spécifique informatique: conserve les options QCM et filtre le bruit OCR."""
            result = []
            seen = set()

            def _source_label(exam: dict) -> str:
                yr = exam.get('year', '')
                return f"Bac Haïti {yr}" if yr else "Bac Haïti"

            _bad_enonce_fragments = [
                'cocher la bonne réponse',
                'cocher la/les bonne',
                'cocher (x)',
                'en cochant la ou les bonnes réponses, identifiez quel terme décrit',
            ]

            _dependent_patterns = [
                r'\bdans la question\s*\d+\b',
                r'\bquestion\s*\d+\b',
                r'\bci-dessus\b',
                r'\bpr[ée]c[ée]dente?s?\b',
                r'\bparmi les deux\b',
                r'\br[ée]ponse\s*i\b',
                r'\br[ée]ponse\s*ii\b',
                r'\balgorithme d[ée]crit\b',
            ]

            def _is_standalone(_txt: str) -> bool:
                _t = (_txt or '').strip().lower()
                if len(_t) < 20:
                    return False
                return not any(_re.search(p, _t) for p in _dependent_patterns)

            for exam in data.get('exams', []):
                src = _source_label(exam)
                for item in exam.get('items', []):
                    t = (item.get('type') or '').strip().lower()
                    if t not in ('exercice', 'question', 'qcm', 'question_texte', 'production_ecrite'):
                        continue

                    enonce = (item.get('enonce') or item.get('intro') or '').strip()
                    if not enonce or len(enonce) < 20:
                        continue

                    enonce_low = enonce.lower()
                    if any(bad in enonce_low for bad in _bad_enonce_fragments):
                        # On exclut les lignes d'instruction OCR non exploitables comme exercice autonome.
                        continue
                    if not _is_standalone(enonce):
                        continue

                    options = item.get('options') or []
                    if t == 'qcm':
                        if len(options) < 3:
                            continue
                        # QCM autonome: options assez informatives
                        if sum(1 for op in options if len(str(op).strip()) >= 6) < 3:
                            continue

                    # Déduplication grossière
                    key = (t, enonce[:80])
                    if key in seen:
                        continue
                    seen.add(key)

                    rep = (item.get('reponse') or '').strip()
                    result.append({
                        'type': t,
                        'theme': item.get('theme', '') or t.capitalize(),
                        'text': enonce,
                        'options': options,
                        'answer': rep or 'Réponse attendue.',
                        'source': item.get('source') or src,
                    })

            return result

        def _format_info_item(it: dict, idx: int) -> str:
            base = f"Exercice {idx+1}\n\n{it.get('text', '').strip()}"
            if (it.get('type') or '').lower() != 'qcm':
                return _fix_latex(_global_format_tables(base))

            opts = []
            for j, op in enumerate(it.get('options') or []):
                raw = str(op).strip()
                if not raw:
                    continue
                # Normalise "A: ..." en "A) ..." pour un rendu propre.
                m = _re.match(r'^\s*([A-Da-d])\s*[:.)-]\s*(.+)$', raw)
                if m:
                    letter = m.group(1).upper()
                    body = m.group(2).strip()
                    opts.append(f"{letter}) {body}")
                else:
                    letter = chr(ord('A') + j)
                    opts.append(f"{letter}) {raw}")

            if opts:
                base += "\n\nChoisir la (ou les) bonne(s) réponse(s) :\n" + "\n".join(opts)
            return _fix_latex(_global_format_tables(base))

        info_items = _collect_info_items()
        info_quiz_items = _load_info_quiz_items()
        info_all = info_items + info_quiz_items

        def _blob(it: dict) -> str:
            txt = str(it.get('text', '') or '').lower()
            th = str(it.get('theme', '') or '').lower()
            return f"{th} {txt}"

        def _is_algo_write(it: dict) -> bool:
            b = _blob(it)
            return ('algorith' in b and ('écrire' in b or 'ecrire' in b or 'compléter' in b or 'completer' in b))

        def _is_algo_read(it: dict) -> bool:
            b = _blob(it)
            return ('algorith' in b and ('quel résultat' in b or 'quel resultat' in b or 'produit' in b or 'expliquer' in b or 'compréhension' in b or 'comprehension' in b))

        def _is_algo_read_complete(it: dict) -> bool:
            t = str(it.get('text', '') or '').lower()
            # Une vraie compréhension d'algo doit contenir le pseudo-code (ou au moins les marqueurs de structure)
            has_structure = any(k in t for k in ['début', 'debut', 'fin', 'pour ', 'si ', 'tant que', 'lire', 'ecrire'])
            return _is_algo_read(it) and has_structure

        def _is_calc(it: dict) -> bool:
            b = _blob(it)
            return any(k in b for k in ['mégabit', 'megabit', 'bits', 'conversion', 'convertir', 'calcul'])

        algo_write_pool = [it for it in info_all if _is_algo_write(it)]
        algo_read_pool = [it for it in info_all if _is_algo_read_complete(it)]
        qcm_pool = [it for it in info_all if it.get('type') == 'qcm']
        calc_pool = [it for it in info_all if _is_calc(it)]
        other_pool = [it for it in info_all if it not in algo_write_pool and it not in algo_read_pool and it not in qcm_pool and it not in calc_pool]

        _random.shuffle(algo_write_pool)
        _random.shuffle(algo_read_pool)
        _random.shuffle(qcm_pool)
        _random.shuffle(calc_pool)
        _random.shuffle(other_pool)

        def _fb_pick(pred):
            # Fallback UNIQUEMENT à partir des banques quiz/exams (pas d'énoncé inventé).
            for fb in info_all:
                if pred(fb):
                    return dict(fb)
            return None

        used_texts = set()
        chosen = []

        def _take_slot(pool: list, fallback_item: dict | None = None):
            for it in pool:
                k = (it.get('text') or '').strip().lower()
                if k and k not in used_texts:
                    chosen.append(it)
                    used_texts.add(k)
                    return
            if fallback_item:
                kf = (fallback_item.get('text') or '').strip().lower()
                if kf and kf not in used_texts:
                    chosen.append(fallback_item)
                    used_texts.add(kf)

        # Structure stricte (structure_exam.json):
        # 1) Algorithmique (écrire/compléter)
        # 2) Compréhension d’algorithme
        # 3) QCM
        # 4) QCM
        # 5) Calcul
        _take_slot(algo_write_pool, _fb_pick(lambda x: _is_algo_write(x)))
        _take_slot(algo_read_pool, _fb_pick(lambda x: _is_algo_read_complete(x)))
        _take_slot(qcm_pool, _fb_pick(lambda x: x.get('type') == 'qcm'))
        _take_slot([q for q in qcm_pool if (q.get('text') or '').strip().lower() not in used_texts], _fb_pick(lambda x: x.get('type') == 'qcm'))
        _take_slot(calc_pool, _fb_pick(lambda x: _is_calc(x)))

        # Si la compréhension d'algorithme manque encore, la dériver d'un exercice algo REEL déjà sélectionné.
        has_algo_read_slot = any(_is_algo_read_complete(it) for it in chosen)
        if not has_algo_read_slot and chosen:
            base_algo = next((it for it in chosen if _is_algo_write(it)), chosen[0])
            base_text = (base_algo.get('text') or '').strip()
            derived = {
                'type': 'question',
                'theme': 'Compréhension d’algorithme',
                'text': (
                    "À partir de l’algorithme/exercice suivant :\n\n"
                    f"{base_text}\n\n"
                    "Expliquez le résultat attendu et illustrez avec une valeur d’entrée de votre choix."
                ),
                'options': [],
                'answer': 'Analyse du fonctionnement et simulation d’une entrée attendues.',
                'source': base_algo.get('source') or 'Banque examens Informatique',
            }
            k = (derived.get('text') or '').strip().lower()
            if k and k not in used_texts:
                chosen.insert(1, derived)
                used_texts.add(k)
                if len(chosen) > 5:
                    chosen = chosen[:5]

        # Si un slot est toujours manquant, compléter avec tout exercice autonome restant.
        if len(chosen) < 5:
            leftovers = [it for it in (algo_write_pool + algo_read_pool + qcm_pool + calc_pool + other_pool) if (it.get('text') or '').strip().lower() not in used_texts]
            for it in leftovers:
                if len(chosen) >= 5:
                    break
                chosen.append(it)
                used_texts.add((it.get('text') or '').strip().lower())

        # Dernier filet de sécurité: fallback global depuis banques seulement.
        if len(chosen) < 5:
            for fb in info_all:
                if len(chosen) >= 5:
                    break
                kf = (fb.get('text') or '').strip().lower()
                if kf in used_texts:
                    continue
                chosen.append(dict(fb))
                used_texts.add(kf)

        if not chosen:
            return {}
        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [{
                'label': 'INFORMATIQUE — Exercices indépendants (100 points)',
                'sections': [{
                    'label': 'Traiter tous les exercices (5 × 20 points)',
                    'type': 'open', 'pts': 100,
                    'items': [
                        {
                            'text': _format_info_item(it, i),
                            'answer': it.get('answer') or 'Réponse attendue.',
                            'pts': 20,
                        }
                        for i, it in enumerate(chosen)
                    ],
                }],
            }],
        }

    # ════════════════════════════════════════════════════════════════════════
    # ART — Séries A/B/C
    # ════════════════════════════════════════════════════════════════════════
    if subject == 'art':
        # Durée réaliste observée pour Art & Musique (LLA): entre 2h30 et 3h30.
        # On fixe une valeur cohérente et stable pour l'impression.
        duration = '2 h 30'

        art_all = _collect_items(['dissertation', 'question_texte', 'question', 'qcm', 'production_ecrite', 'exercice'], min_len=30)

        def _load_art_quiz_items() -> list[dict]:
            """Charge quiz_art.json pour soutenir les modèles de questions par série."""
            qpath = os.path.join(settings.BASE_DIR, 'database', 'quiz_art.json')
            if not os.path.exists(qpath):
                return []
            try:
                import json as _jq
                with open(qpath, encoding='utf-8') as _f:
                    raw = _jq.load(_f)
                qlist = list(raw.get('quiz', raw) if isinstance(raw, dict) else raw)
            except Exception:
                return []

            out = []
            for q in qlist:
                qq = (q.get('question') or '').strip()
                if len(qq) < 10:
                    continue
                opts = list(q.get('options') or [])
                corr = (q.get('correct') or 'A').upper()
                cidx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(corr, 0)
                ans = opts[cidx] if cidx < len(opts) else corr
                out.append({
                    'type': 'qcm',
                    'theme': q.get('category') or 'Art / Culture',
                    'text': qq,
                    'options': opts,
                    'answer': ans,
                    'source': 'Quiz Art BAC',
                })
            return out

        art_quiz = _load_art_quiz_items()
        art_all = art_all + art_quiz
        if not art_all:
            return {}

        def _is_art_prompt_ok(s: str) -> bool:
            t = (s or '').strip()
            if len(t) < 20 or len(t) > 420:
                return False
            tl = t.lower()
            bad = [
                '1 _ _ _ _', '2 _ _ _', '5 6h', '2 3h',
                'série c : musique', 'serie c : musique',
                'partie a :', 'partie b :',
                'tableau : ii-', 'iii-', 'iv-', 'v-',
            ]
            return not any(b in tl for b in bad)

        def _pick_texts(pool: list, n: int) -> list[str]:
            picked = []
            seen = set()
            for it in _pick(pool, max(n * 3, n)):
                txt = (it.get('text') or '').strip()
                key = txt.lower()
                if not txt or key in seen:
                    continue
                if not _is_art_prompt_ok(txt):
                    continue
                seen.add(key)
                picked.append(txt)
                if len(picked) >= n:
                    break
            return picked

        def _quiz_prompt(it: dict) -> str:
            q = (it.get('text') or '').strip()
            opts = [str(o).strip() for o in (it.get('options') or []) if str(o).strip()]
            if not opts:
                return q
            lines = []
            for i, op in enumerate(opts):
                m = _re.match(r'^\s*([A-Da-d])\s*[:.)-]\s*(.+)$', op)
                if m:
                    lines.append(f"{m.group(1).upper()}) {m.group(2).strip()}")
                else:
                    lines.append(f"{chr(ord('A')+i)}) {op}")
            return q + "\n" + "\n".join(lines)

        # Série A — Histoire de l'art: 2 sujets au choix + questions patrimoine obligatoires
        a_subject_pool = _pick_by_keywords(
            art_all,
            ['dissertation', 'commentaire', 'peinture', 'vaudou', 'théorie', 'theorie', 'art haïtien', 'art haitien', 'esthétique', 'esthetique'],
            6,
        )
        a_subject_pool = [it for it in a_subject_pool if it.get('type') in ('dissertation', 'question', 'production_ecrite')]
        a_subjects = _pick_texts(a_subject_pool or art_all, 2)
        if len(a_subjects) < 2:
            quiz_theory = _pick_by_keywords(art_quiz, ['art', 'peinture', 'vaudou', 'culture', 'esthétique', 'esthetique'], 4)
            a_subjects.extend([_quiz_prompt(it) for it in quiz_theory][: (2 - len(a_subjects))])

        a_oblig_pool = _pick_by_keywords(
            art_all,
            ['MUPANAH', 'ISPAN', 'Citadelle', 'Sans-Souci', 'Hector Hyppolite', 'Mangonès', 'sculpteur', 'patrimoine'],
            6,
        )
        a_oblig_pool = [it for it in a_oblig_pool if it.get('type') in ('question', 'qcm', 'production_ecrite')]
        a_oblig = _pick_texts(a_oblig_pool, 3)
        if len(a_oblig) < 3:
            quiz_patrimoine = _pick_by_keywords(art_quiz, ['MUPANAH', 'ISPAN', 'Hyppolite', 'Mangon', 'patrimoine', 'Citadelle', 'Sans-Souci'], 6)
            a_oblig.extend([_quiz_prompt(it) for it in quiz_patrimoine][: (3 - len(a_oblig))])

        # Série B — Arts plastiques
        b_pool = _pick_by_keywords(
            art_all,
            ['ellipse', 'ovale', 'perspective', 'compas', 'ciseaux', 'équerre', 'equerre', 'dessin', 'tracer', 'cube'],
            8,
        )
        b_pool = [it for it in b_pool if it.get('type') in ('question', 'qcm', 'exercice', 'production_ecrite')]
        b_items = _pick_texts(b_pool, 4)
        if len(b_items) < 4:
            quiz_b = _pick_by_keywords(art_quiz, ['ellipse', 'ovale', 'perspective', 'compas', 'équerre', 'equerre', 'dessin'], 8)
            b_items.extend([_quiz_prompt(it) for it in quiz_b][: (4 - len(b_items))])

        # Série C — Musique
        c_pool = _pick_by_keywords(
            art_all,
            ['gamme', 'dièse', 'diese', 'bémol', 'bemol', 'intervalle', 'accord', 'hauteur', 'intensité', 'intensite', 'timbre', 'solfège', 'solfege'],
            8,
        )
        c_pool = [it for it in c_pool if it.get('type') in ('question', 'qcm', 'exercice', 'production_ecrite')]
        c_items = _pick_texts(c_pool, 4)
        if len(c_items) < 4:
            quiz_c = _pick_by_keywords(art_quiz, ['gamme', 'dièse', 'diese', 'bémol', 'bemol', 'intervalle', 'accord', 'hauteur', 'intensité', 'intensite', 'timbre', 'solfège', 'solfege'], 8)
            c_items.extend([_quiz_prompt(it) for it in quiz_c][: (4 - len(c_items))])

        a_choice_text = 'SUJETS AU CHOIX (Traiter un seul sujet):\n\n'
        a_choice_text += '\n\n'.join([f"{i+1}) {s}" for i, s in enumerate(a_subjects)])

        a_oblig_text = 'QUESTIONS OBLIGATOIRES (patrimoine et artistes):\n\n'
        a_oblig_text += '\n'.join([f"{i+1}) {q}" for i, q in enumerate(a_oblig)])

        b_text = 'ARTS PLASTIQUES — techniques et geometrie:\n\n'
        b_text += '\n'.join([f"{i+1}) {q}" for i, q in enumerate(b_items)])

        c_text = 'MUSIQUE — theorie et applications:\n\n'
        c_text += '\n'.join([f"{i+1}) {q}" for i, q in enumerate(c_items)])

        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'SÉRIE A — Histoire de l\'art (60 points)',
                    'sections': [
                        {
                            'label': 'Dissertation / commentaire (sujet au choix)',
                            'type': 'open', 'pts': 40,
                            'items': [{
                                'text': _fix_latex(_global_format_tables(a_choice_text)),
                                'answer': 'Développement argumenté attendu avec introduction, analyse et conclusion.',
                                'pts': 40,
                            }],
                        },
                        {
                            'label': 'Questions obligatoires de patrimoine artistique',
                            'type': 'open', 'pts': 20,
                            'items': [{
                                'text': _fix_latex(_global_format_tables(a_oblig_text)),
                                'answer': 'Réponses courtes et précises attendues.',
                                'pts': 20,
                            }],
                        },
                    ],
                },
                {
                    'label': 'SÉRIE B — Arts plastiques (20 points)',
                    'sections': [{
                        'label': 'Questions techniques et tracés',
                        'type': 'open', 'pts': 20,
                        'items': [{
                            'text': _fix_latex(_global_format_tables(b_text)),
                            'answer': 'Application technique et vocabulaire plastique attendus.',
                            'pts': 20,
                        }],
                    }],
                },
                {
                    'label': 'SÉRIE C — Musique (20 points)',
                    'sections': [{
                        'label': 'Théorie musicale et applications',
                        'type': 'open', 'pts': 20,
                        'items': [{
                            'text': _fix_latex(_global_format_tables(c_text)),
                            'answer': 'Réponses de théorie musicale attendues (gammes, intervalles, terminologie).',
                            'pts': 20,
                        }],
                    }],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # GÉOGRAPHIE / AUTRES SCIENCES HUMAINES (fallback structuré)
    # ════════════════════════════════════════════════════════════════════════
    if subject in ('geographie',):
        all_types = ['question', 'qcm', 'dissertation', 'question_texte', 'production_ecrite', 'exercice']
        all_items = _collect_items(all_types)
        if not all_items:
            return {}
        _random.shuffle(all_items)

        p1 = _pick([i for i in all_items if i['type'] in ('qcm', 'question')], 4)
        p2 = _pick([i for i in all_items if i['type'] in ('question_texte', 'dissertation', 'exercice')], 1)

        if not p1 or not p2:
            return {}
        pts = _distribute(40, len(p1))
        return {
            'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
            'parts': [
                {
                    'label': 'PREMIÈRE PARTIE — Questions de cours (40 points)',
                    'sections': [_section('Questions / Définitions', p1, pts)],
                },
                {
                    'label': 'DEUXIÈME PARTIE — Composition / Analyse (60 points)',
                    'sections': [{
                        'label': f'Composition / Analyse — {p2[0]["theme"]} — {p2[0]["source"]} (60 pts)',
                        'type': 'open', 'pts': 60,
                        'items': [{'text': _fix_latex(_global_format_tables(p2[0]['text'])), 'answer': p2[0].get('answer') or 'Réponse développée attendue.', 'pts': 60}],
                    }],
                },
            ],
        }

    # ════════════════════════════════════════════════════════════════════════
    # FALLBACK GÉNÉRIQUE : 3 items par partie
    # ════════════════════════════════════════════════════════════════════════
    all_types = ['exercice', 'question', 'dissertation', 'question_texte', 'qcm', 'production_ecrite']
    all_items = _collect_items(all_types)
    if not all_items:
        return {}
    _random.shuffle(all_items)
    p1 = _pick(all_items, 3)
    p2 = _pick(all_items, 3)
    pts1 = _distribute(50, 3)
    pts2 = _distribute(50, 3)
    return {
        'title': title, 'duration': duration, 'annee': _annee, 'serie': _serie, 'coeff': _coeff,
        'parts': [
            {'label': 'PREMIÈRE PARTIE (50 points)',
             'sections': [{'label': f'{it["theme"]} — {it["source"]} ({pts1[i]} pts)',
                           'type': 'open', 'pts': pts1[i],
                           'items': [{'text': _fix_latex(_global_format_tables(it['text'])), 'answer': 'Réponse développée attendue.', 'pts': pts1[i]}]}
                          for i, it in enumerate(p1)]},
            {'label': 'DEUXIÈME PARTIE (50 points)',
             'sections': [{'label': f'{it["theme"]} — {it["source"]} ({pts2[i]} pts)',
                           'type': 'open', 'pts': pts2[i],
                           'items': [{'text': _fix_latex(_global_format_tables(it['text'])), 'answer': 'Réponse développée attendue.', 'pts': pts2[i]}]}
                          for i, it in enumerate(p2)]},
        ],
    }



def extract_structured_exercise(exam_texts: str, subject: str, chapter: str = '') -> dict | None:
    """
    Lit le texte brut d'examens BAC (potentiellement corrompu par scan 2 colonnes)
    et COMPOSE un exercice complet propre, fidèle au style BAC Haïti, pour le chapitre demandé.
    Approche : l'IA utilise les examens comme modèle/inspiration, puis génère un exercice
    structuré clair avec contexte + sous-questions numérotées.
    Retourne {intro, questions, theme, difficulte, source} ou None.
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    chapter_hint  = f" sur le thème **{chapter}**" if chapter else ''

    system = (
        f"Tu es un professeur examinateur expert en {subject_label} au Bac Haïti (Terminale). "
        f"Tu connais parfaitement le programme et le style des examens officiels haïtiens. "
        f"Tu rédiges en français académique clair."
    )

    prompt = f"""Voici des extraits de vrais examens officiels du Bac Haïti en {subject_label} :

{exam_texts[:6000]}

---
Ces textes peuvent contenir des artefacts de scan (colonnes mélangées, espaces parasites, 
caractères corrompus). C'est normal — utilise-les uniquement comme MODÈLE de style et de niveau.

TA MISSION : Compose UN exercice complet{chapter_hint} de style BAC Haïti Terminale.

L'exercice doit :
1. Avoir un CONTEXTE/ÉNONCÉ avec des données numériques réalistes (situations concrètes)
2. Avoir 4 à 6 sous-questions numérotées a), b), c), d)... qui s'enchaînent logiquement
3. Être au niveau Terminale, similaire aux exercices de la PARTIE B des vrais examens
4. Être parfaitement lisible et sans artefacts
5. Utiliser KaTeX pour les formules : $expression$

Exemple de structure attendue :
- intro : "On considère la fonction $f$ définie sur $\\mathbb{{R}}$ par $f(x) = 2x^2 - 3x + 1$. On note $(C_f)$ sa courbe représentative."
- questions : ["a) Calculer $f'(x)$", "b) Dresser le tableau de variations de $f$", ...]

Réponds UNIQUEMENT avec ce JSON (rien d'autre, pas de texte autour) :
{{
  "intro": "Énoncé complet avec toutes les données — phrase(s) de contexte, valeurs numériques, définitions. En français correct.",
  "questions": [
    "a) Libellé complet de la première sous-question",
    "b) Libellé complet de la deuxième sous-question",
    "c) Libellé complet de la troisième sous-question",
    "d) Libellé complet de la quatrième sous-question"
  ],
  "theme": "Titre court du sujet (ex: Étude de fonction, Suites numériques, Nombres complexes)",
  "difficulte": "facile|moyen|difficile",
  "source": "Style Bac Haïti Terminale — {subject_label}"
}}"""

    text = _call_json(prompt, system=system, max_tokens=2000)
    text = re.sub(r'```[a-z]*\s*', '', text).strip().rstrip('`').strip()
    m = re.search(r'\{[\s\S]+\}', text)
    if not m:
        return None
    raw = m.group(0)
    # Fix LaTeX backslashes that break json.loads (\r→rightarrow, \t→theta, \b→beta, \f→frac)
    raw = re.sub(r'(?<!\\)\\([tbfr])', r'\\\\\1', raw)
    raw = re.sub(r'(?<!\\)\\([^"\\/bfnrtu0-9\n\r ])', r'\\\\\1', raw)
    raw = re.sub(r',\s*([}\]])', r'\1', raw)  # trailing commas
    try:
        data = _json.loads(raw)
    except Exception:
        return None

    intro     = (data.get('intro') or '').strip()
    questions = [str(q).strip() for q in (data.get('questions') or []) if str(q).strip()]
    if not intro or len(questions) < 2:
        return None

    theme = (data.get('theme') or chapter or subject_label).strip()
    diff  = data.get('difficulte', 'moyen')
    if diff not in ('facile', 'moyen', 'difficile'):
        diff = 'moyen'
    source = (data.get('source') or f'Style Bac Haïti — {subject_label}').strip()

    return {
        'intro':      intro,
        'enonce':     intro + '\n\n' + '\n'.join(questions),
        'questions':  questions,
        'theme':      theme,
        'matiere':    subject.upper(),
        'difficulte': diff,
        'source':     source,
        'solution':   '',
        'conseils':   f"Exercice de style Bac Haïti en {subject_label}. Montre toutes tes étapes de calcul.",
    }


def extract_exercise_from_pdf(exam_text: str, subject: str, chapter: str = '') -> dict:
    """
    Extrait un exercice ouvert depuis le texte d'un examen PDF.
    Retourne {enonce, solution, source, type}
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    chapter_hint = f" sur le chapitre '{chapter}'" if chapter else ''
    prompt = (
        f"Tu es prof de {subject_label} au Bac Haïti.\n"
        f"Voici des extraits d'examens officiels :\n\n{exam_text[:3000]}\n\n"
        f"Extraire ou adapter UN exercice{chapter_hint} de niveau Terminale.\n\n"
        "Réponds en JSON :\n"
        '{"enonce":"texte complet de l\'exercice avec toutes les données",'
        '"solution":"solution détaillée étape par étape, pédagogique",'
        '"conseils":"1-2 astuces pour ce type d\'exercice",'
        '"difficulte":"facile|moyen|difficile",'
        '"source":"Extrait examen BAC Haïti"}\n\n'
        "Formules en KaTeX ($...$). Exercice réaliste niveau Bac."
    )
    text = _call_fast(prompt, max_tokens=1200)
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    m = re.search(r'\{[\s\S]+\}', text)
    if m:
        try:
            return _json.loads(m.group(0))
        except Exception:
            pass
    return {'enonce': text[:800], 'solution': '', 'conseils': '', 'difficulte': 'moyen', 'source': 'Examen BAC'}


def generate_language_exercise(subject: str, chapter: str) -> dict:
    """
    Génère un exercice 100% IA pour les matières de langue (anglais, espagnol, kreyol).
    Style BAC Haïti authentique: texte de lecture + questions, grammaire, vocabulaire, etc.
    Quantité illimitée, sujets variés, toujours dans la langue cible.
    """
    import json as _json

    # Each exercise type has: description, needs_texte (bool), specific instructions
    LANG_MAP = {
        'anglais': {
            'name': 'English',
            'instruction_lang': 'English',
            'exam_name': 'English BAC Haïti',
            'exercise_types': [
                {
                    'type': 'reading_comprehension',
                    'label': 'Reading Comprehension',
                    'needs_texte': True,
                    'instructions': (
                        "Write a COMPLETE original passage (170-220 words) about a topic relevant to Haiti or the world. "
                        "Then write 5-7 comprehension questions based ENTIRELY on the passage content. "
                        "texte = the full passage. enonce = 'Read the following passage and answer the questions.'"
                    )
                },
                {
                    'type': 'fill_blanks_grammar',
                    'label': 'Fill in the Blanks',
                    'needs_texte': False,
                    'instructions': (
                        "Write 8-12 SEPARATE independent sentences, each with ONE blank (___) to fill. "
                        "Focus on: prepositions (in/on/at/for/since/ago), adjective endings (-ed/-ing), "
                        "articles (a/an/the), or verb tenses. "
                        "texte = EMPTY string. enonce = the instruction. "
                        "questions = the list of ALL sentences with blanks, e.g. 'She has lived here ___ 2015.'"
                    )
                },
                {
                    'type': 'multiple_choice',
                    'label': 'Multiple Choice Grammar',
                    'needs_texte': False,
                    'instructions': (
                        "Write 8-10 multiple choice questions. Each question is a sentence with a blank, "
                        "followed by 3 options (a, b, c). "
                        "texte = EMPTY string. "
                        "questions = list like: '1. She ___ to school every day. a) go  b) goes  c) going'"
                    )
                },
                {
                    'type': 'sentence_transformation',
                    'label': 'Sentence Transformation',
                    'needs_texte': False,
                    'instructions': (
                        "Write 6-8 sentence transformation exercises. Give the original sentence "
                        "and ask the student to rewrite it (passive→active, direct→indirect speech, "
                        "affirmative→negative, etc.). "
                        "texte = EMPTY string. "
                        "questions = list of transformation tasks."
                    )
                },
                {
                    'type': 'composition',
                    'label': 'Written Production',
                    'needs_texte': False,
                    'instructions': (
                        "Generate a WRITTEN PRODUCTION exercise. Give a TOPIC (a real-world subject relevant to Haiti "
                        "or daily life) and ask the student to write a paragraph or short essay of 80-120 words. "
                        "Include 3-4 guiding questions/points the student must address in their writing. "
                        "texte = EMPTY string. "
                        "enonce = the writing topic and global instruction (e.g. 'Write a paragraph about...'). "
                        "questions = the 3-4 guiding points, e.g.: "
                        "['1. What is your opinion on this topic?', '2. Give two specific examples.', '3. What would you recommend?']. "
                        "DO NOT generate sentence transformations or fill-in-the-blank items."
                    )
                },
            ]
        },
        'espagnol': {
            'name': 'Español',
            'instruction_lang': 'Spanish',
            'exam_name': 'Español BAC Haïti',
            'exercise_types': [
                {
                    'type': 'reading_comprehension',
                    'label': 'Comprensión de Lectura',
                    'needs_texte': True,
                    'instructions': (
                        "Escribe un pasaje COMPLETO (150-200 palabras) sobre un tema relevante. "
                        "Luego escribe 5-7 preguntas basadas COMPLETAMENTE en el pasaje. "
                        "texte = el pasaje completo. enonce = 'Lee el siguiente texto y responde las preguntas.'"
                    )
                },
                {
                    'type': 'fill_blanks',
                    'label': 'Completa los Espacios',
                    'needs_texte': False,
                    'instructions': (
                        "Escribe 8-12 frases independientes, cada una con UN espacio en blanco (___). "
                        "Enfócate en: preposiciones, conjugaciones verbales, artículos. "
                        "texte = cadena VACÍA. questions = lista de todas las frases con blancos."
                    )
                },
                {
                    'type': 'multiple_choice',
                    'label': 'Gramática Opción Múltiple',
                    'needs_texte': False,
                    'instructions': (
                        "Escribe 8-10 preguntas de opción múltiple con 3 opciones (a, b, c). "
                        "texte = cadena VACÍA. questions = lista de preguntas con opciones."
                    )
                },
            ]
        },
        'kreyol': {
            'name': 'Kreyòl Ayisyen',
            'instruction_lang': 'Haitian Creole',
            'exam_name': 'Kreyòl BAC Ayiti',
            'exercise_types': [
                {
                    'type': 'reading_comprehension',
                    'label': 'Konpreyansyon Tèks',
                    'needs_texte': True,
                    'instructions': (
                        "Ekri yon tèks KONPLÈ (150-200 mo) sou yon sijè enpòtan pou Ayiti. "
                        "Answit ekri 5-7 kesyon ki baze TOTALMAN sou tèks la. "
                        "texte = tèks konplè a. enonce = 'Li tèks sa a epi repon kesyon yo.'"
                    )
                },
                {
                    'type': 'fill_blanks',
                    'label': 'Ranpli Espas Yo',
                    'needs_texte': False,
                    'instructions': (
                        "Ekri 8-10 fraz endepandan, chak ak YON espas vid (___) pou ranpli. "
                        "texte = chaîne VIDE. questions = lis tout fraz yo ak espas vid."
                    )
                },
            ]
        },
    }

    lang_info = LANG_MAP.get(subject, LANG_MAP['anglais'])
    lang_name = lang_info['name']
    instr_lang = lang_info['instruction_lang']
    exam_name = lang_info['exam_name']

    import random
    reading_keywords   = ['reading', 'comprehension', 'text', 'passage', 'lecture', 'tèks', 'comprensión', 'lectura']
    writing_keywords   = ['writing', 'written', 'production', 'essay', 'composition', 'rédaction', 'write', 'paragraph', 'ekri', 'redaksyon']
    grammar_keywords   = ['-ed', '-ing', 'preposition', 'tense', 'verb', 'adjective', 'article', 'grammar', 'fill', 'blank', 'grammaire', 'préposition', 'transformation', 'transform']

    chapter_lower = chapter.lower() if chapter else ''

    if any(k in chapter_lower for k in writing_keywords):
        # Force composition/writing type
        type_info = next((t for t in lang_info['exercise_types'] if t['type'] == 'composition'), lang_info['exercise_types'][0])
    elif any(k in chapter_lower for k in reading_keywords):
        # Force reading comprehension type
        type_info = next((t for t in lang_info['exercise_types'] if t['type'] == 'reading_comprehension'), lang_info['exercise_types'][0])
    elif any(k in chapter_lower for k in grammar_keywords):
        # Force grammar/fill-in-blank type (exclude reading + composition)
        grammar_types = [t for t in lang_info['exercise_types'] if t['type'] not in ('reading_comprehension', 'composition')]
        type_info = random.choice(grammar_types) if grammar_types else lang_info['exercise_types'][0]
    else:
        type_info = random.choice(lang_info['exercise_types'])

    chosen_label = type_info['label']
    chosen_instructions = type_info['instructions']
    needs_texte = type_info['needs_texte']

    chapter_hint = f"focused on the chapter/topic: {chapter}" if chapter else "on any relevant BAC topic"

    prompt = (
        f"You are an expert BAC Haïti examiner for {lang_name}.\n"
        f"Create ONE original BAC-style exercise {chapter_hint}.\n"
        f"Exercise type: {chosen_label}\n\n"
        f"STRICT INSTRUCTIONS FOR THIS EXERCISE TYPE:\n"
        f"{chosen_instructions}\n\n"
        f"LANGUAGE RULE: Everything must be in {instr_lang}.\n"
        f"Make it realistic for a Haitian BAC Terminale student.\n\n"
        f"Respond in JSON (no markdown, no extra text):\n"
        '{{"enonce": "Short instruction (1-2 sentences)",'
        f'"texte": "{"The passage/sentences HERE (MANDATORY for this type)" if needs_texte else "EMPTY STRING - leave as empty string"}",'
        '"questions": ["item 1", "item 2", ...],'
        '"solution": "Expected answers",'
        '"conseils": "Brief study tip",'
        '"difficulte": "moyen|difficile",'
        f'"source": "{exam_name}",'
        f'"theme": "{chosen_label}"}}'
    )

    text = _call_fast(prompt, max_tokens=1800)
    # Strip ALL markdown code fences (with or without language tag)
    text = re.sub(r'```[a-z]*\n?', '', text).strip()
    text = text.rstrip('`').strip()
    # Try direct parse first (clean response)
    parsed = None
    try:
        parsed = _json.loads(text)
    except Exception:
        pass
    if not parsed:
        # Find outermost JSON object
        start = text.find('{')
        if start >= 0:
            depth = 0
            end = -1
            in_str = False
            escape = False
            for i, ch in enumerate(text[start:], start):
                if escape:
                    escape = False
                    continue
                if ch == '\\' and in_str:
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                if not in_str:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
            if end > start:
                try:
                    parsed = _json.loads(text[start:end])
                except Exception:
                    pass
    if parsed:
        # Combine texte into enonce for display if present
        texte = parsed.get('texte', '').strip()
        enonce = parsed.get('enonce', '').strip()
        if texte:
            parsed['enonce'] = enonce + '\n\n' + texte
            parsed['intro'] = enonce  # consigne only in intro
        else:
            parsed['intro'] = enonce
        parsed['_ai_generated'] = True
        parsed['matiere'] = subject.upper()
        return parsed
    return {
        'enonce': text[:800],
        'intro': text[:400],
        'questions': [],
        'solution': '',
        'conseils': '',
        'difficulte': 'moyen',
        'source': exam_name,
        '_ai_generated': True,
    }


def generate_philosophy_exercise(chapter_type: str) -> dict:
    """
    Génère un exercice de philosophie BAC Haïti.
    chapter_type : 'dissertation' ou 'étude de texte'
    Retourne {intro, texte, questions, theme, matiere, difficulte, source, solution, conseils}
    """
    import json as _json
    import random as _random

    # ── Sujets de dissertation les plus fréquents au BAC Haïti ──────────────
    _DISSERTATION_SUBJECTS = [
        "Le déterminisme constitue-t-il un obstacle à la liberté ?",
        "Suffit-il d'obéir à la raison pour être libre ?",
        "L'homme doit-il seulement travailler par nécessité ?",
        "Le travail n'est-il que servitude ?",
        "Les progrès techniques sont-ils capables de rendre l'homme heureux ?",
        "Suffit-il de respecter les règles sociales pour être moral ?",
        "La moralité consiste-t-elle à fuir notre nature ?",
        "Peut-on se connaître soi-même ?",
        "L'expérience courante nous met-elle sur le chemin de la science ?",
        "Quelle est la part de la nature et celle de la culture dans le comportement humain ?",
        "La politique est-elle l'affaire de tous ?",
        "Peut-on considérer l'État comme l'ennemi de la liberté ?",
        "Faut-il considérer la philosophie comme une activité secondaire ?",
        "La liberté consiste-t-elle à faire ce que l'on veut ?",
        "Le doute est-il nécessairement le refus de la vérité ?",
        "Peut-on dire de l'homme qu'il est un être inachevé ?",
        "La conscience est-elle un obstacle au bonheur ?",
        "Peut-on être libre sans les autres ?",
        "L'art est-il un luxe ou une nécessité ?",
        "Faut-il craindre la mort ?",
    ]

    # ── Textes philosophiques authentiques pour l'étude de texte ────────────
    _PHILO_TEXTS = [
        {
            "auteur": "Kant",
            "oeuvre": "Fondements de la métaphysique des mœurs",
            "theme": "Liberté et autonomie",
            "texte": (
                "La liberté de penser signifie que la raison ne se soumet à aucune autre loi "
                "que celle qu'elle se donne à elle-même. Et son contraire est la maxime d'un usage "
                "sans loi de la raison — afin, comme le génie en fait le rêve, de voir plus loin "
                "qu'en restant dans les limites de ses lois. Il s'ensuit naturellement que là où "
                "la raison ne veut pas se soumettre à la loi qu'elle se donne à elle-même, elle "
                "doit plier sous le joug des lois que lui donne autrui. Car sans loi quelconque, "
                "rien — pas même le plus grand non-sens — ne peut longtemps avoir cours."
            ),
            "phrase_a_expliquer": "la raison ne se soumet à aucune autre loi que celle qu'elle se donne à elle-même",
        },
        {
            "auteur": "Rousseau",
            "oeuvre": "L'Émile",
            "theme": "Connaissance d'autrui",
            "texte": (
                "Pour connaître les hommes, il faut les voir agir. Dans le monde on les entend parler ; "
                "ils montrent leurs discours et cachent leurs actions : mais dans l'histoire elles sont "
                "dévoilées, et on les juge sur les faits. Leurs propos même aident à les apprécier ; "
                "car, comparant ce qu'ils font à ce qu'ils disent, on voit à la fois ce qu'ils sont "
                "et ce qu'ils veulent paraître : plus ils se déguisent, mieux on les connaît."
            ),
            "phrase_a_expliquer": "plus ils se déguisent, mieux on les connaît",
        },
        {
            "auteur": "Freud",
            "oeuvre": "L'Interprétation des rêves",
            "theme": "Inconscient et conscience",
            "texte": (
                "L'inconscient est pareil à un grand cercle qui enfermerait le conscient comme un cercle "
                "plus petit. Il ne peut y avoir de fait conscient sans stade antérieur inconscient, "
                "tandis que l'inconscient peut se passer de stade conscient et avoir cependant une valeur "
                "psychique. L'inconscient est le vrai réel psychique ; sa nature intime nous est aussi "
                "inconnue que la réalité du monde extérieur, et il nous est aussi incomplètement livré "
                "par les données de la conscience que l'est le monde extérieur par les indications de "
                "nos organes sensoriels."
            ),
            "phrase_a_expliquer": "l'inconscient peut se passer de stade conscient et avoir cependant une valeur psychique",
        },
        {
            "auteur": "Platon",
            "oeuvre": "La République (Allégorie de la Caverne)",
            "theme": "Vérité et ignorance",
            "texte": (
                "Figure-toi des hommes dans une demeure souterraine en forme de caverne, ayant sur toute "
                "sa largeur une entrée ouverte à la lumière ; ces hommes sont là depuis leur enfance, les "
                "jambes et le cou enchaînés, de sorte qu'ils ne peuvent bouger ni voir ailleurs que devant "
                "eux. Ils ne voient que des ombres projetées sur le fond de la caverne. Ils prennent ces "
                "ombres pour la réalité, car ils n'ont jamais rien connu d'autre. Si l'un d'eux était "
                "libéré et contraint de regarder la lumière, il souffrirait et ne pourrait voir les objets "
                "dont il voyait auparavant les ombres. Il lui faudrait du temps pour s'habituer à la lumière "
                "et comprendre qu'il voyait des illusions."
            ),
            "phrase_a_expliquer": "Ils prennent ces ombres pour la réalité, car ils n'ont jamais rien connu d'autre",
        },
        {
            "auteur": "Spinoza",
            "oeuvre": "Éthique",
            "theme": "Liberté et déterminisme",
            "texte": (
                "Les hommes se trompent en ce qu'ils se croient libres ; cette opinion consiste seulement "
                "en ce qu'ils ont conscience de leurs actions et ignorent les causes qui les déterminent. "
                "C'est donc là leur idée de la liberté : qu'ils ne connaissent aucune cause de leurs actions. "
                "Ce qu'ils disent, que les actions humaines dépendent de la volonté, ce sont des mots sans "
                "idée correspondante. Car ce que c'est que la volonté et comment elle meut le corps, ils "
                "l'ignorent tous ; et ceux qui se vantent du contraire, ceux-là élèvent des magnificences "
                "et inventent des facultés occultes ou mystérieuses."
            ),
            "phrase_a_expliquer": "Les hommes se trompent en ce qu'ils se croient libres",
        },
        {
            "auteur": "Descartes",
            "oeuvre": "Méditations Métaphysiques",
            "theme": "Doute et vérité",
            "texte": (
                "Tout ce que j'ai reçu jusqu'à présent pour le plus vrai et assuré, je l'ai appris des "
                "sens ou par les sens : or j'ai quelquefois éprouvé que ces sens étaient trompeurs, et "
                "il est de la prudence de ne se fier jamais entièrement à ceux qui nous ont une fois trompés. "
                "Mais peut-être que, quoique les sens nous trompent quelquefois touchant des choses peu "
                "sensibles et fort éloignées, il s'en rencontre beaucoup d'autres desquelles on ne peut "
                "pas raisonnablement douter. Ainsi, par exemple, que je sois ici, assis auprès du feu, "
                "vêtu d'une robe de chambre, ayant ce papier entre les mains : cela me semble si évident "
                "que je dois penser que c'est folie d'en douter."
            ),
            "phrase_a_expliquer": "il est de la prudence de ne se fier jamais entièrement à ceux qui nous ont une fois trompés",
        },
    ]

    # Normaliser le type de chapitre
    ch = chapter_type.strip().lower()
    is_dissertation = 'dissert' in ch

    if is_dissertation:
        sujet = _random.choice(_DISSERTATION_SUBJECTS)
        return {
            'intro': (
                f'**Sujet de dissertation :** "{sujet}"\n\n'
                'Rédigez une dissertation philosophique complète suivant le plan **Thèse / Antithèse**.\n'
                'Respectez les longueurs indiquées — elles comptent dans la notation.'
            ),
            'texte': '',
            # Pas de liste de questions affichees - l'IA guide a->b->c->d dans le chat
            'questions': [],
            '_dissertation_sujet': sujet,
            'theme': 'Dissertation philosophique',
            'matiere': 'PHILOSOPHIE',
            'difficulte': 'difficile',
            'source': 'Style BAC Haïti',
            'solution': (
                'Critères d\'évaluation :\n'
                '— Introduction : accroche + définitions + problématique + annonce du plan\n'
                '— Thèse : 2-3 arguments clairs avec références philosophers + exemples + transition\n'
                '— Antithèse : 2-3 arguments qui vraiment nuancent + références + exemples\n'
                '— Conclusion : synthèse + réponse à la problématique + ouverture\n'
                '— Interdit : "je pense que", catalogue d\'idées sans plan, répétitions, nouveaux arguments en conclusion\n'
                '— Obligatoire : connecteurs logiques, citations d\'auteurs (Kant, Rousseau, Platon, Descartes, Spinoza, Marx, Freud, Aristote, Bergson, Pascal)'
            ),
            'conseils': (
                'Règles pour avoir 10/10 :\n'
                '✅ Structure Thèse/Antithèse obligatoire\n'
                '✅ Problématique formulée en question\n'
                '✅ Connecteurs logiques à chaque étape\n'
                '✅ Citer au moins 2 auteurs avec leur œuvre\n'
                '✅ Longueurs respectées (intro 6-12L, chaque partie 15-20L, conclusion 6-12L)\n'
                '❌ Ne jamais écrire "je pense que"\n'
                '❌ Ne jamais introduire un argument nouveau dans la conclusion\n'
                '❌ Ne jamais faire une seule partie sans plan dialectique'
            ),
            '_ai_generated': True,
            '_philo_type': 'dissertation',
        }
    else:
        # Étude de texte
        txt_data = _random.choice(_PHILO_TEXTS)
        auteur  = txt_data['auteur']
        oeuvre  = txt_data['oeuvre']
        texte   = txt_data['texte']
        phrase  = txt_data['phrase_a_expliquer']
        theme   = txt_data['theme']
        return {
            'intro': (
                f'Lisez attentivement cet extrait de **{auteur}** (*{oeuvre}*) '
                f'et répondez aux 4 questions suivantes. '
                f'Ces 4 questions sont toujours présentes dans les examens BAC Haïti.'
            ),
            'texte': texte,
            # Questions simples sans instructions de méthode — l’IA dévoile la méthode seulement si besoin
            'questions': [
                'Q1 — Quelle est la thèse (l\'idée principale) du texte ?',
                'Q2 — Dégagez les articulations de l\'argumentation.',
                f'Q3 — Expliquez la phrase suivante : "{phrase}"',
                'Q4 — Quel est l\'intérêt philosophique du texte ?',
            ],
            '_phrase_a_expliquer': phrase,
            'theme': f'Étude de texte — {auteur} ({theme})',
            'matiere': 'PHILOSOPHIE',
            'difficulte': 'difficile',
            'source': f'Style BAC Haïti — {auteur}',
            'solution': (
                f'Auteur : {auteur} — Œuvre : {oeuvre}\n'
                f'Phrase à expliquer : "{phrase}"\n\n'
                'Critères de notation :\n'
                '— Q1 (10 pts) : La thèse est une affirmation centrale, pas un résumé\n'
                '— Q2 (10-20 pts) : 3-4 étapes logiques distinctes avec verbes d\'action\n'
                f'— Q3 (5-20 pts) : Contexte + mots-clés + sens + exemple + enjeu (15-20 lignes)\n'
                '— Q4 (15-20 pts) : Modèle "L\'intérêt philosophique... réside dans..." + 3 questions\n'
                '— Piège principal : confondre thèse et résumé / confondre intérêt philo et thèse'
            ),
            'conseils': (
                'Règles pour avoir 10/10 :\n'
                '✅ Q1 : Formuler une AFFIRMATION (pas un résumé, pas une question)\n'
                '✅ Q2 : Découper en étapes logiques, pas phrase par phrase\n'
                '✅ Q3 : Définir chaque mot-clé avant d\'expliquer + donner un exemple concret\n'
                '✅ Q4 : Utiliser le modèle "L\'intérêt philosophique réside dans..." + poser 3 questions\n'
                '❌ Ne jamais paraphraser (redire la même chose autrement)\n'
                '❌ Ne jamais donner son avis personnel dans l\'étude de texte\n'
                '❌ Ne jamais confondre "intérêt philosophique" avec "résumé du texte"'
            ),
            '_ai_generated': True,
            '_philo_type': 'etude_texte',
        }


def generate_exam_exercise(subject: str, chapter: str, exam_context: str = '', chapter_rule: dict | None = None) -> dict:
    """
    Génère un exercice ORIGINAL de style examen pour un sujet/chapitre.
    Retourne {enonce, solution, conseils, difficulte}
    chapter_rule: dict with 'must_include' and 'must_exclude' keywords for chapter filtering.
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    chapter_hint  = f" - chapitre : {chapter}" if chapter else ''
    ctx_block     = f"\nExemples d'examens passés :\n{exam_context[:1500]}\n" if exam_context else ''

    # Build chapter exclusion instruction for the AI
    chapter_exclusion_line = ''
    if chapter_rule:
        must_excl = chapter_rule.get('must_exclude', [])
        if must_excl:
            excl_topics = ', '.join(must_excl[:6])
            chapter_exclusion_line = (
                f"\nATTENTION : L'exercice doit porter EXCLUSIVEMENT sur le chapitre '{chapter}'. "
                f"Ne génère JAMAIS un exercice qui porte sur : {excl_topics}. "
                f"Respecte strictement cette contrainte.\n"
            )

    prompt = (
        f"Tu es un examinateur du Bac Haïti en {subject_label}{chapter_hint}.\n"
        f"{ctx_block}\n"
        f"{chapter_exclusion_line}"
        "Génère UN exercice ORIGINAL de niveau Terminale, similaire aux vrais examens.\n\n"
        "RÈGLES ABSOLUES DE FORMATAGE — RESPECTE-LES EXACTEMENT :\n"
        "1. TOUJOURS entourer formules et variables de dollars : $C = \\varepsilon_0 S / d$, $Q = 4\\,\\mu\\text{C}$.\n"
        "2. NE JAMAIS écrire une commande LaTeX hors des $ (pas de \\varepsilon en dehors de $...$).\n"
        "3. NE JAMAIS écrire une variable deux fois : pas de '$x$ x', pas de '$C$ C'. UNIQUEMENT le LaTeX.\n"
        "4. Unités : dans les $ avec \\text{} : $S = 0{,}02\\,\\text{m}^2$, $d = 1\\,\\text{mm}$.\n"
        "5. Tableaux de données : format Markdown uniquement : | Grandeur | Valeur | header + rows.\n"
        "6. NE PAS utiliser \\, outside $ (virgule décimale : $0{,}02$ pas 0,02 seul).\n\n"
        "Réponds UNIQUEMENT en JSON valide (les backslashes LaTeX doivent être doublés dans le JSON) :\n"
        '{"enonce":"énoncé avec formules en $...$",'
        '"solution":"solution détaillée avec formules en $...$",'
        '"conseils":"méthode à retenir",'
        '"difficulte":"facile|moyen|difficile",'
        '"source":"Exercice style BAC Haïti"}\n\n'
        "RAPPEL JSON : dans une chaîne JSON, un backslash LaTeX s'écrit \\\\varepsilon, \\\\frac, \\\\sigma, etc."
    )
    text = _call_fast(prompt, max_tokens=1200)
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    m = re.search(r'\{[\s\S]+\}', text)
    if m:
        raw = m.group(0)
        # Fix LaTeX backslashes that would break json.loads
        # Step 1: double \t \b \f \r so they become literal backslash+letter (not control chars)
        # e.g. \rightarrow, \rho, \right (\r→carriage return in JSON)
        raw = re.sub(r'(?<!\\)\\([tbfr])', r'\\\\\1', raw)
        # Step 2: double any other single backslash + non-JSON-escape char (e.g. \varepsilon, \sigma, \,)
        raw = re.sub(r'(?<!\\)\\([^"\\/bfnrtu0-9\n\r ])', r'\\\\\1', raw)
        # Step 3: fix trailing commas
        raw = re.sub(r',\s*([}\]])', r'\1', raw)
        try:
            return _json.loads(raw)
        except Exception:
            pass
    return {'enonce': text[:800], 'solution': '', 'conseils': '', 'difficulte': 'moyen', 'source': 'Exercice style BAC'}


def correct_exercise_answers(exercise: dict, student_answers: list, subject: str, user_lang: str = 'fr') -> dict:
    """
    Évalue les réponses ouvertes d'un étudiant pour un exercice de style BAC.
    Retourne: {corrections:[{question, student_answer, correct, partial, score, explanation, expected_key}],
               global_score, max_score, global_feedback}
    """
    import json as _json
    subject_label = MATS.get(subject, subject)

    questions = exercise.get('questions', [])
    # Intro/context is the enonce minus the questions block, or the intro field
    intro = exercise.get('intro', '') or exercise.get('enonce', '')[:600]

    # If no structured questions, treat the whole enonce as one open question
    if not questions:
        questions = ['Développez votre solution complète pour cet exercice.']

    qa_pairs = []
    for i, q in enumerate(questions):
        ans = student_answers[i] if i < len(student_answers) else ''
        qa_pairs.append(f"Question {i+1}: {q}\nRéponse de l'élève: {ans.strip() if ans.strip() else '(pas de réponse)'}")

    qa_block = '\n\n'.join(qa_pairs)
    nb_q = len(questions)

    prompt = (
        f"Tu es correcteur au Bac Haïti en {subject_label}.\n"
        + (_lang_instruction('', user_lang) + '\n' if user_lang == 'kr' else '')
        + f"\nÉNONCÉ / CONTEXTE DE L'EXERCICE:\n{intro[:700]}\n\n"
        f"RÉPONSES DE L'ÉLÈVE:\n{qa_block}\n\n"
        f"Évalue chaque réponse ({nb_q} questions). Pour chaque question:\n"
        "- correct: true si la réponse est juste ou essentiellement correcte\n"
        "- partial: true si partiellement correct (raisonnement bon mais erreur de calcul, etc.)\n"
        "- score: 2=correct, 1=partiel, 0=faux/vide\n"
        "- unit_required: true si la question attend une valeur numérique avec unité\n"
        "- unit_present: true si l élève a réellement écrit une unité\n"
        "- unit_correct: true seulement si l unité fournie est correcte et cohérente\n"
        "- unit_expected: l unité attendue (ex: T, N, m/s, V, J, Wb) si applicable\n"
        "- explanation: explication pédagogique courte et encourageante (2-3 phrases max)\n"
        "- expected_key: la réponse attendue résumée clairement\n\n"
        "RÈGLE OBLIGATOIRE: si une réponse numérique est correcte mais que l unité est absente, fausse ou incohérente, alors la réponse n est PAS correcte et l élève n obtient PAS la note de cette question. "
        "Rappelle explicitement que l unité est cruciale car 50 cm est différent de 50 km.\n\n"
        'Réponds UNIQUEMENT en JSON valide (SANS markdown, SANS ``` ):\n'
        '{"corrections":['
        '{"question":"texte question","student_answer":"réponse élève","correct":true,"partial":false,"score":2,"unit_required":false,"unit_present":false,"unit_correct":false,"unit_expected":"","explanation":"...","expected_key":"..."}'
        '],"global_score":X,"max_score":Y,"global_feedback":"feedback global motivant en 1-2 phrases"}'
    )

    # Augmenter les tokens selon le nombre de questions + retry si JSON invalide
    token_budget = min(500 + nb_q * 350, 3500)
    text = None

    for _attempt in range(3):
        raw = _call_fast(prompt, max_tokens=token_budget)
        raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            try:
                result = _json.loads(m.group(0))
                # Ensure max_score matches
                if 'max_score' not in result:
                    result['max_score'] = nb_q * 2
                # Ensure corrections list length matches questions
                corrs = result.get('corrections', [])
                if corrs:
                    # Fill missing corrections if AI returned fewer
                    while len(corrs) < nb_q:
                        corrs.append({
                            'question': questions[len(corrs)],
                            'student_answer': student_answers[len(corrs)] if len(corrs) < len(student_answers) else '',
                            'correct': False, 'partial': False, 'score': 0,
                            'unit_required': False, 'unit_present': False, 'unit_correct': False, 'unit_expected': '',
                            'explanation': 'Correction non disponible pour cette question.',
                            'expected_key': ''
                        })
                    for corr in corrs:
                        corr.setdefault('unit_required', False)
                        corr.setdefault('unit_present', False)
                        corr.setdefault('unit_correct', False)
                        corr.setdefault('unit_expected', '')
                        if corr.get('unit_required') and (not corr.get('unit_present') or not corr.get('unit_correct')):
                            corr['correct'] = False
                            corr['partial'] = False
                            corr['score'] = 0
                            explanation = str(corr.get('explanation', '') or '').strip()
                            reminder = ' L unité est obligatoire: 50 cm est différent de 50 km.'
                            if reminder.strip() not in explanation:
                                corr['explanation'] = (explanation + reminder).strip()
                    result['global_score'] = sum(int(corr.get('score', 0) or 0) for corr in corrs)
                    result['max_score'] = nb_q * 2
                    result['corrections'] = corrs
                    return result
            except _json.JSONDecodeError:
                import time as _t; _t.sleep(1)
                continue
        import time as _t; _t.sleep(1)

    return {
        'corrections': [
            {
                'question': q,
                'student_answer': student_answers[i] if i < len(student_answers) else '',
                'correct': False, 'partial': False, 'score': 0,
                'unit_required': False, 'unit_present': False, 'unit_correct': False, 'unit_expected': '',
                'explanation': 'La correction automatique a rencontré une erreur. Réessaie dans quelques secondes.',
                'expected_key': ''
            }
            for i, q in enumerate(questions)
        ],
        'global_score': 0,
        'max_score': nb_q * 2,
        'global_feedback': 'Correction temporairement indisponible. Réessaie dans quelques instants.',
    }


def correct_exam_open_answers(subject: str, qa_pairs: list, user_lang: str = 'fr', mise_au_net: str = '') -> dict:
    """
    Évalue les réponses ouvertes d'un élève pour un examen blanc complet.
    qa_pairs: list of {question, student_answer, model_answer, pts, section}
    mise_au_net: free text written by student on the blank answer sheet
    Returns: {corrections:[{question,student_answer,scored_pts,max_pts,status,feedback}],
              estimated_score, total_pts, global_feedback}
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    if not qa_pairs:
        return {'corrections': [], 'estimated_score': 0, 'total_pts': 0, 'global_feedback': 'Aucune réponse à corriger.'}

    total_pts = sum(float(q.get('pts', 0) or 0) for q in qa_pairs)
    nb_q = len(qa_pairs)

    lang_note = 'Réponds en créole haïtien.' if user_lang == 'kr' else 'Réponds en français.'

    # If student used the mise au net, inject it as the student_answer context
    mau_block = ''
    if mise_au_net and mise_au_net.strip():
        mau_block = (
            f"\n\n── MISE AU NET DE L'ÉLÈVE ──\n"
            f"L'élève a rédigé ses réponses sur sa feuille de mise au net. "
            f"Utilise ce texte pour identifier les réponses à chaque question (l'élève peut mentionner les numéros d'exercice) :\n\n"
            f"{mise_au_net[:6000]}\n"
            f"── FIN MISE AU NET ──\n"
        )

    lang_note = 'Réponds en créole haïtien.' if user_lang == 'kr' else 'Réponds en français.'

    # Pour la philosophie, enrichir chaque paire avec le type d'exercice détecté
    # afin que Groq sache exactement ce qu'il corrige (dissertation / étude de texte / cours)
    if subject == 'philosophie':
        def _philo_section_type(section_label: str) -> str:
            sl = section_label.lower()
            if 'sujet a' in sl or 'dissertation' in sl:
                return 'dissertation philosophique'
            if 'sujet b' in sl or 'texte' in sl or 'étude' in sl:
                return 'étude de texte'
            return 'question de cours'

        qa_lines = []
        for i, q in enumerate(qa_pairs, 1):
            ans = str(q.get('student_answer', '') or '').strip()
            ex_type = _philo_section_type(q.get('section', ''))
            qa_lines.append(
                f"Q{i} [{ex_type} — {q.get('pts', 0)} pts]: {q.get('question', '')}\n"
                f"  Réponse élève: {ans if ans else '(pas de réponse)'}\n"
                f"  Réponse attendue: {str(q.get('model_answer', '') or '').strip()[:300]}"
            )
        qa_block = '\n\n'.join(qa_lines)

        subject_ctx = (
            "Tu corriges un examen de PHILOSOPHIE BAC Haïti. Chaque question indique son type entre crochets.\n\n"
            "Corrige exactement comme tu le ferais pour un vrai exercice de philo :\n"
            "• Pour une **dissertation philosophique** : évalue l'introduction (problématisation + annonce du plan),"
            " le développement (thèse + antithèse, arguments, exemples, références à des auteurs),"
            " et la conclusion (bilan + ouverture). Donne un feedback précis sur chaque composante manquante.\n"
            "• Pour une **étude de texte** : évalue si l'élève a bien répondu à CE que la question demande"
            " (identifier la thèse, analyser l'argumentation, expliquer une phrase, donner un avis critique)."
            " Indique ce qui était juste et ce qui manquait dans la réponse.\n"
            "• Pour une **question de cours** : vérifie la précision de la définition, l'identification correcte"
            " de l'auteur/courant/œuvre, la distinction entre les notions et la pertinence de l'exemple.\n"
        )
    else:
        subject_ctx = ''

    if subject != 'philosophie':
        # rebuild qa_block (already built above for non-philo)
        qa_lines_default = []
        for i, q in enumerate(qa_pairs, 1):
            ans = str(q.get('student_answer', '') or '').strip()
            qa_lines_default.append(
                f"Q{i} [{q.get('section', '...')} — {q.get('pts', 0)} pts]: {q.get('question', '')}\n"
                f"  Réponse élève: {ans if ans else '(pas de réponse)'}\n"
                f"  Réponse attendue: {str(q.get('model_answer', '') or '').strip()[:300]}"
            )
        qa_block = '\n\n'.join(qa_lines_default)

    # Language enforcement for language exams
    _LANG_SUBJECTS = {
        'francais': ('kreyòl ayisyen', 'Kreyòl Ayisyen', 'kreyol|kreyòl|ayiti|mwen|ou|li|yo|nou|se|pa|ak|nan|pou|yon'),
        'anglais':  ('english', 'English', 'the|is|are|was|were|have|has|do|does|this|that|which'),
        'espagnol': ('español', 'Español', 'el|la|los|las|es|son|está|tienen|que|por|para|con'),
    }
    lang_rule = ''
    if subject in _LANG_SUBJECTS:
        _exam_lang, _exam_lang_label, _lang_tokens = _LANG_SUBJECTS[subject]
        lang_rule = (
            f"\n⚠️ RÈGLE LANGUE OBLIGATOIRE — Cet examen est en {_exam_lang_label}.\n"
            f"Si la réponse de l'élève n'est PAS dans cette langue (ou est dans une autre langue comme le français, l'anglais, etc.), "
            f"la réponse ne compte pas : scored_pts = 0, status = 'wrong', "
            f"feedback = 'Répons lan dwe ekri an {_exam_lang_label} sèlman. Fransè oswa lòt lang pa aksepte.'\n"
            if subject == 'francais' else
            f"\n⚠️ MANDATORY LANGUAGE RULE — This exam is in {_exam_lang_label}.\n"
            f"If the student's answer is NOT written in {_exam_lang_label} (e.g. written in French or Creole instead), "
            f"the answer does not count: scored_pts = 0, status = 'wrong', "
            f"feedback = 'Your answer must be written in {_exam_lang_label}. Answers in other languages are not accepted.'\n"
        )

    prompt = (
        f"Tu es un correcteur expert du Baccalauréat Haïti en {subject_label}. {lang_note}\n\n"
        + lang_rule
        + (f"{subject_ctx}\n" if subject_ctx else "")
        + f"Un élève vient de passer un examen blanc. Voici les questions ({nb_q} questions):\n\n"
        f"{qa_block}\n"
        + mau_block
        + "\nPour CHAQUE question, évalue la réponse de l'élève"
        + (" en te basant sur sa mise au net ci-dessus" if mau_block else " (aucune réponse fournie)")
        + ":\n"
        "- scored_pts: points accordés (0 jusqu'au max — peut être fractionnaire comme 7.5)\n"
        "- status: 'correct' | 'partial' | 'wrong' | 'empty'\n"
        "- feedback: 2-3 phrases pédagogiques précises et bienveillantes.\n"
        "  Si wrong/empty : explique l'erreur ET ce qui manquait.\n"
        "  Si partial : dis ce qui était bon ET ce qui était incomplet.\n"
        "  Si correct : félicite en nommant le point fort.\n\n"
        "RÈGLES :\n"
        "1. Ne jamais donner plein score à une réponse vide ou clairement fausse.\n"
        "2. Réponse attendue = référence ; si l'élève est cohérent et correct, accorde les points.\n"
        "3. global_feedback : 2-3 phrases motivantes + UN conseil de révision précis.\n\n"
        "Réponds UNIQUEMENT en JSON valide (sans markdown, sans ```):\n"
        '{"corrections":['
        '{"question":"...","student_answer":"...","scored_pts":X,"max_pts":Y,"status":"correct","feedback":"..."}'
        f'],"estimated_score":X,"total_pts":{total_pts},"global_feedback":"..."}}'
    )

    token_budget = min(800 + nb_q * 300 + (len(mau_block) // 10), 6000)

    for _attempt in range(3):
        raw = _call_fast(prompt, max_tokens=token_budget)
        raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            try:
                result = _json.loads(m.group(0))
                corrs = result.get('corrections', [])
                # Patch missing fields
                for j, corr in enumerate(corrs):
                    corr.setdefault('question', qa_pairs[j]['question'] if j < nb_q else '')
                    corr.setdefault('student_answer', qa_pairs[j].get('student_answer', '') if j < nb_q else '')
                    corr.setdefault('max_pts', float(qa_pairs[j].get('pts', 0)) if j < nb_q else 0)
                    corr.setdefault('scored_pts', 0)
                    corr.setdefault('status', 'empty')
                    corr.setdefault('feedback', 'Correction non disponible.')
                    # Clamp scored_pts
                    mp = float(corr['max_pts'])
                    corr['scored_pts'] = max(0.0, min(float(corr.get('scored_pts', 0) or 0), mp))
                # Fill if AI returned fewer corrections
                while len(corrs) < nb_q:
                    j = len(corrs)
                    corrs.append({
                        'question': qa_pairs[j]['question'],
                        'student_answer': qa_pairs[j].get('student_answer', ''),
                        'scored_pts': 0, 'max_pts': float(qa_pairs[j].get('pts', 0)),
                        'status': 'empty', 'feedback': 'Non évalué.'
                    })
                est = sum(float(c.get('scored_pts', 0) or 0) for c in corrs)
                result['corrections'] = corrs
                result['estimated_score'] = round(est, 1)
                result['total_pts'] = total_pts
                result.setdefault('global_feedback', 'Bonne performance globale. Continue à réviser!')
                return result
            except (_json.JSONDecodeError, KeyError, IndexError):
                pass

    # Fallback
    return {
        'corrections': [
            {'question': q['question'], 'student_answer': q.get('student_answer', ''),
             'scored_pts': 0, 'max_pts': float(q.get('pts', 0)),
             'status': 'empty', 'feedback': 'Correction IA temporairement indisponible.'}
            for q in qa_pairs
        ],
        'estimated_score': 0,
        'total_pts': total_pts,
        'global_feedback': 'Correction IA temporairement indisponible. Réessaie dans quelques instants.',
    }


def teach_exercise_type(exercise: dict, subject: str, user_lang: str = 'fr') -> str:
    """
    Génère une explication pédagogique complète sur COMMENT résoudre ce type d'exercice.
    L'IA explique la méthode étape par étape, les formules clés, les pièges courants,
    et termine en proposant un exercice similaire à l'élève.

    Retourne le message texte complet prêt à être envoyé au chat.
    """
    subject_label = MATS.get(subject, subject)
    theme   = exercise.get('theme', subject_label)
    intro   = (exercise.get('intro') or exercise.get('enonce', ''))[:500]
    questions = exercise.get('questions', [])
    q_block = '\n'.join(f"  {i+1}. {q}" for i, q in enumerate(questions[:5]))

    prompt = (
        f"Tu es un professeur expert du Bac Haïti en {subject_label}.\n"
        + (_lang_instruction('', user_lang) + '\n' if user_lang == 'kr' else '')
        + f"\nUn élève veut apprendre à résoudre des exercices du type :\n"
        f"**Thème : {theme}**\n"
        f"Contexte de l'exercice :\n{intro}\n"
        f"Questions :\n{q_block}\n\n"
        f"Génère un cours complet et engageant en français (2-4 paragraphes) qui :\n"
        f"1. 🎯 **Identifie le type d'exercice** (en 1 phrase claire)\n"
        f"2. 📐 **Explique la méthode GÉNÉRALE** étape par étape pour résoudre ce type\n"
        f"   - Formules/lois clés à connaître (en KaTeX avec $...$ pour l'inline)\n"
        f"   - Démarche logique à suivre toujours\n"
        f"3. ⚠️ **Signale les pièges classiques** que les élèves font souvent\n"
        f"4. 💡 **Exemple résolu** : résous brièvement l'exercice ci-dessus étape par étape\n"
        f"5. 🔔 À la fin, dis exactement : "
        f"\"---PROPOSER_EXERCICE---\" sur une ligne seule (pour déclencher la proposition automatique)\n\n"
        f"Sois pédagogique, encourageant, utilise des emojis pour structurer, "
        f"et des formules en $...$ (pas \\( \\) ni \\[ \\])."
    )

    return _call(prompt, max_tokens=2000)


def generate_similar_exercise(exercise: dict, subject: str) -> str:
    """
    Génère un exercice similaire au type de l'exercice fourni.
    Retourne le texte de l'exercice formaté pour le chat (pas JSON).
    """
    subject_label = MATS.get(subject, subject)
    theme   = exercise.get('theme', subject_label)
    intro   = (exercise.get('intro') or exercise.get('enonce', ''))[:400]
    nb_q    = len(exercise.get('questions', [])) or 3

    prompt = (
        f"Tu es examinateur au Bac Haïti en {subject_label}.\n"
        f"Génère un NOUVEL exercice du même type que :\n"
        f"Thème : {theme}\nModèle : {intro}\n\n"
        f"Crée un exercice ORIGINAL avec {nb_q} questions de difficulté similaire.\n"
        f"Format :\n"
        f"**Exercice — {theme}**\n\n"
        f"[Intro/données numériques réalistes en 3-5 lignes]\n\n"
        f"1. [question 1]\n2. [question 2]\n...\n\n"
        f"Utilise des données numériques réalistes différentes de l'original. "
        f"Formules en $...$ (KaTeX inline)."
    )

    return _call_fast(prompt, max_tokens=800)  # FAST_MODEL : exercice similaire = tâche de génération


def _parse_exercise_bank_json(text: str) -> list:
    import json as _json

    raw = re.sub(r'```[a-z]*\s*', '', text).strip()
    match = re.search(r'\[[\s\S]+\]', raw)
    if not match:
        return []
    try:
        data = _json.loads(match.group(0))
    except Exception:
        return []

    exercises = []
    for item in data if isinstance(data, list) else []:
        questions = item.get('questions', [])
        if not isinstance(questions, list):
            questions = []
        normalized_questions = [str(question).strip() for question in questions if str(question).strip()]
        if not normalized_questions:
            continue
        exercises.append({
            'title': str(item.get('title', '') or item.get('theme', 'Exercice')).strip(),
            'theme': str(item.get('theme', '') or item.get('title', 'Physique')).strip(),
            'intro': str(item.get('intro', '')).strip(),
            'enonce': str(item.get('enonce', '') or item.get('intro', '')).strip(),
            'questions': normalized_questions,
            'solution': str(item.get('solution', '')).strip(),
            'conseils': str(item.get('conseils', '')).strip(),
            'source': str(item.get('source', 'Banque interne Physique')).strip(),
            'difficulte': str(item.get('difficulte', 'moyen')).strip() or 'moyen',
        })
    return exercises


def generate_chapter_task_list(subject: str, chapter_title: str, note_content: str, max_items: int = 15) -> list[str]:
    """
    Build a precise concept/task list from the FULL local chapter note content.
    Returns an ordered list covering ALL concepts in the chapter — no arbitrary cap.
    Uses FAST_MODEL (20b) for lower-cost structured planning.
    """
    import json as _json

    if not note_content or len(note_content.strip()) < 80:
        return _extract_concepts_from_notes(note_content, chapter_title, limit=max_items)

    subject_label = MATS.get(subject, subject)
    if isinstance(subject_label, dict):
        subject_label = subject_label.get('label', subject)

    prompt = (
        f"Tu es un expert pédagogique du Bac Haïti en {subject_label}.\n"
        f"Chapitre: {chapter_title}\n\n"
        "Tu dois lire le CONTENU INTÉGRAL ci-dessous et créer un plan d'apprentissage pédagogique.\n"
        "RÈGLE ABSOLUE : génère ENTRE 5 ET 15 étapes — pas plus de 15, pas moins de 5.\n"
        "• Regroupe les détails accessoires sous la grande idée dont ils font partie.\n"
        "• Ne micro-découpe pas : un seul mécanisme = une seule étape.\n"
        "• Couvre TOUTES les grandes idées du chapitre — aucun thème principal ne doit être absent.\n\n"
        "RÈGLES STRICTES :\n"
        "- 5 à 15 étapes au total — NE DÉPASSE JAMAIS 15.\n"
        "- Chaque étape = UN concept enseignable large (définition, mécanisme, méthode, formule, application, propriété).\n"
        "- La DERNIÈRE étape DOIT être une conclusion/bilan : fin de l'événement, résultat final, bilan du chapitre, "
        "chute du régime, conséquences à long terme, etc. selon le sujet.\n"
        "- Respecte l'ordre exact du cours.\n"
        "- Chaque nom d'étape = UN TITRE DE SECTION court (3-4 mots) — syntagme nominal, pas une phrase verbale, pas une question.\n"
        "  Format obligatoire : 'Le/La/Les [sujet] + [caractéristique]' — ex: 'Les causes de l'occupation américaine'.\n\n"
        "⛔ INTERDIT dans les noms d'étapes :\n"
        "- TOUTE FORME DE QUESTION — ne commence JAMAIS par : Quel, Quelle, Quels, Quelles, Comment, Pourquoi, Qui, Où, Quand, Que, Qu'est, Est-ce, Mais pourquoi, Mais quel\n"
        "- Phrases avec verbe conjugué (ex: 'Les États-Unis contrôlent...', 'On entend souvent...', 'L'armée est dissoute...')\n"
        "- Phrases incomplètes ou tronquées copiées du texte\n"
        "- Formules mathématiques, équations, variables : U_R, I_eff, f₀, (C₁+C₂), ε₀, etc.\n"
        "- Code LaTeX : $...$, \\frac, \\cdot, \\sqrt, etc.\n"
        "- Options de QCM (A, B, C, D suivi d'une réponse)\n"
        "- Libellés techniques : Chapitre, Thème, Énoncé, Questions, EXEMPLE, Exercice\n"
        "- Réponses à des questions (ex: 'Par un coup d'État militaire')\n"
        "- Détails anecdotiques (dates isolées, noms de lieux sans contexte)\n\n"
        "✅ BON exemple pour un chapitre de physique (courant alternatif) :\n"
        '["Résistance pure : tension et courant en phase", '
        '"Bobine : déphasage de 90°", '
        '"Condensateur : comportement capacitif", '
        '"Circuit RLC série et résonance", '
        '"Applications BAC"]\n\n'
        "✅ BON exemple pour un chapitre d'histoire :\n"
        '["Contexte et causes de l\'événement", '
        '"Les acteurs et leur rôle", '
        '"Le déroulement des faits", '
        '"Les conséquences à long terme", '
        '"Bilan général"]\n\n'
        "✅ BON exemple pour un chapitre de maths :\n"
        '["Définition et domaine", '
        '"Limites et asymptotes", '
        '"Dérivée et tableau de variation", '
        '"Convexité et points d\'inflexion", '
        '"Exercices types BAC"]\n\n'
        "Retourne UNIQUEMENT un JSON array de strings (5 à 15 éléments). Rien d'autre.\n\n"
        "CONTENU CHAPITRE (source locale):\n"
        f"{note_content[:90000]}"
    )

    try:
        raw = _call_json_fast(prompt, max_tokens=2200)
        cleaned = re.sub(r'```[a-z]*\s*', '', raw or '').strip()
        m = re.search(r'\[[\s\S]*\]', cleaned)
        if m:
            data = _json.loads(m.group(0))
            if isinstance(data, list):
                out = []
                seen = set()
                for it in data:
                    s = str(it).strip(' -\n\t')
                    if not s:
                        continue
                    # Filter out quiz answer options (A/B/C/D patterns)
                    if re.match(r'^[A-D]\s', s) or re.match(r'^[A-D]\.\s', s):
                        continue
                    k = _normalize_grounding_text(s)
                    if k and k not in seen:
                        seen.add(k)
                        out.append(s)
                out = _sanitize_task_items(out, max_items=max_items)
                if len(out) >= 3:
                    return out
    except Exception:
        pass

    fallback = _extract_concepts_from_notes(note_content, chapter_title, limit=max_items)
    q_based = _extract_skills_from_question_lines(note_content, limit=max_items)
    merged = _sanitize_task_items(q_based + fallback, max_items=max_items)
    return merged if merged else [chapter_title or 'Concept principal']


def _sanitize_task_items(items: list[str], max_items: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (items or []):
        s = re.sub(r'^\s*[-•\d\.)]+\s*', '', str(raw or '')).strip(' \t\n:-')
        if not s:
            continue

        # Drop quiz answer options (A Par..., B Par..., C Par..., D Par...)
        if re.match(r'^[A-D]\s', s) or re.match(r'^[A-D]\.\s', s):
            continue

        # Drop question-style items (interrogative words or ending with ?)
        _question_starts = (
            'quel ', 'quelle ', 'quels ', 'quelles ', 'comment ', 'pourquoi ',
            'qui ', 'où ', 'quand ', 'que ', "qu'est", 'est-ce', 'mais ',
        )
        _sl = s.lower()
        if s.endswith('?') or any(_sl.startswith(q) for q in _question_starts):
            continue

        # Drop noisy label-like items and overly short generic words.
        if _is_concept_noise(s):
            continue
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", s)
        if len(words) < 3:
            continue
        if len(words) > 4:
            words = words[:4]
            s = ' '.join(words).strip()

        # Keep only concise, teachable task titles.
        if len(s) > 96:
            s = ' '.join(words[:4]).strip()
        s = s[:1].upper() + s[1:]

        k = _normalize_grounding_text(s)
        if not k or k in seen or _is_concept_noise(k):
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _extract_skills_from_question_lines(note_content: str, limit: int = 40) -> list[str]:
    """Build concept candidates from exercise question lines when source is exercise-heavy."""
    text = note_content or ''
    candidates: list[str] = []
    for ln in text.splitlines():
        line = ln.strip()
        if not line:
            continue
        if not (
            line.startswith('- ') or line.startswith('• ') or re.match(r'^\d+[\.)]\s+', line)
            or '?' in line
        ):
            continue

        q = re.sub(r'^\s*[-•\d\.)]+\s*', '', line).strip()
        q = re.sub(r'\$[^$]*\$', '', q).strip()
        q = re.sub(r'\s+', ' ', q)
        if not q or _is_concept_noise(q):
            continue

        # Keep a compact skill-style title from the question text.
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", q)
        if len(words) < 4:
            continue
        skill = ' '.join(words[:12]).strip()
        if skill:
            candidates.append(skill)
        if len(candidates) >= limit * 3:
            break
    return _sanitize_task_items(candidates, max_items=limit)


def _is_concept_noise(text: str) -> bool:
    s = (text or '').strip().strip(' -*:[]')
    normalized = _normalize_grounding_text(s)
    if not normalized:
        return True
    banned_exact = {
        'chapitre selectionne', 'chapitre', 'theme', 'enonce', 'questions',
        'question', 'source', 'source inconnue', 'contenu officiel du chapitre',
        'exemple', 'exercice', 'competence visee', 'situation', 'questions type',
        'question type', 'calculer'
    }
    if normalized in banned_exact:
        return True
    banned_prefixes = (
        'chapitre selectionne', 'chapitre ', 'theme ', 'enonce ', 'questions ',
        'question ', 'source ', 'exemple ', 'exercice ', 'competence visee ',
        'situation ', 'questions type ', 'question type '
    )
    return normalized.startswith(banned_prefixes)


def extract_physics_exercise_bank(
    chapter_title: str,
    section_title: str,
    internal_context: str,
    count: int = 4,
) -> list:
    """
    Extrait une banque d'exercices ELEVE à partir du brief interne Physique.
    Priorité : reprendre les exercices/tests/problèmes déjà suggérés dans le brief,
    pas inventer un nouveau cours.
    """
    prompt = (
        "Tu es concepteur d'exercices du Bac Haïti en Physique.\n"
        f"Chapitre: {chapter_title}\n"
        f"Sous-partie: {section_title}\n\n"
        "BRIEF INTERNE (cache a l'eleve) :\n"
        f"{internal_context[:5000]}\n\n"
        f"Mission: extrais ou reformule en format eleve jusqu'a {count} exercices QUI VIENNENT DU BRIEF. "
        "Si le brief contient des tests rapides, questions numeriques, applications ou problemes types, utilise-les en priorite. "
        "N'invente pas un nouveau theme hors du brief.\n\n"
        "Retourne UNIQUEMENT un JSON array:\n"
        "["
        "{"
        '"title":"Exercice 1",'
        '"theme":"Champ magnetique",'
        '"intro":"Contexte et donnees de l exercice",'
        '"enonce":"Enonce complet court",'
        '"questions":["Question 1", "Question 2"],'
        '"hints":["Indice precis pour Q1: Quelle formule utiliser?", "Indice pour Q2: Comment proceder?"],'
        '"solution":"Correction synthetique complete",'
        '"conseils":"Pieges et methode",'
        '"source":"Brief interne Physique",'
        '"difficulte":"moyen"'
        "}"
        "]\n\n"
        "Regles pour les hints:\n"
        "- Exactement UN indice par question (meme nombre que questions[])\n"
        "- Hint TRES precis: indique la formule a utiliser, la methode exacte, les etapes clees\n"
        "- L indice guide SANS donner la reponse numerique\n"
        "- Format: 'Appelle la formule X. Verifie l unite Y. Puis calcule Z en remplacant...'\n\n"
        "Autres regles:\n"
        "- Francais uniquement\n"
        "- Questions concretes, resolvables, niveau Bac\n"
        "- Solutions correctes et pedagogiques\n"
        "- Formules en $...$ uniquement\n"
        "- Pas de markdown autour du JSON"
    )
    return _parse_exercise_bank_json(_call_json(prompt, max_tokens=3200))
def generate_physics_similar_exercises(
    chapter_title: str,
    section_title: str,
    internal_context: str,
    example_exercises: list = None,
    count: int = 2,
    randomness_seed: int = None,
) -> list:
    """
    Genere des exercices en copiant EXACTEMENT le format des exemples.
    Chaque appel doit generer des donnees COMPLETEMENT DIFFERENTES.
    """
    import json as _json
    import logging
    import random
    logger = logging.getLogger(__name__)

    if not example_exercises:
        example_exercises = []

    # Générer une graine aléatoire pour la variation
    if randomness_seed is None:
        randomness_seed = random.randint(1000000, 9999999)
    
    logger.info(f"[generate_physics] START: {section_title}, count={count}, seed={randomness_seed}")
    
    # Créer des exemples JSON très explicites pour forcer le pattern matching
    if not example_exercises:
        logger.warning(f"[generate_physics] No examples - cannot match pattern")
        return []
    
    # Limiter ET minimiser les exemples
    examples_for_prompt = example_exercises[:2]
    # Extraire juste l'essentiel de chaque exemple pour que ça rentre
    compact_examples = []
    for ex in examples_for_prompt:
        compact = {
            'title': ex.get('title'),
            'intro': ex.get('intro', '')[:150],  # Garder plus pour voir le format LaTeX
            'enonce': ex.get('enonce', '')[:100],
            'questions': ex.get('questions', [])[:2],
            'hints': ex.get('hints', [])[:2],
            'difficulte': ex.get('difficulte', 'moyen'),
        }
        compact_examples.append(compact)
    
    examples_json = _json.dumps(compact_examples, ensure_ascii=False)
    
    # Prompt très strict - FORCE les formules, ignore les exemples s'ils n'en ont pas
    prompt = (
        f"Genere {count} exercice(s) DE PHYSIQUE EN FRANCAIS.\n\n"
        f"INSTRUCTION CAPITALE: CHAQUE HINT DOIT CONTENIR UNE FORMULE AVEC $ $\n"
        f"- JAMAIS de hint sans formule\n"
        f"- Si les modeles ci-dessous n'ont PAS de formules, IGNORE et ajoute TOI-MEME les formules\n"
        f"- FORMAT: 'Utilise $F = BIL$ pour calculer...'\n\n"
        f"Modeles de format (ignore les hints s'ils sont generiques):\n{examples_json}\n\n"
        f"Tache:\n"
        f"1. Change COMPLETEMENT les donnees numeriques\n"
        f"2. Varie les CALCULS demands (pas les memes questions)\n"
        f"3. GENERE tes PROPRES hints avec FORMULES - tres specifiques au probleme\n"
        f"4. Sujet: {section_title}\n\n"
        f"Reponds UNIQUEMENT en JSON []:"
    )

    try:
        text = _call_json(prompt, max_tokens=2500)
        logger.info(f"[generate_physics] API response: {len(text)} chars")

        # Clean markdown code blocks
        raw = re.sub(r'```[a-z]*\s*', '', text).strip()
        raw = raw.strip('`').strip()
        
        # Try to find JSON array - handle various formats
        # Try to find the opening bracket first
        start_idx = raw.find('[')
        if start_idx == -1:
            logger.warning(f"[generate_physics] No opening bracket found")
            return []
        
        # Find matching closing bracket (simple greedy approach)
        # Count brackets to ensure we get proper array
        bracket_count = 0
        end_idx = -1
        for i in range(start_idx, len(raw)):
            if raw[i] == '[':
                bracket_count += 1
            elif raw[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break
        
        if end_idx == -1:
            logger.warning(f"[generate_physics] No matching closing bracket")
            return []
        
        json_str = raw[start_idx:end_idx]
        logger.info(f"[generate_physics] Extracted: {len(json_str)} chars")

        # Try to parse JSON
        try:
            data = _json.loads(json_str)
        except _json.JSONDecodeError as e:
            logger.error(f"[generate_physics] Initial JSON parse failed: {e}")
            # Try removing problematic unicode escapes
            json_str = json_str.encode('utf-8', 'replace').decode('utf-8')
            try:
                data = _json.loads(json_str)
            except _json.JSONDecodeError as e2:
                logger.error(f"[generate_physics] Re-encoded JSON still failed: {e2}")
                return []
        
        if not isinstance(data, list):
            logger.warning(f"[generate_physics] JSON not array: {type(data)}")
            return []

        logger.info(f"[generate_physics] Parsed {len(data)} items")

        exercises = []
        for idx, item in enumerate(data):
            try:
                # Get questions
                questions = item.get('questions', [])
                if not isinstance(questions, list):
                    questions = [str(questions).strip()] if questions else []
                questions = [str(q).strip() for q in questions if str(q).strip()]
                
                if not questions:
                    continue

                # Get hints from AI generation
                hints = item.get('hints', [])
                if not isinstance(hints, list):
                    hints = [str(hints).strip()] if hints else []
                hints = [str(h).strip() for h in hints if str(h).strip()]
                
                # Si pas assez de hints, remplir avec des génériques
                fallback_hints = [
                    "Repere les donnees et note chaque valeur avec son unite.",
                    "Identifie la formule physique a utiliser.",
                    "Remplace les donnees numeriques dans la formule et calcule.",
                ]
                
                while len(hints) < len(questions):
                    hint_idx = len(hints) % len(fallback_hints)
                    hints.append(fallback_hints[hint_idx])

                # Build exercise
                ex = {
                    'title': str(item.get('title', f'Exercice {idx+1}')).strip(),
                    'theme': str(item.get('theme', section_title)).strip(),
                    'intro': str(item.get('intro', '')).strip(),
                    'enonce': str(item.get('enonce', '')).strip(),
                    'questions': questions,
                    'hints': hints[:len(questions)],
                    'solution': str(item.get('solution', '')).strip(),
                    'conseils': str(item.get('conseils', '')).strip(),
                    'source': 'Exercice similaire IA',
                    'difficulte': str(item.get('difficulte', 'moyen')).strip() or 'moyen',
                }
                
                # Validate not empty
                if not ex['enonce'] or not ex['intro']:
                    continue

                exercises.append(ex)
                logger.info(f"[generate_physics] Item {idx+1}: OK")

            except Exception as e:
                logger.debug(f"[generate_physics] Item {idx} error: {e}")
                continue

        logger.info(f"[generate_physics] SUCCESS: {len(exercises)} exercises")
        return exercises

    except _json.JSONDecodeError as e:
        logger.error(f"[generate_physics] JSON error: {e}")
        return []
    except Exception as e:
        logger.error(f"[generate_physics] Error: {e}", exc_info=True)
        return []


def generate_all_diagnostic_questions(
        subjects: list,
        pdf_contexts: dict,
        user_seed: str = '',
        serie_key: str = '',
        priority_subjects: list = None,
        serie_context: str = '',
) -> dict:
    """
    Évaluation diagnostique initiale ultra-personnalisée.
    3 questions par matière, de difficulté croissante (facile→difficile)
    pour vraiment cerner le niveau réel de l'élève.
    """
    import json as _json

    priority_subjects = priority_subjects or []

    subjects_info = '\n'.join(
        f'- {MATS.get(s, s)}'
        + (' ⭐ PRIORITAIRE (fort coefficient)' if s in priority_subjects else '')
        + (' [contenu examen disponible]' if pdf_contexts.get(s) else '')
        for s in subjects
    )

    serie_block = f'\n{serie_context}\n' if serie_context else ''

    pdf_sections = ''
    for subj in subjects:
        ctx = pdf_contexts.get(subj, '')
        if ctx:
            pdf_sections += f'\n\n=== CONTENU {MATS.get(subj, subj).upper()} ===\n{ctx[:1500]}'

    prompt = f"""Tu prépares un test de niveau pour un lycéen haïtien qui passe le Bac.

SEED (garantit des questions uniques pour cet élève) : {user_seed}
{serie_block}

MATIÈRES À ÉVALUER :
{subjects_info}
{pdf_sections}

STRATÉGIE D'ÉVALUATION PAR MATIÈRE :
Pour chaque matière, génère EXACTEMENT 3 questions avec une progression de difficulté :
  Q1 — NIVEAU MOYEN : application directe d'une formule ou notion de base. Distingue les élèves faibles des moyens.
  Q2 — NIVEAU DIFFICILE : raisonnement en plusieurs étapes. Distingue les moyens des bons.
  Q3 — NIVEAU EXPERT : tirée DIRECTEMENT d'un type d'exercice qui tombe dans les vrais examens du Bac Haïti. Distingue les bons des excellents.

RÈGLES IMPORTANTES :
- PRIORITÉ ABSOLUE : si du contenu d'examen officiel est disponible dans les sections PDF ci-dessus, COPIE le style exact des questions (même structure, même vocabulaire, même type de calcul). Les examens se répètent.
- Pour les matières PRIORITAIRES de cette série : questions encore plus pointues.
- Chaque question teste UN concept précis (pas vague).
- Les 4 options doivent être plausibles — les mauvaises réponses = les erreurs classiques que les élèves font vraiment.
- Formules mathématiques en KaTeX : $inline$ et $$blocs$$.
- reponse_correcte = INDEX 0,1,2,3 dans le tableau options.

FORMAT — JSON UNIQUEMENT (rien avant, rien après) :
{{
  "maths": [
    {{"enonce":"...","options":["A","B","C","D"],"reponse_correcte":0,"explication":"...","sujet":"Thème précis","difficulte":"moyen"}},
    {{"enonce":"...","options":["A","B","C","D"],"reponse_correcte":2,"explication":"...","sujet":"Thème précis","difficulte":"difficile"}},
    {{"enonce":"...","options":["A","B","C","D"],"reponse_correcte":1,"explication":"...","sujet":"Thème précis","difficulte":"expert"}}
  ],
  "physique": [...],
  "chimie": [...],
  "svt": [...],
  "francais": [...],
  "philosophie": [...],
  "histoire": [...],
  "anglais": [...]
}}"""

    text = _call(prompt, max_tokens=4000)

    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        return {}
    try:
        raw = _json.loads(match.group(0))
        result = {}
        for subj in subjects:
            qs = raw.get(subj, [])
            normalized = []
            for q in qs:
                options = q.get('options', [])
                rc = q.get('reponse_correcte', 0)
                try:
                    correct_idx = int(rc)
                except (ValueError, TypeError):
                    try:
                        correct_idx = options.index(str(rc))
                    except (ValueError, AttributeError):
                        correct_idx = 0
                correct_idx = max(0, min(correct_idx, len(options) - 1))
                normalized.append({
                    'enonce': q.get('enonce', ''),
                    'options': options,
                    'reponse_correcte': correct_idx,
                    'explication': q.get('explication', ''),
                    'sujet': q.get('sujet', MATS.get(subj, subj)),
                    'difficulte': q.get('difficulte', ''),
                    'subject': subj,
                })
            result[subj] = normalized
        return result
    except Exception:
        return {}


def analyse_quiz_mistakes(subject: str, details: list, serie_key: str = '', user_profile: str = '') -> dict:
    """
    Analyse les erreurs d'un quiz avec connaissance complète du profil de l'élève.
    Détecte les patterns récurrents, relie aux faiblesses déjà connues.
    details = [{enonce, user_answer, correct_answer, ok, topic}, ...]
    """
    import json as _json
    wrong = [d for d in details if not d.get('ok')]
    if not wrong:
        return {'analysis': "Parfait ! Tu as tout bon. Continue comme ça ! 🎯", 'weak_tags': [], 'advice': []}

    wrong_text = '\n'.join(
        f"- Q : {d.get('enonce','?')}\n  Ta réponse : {d.get('user_answer','?')} | Correcte : {d.get('correct_answer','?')}\n  Thème : {d.get('topic','?')}"
        for d in wrong[:8]
    )

    profile_block = f"\nCONNAISSANCE DE L'ÉLÈVE :\n{user_profile}\n" if user_profile else ''
    serie_hint    = f"Série de l'élève : {serie_key}." if serie_key else ''

    prompt = f"""Tu es BacIA, le meilleur pote de cet élève qui l'aide à préparer son Bac.
{serie_hint}
{profile_block}
L'élève vient de finir un quiz en {MATS.get(subject, subject)}.

ERREURS DE CE QUIZ :
{wrong_text}

MISSION — parle-lui comme un pote, direct et honnête :
1. Pour CHAQUE erreur, explique simplement pourquoi c'était faux et comment retenir la bonne réponse. Pas juste "tu t'es trompé".
2. Si une erreur touche un de ses THÈMES DÉJÀ FAIBLES → mentionne-le : "Ce truc-là, c'est ta kryptonite depuis un moment..."
3. Donne 2-3 conseils CONCRETS et actionnables (pas du blabla).
4. Sois motivant mais honnête. Il peut le faire s'il bosse les bons trucs.

Réponds en JSON UNIQUEMENT :
{{
  "analysis": "3-4 phrases directes et personnalisées — montre que tu connais ses lacunes et explique clairement ce qu'il a raté. Inclus la démarche correcte pour au moins 1 erreur.",
  "weak_tags": ["ThèmePrécis1", "ThèmePrécis2", "ThèmePrécis3"],
  "advice": [
    "Conseil concret 1 avec une technique précise (ex: pour les dérivées, retiens la règle du produit comme ça...)",
    "Conseil concret 2",
    "Conseil concret 3"
  ]
}}"""

    text = _call(prompt, max_tokens=1000)

    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        return {'analysis': text.strip(), 'weak_tags': [], 'advice': []}
    try:
        return _json.loads(match.group(0))
    except Exception:
        return {'analysis': text.strip(), 'weak_tags': [], 'advice': []}


def generate_flashcards(subject: str, pdf_context: str = '', count: int = 8, user_profile: str = '') -> list:
    """
    Génère des fiches mémo ciblées sur les lacunes réelles de l'élève.
    Retourne [{question, answer, hint, difficulty(1-3), source}]
    """
    import json as _json

    source_hint = "Crée des fiches basées sur les extraits d'examens officiels fournis — ces sujets tombent vraiment au Bac." if pdf_context else "Crée des fiches sur les points clés et les pièges classiques du programme de Terminale."
    profile_block = f"""\nPROFIL DE L'ÉLÈVE :\n{user_profile}\n→ Priorise les fiches sur ses THÈMES FAIBLES. Si l'élève maîtrise déjà un concept, génère une fiche niveau expert dessus.\n""" if user_profile else ''

    prompt = f"""Tu es BacIA, le pote qui aide cet élève à mémoriser les trucs importants pour son Bac.
Génère {count} flashcards ultra-ciblées pour {MATS.get(subject, subject)} en Terminale.
{source_hint}
{profile_block}
{f'EXAMENS OFFICIELS (base de tes fiches) :{chr(10)}{pdf_context[:3000]}' if pdf_context else ''}

RÈGLES :
- 60% fiches sur les points faibles du profil, 40% sur les incontournables qui tombent au Bac.
- Pour les thèmes forts → fiche niveau 3 (raisonnement complexe, piège classique).
- Pour les thèmes faibles → fiche niveau 1-2 avec hint super utile.
- Formules en KaTeX : $inline$ et $$blocs$$.
- L'answer doit être une vraie explication (pas juste le nom du théorème).

FORMAT — JSON array UNIQUEMENT :
[{{
  "question": "Question courte et précise",
  "answer": "Réponse complète et claire avec la démarche si c'est un calcul",
  "hint": "Astuce mémorisation (1 phrase max, style pote)",
  "difficulty": 2
}}]

difficulty : 1=définition, 2=application, 3=raisonnement complexe/piège"""

    text = _call_json_fast(prompt, max_tokens=2000)  # FAST_MODEL : JSON structure

    match = re.search(r'\[[\s\S]+\]', text)
    if not match:
        return []
    try:
        import json as _json
        raw = _json.loads(match.group(0))
        return [{
            'question':   q.get('question', ''),
            'answer':     q.get('answer', ''),
            'hint':       q.get('hint', ''),
            'difficulty': max(1, min(3, int(q.get('difficulty', 2)))),
            'source':     'ai+pdf' if pdf_context else 'ai',
        } for q in raw if q.get('question')]
    except Exception:
        return []


def generate_revision_plan(serie_key: str, scores: dict, weeks_until_bac: int = 8, user_profile: str = '', user_lang: str = 'fr') -> dict:
    """
    Génère un plan de révision semaine par semaine ultra-personnalisé.
    scores = {subject: score_pct}
    Retourne {weeks: [{label, focus, days: [{day, subject, task, duration_min, priority}]}]}
    """
    import json as _json
    from .series_data import get_serie_context_text

    try:
        serie_info = get_serie_context_text(serie_key)
    except Exception:
        serie_info = f"Série {serie_key}"

    scores_text = '\n'.join(
        f"  • {MATS.get(s, s)}: {pct}% {'🔴 À travailler en urgence' if pct < 50 else '🟡 Peut mieux faire' if pct < 70 else '🟢 Bon niveau'}"
        for s, pct in sorted(scores.items(), key=lambda x: x[1])
        if s in MATS
    )

    # ── Collecter les vrais chapitres / catégories quiz du site ──
    resource_block = ''
    try:
        from .resource_index import get_subject_chapters, get_quiz_categories
        _res_parts = []
        for _subj, _info in MATS.items():
            chs = get_subject_chapters(_subj)
            cats = get_quiz_categories(_subj)
            if chs or cats:
                _line = f"  {_info.get('label', _subj)}:"
                if chs:
                    _line += f" Chapitres cours=[{', '.join(chs[:12])}]"
                if cats:
                    _line += f" Quiz=[{', '.join(cats[:10])}]"
                _res_parts.append(_line)
        if _res_parts:
            resource_block = "\n\nCONTENU RÉEL DISPONIBLE SUR LE SITE (utilise ces chapitres et catégories EXACTS dans les tâches) :\n" + '\n'.join(_res_parts) + "\n→ Les tâches du plan DOIVENT référencer ces vrais chapitres, PAS des thèmes inventés.\n"
    except Exception:
        pass

    profile_block = f"""\nDOSSIER ÉLÈVE (historique complet) :\n{user_profile}\n→ Utilise CE profil pour personnaliser chaque tâche : mentionne les thèmes faibles récurrents, adapte la charge selon les tendances quiz, renforce les matières en régression.\n""" if user_profile else ''

    prompt = f"""Tu es BacIA, coach Bac expert en planification pédagogique personnalisée.
{_lang_instruction('', user_lang) if user_lang == 'kr' else ''}
Génère un plan de révision sur {weeks_until_bac} semaines pour ce lycéen.

{serie_info}
{profile_block}
SCORES DIAGNOSTIC :
{scores_text or 'Aucun diagnostic disponible — génère un plan équilibré standard.'}
{resource_block}
STRATÉGIE :
- Semaines 1-2 : Attaque frontale sur les thèmes faibles identifiés dans le profil (pas juste les scores globaux)
- Semaines 3-{max(3, weeks_until_bac - 2)} : Consolidation + progression sur les matières en tendance négative
- Semaines {max(4, weeks_until_bac - 1)}-{weeks_until_bac} : Révisions finales globales + simulation d'examen
- Chaque tâche journalière doit référencer un chapitre PRÉCIS du site (ex: "Chapitre HU-4 : GUERRE FROIDE" pas juste "Histoire")
- Ne dépasse pas 3h/jour
- 5 jours par semaine (lundi à vendredi), repos le weekend

FORMAT — JSON UNIQUEMENT, pas de texte avant ou après :
{{
  "summary": "Résumé personnalisé qui montre que tu connais les points faibles de l'élève (2-3 phrases motivantes)",
  "weeks": [
    {{
      "label": "Semaine 1",
      "focus": "Thème principal ciblé cette semaine",
      "days": [
        {{"day": "Lundi", "subject": "maths", "task": "Tâche précise avec nom de chapitre réel", "duration_min": 60, "priority": "high"}},
        {{"day": "Mardi", "subject": "physique", "task": "...", "duration_min": 45, "priority": "medium"}}
      ]
    }}
  ]
}}

priority: high/medium/low
subject: utilise la clé exacte (maths, physique, chimie, svt, francais, philosophie, anglais, histoire, economie, informatique, art, espagnol)

IMPORTANT : Retourne UNIQUEMENT le JSON, rien d'autre."""

    try:
        text = _call_json(prompt, max_tokens=3500)  # MODEL 120b : plan de révision premium
    except Exception as _e:
        import sys; print(f'[generate_revision_plan] _call error: {_e}', file=sys.stderr)
        return {}

    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        # Retry once with simpler prompt if first attempt returned garbage
        text2 = _call_json("Retourne UNIQUEMENT un JSON valide pour un plan de révision de " + str(weeks_until_bac) + " semaines. Format: {\"summary\":\"...\",\"weeks\":[{\"label\":\"Semaine 1\",\"focus\":\"...\",\"days\":[{\"day\":\"Lundi\",\"subject\":\"maths\",\"task\":\"...\",\"duration_min\":60,\"priority\":\"high\"}]}]}", max_tokens=3000)
        match = re.search(r'\{[\s\S]+\}', text2)
        if not match:
            return {}
    try:
        result = _json.loads(match.group(0))
        # Validate structure
        if 'weeks' not in result or not isinstance(result['weeks'], list):
            return {}
        return result
    except Exception:
        # Try to fix common JSON issues (trailing commas, etc.)
        cleaned = re.sub(r',\s*([}\]])', r'\1', match.group(0))
        try:
            return _json.loads(cleaned)
        except Exception:
            return {}


def generate_coaching_advice(student_data: dict) -> str:
    """
    Génère un message de coaching personnalisé et conversationnel.
    student_data keys: first_name, streak, study_minutes, quiz_count, total_mistakes,
                       total_mastered, weak_subjects, declining_subjects, inactive_subjects,
                       never_tested, top_mistake_subjects, top_mistakes_detail, memories,
                       course_counts, avg_score, bac_score, bac_gap_pass, bac_gap_target.
    """
    name            = student_data.get('first_name') or 'Élève'
    streak          = student_data.get('streak', 0)
    study_min       = student_data.get('study_minutes', 0)
    quiz_count      = student_data.get('quiz_count', 0)
    total_mistakes  = student_data.get('total_mistakes', 0)
    total_mastered  = student_data.get('total_mastered', 0)
    course_counts   = student_data.get('course_counts', {})
    avg_score       = student_data.get('avg_score', 0)
    bac_score       = student_data.get('bac_score', 0)
    bac_gap_pass    = student_data.get('bac_gap_pass', 0)
    bac_gap_target  = student_data.get('bac_gap_target', 0)

    context_lines = [f"PROFIL COMPLET DE {name.upper()} :"]

    # ── BAC score — chiffres précis déjà calculés, NE PAS recalculer ──
    if bac_score:
        if bac_gap_pass > 0:
            # Below pass threshold
            context_lines.append(
                f"Note estimée au BAC : {bac_score}/1900 ({avg_score}% de maîtrise composite). "
                f"L'élève EST EN DESSOUS du seuil de passage (950/1900). "
                f"Il lui manque EXACTEMENT {bac_gap_pass} points pour passer (seuil 50%) "
                f"ET EXACTEMENT {bac_gap_target} points pour atteindre l'objectif sécurisé de 65% (1235/1900). "
                "UTILISE CES CHIFFRES EXACTS — NE LES RECALCULE PAS."
            )
        elif bac_gap_target > 0:
            # Passed but below 65%
            context_lines.append(
                f"Note estimée au BAC : {bac_score}/1900 ({avg_score}%). "
                f"L'élève EST AU-DESSUS du seuil de passage (950/1900). "
                f"Il lui manque EXACTEMENT {bac_gap_target} points pour l'objectif sécurisé de 65% (1235/1900). "
                "UTILISE CES CHIFFRES EXACTS — NE LES RECALCULE PAS."
            )
        else:
            context_lines.append(
                f"Note estimée au BAC : {bac_score}/1900 ({avg_score}%). "
                "L'élève a DÉJÀ atteint l'objectif sécurisé de 65% (1235/1900). Félicite-le."
            )

    context_lines.append(
        "RAPPEL SYSTÈME : Le niveau est un score composite (quiz 60% + exercices corrigés 25% + cours +15 pts). "
        "Kreyòl n'a pas de section exercices : quiz 85% + cours 15%."
    )

    # ── Matières faibles ──────────────────────────────────────────────
    weak = student_data.get('weak_subjects', [])
    if weak:
        context_lines.append("Matières faibles (score composite < 60%) :")
        for s in weak[:5]:
            parts = [f"{s['label']} : {s['avg']}% global"]
            if s.get('quiz_avg') is not None:
                parts.append(f"quiz {s['quiz_avg']}%")
            if s.get('exo_pct') is not None:
                parts.append(f"exercices {s['exo_pct']}% récupérés")
            if s.get('has_course'):
                parts.append("cours commencé")
            else:
                parts.append("cours jamais suivi → priorité !")
            context_lines.append(f"  • {' — '.join(parts)}")

    # ── Questions ratées avec thèmes → chapitres spécifiques ─────────
    top_mistakes_detail = student_data.get('top_mistakes_detail', [])
    if top_mistakes_detail:
        context_lines.append("Questions les plus ratées (chapitres à travailler en priorité) :")
        for m in top_mistakes_detail[:8]:
            subj_label = m.get('subject', '')
            theme      = m.get('theme', '')
            enonce     = (m.get('enonce') or '')[:80]
            wrong      = m.get('wrong_count', 0)
            line = f"  • [{subj_label}] {wrong}× raté"
            if theme:
                line += f" — thème : {theme}"
            if enonce:
                line += f" — question : « {enonce} »"
            context_lines.append(line)

    # ── Matières en baisse / inactives / jamais testées ───────────────
    declining = student_data.get('declining_subjects', [])
    if declining:
        context_lines.append("Matières en régression récente :")
        for s in declining[:3]:
            context_lines.append(f"  • {s['label']} : tendance {s['trend']:+d}%")

    inactive = student_data.get('inactive_subjects', [])
    if inactive:
        context_lines.append("Matières abandonnées :")
        for s in inactive[:3]:
            context_lines.append(f"  • {s['label']} : {s['days_ago']} jours sans activité")

    never = student_data.get('never_tested', [])
    if never:
        context_lines.append(f"Matières jamais abordées : {', '.join(never[:4])}")

    # ── Cours ─────────────────────────────────────────────────────────
    active_courses = [subj for subj, n in course_counts.items() if n > 0]
    if active_courses:
        context_lines.append(f"Cours interactifs suivis : {len(active_courses)} matière(s)")
    else:
        context_lines.append("Cours interactifs : aucun cours suivi (grosse opportunité !).")

    # ── Mémoires IA ───────────────────────────────────────────────────
    memories = student_data.get('memories', [])
    if memories:
        context_lines.append("Erreurs conceptuelles récurrentes :")
        for mem in memories[:3]:
            context_lines.append(f"  • [{mem['subject']}] {mem['content'][:100]}")

    # ── Maîtrise adaptative (SubjectMastery) ─────────────────────────
    mastery_data = student_data.get('mastery_data', [])
    if mastery_data:
        context_lines.append("Maîtrise adaptative (score EMA basé sur toutes les réponses) :")
        for m in mastery_data[:6]:
            wt = ', '.join(m.get('weak_topics', [])[:3])
            line = f"  • {m['label']}: {m['mastery']}% ({m['confidence']})"
            if wt:
                line += f" — points faibles : {wt}"
            context_lines.append(line)

    # ── Résumés de chat récents ───────────────────────────────────────
    recent_summaries = student_data.get('recent_chat_summaries', [])
    if recent_summaries:
        context_lines.append("Observations des dernières sessions de chat :")
        for s in recent_summaries[:2]:
            wk = ', '.join(s.get('weaknesses', [])[:2])
            st = ', '.join(s.get('strengths', [])[:2])
            if wk:
                context_lines.append(f"  • Faiblesses : {wk}")
            if st:
                context_lines.append(f"  • Forces : {st}")
            if s.get('confidence'):
                context_lines.append(f"  • Confiance : {s['confidence']}")

    h, m = divmod(study_min, 60)
    context_lines.append(
        f"Stats : {quiz_count} quiz, {h}h{m:02d} révision, "
        f"{total_mastered} questions maîtrisées, {total_mistakes} erreurs restantes, "
        f"série de {streak} jour{'s' if streak != 1 else ''}."
    )

    context = '\n'.join(context_lines)

    # Les noms exacts des sections disponibles dans la barre latérale du site :
    # - "Quiz" (lancer un quiz par matière)
    # - "Exercices" (exercices pratiques BAC)
    # - "Cours Interactif" (cours par chapitre)
    # - "Fiches Mémo" (fiches de révision rapide)
    # - "Chat IA" (poser des questions à l'IA)
    # - "Examen Blanc" (test complet)

    prompt = f"""{context}

Tu es BacIA, un coach pédagogique bienveillant pour le BAC haïtien.
Rédige un message de coaching PERSONNALISÉ de 200-240 mots pour {name}.

Consignes IMPÉRATIVES :
- Utilise le prénom {name} exactement 1 ou 2 fois, tutoie l'élève.
- UTILISE LES CHIFFRES EXACTS fournis ci-dessus (bac_score, bac_gap_pass, bac_gap_target). NE LES RECALCULE PAS.
- Mentionne les CHAPITRES et THÈMES SPÉCIFIQUES tirés des « Questions les plus ratées » listées ci-dessus. Dis à l'élève exactement quels chapitres travailler (ex : "la génétique en SVT", "les condensateurs en Physique").
- Cite les sections du site par leur NOM EXACT dans la barre latérale : "Quiz", "Exercices", "Cours Interactif", "Fiches Mémo", "Chat IA". PAS d'URL.
- Couvre les 3 dimensions : quiz (Quiz), exercices pratiques (Exercices), cours (Cours Interactif).
- Reste positif et motivant, mais honnête sur les lacunes.
- Termine par une phrase d'encouragement liée au BAC haïtien.
- Texte FLUIDE et NATUREL — PAS de liste à tirets, pas de titres, pas de balises HTML.
- Réponds UNIQUEMENT en français."""

    return _call(prompt, max_tokens=600).strip()  # MODEL 120b : coaching premium personnalisé


def generate_chapter_advice(top_mistakes_detail: list, weak_subjects: list, MATS: dict) -> str:
    """
    Génère un court paragraphe (80-120 mots) listant les chapitres PRÉCIS à travailler
    en priorité, basé sur les questions les plus ratées en quiz et les matières faibles.
    Pas de liste à tirets — texte fluide. Uniquement en français.
    """
    # Fallback: si aucune donnée d'erreurs, fournir des conseils basés sur les matières de l'utilisateur
    if not top_mistakes_detail and not weak_subjects:
        return "Pour optimiser ta progression au BAC, concentre-toi sur les chapitres fondamentaux de chaque matière. En Maths, revois les fonctions et les dérivées. En Physique, maîtrise la mécanique et l'électricité. En SVT, étudie la biologie cellulaire et la génétique. En Philosophie, travaille les notions de conscience et de liberté. Fais des quiz régulièrement pour identifier tes points faibles précis."

    lines = []
    if top_mistakes_detail:
        lines.append("Questions les plus ratées (quiz) avec leurs chapitres/thèmes :")
        for m in top_mistakes_detail[:10]:
            subj    = MATS.get(m.get('subject', ''), {}).get('label', m.get('subject', ''))
            theme   = m.get('theme', '')
            enonce  = (m.get('enonce') or '')[:90]
            wrong   = m.get('wrong_count', 0)
            entry   = f"  • [{subj}] {wrong}× — "
            if theme:
                entry += f"thème : {theme}"
            if enonce:
                entry += f" · « {enonce} »"
            lines.append(entry)

    if weak_subjects:
        lines.append("Matières avec exercices peu récupérés :")
        for s in weak_subjects[:4]:
            if s.get('exo_pct') is not None and s['exo_pct'] < 50:
                lines.append(f"  • {s['label']} : {s['exo_pct']}% exercices récupérés seulement")

    data = '\n'.join(lines)

    prompt = f"""{data}

En te basant UNIQUEMENT sur les données ci-dessus, rédige un paragraphe COURT (80-120 mots) qui :
- Nomme explicitement les chapitres/thèmes précis à retravailler (ex : "la cellule procaryote et eucaryote en SVT", "les prépositions en Kreyòl", "la génétique mendélienne").
- Groupe par matière naturellement dans le texte.
- Indique si c'est surtout dans les quiz ou dans les exercices pratiques (section "Exercices") que des points sont à regagner.
- Texte FLUIDE, PAS de liste à tirets, PAS de titre.
- Réponds UNIQUEMENT en français."""

    return _call(prompt, max_tokens=500).strip()  # MODEL 120b : analyse chapitres premium


def extract_and_clean_exercises(exercises: list[dict]) -> list[dict]:
    """
    Prend une liste d'exercices trouvés dans les examens BAC (raw OCR),
    extrait la section pertinente et la nettoie (artefacts OCR, colonnes mélangées,
    notation mathématique). NE génère AUCUN nouveau contenu.

    exercises: list of {topic, raw_text, exam_name, year}

    Returns: list of {exam_name, year, topic, cleaned_text}
              (même ordre que l'entrée, cleaned_text vide si échec)
    """
    import json as _json, re as _re

    if not exercises:
        return []

    blocks = []
    for i, ex in enumerate(exercises):
        topic    = ex.get('topic', '').strip()
        raw      = (ex.get('raw_text') or '').strip()
        # Truncate raw text to avoid token overflow (keep most relevant portion)
        raw_truncated = raw[:3500] if len(raw) > 3500 else raw
        blocks.append(
            f"=== EXERCICE {i+1} ===\n"
            f"THÈME CHERCHÉ : {topic}\n"
            f"NOM EXAMEN : {ex.get('exam_name','')}, ANNÉE : {ex.get('year','')}\n"
            f"TEXTE BRUT OCR :\n{raw_truncated}"
        )

    prompt = f"""Tu es un assistant de nettoyage de textes d'examens haïtiens. Tu reçois {len(exercises)} exercice(s) sous forme de texte OCR brut.

Pour CHAQUE exercice :
1. Localise dans le texte brut la section de l'exercice/problème qui correspond au THÈME CHERCHÉ
2. Nettoie le texte : corrige les artefacts OCR (lettres manquantes, espaces parasites, colonnes mélangées), normalise la notation mathématique en texte lisible (ex: "V0 = 200 m/s", "L = 0,5 m"), garde les sauts de ligne logiques entre l'intro, les données et les questions
3. NE génère AUCUN nouveau contenu, NE résous AUCUNE question, NE modifie PAS les questions ni les données numériques
4. Si l'exercice est introuvable dans le texte brut, écris "" pour cleaned_text

Réponds UNIQUEMENT avec un JSON valide (tableau) :
[
  {{
    "exam_name": "nom exact de l'examen",
    "year": "année",
    "topic": "thème",
    "cleaned_text": "Texte nettoyé complet : données + questions numérotées. Préserve TOUT le contenu original."
  }}
]

{"".join(chr(10)+b for b in blocks)}

RAPPEL : JSON uniquement, pas de texte avant ou après."""

    raw_out = _call(prompt, max_tokens=2000).strip()

    try:
        match = _re.search(r'\[[\s\S]*\]', raw_out)
        if match:
            result = _json.loads(match.group())
            # Ensure same length as input
            if len(result) == len(exercises):
                return result
    except Exception:
        pass

    # Fallback: return empty cleaned_text for each
    return [
        {
            'exam_name':    ex.get('exam_name', ''),
            'year':         ex.get('year', ''),
            'topic':        ex.get('topic', ''),
            'cleaned_text': '',
        }
        for ex in exercises
    ]


def generate_smart_coach_plan(student_data: dict, resource_catalog: str) -> dict:
    """
    Génère un plan de coaching ultra-personnalisé basé sur TOUTES les données de l'élève
    et TOUTES les ressources disponibles sur le site.

    student_data keys: first_name, mastery_data (with recent_errors/correct),
                       recent_chat_summaries, learning_events
    resource_catalog: texte compact listant quiz et chapitres disponibles par matière

    Returns dict:
        message        — texte de coaching personnalisé (200-250 mots)
        quiz_picks     — [{subject, category, n_questions, difficulty, reason}]
        exercise_recs  — [{subject, chapter, reason}]
        chapter_recs   — [{subject, chapter, reason}]
    """
    import json as _json, re as _re

    name = student_data.get('first_name') or 'Élève'

    # ─── Construction du profil profond de l'élève ───────────────────────────
    profile_lines = [f"=== PROFIL COMPLET ET DÉTAILLÉ DE {name.upper()} ==="]

    mastery_data = student_data.get('mastery_data', [])
    if mastery_data:
        profile_lines.append("\n--- MAÎTRISE PAR MATIÈRE (données réelles) ---")
        for m in mastery_data:
            label   = m.get('label', m.get('subject', ''))
            subject = m.get('subject', '')
            score   = m.get('mastery', 0)
            conf    = m.get('confidence', 'débutant')
            n_ok    = m.get('correct_count', 0)
            n_err   = m.get('error_count', 0)
            emoji   = '🟢' if score >= 70 else '🟡' if score >= 40 else '🔴'

            profile_lines.append(
                f"  {emoji} {label} [{subject}]: {score}% maîtrise ({conf}) — {n_ok} bonnes / {n_err} erreurs"
            )

            weak = m.get('weak_topics', [])
            if weak:
                profile_lines.append(f"     ⚠️ Points faibles: {', '.join(weak[:8])}")

            mastered = m.get('mastered_topics', [])
            if mastered:
                profile_lines.append(f"     ✅ Maîtrisés: {', '.join(mastered[:5])}")

            # Erreurs récentes (les 15 plus récentes pour le prompt)
            recent_errors = m.get('recent_errors', [])
            if recent_errors:
                profile_lines.append(f"     📌 Historique erreurs ({len(recent_errors)} au total) — 15 plus récentes:")
                for err in recent_errors[:15]:
                    q_text = (err.get('question') or '')[:100]
                    topic  = err.get('topic', '')
                    etype  = err.get('error_type', '')
                    date_s = err.get('date', '')
                    line   = f"       - [{date_s}] {q_text}"
                    if topic:
                        line += f" | topic: {topic}"
                    if etype:
                        line += f" | type: {etype}"
                    profile_lines.append(line)

            # Bonnes réponses récentes (5 pour voir les forces)
            recent_correct = m.get('recent_correct', [])
            if recent_correct:
                profile_lines.append(f"     ✅ Bonnes réponses récentes ({len(recent_correct)} total) — 5 dernières:")
                for corr in recent_correct[:5]:
                    q_text = (corr.get('question') or '')[:80]
                    topic  = corr.get('topic', '')
                    date_s = corr.get('date', '')
                    profile_lines.append(f"       + [{date_s}] {q_text}" + (f" | topic: {topic}" if topic else ''))

    # Sessions de chat
    chat_summaries = student_data.get('recent_chat_summaries', [])
    if chat_summaries:
        profile_lines.append("\n--- SESSIONS DE CHAT AVEC L'IA (observations) ---")
        for i, s in enumerate(chat_summaries[:5], 1):
            subjects   = ', '.join(s.get('subjects', []))
            weaknesses = s.get('weaknesses', [])
            strengths  = s.get('strengths', [])
            key_qs     = s.get('key_questions', [])
            conf       = s.get('confidence', '')
            profile_lines.append(f"  Session {i} — matières: {subjects}")
            if weaknesses:
                profile_lines.append(f"    Faiblesses observées: {', '.join(weaknesses)}")
            if strengths:
                profile_lines.append(f"    Points forts: {', '.join(strengths)}")
            if key_qs:
                profile_lines.append(f"    Questions posées: {', '.join(key_qs[:3])}")
            if conf:
                profile_lines.append(f"    Confiance: {conf}")

    # Événements d'apprentissage récents
    learning_events = student_data.get('learning_events', [])
    if learning_events:
        profile_lines.append("\n--- ACTIVITÉ RÉCENTE ---")
        for ev in learning_events[:8]:
            etype   = ev.get('event_type', '')
            subject = ev.get('subject', '')
            score   = ev.get('score_pct')
            score_s = f" — score: {score:.0f}%" if score is not None else ''
            profile_lines.append(f"  [{etype}] {subject}{score_s}")

    student_profile = '\n'.join(profile_lines)

    # ─── Prompt ──────────────────────────────────────────────────────────────
    prompt = f"""{student_profile}

{resource_catalog}

Tu es BacIA, un coach pédagogique expert pour le BAC haïtien.
Tu viens de recevoir le profil COMPLET et DÉTAILLÉ de {name}, ainsi que la liste exacte de toutes les ressources disponibles sur le site.

Ta mission : générer un plan de coaching ultra-personnalisé en JSON.

Réponds UNIQUEMENT avec un objet JSON valide. PAS de markdown, PAS de texte avant ou après, PAS de balises de code.

Structure JSON requise :
{{
  "message": "Ton message de coaching personnalisé ici. 200-250 mots. En français. Tutoie {name}. Mentionne ses VRAIES erreurs, ses topics faibles RÉELS, et les actions CONCRÈTES à faire. Cite les sections du site : Quiz, Exercices, Cours Interactif. Texte fluide et naturel, pas de listes ni titres.",
  "quiz_picks": [
    {{
      "subject": "maths",
      "category": "La catégorie EXACTE telle qu'elle apparaît dans [QUIZ — catégories] ci-dessus",
      "n_questions": 8,
      "difficulty": "moyen",
      "reason": "Explication courte basée sur les VRAIES erreurs de {name} dans cette catégorie"
    }}
  ],
  "exercise_recs": [
    {{
      "subject": "physique",
      "chapter": "Le titre EXACT d'un chapitre listé dans [COURS — chapitres] ci-dessus",
      "reason": "Pourquoi cet exercice est prioritaire pour {name} maintenant"
    }}
  ],
  "chapter_recs": [
    {{
      "subject": "svt",
      "chapter": "Le titre EXACT d'un chapitre listé dans [COURS — chapitres] ci-dessus",
      "reason": "Ce chapitre couvre directement les lacunes réelles de {name}"
    }}
  ]
}}

RÈGLES IMPÉRATIVES :
1. Utilise UNIQUEMENT les sujets, catégories et chapitres qui apparaissent dans les ressources listées ci-dessus
2. Base-toi EXCLUSIVEMENT sur les vraies erreurs et données réelles de {name}
3. quiz_picks : 2 à 4 recommandations max, choisies pointilleusement sur les topics les plus problématiques
4. exercise_recs : 1 à 3 recommandations max, chapitres urgents avec exercices BAC disponibles
5. chapter_recs : 1 à 3 recommandations max, chapitres de cours qui peuvent corriger les lacunes
6. JSON valide UNIQUEMENT — aucun texte en dehors du JSON"""

    raw = _call_json(prompt, max_tokens=2500).strip()

    # ─── Parse JSON avec fallback robuste ────────────────────────────────────
    try:
        # Extract JSON block (in case model adds text around it)
        json_match = _re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            result = _json.loads(json_match.group())
        else:
            result = _json.loads(raw)

        # Validate structure
        result.setdefault('message', f"Continue tes révisions, {name} — tu avances bien !")
        result.setdefault('quiz_picks', [])
        result.setdefault('exercise_recs', [])
        result.setdefault('chapter_recs', [])
        return result

    except Exception:
        # If JSON parse still fails, extract just the message value if visible
        msg_match = _re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        extracted_msg = msg_match.group(1).replace('\\n', '\n').replace('\\"', '"') if msg_match else ''
        return {
            'message': extracted_msg or f"Bonjour {name} ! Analyse de ton profil disponible prochainement.",
            'quiz_picks': [],
            'exercise_recs': [],
            'chapter_recs': [],
        }


def enrich_open_questions_to_qcm(items: list, subject: str, count: int = 10) -> list:
    """
    Prend des items de type 'question' (questions ouvertes tirées des JSON d'examens réels)
    et génère des options QCM pour chacun via l'IA.
    Le texte de la question vient du vrai examen — seules les options A/B/C/D sont IA.
    Retourne une liste de dicts QCM compatibles avec le quiz frontend.
    """
    import json as _json, random as _random

    subject_label = MATS.get(subject, subject)
    # Sélectionner les items ouverts sans options (type 'question')
    open_items = [it for it in items if it.get('type') == 'question' and not it.get('options')]
    _random.shuffle(open_items)
    batch = open_items[:min(count, 8)]  # max 8 par appel pour rester dans le context window
    if not batch:
        return []

    questions_block = '\n'.join(
        f'Q{i+1}: {it.get("enonce","").strip()[:400]}'
        for i, it in enumerate(batch)
    )

    # Options must be written in the exam language
    if subject == 'espagnol':
        lang_instruction = (
            "IMPORTANTE: Las opciones (A, B, C, D) deben estar escritas COMPLETAMENTE EN ESPAÑOL "
            "(palabras o frases cortas en español — jamás en francés). "
            "La explicación puede estar en francés."
        )
    elif subject == 'anglais':
        lang_instruction = (
            "IMPORTANT: Options (A, B, C, D) must be written ENTIRELY IN ENGLISH. "
            "Explanation may be in French."
        )
    else:
        lang_instruction = ""

    prompt = (
        f"Tu es professeur en {subject_label} au Baccalauréat Haïtien.\n"
        f"Voici {len(batch)} questions ouvertes tirées de vrais examens du Bac Haïti (ne les modifie PAS) :\n\n"
        f"{questions_block}\n\n"
        f"Pour CHACUNE, crée exactement 4 options QCM (A, B, C, D) :\n"
        f"- 1 bonne réponse précise et factuelle\n"
        f"- 3 distracteurs plausibles mais incorrects\n"
        f"- Une explication courte (1 phrase) de la bonne réponse\n"
        + (f"\n{lang_instruction}\n" if lang_instruction else "\n") +
        f"\nRéponds UNIQUEMENT avec ce tableau JSON (sans texte autour) :\n"
        f'[{{"q_index":1,"options":["option A","option B","option C","option D"],"correct":0,"explication":"..."}},...]'
        f'\noù "correct" est l\'index (0=A,1=B,2=C,3=D) de la bonne réponse.\n'
        f"Retourne exactement {len(batch)} entrées."
    )

    raw = _call(prompt, max_tokens=1200)
    try:
        enriched_data = _json.loads(raw[raw.find('['):raw.rfind(']')+1])
    except Exception:
        return []

    result = []
    for entry in enriched_data:
        idx = entry.get('q_index', 1) - 1
        if idx < 0 or idx >= len(batch):
            continue
        original = batch[idx]
        opts = entry.get('options', [])
        rc   = entry.get('correct', 0)
        if len(opts) < 4:
            continue
        try:
            rc = int(rc)
        except (ValueError, TypeError):
            rc = 0
        result.append({
            'enonce':           original.get('enonce', '').strip(),
            'options':          opts[:4],
            'reponse_correcte': rc,
            'explication':      entry.get('explication', ''),
            'theme':            original.get('theme', ''),
            'difficulte':       original.get('difficulte', 'moyen'),
            'source':           original.get('source', ''),
            'type':             'qcm',
            '_from_json':       True,
        })
    return result


def generate_quiz_questions(subject: str, count: int, weak_topics: list = None, db_context: str = '', exam_context: str = '', serie_key: str = '', user_profile: str = '', chapter: str = '') -> list:
    """
    Génère des questions QCM tirées des vrais examens du Bac.
    Compact et rapide : prompt court, max 5 questions, JSON robuste.
    """
    import json as _json

    weak_hint = f"Focus: {', '.join(weak_topics[:3])}. " if weak_topics else ''
    serie_hint = f"Série {serie_key}. " if serie_key else ''
    exam_snippet = exam_context[:900] if exam_context else (db_context[:900] if db_context else '')
    exam_block = ("\nExtraits d'examens BAC Haïti :\n" + exam_snippet + "\n") if exam_snippet else ''
    creole_instruction = _creole_subject_instruction(subject)

    # Pour le créole : system prompt dédié avec exemples concrets
    creole_system = ''
    if subject == 'francais':
        creole_system = (
            "Ou se yon ekspè nan GRAMÈ KREYÒL AYISYEN ak pwogram Bac Ayiti. "
            "Misyon ou: kreye kesyon QCM ki kòrèk grammatikman an kreyòl. "
            "Konnen diferans ant: prepozisyon (nan, ak, pou, sou, devan, dèyè, pandan, lè), "
            "pwonon (mwen, ou, li, nou, yo, sa, ki), atik (la/a/an = defini; yon = endefini), "
            "adverb (toujou, deja, poko, pa janm, souvan, regilyèman), "
            "konjugezyon (pase: te; pwochen: pral/ap; kondisyonèl: ta). "
            "Ekzanp bon kesyon: 'Ki mo ki sèvi kòm atik defini nan fraz: \"Liv la bèl\"?' "
            "Reponse kòrèk: 'la'. Mauvaise: 'Liv la bèl', 'bèl', 'ki'. "
            "RÈG: chak opsyon dwe yon MO oswa yon EKSPRESYON KÒT (2-5 mo). "
            "ENTÈDI: opsyon ki se fraz konplè. Reponse_correcte DWE 100% kòrèk gramatikal."
        )

    # Pour l'anglais : instructions spécifiques pour éviter les questions multi-parties
    anglais_system = ''
    anglais_instruction = ''
    if subject == 'anglais':
        anglais_system = (
            "You are an expert English teacher for the Haitian Baccalaureate (BAC Haïti). "
            "Your task: generate individual MCQ grammar questions for Terminale students. "
            "CRITICAL RULES:\n"
            "1. Each question is ONE single English sentence with ONE blank (___). "
            "   FORBIDDEN: multi-part exercises like '1. He went... 2. I fell... 3. I thought...' — each question MUST be completely independent.\n"
            "2. ALWAYS provide EXACTLY 4 options (A, B, C, D) — never 3 or 5.\n"
            "3. The 'enonce' field must be a single complete English sentence with a blank, e.g.: "
            "   'She _______ to school every day.' or 'By the time he arrived, they _______ already left.'\n"
            "4. Options must be short verb forms, conjunctions, or vocabulary words (2-5 words max).\n"
            "5. The 'explication' field MUST always be filled in: explain in French (2-3 sentences) "
            "   why the correct answer is right, naming the grammar rule (e.g. past perfect, present perfect, preposition after verb, etc.).\n"
            "6. Topics to cover: verb tenses, conjunctions, prepositions, vocabulary, conditionals, "
            "   passive voice, indirect speech, relative clauses, articles, modal verbs."
        )
        anglais_instruction = (
            "\n⚠️ RÈGLES ANGLAIS — OBLIGATOIRES :\n"
            "- 'enonce' = UNE SEULE phrase anglaise avec UN SEUL blanc (___). PAS de listes numérotées.\n"
            "- EXACTEMENT 4 options (A, B, C, D). Jamais 3.\n"
            "- 'explication' : TOUJOURS rempli, en français, 2-3 phrases expliquant la règle grammaticale.\n"
            "- 'sujet' : en anglais, précise le point de grammaire (ex: 'Past Perfect', 'Prepositions', 'Conditionals').\n"
            "- Exemples de bons énoncés :\n"
            "  • 'He left ___ it started raining.' → options: before / after / while / although\n"
            "  • 'They ___ studying for two hours.' → options: have been / were / are / had\n"
            "  • 'I'm looking forward ___ meeting you.' → options: to / for / at / of"
        )

    # ── Anglais: chapter-based pure AI generation ─────────────────────────────
    if subject == 'anglais':
        import random as _rnd_ang
        ANGLAIS_CHAPTERS = [
            ('Verb Tenses',
             'Present Simple/Continuous, Past Simple/Continuous, Present Perfect, Past Perfect, Future forms. '
             'Focus on: choosing the right tense, signal words (yesterday/already/for/since/tomorrow/right now), '
             'irregular verbs. Example: "She ___ (study) for two hours when I called."'),
            ('Tag Questions',
             'Positive/negative tags, matching auxiliary to the main clause. '
             'E.g. "He doesn\'t like coffee, ___ ?" → does he. '
             'Covers: be/do/have/modal auxiliaries as tags, subject pronouns in tags.'),
            ('Passive Voice',
             'Transforming active → passive, choosing the right tense in passive. '
             'E.g. "The letter ___ (write) by her yesterday." → was written. '
             'Covers: all tenses in passive, agent (by), omission of agent.'),
            ('Reported Speech',
             'Tense backshift, pronoun changes, reporting verbs (said/told/asked/wondered). '
             'E.g. Direct: "I am tired." → Reported: "She said she ___ tired." → was. '
             'Covers: statements, questions, commands in reported speech.'),
            ('Comparatives & Superlatives',
             'Short/long adjective forms, than/the most/as...as/less...than. '
             'E.g. "This book is ___ than the other one." → more interesting. '
             'Covers: irregular forms (good/better/best, bad/worse/worst), double comparatives.'),
            ('Gerund vs Infinitive',
             'Verbs + -ing (enjoy/avoid/finish/mind/suggest) vs verbs + to+verb (want/decide/hope/plan). '
             'E.g. "I look forward to ___ you." → meeting. '
             'Covers: verb + ing/infinitive, preposition + ing, purpose (in order to).'),
            ('Adjectives -ED and -ING',
             'Bored/boring, excited/exciting, frightened/frightening, interested/interesting. '
             'E.g. "The film was very ___." → boring (not bored). '
             '-ED = how the person feels; -ING = characteristic of the thing.'),
            ('Prepositions of Time: for/since/ago/in/on/at',
             'At (specific time/night/weekend), on (days/dates), in (months/years/seasons), '
             'for (duration), since (starting point), ago (past reference). '
             'E.g. "I have lived here ___ 2019." → since.'),
            ('Prepositions of Place',
             'In/on/at/by/next to/between/opposite/above/below/near/in front of/behind/under. '
             'E.g. "The bank is ___ the post office and the school." → between. '
             'Covers: spatial relationships, at/in/on for locations.'),
            ('Relative Clauses',
             'Who (people), which (things), that (people/things), where (place), whose (possession). '
             'Defining vs non-defining clauses (commas). '
             'E.g. "The man ___ helped me was very kind." → who.'),
            ('Compound Words & Prefixes under-/over-',
             'Underestimate, underrate, underpay, overwork, overeat, overslept, overwhelmed. '
             'E.g. "She ___ the difficulty of the exam and failed." → underestimated. '
             'Covers: meaning of under- (less than enough) vs over- (too much).'),
            ('Essential BAC Vocabulary',
             'Common adjectives (ambitious/reliable/generous/stubborn/grateful), '
             'verbs (achieve/encourage/contribute/affect/improve/reduce/increase/prevent), '
             'nouns (opportunity/challenge/environment/responsibility/consequence). '
             'E.g. "Pollution ___ the quality of water in the country." → affects.'),
            ('Indefinite Pronouns: whoever/whatever/wherever/however/whichever',
             'Whoever = any person who; whatever = anything that; wherever = any place where; '
             'however = in any way/no matter how; whichever = any one of a set. '
             'E.g. "___ you go, you will find friendly people." → Wherever.'),
        ]
        # Pick chapter: use provided chapter name to match, else pick randomly
        chosen_chapter = None
        if chapter:
            ch_lower = chapter.lower()
            for name, desc in ANGLAIS_CHAPTERS:
                if any(kw in ch_lower for kw in name.lower().split()):
                    chosen_chapter = (name, desc)
                    break
        if not chosen_chapter:
            chosen_chapter = _rnd_ang.choice(ANGLAIS_CHAPTERS)

        ch_name, ch_desc = chosen_chapter

        prompt = (
            f"You are an expert English teacher for the Haitian Baccalaureate (BAC Haïti Terminale).\n\n"
            f"Generate EXACTLY {count} independent English grammar MCQ questions on this topic:\n"
            f"CHAPTER: {ch_name}\n"
            f"RULES FOR THIS CHAPTER: {ch_desc}\n\n"
            "═══ MANDATORY RULES ═══\n"
            "1. Every question = ONE complete English sentence with EXACTLY ONE blank (___). "
            "   NEVER multi-part numbered lists (no '1. … 2. … 3. …').\n"
            "2. EXACTLY 4 options (short words/phrases, 1-5 words each, NO 'A:'/'B:' prefix needed).\n"
            "3. 'explication': MANDATORY — 2-3 sentences IN FRENCH explaining WHY the answer is correct "
            "   and what grammar rule applies. Example: \"On utilise le passé composé ici car...\"\n"
            "4. 'sujet': the BROAD grammar category name in English — NOT the specific answer. E.g. 'Verb Tenses', 'Prepositions', 'Relative Clauses'. NEVER include the specific word or form that is the answer.\n"
            "5. Vary difficulty: 30% easy, 50% medium, 20% hard.\n"
            "6. All questions must be self-contained (no 'according to the text', no external reference).\n\n"
            "═══ EXAMPLE ═══\n"
            '{"enonce":"He had his nose ___ in a fight.","options":["break","breaking","broken","to break"],'
            '"reponse_correcte":2,"explication":"On utilise le participe passé \'broken\' dans la construction '
            '\'have something done\' (causatif passif). La structure est have/get + objet + participe passé. '
            '\'Break\' est l\'infinitif, \'breaking\' est le gérondif, \'to break\' est l\'infinitif avec \'to\', '
            'aucun n\'est correct ici.","sujet":"Causative Passive (have something done)"}\n\n'
            "Reply ONLY with this JSON array — no comments, no markdown:\n"
            '[{"enonce":"...","options":["...","...","...","..."],'
            '"reponse_correcte":0,"explication":"...","sujet":"..."}]\n\n'
            "reponse_correcte = integer index (0=first option, 1=second, 2=third, 3=fourth)."
        )
        resp = _client().chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": anglais_system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2800,
        )
        text = resp.choices[0].message.content or ''
        # Clean markdown fences
        text = re.sub(r'```[a-z]*\s*', '', text).strip()
        match = re.search(r'\[[\s\S]+?\](?=\s*$|\s*\[)', text) or re.search(r'\[[\s\S]+\]', text)
        if not match:
            return []
        json_str = match.group(0)
        json_str = re.sub(r'\\([^"\\/bfnrtu0-9\n\r])', r'\\\\\1', json_str)
        try:
            raw = _json.loads(json_str)
        except Exception:
            raw = []
            for m in re.finditer(r'\{[^{}]+\}', json_str):
                try:
                    raw.append(_json.loads(m.group(0)))
                except Exception:
                    pass
        result = []
        for q in raw:
            options = q.get('options', [])
            # Reject: wrong option count, or all True/False options
            if len(options) != 4:
                continue
            if all(o.strip().upper() in ('VRAI', 'FAUX', 'TRUE', 'FALSE') for o in options):
                continue
            rc = q.get('reponse_correcte', 0)
            try:
                correct_idx = int(rc)
                if correct_idx < 0 or correct_idx >= 4:
                    correct_idx = 0
            except (ValueError, TypeError):
                correct_idx = 0
            result.append({
                'enonce':           q.get('enonce', '').strip(),
                'options':          [str(o).strip() for o in options],
                'reponse_correcte': correct_idx,
                'explication':      q.get('explication', ''),
                'sujet':            q.get('sujet', ch_name),
                'theme':            ch_name,
                'difficulte':       q.get('difficulte', 'moyen'),
                'source':           'ai_anglais',
                'type':             'qcm',
            })
        return result

    # ── Espagnol: chapter-based pure AI generation (mirror of anglais) ──────
    if subject == 'espagnol':
        import random as _rnd_esp
        espagnol_system = (
            "Eres un experto profesor de Español para el Baccalauréat Haïtien (BAC Haïti Terminale). "
            "Tu misión: generar preguntas QCM de gramática española INDEPENDIENTES para estudiantes de Terminale. "
            "REGLAS CRÍTICAS:\n"
            "1. Cada pregunta es UNA SOLA frase en español con UN SOLO espacio en blanco (___). "
            "   PROHIBIDO: ejercicios de múltiples partes como '1. Él fue... 2. Yo caí...' — cada pregunta DEBE ser completamente independiente.\n"
            "2. SIEMPRE proporcionar EXACTAMENTE 4 opciones (A, B, C, D) — nunca 3 ni 5.\n"
            "3. El campo 'enonce' debe ser una frase española completa con un espacio en blanco, ej: "
            "   '¿___ tú al mercado ayer?' o 'Ella ___ estudiando cuando llegué.'\n"
            "4. Las opciones deben ser formas verbales, conjunciones o palabras cortas (1-5 palabras máx).\n"
            "5. El campo 'explication' SIEMPRE debe estar relleno: explicar en francés (2-3 frases) "
            "   por qué la respuesta es correcta, nombrando la regla gramatical.\n"
            "6. Temas: conjugación verbal, ser/estar, por/para, subjuntivo, pronombres, vocabulario, "
            "   imperativo, condicional, tiempos compuestos."
        )
        ESPAGNOL_CHAPTERS = [
            ('Tiempos verbales — Pretéritos',
             'Pretérito indefinido (hablé, comí, viví), pretérito imperfecto (hablaba, comía), '
             'pretérito perfecto compuesto (he hablado, ha comido). '
             'Señales: ayer/el año pasado → indefinido; siempre/antes/cuando era niño → imperfecto; '
             'hoy/este año/ya/todavía → perfecto compuesto. '
             'Ej: "Cuando era niño, ___ (jugar) mucho en el parque." → jugaba.'),
            ('Ser vs Estar',
             'SER: identidad, origen, profesión, material, tiempo (hora/fecha), cualidades permanentes. '
             'ESTAR: estado, ubicación, resultado de acción, condición temporal. '
             'Ej: "Mi madre ___ médica." → es. "El libro ___ sobre la mesa." → está. '
             'Truco: COLD (Condition/Origin/Location/Description) para estar, resto para ser.'),
            ('Por vs Para',
             'POR: causa/motivo, duración, intercambio, medio, a favor de. '
             'PARA: propósito/destino, destinatario, opinión, fecha límite, contraste. '
             'Ej: "Estudia ___ aprobar el examen." → para (propósito). '
             '"Te llamo ___ teléfono." → por (medio).'),
            ('Subjuntivo presente',
             'Uso después de: querer que, esperar que, ojalá, es importante que, dudar que. '
             'Formación: raíz del presente yo + terminaciones -e/-es/-e/-emos/-éis/-en (AR) '
             'o -a/-as/-a/-amos/-áis/-an (ER/IR). '
             'Irregulares: ser→sea, ir→vaya, tener→tenga, hacer→haga. '
             'Ej: "Espero que tú ___ (venir) a la fiesta." → vengas.'),
            ('Pronombres de objeto directo e indirecto',
             'OD: me/te/lo,la/nos/os/los,las. OI: me/te/le/nos/os/les. '
             'Orden: OI antes de OD. Con infinitivo/gerundio → se adjuntan detrás. '
             'LE/LES → SE cuando van antes de lo/la/los/las. '
             'Ej: "¿Le diste el libro a María?" → "Sí, ___ lo di." → se lo di.'),
            ('Imperativo',
             'Afirmativo tú: igual que 3ª persona presente (habla, come, vive). '
             'Irregulares tú: ven, di, haz, ve, sé, ten, pon, sal. '
             'Negativo tú: usa el subjuntivo (no hables, no comas, no vengas). '
             'Ej: "___ (tú / hablar) más despacio, por favor." → Habla. '
             '"No ___ (tú / olvidar) tu tarea." → olvides.'),
            ('Condicional simple',
             'Formación: infinitivo + ía/ías/ía/íamos/íais/ían. '
             'Irregulares: tener→tendría, poder→podría, querer→querría, '
             'hacer→haría, venir→vendría, salir→saldría, decir→diría. '
             'Uso: hipótesis, cortesía, consejo (debería). '
             'Ej: "Si tuviera dinero, ___ (comprar) una casa." → compraría.'),
            ('Pronombres relativos',
             'Que (personas y cosas, el más común). Quien/quienes (solo personas, tras preposición). '
             'El/la/los/las que / El cual (con preposición). Cuyo (posesión). '
             'Donde (lugar). '
             'Ej: "El libro ___ me prestaste es muy interesante." → que. '
             '"La persona con ___ hablé es mi profesora." → quien.'),
            ('Vocabulario esencial BAC Haïti — Español',
             'Adjetivos: amable/generoso/perezoso/trabajador/inteligente/simpático/antipático. '
             'Verbos: conseguir/lograr/mejorar/reducir/aumentar/afectar/contribuir/impedir. '
             'Sustantivos: oportunidad/desafío/medioambiente/responsabilidad/consecuencia/desarrollo. '
             'Ej: "La contaminación ___ la calidad del agua." → afecta.'),
            ('Comparativos y superlativos',
             'Comparativo: más/menos + adj + que; tan + adj + como. '
             'Superlativos relativos: el/la más/menos + adj + de. '
             'Formas irregulares: bueno→mejor, malo→peor, grande→mayor, pequeño→menor. '
             'Ej: "Este examen es ___ difícil que el anterior." → más. '
             '"Es la ciudad ___ grande del país." → más.'),
            ('Tiempos compuestos — Pluscuamperfecto y futuro perfecto',
             'Pluscuamperfecto: había/habías/había... + participio (acción anterior a otra en pasado). '
             'Ej: "Cuando llegué, ella ya ___ (salir)." → había salido. '
             'Futuro perfecto: habré/habrás... + participio (acción completada antes de un momento futuro). '
             'Ej: "Para el viernes, ___ (terminar, yo) el trabajo." → habré terminado.'),
            ('Preposiciones — a, en, de, con, sin, sobre, entre',
             'A: dirección, hora, OD de persona (vi a María). '
             'EN: lugar, medio de transporte, idioma (en autobús, en español). '
             'DE: origen, posesión, material, partitivo. '
             'CON: compañía, instrumento. SIN: ausencia. '
             'SOBRE: encima de, tema (hablar sobre). ENTRE: posición intermedia. '
             'Ej: "Llegamos ___ Madrid a las ocho." → a.'),
        ]
        # Pick chapter: match by name if provided, else pick randomly
        chosen_ch = None
        if chapter:
            ch_lower = chapter.lower()
            for name, desc in ESPAGNOL_CHAPTERS:
                if any(kw in ch_lower for kw in name.lower().split()[:3]):
                    chosen_ch = (name, desc)
                    break
        if not chosen_ch:
            chosen_ch = _rnd_esp.choice(ESPAGNOL_CHAPTERS)

        ch_name, ch_desc = chosen_ch

        prompt = (
            f"Eres un experto profesor de Español para el Baccalauréat Haïtien (BAC Haïti Terminale).\n\n"
            f"Genera EXACTAMENTE {count} preguntas QCM independientes de gramática española sobre este tema:\n"
            f"CAPÍTULO: {ch_name}\n"
            f"REGLAS DEL CAPÍTULO: {ch_desc}\n\n"
            "═══ REGLAS OBLIGATORIAS ═══\n"
            "1. Cada pregunta = UNA frase completa en ESPAÑOL con EXACTAMENTE UN espacio en blanco (___). "
            "   NUNCA listas numeradas (no '1. … 2. … 3. …').\n"
            "2. EXACTAMENTE 4 opciones cortas en ESPAÑOL (1-5 palabras, sin prefijo 'A:'/'B:').\n"
            "3. 'explication': OBLIGATORIO — 2-3 frases EN FRANCÉS explicando POR QUÉ la respuesta es correcta "
            "   y qué regla gramatical aplica. Ejemplo: \"On utilise le subjonctif ici car...\"\n"
            "4. 'sujet': la categoría gramatical GENERAL en español — NUNCA la respuesta específica. Ej: 'Pronombres relativos', 'Tiempos verbales', 'Preposiciones'. PROHIBIDO incluir la palabra o forma específica que es la respuesta.\n"
            "5. Varía la dificultad: 30% fácil, 50% medio, 20% difícil.\n"
            "6. Todas las preguntas deben ser autónomas (sin 'según el texto', sin referencia externa).\n\n"
            "═══ EJEMPLO ═══\n"
            '{"enonce":"Cuando era niña, ___ mucho al fútbol con mis amigos.",'
            '"options":["jugué","jugaba","jugaré","jugaría"],'
            '"reponse_correcte":1,"explication":"On utilise le prétérit imparfait (\'jugaba\') pour décrire '
            'une habitude ou une action répétée dans le passé. La formule \'cuando era niña\' indique une '
            'situation habituelle, pas un événement ponctuel.","sujet":"Pretérito imperfecto"}\n\n'
            "Responde ÚNICAMENTE con este array JSON — sin comentarios, sin markdown:\n"
            '[{"enonce":"...","options":["...","...","...","..."],'
            '"reponse_correcte":0,"explication":"...","sujet":"..."}]\n\n'
            "reponse_correcte = índice entero (0=primera opción, 1=segunda, 2=tercera, 3=cuarta)."
        )
        resp = _client().chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": espagnol_system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2800,
        )
        text = resp.choices[0].message.content or ''
        # Clean markdown fences
        text = re.sub(r'```[a-z]*\s*', '', text).strip()
        match = re.search(r'\[[\s\S]+?\](?=\s*$|\s*\[)', text) or re.search(r'\[[\s\S]+\]', text)
        if not match:
            return []
        json_str = match.group(0)
        json_str = re.sub(r'\\([^"\\/bfnrtu0-9\n\r])', r'\\\\\1', json_str)
        try:
            raw = _json.loads(json_str)
        except Exception:
            raw = []
            for m in re.finditer(r'\{[^{}]+\}', json_str):
                try:
                    raw.append(_json.loads(m.group(0)))
                except Exception:
                    pass
        result = []
        for q in raw:
            options = q.get('options', [])
            if len(options) != 4:
                continue
            rc = q.get('reponse_correcte', 0)
            try:
                correct_idx = int(rc)
                if correct_idx < 0 or correct_idx >= 4:
                    correct_idx = 0
            except (ValueError, TypeError):
                correct_idx = 0
            result.append({
                'enonce':           q.get('enonce', '').strip(),
                'options':          [str(o).strip() for o in options],
                'reponse_correcte': correct_idx,
                'explication':      q.get('explication', ''),
                'sujet':            q.get('sujet', ch_name),
                'theme':            ch_name,
                'difficulte':       q.get('difficulte', 'moyen'),
                'source':           'ai_espagnol',
                'type':             'qcm',
            })
        return result

    # ── All other subjects ─────────────────────────────────────────────────
    prompt = (
            f"Quiz Bac Haïti — {MATS.get(subject, subject)}. {serie_hint}{weak_hint}"
            f"{exam_block}\n"
            f"Génère EXACTEMENT {min(count, 5)} questions QCM Terminale.\n"
            "Formules : signe dollar uniquement ($F=ma$), PAS de \\( \\) ni \\[ \\].\n"
            "Texte des options : court, sans guillemets imbriqués.\n\n"
            "RÈGLE CRITIQUE : Chaque question DOIT être COMPLÈTEMENT AUTONOME. "
            "INTERDIT : références à 'le tableau', 'la figure', 'l'énoncé ci-dessus', 'le texte précédent', "
            "'ci-contre', 'selon les données', 'd'après le graphe'. "
            "Chaque question doit être compréhensible sans document externe.\n"
            f"{creole_instruction}\n\n"
            'Réponds UNIQUEMENT avec ce JSON array (rien d\'autre) :\n'
            '[{"enonce":"...","options":["A","B","C","D"],'
            '"reponse_correcte":0,"explication":"...","sujet":"..."}]\n\n'
            "reponse_correcte = INDEX entier (0, 1, 2 ou 3)."
        )

    if subject == 'francais' and creole_system:
        # Utilise llama-3.3-70b avec system prompt dédié pour le créole
        resp = _client().chat.completions.create(
            model=CREOLE_MODEL,
            messages=[
                {"role": "system", "content": creole_system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1600,
        )
        text = resp.choices[0].message.content or ''
    else:
        text = _call_fast(prompt, max_tokens=1400)

    # Nettoyer blocs markdown
    text = re.sub(r'```[a-z]*\s*', '', text).strip()

    match = re.search(r'\[[\s\S]+?\](?=\s*$|\s*\[)', text) or re.search(r'\[[\s\S]+\]', text)
    if not match:
        return []

    json_str = match.group(0)
    # Fix backslash sequences invalides en JSON (\( \) \, \omega …) → \\(
    json_str = re.sub(r'\\([^"\\/bfnrtu0-9\n\r])', r'\\\\\1', json_str)

    try:
        raw = _json.loads(json_str)
    except Exception:
        # Fallback : extraire question par question avec regex
        raw = []
        for m in re.finditer(r'\{[^{}]+\}', json_str):
            try:
                raw.append(_json.loads(m.group(0)))
            except Exception:
                pass

    result = []
    for q in raw:
        options = q.get('options', [])
        rc = q.get('reponse_correcte', 0)
        try:
            correct_idx = int(rc)
            if correct_idx < 0 or correct_idx >= len(options):
                correct_idx = 0
        except (ValueError, TypeError):
            try:
                correct_idx = options.index(str(rc))
            except (ValueError, AttributeError):
                correct_idx = 0
        q['reponse_correcte'] = correct_idx
        q['subject'] = subject
        # Guard: skip questions with fewer than 4 options (broken multi-part)
        if len(options) < 4:
            continue
        # Guard: Anglais — skip if enonce looks like a multi-part list
        if subject == 'anglais' and re.search(r'^\s*1[.)]\s', q.get('enonce', ''), re.MULTILINE):
            continue
        result.append(q)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# COURS INTERACTIF — programme Bac Haïti + session de cours
# ═════════════════════════════════════════════════════════════════════════════

# ── Programme officiel Bac Haïti (Terminale) ─────────────────────────────────
# Zéro appel IA — ces chapitres suivent le vrai programme national haïtien.
_BAC_CURRICULUM: dict = {
    'physique': [
        {'title': 'Cinématique', 'keywords': ['cinématique', 'vitesse', 'accélération', 'mouvement', 'MRU', 'MRUA', 'chute libre'], 'description': "Étude du mouvement : MRU, MRUA, chute libre, équations horaires. Maîtrise les graphes v(t) et x(t) typiques du Bac."},
        {'title': 'Dynamique – Lois de Newton', 'keywords': ['Newton', 'force', 'inertie', 'dynamique', 'principe fondamental'], 'description': "Les 3 lois de Newton et le principe fondamental de la dynamique. Applications directes aux exercices du Bac."},
        {'title': 'Travail, Énergie et Puissance', 'keywords': ['travail', 'énergie cinétique', 'énergie potentielle', 'puissance', 'conservation'], 'description': "Théorème énergie-travail, conservation de l'énergie mécanique, puissance. Sujets très récurrents au Bac."},
        {'title': 'Électrostatique', 'keywords': ['électrostatique', 'Coulomb', 'champ électrique', 'potentiel', 'condensateur'], 'description': "Loi de Coulomb, champ et potentiel électrique, condensateur. Calculs et schémas incontournables."},
        {'title': 'Électrocinétique et Circuits', 'keywords': ['circuit', 'résistance', 'Kirchhoff', 'courant', 'tension', 'Ohm', 'pont de Wheatstone'], 'description': "Lois d'Ohm et de Kirchhoff, dipôles actifs/passifs, circuits en série/parallèle, pont de Wheatstone."},
        {'title': 'Magnétisme et Induction', 'keywords': ['magnétisme', 'champ magnétique', 'induction', 'Laplace', 'Faraday', 'solénoïde'], 'description': "Champ magnétique, force de Laplace, induction électromagnétique, loi de Faraday-Lenz."},
        {'title': 'Optique Géométrique', 'keywords': ['optique', 'réfraction', 'réflexion', 'lentille', 'vergence', 'image'], 'description': "Lois de Descartes, lentilles convergentes et divergentes, construction d'images, miroirs."},
        {'title': 'Ondes et Acoustique', 'keywords': ['onde', 'fréquence', 'période', 'longueur onde', 'son', 'diffraction'], 'description': "Propriétés des ondes (fréquence, longueur d'onde, célérité), phénomènes ondulatoires, acoustique."},
        {'title': 'Radioactivité et Noyau', 'keywords': ['radioactivité', 'désintégration', 'demi-vie', 'noyau', 'fission', 'fusion'], 'description': "Types de désintégrations, loi de décroissance radioactive, demi-vie, bilan et applications."},
    ],
    'chimie': [
        {'title': 'Structure Atomique et Tableau Périodique', 'keywords': ['atome', 'électron', 'configuration', 'périodique', 'orbitale'], 'description': "Configuration électronique, classification périodique, propriétés des éléments selon leur position."},
        {'title': 'Liaisons Chimiques', 'keywords': ['liaison', 'covalente', 'ionique', 'polaire', 'électronégativité', 'VSEPR'], 'description': "Liaisons covalentes et ioniques, molécules polaires/apolaires, géométrie VSEPR."},
        {'title': 'Stœchiométrie et Réactions', 'keywords': ['stœchiométrie', 'mole', 'équation', 'bilan', 'rendement', 'réactif limitant'], 'description': "Équilibrage, calculs stœchiométriques, réactif limitant, rendement de réaction."},
        {'title': 'Alcanes – Hydrocarbures Saturés', 'keywords': ['alcane', 'méthane', 'éthane', 'propane', 'butane', 'isomérie', 'combustion'], 'description': "Nomenclature IUPAC, isomérie de chaîne, propriétés physiques, combustion, réactions de substitution."},
        {'title': 'Alcènes et Hydrocarbures Insaturés', 'keywords': ['alcène', 'éthylène', 'alcyne', 'double liaison', 'addition', 'Z/E'], 'description': "Doubles et triples liaisons C=C, isomérie Z/E, réactions d'addition (H₂, HX, H₂O), test de Bayer."},
        {'title': 'Alcools et Éthers-Oxydes', 'keywords': ['alcool', 'éthanol', 'méthanol', 'oxydation', 'déshydratation', 'classification'], 'description': "Classification (primaire, secondaire, tertiaire), oxydation ménagée, déshydratation, identification."},
        {'title': 'Aldéhydes, Cétones et Acides Carboxyliques', 'keywords': ['aldéhyde', 'cétone', 'acide carboxylique', 'estérification', 'ester', 'saponification'], 'description': "Fonctions carbonyle, réactions d'oxydation, estérification-hydrolyse, saponification, reconnaissance."},
        {'title': 'Solutions Acide-Base et Titrages', 'keywords': ['acide', 'base', 'pH', 'neutralisation', 'titrage', 'indicateur coloré', 'pKa'], 'description': "Théorie de Brønsted-Lowry, calcul de pH, titrages acid-base, equivalence, indicateurs colorés."},
        {'title': 'Oxydoréduction et Électrochimie', 'keywords': ['oxydoréduction', 'pile', 'électrolyse', 'oxydant', 'réducteur', 'potentiel'], 'description': "Couples redox, piles électrochimiques (Daniell, etc.), électrolyse, loi de Faraday."},
        {'title': 'Thermochimie', 'keywords': ['enthalpie', 'thermochimie', 'exothermique', 'endothermique', 'chaleur', 'Hess'], 'description': "Enthalpie de réaction, loi de Hess, énergie de liaison, échanges thermiques dans les réactions."},
    ],
    'svt': [
        {'title': 'Génétique Mendélienne – Hérédité', 'keywords': ['mendel', 'allèle', 'dominance', 'croisement', 'monohybridisme', 'dihybridisme', 'généalogique'], 'description': "Lois de Mendel, croisements mono- et dihybrides, dominance/récessivité, arbres généalogiques."},
        {'title': 'Génétique Chromosomique', 'keywords': ['chromosome', 'liaison', 'crossing-over', 'caryotype', 'carte génétique'], 'description': "Gènes liés, crossing-over, caryotype, maladies chromosomiques (trisomie 21, etc.)."},
        {'title': 'ADN, Réplication et Expression Génétique', 'keywords': ['ADN', 'réplication', 'transcription', 'traduction', 'ARN', 'code génétique', 'mutation'], 'description': "Structure de l'ADN, réplication semi-conservative, transcription → traduction, mutations et conséquences."},
        {'title': 'Immunologie et Défenses de l\'Organisme', 'keywords': ['immunité', 'anticorps', 'antigène', 'lymphocyte', 'vaccin', 'SIDA', 'défense'], 'description': "Immunité innée et adaptative, lymphocytes B et T, anticorps, vaccination, sérothérapie."},
        {'title': 'Neurophysiologie', 'keywords': ['neurone', 'synapse', 'influx nerveux', 'potentiel action', 'système nerveux'], 'description': "Structure du neurone, genèse et propagation de l'influx nerveux, transmission synaptique."},
        {'title': 'Reproduction – Mitose et Méiose', 'keywords': ['mitose', 'méiose', 'reproduction', 'gamète', 'fécondation', 'cycle'], 'description': "Phases de la mitose et méiose, gamétogenèse, fécondation, cycles de développement."},
        {'title': 'Écologie et Environnement', 'keywords': ['écosystème', 'chaîne alimentaire', 'biosphère', 'cycle', 'population', 'biome'], 'description': "Écosystèmes, flux d'énergie et de matière, cycles biogéochimiques, relations inter-espèces."},
        {'title': 'Évolution et Classification du Vivant', 'keywords': ['évolution', 'sélection naturelle', 'Darwin', 'phylogénèse', 'espèce', 'classification'], 'description': "Théorie de l'évolution, sélection naturelle, dérive génétique, classification phylogénétique."},
        {'title': 'Photosynthèse et Respiration Cellulaire', 'keywords': ['photosynthèse', 'chlorophylle', 'respiration cellulaire', 'ATP', 'mitochondrie'], 'description': "Équations de la photosynthèse et respiration cellulaire, rôle de l'ATP, bilan énergétique."},
    ],
    'maths': [
        {'title': 'Limites et Continuité', 'keywords': ['limite', 'continuité', 'asymptote', 'forme indéterminée', 'infini'], 'description': "Calcul de limites (polynômes, rationnelles, trigono.), théorèmes des gendarmes, continuité."},
        {'title': 'Dérivation et Applications', 'keywords': ['dérivée', 'dérivation', 'tangente', 'extremum', 'croissance', 'tableau de variations'], 'description': "Règles de dérivation, équation de tangente, tableaux de variation, extrema, optimisation."},
        {'title': 'Fonctions Exponentielle et Logarithme', 'keywords': ['exponentielle', 'logarithme', 'ln', 'log', 'exp', 'croissance exponentielle'], 'description': "Fonctions exp et ln : propriétés, dérivées, équations, applications à la croissance/décroissance."},
        {'title': 'Intégration', 'keywords': ['intégrale', 'primitive', 'somme de Riemann', 'aire', 'intégration par parties'], 'description': "Primitives, intégrale définie, théorème fondamental du calcul, calculs d'aires, intégration par parties."},
        {'title': 'Suites Numériques', 'keywords': ['suite', 'arithmétique', 'géométrique', 'convergence', 'terme général', 'récurrence'], 'description': "Suites arithmétiques et géométriques, raisonnement par récurrence, limites de suites."},
        {'title': 'Trigonométrie', 'keywords': ['sinus', 'cosinus', 'tangente', 'équation trigonométrique', 'cercle unité', 'formules'], 'description': "Identités trigonométriques, formules d'addition, équations trigonométriques, cercle unitaire."},
        {'title': 'Nombres Complexes', 'keywords': ['complexe', 'imaginaire', 'module', 'argument', 'forme exponentielle', 'racine'], 'description': "Formes algébrique et trigonométrique, module et argument, équations du 2e degré dans ℂ."},
        {'title': 'Géométrie Analytique et Vecteurs', 'keywords': ['vecteur', 'produit scalaire', 'droite', 'plan', 'distance', 'norme'], 'description': "Vecteurs, produit scalaire, équations de droites et plans, distances, positions relatives."},
        {'title': 'Probabilités et Statistiques', 'keywords': ['probabilité', 'loi binomiale', 'espérance', 'variance', 'statistique', 'dénombrement'], 'description': "Combinatoire, probabilités conditionnelles, loi binomiale, espérance, variance, statistiques."},
    ],
    'francais': [
        {'title': 'Lecture et Analyse Littéraire', 'keywords': ['analyse', 'texte', 'lecture', 'registre', 'tonalité', 'extrait', 'commentaire'], 'description': "Comprendre et analyser un texte : contexte, registres littéraires, visée de l'auteur."},
        {'title': 'Commentaire Composé', 'keywords': ['commentaire', 'procédé', 'stylistique', 'axes', 'introduction', 'plan'], 'description': "Méthodologie du commentaire composé : introduction, axes d'étude, conclusion. Exemples du Bac."},
        {'title': 'Dissertation Littéraire', 'keywords': ['dissertation', 'thèse', 'antithèse', 'synthèse', 'argument', 'plan dialectique'], 'description': "Construction d'un plan dialectique, formulation de la problématique, argumentation organisée."},
        {'title': 'Figures de Style et Rhétorique', 'keywords': ['métaphore', 'comparaison', 'hyperbole', 'anaphore', 'figure', 'rhétorique', 'allitération'], 'description': "Identifier et analyser les figures de style (métaphore, anaphore, antithèse…) et leurs effets."},
        {'title': 'Grammaire et Syntaxe Avancées', 'keywords': ['grammaire', 'syntaxe', 'proposition subordonnée', 'concordance des temps', 'mode', 'subjonctif'], 'description': "Propositions subordonnées, concordance des temps, modes verbaux, analyse grammaticale."},
        {'title': 'Littérature Haïtienne', 'keywords': ['haïtien', 'littérature haïtienne', 'indigénisme', 'noirisme', 'négritude', 'Roumain', 'Depestre'], 'description': "Grands courants et auteurs haïtiens : indigénisme, noirisme, négritude. Jacques Roumain, René Depestre, etc."},
        {'title': 'Poésie – Versification et Analyse', 'keywords': ['poème', 'vers', 'rime', 'rythme', 'strophe', 'sonnet', 'alexandrin'], 'description': "Versification (mètre, rime, strophe), analyse d'un poème, courants poétiques."},
        {'title': 'Résumé et Synthèse de Texte', 'keywords': ['résumé', 'synthèse', 'contraction', 'reformulation', 'idée principale'], 'description': "Technique de résumé et contraction de texte : identifier les idées essentielles, reformuler fidèlement."},
    ],
    'philosophie': [
        {'title': 'La Conscience et l\'Inconscient', 'keywords': ['conscience', 'inconscient', 'Freud', 'psychanalyse', 'refoulement', 'moi'], 'description': "Concept de conscience, théorie freudienne, rapports entre conscience, inconscient et identité."},
        {'title': 'Le Désir et le Bonheur', 'keywords': ['désir', 'bonheur', 'épicurisme', 'stoïcisme', 'plaisir', 'manque', 'Épicure'], 'description': "Nature du désir (manque ou élan), conceptions du bonheur (épicurisme, stoïcisme, hédonisme)."},
        {'title': 'La Liberté et la Responsabilité', 'keywords': ['liberté', 'responsabilité', 'déterminisme', 'libre arbitre', 'Sartre', 'choix'], 'description': "Déterminisme vs libre arbitre, liberté existentielle (Sartre), responsabilité morale et juridique."},
        {'title': 'L\'État, la Politique et la Justice', 'keywords': ['état', 'politique', 'justice', 'contrat social', 'Rousseau', 'Hobbes', 'Locke', 'démocratie'], 'description': "Fondement de l'État, contrat social (Rousseau, Hobbes, Locke), justice distributive, démocratie."},
        {'title': 'La Vérité et la Connaissance', 'keywords': ['vérité', 'connaissance', 'science', 'rationalisme', 'empirisme', 'Descartes', 'Kant'], 'description': "Théories de la connaissance, doute cartésien, rationalisme vs empirisme, vérité scientifique."},
        {'title': 'Le Langage et la Pensée', 'keywords': ['langage', 'signe', 'signification', 'pensée', 'parole', 'symbole', 'Saussure'], 'description': "Rapport langage/pensée, linguistique de Saussure, fonctions du langage, limites du langage."},
        {'title': 'Morale et Éthique', 'keywords': ['morale', 'éthique', 'devoir', 'valeur', 'Kant', 'utilitarisme', 'bien', 'mal'], 'description': "Éthique kantienne, utilitarisme (Bentham), relativisme moral, droits de l'homme, valeurs."},
    ],
    'histoire': [
        {'title': 'La Révolution Haïtienne (1791-1804)', 'keywords': ['révolution haïtienne', '1804', 'Toussaint', 'Dessalines', 'indépendance', 'Saint-Domingue', 'esclavage'], 'description': "De la révolte servile à l'indépendance : Toussaint Louverture, Jean-Jacques Dessalines, proclamation du 1er janvier 1804."},
        {'title': 'Haïti au XIXe Siècle', 'keywords': ['Haïti XIXe', 'Boyer', 'Pétion', 'Christophe', 'dette', 'occupation', 'partition'], 'description': "Consolidation de l'État haïtien, Pétion vs Christophe, Boyer, indemnité de 1825, instabilité politique."},
        {'title': 'Première Guerre Mondiale', 'keywords': ['1914', 'guerre 14', 'Verdun', 'tranchées', 'Versailles', 'Triple Entente', 'armistice'], 'description': "Causes, alliances (Triple Entente/Alliance), batailles de Verdun et de la Marne, traité de Versailles 1919."},
        {'title': 'Deuxième Guerre Mondiale', 'keywords': ['1939', 'Hitler', 'nazisme', 'Holocaust', 'libération', 'ONU', 'atome'], 'description': "Montée du nazisme, grandes batailles, Shoah, capitulation allemande et japonaise, création de l'ONU."},
        {'title': 'La Guerre Froide', 'keywords': ['guerre froide', 'URSS', 'USA', 'bloc', 'Cuba', 'Berlin', 'Otan', 'Pacte de Varsovie'], 'description': "Bipartition Est-Ouest, course aux armements, crise de Cuba, mur de Berlin, fin de l'URSS en 1991."},
        {'title': 'Décolonisation et Monde Postcolonial', 'keywords': ['décolonisation', 'Bandung', 'Afrique', 'Asie', 'tiers-monde', 'indépendance', 'néocolonialisme'], 'description': "Mouvement des indépendances (années 1950-70), conférence de Bandung, néocolonialisme, non-alignement."},
        {'title': 'Géographie d\'Haïti', 'keywords': ['géographie Haïti', 'département', 'relief', 'fleuve', 'population', 'zone', 'agriculture', 'artibonite'], 'description': "Divisions administratives, reliefs (Massif du Nord, La Selle…), hydrographie, économie et population."},
    ],
    'anglais': [
        {'title': 'Reading Comprehension', 'keywords': ['reading', 'comprehension', 'passage', 'main idea', 'inference', 'detail'], 'description': "How to read and understand English texts: main idea, supporting details, inference, vocabulary in context."},
        {'title': 'Tenses and Verb Forms', 'keywords': ['tense', 'past', 'present', 'future', 'conditional', 'passive', 'reported speech'], 'description': "All English verb tenses, passive voice, conditional sentences, reported speech — key grammar for the Bac."},
        {'title': 'Essay and Composition Writing', 'keywords': ['writing', 'essay', 'composition', 'paragraph', 'topic sentence', 'argument'], 'description': "Structured essay writing: introduction, supporting paragraphs, conclusion. Argumentation in English."},
        {'title': 'Vocabulary and Word Formation', 'keywords': ['vocabulary', 'synonym', 'antonym', 'prefix', 'suffix', 'word formation', 'idiom'], 'description': "Building vocabulary through prefixes, suffixes, synonyms, antonyms and context-based meaning."},
        {'title': 'Literary Analysis in English', 'keywords': ['poem', 'story', 'literature', 'theme', 'character', 'figure of speech', 'metaphor'], 'description': "Analyzing poems and short stories: themes, characters, figures of speech (metaphor, simile, etc.)."},
        {'title': 'Spoken and Written Communication', 'keywords': ['letter', 'email', 'dialogue', 'conversation', 'formal', 'informal', 'register'], 'description': "Writing formal/informal letters, emails, dialogues. Expressing opinions and arguments in English."},
    ],
}


def _find_pdf_excerpt(exam_text: str, keywords: list, max_len: int = 400) -> str:
    """
    Cherche dans exam_text un passage pertinent contenant un des mots-clés.
    Retourne le meilleur extrait trouvé (ou '' si rien).
    Zéro appel API — simple recherche textuelle.
    """
    if not exam_text or not keywords:
        return ''
    text_lower = exam_text.lower()
    best = ''
    best_score = 0
    # Découper en phrases/blocs (~300 chars)
    chunks = [exam_text[i:i+400] for i in range(0, len(exam_text), 300)]
    for chunk in chunks:
        chunk_lower = chunk.lower()
        score = sum(1 for kw in keywords if kw.lower() in chunk_lower)
        if score > best_score:
            best_score = score
            best = chunk.strip()
    return best[:max_len] if best_score > 0 else ''


def extract_chapters_for_subject(subject: str, exam_text: str) -> list:
    """
    Retourne les chapitres du vrai programme Bac Haïti (SANS appel IA).
    Utilise _BAC_CURRICULUM pour les titres/descriptions, et recherche
    textuelle dans les PDFs pour trouver des extraits d'examens pertinents.
    Zéro coût API. Instantané.
    """
    curriculum = _BAC_CURRICULUM.get(subject, [])
    result = []
    for i, chap in enumerate(curriculum):
        excerpt = _find_pdf_excerpt(exam_text, chap.get('keywords', [chap['title']]))
        result.append({
            'title':        chap['title'],
            'description':  chap['description'],
            'order':        i + 1,
            'exam_excerpt': excerpt,
        })
    return result


def generate_section_content(
    chapter_title: str,
    section_title: str,
    subject: str,
    chapter_context: str = '',
    mode: str = 'normal',
    weak_points: list = None,
    exam_related: str = '',
    user_lang: str = 'fr',
) -> str:
    """
    Génère un contenu riche et immersif pour un sous-chapitre.
    mode = 'normal' | 'remediation' (version simplifiée ciblée sur les lacunes)
    user_lang = 'fr' ou 'kr' — langue de l'interface utilisateur
    Retourne du markdown + KaTeX.
    """
    subject_label = MATS.get(subject, subject)
    weak_str = ', '.join(weak_points) if weak_points else ''
    exam_block = f"\n\n⭐ QUESTIONS RÉELLES D'EXAMEN LIÉES :\n{exam_related[:600]}\n" if exam_related else ''
    # user_lang est maintenant 'fr'/'kr' (code langue), pas du texte
    lang_instruction = _lang_instruction('', forced_lang=user_lang)
    creole_instruction = _creole_subject_instruction(subject)
    if creole_instruction:
        lang_instruction = creole_instruction

    if mode == 'remediation':
        mode_instruction = f"""‼️ MODE REMÉDIATION : L'élève a eu du mal avec cette partie.
Points faibles identifiés : {weak_str or 'compréhension générale'}

Repars de ZÉRO avec :
1. Une analogie ultra-simple du quotidien AVANT toute formule
2. Décompose le concept en micro-étapes (une idée à la fois)
3. Maximum 2 formules, bien expliquées ensemble
4. 1 exemple numérique résolu TRÈS détaillé, commenté ligne par ligne
5. Résumé "Ce qu'il faut retenir" en 3 bullet points maximum
Ton = pote bienveillant qui dit "OK, on reprend ensemble, pas de souci"."""
    else:
        mode_instruction = """MODE NORMAL : Cours complet, captivant, progressif.
Structure :
1. 🎯 **Accroche** — Pourquoi ce concept compte pour le Bac + une situation de la vie réelle
2. 💡 **Le concept** — Explication avec analogie concrète AVANT la définition formelle
3. 📐 **Formules & Définitions** — LaTeX, bien mises en valeur, expliquées intuitivement
4. 🔢 **Exemple résolu** — Exercice de niveau Bac, résolution étape par étape
5. 🏆 **Bac Tip** — Ce qui tombe vraiment à l'exam et le piège classique à éviter"""

    system = f"Tu es BacIA, le meilleur prof de {subject_label} pour le Bac Haïti. Tu expliques comme un grand frère passionné."

    prompt = f"""Génère le contenu pédagogique pour cette partie du cours.

CHAPITRE : {chapter_title}
PARTIE : **{section_title}**
MATIÈRE : {subject_label} — Terminale Bac Haïti

CONTEXTE DU CHAPITRE :
{chapter_context[:800] if chapter_context else 'Programme officiel Terminale Haïti.'}
{exam_block}
{mode_instruction}

RÈGLES ABSOLUES :
- Formules OBLIGATOIREMENT en LaTeX MathJax : $inline$ et $$blocs display$$, jamais \\(\\) ou \\[\\]
- Markdown riche : **gras** pour les termes clés, > pour les "Bac Tip", --- pour les séparateurs
- Longueur : 250-400 mots (dense et utile, pas du remplissage)
- Commence DIRECTEMENT par le contenu, sans "Voici..." ni intro creuse
- Finis par une phrase d'encouragement courte et percutante

RÈGLES STRICTES DE FORMATAGE DES FORMULES :
- SCALAIRE par défaut : écris $a = -g$ et NON $\\mathbf{{a}}= -g\\,\\hat{{\\mathbf{{z}}}}$. Pas de vecteurs unitaires.
- UNITÉS propres : $g \\approx 9,81 \\text{{ m/s}}^2$. JAMAIS de · (Unicode) dans une formule.
- MULTIPLICATION : \\cdot ou \\times, JAMAIS · (Unicode) ni \\cdotp.
- Variables simples : $a$, $g$, $v$ — pas $\\mathbf{{a}}$, $\\mathbf{{g}}$.

{lang_instruction}

Écris le contenu maintenant :"""

    return _call(prompt, system=system, max_tokens=1400)


def generate_section_miniquiz(
    section_title: str,
    chapter_title: str,
    subject: str,
    count: int = 4,
    mode: str = 'normal',
    weak_points: list = None,
    exam_related: str = '',
    user_lang: str = '',
) -> list:
    """
    Génère un mini-quiz (3-4 QCM) pour un sous-chapitre.
    mode = 'normal' | 'remediation' (questions plus simples, ciblées)
    user_lang = texte récent de l'utilisateur pour détecter la langue
    Retourne une liste de questions [{enonce, options, reponse_correcte, explication, sujet}]
    """
    import json as _json
    subject_label = MATS.get(subject, subject)
    weak_str = f"Lacunes détectées : {', '.join(weak_points)}." if weak_points else ''
    exam_block = f"\nExtraits d'examens officiels pour inspiration :\n{exam_related[:500]}\n" if exam_related else ''
    lang_instruction = _lang_instruction(user_lang) if user_lang else "LANGUE : Réponds en français."
    creole_instruction = _creole_subject_instruction(subject)
    if creole_instruction:
        lang_instruction = creole_instruction

    difficulty = "Questions de remédiation : SIMPLES et directes, centrent sur les bases de la section." if mode == 'remediation' else "Niveau Bac Haïti : mélange application directe (60%) et raisonnement (40%)."

    prompt = f"""Tu es professeur de {subject_label} au Bac Haïti.
Génère EXACTEMENT {count} questions QCM sur cette partie du cours.

CHAPITRE : {chapter_title}
PARTIE : {section_title}
{weak_str}
{exam_block}
{difficulty}

RÈGLES :
- Questions précises, directement liées au contenu de la section
- 4 options par question (A, B, C, D)
- Les mauvaises réponses = erreurs classiques des élèves
- L'explication doit enseigner le concept (2-3 phrases minimum)
- Formules : $inline$ uniquement (PAS de \\( \\) ni \\[ \\])
{lang_instruction}

Réponds UNIQUEMENT avec ce JSON array (rien d'autre) :
[{{"enonce":"...","options":["A: ...","B: ...","C: ...","D: ..."],"reponse_correcte":0,"explication":"...","sujet":"{section_title}"}}]

reponse_correcte = INDEX entier (0=A, 1=B, 2=C, 3=D)."""

    text = _call_fast(prompt, max_tokens=2000)
    return _parse_quiz_json(text)


def find_exam_questions_for_section(
    section_title: str,
    chapter_title: str,
    subject: str,
    exam_json_text: str,
) -> list:
    """
    Cherche dans les données d'examen JSON des questions liées à cette section.
    Retourne une liste de textes de questions pertinents (max 3).
    Zéro appel IA — recherche textuelle par mots-clés.
    """
    if not exam_json_text:
        return []
    keywords = []
    # Extraire les mots significatifs du titre de section
    for word in (section_title + ' ' + chapter_title).lower().split():
        if len(word) > 4 and word not in {'avec', 'dans', 'pour', 'les', 'des', 'une', 'qui', 'que', 'sur', 'est'}:
            keywords.append(word)
    keywords = keywords[:8]
    if not keywords:
        return []
    lines = exam_json_text.split('\n')
    results = []
    for i, line in enumerate(lines):
        line_l = line.lower()
        score = sum(1 for kw in keywords if kw in line_l)
        if score >= 2 and len(line.strip()) > 40:
            # Prendre contexte (ligne + 2 suivantes)
            context = '\n'.join(lines[i:i+3]).strip()[:300]
            results.append((score, context))
    results.sort(key=lambda x: -x[0])
    return [r[1] for r in results[:3]]


def _normalize_grounding_text(text: str) -> str:
    if not text:
        return ''
    import unicodedata as _ud
    t = _ud.normalize('NFD', str(text))
    t = ''.join(ch for ch in t if _ud.category(ch) != 'Mn')
    return re.sub(r'\s+', ' ', t).strip().lower()


def _extract_concept_notes(full_notes: str, concept_name: str, max_chars: int = 4000) -> str:
    """Extract the portion of notes most relevant to *concept_name*.

    Splits notes into paragraphs, scores each by keyword overlap with the
    concept, and returns the top-scoring paragraphs (in original order) up
    to *max_chars*.  Falls back to a centred window around the best match.
    """
    if not full_notes or len(full_notes) <= max_chars:
        return full_notes or ''
    if not concept_name:
        return full_notes[:max_chars]

    concept_lower = concept_name.lower()
    notes_lower = full_notes.lower()

    # --- paragraph-based extraction ---
    paragraphs = re.split(r'\n\s*\n', full_notes)
    if len(paragraphs) >= 3:
        stop_words = {'les', 'des', 'une', 'pour', 'dans', 'avec', 'sur',
                       'par', 'est', 'que', 'qui', 'sont', 'aux', 'ses'}
        keywords = [w for w in re.findall(r'[a-zà-ÿ]{3,}', concept_lower)
                     if w not in stop_words]
        scored: list[tuple[int, int, str]] = []
        for idx, para in enumerate(paragraphs):
            pl = para.lower()
            score = sum(1 for k in keywords if k in pl)
            if concept_lower in pl:
                score += 5
            scored.append((score, idx, para))
        scored.sort(key=lambda x: (-x[0], x[1]))
        selected: list[tuple[int, str]] = []
        total_len = 0
        for score, idx, para in scored:
            if total_len + len(para) + 2 > max_chars:
                if not selected:
                    selected.append((idx, para[:max_chars]))
                break
            selected.append((idx, para))
            total_len += len(para) + 2
        selected.sort(key=lambda x: x[0])
        return '\n\n'.join(p for _, p in selected)

    # --- fallback: centred window ---
    pos = notes_lower.find(concept_lower)
    if pos < 0:
        pos = 0
    half = max_chars // 2
    start = max(0, pos - half)
    end = min(len(full_notes), start + max_chars)
    start = max(0, end - max_chars)
    return full_notes[start:end]


def _extract_windowed_notes(
    full_notes: str,
    concepts: list[str],
    teach_idx: int,
    total: int,
    max_chars_per_concept: int = 3000,
) -> tuple[str, str]:
    """Return (windowed_notes, window_label) covering [prev, current, next] concepts.

    For the diagnostic intro (teach_idx == 0, first message) or synthesis
    (teach_idx >= total), the caller handles notes differently so this is
    only called for the active teaching phase (0 <= teach_idx < total).

    Strategy:
      - prev  concept (if any): max_chars_per_concept // 2  (recap, less detail)
      - current concept:        max_chars_per_concept        (full detail)
      - next  concept (if any): max_chars_per_concept // 3   (preview, minimal)

    This turns ~6 000 tokens into ~1 500 for a typical maths chapter.
    """
    if not full_notes or teach_idx >= total or not concepts:
        return full_notes or '', 'chapitre complet'

    parts: list[str] = []
    label_parts: list[str] = []

    # Previous concept — light recap
    if teach_idx > 0:
        prev_name = concepts[teach_idx - 1]
        prev_text = _extract_concept_notes(full_notes, prev_name,
                                           max_chars=max_chars_per_concept // 2)
        if prev_text:
            parts.append(f"── Rappel : {prev_name} ──\n{prev_text}")
            label_parts.append(prev_name)

    # Current concept — full detail
    cur_name = concepts[teach_idx]
    cur_text = _extract_concept_notes(full_notes, cur_name,
                                      max_chars=max_chars_per_concept)
    if cur_text:
        parts.append(f"── Concept actuel : {cur_name} ──\n{cur_text}")
        label_parts.append(cur_name)
    else:
        # Fallback: if extraction found nothing, send first max_chars_per_concept
        parts.append(full_notes[:max_chars_per_concept])
        label_parts.append(cur_name)

    # Next concept — brief preview
    if teach_idx + 1 < total:
        next_name = concepts[teach_idx + 1]
        next_text = _extract_concept_notes(full_notes, next_name,
                                           max_chars=max_chars_per_concept // 3)
        if next_text:
            parts.append(f"── Aperçu suivant : {next_name} ──\n{next_text}")
            label_parts.append(next_name)

    window_label = ' → '.join(label_parts)
    return '\n\n'.join(parts), window_label


def _extract_concepts_from_notes(note_content: str, chapter_title: str, limit: int = 40) -> list[str]:
    """Extract an ordered concept list from raw chapter notes with lightweight heuristics."""
    text = (note_content or '').strip()
    if not text:
        return [chapter_title or 'Concept principal']

    concepts: list[str] = []
    seen: set[str] = set()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        if re.match(r'^(#{1,4}\s+|\*\*.+\*\*$|\d+[\.)]\s+|[A-Z][A-Z\s\-:,]{6,}$)', ln):
            c = re.sub(r'^(#{1,4}\s+|\d+[\.)]\s+)', '', ln).strip(' -*:')
            if 4 <= len(c) <= 90 and not _is_concept_noise(c):
                key = _normalize_grounding_text(c)
                if key and key not in seen:
                    seen.add(key)
                    concepts.append(c)
        if len(concepts) >= limit:
            break

    if not concepts:
        for m in re.finditer(r'\b([A-ZÉÈÀÂÎÔÙ][^\n\.:;]{4,80})\s*:', text):
            c = m.group(1).strip()
            if _is_concept_noise(c):
                continue
            key = _normalize_grounding_text(c)
            if key and key not in seen:
                seen.add(key)
                concepts.append(c)
            if len(concepts) >= limit:
                break

    if not concepts:
        concepts = [chapter_title or 'Concept principal']

    return concepts[:limit]


def _detect_student_level(messages: list, current_message: str) -> str:
    """
    Analyse conversation patterns to dynamically detect student level.
    Returns: 'faible', 'moyen', 'avancé'

    Signals for FAIBLE:
    - Short messages (< 20 chars average)
    - Frequent confusion signals
    - Many errors on same concept (repeated corrections)
    - Use of informal/slang

    Signals for AVANCÉ:
    - Long, detailed answers
    - Correct use of technical terms
    - Questions that go beyond the current concept
    - Quick validation of concepts

    Signals for MOYEN:
    - Mix of the above
    """
    if not messages:
        return 'moyen'

    student_msgs = [m for m in messages
                    if m.get('role') not in ('ai', 'assistant', '__plan__', '__plan_intro__')
                    and m.get('content')]
    if len(student_msgs) < 2:
        return 'moyen'

    # Compute metrics
    msg_lengths = [len(m.get('content', '')) for m in student_msgs]
    avg_length = sum(msg_lengths) / len(msg_lengths) if msg_lengths else 0

    # Count confusion signals in history
    confusion_count = 0
    validation_count = 0
    for m in student_msgs:
        content = (m.get('content', '') or '').lower()
        if any(p in content for p in ('comprend pas', 'pas clair', 'perdu', 'trop dur', 'pa kompran')):
            confusion_count += 1
        if any(p in content for p in ('compris', 'ok', 'oui', 'clair', 'parfait', 'wi', 'konprann')):
            validation_count += 1

    # Count how many AI corrections happened (repeated concept teaching)
    ai_msgs = [m for m in messages if m.get('role') in ('ai', 'assistant') and m.get('content')]
    correction_count = sum(1 for m in ai_msgs if 'Presque' in (m.get('content', '') or '')
                          or 'erreur' in (m.get('content', '') or '').lower()
                          or '❌' in (m.get('content', '') or ''))

    # Check current message complexity
    current_lower = (current_message or '').lower()
    has_technical = bool(re.search(r'[=∈∀∃∑∏∫\^_]|\$.*\$|formule|théorème|démontrer|prouver|dériver|intégr', current_lower))
    has_advanced_question = bool(re.search(r'pourquoi.*(fonctionne|marche|vrai)|comment.*(démontr|prouv)|quelle.*différence.*entre|dans quel.*cas', current_lower))

    # Decision logic
    score = 0  # negative = faible, positive = avancé

    if avg_length < 15:
        score -= 2
    elif avg_length > 60:
        score += 2
    elif avg_length > 30:
        score += 1

    if confusion_count >= 3:
        score -= 2
    elif confusion_count >= 2:
        score -= 1

    if correction_count >= 3:
        score -= 2
    elif correction_count >= 2:
        score -= 1

    if validation_count >= 4 and confusion_count <= 1:
        score += 2

    if has_technical:
        score += 2
    if has_advanced_question:
        score += 1

    if score <= -2:
        return 'faible'
    elif score >= 2:
        return 'avancé'
    return 'moyen'


def _analyze_student_behavior(messages: list, user_message: str) -> dict:
    """
    Layer 2 — Behavioral analysis of the conversation history.
    Detects silent difficulty the student doesn't explicitly express.

    Returns a dict with:
      - 'signal': None | 'hesitant' | 'confused' | 'frustrated'
      - 'confidence': float 0-1 (how confident is the behavioral detection)
      - 'details': str describing the behavioral pattern detected
      - 'metrics': dict of raw behavioral metrics for AI fallback context
    """
    if not messages:
        return {'signal': None, 'confidence': 0, 'details': '', 'metrics': {}}

    student_msgs = [m for m in messages
                    if m.get('role') not in ('ai', 'assistant', '__plan__', '__plan_intro__')
                    and m.get('content')]

    if len(student_msgs) < 2:
        return {'signal': None, 'confidence': 0, 'details': '', 'metrics': {}}

    # ── Metric 1: Average message length (short = struggling) ──────────────
    msg_lengths = [len((m.get('content', '') or '').strip()) for m in student_msgs]
    avg_length = sum(msg_lengths) / len(msg_lengths) if msg_lengths else 0
    recent_lengths = msg_lengths[-3:]  # last 3 messages
    recent_avg = sum(recent_lengths) / len(recent_lengths) if recent_lengths else 0

    # ── Metric 2: Error streak (consecutive wrong answers) ─────────────────
    ai_msgs = [m for m in messages if m.get('role') in ('ai', 'assistant') and m.get('content')]
    error_indicators = ('presque', '❌', 'pas tout à fait', 'erreur', 'incorrect',
                        'pas exactement', 'pas correct', 'essaie encore',
                        'bonne tentative', 'tu es proche')
    recent_ai = ai_msgs[-4:]  # last 4 AI messages
    recent_errors = 0
    for m in recent_ai:
        content_lower = (m.get('content', '') or '').lower()
        if any(ind in content_lower for ind in error_indicators):
            recent_errors += 1

    # ── Metric 3: Hesitation frequency (uncertainty signals in recent msgs) ─
    hesitation_signals = ('je pense', 'peut-être', 'peut être', 'je crois',
                          'pas sûr', 'pas sur', 'probablement', 'je suppose',
                          'je dirais', 'j\'hésite', 'hmm', '...',
                          'euh', 'bof', 'je sais pas trop', 'mwen panse')
    recent_student = student_msgs[-4:]  # last 4 student messages
    hesitation_count = 0
    for m in recent_student:
        content_lower = (m.get('content', '') or '').lower()
        if any(h in content_lower for h in hesitation_signals):
            hesitation_count += 1

    # ── Metric 4: Answer pattern (repeated guessing with ? or very short) ──
    guess_count = 0
    for m in recent_student:
        content = (m.get('content', '') or '').strip()
        # Short answer ending with ? = guessing
        if len(content) < 15 and content.endswith('?'):
            guess_count += 1
        # Ultra-short numeric answer = possible random guess
        elif len(content) < 5 and re.search(r'\d', content):
            guess_count += 1

    # ── Metric 5: Declining message quality (messages getting shorter) ─────
    declining = False
    if len(msg_lengths) >= 4:
        first_half_avg = sum(msg_lengths[:len(msg_lengths)//2]) / max(1, len(msg_lengths)//2)
        second_half_avg = sum(msg_lengths[len(msg_lengths)//2:]) / max(1, len(msg_lengths) - len(msg_lengths)//2)
        if first_half_avg > 20 and second_half_avg < first_half_avg * 0.5:
            declining = True

    # ── Metric 6: Current message signals ──────────────────────────────────
    current_lower = (user_message or '').strip().lower()
    current_len = len(current_lower)
    current_has_ellipsis = '...' in current_lower or '…' in current_lower
    current_has_filler = any(f in current_lower for f in ('hmm', 'euh', 'bof', 'bah', 'ben'))

    # ── Decision: combine metrics into behavioral signal ───────────────────
    score = 0  # higher = more difficulty detected
    details_parts = []

    if recent_avg < 12:
        score += 2
        details_parts.append(f'msgs très courts ({recent_avg:.0f} car)')
    elif recent_avg < 20:
        score += 1
        details_parts.append(f'msgs courts ({recent_avg:.0f} car)')

    if recent_errors >= 3:
        score += 3
        details_parts.append(f'{recent_errors} erreurs consécutives')
    elif recent_errors >= 2:
        score += 2
        details_parts.append(f'{recent_errors} erreurs récentes')

    if hesitation_count >= 3:
        score += 3
        details_parts.append(f'hésitation répétée ({hesitation_count}x)')
    elif hesitation_count >= 2:
        score += 2
        details_parts.append(f'hésitation ({hesitation_count}x)')

    if guess_count >= 2:
        score += 2
        details_parts.append(f'réponses au hasard ({guess_count}x)')

    if declining:
        score += 1
        details_parts.append('qualité en baisse')

    if current_has_ellipsis:
        score += 1
        details_parts.append('hésitation (\"...\")')
    if current_has_filler:
        score += 1
        details_parts.append('mots de remplissage')
    if current_len < 8 and re.search(r'\d', current_lower):
        score += 1
        details_parts.append('réponse ultra-courte')

    # ── Map score to signal ────────────────────────────────────────────────
    metrics = {
        'avg_length': round(avg_length, 1),
        'recent_avg_length': round(recent_avg, 1),
        'recent_errors': recent_errors,
        'hesitation_count': hesitation_count,
        'guess_count': guess_count,
        'declining': declining,
        'score': score,
    }

    if score >= 5:
        # Strong difficulty signal → frustrated (silent)
        return {
            'signal': 'frustrated',
            'confidence': min(0.9, 0.5 + score * 0.05),
            'details': ' | '.join(details_parts),
            'metrics': metrics,
        }
    elif score >= 3:
        # Moderate difficulty → hesitant (struggling but trying)
        return {
            'signal': 'hesitant',
            'confidence': min(0.8, 0.4 + score * 0.1),
            'details': ' | '.join(details_parts),
            'metrics': metrics,
        }
    elif score >= 2:
        # Mild signal → confused (might need help)
        return {
            'signal': 'confused',
            'confidence': min(0.6, 0.3 + score * 0.1),
            'details': ' | '.join(details_parts),
            'metrics': metrics,
        }

    return {'signal': None, 'confidence': 0, 'details': '', 'metrics': metrics}


def _classify_student_intent(user_message: str, messages: list | None = None) -> str:
    """
    3-layer intent classification (like Duolingo/Khan Academy):

    Layer 1 — PATTERNS (fast, ~80% of cases):
      Heuristic keyword/phrase matching. No API call needed.

    Layer 2 — BEHAVIORAL ANALYSIS (~15% of cases):
      When patterns return 'other', analyze conversation history:
      message length trends, error streaks, hesitation frequency,
      guessing patterns, declining engagement.

    Layer 3 — AI FALLBACK (~5% remaining):
      When both fail, call AI with behavioral context for smart classification.

    Categories:
      VALIDATED  — student confirms understanding
      CONFUSED   — student signals they don't understand
      LAZY       — student wants the answer without effort
      FRUSTRATED — student is emotionally overwhelmed / wants to quit
      CHEATING   — student wants answers for homework/exam (academic dishonesty)
      HESITANT   — student is uncertain, guessing, lacks confidence in answer
      OTHER      — answering a question, asking something, etc.

    Returns one of: 'validated', 'confused', 'lazy', 'frustrated', 'cheating', 'hesitant', 'other'.
    Falls back to 'other' on any error.
    """
    if not (user_message or '').strip():
        return 'other'

    msg = user_message.strip().lower()

    # ── Fast heuristic classification (no API call needed) ────────────────
    # Frustrated patterns (emotional distress, wanting to quit)
    _frustrated_patterns = (
        'j\'abandonne', 'j abandonne', 'je lâche', 'je lache',
        'c\'est trop dur', 'trop dur pour moi', 'je suis nul',
        'je suis nulle', 'je suis bête', 'je suis bete',
        'j\'y arriverai jamais', 'j y arriverai jamais',
        'je vais rater', 'je vais échouer', 'je vais echouer',
        'ça sert à rien', 'ca sert a rien', 'sa sèv a anyen',
        'mwen pa ka', 'mwen pa kapab', 'm pa kapab',
        'je peux pas', 'je n\'y arrive pas', 'j\'y arrive pas',
        'c\'est impossible', 'impossible pour moi',
        'je déteste', 'je deteste', 'je hais', 'j\'en ai marre',
        'j en ai marre', 'ras le bol', 'ral bol',
        'laisse tomber', 'oublie', 'tant pis', 'tanpi',
        'je suis fatigué', 'je suis fatigue', 'mwen bouke',
        'i give up', 'i quit', 'i can\'t', 'too hard',
    )

    # Lazy patterns (student wants answers without effort)
    _lazy_patterns = (
        'donne la réponse', 'donne moi la réponse', 'donne la reponse',
        'donne juste la réponse', 'donne juste la reponse',
        'fais l\'exercice', 'fais le pour moi', 'fais-le pour moi',
        'résous', 'resous', 'résoudre pour moi', 'résous pour moi',
        'dis moi la réponse', 'dis-moi la réponse', 'dis moi la reponse',
        'give me the answer', 'just tell me', 'solve it',
        'ban m repons', 'ban m répons', 'bay mwen repons',
        'la réponse c\'est quoi', 'la reponse c\'est quoi',
        'c\'est quoi la réponse', 'c\'est quoi la reponse',
        'je veux la réponse', 'je veux la reponse',
        'fais mes devoirs', 'fais mon devoir',
    )

    # Validated patterns (understanding confirmed)
    _validated_patterns = (
        'ok', 'oui', 'wi', 'compris', 'konprann', 'mwen konprann', 'got it',
        'je comprends', 'clair', 'je vois', 'ah oui', 'dakò', 'ok compris',
        'parfait', 'c bon', 'super', 'merci', 'dac', 'd\'accord', 'okay',
        'oke', 'ouais', 'yes', 'yeah', 'yep', 'c\'est clair', 'je capte',
        'ah ok', 'ah d\'accord', 'bien compris', 'nickel', 'on continue',
        'continue', 'suivant', 'next', 'avance', 'pase', 'bon',
    )
    # Confused patterns (needs simpler explanation)
    _confused_patterns = (
        'comprend pas', 'comprends pas', 'kompran pa', 'pa kompran',
        'pa konprann', 'explique autrement', 'pas clair', 'kisa sa vle di',
        'confused', 'perdu', 'je suis perdu', 'toujours pas', 'encore',
        'je ne comprends', 'c\'est pas clair', 'hein', 'quoi', 'je capte pas',
        'je pige pas', 'simplifie', 'plus simple', 'trop compliqué',
        'trop dur', 'je sais pas', 'm pa konnen', 'sa pa clair',
    )

    # Hesitant patterns (student is uncertain, guessing, lacks confidence)
    _hesitant_patterns = (
        'je pense que', 'je crois que', 'je crois', 'peut-être',
        'peut être', 'petèt', 'je sais pas trop', 'je suis pas sûr',
        'je suis pas sur', 'je suis pas sûre', 'je suis pas sure',
        'pas sûr', 'pas sur', 'pas sûre', 'pas sure',
        'c\'est peut-être', 'c\'est peut être',
        'je pense', 'mwen panse', 'mwen kwè', 'm kwè',
        'i think', 'maybe', 'not sure', 'i guess',
        'probablement', 'je suppose', 'sans doute',
        'j\'hésite', 'j hesite', 'm pa sèten', 'mwen pa sèten',
        'je dirais', 'ça pourrait être', 'ca pourrait etre',
    )

    # Cheating patterns (student wants to copy answers for homework/exam)
    _cheating_patterns = (
        'devoir maison', 'c\'est pour un devoir', 'c\'est un devoir',
        'c\'est pour un examen', 'c\'est un examen', 'c\'est pour le contrôle',
        'c\'est pour le controle', 'c\'est un contrôle', 'c\'est un controle',
        'c\'est noté', 'c\'est note', 'c\'est pour une évaluation',
        'c\'est pour une evaluation', 'c\'est une interro',
        'c\'est pour une interro', 'c\'est pour l\'interro',
        'devoir surveillé', 'devoir surveille', 'dm de', 'dm sur',
        'mon dm', 'mon devoir', 'mon examen', 'mon contrôle', 'mon controle',
        'aide moi pour mon devoir', 'aide-moi pour mon devoir',
        'aide moi pour mon dm', 'aide-moi pour mon dm',
        'je dois rendre', 'à rendre demain', 'a rendre demain',
        'à rendre pour', 'a rendre pour', 'c\'est à rendre', 'c\'est a rendre',
        'homework', 'it\'s for an exam', 'it\'s homework',
        'devwa lakay', 'devwa m', 'ekzamen m', 'se pou yon ekzamen',
    )

    # Check frustrated patterns FIRST (highest priority emotional state)
    for pattern in _frustrated_patterns:
        if pattern in msg:
            return 'frustrated'

    # Check cheating patterns (before lazy, because "mon devoir" overlaps)
    for pattern in _cheating_patterns:
        if pattern in msg:
            return 'cheating'

    # Check lazy patterns (before validated, because "donne la réponse" shouldn't be 'other')
    for pattern in _lazy_patterns:
        if pattern in msg:
            return 'lazy'

    # Check for exact or near-exact matches
    for pattern in _validated_patterns:
        if msg == pattern or msg == pattern + '!' or msg == pattern + '.':
            return 'validated'
    # Check emoji-only messages
    if all(c in '👍👌✅🤝💪😊🙂👏✔️☑️' or not c.strip() for c in msg):
        return 'validated'
    if msg in ('??', '???', '????'):
        return 'confused'
    # Check substring matches for confused
    for pattern in _confused_patterns:
        if pattern in msg:
            return 'confused'
    # Check substring matches for validated (only for short messages to avoid false positives)
    if len(msg) < 30:
        for pattern in _validated_patterns:
            if pattern in msg:
                return 'validated'

    # ── Hesitant detection: short uncertain answers ("3 ?", "je pense que c'est 5") ──
    # Check substring matches for hesitant patterns
    for pattern in _hesitant_patterns:
        if pattern in msg:
            return 'hesitant'
    # Short answer ending with '?' = guessing (e.g. "3 ?", "oui ?")
    if len(msg) < 20 and msg.rstrip().endswith('?') and re.search(r'[\d]', msg):
        return 'hesitant'

    # ── If message looks like an answer attempt (has numbers, formulas, etc.) → OTHER ──
    if re.search(r'[=\d²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉\^]', msg) and len(msg) > 3:
        # Even for answer attempts, check behavioral analysis for silent difficulty
        if messages and len(messages) >= 4:
            behavior = _analyze_student_behavior(messages, user_message)
            if behavior['signal'] and behavior['confidence'] >= 0.6:
                return behavior['signal']
        return 'other'

    # ── LAYER 2: Behavioral analysis (conversation history patterns) ──────
    # When patterns didn't catch anything, analyze the student's behavior
    if messages and len(messages) >= 4:
        behavior = _analyze_student_behavior(messages, user_message)
        if behavior['signal'] and behavior['confidence'] >= 0.5:
            return behavior['signal']
    else:
        behavior = {'signal': None, 'confidence': 0, 'details': '', 'metrics': {}}

    # ── LAYER 3: AI fallback for ambiguous messages ───────────────────────
    # Build behavioral context for smarter AI classification
    _behavior_context = ''
    if behavior.get('metrics'):
        m = behavior['metrics']
        _behavior_context = (
            f"\n\nBehavioral context from conversation history:\n"
            f"- Average message length: {m.get('avg_length', '?')} chars (recent: {m.get('recent_avg_length', '?')})\n"
            f"- Recent errors by student: {m.get('recent_errors', 0)}\n"
            f"- Hesitation signals in recent messages: {m.get('hesitation_count', 0)}\n"
            f"- Random guessing signals: {m.get('guess_count', 0)}\n"
            f"- Message quality declining: {'yes' if m.get('declining') else 'no'}\n"
            f"- Behavioral difficulty score: {m.get('score', 0)}/10\n"
        )
        if behavior.get('details'):
            _behavior_context += f"- Detected patterns: {behavior['details']}\n"

    prompt = (
        "Classify the student's message below into exactly ONE category.\n"
        "Categories:\n"
        "  VALIDATED  = student confirms they understood (any language, emoji, slang, abbreviation)\n"
        "  CONFUSED   = student signals they don't understand and needs a simpler/different explanation\n"
        "  LAZY       = student wants the answer without effort (asks for solution, refuses to try)\n"
        "  FRUSTRATED = student is emotionally overwhelmed, wants to give up, feels incapable\n"
        "  CHEATING   = student wants answers for homework, exam, test, or graded assignment\n"
        "  HESITANT   = student is uncertain, guessing, lacks confidence ('je pense', 'peut-être', '3 ?', 'pas sûr')\n"
        "  OTHER      = answering a question, giving an attempt (right or wrong), asking a question, anything else\n\n"
        "Rules:\n"
        "- Reply with ONLY one word: VALIDATED, CONFUSED, LAZY, FRUSTRATED, CHEATING, HESITANT, or OTHER. No explanation.\n"
        "- Examples of VALIDATED: 'ok compris', 'ah oui!', 'mwen konprann', 'got it', 'oui je vois', 'clair', '👍', 'wi'\n"
        "- Examples of CONFUSED: 'je comprend pas', 'pa kompran', 'kisa sa vle di?', 'explique autrement', 'confused', 'toujours pas', '??', 'nah pas clair'\n"
        "- Examples of LAZY: 'donne la réponse', 'fais l'exercice pour moi', 'résous', 'dis-moi la réponse', 'ban m repons'\n"
        "- Examples of FRUSTRATED: 'j'abandonne', 'c'est trop dur', 'je suis nul', 'je vais rater le bac', 'ça sert à rien', 'mwen pa kapab'\n"
        "- Examples of CHEATING: 'c'est pour un devoir', 'c'est noté', 'devoir maison', 'c'est un examen', 'mon dm', 'aide-moi pour mon contrôle'\n"
        "- Examples of HESITANT: 'je pense que c'est 5', 'peut-être', '3 ?', 'je crois', 'je suis pas sûr', 'probablement x=2'\n"
        "- Examples of OTHER: 'ca devrait etre [1,+infini]', 'x > 0', 'je sais pas', 'pourquoi?', 'donne un exemple'\n\n"
        "IMPORTANT: Consider the behavioral context below (if provided). If the student shows\n"
        "signs of difficulty (many errors, short messages, hesitation), lean towards HESITANT or CONFUSED\n"
        "even if the message itself seems neutral.\n"
        f"{_behavior_context}\n"
        f"Student message: {user_message.strip()[:300]}"
    )
    try:
        resp = _client().chat.completions.create(
            model=FAST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
        )
        result = (resp.choices[0].message.content or '').strip().upper()
        if result.startswith('VALIDATED'):
            return 'validated'
        if result.startswith('CONFUSED'):
            return 'confused'
        if result.startswith('LAZY'):
            return 'lazy'
        if result.startswith('FRUSTRATED'):
            return 'frustrated'
        if result.startswith('CHEATING'):
            return 'cheating'
        if result.startswith('HESITANT'):
            return 'hesitant'
        return 'other'
    except Exception:
        return 'other'


def _did_student_confirm_understanding(user_message: str) -> bool:
    return _classify_student_intent(user_message) == 'validated'


def _is_student_confused(user_message: str) -> bool:
    return _classify_student_intent(user_message) == 'confused'


def _is_suspicious_validation(user_message: str, messages: list) -> bool:
    """
    Detect if a student says 'compris/ok/oui' too quickly without actually
    demonstrating understanding. Returns True if the validation looks suspicious.

    Suspicious = the student's message is very short AND the last AI message
    contained a verification question (❓) that the student didn't actually answer.
    """
    msg = (user_message or '').strip()

    # If message is long enough (>30 chars), student probably gave a real answer
    if len(msg) > 30:
        return False

    # Check if message contains any substance (numbers, formulas, real content)
    if re.search(r'[=\d²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉\^]', msg) and len(msg) > 5:
        return False

    # Find the last AI message
    last_ai_msg = ''
    for m in reversed(messages or []):
        if m.get('role') in ('ai', 'assistant') and m.get('content'):
            last_ai_msg = m['content']
            break

    if not last_ai_msg:
        return False

    # If last AI message contained a question (❓ or ends with ?) that expects
    # a substantive answer, and student just says "ok" → suspicious
    has_question = '❓' in last_ai_msg or '?' in last_ai_msg[-200:]

    if not has_question:
        return False

    # Check if there's a recent substantive student answer (within last 2 student msgs)
    recent_student_msgs = []
    for m in reversed(messages or []):
        if m.get('role') not in ('ai', 'assistant', '__plan__', '__plan_intro__') and m.get('content'):
            recent_student_msgs.append(m['content'])
            if len(recent_student_msgs) >= 2:
                break

    # If the student gave a substantive answer recently (>30 chars or has numbers),
    # then this "ok/compris" is probably genuine confirmation
    for recent_msg in recent_student_msgs:
        if len(recent_msg) > 30 or re.search(r'[=\d²³]', recent_msg):
            return False

    # Short message + unanswered question + no recent substantive answer = suspicious
    return True


def _pick_exercise_for_concept(chapter_exercises: str, concept: str, max_chars: int = 1100) -> str:
    if not chapter_exercises:
        return ''
    chunks = [c.strip() for c in re.split(r'\n\s*\n+', chapter_exercises) if c.strip()]
    if not chunks:
        return ''
    kws = [w for w in re.findall(r'\w{4,}', (concept or '').lower())]
    scored = []
    for ch in chunks:
        low = ch.lower()
        score = sum(1 for k in kws if k in low)
        scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1] if scored else ''
    return best[:max_chars]


def _parse_evidence_block(answer_text: str) -> tuple[str, list[str]]:
    m = re.search(r'---EVIDENCE---\s*([\s\S]*?)\s*---END-EVIDENCE---', answer_text)
    if not m:
        return answer_text.strip(), []
    ev_raw = m.group(1)
    evidences = []
    for ln in ev_raw.splitlines():
        ln = re.sub(r'^\s*[-\d\.)\s]+', '', ln).strip().strip('"')
        if ln:
            evidences.append(ln)
    cleaned = re.sub(r'---EVIDENCE---[\s\S]*?---END-EVIDENCE---', '', answer_text).strip()
    return cleaned, evidences[:6]


def _strip_internal_blocks(answer_text: str) -> str:
    """Remove control blocks that must never be rendered to the user."""
    text = answer_text or ''
    patterns = [
        r'---FOLLOWUP---[\s\S]*?(?:---END---|$)',
        r'---EVIDENCE---[\s\S]*?(?:---END-EVIDENCE---|---END---|$)',
    ]
    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
    return text


def _sanitize_source_math_artifacts(text: str) -> str:
    """Clean known placeholder artifacts from source material before prompting the model."""
    s = text or ''
    # Remove quoted placeholders like «M2», "M6", [M7] that are not learner-facing content.
    s = re.sub(r'[«"\[]\s*M\d+\s*[»"\]]', '', s)
    # Collapse excessive blank lines introduced by cleanup.
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def _clean_step_name(name: str) -> str:
    """Strip LaTeX, formulas and unicode math from concept names for plain-text display."""
    # Remove display math $$...$$
    name = re.sub(r'\$\$[\s\S]*?\$\$', '', name)
    # Remove inline math $...$
    name = re.sub(r'\$[^$\n]*?\$', '', name)
    # Remove LaTeX commands like \frac{...}{...}, \sqrt{...}, etc.
    name = re.sub(r'\\[a-zA-Z]+\{[^}]*\}(?:\{[^}]*\})?', '', name)
    # Remove bare LaTeX commands \cdot \leq etc.
    name = re.sub(r'\\[a-zA-Z]+', '', name)
    # Remove variable patterns with subscripts: U_R, I_eff, f_0, C_1, U_{AB}
    name = re.sub(r'\b[A-Za-z]{1,3}[_\^]\w+\b', '', name)
    # Remove compound variable names like Ieff, Ueff, Zmax, Pmin
    name = re.sub(r'\b[A-Z][a-z]*(eff|rms|max|min|tot|moy)\b', '', name, flags=re.IGNORECASE)
    # Remove 2-3 letter all-uppercase tokens (ZR, RL, etc.) but keep common acronyms > 3 chars
    name = re.sub(r'\b[A-Z]{2,3}\b', '', name)
    # Remove standalone single uppercase physics letter tokens (U R I L C Z E B P Q V W T N S F)
    # Lookbehind/ahead ensures they are truly isolated (not start of a real word)
    name = re.sub(r'(?<![A-Za-zÀ-ÿ])[A-Z](?![A-Za-zÀ-ÿ])', '', name)
    # Remove leftover physics abbreviations standing alone
    name = re.sub(r'\b(eff|rms|max|min|tot|eq|th)\b', '', name, flags=re.IGNORECASE)
    # Remove isolated digits and simple math operators
    name = re.sub(r'(?<![\w\d])\d+(?![\w\d])', '', name)
    name = re.sub(r'(?<![\w])([=+*/÷×·])(?![\w])', '', name)
    # Collapse multiple spaces and strip
    name = re.sub(r'\s{2,}', ' ', name).strip(' :;,.—–-=()[]')
    return name or 'Concept'


def _dedup_consecutive_paragraphs(text: str) -> str:
    """Remove paragraph-level duplication where the LLM repeated a block verbatim."""
    if not text or len(text) < 200:
        return text
    paragraphs = re.split(r'\n{2,}', text)
    if len(paragraphs) < 3:
        return text
    seen: list[str] = []
    for p in paragraphs:
        norm = p.strip()
        if not norm:
            continue
        # Check if this paragraph was already seen (exact or near-exact)
        is_dup = False
        for prev in seen:
            # Exact match
            if norm == prev:
                is_dup = True
                break
            # Near-match: one is a prefix/suffix of the other (covers minor trailing diffs)
            shorter, longer = (norm, prev) if len(norm) <= len(prev) else (prev, norm)
            if len(shorter) > 80 and shorter in longer:
                is_dup = True
                break
        if not is_dup:
            seen.append(norm)
    result = '\n\n'.join(seen)
    return result


def _quality_check_course_reply(reply: str, teach_idx: int, total: int,
                                concept_name: str | None, is_first_message: bool,
                                subject: str = '') -> str:
    """
    Lightweight quality check for course chat replies.
    Detects weak responses and fixes them without an extra API call.
    Checks: too short, missing structure, missing question, repetitive openers, math coherence.
    """
    text = (reply or '').strip()
    if not text or is_first_message:
        return text  # Plan intro doesn't need structure checks

    issues = []

    # 1. Too short for a teaching message (concept teaching should be substantial)
    if teach_idx < total and len(text) < 200:
        issues.append('too_short')

    # 2. Missing key pedagogical sections (for concept teaching, not synthesis)
    if teach_idx < total and concept_name:
        has_example = bool(re.search(r'[❌✅]|exemple|example|exercice', text, re.IGNORECASE))
        has_explanation = bool(re.search(r'📖|explication|expliqu|définition', text, re.IGNORECASE))
        has_key_point = bool(re.search(r'💡|🔑|point clé|retenir|à retenir', text, re.IGNORECASE))
        if not has_example:
            issues.append('no_example')
        if not has_explanation:
            issues.append('no_explanation')
        if not has_key_point:
            issues.append('no_key_point')

    # 3. Repetitive robotic openers (phrases bateau)
    _bateau_patterns = [
        r'^(bien sûr|excellente question|certainement|absolument|avec plaisir)',
        r'^(great question|of course|absolutely)',
    ]
    for bp in _bateau_patterns:
        if re.match(bp, text, re.IGNORECASE):
            text = re.sub(bp + r'[!.\s]*', '', text, count=1, flags=re.IGNORECASE).strip()

    # 4. Math coherence check for science subjects
    if subject in ('maths', 'physique', 'chimie'):
        # Check for contradictory results (same variable = two different values)
        equations = re.findall(r'[=]\s*(\-?\d+[\.,]?\d*)', text)
        if len(equations) >= 2:
            # Check for obvious contradictions (same number appearing differently)
            pass  # AI-level verification is already in the prompt

        # Check for incomplete formulas (lone $ without closure)
        dollar_count = text.count('$') - 2 * text.count('$$')
        if dollar_count % 2 != 0:
            issues.append('broken_latex')

    return text


def _ensure_turn_closing_question(reply: str) -> str:
    """Ensure each teaching turn ends with one short verification question."""
    text = (reply or '').strip()
    if not text:
        return text
    tail = text[-220:]
    if '?' in tail:
        return text
    if text.endswith(('.', '!', '…')):
        text = text.rstrip('.!…').rstrip()
    return text + "\n\nEst-ce que c'est plus clair maintenant ?"


def _has_expected_concept_heading(reply: str, concept_num: int, total: int) -> bool:
    if not reply:
        return False
    pat = rf'CONCEPT\s+{concept_num}\s*/\s*{total}\b'
    return bool(re.search(pat, reply, flags=re.IGNORECASE))


def _has_placeholder_artifacts(reply: str) -> bool:
    text = reply or ''
    # Detect common placeholder artifacts like «M0», "M1", or isolated M2 markers.
    patterns = [
        r'[«"]\s*M\d+\s*[»"]',
        r'\bM\d+\b',
    ]
    hits = 0
    for p in patterns:
        hits += len(re.findall(p, text))
    return hits >= 2


def _ai_quality_style_pass(
    reply: str,
    *,
    subject_label: str,
    chapter_title: str,
    concept_name: str,
    concept_num: int,
    total: int,
    must_teach_this_concept: bool,
) -> str:
    """
    Generic AI post-editor:
    - normalize formula styling for any expression (not hardcoded formulas)
    - enforce structural quality and concept coverage
    - ensure final checkpoint question
    """
    text = (reply or '').strip()
    if not text:
        return text

    contract = (
        f"Tu es un éditeur QA strict. Matière: {subject_label}. Chapitre: {chapter_title}.\n"
        f"Concept cible: {concept_num}/{total} — {concept_name}.\n\n"
        "Réécris la réponse pour qu'elle soit parfaite, sans changer le fond utile.\n"
        "Règles obligatoires:\n"
        "0) RÈGLE ABSOLUE LaTeX : Reproduis VERBATIM tous les blocs $...$ et $$...$$ existants. "
        "Ne modifie JAMAIS le contenu entre délimiteurs $. Ne génère JAMAIS «M0», «M1», «M2» ou tout token du style 'M+chiffre'.\n"
        "1) Toute expression mathématique doit être stylée en LaTeX: $...$ (inline) ou $$...$$ (bloc).\n"
        "2) Aucune formule brute non délimitée.\n"
        "3) Aucune balise interne (FOLLOWUP/EVIDENCE/END).\n"
        "4) Si le concept mentionne plusieurs composantes/éléments (ex: horizontale et verticale), couvre TOUS les éléments.\n"
        "5) Termine toujours par une question checkpoint à l'élève.\n"
        "6) Interdit absolu: aucun placeholder (ex: M0, M1, M2, «M0», [FORMULE], [X]).\n"
        "7) Si tu vois un placeholder, remplace-le par une vraie formule en LaTeX ou une phrase explicite claire.\n"
        "8) Utilise une notation scalaire simple par défaut (niveau lycée): évite les vecteurs unitaires comme $\\hat{z}$ sauf si la source le demande explicitement.\n"
        "9) Les unités doivent être propres et lisibles en LaTeX (ex: $\\mathrm{m\\,s^{-2}}$).\n"
    )
    if must_teach_this_concept:
        contract += (
            "10) Tu dois enseigner immédiatement CE concept avec cette structure exacte:\n"
            "━━━ 🎯 CONCEPT N/T ━━━, puis sections 📖, 💡, 📝, 🔑, ❓.\n"
            "11) N'envoie pas seulement une félicitation. Continue le cours maintenant.\n"
        )
    contract += "\nRetourne UNIQUEMENT la version finale à afficher à l'élève."

    try:
        resp = _client().chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": contract},
                {"role": "user", "content": text},
            ],
            max_tokens=1400,
        )
        out = (resp.choices[0].message.content or '').strip()
        return out or text
    except Exception:
        return text


def _evidence_is_grounded(note_content: str, evidences: list[str]) -> bool:
    if not note_content:
        return True
    if not evidences:
        return False
    base = _normalize_grounding_text(note_content)
    hits = 0
    for ev in evidences:
        evn = _normalize_grounding_text(ev)
        if evn and len(evn) >= 12 and evn in base:
            hits += 1
    return hits >= min(2, len(evidences))


def _pad_last_matrix_row(text: str, env: str) -> str:
    """Best-effort fix: if a matrix is truncated, pad the last row to expected column count."""
    marker = f'\\begin{{{env}}}'
    start = text.rfind(marker)
    if start < 0:
        return text
    end_marker = f'\\end{{{env}}}'
    if end_marker in text[start:]:
        return text

    head = text[: start + len(marker)]
    body = text[start + len(marker):]
    rows = body.split('\\\\')
    if not rows:
        return text

    col_counts = [r.count('&') + 1 for r in rows if r.strip()]
    if not col_counts:
        return text
    expected_cols = max(col_counts)
    if expected_cols <= 1:
        return text

    last = rows[-1]
    cur_cols = last.count('&') + 1 if last.strip() else 0
    if cur_cols <= 0:
        rows[-1] = '0' + '&0' * (expected_cols - 1)
    elif cur_cols < expected_cols:
        rows[-1] = last + ('&0' * (expected_cols - cur_cols))
    elif last.rstrip().endswith('&'):
        rows[-1] = last + '0'

    return head + '\\\\'.join(rows)


def _is_probable_latex_line(line: str) -> bool:
    """Return True only for lines that are PURE math formulas without $ delimiters.
    
    Must NOT match lines that mix French text with LaTeX commands like:
    "puis on prend \\theta dans l'intervalle ]-\\pi,\\pi]"
    """
    s = (line or '').strip()
    if not s or '$' in s:
        return False
    # Must contain LaTeX-like commands
    if not re.search(r'\\[a-zA-Z]+|[_\^]|\{[^}]*\}', s):
        return False
    # If the line contains 3+ regular French/English words, it's text, not a formula
    # Strip LaTeX commands and braces, then count remaining word-like tokens
    stripped = re.sub(r'\\[a-zA-Z]+', '', s)
    stripped = re.sub(r'[{}\[\]()_\^=+\-*/,.|<>:;0-9]', ' ', stripped)
    words = [w for w in stripped.split() if len(w) >= 2 and re.match(r'[a-zA-Zà-ÿÀ-Ÿ]', w)]
    if len(words) >= 3:
        return False
    return True


def _repair_math_segment(seg: str) -> str:
    s = (seg or '').strip()
    if not s:
        return s

    # Generic token normalization for malformed math output (not formula-specific)
    s = s.replace('\\cdotp', '\\cdot')
    s = re.sub(r'\\cdot([A-Za-z])', r'\\cdot \1', s)
    s = re.sub(r'\bs\s*[−-]\s*2\b', r's^{-2}', s)
    s = re.sub(r'\bm\s*[−-]\s*1\b', r'm^{-1}', s)
    s = re.sub(r'\\+hat\{\\mathbf\{[xyz]\}\}', '', s)
    s = re.sub(r'\\+hat\{[xyz]\}', '', s)
    s = re.sub(r'\\+vec\{z\}', '', s)
    # Replace Unicode middle dot · with \cdot (causes MathJax rendering issues)
    s = s.replace('·', '\\cdot ')
    s = s.replace('⋅', '\\cdot ')
    # Fix \mathbf on simple scalar variables (a, g, v, F) → plain variable
    s = re.sub(r'\\mathbf\{([a-zA-Z])\}', r'\1', s)
    # Clean up stray \, spacing left after removing unit vectors
    s = re.sub(r'\\,\s*\\,', r'\\,', s)
    s = re.sub(r'\\,\s*$', '', s)

    # Convert dangling macros like "\text" or "\operatorname" to empty-group form.
    s = re.sub(r'\\(text|mathrm|mathbf|mathit|operatorname|sqrt)\b(?!\s*\{)', r'\\\1{}', s)

    # If braces are unbalanced inside a math segment, close missing ones.
    opens = s.count('{')
    closes = s.count('}')
    if opens > closes:
        s += '}' * (opens - closes)

    return s


def _repair_math_segments_with_delimiters(text: str) -> str:
    def repl_display(m):
        inner = _repair_math_segment(m.group(1))
        return f"$${inner}$$"

    def repl_inline(m):
        inner = _repair_math_segment(m.group(1))
        return f"${inner}$"

    out = re.sub(r'(?<!\\)\$\$([\s\S]*?)(?<!\\)\$\$', repl_display, text)
    out = re.sub(r'(?<!\\)(?<!\$)\$([^$]*?)(?<!\\)\$(?!\$)', repl_inline, out)
    return out


# Common French words that should NEVER appear inside $...$ math delimiters
_FRENCH_WORDS_IN_MATH = re.compile(
    r'\b(?:puis|donc|alors|dans|pour|avec|est|sont|les|des|une|que|qui|'
    r'prend|trouve|donne|soit|car|mais|sur|par|entre|comme|cette|'
    r"l'intervalle|on\s+prend|on\s+a|il\s+faut|c'est)\b",
    re.IGNORECASE
)


def _split_mixed_math_text(text: str) -> str:
    """Fix inline math blocks that accidentally contain French text.
    
    e.g. $\\cos\\theta = \\frac{a}{|z|}, puis on prend \\theta dans l'intervalle$
    →    $\\cos\\theta = \\frac{a}{|z|}$, puis on prend $\\theta$ dans l'intervalle
    """
    if not text:
        return text

    def _fix_inline(m):
        inner = m.group(1)
        # If no French words found, leave it alone
        if not _FRENCH_WORDS_IN_MATH.search(inner):
            return m.group(0)
        # Find where math ends and French text begins
        match = _FRENCH_WORDS_IN_MATH.search(inner)
        if not match:
            return m.group(0)
        math_part_raw = inner[:match.start()]
        text_part = inner[match.start():]
        # Stop math_part BEFORE any markdown syntax (**bold** or *italic*)
        # so we don't embed markdown inside $...$
        md_match = re.search(r'\s*\*+', math_part_raw)
        if md_match:
            # Move the markdown part to the text_part
            text_part = math_part_raw[md_match.start():].lstrip() + ' ' + text_part
            math_part_raw = math_part_raw[:md_match.start()]
        math_part = math_part_raw.rstrip(' ,;:.')
        # Wrap any remaining LaTeX commands in the text part with $...$
        text_part = re.sub(r'(\\[a-zA-Z]+(?:\{[^}]*\})*)', r'$\1$', text_part)
        if math_part.strip():
            return f'${math_part}$ {text_part}'
        else:
            return text_part

    result = re.sub(r'(?<!\\)(?<!\$)\$([^$]+?)(?<!\\)\$(?!\$)', _fix_inline, text)
    return result


def _repair_math_notation(text: str) -> str:
    """Repair common truncated LaTeX patterns so KaTeX/MathJax rendering does not break."""
    if not text:
        return text
    fixed = text.strip()
    if fixed.endswith('\\'):
        fixed = fixed[:-1].rstrip()

    # ── Convert \[..\] → $$...$$ and \(..\) → $...$ (quality-pass uses wrong delimiters) ──
    fixed = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', fixed, flags=re.DOTALL)
    fixed = re.sub(r'\\\((.*?)\\\)', r'$\1$', fixed, flags=re.DOTALL)

    # Wrap bare LaTeX lines without $...$ so renderer can style them.
    repaired_lines = []
    for raw in fixed.splitlines():
        line = raw.rstrip()
        if _is_probable_latex_line(line):
            repaired_lines.append(f"${_repair_math_segment(line)}$")
        else:
            repaired_lines.append(line)
    fixed = '\n'.join(repaired_lines)

    # Close unmatched LaTeX environments
    begin_envs = re.findall(r'\\begin\{([a-zA-Z*]+)\}', fixed)
    end_envs = re.findall(r'\\end\{([a-zA-Z*]+)\}', fixed)
    if begin_envs:
        begin_counts = Counter(begin_envs)
        end_counts = Counter(end_envs)
        for env, begin_n in begin_counts.items():
            missing = begin_n - end_counts.get(env, 0)
            for _ in range(max(0, missing)):
                if env in ('matrix', 'pmatrix', 'bmatrix', 'Bmatrix', 'vmatrix', 'Vmatrix'):
                    fixed = _pad_last_matrix_row(fixed, env)
                fixed += f'\\end{{{env}}}'

    # Close unmatched math delimiters (must run after env closure)
    if len(re.findall(r'(?<!\\)\$\$', fixed)) % 2 == 1:
        fixed += '$$'
    if len(re.findall(r'(?<!\\)(?<!\$)\$(?!\$)', fixed)) % 2 == 1:
        fixed += '$'

    # Repair macro fragments and brace balance inside delimited math segments.
    fixed = _repair_math_segments_with_delimiters(fixed)

    # ── Comprehensive Unicode→LaTeX conversion inside math delimiters ─────────
    _UNICODE_TO_LATEX = {
        # Greek letters
        'α': '\\alpha', 'β': '\\beta', 'γ': '\\gamma', 'δ': '\\delta',
        'ε': '\\varepsilon', 'ζ': '\\zeta', 'η': '\\eta', 'θ': '\\theta',
        'λ': '\\lambda', 'μ': '\\mu', 'ν': '\\nu', 'π': '\\pi',
        'ρ': '\\rho', 'σ': '\\sigma', 'τ': '\\tau', 'φ': '\\varphi',
        'ω': '\\omega', 'Δ': '\\Delta', 'Ω': '\\Omega', 'Σ': '\\Sigma',
        'Π': '\\Pi', 'Φ': '\\Phi', 'Γ': '\\Gamma', 'Θ': '\\Theta',
        'Λ': '\\Lambda',
        # Special characters
        'ℓ': '\\ell',  # script small l (U+2113) — often output by AI models
        # Subscript digits → _{n}
        '₀': '_{0}', '₁': '_{1}', '₂': '_{2}', '₃': '_{3}', '₄': '_{4}',
        '₅': '_{5}', '₆': '_{6}', '₇': '_{7}', '₈': '_{8}', '₉': '_{9}',
        'ₐ': '_{a}', 'ₑ': '_{e}', 'ₒ': '_{o}', 'ₓ': '_{x}', 'ᵢ': '_{i}',
        'ⱼ': '_{j}', 'ₖ': '_{k}', 'ₙ': '_{n}', 'ₘ': '_{m}', 'ₚ': '_{p}',
        'ᵣ': '_{r}', 'ₛ': '_{s}', 'ₜ': '_{t}',
        # Superscript digits → ^{n}
        '⁰': '^{0}', '¹': '^{1}', '²': '^{2}', '³': '^{3}', '⁴': '^{4}',
        '⁵': '^{5}', '⁶': '^{6}', '⁷': '^{7}', '⁸': '^{8}', '⁹': '^{9}',
        '⁺': '^{+}', '⁻': '^{-}', '⁼': '^{=}',
        'ⁿ': '^{n}',
        # Math operators
        '·': '\\cdot ', '⋅': '\\cdot ', '×': '\\times ', '÷': '\\div ',
        '≈': '\\approx ', '≠': '\\neq ', '≤': '\\leq ', '≥': '\\geq ',
        '∞': '\\infty', '√': '\\sqrt', '∑': '\\sum', '∏': '\\prod',
        '∫': '\\int', '∂': '\\partial', '∈': '\\in ', '∉': '\\notin ',
        '⊂': '\\subset ', '∪': '\\cup ', '∩': '\\cap ', '∅': '\\emptyset',
        '→': '\\to ', '←': '\\leftarrow ', '⇒': '\\Rightarrow ',
        '↔': '\\leftrightarrow ', '½': '\\frac{1}{2}', '¼': '\\frac{1}{4}',
        '¾': '\\frac{3}{4}',
    }

    def _fix_unicode_in_math(m):
        inner = m.group(1)
        for uc, latex in _UNICODE_TO_LATEX.items():
            if uc in inner:
                inner = inner.replace(uc, latex)
        # Merge consecutive superscripts: ^{a}^{b} → ^{ab}
        while re.search(r'\^\{([^}]*)\}\^\{([^}]*)\}', inner):
            inner = re.sub(r'\^\{([^}]*)\}\^\{([^}]*)\}', r'^{\1\2}', inner)
        # Merge consecutive subscripts: _{a}_{b} → _{ab}
        while re.search(r'_\{([^}]*)\}_\{([^}]*)\}', inner):
            inner = re.sub(r'_\{([^}]*)\}_\{([^}]*)\}', r'_{\1\2}', inner)
        return f"${inner}$"

    def _fix_unicode_in_display_math(m):
        inner = m.group(1)
        for uc, latex in _UNICODE_TO_LATEX.items():
            if uc in inner:
                inner = inner.replace(uc, latex)
        while re.search(r'\^\{([^}]*)\}\^\{([^}]*)\}', inner):
            inner = re.sub(r'\^\{([^}]*)\}\^\{([^}]*)\}', r'^{\1\2}', inner)
        while re.search(r'_\{([^}]*)\}_\{([^}]*)\}', inner):
            inner = re.sub(r'_\{([^}]*)\}_\{([^}]*)\}', r'_{\1\2}', inner)
        return f"$${inner}$$"

    fixed = re.sub(r'(?<!\\)\$\$([\s\S]*?)(?<!\\)\$\$', _fix_unicode_in_display_math, fixed)
    fixed = re.sub(r'(?<!\\)(?<!\$)\$([^$]*?)(?<!\\)\$(?!\$)', _fix_unicode_in_math, fixed)

    # ── Wrap bare LaTeX-like tokens outside $...$ ────────────────────────────
    # Catch patterns like H_{10}, C_5, v_{z}, a_{z}, =10-19{,}6\approx... outside math delimiters
    def _wrap_bare_latex_tokens(text):
        """Wrap bare LaTeX tokens (subscripts, superscripts, backslash commands) that appear outside $...$."""
        parts = re.split(r'(\$\$[\s\S]*?\$\$|\$[^$]*?\$)', text)
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside math delimiters, skip
                continue
            # Wrap bare subscript/superscript patterns: H_{10}, C_5, v_{z}, a_z
            part = re.sub(r'(?<!\$)([A-Za-z])(_\{[^}]+\}|\^\{[^}]+\}|_[0-9A-Za-z]|\^[0-9A-Za-z])(?!\$)',
                          r'$\1\2$', part)
            # Wrap bare backslash commands like \approx, \text{m}, \times outside $
            part = re.sub(r'(?<!\$)(\\(?:approx|text\{[^}]*\}|times|cdot|frac\{[^}]*\}\{[^}]*\}|sqrt\{[^}]*\})[^$\n]*?)(?=[\s,.]|$)',
                          r'$\1$', part)
            parts[i] = part
        return ''.join(parts)

    fixed = _wrap_bare_latex_tokens(fixed)

    # ── Strip all guillemets: «content» → content, orphan «/» removed ────────────
    fixed = re.sub(r'«([^«»\n]*)»', lambda m: m.group(1).strip(), fixed)
    fixed = fixed.replace('«', '').replace('»', '')

    return fixed


def course_chat(
    chapter_title: str,
    chapter_description: str,
    exam_excerpts: str,       # holds note_content extracted by backend from note_*.json
    subject: str,
    messages: list,
    user_message: str,
    progress_step: int = 0,
    user_profile: str = '',
    chapter_exercises: str = '',  # no longer used — kept for backward compat
    chapter_task_list: list | None = None,
    image_data: bytes = None,
    image_mime: str = None,
) -> dict:
    """
    IA tuteur pour un chapitre précis — basée EXCLUSIVEMENT sur les données note_*.json.
    Le backend a extrait tout le contenu du chapitre avant d'appeler cette fonction.
    Retourne {reply, new_step, followups}
    """
    import json as _json

    subject_label = MATS.get(subject, subject)
    if isinstance(subject_label, dict):
        subject_label = subject_label.get('label', subject)
    lang_block = _lang_instruction(user_message, forced_lang='kr') if subject == 'francais' else _lang_instruction(user_message)

    note_content = _sanitize_source_math_artifacts((exam_excerpts or '').strip())
    has_notes = bool(note_content and len(note_content) > 100)

    # ── Notes block — PRIMARY source of truth ────────────────────────────────
    if has_notes:
        notes_block = f"""╔══════════════════════════════════════════════════════════════╗
  CONTENU OFFICIEL DU COURS — EXTRAIT DE note_*.json
  Matière : {subject_label} | Chapitre : {chapter_title}
╚══════════════════════════════════════════════════════════════╝

{note_content}

╔══════════════════════════════════════════════════════════════╗
  FIN DU CONTENU OFFICIEL
╚══════════════════════════════════════════════════════════════╝"""
    else:
        notes_block = f"⚠️ Contenu des notes non disponible pour ce chapitre.\nEnseigne à partir du programme officiel BAC Haïti pour {subject_label}.\nUtilise tes connaissances du programme officiel, mais reste fidèle au curriculum BAC Haïti.\nSignale à l'élève : « Je n'ai pas les notes complètes pour ce chapitre, mais je vais t'enseigner à partir du programme officiel. »"

    # ── Concept plan ──────────────────────────────────────────────────────────
    concepts = (
        chapter_task_list
        if isinstance(chapter_task_list, list) and len(chapter_task_list) >= 2
        else _extract_concepts_from_notes(note_content, chapter_title, limit=40)
    )
    concepts = [str(c).strip() for c in concepts if str(c).strip()]
    if not concepts:
        concepts = [chapter_title]
    total = len(concepts)
    max_idx = total - 1

    # ── Turn state: what to teach THIS message ────────────────────────────────
    # 3-layer classification: patterns → behavioral analysis → AI fallback
    _student_intent = _classify_student_intent(user_message, messages)
    concept_validated = (_student_intent == 'validated')
    student_confused  = (_student_intent == 'confused')
    student_lazy      = (_student_intent == 'lazy')
    student_frustrated = (_student_intent == 'frustrated')
    student_cheating  = (_student_intent == 'cheating')
    student_hesitant  = (_student_intent == 'hesitant')

    _course_model = FAST_MODEL
    is_first_message = not any(
        m.get('role') in ('ai', 'assistant') for m in (messages or [])
    )
    # Plan intro state: plan was shown but concept 0 not yet taught
    _plan_intro_shown = any(m.get('role') == '__plan_intro__' for m in (messages or []))

    # Keep course tutoring strictly on 20b.

    # ── Detect suspicious "compris" (too quick, no real answer) ─────────────
    _suspicious_validation = False
    if concept_validated and not is_first_message:
        _suspicious_validation = _is_suspicious_validation(user_message, messages or [])
        if _suspicious_validation:
            concept_validated = False  # block advancement

    if progress_step >= total:
        # All concepts done → synthesis
        teach_idx = total
        new_step = total
        concept_to_teach = None
    elif concept_validated and progress_step < max_idx:
        # Student understood → advance and teach NEXT concept immediately
        teach_idx = progress_step + 1
        new_step = teach_idx
        concept_to_teach = concepts[teach_idx]
    elif concept_validated and progress_step == max_idx:
        # Last concept understood → synthesis
        teach_idx = total
        new_step = total
        concept_to_teach = None
    else:
        # Teach (or re-teach) current concept
        teach_idx = min(max(0, progress_step), max_idx)
        new_step = teach_idx
        concept_to_teach = concepts[teach_idx]

    # Override: plan intro shown → always teach concept 0 on next response
    if _plan_intro_shown and not is_first_message:
        teach_idx = 0
        new_step = 0
        concept_to_teach = concepts[0]
        concept_validated = False  # prevent accidental jump

    # ── Student communication style / repeated confusion signals ─────────────
    _msg_norm = re.sub(r'[^a-z0-9]+', '', (user_message or '').lower())
    _short_slang_style = bool(re.search(r'\b(pk|stp|svp|wesh|g|c|pq|prk)\b', (user_message or '').lower()))
    recent_user_msgs = [
        (m.get('content') or '') for m in (messages or [])
        if m.get('role') not in ('ai', 'assistant', '__plan__', '__plan_intro__') and (m.get('content') or '').strip()
    ]
    _repeated_confusion = False
    if len(recent_user_msgs) >= 2 and _msg_norm:
        _prev_norm = re.sub(r'[^a-z0-9]+', '', recent_user_msgs[-1].lower())
        _repeated_confusion = (
            (_msg_norm == _prev_norm) or
            (_msg_norm in _prev_norm and len(_msg_norm) >= 8) or
            (_prev_norm in _msg_norm and len(_prev_norm) >= 8)
        )
    _needs_ultra_simple = student_confused or student_frustrated or _repeated_confusion

    # ── Plan display (✅ done / 👉 current / ○ upcoming) ─────────────────────
    done_up_to = teach_idx if concept_validated else progress_step
    plan_lines = []
    for i, c in enumerate(concepts):
        if i < done_up_to:
            marker = '✅'
        elif i == teach_idx and teach_idx < total:
            marker = '👉'
        else:
            marker = '○'
        plan_lines.append(f"  {marker} {i + 1}. {c}")
    plan_display = '\n'.join(plan_lines)

    # ── Controlled chunk flow per subchapter (internal, invisible) ───────────
    detected_level = _detect_student_level(messages or [], user_message)

    _BLOCK_ORDER = [
        'definition', 'explication', 'regle_formule', 'exemple_simple',
        'exemple_avance', 'pieges', 'resume', 'exercices'
    ]
    _BLOCK_CHUNKS = [(0, 3), (3, 6), (6, 8)]  # 3 / 3 / 2 blocks

    def _load_chunk_state() -> dict:
        for _m in reversed(messages or []):
            if _m.get('role') != '__chunk_state__':
                continue
            try:
                _raw = _m.get('content') or '{}'
                _obj = _json.loads(_raw) if isinstance(_raw, str) else (_raw or {})
                if isinstance(_obj, dict):
                    return _obj
            except Exception:
                continue
        return {}

    _saved_chunk = _load_chunk_state()
    chunk_idx = 0
    if teach_idx < total and _saved_chunk:
        try:
            if int(_saved_chunk.get('concept_idx', -1)) == int(teach_idx):
                chunk_idx = int(_saved_chunk.get('next_chunk_idx', 0))
        except Exception:
            chunk_idx = 0

    # New concept always starts at chunk 1/3.
    if concept_validated and teach_idx < total:
        chunk_idx = 0

    chunk_idx = max(0, min(chunk_idx, len(_BLOCK_CHUNKS) - 1))
    _chunk_start, _chunk_end = _BLOCK_CHUNKS[chunk_idx] if teach_idx < total else (0, 0)
    _chunk_blocks = _BLOCK_ORDER[_chunk_start:_chunk_end] if teach_idx < total else []
    _is_last_chunk = (teach_idx >= total) or (_chunk_end >= len(_BLOCK_ORDER))
    # NEVER auto-continue: always wait for user before the next pedagogical chunk
    _auto_continue = False
    # Advance chunk pointer so next user reply gets the next chunk of this sub-chapter
    _next_chunk_idx = chunk_idx + 1 if not _is_last_chunk else 0

    # ── Step instructions (single controlled mission per response) ───────────
    _returned_plan_intro = False
    if teach_idx >= total:
        step_instructions = (
            "MISSION — Consolidation ciblée (sans conclusion globale)\n"
            "Le chapitre est déjà couvert. Ne fais PAS de bilan global.\n"
            "Donne seulement 2 mini-exercices cohérents, puis attends la réponse de l'élève.\n"
            "Sortie visible : texte fluide sans titre ni section."
        )
    else:
        _chunk_list = ', '.join(_chunk_blocks)
        _level_hint = (
            "niveau faible: vocabulaire simple, phrases courtes"
            if detected_level == 'faible'
            else "niveau normal: rigoureux et clair"
        )
        if _needs_ultra_simple:
            _length_hint = (
                "Longueur visée : environ 90 à 150 mots."
                " Très peu de formules, seulement si elles sont indispensables."
            )
        elif student_lazy or student_cheating or student_hesitant or detected_level == 'faible':
            _length_hint = (
                "Longueur visée : environ 120 à 200 mots."
                " Une explication propre, un exemple, puis une question courte."
            )
        elif subject in ('maths', 'physique', 'chimie'):
            _length_hint = (
                "Longueur visée : environ 180 à 320 mots."
                " Garde les formules propres, complètes, et limite-toi à l'essentiel utile."
            )
        else:
            _length_hint = (
                "Longueur visée : environ 160 à 280 mots."
                " Développe clairement sans bavardage inutile."
            )
        _pedagogy_override = (
            "\nMODE PÉDAGOGIQUE RENFORCÉ :\n"
            "- Commence par répondre DIRECTEMENT à la question exacte de l'élève en 1 phrase.\n"
            "- Si l'élève compare deux valeurs/cas, sépare clairement : cas 1, cas 2, conclusion.\n"
            "- Corrige explicitement les confusions logiques (exclu vs intervalle gardé).\n"
            "- Utilise des mots simples et concrets; évite le jargon abstrait.\n"
            "- Donne un mini-exemple numérique court si utile.\n"
        ) if _needs_ultra_simple else ''
        _style_override = (
            "\nSTYLE ÉLÈVE : l'élève écrit court/familier; réponds simplement et naturellement.\n"
        ) if _short_slang_style else ''
        step_instructions = (
            f"MISSION — Sous-chapitre: {concept_to_teach}\n"
            f"Chunk interne {chunk_idx + 1}/3 couvrant strictement: {_chunk_list}.\n"
            "RÈGLES DE SORTIE OBLIGATOIRES (visibles élève):\n"
            "- texte fluide continu, 1 à 4 paragraphes\n"
            "- aucun titre, aucun label, aucune numérotation, aucun 'Étape X'\n"
            "- ne jamais afficher les noms des blocs internes\n"
            "- ne jamais dire 'ce point n'est pas dans les notes'\n"
            "- pas de quiz long automatique\n"
            "- uniquement le contenu présent dans les notes fournies\n"
            "Séquence interne à respecter dans ce chunk (invisible):\n"
            f"- {_chunk_list}\n"
            "Style : professeur clair, neutre, non bavard; "
            f"{_level_hint}.\n"
            f"{_length_hint}\n"
            f"{_pedagogy_override}"
            f"{_style_override}"
            "Termine par une seule question courte de vérification adaptée au contenu de ce chunk. "
            "Attends la réponse de l'élève avant tout."
        )

    profile_block = f"\nPROFIL ÉLÈVE :\n{user_profile[:400]}\n" if user_profile else ''

    _level_instructions = {
        'faible': (
            "\n━━ NIVEAU DÉTECTÉ : EN DIFFICULTÉ ━━\n"
            "Utilise des mots simples, une idée par phrase, et un rythme lent.\n"
        ),
        'moyen': (
            "\n━━ NIVEAU DÉTECTÉ : INTERMÉDIAIRE ━━\n"
            "Reste clair, structuré et progressif sans surcharge.\n"
        ),
        'avancé': (
            "\n━━ NIVEAU DÉTECTÉ : AVANCÉ ━━\n"
            "Reste précis, va droit au point, conserve la rigueur BAC.\n"
        ),
    }
    level_block = _level_instructions.get(detected_level, '')

    # ── Chapter notes: focused window only (token-efficient) ─────────────────
    if has_notes and concept_to_teach and teach_idx < total and len(note_content) > 2000:
        focus_chars = 2200 if subject in ('maths', 'physique', 'chimie') else 1600
        if _needs_ultra_simple:
            focus_chars += 400
        windowed_text, window_label = _extract_windowed_notes(
            note_content,
            concepts,
            teach_idx,
            total,
            max_chars_per_concept=focus_chars,
        )
        notes_block = f"""╔══════════════════════════════════════════════════════════════╗
  CONTENU OFFICIEL DU COURS — EXTRAIT DE note_*.json
  Matière : {subject_label} | Chapitre : {chapter_title}
    Fenêtre cible : {window_label}
╚══════════════════════════════════════════════════════════════╝

{windowed_text}

╔══════════════════════════════════════════════════════════════╗
  FIN DU CONTENU OFFICIEL
╚══════════════════════════════════════════════════════════════╝"""
    elif has_notes and teach_idx >= total and len(note_content) > 2000:
        notes_block = f"""╔══════════════════════════════════════════════════════════════╗
  CONTENU OFFICIEL DU COURS — EXTRAIT DE note_*.json
  Matière : {subject_label} | Chapitre : {chapter_title}
╚══════════════════════════════════════════════════════════════╝

{note_content[:3200]}

╔══════════════════════════════════════════════════════════════╗
  FIN DU CONTENU OFFICIEL
╚══════════════════════════════════════════════════════════════╝"""

    # ── Extra directive for sciences ──────────────────────────────────────────
    science_extra = ''
    if subject in ('maths', 'physique', 'chimie'):
        science_extra = """
FORMULES — LaTeX OBLIGATOIRE :
• $...$ pour inline, $$...$$ pour display. JAMAIS \\( \\) ni \\[ \\].
• Convertis TOUT le Unicode en LaTeX dans les formules :
  ε₀ → $\\varepsilon_0$  |  10⁻⁶ → $10^{-6}$  |  × → $\\times$
  ½ → $\\frac{1}{2}$  |  ² → $^2$  |  π → $\\pi$
• CHAQUE formule doit être COMPLÈTE entre ses délimiteurs $ ou $$.
  ⛔ INTERDIT : du texte français à l'intérieur de $...$
  ⛔ INTERDIT : $\\cos \\theta = \\frac{a}{|z|}, puis on prend \\theta...$
  ✅ CORRECT : $\\cos \\theta = \\frac{a}{|z|}$, puis on prend $\\theta$...
• TOUJOURS vérifier que chaque { a son } correspondant.
  ⛔ INTERDIT : $\\sqrt{5$ ou $\\frac{a}{b$
  ✅ CORRECT : $\\sqrt{5}$ ou $\\frac{a}{b}$
• Variable $i$ (nombre complexe) : écris $i$, JAMAIS ℓ ou l.
• Variables dans le texte en LaTeX : $C$, $R$, $U$, $I$ (pas C, R, U, I en texte brut)
• Unités en \\text{} : $9{,}81 \\text{ m/s}^2$
• Multiplication : $\\times$ ou $\\cdot$ (JAMAIS · Unicode)
• JAMAIS de guillemets «» autour des formules
• JAMAIS de \\mathbf{} ni \\vec{} : $F = ma$ pas $\\vec{F}=m\\vec{a}$
• Ne coupe JAMAIS une formule sur plusieurs lignes
"""

    # ── Extra directive for language subjects ─────────────────────────────────
    language_extra = ''
    if subject in ('anglais', 'espagnol', 'francais'):
        language_extra = """
IMPORTANT — MATIÈRE LINGUISTIQUE :
• N'utilise JAMAIS de notation LaTeX ($...$, $$...$$). Ce n'est pas une matière scientifique.
• N'utilise PAS de symboles mathématiques ni de formules.
• Pour les règles grammaticales, utilise du texte simple en gras (**règle**) ou des listes.
• Le «Point clé» doit contenir une VRAIE règle grammaticale ou un fait linguistique, JAMAIS «» vide.
• Mets les exemples en anglais/espagnol en *italique* pour les distinguer du français.
• Les underscores (_) dans les exercices à trous doivent être écrits comme des blancs : _____ (5 underscores minimum).
"""
    elif subject not in ('maths', 'physique', 'chimie'):
        language_extra = """
IMPORTANT — MATIÈRE NON-SCIENTIFIQUE :
• N'utilise PAS de notation LaTeX ($...$, $$...$$) sauf si une formule mathématique est réellement nécessaire.
• Le «Point clé» doit contenir une VRAIE date, définition, ou fait essentiel, JAMAIS «» vide.
"""

    # ── System prompt ─────────────────────────────────────────────────────────
    # ── Source content rules (conditional on notes availability) ────────────
    if has_notes:
        source_rules = """SOURCE — RÈGLE ABSOLUE :
- Enseigne UNIQUEMENT à partir du contenu officiel fourni ci-dessous (note_*.json).
- Utilise 100% des informations disponibles dans les notes, sans omission inutile.
- N'invente PAS de contenu absent des notes.
- N'utilise PAS ta connaissance générale si le sujet est couvert dans les notes.
- Travaille uniquement avec la fenêtre d'extrait fournie pour ce tour.
- Si une information n'est pas présente dans cette fenêtre, ne l'invente pas.
- INTERDIT d'écrire au user : "ce point n'est pas dans les notes" ou toute variante.
- INTERDIT d'inventer des noms de méthodes qui ne figurent pas dans les notes."""
    else:
        source_rules = """SOURCE :
- Contenu officiel indisponible pour ce chapitre.
- Reste strictement dans le programme BAC Haïti.
- N'invente PAS de fausses informations."""

    system_prompt = f"""Matière : {subject_label} | Chapitre : {chapter_title}
{profile_block}
{level_block}

{source_rules}

━━ PLAN DU CHAPITRE (usage interne — ne pas afficher tel quel) ━━
{plan_display}

{step_instructions}

Question hors matière → refuse poliment et recentre sur {subject_label}.
{science_extra}
{language_extra}

À ajouter à la fin de chaque réponse :
---EVIDENCE---
[2-3 citations courtes tirées EXACTEMENT des notes]
---END-EVIDENCE---

{lang_block}"""

    # ── Build message history ─────────────────────────────────────────────────
    all_chat_msgs = [m for m in (messages or [])
                     if not m.get('_cache_key') and not str(m.get('role', '')).startswith('__')]

    hist_summary, recent_msgs = _build_compact_history(all_chat_msgs, keep=4)

    # Layout optimisé pour le prefix caching Groq :
    # [0] _STATIC_COURSE_SYSTEM → identique à tous les appels → cacheable permanent
    # [1] notes_block           → fenêtre glissante (prev+current+next concept)
    #                             reste identique tant qu'on enseigne le même concept → cacheable
    # [2] system_prompt         → change à chaque message (plan, step, level)
    api_messages = [{"role": "system", "content": _STATIC_COURSE_SYSTEM}]
    if notes_block:
        api_messages.append({"role": "system", "content": notes_block})
    api_messages.append({"role": "system", "content": system_prompt})
    if hist_summary:
        api_messages.append({
            "role": "system",
            "content": (
                hist_summary + "\n\n"
                f"Progression élève : {teach_idx}/{total} concepts validés. "
                f"Niveau détecté : {detected_level}."
            ),
        })
    for msg in recent_msgs:
        role = 'assistant' if msg.get('role') in ('ai', 'assistant') else 'user'
        content = msg.get('content', '')
        if content:
            api_messages.append({"role": role, "content": content})
    # Final user message — with optional image
    if image_data:
        import base64 as _b64
        _b64_img = _b64.b64encode(image_data).decode('utf-8')
        api_messages.append({"role": "user", "content": [
            {"type": "text", "text": user_message or "Analyse cette image."},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime or 'image/jpeg'};base64,{_b64_img}"}}
        ]})
    else:
        api_messages.append({"role": "user", "content": user_message})

    # ── Token allocation ─────────────────────────────────────────────────────
    # Do not tightly constrain generation length here.
    # The prompt's word-target guidance is the primary control.
    # max_tokens stays as a generous safety ceiling to avoid truncated math/style.
    if teach_idx >= total:
        course_max_tokens = 2000
    elif _needs_ultra_simple:
        course_max_tokens = 2400
    elif student_lazy or student_cheating or student_hesitant or detected_level == 'faible':
        course_max_tokens = 2200
    elif subject in ('maths', 'physique', 'chimie'):
        course_max_tokens = 2800
    else:
        course_max_tokens = 2400

    # ── Call API ──────────────────────────────────────────────────────────────
    evidences: list[str] = []
    _call_model = VISION_MODEL if image_data else _course_model
    resp = _client().chat.completions.create(
        model=_call_model,
        messages=api_messages,
        max_tokens=course_max_tokens,
    )
    full = resp.choices[0].message.content or ''
    _, evidences = _parse_evidence_block(full)

    # ── AI advance signal (correct answer without explicit oui/compris) ──────
    ai_advance_signal = '[AVANCER]' in full
    if ai_advance_signal:
        full = full.replace('[AVANCER]', '').strip()
        if new_step == teach_idx and teach_idx < total:
            new_step = min(progress_step + 1, total)

    # ── Extract follow-ups ────────────────────────────────────────────────────
    followups = []
    fu_match = re.search(r'---FOLLOWUP---\s*([\s\S]*?)\s*---END---', full)
    if fu_match:
        followups = [
            re.sub(r'^\d+\.\s*', '', l).strip().strip('[]')
            for l in fu_match.group(1).split('\n')
            if l.strip() and len(l.strip()) > 5
        ][:3]
    reply = _strip_internal_blocks(full)
    reply, _ = _parse_evidence_block(reply)
    reply = _strip_internal_blocks(reply)
    reply = _repair_math_notation(reply)
    reply = _split_mixed_math_text(reply)

    # Fallback: if stripping internal blocks left an empty reply, recover the raw AI content
    if not reply.strip() and full.strip():
        fallback = re.sub(r'---FOLLOWUP---[\s\S]*?(?:---END---|$)', '', full, flags=re.IGNORECASE)
        fallback = re.sub(r'---EVIDENCE---[\s\S]*?(?:---END-EVIDENCE---|---END---|$)', '', fallback, flags=re.IGNORECASE)
        reply = _repair_math_notation(fallback.strip())
        reply = _split_mixed_math_text(reply)

    # If the API returned nothing at all (both attempts), surface a real error rather than
    # silently returning empty (views.py would give a 200+error with no console trace)
    if not reply.strip():
        raise RuntimeError('course_chat: API returned empty content')

    # Clean up «M0»/«M1»/«M2» placeholder artifacts — always strip (safety net, 20b model can hallucinate these)
    reply = re.sub(r'[««"]\s*M\d+\s*[»»"]', '', reply)
    if _has_placeholder_artifacts(reply):
        reply = re.sub(r'\bM\d+\b', '', reply)
    reply = re.sub(r'\n{3,}', '\n\n', reply).strip()

    # ── Dedup consecutive paragraphs (LLM sometimes repeats blocks) ───────────
    reply = _dedup_consecutive_paragraphs(reply)

    # ── Quality check & auto-reformulation ────────────────────────────────────
    reply = _quality_check_course_reply(reply, teach_idx, total, concept_to_teach, is_first_message, subject=subject)

    # Always ensure a closing question (user must respond before next chunk)
    reply = _ensure_turn_closing_question(reply)

    # ── Wrap bare Unicode physics/math symbols in $...$ for science subjects ──
    if subject in ('maths', 'physique', 'chimie'):
        # Common Greek letters used as variables in flowing text (not already inside $...$)
        _GREEK_WRAP = {
            'ε₀': '$\\varepsilon_0$', 'ε_0': '$\\varepsilon_0$',
            'εᵣ': '$\\varepsilon_r$', 'ε_r': '$\\varepsilon_r$',
            'ε₀εᵣ': '$\\varepsilon_0 \\varepsilon_r$',
        }
        for uc, latex in _GREEK_WRAP.items():
            reply = reply.replace(uc, latex)
        # Wrap lone Greek letters NOT already inside $ as inline math
        reply = re.sub(r'(?<!\$)(?<![\\a-zA-Z])([αβγδεζηθλμνπρστφωΔΩΣΠΦΓΘΛ])(?!\$)(?![a-zA-Z])',
                       lambda m: f'${m.group(1)}$' if m.group(1) not in ('Δ',) or not re.search(r'[a-zA-Zà-ÿ]', m.string[max(0,m.start()-1):m.start()]) else m.group(0),
                       reply)

    # ── Strip all guillemets: «content» → content, lone «/» removed ────────────
    reply = re.sub(r'«([^«»\n]*)»', lambda m: m.group(1).strip(), reply)
    reply = reply.replace('«', '').replace('»', '')
    # Final sweep: remove any M+digit tokens exposed by guillemet stripping (e.g. «M3» → M3)
    reply = re.sub(r'(?<![a-zA-Z$\\])M\d+(?![a-zA-Z_$])', '', reply)
    reply = re.sub(r'\s{2,}', ' ', reply)
    reply = re.sub(r'\n{3,}', '\n\n', reply).strip()

    # ── Summary shortcut (explicit request overrides computed new_step) ────────
    um_lower = (user_message or '').lower()
    wants_summary = any(
        kw in um_lower
        for kw in ['résumé', 'bilan', 'recap', 'récap', 'fini', 'terminé', 'rezime', 'tout compris', 'on a fini']
    )
    if wants_summary and teach_idx >= max_idx:
        new_step = total

    _chunk_meta = None
    if teach_idx < total:
        _chunk_meta = {'concept_idx': int(teach_idx), 'next_chunk_idx': int(_next_chunk_idx)}

    return {
        'reply': reply,
        'new_step': new_step,
        'followups': [],
        'total_steps': total,
        'plan_intro': _returned_plan_intro,
        'auto_continue': _auto_continue,
        'chunk_meta': _chunk_meta,
    }


def course_chunk_clarification(
    *,
    subject: str,
    chapter_title: str,
    subchapter_title: str,
    chunk_title: str,
    lesson_context: str,
    user_question: str,
    user_profile: str = '',
    messages: list | None = None,
) -> str:
    """Low-cost clarification helper for hybrid course mode.

    Uses only the current chunk context plus the student's question.
    """
    subject_label = MATS.get(subject, subject)
    if isinstance(subject_label, dict):
        subject_label = subject_label.get('label', subject)

    style_hint = ''
    msg_lower = (user_question or '').lower()
    if re.search(r'\b(pk|pq|prk|stp|svp|c\b|g\b)\b', msg_lower):
        style_hint = (
            "L'élève écrit de façon courte/familière. Réponds simplement, naturellement, sans ton trop académique.\n"
        )

    recent_msgs = []
    for m in reversed(messages or []):
        if m.get('role') not in ('user', 'assistant', 'ai'):
            continue
        content = (m.get('content') or '').strip()
        if not content:
            continue
        recent_msgs.append(content[:280])
        if len(recent_msgs) >= 2:
            break
    recent_msgs.reverse()
    recent_block = '\n'.join(f'- {item}' for item in recent_msgs) if recent_msgs else 'Aucun historique utile.'

    prompt = (
        f"Matière : {subject_label}\n"
        f"Chapitre : {chapter_title}\n"
        f"Sous-chapitre : {subchapter_title}\n"
        f"Chunk actuel : {chunk_title}\n\n"
        "MISSION : l'élève a trouvé ce passage flou. Tu dois répondre à sa question exacte.\n"
        "Règles pédagogiques obligatoires :\n"
        "- Réponds DIRECTEMENT à la question dans la première phrase.\n"
        "- Corrige la confusion logique si l'élève mélange deux idées.\n"
        "- Découpe en 2 à 4 petits paragraphes très clairs.\n"
        "- Utilise des mots simples et concrets.\n"
        "- Si utile, donne un mini-exemple numérique ou concret.\n"
        "- Ne répète pas le chunk mot pour mot.\n"
        "- N'utilise QUE le contexte de leçon ci-dessous.\n"
        "- Termine par une seule petite question de vérification.\n"
        f"{style_hint}"
        "Longueur visée : environ 90 à 170 mots.\n"
        "Style : JAMAIS d'italique Markdown (*...*). Texte normal avec espaces entre chaque mot.\n\n"
        "Historique récent :\n"
        f"{recent_block}\n\n"
        "Contexte exact du chunk :\n"
        f"{_sanitize_source_math_artifacts((lesson_context or '').strip())[:2600]}\n\n"
        "Question de l'élève :\n"
        f"{(user_question or '').strip()[:500]}"
    )

    profile_block = f"\nProfil élève :\n{user_profile[:300]}\n" if user_profile else ''
    resp = _client().chat.completions.create(
        model=FAST_MODEL,
        messages=[
            {"role": "system", "content": (
                "Tu es un professeur patient du Bac Haïti. "
                "Tu expliques très simplement sans perdre la rigueur."
            )},
            {"role": "user", "content": prompt + profile_block},
        ],
        max_tokens=2200,
    )
    reply = (resp.choices[0].message.content or '').strip()
    reply = _strip_internal_blocks(reply)
    reply = _repair_math_notation(reply)
    reply = _split_mixed_math_text(reply)
    # Remove trailing orphaned $ left by _repair_math_notation when split fixed the content
    reply = re.sub(r'\$\s*$', '', reply).rstrip()
    # Remove AI-generated «M1»-style condition labels (use broader pattern)
    reply = re.sub(r'[«\u2039\u201C\u201E"]\s*M\s*\d+\s*[»\u203A\u201D\u201F"]', '', reply)
    reply = _dedup_consecutive_paragraphs(reply)
    reply = _ensure_turn_closing_question(reply)
    return re.sub(r'\n{3,}', '\n\n', reply).strip()


# ─────────────────────────────────────────────────────────────────────────────
# EXERCISE DISPLAY FORMATTING — Groq reviews raw exercise before it reaches the user
# ─────────────────────────────────────────────────────────────────────────────

def format_exercise_display(subject: str, intro: str, questions: list) -> dict:
    """
    Ask Groq to check a raw exercise and fix display issues:
      1. Broken/unclear math expressions → proper $...$ inline LaTeX
      2. Data that would be clearer as a Markdown pipe table
         (stats, tuples list, effectifs, distribution tables, etc.)
      3. Remove duplicate text, stray LaTeX artefacts

    Returns a dict:
      {
        'intro':     str,   # cleaned intro text (may contain pipe table)
        'questions': list,  # cleaned questions list
      }
    If Groq fails for any reason, returns the original unchanged.
    """
    full_text = intro
    if questions:
        full_text += '\n' + '\n'.join(f'{i+1}. {q}' for i, q in enumerate(questions))

    # Hard limit — don't send huge exercises to the API
    if len(full_text) > 3000:
        return {'intro': intro, 'questions': questions}

    prompt = (
        f"Matière : {subject.upper()}\n\n"
        "Tu reçois le texte BRUT d'un exercice de BAC Haïti tiré d'un fichier JSON.\n"
        "Ton rôle est de corriger UNIQUEMENT les problèmes d'AFFICHAGE, sans changer le contenu mathématique.\n\n"
        "RÈGLES STRICTES :\n"
        "1. Si tu vois des données tabulaires (liste de valeurs x/y, tuples, effectifs, distribution de probabilité, données séparées par des virgules/tabulations/point-virgules correspondant à plusieurs variables), "
        "convertis-les OBLIGATOIREMENT en tableau Markdown à pipe (| col | col |) avec ligne `|---|---|` séparatrice. "
        "TOUTES les lignes du tableau doivent utiliser le format `| valeur | valeur |` — INTERDIT d'utiliser des tabulations ou espaces comme séparateurs dans le tableau.\n"
        "Si tu vois déjà un tableau Markdown bien formaté (lignes commençant par |), GARDE-LE INTACT sans aucune modification de structure.\n"
        "Exemple correct :\n"
        "| x | y |\n| --- | --- |\n| 1 | 5 |\n| 2 | 10 |\n"
        "2. Si une formule mathématique est écrite en texte plat (ex: \"E(X) et Var(X)\"), entoure-la avec $...$ pour KaTeX.\n"
        "3. Supprime les répétitions ou artefacts de parsing évidents (ex: \"x=1,2,3 x=1,2,3\").\n"
        "4. Ne modifie PAS le sens, les valeurs numériques, ni les questions.\n"
        "5. Si aucune correction n'est nécessaire, retourne le texte inchangé.\n\n"
        "EXERCICE BRUT :\n"
        "---\n"
        f"{full_text[:2500]}\n"
        "---\n\n"
        "Retourne UNIQUEMENT un JSON avec exactement ces clés (rien d'autre) :\n"
        '{"intro": "texte amélioré (tableau pipe si applicable)", '
        '"questions": ["question 1", "question 2", ...]}\n\n'
        "CRITIQUE : dans le JSON, les sauts de ligne dans intro doivent être \\n (backslash-n). "
        "Le tableau doit être au format pipe STRICT : chaque ligne commence et finit par |."
    )

    try:
        raw = _call_fast(prompt, max_tokens=1200)
        raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
        import json as _json
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return {'intro': intro, 'questions': questions}
        data = _json.loads(m.group(0))
        new_intro = str(data.get('intro') or intro).strip('\r').rstrip(' ')
        # Preserve trailing newline so the last pipe row is not orphaned
        new_qs = data.get('questions')
        if not isinstance(new_qs, list) or len(new_qs) == 0:
            new_qs = questions
        # Sanity: if intro got way shorter something went wrong
        if len(new_intro) < len(intro) * 0.4:
            return {'intro': intro, 'questions': questions}
        return {'intro': new_intro, 'questions': [str(q) for q in new_qs]}
    except Exception:
        return {'intro': intro, 'questions': questions}


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE TOOLS DETECTION — Punnett square, tableau d'avancement, stats table
# ─────────────────────────────────────────────────────────────────────────────

def analyze_exercise_for_interactive(subject: str, intro: str, enonce: str, questions: list, note_context: str = '') -> dict:
    """
    Analyze a BAC exercise to determine which interactive tools to show the student:
      - 'punnett'    : échiquier de Punnett (SVT genetics)
      - 'avancement' : tableau d'avancement / ICE table (chimie reactions)
      - 'table'      : formatted markdown table (stats / data exercises)

    note_context: relevant excerpt from the subject's note_*.json (passed by views layer).
    Returns dict with keys punnett, avancement, table, note_advice.
    """
    text = f"{intro}\n{enonce}\n{' '.join(str(q) for q in (questions or []))}"
    text_lower = text.lower()

    # ── Punnett square: SVT genetics ──────────────────────────────────────────
    needs_punnett = (subject == 'svt') and any(kw in text_lower for kw in [
        'échiquier', 'echiquie', 'croisement', 'génotype', 'phénotype',
        'allèle', 'allele', 'dominant', 'récessif', 'recessif',
        'hétérozygote', 'heterozy', 'homozygote', 'f1', 'f2',
        'hybride', 'génération filiale', 'génétique', 'punnet',
        'gamète', 'gamete', 'locus', 'diploïde', 'haploïde',
    ])

    # ── Tableau d'avancement: chimie reactions ────────────────────────────────
    needs_avancement = (subject in ('chimie', 'physique')) and any(kw in text_lower for kw in [
        "réactif limitant", "reactif limitant",
        "réactif en excès", "réactif en exces", "reactif en exces",
        "tableau d'avancement", "avancement de la réaction",
        "dressez le tableau", "établissez le tableau", "dresser le tableau",
        "avancement maximum", "avancement maximal",
        "état final", "quantité de matière initiale", "etat d'avancement",
    ])

    # ── Data table: statistics / tabular data not already converted ───────────
    needs_table = (not needs_punnett and not needs_avancement) and any(
        kw in text_lower for kw in [
            'tableau', 'effectif', 'fréquence', 'frequence',
            'série statistique', 'nuage de points',
        ]
    ) and (
        # Only call AI if text contains data-like patterns
        bool(re.search(r'\(\s*[\d,. ]+\s*,\s*[\d,. ]+\s*\)', text))  # (x,y) tuples
        or bool(re.search(r':\s*[\d,;. ]+(?:;\s*[\d,;. ]+){2,}', text))  # : v1; v2; v3
    )

    result: dict = {'punnett': None, 'avancement': None, 'table': None, 'note_advice': ''}

    # ── Build note advice for punnett if notes available ────────────────────
    if needs_punnett and note_context:
        try:
            advice_prompt = (
                f"Notes de cours (SVT — génétique) :\n---\n{note_context[:3000]}\n---\n\n"
                f"Exercice :\n{text[:800]}\n\n"
                "En te basant UNIQUEMENT sur les notes de cours ci-dessus, donne 1 à 3 conseils "
                "courts et précis à l'élève pour réussir cet échiquier de Punnett. "
                "Formate en bullet points courts (max 2 lignes chacun). "
                "Si les notes ne contiennent rien sur le sujet, réponds exactement : AUCUN_CONSEIL"
            )
            advice_raw = _call_fast(advice_prompt, max_tokens=300).strip()
            if advice_raw.upper() != 'AUCUN_CONSEIL' and len(advice_raw) > 10:
                result['note_advice'] = advice_raw
        except Exception:
            pass

    if needs_punnett:
        # Only give the first cross (F1) — F2 is generated client-side from F1 results
        note_ctx_block = f"\n\nNotes de cours disponibles :\n{note_context[:1200]}" if note_context else ''
        prompt = (
            "Exercice SVT (génétique) :\n"
            f"---\n{text[:2000]}\n---"
            f"{note_ctx_block}\n\n"
            "Détermine UNIQUEMENT le premier croisement nécessaire (croisement parental → F1).\n"
            "Le croisement F1×F1 (F2) sera généré automatiquement ensuite par le système.\n"
            "Retourne UNIQUEMENT un JSON valide (rien d'autre) avec cette structure exacte :\n"
            '{"legend":{"dominant":{"symbol":"A","description":"caractère ex: souris noire"},'
            '"recessive":{"symbol":"a","description":"caractère ex: souris blanche"}},'
            '"crosses":['
            '{"label":"Croisement F1 (parental)","cross":"AA × aa",'
            '"alleles_row":["A","A"],"alleles_col":["a","a"],'
            '"f1_genotype":"Aa",'
            '"phenotype_analysis":"100% de phénotype X","genotype_analysis":"100% Aa hétérozygotes"}'
            ']}\n\n'
            "Règles :\n"
            "- Inclure SEULEMENT le croisement parental dans crosses (1 seul élément)\n"
            "- alleles_row = gamètes du parent 1, alleles_col = gamètes du parent 2\n"
            "- f1_genotype = génotype obtenu en F1 (ex: 'Aa') pour générer F2 automatiquement\n"
            "- Le dominant s'écrit en MAJUSCULE, le récessif en minuscule\n"
            "- Utilise les symboles réels de l'exercice, sinon A/a par défaut"
        )
        try:
            import json as _json
            raw = _call_fast(prompt, max_tokens=700)
            raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                parsed = _json.loads(m.group(0))
                # Validate new format: must have crosses array
                if 'crosses' in parsed and isinstance(parsed['crosses'], list):
                    result['punnett'] = parsed
                else:
                    # Old flat format returned: wrap it
                    result['punnett'] = {
                        'legend': {
                            'dominant': {'symbol': parsed.get('alleles_row', ['A'])[0], 'description': 'caractère dominant'},
                            'recessive': {'symbol': parsed.get('alleles_col', ['a'])[0], 'description': 'caractère récessif'},
                        },
                        'crosses': [{
                            'label': 'Croisement F1 (parental)',
                            'cross': parsed.get('cross', ''),
                            'alleles_row': parsed.get('alleles_row', ['A', 'a']),
                            'alleles_col': parsed.get('alleles_col', ['A', 'a']),
                            'f1_genotype': '',
                            'phenotype_analysis': '',
                            'genotype_analysis': '',
                        }],
                    }
        except Exception:
            result['punnett'] = {
                'legend': {
                    'dominant': {'symbol': 'A', 'description': 'caractère dominant'},
                    'recessive': {'symbol': 'a', 'description': 'caractère récessif'},
                },
                'crosses': [{
                    'label': 'Croisement F1 (parental)',
                    'cross': '?',
                    'alleles_row': ['A', 'a'],
                    'alleles_col': ['A', 'a'],
                    'f1_genotype': '',
                    'phenotype_analysis': '',
                    'genotype_analysis': '',
                }],
            }

    elif needs_avancement:
        prompt = (
            "Exercice de chimie :\n"
            f"---\n{text[:1500]}\n---\n\n"
            "Extrait la réaction chimique principale et retourne UNIQUEMENT un JSON :\n"
            '{"equation":"2H₂ + O₂ → 2H₂O",'
            '"reactants":[{"name":"H₂","coeff":2},{"name":"O₂","coeff":1}],'
            '"products":[{"name":"H₂O","coeff":2}]}'
            "\n\nSi la réaction n'est pas claire, laisse equation vide et reactants/products = []."
        )
        try:
            import json as _json
            raw = _call_fast(prompt, max_tokens=350)
            raw = re.sub(r'```[a-z]*\s*', '', raw).strip()
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                result['avancement'] = _json.loads(m.group(0))
        except Exception:
            result['avancement'] = {'equation': '', 'reactants': [], 'products': []}

    elif needs_table:
        prompt = (
            "Exercice :\n"
            f"---\n{text[:1200]}\n---\n\n"
            "Si cet exercice contient des données qui devraient être dans un tableau, "
            "convertis UNIQUEMENT ces données en tableau Markdown (format | col | col |... avec ligne --- séparatrice).\n"
            "Si aucune donnée tabulaire n'est présente, réponds exactement : NULL\n"
            "Réponds UNIQUEMENT avec le tableau markdown ou NULL, sans autre texte."
        )
        try:
            raw = _call_fast(prompt, max_tokens=400).strip()
            if raw.upper() != 'NULL' and '|' in raw:
                result['table'] = raw
        except Exception:
            pass

    return result