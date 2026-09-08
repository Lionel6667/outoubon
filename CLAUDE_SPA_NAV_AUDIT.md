# Audit navigation SPA — OU TOU BON

**Mission:** trouver et implémenter un moyen de passer d’une page dashboard à une autre **en moins d’une seconde**, sans écran de chargement perceptible. Aujourd’hui un clic sur la tab bar / sidebar / un lien interne attend souvent plusieurs secondes (parfois 10–15 s, parfois plus sur pages lourdes). Ça nous dépasse : on a déjà un “SPA maison” et ça ne suffit pas.

Ce fichier est l’état réel du code (septembre 2026). Lis-le en entier avant de proposer une architecture. Ne réécris pas l’app en React. Django templates restent la source d’UI. Production = élèves en Haïti, mobile, connexion moyenne, **PostgreSQL distant** (Neon/Railway via `DATABASE_URL`). En local, `USE_LOCAL_DB=1` utilise SQLite et la nav est ~10× plus rapide — le vrai problème est donc **latence DB + HTML trop gros par clic**, pas “le téléphone est lent”.

Repo : Django (`bacia` / app `core`). Front dashboard : templates HTML + `static/js/v2/app.js`. Pas de framework JS.

---

## 1. Objectif produit

- Clic tab bar (Accueil / Apprendre / Match / Chat / Messages) → **contenu visible < 1 s**, idéalement instantané.
- Même chose pour les liens sidebar (Cours, Exercices, Quiz, Génies, Profil, etc.).
- Pas de flash blanc, pas de disparition de la tab bar, pas de “rien ne se passe puis ça saute”.
- Les données peuvent se rafraîchir **après** l’affichage (stale-while-revalidate OK).
- Ne casse pas : quiz en cours, chat, match live, examen blanc, session exercices, scroll, MathJax, CSRF, auth.

**Succès mesurable :** clic Accueil → Cours → Match → Chat, chacun < 1 s une fois l’app ouverte, même avec DB distante.

---

## 2. Architecture actuelle (ce qui existe vraiment)

### 2.1 Ce n’est PAS un vrai SPA

C’est un **app shell HTML** avec un intercepteur de clics :

1. Premier chargement = page Django complète (`templates/base.html`).
2. `static/js/v2/app.js` capture les `<a href="/dashboard/...">`.
3. `fetch(url, { headers: { 'X-OTB-SPA': '1' } })` redemande **la même URL Django**.
4. Le HTML renvoyé est parsé, on extrait `.app.innerHTML`, on le swap dans le DOM vivant.
5. On réinjecte CSS page + scripts `extra_js`.
6. `history.pushState`. Tab bar hors `.app` est censée rester en place.

Fichiers clés :

| Fichier | Rôle |
|---|---|
| `static/js/v2/app.js` | SPA : intercept, cache mémoire, swap DOM, tab bar |
| `core/spa_middleware.py` | `request.spa_mode = (X-OTB-SPA == '1')` |
| `templates/base.html` | Shell : CSS globaux, MathJax, tab bar, `app.js` **seulement si `not spa_mode`** |
| `templates/core/_sidebar.html` | Sidebar **recopiée dans CHAQUE page** |
| `templates/partials/v2/_bottom_nav.html` | Tab bar mobile (5 items) |
| `core/context_processors.py` | `user_lang` : en spa_mode skip unread/XP/stats ; sinon plusieurs COUNT |

### 2.2 Tab bar (mobile, `max-width: 768px`)

HTML : `templates/partials/v2/_bottom_nav.html`, inclus dans `base.html` **en dehors** de `.app` (donc pas swapée). CSS : `static/css/v2/layout.css` (`.v2-bottom-nav`, z-index 450).

5 hubs, chacun pointe vers **une** URL :

| `data-hub` | Label | URL réelle |
|---|---|---|
| `accueil` | Accueil | `/dashboard/` |
| `apprendre` | Apprendre | `/dashboard/cours/` |
| `match` | Match | `/dashboard/match/` |
| `astra` | Chat IA (nom coach) | `/dashboard/chat/` |
| `messages` | Messages | `/dashboard/amis/` |

