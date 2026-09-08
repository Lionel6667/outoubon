# -*- coding: utf-8 -*-
"""Build comprehensive per-page audit for ChatGPT."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "AUDIT_COMPLET_PAR_PAGE.md"

PAGES = []

def page(title, **kwargs):
    PAGES.append((title, kwargs))

# --- GLOBAL ---
page("COMPOSANTS GLOBAUX (base.html + sidebar)", section="global",
     url="Toutes pages /dashboard/*",
     template="templates/base.html + templates/core/_sidebar.html",
     visual="Dark mode slate. Sidebar 240px gauche fixe. Zone main scrollable. Topbar mobile avec hamburger. Variables CSS: --green #10B981, fond #020617, cartes #1e293b. Font Inter. MathJax pour formules.",
     sections=[
         "HEAD: CSRF meta, auth persistence (localStorage otb token + cookie), MathJax 3, Font Awesome",
         "Mobile topbar: logo OTB, titre page, menu hamburger → ouvre sidebar",
         "Sidebar brand: logo + OU TOU BON",
         "Sidebar user: avatar (image ou initiale), nom, badge PRO ou DÉMO, niveau/ligue",
         "Nav Principal: Accueil, Chat IA, Quiz, Extra bèt, Exercices, Examen Blanc, Cours",
         "Nav Outils: Fiches, Plan, Favoris, Bibliothèque, Messages (badge unread)",
         "Nav Suivi: Progression, Profil, Abonnement (couronne dorée)",
         "Footer sidebar: toggle Anglais/Espagnol (POST /dashboard/api/set-language/), WhatsApp Assistance, timer session, Déconnexion ou Créer compte (guest)",
     ],
     buttons=[
         ("Bouton", "Anglais 🇬🇧", "lang-btn-en", "setForeignLang('anglais') → reload", "POST /dashboard/api/set-language/", "0"),
         ("Bouton", "Espagnol 🇪🇸", "lang-btn-es", "setForeignLang('espagnol')", "idem", "0"),
         ("Lien", "Assistance", "—", "Ouvre WhatsApp wa.me/50936200585", "externe", "0"),
         ("Lien", "Chaque nav-item", "nav-item", "Navigation page", "GET page", "0"),
         ("Lien", "Déconnexion", "logout-btn", "/logout/", "GET", "0"),
         ("Lien", "Créer compte (guest)", "—", "/signup/", "GET", "0"),
         ("Lien", "Quitter démo", "—", "/dashboard/guest/stop/", "GET", "0"),
     ],
     apis=[],
     limits="Timer session: ping /dashboard/api/study/ping/ périodique (0 tokens)",
     flow="User ouvre app → sidebar toujours visible desktop → active_page highlight vert → mobile overlay sidebar")

page("LANDING — Page marketing", section="public",
     url="https://outoubon.com/ — route name: landing",
     template="templates/landing.html + static/css/bac_landing.css",
     visual="Page standalone plein écran, fond sombre étoilé, hero split gauche texte / droite mockup chat. Navbar sticky transparente. Sections alternées avec padding 70-80px. Pricing cards glow cyan/or. Pas de sidebar.",
     sections=[
         "NAVBAR: logo, liens ancres #home #subjects #pricing, bouton doré Gagner de l'argent",
         "HERO #home: titre BAC NS4, sous-titre, avatars social proof, 2 CTAs",
         "HERO CARD droite: faux chat IA + barre progression",
         "DASHBOARD preview: 3 stat cards (Chat 24/7, Examen blanc, Cours+Quiz)",
         "DREAM SECTION: 4 cartes objectifs élève",
         "AGENT SECTION: programme parrainage 150G/élève, flow 3 steps, CTA modal",
         "LEAGUE/RANKING section: ligues 30 niveaux, XP",
         "SUBJECTS #subjects: grille matières BAC",
         "PRICING #pricing: plans mensuel/annuel inline (duplique /pricing)",
         "FOOTER: liens login signup",
     ],
     buttons=[
         ("CTA", "Commencer maintenant", "btn-primary", "→ /signup/", "GET", "0"),
         ("CTA", "Explorer sans compte", "btn-demo", "→ /dashboard/guest/start/", "GET", "0"),
         ("Nav", "Gagner de l'argent", "btn-agent", "openAgentModal()", "—", "0"),
         ("Modal agent", "S'inscrire / Se connecter toggle", "agentRegisterBtn", "POST /agent/register/ ou /agent/login/", "JSON", "0"),
         ("Modal agent", "Créer mon compte agent", "submit", "agentFormSubmit", "JSON", "0"),
         ("Modal", "Fermer ×", "agent-modal-close", "closeAgentModal()", "—", "0"),
         ("Pricing", "Choisir plan", "—", "→ /create-payment/ ou signup", "POST", "0"),
     ],
     apis=["POST /agent/login/", "POST /agent/register/"],
     limits="Aucune IA sur landing",
     flow="1 Visiteur arrive 2 Lit hero 3 Soit signup soit guest_start 4 Ou ouvre modal agent")

page("LOGIN", section="public",
     url="/login/ — login_view",
     template="templates/accounts/login.html",
     visual="Centré, carte auth 440px max, gradient vert/bleu en arrière-plan, logo 48px, titre Bon retour 👋",
     sections=["Logo OTB", "Toggle Email / Téléphone", "Champ identifier", "Mot de passe + œil", "Erreur rouge si échec", "Footer lien signup"],
     buttons=[
         ("Toggle", "Email", "lBtnEmail", "switchLogin('email')", "—", "0"),
         ("Toggle", "Téléphone", "lBtnPhone", "switchLogin('phone') +509 auto", "—", "0"),
         ("Toggle pwd", "Œil", "eyeBtn", "togglePwd()", "—", "0"),
         ("Submit", "Se connecter", "loginForm POST", "identifier + password", "POST /login/", "0"),
     ],
     apis=["POST /api/auth/token/verify/ si token localStorage"],
     limits="0 tokens. Erreurs: compte introuvable vs mot de passe incorrect",
     flow="1 GET form 2 POST 3 redirect dashboard ou ?next= 4 Cookie otb_persistent_token 1 an")

page("SIGNUP ÉTAPE 1", section="public",
     url="/signup/",
     template="accounts/signup.html",
     visual="Carte auth, indicateur étapes 1→2→3, champs nom email téléphone",
     sections=["Step indicator", "Prénom Nom", "Email OU téléphone toggle", "Password ×2", "Lien login"],
     buttons=[("Submit", "Continuer", "—", "→ signup_step2", "POST", "0")],
     apis=[],
     limits="0",
     flow="POST valide → redirect step2")

page("SIGNUP ÉTAPE 2", section="public",
     url="/signup/step2/",
     template="accounts/signup_step2.html",
     visual="Carte auth, autocomplete école, 4 cartes radio série avec emoji",
     sections=["École autocomplete", "Série SVT SMP SES LLA", "Langue Anglais/Espagnol", "Objectif BAC optionnel"],
     buttons=[("Submit", "Continuer vers diagnostic", "—", "POST", "POST", "0"), ("Back", "← signup", "—", "GET", "0")],
     apis=["GET /schools/?q= autocomplete"],
     limits="0",
     flow="→ /diagnostic/")

page("DIAGNOSTIC ONBOARDING", section="public",
     url="/diagnostic/ + /diagnostic/generate/",
     template="accounts/diagnostic.html",
     visual="Fullscreen quiz onboarding, barre progression matières, bannière honnêteté",
     sections=["État génération loader", "Quiz multi-matières QCM", "POST final compte"],
     buttons=[("—", "Réponses QCM", "radio", "POST diagnostic", "POST", "⚠️ si IA génère ~1400 tok")],
     apis=["GET/POST /diagnostic/generate/"],
     limits="Génération peut appeler IA si pas JSON",
     flow="1 generate 2 quiz honnête 3 création compte final")

page("DASHBOARD ACCUEIL", section="app",
     url="/dashboard/",
     template="core/dashboard.html (~1255 lignes)",
     visual="Grille stats 4 cartes colorées top border (quiz violet, streak orange, BAC cyan, exos bleu). Section Coach IA avec skeleton shimmer puis cartes. 4 action cards grandes. 2 colonnes: barres matières + ligue leaderboard.",
     sections=[
         "Modal école (si school_missing): input autocomplete + Confirmer",
         "Bannière guest jaune: S'inscrire",
         "Stats: quiz complétés, streak jours, note BAC /1900 + barre milestone, exercices résolus + palier",
         "Coach IA: badge mistakes à réviser, grille coachingCards AJAX",
         "Lien Plan de révision hebdo",
         "Action cards: Chat, Quiz, Exercices, Examen blanc",
         "Colonne gauche: niveau par matière (barres % quiz)",
         "Colonne droite: ligue nom, rang #, XP barre, récompense Top1 Premium, liste classement 🥇🥈🥉",
         "Modal guest série (SVT/SMP/SES/LLA) si pending",
         "Celebration streak confetti si streak_just_earned",
     ],
     buttons=[
         ("Modal", "Confirmer l'école", "saveSchoolBtn", "POST api_save_school", "/dashboard/api/save-school/", "0"),
         ("CTA guest", "S'inscrire", "—", "/signup/", "0", "0"),
         ("Action", "Demander à l'IA", "action-card", "/dashboard/chat/", "0", "0"),
         ("Action", "Lancer Quiz", "—", "/dashboard/quiz/", "0", "0"),
         ("Action", "Exercices", "—", "/dashboard/exercices/", "0", "0"),
         ("Action", "Examen Blanc", "—", "/dashboard/examen-blanc/", "0", "0"),
         ("Coach cards", "Dynamiques", "coaching-card", "lien action_url", "GET", "⚠️ load ~3000 tok si smart coach"),
     ],
     apis=[
         "GET /dashboard/api/coaching/cards/ — ~3000 tok OUT (smart coach JSON)",
         "GET /dashboard/api/mistakes/summary/ — 0 tok (DB)",
         "POST /dashboard/api/save-school/ — 0",
         "GET /schools/?q= — 0",
     ],
     limits="Coach cards = gros poste IA au chargement page. Free: idem 50 API/jour si coach appelle IA",
     flow="1 Load page 2 AJAX coaching cards 3 User clique action card → feature")

page("CHAT IA", section="app",
     url="/dashboard/chat/",
     template="core/chat.html (~1624 lignes)",
     visual="Plein écran type ChatGPT. Fond gradient vert/cyan sombre. Pills matières horizontales scroll. Bulles: IA gris slate border-radius asymétrique, user bleu gradient. Input bar sticky bottom avec paperclip + send vert gradient. Panneau historique slide droite 320px.",
     sections=[
         "Topbar: Nouveau, Historique (clock icon)",
         "Hist panel: liste sessions, lien historique complet",
         "Subj pills: toutes matières mats.items — OBLIGATOIRE avant send",
         "Hint vert si pas de matière choisie",
         "Welcome: BIENVENUE + nom, ou APERÇU guest",
         "Guest: demo QA tabs matières + CTA signup, input bloqué overlay",
         "Msgs area scroll, typewriter IA, MathJax, copy/regen sur hover IA",
         "Input: textarea auto-resize, images + PDF (pdf.js extract 8000 chars), send",
         "Preload: pdf_path, pdf_text, preload_message, preload_session depuis autres pages",
     ],
     buttons=[
         ("Topbar", "Nouveau", "—", "newConversation() reset", "0", "0"),
         ("Topbar", "Historique", "histBtn", "toggleHistory()", "0", "0"),
         ("Pill", "Chaque matière", "subj-pill", "setSubject(key)", "0", "0"),
         ("Input", "Paperclip", "fileInput", "image/pdf attach", "0", "0"),
         ("Input", "Send", "sendBtn", "sendMessage()", "POST /dashboard/api/chat/", "1 appel ~900-2000 tok OUT + RAG input"),
         ("Msg IA", "Copier", "msg-act-btn", "copyMsg", "0", "0"),
         ("Msg IA", "Régénérer", "—", "regenMsgChat", "POST api/chat/", "+1 appel"),
         ("Followup", "Chips", "fup-btn", "useSuggestion → send", "idem", "idem"),
     ],
     apis=[
         "POST /dashboard/api/chat/ — FAST model, max ~2000 out, RAG 4500 chars, history 6 msgs",
         "GET /dashboard/api/chat/session/?session_key= — 0",
         "Background memory extract every 4 msgs — 400 tok (hidden)",
     ],
     limits="Free: 2 chats/jour + 50 API. Guest: input bloqué, demo QA statique. Local: fillers bonjour/merci = 0 API mais compte chat quota",
     flow="1 Choisir matière 2 Taper question 3 Typing dots 4 Réponse typewriter 5 Session_key saved")

page("QUIZ", section="app",
     url="/dashboard/quiz/",
     template="core/quiz.html (~839 lignes)",
     visual="Picker central max-width. Tabs Solo vert / Duel gris. Grille mat-tile colorées par matière. Arena: timer 30s, barre progression, carte question, 4 options MCQ, feedback vert/rouge.",
     sections=[
         "Tab Solo / Défier ami",
         "Solo: grille matières, SVT → sous-picker Bio/Géo/Toutes",
         "Duel: create grille OU join code 6 hex",
         "Duel waiting: code gros monospace, copy, poll state",
         "Loading: astra-loader dots",
         "Arena: scoreboard duel VS, qNum/qTotal, timer, bookmark, next",
         "Results: score %, analyse bouton, CTA guest signup",
         "Modal guest upgrade duel premium",
     ],
     buttons=[
         ("Tab", "Solo", "tabSolo", "setQuizMode('solo')", "0", "0"),
         ("Tab", "Défier ami", "tabDuel", "setQuizMode('duel')", "0", "0"),
         ("Tile", "Matière", "mat-tile", "startQuiz(subject) ou pickSVT", "GET api/quiz/questions/", "0 si JSON"),
         ("SVT", "Biologie/Géologie/Toutes", "—", "startQuiz avec discipline", "idem", "0"),
         ("Duel", "Créer", "—", "createDuel(subject)", "POST api/duel/create/", "0"),
         ("Duel", "Rejoindre", "—", "joinDuel()", "POST api/duel/join/", "0"),
         ("Play", "Option MCQ", "—", "select answer", "0", "0"),
         ("Play", "Sauvegarder", "bookmarkBtn", "toggleBookmark", "POST api/bookmark/", "0"),
         ("Play", "Question suivante", "nextBtn", "nextQuestion()", "0", "0"),
         ("Results", "Analyse erreurs", "—", "api/quiz/analyse/", "POST", "~1000 tok"),
         ("Results", "Sauver score", "—", "api/quiz/save/", "POST", "0"),
     ],
     apis=["GET /dashboard/api/quiz/questions/", "POST api/quiz/save/", "POST api/quiz/analyse/", "api/duel/*"],
     limits="Free: 1 quiz/jour. Guest: 1 quiz/matière session. Questions = JSON database/quiz_*.json = 0 API load",
     flow="Pick matière → load 10 Q → timer → feedback → score → optional analyse IA")

page("EXERCICES GUIDÉS", section="app",
     url="/dashboard/exercices/",
     template="core/exercices.html (~2018 lignes)",
     visual="3 steps: grille matières → grille chapitres → split exercice card + chat Astra. Outils interactifs Punnett tables, inputs scientifiques. Input bar fixe bottom comme chat.",
     sections=[
         "Step1 #stepSubject: grille matières",
         "Step2 #stepChapter: tuiles chapitre + aléatoire",
         "Step3: badge EXERCICE, bouton Nouvel exercice top-right, titre, texte, meta tags",
         "Chat thread exo-msgs avec avatar Astra",
         "Toolbar: Analyser, Corriger, Expliquer type, Similaire",
         "Interactive tools: punnett grid, legend, analysis rows",
         "Footer: Nouvel exercice, Terminer session",
         "Input bar: textarea + send (api exercices/chat)",
     ],
     buttons=[
         ("Step1", "Matière tile", "—", "selectSubject", "0", "0"),
         ("Step2", "Chapitre", "—", "selectChapter", "0", "0"),
         ("Step2", "Chapitre aléatoire", "—", "random chapter", "0", "0"),
         ("Card", "Nouvel exercice", "exo-gen-btn", "api/exercices/get/", "POST", "1200-3200 tok + QC"),
         ("Tool", "Analyser", "—", "api/exercices/analyze/", "POST", "variable Pro"),
         ("Tool", "Corriger", "—", "api/exercices/correct/", "POST", "~2000 tok"),
         ("Tool", "Expliquer", "—", "api/exercices/teach/", "POST", "variable"),
         ("Tool", "Similaire", "—", "api/exercices/similar/", "POST", "~800 fast"),
         ("Chat", "Send", "exo-send-btn", "api/exercices/chat/", "POST", "180-400 tok/msg"),
         ("Finish", "Terminer", "exo-finish-btn", "api/exercices/complete/", "POST", "0 bilan local"),
     ],
     apis=["api/exercices/get|analyze|correct|teach|similar|chat|complete"],
     limits="Free: 1 exercice/jour total. Guest: limité",
     flow="Matière → chapitre → load exo → chat tutor → correct/analyze → finish")

page("EXAMEN BLANC", section="app",
     url="/dashboard/examen-blanc/",
     template="core/examen_blanc.html (~2674 lignes)",
     visual="Style feuille ministère: header bleu gradient seal MENFP, timer 3h monospace vert→jaune→rouge, parties I II III, questions types officiels. Mode zen toggle. Barre progression réponses.",
     sections=[
         "Picker matière grille",
         "Loading génération IA spinner",
         "exam-sheet: header ministry, timer bar, rules",
         "Parts avec pts badges",
         "Fill blank inputs dashed",
         "Matching grid selects",
         "MCQ options",
         "Open textareas + model answer reveal",
         "Passage blocks philo/français",
         "Action bar: Soumettre, Correction IA, Voir corrigé, Mode zen",
         "Results breakdown score par section",
     ],
     buttons=[
         ("Picker", "Matière", "—", "generateExam", "POST generate-v2/", "1-3 appels 2200-3500+ tok"),
         ("Timer", "Mode zen", "—", "toggle UI", "0", "0"),
         ("Per Q", "Correction IA", "—", "api/exam/ai-correct/", "POST", "variable"),
         ("Submit", "Soumettre", "—", "score local + save", "0", "0"),
         ("Flip", "Voir corrigé", "—", "toggle model answers", "0", "0"),
     ],
     apis=["POST /dashboard/api/examen-blanc/generate-v2/", "api/exam/ai-correct/", "cache GeneratedExam = 0"],
     limits="Guest: 1 exam total. LE PLUS COÛTEUX. ENABLE_EXAM_AI_ENHANCE=false par défaut",
     flow="Pick matière → generate (wait) → remplir 3h timer → submit → résultats")

page("EXTRA BÈT (Q&A communauté)", section="app",
     url="/dashboard/extra-bet/",
     template="core/extra_bet.html (~826 lignes)",
     visual="Feed social Q&A. Topbar titre + bouton vert Publier. Modal création overlay blur. Cartes posts avec badges matière/type. Sidebar leaderboard top créateurs. Filtres matière GET + tri Recent/Likes/Unanswered.",
     sections=["Topbar Publier", "Modal: matière, type direct/fill/qcm, toolbar symboles maths, preview, options A-D, publier", "Top créateurs leaderboard", "Filtre matière dropdown", "Tri sort links", "Feed cards: like, répondre, aide IA, delete owner", "Réponses thread expand"],
     buttons=[
         ("Topbar", "Publier", "openEbModal", "ouvre modal", "0", "0"),
         ("Modal", "Fermer", "closeEbModal", "ferme", "0", "0"),
         ("Modal", "Insérer blanc ___", "ebBlankBtn", "insert blank", "0", "0"),
         ("Modal", "Symboles toolbar", "ebToolbar", "insert math chars", "0", "0"),
         ("Modal", "Publier", "ebPublishBtn", "publishExtraBet()", "POST api/extra-bet/create/", "0 vérif IA possible"),
         ("Card", "Like", "eb-like-btn", "api/extra-bet/like/", "POST", "0"),
         ("Card", "Répondre", "—", "api/extra-bet/answer/", "POST", "0"),
         ("Card", "Aide IA", "—", "api/extra-bet/ai-help/", "POST", "1 chat ~900 tok"),
         ("Card", "Supprimer", "eb-delete-btn", "api/extra-bet/delete/", "POST", "0"),
         ("Filter", "Tri recent/likes/unanswered", "eb-sort-btn", "GET reload", "0", "0"),
     ],
     apis=["api/extra-bet/create|answer|like|delete|ai-help"],
     limits="Free: 3 réponses/jour. Publish = 0 API usually",
     flow="Browse feed → like/answer → ou publier question → IA vérifie avant post")

page("COURS HUB", section="app",
     url="/dashboard/cours/",
     template="core/cours.html (~473 lignes)",
     visual="Grille cartes launch 280px min, chaque matière couleur propre (physique bleu, SVT vert, kreyòl rose…). Chaque carte: icône 44px, titre, description, pills topics, CTA Commencer gradient, bouton chevron drawer chapitres avec rings progression %.",
     sections=["Sections groupées (sciences, langues…)", "13 matières avec card-* class", "Drawer chapitres expandable ch-num ring progress", "Locked chapters 🔒 guest/free ch>1"],
     buttons=[
         ("CTA", "Commencer", "cours-launch-cta", "→ cours/<subject>/", "GET", "0"),
         ("Toggle", "Chapitres chevron", "cours-launch-cta-ai", "toggle drawer", "0", "0"),
         ("Link", "Chapitre N", "chapter-link", "chapter_cours si unlocked", "GET", "0"),
     ],
     apis=["Progress rings from DB hybrid-progress — 0"],
     limits="Free: chapitre 1 seulement/matière. Guest: ch1 démo",
     flow="Hub → pick matière → cours page ou chapitre direct")

page("COURS PHYSIQUE", section="app",
     url="/dashboard/cours/physique/",
     template="core/physique.html (large interactive)",
     visual="Hero matière, grille chapitres numérotés, lecteur sections avec mini-quiz embed, sidebar chat IA cours, lien exercices physique par section.",
     sections=["Back to cours", "Chapter grid progress", "Section reader", "Mini-quiz inline", "Course chat panel", "Reset progress", "Link exercices list"],
     buttons=[
         ("Nav", "Retour cours", "—", "/dashboard/cours/", "0", "0"),
         ("Chapter", "Chapitre tile", "—", "load section", "GET/POST api/physique/section/", "~1400 tok si génère"),
         ("Quiz", "Mini-quiz", "—", "api/physique/miniquiz/", "POST", "~850 tok"),
         ("Chat", "Question cours", "—", "api/cours/chat/", "POST", "2200-2800 tok"),
         ("Reset", "Reset progress", "—", "api/cours/reset/", "POST", "0"),
         ("Exos", "Liste exercices", "—", "physique/exercices/<section>/", "GET", "0"),
     ],
     apis=["api/cours/physique/section|miniquiz|exercises|progress", "api/cours/chat", "api/course_hybrid_progress"],
     limits="Chat cours = très coûteux. Contenu pré-généré JSON = 0",
     flow="Chapitre → sections → mini-quiz → chat aide")

page("COURS SC. SOCIALES", section="app",
     url="/dashboard/cours/sc-social/",
     template="core/sc_social.html",
     visual="Similaire physique mais contenu histoire/géo/économie intégré, corrections interactives.",
     sections=["Chapter grid", "Interactive corrections", "Progress save"],
     buttons=[
         ("Section", "Load", "—", "api/cours/sc-social/", "POST", "variable"),
         ("Correct", "Correction", "—", "api/sc-social/correct/", "POST", "variable"),
         ("Progress", "Save", "—", "api/sc-social/progress/", "POST", "0"),
     ],
     apis=["api/cours/sc-social/progress|correct"],
     limits="idem cours",
     flow="Lecture + exercices intégrés")

page("COURS GÉNÉRIQUE (Math, SVT, Kreyòl, Chimie, etc.)", section="app",
     url="/dashboard/cours/math/ | svt/ | kreyol/ | chimie/ | anglais/ | economie/ | histoire/ | informatique/ | art/ | espagnol/ | philosophie/",
     template="core/generic_cours.html",
     visual="Même pattern que physique mais template générique, couleurs par card-* du hub.",
     sections=["Hero", "Chapter list", "Section API generation", "Mini-quiz", "Chat sidebar"],
     buttons=[
         ("Chapter", "—", "—", "api/cours/section/", "POST", "~1400 tok"),
         ("Mini-quiz", "—", "—", "api/cours/miniquiz/", "POST", "~850"),
         ("Chat", "—", "—", "api/cours/chat/", "POST", "2200-2800"),
         ("Chapter link", "—", "—", "chapter_cours/<num>/", "GET", "0"),
     ],
     apis=["api/cours/section|miniquiz|chat|hybrid-progress"],
     limits="Chapitre 1 free",
     flow="Generic course flow")

page("CHAPITRE COURS (vue chapitre)", section="app",
     url="/dashboard/cours/<subject>/<num>/",
     template="core/chapter_cours.html",
     visual="Topbar: back, titre chapitre, badge matière, barre progression %. Lecteur sections, quiz embed, Q&A IA.",
     sections=["Topbar back + progress", "Section content", "Embedded quizzes", "AI Q&A", "Demo lock message guest ch>1"],
     buttons=[
         ("Back", "←", "—", "parent cours", "GET", "0"),
         ("Question", "Poser", "—", "api/course-question/", "POST", "~900 tok"),
         ("Summary", "Résumé", "—", "api/chapter-summary/", "POST", "~600 tok"),
     ],
     apis=["api/course-question", "api/chapter-summary", "api/cours/chat"],
     limits="Guest: ch1 only",
     flow="Read chapter → mini activities → chat")

page("PHYSIQUE EXERCICES LISTE", section="app",
     url="/dashboard/cours/physique/exercices/<section_id>/",
     template="core/physique_exercises.html",
     visual="Liste exercices par section physique, cards numérotées.",
     buttons=[("Card", "Exercice N", "—", "detail page", "GET", "0")],
     apis=["api/physique/exercises"],
     limits="0 load list",
     flow="Section → pick exercise")

page("PHYSIQUE EXERCICE DÉTAIL", section="app",
     url="/dashboard/cours/physique/exercices/<section>/<index>/",
     template="core/physique_exercise_detail.html",
     visual="Exercice plein écran avec solution steps, bouton similaire.",
     buttons=[("Similar", "Exercice similaire", "—", "similar route", "GET", "⚠️ génération")],
     apis=["generate similar"],
     limits="variable",
     flow="View → similar")

page("FICHES MÉMO", section="app",
     url="/dashboard/fiches/",
     template="core/fiches.html",
     visual="Pills filtre matière. Stats 3 compteurs. Grille flip cards 3 états (new/review/known). Boutons générer vert + réviser erreurs.",
     sections=["Subject pills", "Stats total/connus/revoir", "Generate + Review errors", "Flashcard grid flip", "Status buttons on card"],
     buttons=[
         ("Filter", "Matière pill", "—", "filter local", "0", "0"),
         ("Action", "Générer des fiches", "—", "api/fiches/generate/", "POST", "~2000 tok fast JSON"),
         ("Action", "Réviser mes erreurs", "—", "filter mistakes", "0", "0"),
         ("Card", "Flip", "—", "CSS flip", "0", "0"),
         ("Card", "Statut new/review/known", "—", "api/fiches/status/", "POST", "0"),
     ],
     apis=["api/fiches/generate/", "api/fiches/status/"],
     limits="PREMIUM ONLY — free voit premium_required",
     flow="Generate once → flip review → mark known")

page("PLAN DE RÉVISION", section="app",
     url="/dashboard/plan/",
     template="core/plan.html",
     visual="Hero countdown jours avant BAC. Faiblesses barres horizontales. Tabs semaines S1-S4. Cartes tâches jour avec liens cours/exo/quiz. Checkbox done. Barre % complétion.",
     sections=["BAC countdown", "Generate/regenerate plan", "Weakness panel", "Week tabs", "Daily task cards", "Mark done checkbox", "Progress %"],
     buttons=[
         ("Primary", "Générer mon plan", "—", "api/plan/generate/", "POST", "3500+3000 tok Pro"),
         ("Regen", "Régénérer", "—", "idem", "idem", "idem"),
         ("Task", "Lien cours/exo/quiz", "—", "navigation", "GET", "0"),
         ("Checkbox", "Marquer fait", "—", "api/plan/progress/", "POST", "0"),
     ],
     apis=["api/plan/generate/", "api/plan/progress/"],
     limits="PREMIUM ONLY",
     flow="Generate → follow weekly tasks → check done")

page("FAVORIS / BOOKMARKS", section="app",
     url="/dashboard/bookmarks/",
     template="core/bookmarks.html",
     visual="Pills filtre matière. Cartes question quiz sauvegardées avec options (correcte highlight vert), explication, date. Bouton retirer.",
     sections=["Filter pills", "Bookmark cards", "Empty state → quiz"],
     buttons=[
         ("Filter", "Matière", "—", "client filter", "0", "0"),
         ("Card", "Retirer favori", "—", "api/bookmark/", "POST toggle", "0"),
     ],
     apis=["api/bookmark/"],
     limits="PREMIUM ONLY",
     flow="View saved quiz questions from quiz page")

page("PROGRESSION", section="app",
     url="/dashboard/progression/",
     template="core/progression.html",
     visual="4 stat cards top. Chart.js line quiz scores, radar par matière, bar mastery. Bloc recommandations matières faibles avec liens quiz.",
     sections=["Stat cards streak BAC exos temps", "Line chart", "Radar chart", "Bar chart per subject", "Recommendations weak subjects", "Links chat plan"],
     buttons=[("Link", "Aller au quiz", "—", "/dashboard/quiz/", "0", "0"), ("Link", "Chat", "—", "/dashboard/chat/", "0", "0")],
     apis=["GET api/stats/ — DB only 0 tok"],
     limits="Free may be gated — check premium. Charts = 0 API",
     flow="View stats from DB aggregates")

page("MON PROFIL", section="app",
     url="/dashboard/profil/",
     template="core/profil.html",
     visual="Hero: grande avatar editable, nom, email, badge niveau, row stats XP/streak/quiz. Form sections: infos, école, série, langue, objectif BAC.",
     sections=["Avatar upload overlay", "Stats row", "Form personal", "School serie langue target", "Save button"],
     buttons=[
         ("Avatar", "Changer photo", "—", "file input", "POST api/avatar/upload/", "0"),
         ("Submit", "Enregistrer modifications", "—", "POST profil form", "POST", "0"),
     ],
     apis=["api/avatar/upload/"],
     limits="0",
     flow="Edit profile → save")

page("HISTORIQUE CHAT", section="app",
     url="/dashboard/historique/",
     template="core/historique.html",
     visual="Stats row conversations count. Search input + filtre matière. Grille cards conversation preview date matière.",
     sections=["Stats", "Search form GET", "Conversation cards", "Empty state"],
     buttons=[
         ("Search", "Rechercher", "—", "GET form", "0", "0"),
         ("Card", "Ouvrir conversation", "—", "conversation_detail", "GET", "0"),
     ],
     apis=[],
     limits="0 — pas dans sidebar nav principal, accès via chat historique",
     flow="Search → open detail")

page("DÉTAIL CONVERSATION", section="app",
     url="/dashboard/historique/<session_key>/",
     template="core/conversation_detail.html",
     visual="Replay thread messages user/IA. Bouton continuer → chat avec session preload.",
     sections=["Message replay", "Continue button", "Back link"],
     buttons=[
         ("CTA", "Continuer cette conversation", "—", "/dashboard/chat/?session=", "GET", "0"),
         ("Back", "Retour historique", "—", "/dashboard/historique/", "0", "0"),
     ],
     apis=[],
     limits="0",
     flow="Read old chat → continue in chat page")

page("BIBLIOTHÈQUE PDF", section="app",
     url="/dashboard/library/",
     template="core/library.html",
     visual="Sections par matière. Chaque PDF: icône, titre, boutons Voir/Download/Extract IA.",
     sections=["Grouped PDF list", "View new tab", "Download", "AI extract premium", "Guest gate toast"],
     buttons=[
         ("Action", "Voir PDF", "—", "api/pdf-serve/", "GET stream", "0"),
         ("Action", "Télécharger", "—", "download", "0", "0"),
         ("Action", "Extraire IA", "—", "api/pdf-extract-text/", "POST", "⚠️ premium"),
     ],
     apis=["api/pdf-serve/", "api/pdf-extract-text/"],
     limits="Guest: redirect signup. Extract = premium",
     flow="Browse exams PDF → open or send to chat")

page("MESSAGES / AMIS", section="app",
     url="/dashboard/amis/",
     template="core/amis.html (très large)",
     visual="Layout 2 colonnes: gauche liste conversations/amis/groupe OUTOUBON, droite chat panel WhatsApp-like. Header: add friend, invitations bell. Input: photo vidéo emoji quiz groupe.",
     sections=["Guest CTA only", "Left: search friends, tabs Conversations/Gérer", "Friend list", "Group OUTOUBON entry", "Chat header remove friend", "Messages bubbles reply", "Input bar media emoji", "Quiz creator panel group", "Modals: search invite profile"],
     buttons=[
         ("Header", "Ajouter ami", "—", "modal search", "POST api/amis/", "0"),
         ("Header", "Invitations", "bell", "modal", "0", "0"),
         ("Chat", "Envoyer", "—", "api/amis/send/", "POST", "0"),
         ("Chat", "Photo/Vidéo", "—", "upload", "0", "0"),
         ("Chat", "Quiz groupe", "—", "panel publish", "api/group-chat/", "0"),
         ("Group", "Send", "—", "api/group-chat/send/", "POST", "0"),
         ("Delete", "Supprimer msg", "—", "api/delete/", "POST", "0"),
     ],
     apis=["api/amis/*", "api/group-chat/*", "api/admin-message/"],
     limits="Guest: signup CTA only. Free send may be limited. 0 IA",
     flow="Social messaging — no AI cost")

page("DUEL (page standalone)", section="app",
     url="/dashboard/duel/",
     template="core/duel.html",
     visual="Duplicate quiz duel UI: tabs create/join, lobby, gameplay scoreboard.",
     buttons=[("Same as quiz duel panel", "—", "—", "api/duel/*", "0", "0")],
     apis=["api/duel/create|join|state|finish"],
     limits="0 IA — questions JSON",
     flow="Same as quiz duel tab")

page("PRICING", section="public",
     url="/pricing/",
     template="accounts/pricing.html",
     visual="Plans cards Mensuel/Annuel features list checkmarks vert. Boutons payer MonCash. Lien cadeau.",
     sections=["Plan cards", "Feature lists", "MonCash pay buttons", "Gift link", "NatCash notify"],
     buttons=[
         ("Pay", "Choisir Mensuel", "—", "POST create-payment/", "redirect MonCash", "0"),
         ("Pay", "Choisir Annuel", "—", "idem", "0", "0"),
         ("Link", "Offrir abonnement", "—", "/cadeau/", "0", "0"),
     ],
     apis=["create-payment", "payment-status", "natcash-notify"],
     limits="0",
     flow="Pick plan → MonCash → payment-success")

page("CADEAU / GIFT FLOW", section="public",
     url="/cadeau/ | /cadeau/<token>/ | merci/",
     template="gift_share.html, gift_pay.html, gift_success.html, gift_invalid.html, gift_already_used.html",
     visual="Flow lien partage → page paiement pour ami → merci. Cards student info.",
     buttons=[
         ("Generate", "Créer lien", "gift_share", "POST", "0", "0"),
         ("Pay", "Payer MonCash", "gift_pay", "create_gift_payment", "0", "0"),
         ("Copy", "Copier lien", "—", "clipboard", "0", "0"),
     ],
     apis=["gift-payment-status"],
     limits="0",
     flow="Premium user generates link → friend pays")

page("AGENT DASHBOARD", section="agent",
     url="/agent/dashboard/",
     template="agent/dashboard.html",
     visual="Sidebar agent: Overview Referrals Withdrawals. Balance badge. Referral URL + QR. Form retrait MonCash.",
     sections=["Stats balance", "Referral link copy QR", "Withdrawal form amount phone", "Tables referrals withdrawals"],
     buttons=[
         ("Copy", "Lien parrainage", "—", "clipboard", "0", "0"),
         ("Submit", "Demander retrait", "—", "form POST", "0", "0"),
         ("Nav", "Overview/Referrals/Withdrawals", "—", "tabs", "0", "0"),
     ],
     apis=["agent/api/withdrawal-status/"],
     limits="0",
     flow="Agent tracks referrals and withdraws")

page("PREMIUM WALL", section="app",
     url="(overlay sur fiches/plan/bookmarks/etc.)",
     template="core/premium_required.html",
     visual="Centré couronne dorée, nom feature, message limite, bouton Voir les plans, retour dashboard.",
     buttons=[
         ("CTA", "Voir les plans", "—", "/pricing/", "0", "0"),
         ("Back", "Retour", "—", "/dashboard/", "0", "0"),
     ],
     apis=[],
     limits="Shown when free hits premium gate",
     flow="Block → upgrade CTA")

page("PAGES ERREUR", section="system",
     url="404 / 403 / 400 / 500",
     template="404.html etc.",
     visual="Standalone dark, gros code erreur, message, lien Retour accueil.",
     buttons=[("Link", "Retour accueil", "—", "/", "0", "0")],
     apis=[],
     limits="0",
     flow="Error → home")

page("COMPLETE PROFILE", section="public",
     url="/complete-profile/",
     template="accounts/complete_profile.html",
     visual="Post-OAuth minimal form école série langue.",
     buttons=[("Submit", "C'est parti !", "—", "POST", "0", "0")],
     apis=[],
     limits="0",
     flow="OAuth users complete profile")

page("PAYMENT SUCCESS / ERROR", section="public",
     url="/payment-success/ payment_error.html",
     template="accounts/payment_success.html, payment_error.html",
     visual="Status message confirmation ou échec paiement.",
     buttons=[("Link", "Retour dashboard", "—", "/dashboard/", "0", "0")],
     apis=["payment-status poll"],
     limits="0",
     flow="After MonCash return")

# Continue building output...
lines = []
lines.append("# OU TOU BON — AUDIT COMPLET PAR PAGE (v1 NS4)\n")
lines.append("> Document exhaustif pour ChatGPT / redesign. Chaque page: URL, look, structure, **tous les boutons**, APIs, **coût tokens IA**.\n")
lines.append("---\n\n## LÉGENDE TOKENS\n")
lines.append("| Symbole | Meaning |\n|---------|--------|\n")
lines.append("| **0** | Aucun appel DeepSeek |\n")
lines.append("| **~N tok** | N tokens sortie typiques (1 appel API) |\n")
lines.append("| **⚠️** | Peut appeler IA selon contexte |\n")
lines.append("| Plafond global | **50 appels API/jour** tous users, **10** guest, **600** plateforme |\n\n")
lines.append("## TABLE DES MATIÈRES (36 sections)\n\n")
for i, (title, _) in enumerate(PAGES, 1):
    lines.append(f"{i}. {title}\n")
lines.append("\n")

for title, d in PAGES:
    lines.append(f"\n---\n\n# {title}\n\n")
    if d.get("url"):
        lines.append(f"**URL:** {d['url']}\n\n")
    if d.get("template"):
        lines.append(f"**Template:** `{d['template']}`\n\n")
    if d.get("visual"):
        lines.append(f"### À quoi ça ressemble\n{d['visual']}\n\n")
    if d.get("sections"):
        lines.append("### Structure (ordre vertical)\n")
        for i, s in enumerate(d["sections"], 1):
            lines.append(f"{i}. {s}\n")
        lines.append("\n")
    if d.get("buttons"):
        lines.append("### Tous les boutons / actions\n\n")
        lines.append("| Type | Label | ID | Action | Endpoint | Tokens |\n")
        lines.append("|------|-------|-----|--------|----------|--------|\n")
        for row in d["buttons"]:
            lines.append("| " + " | ".join(row) + " |\n")
        lines.append("\n")
    if d.get("apis"):
        lines.append("### APIs\n")
        for a in d["apis"]:
            lines.append(f"- `{a}`\n")
        lines.append("\n")
    if d.get("limits"):
        lines.append(f"### Limites & tokens\n{d['limits']}\n\n")
    if d.get("flow"):
        lines.append(f"### Flux utilisateur\n{d['flow']}\n\n")

lines.append("\n---\n\n# ANNEXE A — Classement coût IA par feature\n\n")
lines.append("| Rang | Feature | Tokens typiques | Appels | Réduction redesign |\n")
lines.append("|------|---------|-----------------|--------|-------------------|\n")
lines.append("| 1 | Examen blanc generate-v2 | 2200-3500+ ×1-3 | Pro | Cache DB, pas auto-load |\n")
lines.append("| 2 | Chat cours | 2200-2800 out | Flash | JSON pré-généré, chat collapsé |\n")
lines.append("| 3 | Chat matière | 900-2000 + RAG | Flash | Quotas visibles, fillers local |\n")
lines.append("| 4 | Plan révision | 3500+3000 | Pro | Premium only — OK |\n")
lines.append("| 5 | Dashboard coach cards | ~3000 | Flash | **Cache 24h ou stats locales** |\n")
lines.append("| 6 | Exercice get/correct | 1200-3200 | Pro | Moins boutons IA visibles |\n")
lines.append("| 7 | Fiches generate | ~2000 | Fast | Premium |\n")
lines.append("| 8 | Quiz analyse | ~1000 | Fast | Optionnel post-quiz |\n")
lines.append("| 9 | Chat exercice | 180-400/msg | Flash | Cheap — OK |\n")
lines.append("| 10 | Quiz load / Duel / Stats / Amis | **0** | — | Safe redesign total |\n\n")

lines.append("# ANNEXE B — Prompt ChatGPT pour redesign\n\n")
lines.append("```\nTu redesignes OU TOU BON (BAC NS4 Haïti, dark #10B981).\n")
lines.append("Lis AUDIT_COMPLET_PAR_PAGE.md section par section.\n")
lines.append("Règles: (1) Ne jamais ajouter appels IA au chargement page\n")
lines.append("(2) Coach dashboard → cache ou contenu statique\n")
lines.append("(3) Chaque proposition UI doit tag [0 tok] ou [+IA]\n")
lines.append("(4) Mobile-first Haïti: +509, MonCash, français/ créole\n")
lines.append("Priorité pages: Landing → Dashboard → Chat → Quiz → Cours → Examen\n```\n")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Written {len(lines)} lines, {len(PAGES)} pages to {OUT}")
