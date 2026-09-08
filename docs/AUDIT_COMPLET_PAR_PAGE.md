# OU TOU BON — AUDIT COMPLET PAR PAGE (v1 NS4)

> Document exhaustif pour ChatGPT / redesign. Chaque page: URL, look, structure, **tous les boutons**, APIs, **coût tokens IA**.

---

## LÉGENDE TOKENS

| Symbole | Meaning |
|---------|--------|

| **0** | Aucun appel DeepSeek |

| **~N tok** | N tokens sortie typiques (1 appel API) |

| **⚠️** | Peut appeler IA selon contexte |

| Plafond global | **50 appels API/jour** tous users, **10** guest, **600** plateforme |


## TABLE DES MATIÈRES (36 sections)


1. COMPOSANTS GLOBAUX (base.html + sidebar)

2. LANDING — Page marketing

3. LOGIN

4. SIGNUP ÉTAPE 1

5. SIGNUP ÉTAPE 2

6. DIAGNOSTIC ONBOARDING

7. DASHBOARD ACCUEIL

8. CHAT IA

9. QUIZ

10. EXERCICES GUIDÉS

11. EXAMEN BLANC

12. EXTRA BÈT (Q&A communauté)

13. COURS HUB

14. COURS PHYSIQUE

15. COURS SC. SOCIALES

16. COURS GÉNÉRIQUE (Math, SVT, Kreyòl, Chimie, etc.)

17. CHAPITRE COURS (vue chapitre)

18. PHYSIQUE EXERCICES LISTE

19. PHYSIQUE EXERCICE DÉTAIL

20. FICHES MÉMO

21. PLAN DE RÉVISION

22. FAVORIS / BOOKMARKS

23. PROGRESSION

24. MON PROFIL

25. HISTORIQUE CHAT

26. DÉTAIL CONVERSATION

27. BIBLIOTHÈQUE PDF

28. MESSAGES / AMIS

29. DUEL (page standalone)

30. PRICING

31. CADEAU / GIFT FLOW

32. AGENT DASHBOARD

33. PREMIUM WALL

34. PAGES ERREUR

35. COMPLETE PROFILE

36. PAYMENT SUCCESS / ERROR




---

# COMPOSANTS GLOBAUX (base.html + sidebar)