`HUB_PATHS` dans `app.js` sert uniquement à colorer le hub actif quand on est sur une **sous-page** :

- Apprendre : cours, exercices, extra-bet, fiches, library
- Match : match, quiz, examen-blanc, genius/*
- Astra : chat, historique
- Messages : amis seulement
- Profil (sidebar, pas tab bar) : profil, gains, progression, plan, bookmarks, pricing

**Piège tab bar :** `initBottomNav()` n’active l’item que si l’URL est **exactement** `BOTTOM_NAV_PATHS[hub]`. Donc `/dashboard/quiz/` est dans le hub Match pour `detectHub()`, mais `detectBottomNavHub()` retourne `null` → **aucun item tab bar n’est `active`** sur Quiz / Exercices / Génies. À connaître si tu changes le modèle.

Au clic tab bar : `setNavLoading` (shimmer sur le label) puis `navigateTo`. Si le package n’est pas en cache → fetch Django → attente.

La tab bar est cachée pendant une session exercices (`body.v2-exo-session-active`).

### 2.3 Sidebar desktop

Chaque template fait `{% include "core/_sidebar.html" %}`. Au swap SPA, **toute la sidebar est détruite et recréée**. XP strip, avatar, langue, badges unread : recalculés côté serveur à chaque fetch (en spa_mode, unread/XP du context processor sont skippés — badges messages peuvent donc rester périmés).

### 2.4 Qu’est-ce qu’un “package” SPA ?

`buildPagePackage(html)` dans `app.js` stocke en **RAM navigateur** (`_pageCache` + `_pagePackages`) :

- `appHTML` = innerHTML complet de `.app` (sidebar + topbar + view-area)
- `scripts` = scripts dans `.app`, siblings, et `#otb-page-scripts` (`{% block extra_js %}`)
- `stylesheetHrefs` = CSS `/static/css/v2/` **sauf** design-system, layout, components, page-shell
- `inlineCss`, title, badge messages

Cache **perdu au refresh**. Pas d’IndexedDB, pas de Service Worker.

Si le package est déjà là → swap immédiat (ça, ça marche).  
Si non → on attend le HTML Django. **C’est le cas presque toujours** : le warm global a été **désactivé**.

### 2.5 Prefetch actuel (volontairement cassé / limité)

`warmAllPages()` existe encore mais **n’est plus appelé**. Commentaire dans `app.js` :

> Bulk warmAllPages() removed — it fired ~16 full Django renders at once and blocked SQLite so every click waited 10–15s behind the warm queue.

`SPA_PREFETCH_URLS` = 18 URLs (dashboard, cours, exercices, match, quiz, chat, amis, genius, profil, gains, fiches, extra-bet, library, plan, bookmarks, progression, historique, examen-blanc).

À la place :

- prefetch au `pointerdown` sur un lien SPA (`warmPage`)
- Match précharge les URLs “Tableau complet” concours

Donc : **le clic attend presque toujours le serveur**, sauf seconde visite dans la même session.

Si `_navBusy` (un fetch déjà en cours) et l’utilisateur reclique ailleurs → **fallback `window.location.href`** = reload complet. Très mauvais.

### 2.6 Mode `spa_mode` côté Django

Header `X-OTB-SPA: 1` → `request.spa_mode = True`.

`base.html` alors **omet** : meta CSRF/viewport, fonts, CSS globaux, toast guest, tab bar, `app.js`, `gamification.js`.

**Il rend quand même** `{% block body %}` = page entière avec sidebar.

Quelques vues ont des raccourcis spa_mode (dashboard : skip masteries, cache 60 s quiz_scores / league / daily_missions). La plupart des vues **n’ont aucun chemin léger** : même travail DB qu’un chargement complet.

Context processor spa_mode : skip COUNT messages/genius + UserStats XP. Utile mais insuffisant.

Middleware `UserActivityMiddleware` et `VisitorTrackingMiddleware` skip spa_mode.

Cache Django = `LocMemCache` (pas Redis). Inutile dès qu’il y a plusieurs workers.

### 2.7 Pages dashboard (toutes éligibles SPA si préfixe `/dashboard/` sauf admin)

Tab bar :

- `/dashboard/` — `dashboard()` : **très lourd** (streak, diagnostic, scores, ligue, missions, spotlights, hall of fame). Template `templates/core/dashboard.html`.
- `/dashboard/cours/` — `cours_view()` : charge chapitres JSON par matière. Template énorme + JS.
- `/dashboard/match/` — `match_view()` : matchmaking + tournois Génies (queries GeniusCompetition). JS match live dans `extra_js`.
- `/dashboard/chat/` — `chat_view()` : coach IA, gros template `chat.html`.
- `/dashboard/amis/` — `amis_view()` : friendships, suggestions.

Ailleurs (sidebar, toujours SPA) : exercices, quiz, extra-bet, fiches, library, examen-blanc, genius (hub/club/competition/match/team), profil, gains, progression, plan, bookmarks, historique, pricing (si sous /dashboard — pricing est `/pricing/`, **pas SPA** : `isSpaEligibleUrl` exige `/dashboard/`).

Hors SPA : landing, login, signup, `/pricing/`, admin `/dashboard/otb-ctrl-9x7k/`.

### 2.8 Scripts page

Les pages (quiz, match, genius hub, exercices, chat) ont des **IIFE énormes en inline JS**. Au swap SPA :

- `const`/`let` top-level réécrits en `var` pour éviter SyntaxError
- le script est ré-exécuté à chaque visite
- listeners se recollent ; état JS de la page précédente est perdu (sauf si leak)

`app.js` et `gamification.js` sont “permanents” (pas rechargés).

MathJax : `typesetPromise` sur `.view-area` après swap.

### 2.9 Mesures / symptômes déjà vus

- Warm de ~16 pages en parallèle : file d’attente 10–15 s, clics bloqués.
- Page concours Génies + Match via Django test client / DB distante : **dizaines de secondes**.
- Commentaire `settings.py` : `USE_LOCAL_DB=1` → nav ~10× plus rapide.
- Clic “Tableau complet” : rien puis apparition tardive (fetch SPA d’une vue encore trop chère). Mitigé depuis (cache 45 s + prefetch pointerdown) mais le modèle reste “attendre Django”.

---

## 3. Pourquoi on n’est pas sous 1 seconde

Ordre d’impact (production, DB distante) :

1. **Chaque navigation = une vue Django complète.** Round-trips SQL (souvent 50–200 ms chacun vers Neon). Dashboard / cours / match / amis / genius enchaînent plusieurs queries.
2. **Le HTML renvoyé contient sidebar + chrome + page.** Parsing + `innerHTML` d’un gros arbre. Templates quiz/exercices/chat/cours font des centaines de Ko de HTML+JS.
3. **Pas de cache chaud au premier clic.** Le warm global a été tué parce qu’il *aggravait* le problème.
4. **Pas de stale-while-revalidate visuel** : même si on a un vieux package, on ne l’affiche pas pendant un revalidate (sauf s’il est déjà en RAM de *cette* session).
5. **Sidebar recréée à chaque fois** pour rien.
6. **Scripts page ré-exécutés** (coût CPU mobile + risques double-bind).
7. `_navBusy` → reload full page si second clic.
8. LocMemCache / pas de fragment cache HTTP.

Ce n’est **pas** “il faut un nouveau framework”. Le shell existe. Il fetch trop, trop tard, trop gros.

---

## 4. Contraintes (ne pas violer)

- Django + templates. Pas de migration React/Vue “toute l’app”.
- Auth session cookie + CSRF (`window.CSRF` / meta). Guest mode existe (`is_guest`).
- Mobile-first, tab bar visible, sidebar desktop.
- Pages interactives doivent continuer à marcher après swap : quiz, duel, match live, chat, exercices (classe `v2-exo-session-active`), examen blanc.
- Ne pas relancer 16 renders Django au boot.
- Admin custom = `/dashboard/otb-ctrl-9x7k/` : hors SPA.
- Ne pas casser le scroll (déjà eu des fuites CSS `amis.css` / `overflow:hidden` sur `.view-area`).
- Pas de commit git sauf si l’utilisateur le demande.
- Pas de nouvelles pages élève “gadget”. Améliorer la nav existante.

---

## 5. Pistes à évaluer (ce qu’on attend de toi)

Propose **une** architecture claire, puis implémente le plus petit chemin qui atteint < 1 s sur la tab bar.

Pistes légitimes (choisis / mixe, justifie) :

**A. Shell persistant + fragment**  
Garder sidebar + topbar + tab bar dans le DOM. Endpoint ou `?partial=1` / header qui ne rend que `.view-area` (ou `#spa-content`). Swap uniquement cette zone. Views peuvent rester, templates splittés.

**B. Stale-while-revalidate**  
Au clic : afficher **immédiatement** le dernier HTML connu (RAM + éventuellement `sessionStorage`/`Cache API`). Fetch en arrière-plan, remplacer si changé. Premier visit d’une URL : skeleton du hub (statique, 0 DB) puis hydrate.

**C. Prefetch idle séquentiel (concurrency 1)**  
Après load, préchauffer **seulement les 5 URLs tab bar**, une par une, `requestIdleCallback`, jamais 16. Si un clic arrive, **priorité au clic**, cancel/pause le warm.

**D. Pages tab bar “keep-alive”**  
Après première visite, cacher le `view-area` au lieu de le détruire (`display:none` / `hidden`). Revenir au tab = 0 réseau. Invalider sur action (nouveau message, fin de quiz). Attention mémoire mobile.

**E. JSON + templates déjà dans le JS**  
Trop gros pour tout le site. OK pour Accueil stats si le HTML chrome est local.

**À éviter :**
- Rewarm 18 pages en parallèle.
- Service Worker magique sans plan d’invalidation.
- “On passe tout en API JSON” pour 20 templates.
- Skeleton infini sans contenu.

---

## 6. Ordre d’implémentation suggéré (tu peux changer)

1. Tab bar 5 pages : instant si déjà visitées (keep-alive ou cache RAM + SWR).
2. Prefetch idle **uniquement** ces 5 URLs, concurrency 1.
3. Réduire le HTML SPA : ne plus renvoyer la sidebar (fragment).
4. Alléger les 5 vues tab bar en spa_mode (moins de SQL, caches courts déjà amorcés sur dashboard).
5. Ensuite sidebar links.

Mesure avant/après : temps `click → first paint du view-area` sur `/dashboard/` → `/dashboard/cours/` → `/dashboard/match/` avec `DATABASE_URL` distant (pas SQLite).

---

## 7. Fichiers à toucher en priorité

- `static/js/v2/app.js` — cœur nav
- `templates/base.html` — shell vs fragment
- `templates/core/_sidebar.html` + chaque `templates/core/*.html` si split chrome/content
- `core/spa_middleware.py` éventuellement `X-OTB-PARTIAL`
- vues : `core/views.py` `dashboard`, `cours_view`, `match_view`, `chat_view`, `amis_view`
- `static/css/v2/layout.css` — tab bar / loading
- `core/context_processors.py` — ne pas recharger XP/unread à chaque fragment si le chrome est persistant

---

## 8. Comment tester

- Mobile 390px et desktop.
- Clics rapides tab bar (Accueil ↔ Match ↔ Chat) sans reload.
- Quiz : démarrer un quiz, tab bar ne doit pas rester visible en session si c’était le cas ; revenir au picker.
- Chat : conversation ne doit pas se reset si keep-alive mal fait — ** trancher** : keep-alive chat vs re-fetch. Documente le choix.
- Exercices : classe body `v2-exo-session-active`.
- Guest et user connecté.
- 2e clic pendant un fetch : **ne jamais** `window.location.href` full reload.

---

Quand tu proposes la solution, commence par 10 lignes : “voici le modèle (A/B/C/D), voici pourquoi < 1 s même avec Neon, voici les 3 fichiers du premier patch”. Puis code.