**URL:** Toutes pages /dashboard/*


**Template:** `templates/base.html + templates/core/_sidebar.html`


### À quoi ça ressemble
Dark mode slate. Sidebar 240px gauche fixe. Zone main scrollable. Topbar mobile avec hamburger. Variables CSS: --green #10B981, fond #020617, cartes #1e293b. Font Inter. MathJax pour formules.


### Structure (ordre vertical)

1. HEAD: CSRF meta, auth persistence (localStorage otb token + cookie), MathJax 3, Font Awesome

2. Mobile topbar: logo OTB, titre page, menu hamburger → ouvre sidebar

3. Sidebar brand: logo + OU TOU BON

4. Sidebar user: avatar (image ou initiale), nom, badge PRO ou DÉMO, niveau/ligue

5. Nav Principal: Accueil, Chat IA, Quiz, Extra bèt, Exercices, Examen Blanc, Cours

6. Nav Outils: Fiches, Plan, Favoris, Bibliothèque, Messages (badge unread)

7. Nav Suivi: Progression, Profil, Abonnement (couronne dorée)

8. Footer sidebar: toggle Anglais/Espagnol (POST /dashboard/api/set-language/), WhatsApp Assistance, timer session, Déconnexion ou Créer compte (guest)



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Bouton | Anglais 🇬🇧 | lang-btn-en | setForeignLang('anglais') → reload | POST /dashboard/api/set-language/ | 0 |

| Bouton | Espagnol 🇪🇸 | lang-btn-es | setForeignLang('espagnol') | idem | 0 |

| Lien | Assistance | — | Ouvre WhatsApp wa.me/50936200585 | externe | 0 |

| Lien | Chaque nav-item | nav-item | Navigation page | GET page | 0 |

| Lien | Déconnexion | logout-btn | /logout/ | GET | 0 |

| Lien | Créer compte (guest) | — | /signup/ | GET | 0 |

| Lien | Quitter démo | — | /dashboard/guest/stop/ | GET | 0 |



### Limites & tokens
Timer session: ping /dashboard/api/study/ping/ périodique (0 tokens)


### Flux utilisateur
User ouvre app → sidebar toujours visible desktop → active_page highlight vert → mobile overlay sidebar



---

# LANDING — Page marketing


**URL:** https://outoubon.com/ — route name: landing


**Template:** `templates/landing.html + static/css/bac_landing.css`


### À quoi ça ressemble
Page standalone plein écran, fond sombre étoilé, hero split gauche texte / droite mockup chat. Navbar sticky transparente. Sections alternées avec padding 70-80px. Pricing cards glow cyan/or. Pas de sidebar.


### Structure (ordre vertical)

1. NAVBAR: logo, liens ancres #home #subjects #pricing, bouton doré Gagner de l'argent

2. HERO #home: titre BAC NS4, sous-titre, avatars social proof, 2 CTAs

3. HERO CARD droite: faux chat IA + barre progression

4. DASHBOARD preview: 3 stat cards (Chat 24/7, Examen blanc, Cours+Quiz)

5. DREAM SECTION: 4 cartes objectifs élève

6. AGENT SECTION: programme parrainage 150G/élève, flow 3 steps, CTA modal

7. LEAGUE/RANKING section: ligues 30 niveaux, XP

8. SUBJECTS #subjects: grille matières BAC

9. PRICING #pricing: plans mensuel/annuel inline (duplique /pricing)

10. FOOTER: liens login signup



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| CTA | Commencer maintenant | btn-primary | → /signup/ | GET | 0 |

| CTA | Explorer sans compte | btn-demo | → /dashboard/guest/start/ | GET | 0 |

| Nav | Gagner de l'argent | btn-agent | openAgentModal() | — | 0 |

| Modal agent | S'inscrire / Se connecter toggle | agentRegisterBtn | POST /agent/register/ ou /agent/login/ | JSON | 0 |

| Modal agent | Créer mon compte agent | submit | agentFormSubmit | JSON | 0 |

| Modal | Fermer × | agent-modal-close | closeAgentModal() | — | 0 |

| Pricing | Choisir plan | — | → /create-payment/ ou signup | POST | 0 |



### APIs

- `POST /agent/login/`

- `POST /agent/register/`



### Limites & tokens
Aucune IA sur landing


### Flux utilisateur
1 Visiteur arrive 2 Lit hero 3 Soit signup soit guest_start 4 Ou ouvre modal agent



---

# LOGIN


**URL:** /login/ — login_view


**Template:** `templates/accounts/login.html`


### À quoi ça ressemble
Centré, carte auth 440px max, gradient vert/bleu en arrière-plan, logo 48px, titre Bon retour 👋


### Structure (ordre vertical)

1. Logo OTB

2. Toggle Email / Téléphone

3. Champ identifier

4. Mot de passe + œil

5. Erreur rouge si échec

6. Footer lien signup



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Toggle | Email | lBtnEmail | switchLogin('email') | — | 0 |

| Toggle | Téléphone | lBtnPhone | switchLogin('phone') +509 auto | — | 0 |

| Toggle pwd | Œil | eyeBtn | togglePwd() | — | 0 |

| Submit | Se connecter | loginForm POST | identifier + password | POST /login/ | 0 |



### APIs

- `POST /api/auth/token/verify/ si token localStorage`



### Limites & tokens
0 tokens. Erreurs: compte introuvable vs mot de passe incorrect


### Flux utilisateur
1 GET form 2 POST 3 redirect dashboard ou ?next= 4 Cookie otb_persistent_token 1 an



---

# SIGNUP ÉTAPE 1


**URL:** /signup/


**Template:** `accounts/signup.html`


### À quoi ça ressemble
Carte auth, indicateur étapes 1→2→3, champs nom email téléphone


### Structure (ordre vertical)

1. Step indicator

2. Prénom Nom

3. Email OU téléphone toggle

4. Password ×2

5. Lien login



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Submit | Continuer | — | → signup_step2 | POST | 0 |



### Limites & tokens
0


### Flux utilisateur
POST valide → redirect step2



---

# SIGNUP ÉTAPE 2


**URL:** /signup/step2/


**Template:** `accounts/signup_step2.html`


### À quoi ça ressemble
Carte auth, autocomplete école, 4 cartes radio série avec emoji


### Structure (ordre vertical)

1. École autocomplete

2. Série SVT SMP SES LLA

3. Langue Anglais/Espagnol

4. Objectif BAC optionnel



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Submit | Continuer vers diagnostic | — | POST | POST | 0 |

| Back | ← signup | — | GET | 0 |



### APIs

- `GET /schools/?q= autocomplete`



### Limites & tokens
0


### Flux utilisateur
→ /diagnostic/



---

# DIAGNOSTIC ONBOARDING


**URL:** /diagnostic/ + /diagnostic/generate/


**Template:** `accounts/diagnostic.html`


### À quoi ça ressemble
Fullscreen quiz onboarding, barre progression matières, bannière honnêteté


### Structure (ordre vertical)

1. État génération loader

2. Quiz multi-matières QCM

3. POST final compte



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| — | Réponses QCM | radio | POST diagnostic | POST | ⚠️ si IA génère ~1400 tok |



### APIs

- `GET/POST /diagnostic/generate/`



### Limites & tokens
Génération peut appeler IA si pas JSON


### Flux utilisateur
1 generate 2 quiz honnête 3 création compte final



---

# DASHBOARD ACCUEIL


**URL:** /dashboard/


**Template:** `core/dashboard.html (~1255 lignes)`


### À quoi ça ressemble
Grille stats 4 cartes colorées top border (quiz violet, streak orange, BAC cyan, exos bleu). Section Coach IA avec skeleton shimmer puis cartes. 4 action cards grandes. 2 colonnes: barres matières + ligue leaderboard.


### Structure (ordre vertical)

1. Modal école (si school_missing): input autocomplete + Confirmer

2. Bannière guest jaune: S'inscrire

3. Stats: quiz complétés, streak jours, note BAC /1900 + barre milestone, exercices résolus + palier

4. Coach IA: badge mistakes à réviser, grille coachingCards AJAX

5. Lien Plan de révision hebdo

6. Action cards: Chat, Quiz, Exercices, Examen blanc

7. Colonne gauche: niveau par matière (barres % quiz)

8. Colonne droite: ligue nom, rang #, XP barre, récompense Top1 Premium, liste classement 🥇🥈🥉

9. Modal guest série (SVT/SMP/SES/LLA) si pending

10. Celebration streak confetti si streak_just_earned



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Modal | Confirmer l'école | saveSchoolBtn | POST api_save_school | /dashboard/api/save-school/ | 0 |

| CTA guest | S'inscrire | — | /signup/ | 0 | 0 |

| Action | Demander à l'IA | action-card | /dashboard/chat/ | 0 | 0 |

| Action | Lancer Quiz | — | /dashboard/quiz/ | 0 | 0 |

| Action | Exercices | — | /dashboard/exercices/ | 0 | 0 |

| Action | Examen Blanc | — | /dashboard/examen-blanc/ | 0 | 0 |

| Coach cards | Dynamiques | coaching-card | lien action_url | GET | ⚠️ load ~3000 tok si smart coach |



### APIs

- `GET /dashboard/api/coaching/cards/ — ~3000 tok OUT (smart coach JSON)`

- `GET /dashboard/api/mistakes/summary/ — 0 tok (DB)`

- `POST /dashboard/api/save-school/ — 0`

- `GET /schools/?q= — 0`



### Limites & tokens
Coach cards = gros poste IA au chargement page. Free: idem 50 API/jour si coach appelle IA


### Flux utilisateur
1 Load page 2 AJAX coaching cards 3 User clique action card → feature



---

# CHAT IA


**URL:** /dashboard/chat/


**Template:** `core/chat.html (~1624 lignes)`


### À quoi ça ressemble
Plein écran type ChatGPT. Fond gradient vert/cyan sombre. Pills matières horizontales scroll. Bulles: IA gris slate border-radius asymétrique, user bleu gradient. Input bar sticky bottom avec paperclip + send vert gradient. Panneau historique slide droite 320px.


### Structure (ordre vertical)

1. Topbar: Nouveau, Historique (clock icon)

2. Hist panel: liste sessions, lien historique complet

3. Subj pills: toutes matières mats.items — OBLIGATOIRE avant send

4. Hint vert si pas de matière choisie

5. Welcome: BIENVENUE + nom, ou APERÇU guest

6. Guest: demo QA tabs matières + CTA signup, input bloqué overlay

7. Msgs area scroll, typewriter IA, MathJax, copy/regen sur hover IA

8. Input: textarea auto-resize, images + PDF (pdf.js extract 8000 chars), send

9. Preload: pdf_path, pdf_text, preload_message, preload_session depuis autres pages



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Topbar | Nouveau | — | newConversation() reset | 0 | 0 |

| Topbar | Historique | histBtn | toggleHistory() | 0 | 0 |

| Pill | Chaque matière | subj-pill | setSubject(key) | 0 | 0 |

| Input | Paperclip | fileInput | image/pdf attach | 0 | 0 |

| Input | Send | sendBtn | sendMessage() | POST /dashboard/api/chat/ | 1 appel ~900-2000 tok OUT + RAG input |

| Msg IA | Copier | msg-act-btn | copyMsg | 0 | 0 |

| Msg IA | Régénérer | — | regenMsgChat | POST api/chat/ | +1 appel |

| Followup | Chips | fup-btn | useSuggestion → send | idem | idem |



### APIs

- `POST /dashboard/api/chat/ — FAST model, max ~2000 out, RAG 4500 chars, history 6 msgs`

- `GET /dashboard/api/chat/session/?session_key= — 0`

- `Background memory extract every 4 msgs — 400 tok (hidden)`



### Limites & tokens
Free: 2 chats/jour + 50 API. Guest: input bloqué, demo QA statique. Local: fillers bonjour/merci = 0 API mais compte chat quota


### Flux utilisateur
1 Choisir matière 2 Taper question 3 Typing dots 4 Réponse typewriter 5 Session_key saved



---

# QUIZ


**URL:** /dashboard/quiz/


**Template:** `core/quiz.html (~839 lignes)`


### À quoi ça ressemble
Picker central max-width. Tabs Solo vert / Duel gris. Grille mat-tile colorées par matière. Arena: timer 30s, barre progression, carte question, 4 options MCQ, feedback vert/rouge.


### Structure (ordre vertical)

1. Tab Solo / Défier ami

2. Solo: grille matières, SVT → sous-picker Bio/Géo/Toutes

3. Duel: create grille OU join code 6 hex

4. Duel waiting: code gros monospace, copy, poll state

5. Loading: astra-loader dots

6. Arena: scoreboard duel VS, qNum/qTotal, timer, bookmark, next

7. Results: score %, analyse bouton, CTA guest signup

8. Modal guest upgrade duel premium



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Tab | Solo | tabSolo | setQuizMode('solo') | 0 | 0 |

| Tab | Défier ami | tabDuel | setQuizMode('duel') | 0 | 0 |

| Tile | Matière | mat-tile | startQuiz(subject) ou pickSVT | GET api/quiz/questions/ | 0 si JSON |

| SVT | Biologie/Géologie/Toutes | — | startQuiz avec discipline | idem | 0 |

| Duel | Créer | — | createDuel(subject) | POST api/duel/create/ | 0 |

| Duel | Rejoindre | — | joinDuel() | POST api/duel/join/ | 0 |

| Play | Option MCQ | — | select answer | 0 | 0 |

| Play | Sauvegarder | bookmarkBtn | toggleBookmark | POST api/bookmark/ | 0 |

| Play | Question suivante | nextBtn | nextQuestion() | 0 | 0 |

| Results | Analyse erreurs | — | api/quiz/analyse/ | POST | ~1000 tok |

| Results | Sauver score | — | api/quiz/save/ | POST | 0 |



### APIs

- `GET /dashboard/api/quiz/questions/`

- `POST api/quiz/save/`

- `POST api/quiz/analyse/`

- `api/duel/*`



### Limites & tokens
Free: 1 quiz/jour. Guest: 1 quiz/matière session. Questions = JSON database/quiz_*.json = 0 API load


### Flux utilisateur
Pick matière → load 10 Q → timer → feedback → score → optional analyse IA



---

# EXERCICES GUIDÉS


**URL:** /dashboard/exercices/


**Template:** `core/exercices.html (~2018 lignes)`


### À quoi ça ressemble
3 steps: grille matières → grille chapitres → split exercice card + chat Astra. Outils interactifs Punnett tables, inputs scientifiques. Input bar fixe bottom comme chat.


### Structure (ordre vertical)

1. Step1 #stepSubject: grille matières

2. Step2 #stepChapter: tuiles chapitre + aléatoire

3. Step3: badge EXERCICE, bouton Nouvel exercice top-right, titre, texte, meta tags

4. Chat thread exo-msgs avec avatar Astra

5. Toolbar: Analyser, Corriger, Expliquer type, Similaire

6. Interactive tools: punnett grid, legend, analysis rows

7. Footer: Nouvel exercice, Terminer session

8. Input bar: textarea + send (api exercices/chat)



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Step1 | Matière tile | — | selectSubject | 0 | 0 |

| Step2 | Chapitre | — | selectChapter | 0 | 0 |

| Step2 | Chapitre aléatoire | — | random chapter | 0 | 0 |

| Card | Nouvel exercice | exo-gen-btn | api/exercices/get/ | POST | 1200-3200 tok + QC |

| Tool | Analyser | — | api/exercices/analyze/ | POST | variable Pro |

| Tool | Corriger | — | api/exercices/correct/ | POST | ~2000 tok |

| Tool | Expliquer | — | api/exercices/teach/ | POST | variable |

| Tool | Similaire | — | api/exercices/similar/ | POST | ~800 fast |

| Chat | Send | exo-send-btn | api/exercices/chat/ | POST | 180-400 tok/msg |

| Finish | Terminer | exo-finish-btn | api/exercices/complete/ | POST | 0 bilan local |



### APIs

- `api/exercices/get|analyze|correct|teach|similar|chat|complete`



### Limites & tokens
Free: 1 exercice/jour total. Guest: limité


### Flux utilisateur
Matière → chapitre → load exo → chat tutor → correct/analyze → finish



---

# EXAMEN BLANC


**URL:** /dashboard/examen-blanc/


**Template:** `core/examen_blanc.html (~2674 lignes)`


### À quoi ça ressemble
Style feuille ministère: header bleu gradient seal MENFP, timer 3h monospace vert→jaune→rouge, parties I II III, questions types officiels. Mode zen toggle. Barre progression réponses.


### Structure (ordre vertical)

1. Picker matière grille

2. Loading génération IA spinner

3. exam-sheet: header ministry, timer bar, rules

4. Parts avec pts badges

5. Fill blank inputs dashed

6. Matching grid selects

7. MCQ options

8. Open textareas + model answer reveal

9. Passage blocks philo/français

10. Action bar: Soumettre, Correction IA, Voir corrigé, Mode zen

11. Results breakdown score par section



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Picker | Matière | — | generateExam | POST generate-v2/ | 1-3 appels 2200-3500+ tok |

| Timer | Mode zen | — | toggle UI | 0 | 0 |

| Per Q | Correction IA | — | api/exam/ai-correct/ | POST | variable |

| Submit | Soumettre | — | score local + save | 0 | 0 |

| Flip | Voir corrigé | — | toggle model answers | 0 | 0 |



### APIs

- `POST /dashboard/api/examen-blanc/generate-v2/`

- `api/exam/ai-correct/`

- `cache GeneratedExam = 0`



### Limites & tokens
Guest: 1 exam total. LE PLUS COÛTEUX. ENABLE_EXAM_AI_ENHANCE=false par défaut


### Flux utilisateur
Pick matière → generate (wait) → remplir 3h timer → submit → résultats



---

# EXTRA BÈT (Q&A communauté)


**URL:** /dashboard/extra-bet/


**Template:** `core/extra_bet.html (~826 lignes)`


### À quoi ça ressemble
Feed social Q&A. Topbar titre + bouton vert Publier. Modal création overlay blur. Cartes posts avec badges matière/type. Sidebar leaderboard top créateurs. Filtres matière GET + tri Recent/Likes/Unanswered.


### Structure (ordre vertical)

1. Topbar Publier

2. Modal: matière, type direct/fill/qcm, toolbar symboles maths, preview, options A-D, publier

3. Top créateurs leaderboard

4. Filtre matière dropdown

5. Tri sort links

6. Feed cards: like, répondre, aide IA, delete owner

7. Réponses thread expand



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Topbar | Publier | openEbModal | ouvre modal | 0 | 0 |

| Modal | Fermer | closeEbModal | ferme | 0 | 0 |

| Modal | Insérer blanc ___ | ebBlankBtn | insert blank | 0 | 0 |

| Modal | Symboles toolbar | ebToolbar | insert math chars | 0 | 0 |

| Modal | Publier | ebPublishBtn | publishExtraBet() | POST api/extra-bet/create/ | 0 vérif IA possible |

| Card | Like | eb-like-btn | api/extra-bet/like/ | POST | 0 |

| Card | Répondre | — | api/extra-bet/answer/ | POST | 0 |

| Card | Aide IA | — | api/extra-bet/ai-help/ | POST | 1 chat ~900 tok |

| Card | Supprimer | eb-delete-btn | api/extra-bet/delete/ | POST | 0 |

| Filter | Tri recent/likes/unanswered | eb-sort-btn | GET reload | 0 | 0 |



### APIs

- `api/extra-bet/create|answer|like|delete|ai-help`



### Limites & tokens
Free: 3 réponses/jour. Publish = 0 API usually


### Flux utilisateur
Browse feed → like/answer → ou publier question → IA vérifie avant post



---

# COURS HUB


**URL:** /dashboard/cours/


**Template:** `core/cours.html (~473 lignes)`


### À quoi ça ressemble
Grille cartes launch 280px min, chaque matière couleur propre (physique bleu, SVT vert, kreyòl rose…). Chaque carte: icône 44px, titre, description, pills topics, CTA Commencer gradient, bouton chevron drawer chapitres avec rings progression %.


### Structure (ordre vertical)

1. Sections groupées (sciences, langues…)

2. 13 matières avec card-* class

3. Drawer chapitres expandable ch-num ring progress

4. Locked chapters 🔒 guest/free ch>1



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| CTA | Commencer | cours-launch-cta | → cours/<subject>/ | GET | 0 |

| Toggle | Chapitres chevron | cours-launch-cta-ai | toggle drawer | 0 | 0 |

| Link | Chapitre N | chapter-link | chapter_cours si unlocked | GET | 0 |



### APIs

- `Progress rings from DB hybrid-progress — 0`



### Limites & tokens
Free: chapitre 1 seulement/matière. Guest: ch1 démo


### Flux utilisateur
Hub → pick matière → cours page ou chapitre direct



---

# COURS PHYSIQUE


**URL:** /dashboard/cours/physique/


**Template:** `core/physique.html (large interactive)`


### À quoi ça ressemble
Hero matière, grille chapitres numérotés, lecteur sections avec mini-quiz embed, sidebar chat IA cours, lien exercices physique par section.


### Structure (ordre vertical)

1. Back to cours

2. Chapter grid progress

3. Section reader

4. Mini-quiz inline

5. Course chat panel

6. Reset progress

7. Link exercices list



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Nav | Retour cours | — | /dashboard/cours/ | 0 | 0 |

| Chapter | Chapitre tile | — | load section | GET/POST api/physique/section/ | ~1400 tok si génère |

| Quiz | Mini-quiz | — | api/physique/miniquiz/ | POST | ~850 tok |

| Chat | Question cours | — | api/cours/chat/ | POST | 2200-2800 tok |

| Reset | Reset progress | — | api/cours/reset/ | POST | 0 |

| Exos | Liste exercices | — | physique/exercices/<section>/ | GET | 0 |



### APIs

- `api/cours/physique/section|miniquiz|exercises|progress`

- `api/cours/chat`

- `api/course_hybrid_progress`



### Limites & tokens
Chat cours = très coûteux. Contenu pré-généré JSON = 0


### Flux utilisateur
Chapitre → sections → mini-quiz → chat aide



---

# COURS SC. SOCIALES


**URL:** /dashboard/cours/sc-social/


**Template:** `core/sc_social.html`


### À quoi ça ressemble
Similaire physique mais contenu histoire/géo/économie intégré, corrections interactives.


### Structure (ordre vertical)

1. Chapter grid

2. Interactive corrections

3. Progress save



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Section | Load | — | api/cours/sc-social/ | POST | variable |

| Correct | Correction | — | api/sc-social/correct/ | POST | variable |

| Progress | Save | — | api/sc-social/progress/ | POST | 0 |



### APIs

- `api/cours/sc-social/progress|correct`



### Limites & tokens
idem cours


### Flux utilisateur
Lecture + exercices intégrés



---

# COURS GÉNÉRIQUE (Math, SVT, Kreyòl, Chimie, etc.)


**URL:** /dashboard/cours/math/ | svt/ | kreyol/ | chimie/ | anglais/ | economie/ | histoire/ | informatique/ | art/ | espagnol/ | philosophie/


**Template:** `core/generic_cours.html`


### À quoi ça ressemble
Même pattern que physique mais template générique, couleurs par card-* du hub.


### Structure (ordre vertical)

1. Hero

2. Chapter list

3. Section API generation

4. Mini-quiz

5. Chat sidebar



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Chapter | — | — | api/cours/section/ | POST | ~1400 tok |

| Mini-quiz | — | — | api/cours/miniquiz/ | POST | ~850 |

| Chat | — | — | api/cours/chat/ | POST | 2200-2800 |

| Chapter link | — | — | chapter_cours/<num>/ | GET | 0 |



### APIs

- `api/cours/section|miniquiz|chat|hybrid-progress`



### Limites & tokens
Chapitre 1 free


### Flux utilisateur
Generic course flow



---

# CHAPITRE COURS (vue chapitre)


**URL:** /dashboard/cours/<subject>/<num>/


**Template:** `core/chapter_cours.html`


### À quoi ça ressemble
Topbar: back, titre chapitre, badge matière, barre progression %. Lecteur sections, quiz embed, Q&A IA.


### Structure (ordre vertical)

1. Topbar back + progress

2. Section content

3. Embedded quizzes

4. AI Q&A

5. Demo lock message guest ch>1



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Back | ← | — | parent cours | GET | 0 |

| Question | Poser | — | api/course-question/ | POST | ~900 tok |

| Summary | Résumé | — | api/chapter-summary/ | POST | ~600 tok |



### APIs

- `api/course-question`

- `api/chapter-summary`

- `api/cours/chat`



### Limites & tokens
Guest: ch1 only


### Flux utilisateur
Read chapter → mini activities → chat



---

# PHYSIQUE EXERCICES LISTE


**URL:** /dashboard/cours/physique/exercices/<section_id>/


**Template:** `core/physique_exercises.html`


### À quoi ça ressemble
Liste exercices par section physique, cards numérotées.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Card | Exercice N | — | detail page | GET | 0 |



### APIs

- `api/physique/exercises`



### Limites & tokens
0 load list


### Flux utilisateur
Section → pick exercise



---

# PHYSIQUE EXERCICE DÉTAIL


**URL:** /dashboard/cours/physique/exercices/<section>/<index>/


**Template:** `core/physique_exercise_detail.html`


### À quoi ça ressemble
Exercice plein écran avec solution steps, bouton similaire.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Similar | Exercice similaire | — | similar route | GET | ⚠️ génération |



### APIs

- `generate similar`



### Limites & tokens
variable


### Flux utilisateur
View → similar



---

# FICHES MÉMO


**URL:** /dashboard/fiches/


**Template:** `core/fiches.html`


### À quoi ça ressemble
Pills filtre matière. Stats 3 compteurs. Grille flip cards 3 états (new/review/known). Boutons générer vert + réviser erreurs.


### Structure (ordre vertical)

1. Subject pills

2. Stats total/connus/revoir

3. Generate + Review errors

4. Flashcard grid flip

5. Status buttons on card



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Filter | Matière pill | — | filter local | 0 | 0 |

| Action | Générer des fiches | — | api/fiches/generate/ | POST | ~2000 tok fast JSON |

| Action | Réviser mes erreurs | — | filter mistakes | 0 | 0 |

| Card | Flip | — | CSS flip | 0 | 0 |

| Card | Statut new/review/known | — | api/fiches/status/ | POST | 0 |



### APIs

- `api/fiches/generate/`

- `api/fiches/status/`



### Limites & tokens
PREMIUM ONLY — free voit premium_required


### Flux utilisateur
Generate once → flip review → mark known



---

# PLAN DE RÉVISION


**URL:** /dashboard/plan/


**Template:** `core/plan.html`


### À quoi ça ressemble
Hero countdown jours avant BAC. Faiblesses barres horizontales. Tabs semaines S1-S4. Cartes tâches jour avec liens cours/exo/quiz. Checkbox done. Barre % complétion.


### Structure (ordre vertical)

1. BAC countdown

2. Generate/regenerate plan

3. Weakness panel

4. Week tabs

5. Daily task cards

6. Mark done checkbox

7. Progress %



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Primary | Générer mon plan | — | api/plan/generate/ | POST | 3500+3000 tok Pro |

| Regen | Régénérer | — | idem | idem | idem |

| Task | Lien cours/exo/quiz | — | navigation | GET | 0 |

| Checkbox | Marquer fait | — | api/plan/progress/ | POST | 0 |



### APIs

- `api/plan/generate/`

- `api/plan/progress/`



### Limites & tokens
PREMIUM ONLY


### Flux utilisateur
Generate → follow weekly tasks → check done



---

# FAVORIS / BOOKMARKS


**URL:** /dashboard/bookmarks/


**Template:** `core/bookmarks.html`


### À quoi ça ressemble
Pills filtre matière. Cartes question quiz sauvegardées avec options (correcte highlight vert), explication, date. Bouton retirer.


### Structure (ordre vertical)

1. Filter pills

2. Bookmark cards

3. Empty state → quiz



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Filter | Matière | — | client filter | 0 | 0 |

| Card | Retirer favori | — | api/bookmark/ | POST toggle | 0 |



### APIs

- `api/bookmark/`



### Limites & tokens
PREMIUM ONLY


### Flux utilisateur
View saved quiz questions from quiz page



---

# PROGRESSION


**URL:** /dashboard/progression/


**Template:** `core/progression.html`


### À quoi ça ressemble
4 stat cards top. Chart.js line quiz scores, radar par matière, bar mastery. Bloc recommandations matières faibles avec liens quiz.


### Structure (ordre vertical)

1. Stat cards streak BAC exos temps

2. Line chart

3. Radar chart

4. Bar chart per subject

5. Recommendations weak subjects

6. Links chat plan



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Link | Aller au quiz | — | /dashboard/quiz/ | 0 | 0 |

| Link | Chat | — | /dashboard/chat/ | 0 | 0 |



### APIs

- `GET api/stats/ — DB only 0 tok`



### Limites & tokens
Free may be gated — check premium. Charts = 0 API


### Flux utilisateur
View stats from DB aggregates



---

# MON PROFIL


**URL:** /dashboard/profil/


**Template:** `core/profil.html`


### À quoi ça ressemble
Hero: grande avatar editable, nom, email, badge niveau, row stats XP/streak/quiz. Form sections: infos, école, série, langue, objectif BAC.


### Structure (ordre vertical)

1. Avatar upload overlay

2. Stats row

3. Form personal

4. School serie langue target

5. Save button



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Avatar | Changer photo | — | file input | POST api/avatar/upload/ | 0 |

| Submit | Enregistrer modifications | — | POST profil form | POST | 0 |



### APIs

- `api/avatar/upload/`



### Limites & tokens
0


### Flux utilisateur
Edit profile → save



---

# HISTORIQUE CHAT


**URL:** /dashboard/historique/


**Template:** `core/historique.html`


### À quoi ça ressemble
Stats row conversations count. Search input + filtre matière. Grille cards conversation preview date matière.


### Structure (ordre vertical)

1. Stats

2. Search form GET

3. Conversation cards

4. Empty state



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Search | Rechercher | — | GET form | 0 | 0 |

| Card | Ouvrir conversation | — | conversation_detail | GET | 0 |



### Limites & tokens
0 — pas dans sidebar nav principal, accès via chat historique


### Flux utilisateur
Search → open detail



---

# DÉTAIL CONVERSATION


**URL:** /dashboard/historique/<session_key>/


**Template:** `core/conversation_detail.html`


### À quoi ça ressemble
Replay thread messages user/IA. Bouton continuer → chat avec session preload.


### Structure (ordre vertical)

1. Message replay

2. Continue button

3. Back link



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| CTA | Continuer cette conversation | — | /dashboard/chat/?session= | GET | 0 |

| Back | Retour historique | — | /dashboard/historique/ | 0 | 0 |



### Limites & tokens
0


### Flux utilisateur
Read old chat → continue in chat page



---

# BIBLIOTHÈQUE PDF


**URL:** /dashboard/library/


**Template:** `core/library.html`


### À quoi ça ressemble
Sections par matière. Chaque PDF: icône, titre, boutons Voir/Download/Extract IA.


### Structure (ordre vertical)

1. Grouped PDF list

2. View new tab

3. Download

4. AI extract premium

5. Guest gate toast



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Action | Voir PDF | — | api/pdf-serve/ | GET stream | 0 |

| Action | Télécharger | — | download | 0 | 0 |

| Action | Extraire IA | — | api/pdf-extract-text/ | POST | ⚠️ premium |



### APIs

- `api/pdf-serve/`

- `api/pdf-extract-text/`



### Limites & tokens
Guest: redirect signup. Extract = premium


### Flux utilisateur
Browse exams PDF → open or send to chat



---

# MESSAGES / AMIS


**URL:** /dashboard/amis/


**Template:** `core/amis.html (très large)`


### À quoi ça ressemble
Layout 2 colonnes: gauche liste conversations/amis/groupe OUTOUBON, droite chat panel WhatsApp-like. Header: add friend, invitations bell. Input: photo vidéo emoji quiz groupe.


### Structure (ordre vertical)

1. Guest CTA only

2. Left: search friends, tabs Conversations/Gérer

3. Friend list

4. Group OUTOUBON entry

5. Chat header remove friend

6. Messages bubbles reply

7. Input bar media emoji

8. Quiz creator panel group

9. Modals: search invite profile



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Header | Ajouter ami | — | modal search | POST api/amis/ | 0 |

| Header | Invitations | bell | modal | 0 | 0 |

| Chat | Envoyer | — | api/amis/send/ | POST | 0 |

| Chat | Photo/Vidéo | — | upload | 0 | 0 |

| Chat | Quiz groupe | — | panel publish | api/group-chat/ | 0 |

| Group | Send | — | api/group-chat/send/ | POST | 0 |

| Delete | Supprimer msg | — | api/delete/ | POST | 0 |



### APIs

- `api/amis/*`

- `api/group-chat/*`

- `api/admin-message/`



### Limites & tokens
Guest: signup CTA only. Free send may be limited. 0 IA


### Flux utilisateur
Social messaging — no AI cost



---

# DUEL (page standalone)


**URL:** /dashboard/duel/


**Template:** `core/duel.html`


### À quoi ça ressemble
Duplicate quiz duel UI: tabs create/join, lobby, gameplay scoreboard.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Same as quiz duel panel | — | — | api/duel/* | 0 | 0 |



### APIs

- `api/duel/create|join|state|finish`



### Limites & tokens
0 IA — questions JSON


### Flux utilisateur
Same as quiz duel tab



---

# PRICING


**URL:** /pricing/


**Template:** `accounts/pricing.html`


### À quoi ça ressemble
Plans cards Mensuel/Annuel features list checkmarks vert. Boutons payer MonCash. Lien cadeau.


### Structure (ordre vertical)

1. Plan cards

2. Feature lists

3. MonCash pay buttons

4. Gift link

5. NatCash notify



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Pay | Choisir Mensuel | — | POST create-payment/ | redirect MonCash | 0 |

| Pay | Choisir Annuel | — | idem | 0 | 0 |

| Link | Offrir abonnement | — | /cadeau/ | 0 | 0 |



### APIs

- `create-payment`

- `payment-status`

- `natcash-notify`



### Limites & tokens
0


### Flux utilisateur
Pick plan → MonCash → payment-success



---

# CADEAU / GIFT FLOW


**URL:** /cadeau/ | /cadeau/<token>/ | merci/


**Template:** `gift_share.html, gift_pay.html, gift_success.html, gift_invalid.html, gift_already_used.html`


### À quoi ça ressemble
Flow lien partage → page paiement pour ami → merci. Cards student info.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Generate | Créer lien | gift_share | POST | 0 | 0 |

| Pay | Payer MonCash | gift_pay | create_gift_payment | 0 | 0 |

| Copy | Copier lien | — | clipboard | 0 | 0 |



### APIs

- `gift-payment-status`



### Limites & tokens
0


### Flux utilisateur
Premium user generates link → friend pays



---

# AGENT DASHBOARD


**URL:** /agent/dashboard/


**Template:** `agent/dashboard.html`


### À quoi ça ressemble
Sidebar agent: Overview Referrals Withdrawals. Balance badge. Referral URL + QR. Form retrait MonCash.


### Structure (ordre vertical)

1. Stats balance

2. Referral link copy QR

3. Withdrawal form amount phone

4. Tables referrals withdrawals



### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Copy | Lien parrainage | — | clipboard | 0 | 0 |

| Submit | Demander retrait | — | form POST | 0 | 0 |

| Nav | Overview/Referrals/Withdrawals | — | tabs | 0 | 0 |



### APIs

- `agent/api/withdrawal-status/`



### Limites & tokens
0


### Flux utilisateur
Agent tracks referrals and withdraws



---

# PREMIUM WALL


**URL:** (overlay sur fiches/plan/bookmarks/etc.)


**Template:** `core/premium_required.html`


### À quoi ça ressemble
Centré couronne dorée, nom feature, message limite, bouton Voir les plans, retour dashboard.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| CTA | Voir les plans | — | /pricing/ | 0 | 0 |

| Back | Retour | — | /dashboard/ | 0 | 0 |



### Limites & tokens
Shown when free hits premium gate


### Flux utilisateur
Block → upgrade CTA



---

# PAGES ERREUR


**URL:** 404 / 403 / 400 / 500


**Template:** `404.html etc.`


### À quoi ça ressemble
Standalone dark, gros code erreur, message, lien Retour accueil.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Link | Retour accueil | — | / | 0 | 0 |



### Limites & tokens
0


### Flux utilisateur
Error → home



---

# COMPLETE PROFILE


**URL:** /complete-profile/


**Template:** `accounts/complete_profile.html`


### À quoi ça ressemble
Post-OAuth minimal form école série langue.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Submit | C'est parti ! | — | POST | 0 | 0 |



### Limites & tokens
0


### Flux utilisateur
OAuth users complete profile



---

# PAYMENT SUCCESS / ERROR


**URL:** /payment-success/ payment_error.html


**Template:** `accounts/payment_success.html, payment_error.html`


### À quoi ça ressemble
Status message confirmation ou échec paiement.


### Tous les boutons / actions


| Type | Label | ID | Action | Endpoint | Tokens |

|------|-------|-----|--------|----------|--------|

| Link | Retour dashboard | — | /dashboard/ | 0 | 0 |



### APIs

- `payment-status poll`



### Limites & tokens
0


### Flux utilisateur
After MonCash return



---

# ANNEXE A — Classement coût IA par feature


| Rang | Feature | Tokens typiques | Appels | Réduction redesign |

|------|---------|-----------------|--------|-------------------|

| 1 | Examen blanc generate-v2 | 2200-3500+ ×1-3 | Pro | Cache DB, pas auto-load |

| 2 | Chat cours | 2200-2800 out | Flash | JSON pré-généré, chat collapsé |

| 3 | Chat matière | 900-2000 + RAG | Flash | Quotas visibles, fillers local |

| 4 | Plan révision | 3500+3000 | Pro | Premium only — OK |

| 5 | Dashboard coach cards | ~3000 | Flash | **Cache 24h ou stats locales** |

| 6 | Exercice get/correct | 1200-3200 | Pro | Moins boutons IA visibles |

| 7 | Fiches generate | ~2000 | Fast | Premium |

| 8 | Quiz analyse | ~1000 | Fast | Optionnel post-quiz |

| 9 | Chat exercice | 180-400/msg | Flash | Cheap — OK |

| 10 | Quiz load / Duel / Stats / Amis | **0** | — | Safe redesign total |


# ANNEXE B — Prompt ChatGPT pour redesign


```
Tu redesignes OU TOU BON (BAC NS4 Haïti, dark #10B981).

Lis AUDIT_COMPLET_PAR_PAGE.md section par section.

Règles: (1) Ne jamais ajouter appels IA au chargement page

(2) Coach dashboard → cache ou contenu statique

(3) Chaque proposition UI doit tag [0 tok] ou [+IA]

(4) Mobile-first Haïti: +509, MonCash, français/ créole

Priorité pages: Landing → Dashboard → Chat → Quiz → Cours → Examen
```
