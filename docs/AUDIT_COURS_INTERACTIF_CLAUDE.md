# Audit complet des cours interactifs

Document de passation technique pour expliquer le fonctionnement de la plateforme.

## 1. Résumé du parcours

Le parcours principal est :

1. L'élève ouvre `/dashboard/cours/`.
2. `core.views.cours_view` choisit les matières autorisées par la série de l'élève et charge le catalogue des chapitres.
3. Le template `templates/core/cours.html` affiche une carte par matière.
4. Le bouton `Cours IA` ouvre un drawer côté navigateur. Aucun appel réseau n'est nécessaire pour ouvrir la liste : les chapitres sont déjà injectés dans `CHAPTERS_BY_SUBJ`.
5. Chaque chapitre est rendu comme un lien vers `/dashboard/cours/<subject>/<num>/` s'il est accessible, ou comme un lien visuel verrouillé sinon.
6. `core.views.chapter_cours_view` vérifie à nouveau le droit côté serveur, crée/reprend une `CourseSession`, charge le contenu du chapitre et rend `templates/core/chapter_cours.html`.
7. Le navigateur affiche soit le contenu hybride pré-généré, soit le tuteur IA conversationnel.
8. La progression est sauvegardée dans `CourseSession` ou `CourseProgressState`, selon le mode utilisé.

La règle côté serveur est la seule règle de sécurité : le JavaScript du hub ne fait qu'améliorer l'affichage. Un utilisateur ne peut donc pas contourner le verrouillage en fabriquant une URL.

## 2. Matières et routes

Les routes sont déclarées dans `core/urls.py` sous le commentaire `# Cours interactif`.

### Hub et nouvelle vue chapitre

- `/dashboard/cours/` -> `views.cours_view` -> `templates/core/cours.html`.
- `/dashboard/cours/<subject>/<num>/` -> `views.chapter_cours_view` -> `templates/core/chapter_cours.html`.

Le catalogue comprend notamment `maths`, `physique`, `chimie`, `svt`, `francais`/Kreyòl, `philosophie`, `anglais`, `histoire`, `economie`, `informatique`, `art` et `espagnol`.

### Pages historiques par matière

Ces routes existent encore et chargent les anciens lecteurs riches :

- `/dashboard/cours/physique/` -> `physique_view` -> `core/physique.html`.
- `/dashboard/cours/sc-social/` -> `sc_social_view` -> `core/sc_social.html`.
- `/dashboard/cours/math/` -> `math_cours_view`.
- `/dashboard/cours/svt/` -> `svt_cours_view`.
- `/dashboard/cours/kreyol/` -> `kreyol_cours_view`.
- `/dashboard/cours/chimie/` -> `chimie_cours_view`.
- `/dashboard/cours/anglais/` -> `anglais_cours_view`.
- `/dashboard/cours/economie/` -> `economie_cours_view`.
- `/dashboard/cours/histoire/` -> `histoire_cours_view`.
- `/dashboard/cours/informatique/` -> `informatique_cours_view`.
- `/dashboard/cours/art/` -> `art_cours_view`.
- `/dashboard/cours/espagnol/` -> `espagnol_cours_view`.
- `/dashboard/cours/philosophie/` -> `philosophie_cours_view`.

Le hub actuel pointe prioritairement vers la vue chapitre JSON commune; les pages historiques restent utilisées par d'anciens liens, certaines fonctions spécialisées et des contenus de matière.

## 3. Sources de contenu

### Catalogue du hub

`cours_view` utilise :

- `MATS` pour le label, l'icône, la description et la couleur de chaque matière.
- `_get_cours_chapters(subject)` pour les chapitres.
- `_get_user_serie_subjects(request.user)` pour filtrer les matières de la série.
- `_cours_progress_by_chapter(user, user_subjs)` pour afficher les anneaux de progression.

Les chapitres sont généralement issus des fichiers JSON du dossier `database/`, avec des fallbacks vers des modèles DB historiques lorsque nécessaire. Les cartes ne génèrent pas de contenu IA au chargement.

### Vue chapitre

`chapter_cours_view` recherche le chapitre par son numéro. Le chapitre est copié et reçoit un alias `description` à partir de `summary` pour maintenir la compatibilité avec les templates.

Pour les matières présentes dans `MATS`, la vue essaie ensuite :

```text
pdf_loader.get_hybrid_course_payload(subject, num, chapter_title)
```

Si le payload contient des `subchapters`, `hybrid_mode` est activé. Le contenu est alors lu depuis le payload pré-généré, sans appel IA initial. Chaque sous-chapitre contient des chunks affichés progressivement.

Si le mode hybride n'est pas disponible, la session conversationnelle classique est utilisée et le tuteur génère les réponses à la demande.

## 4. Premium, gratuit et invité

### Source de vérité

Le modèle `accounts.models.UserProfile` possède `plan_expiration`. Sa propriété `is_premium` retourne vrai quand cette date est supérieure ou égale à la date courante.

`core.premium.is_premium(user)` utilise cette propriété. Cette fonction est la source commune utilisée par les protections serveur.

### Règles des cours

`core.premium.can_access_chapter(user, subject, chapter_num)` applique :

- Premium actif : tous les chapitres de toutes les matières.
- Compte gratuit : uniquement le chapitre 1 de chaque matière.
- Invité : pas d'accès serveur direct à une page chapitre; redirection vers le hub en mode démo.

La protection est exécutée au début de `chapter_cours_view`, avant la création de session et avant le chargement du contenu. Un gratuit qui demande directement le chapitre 2 reçoit `core/premium_required.html`.

### Affichage du hub

`cours.html` reçoit maintenant `is_premium` calculé explicitement par `core.premium.is_premium` dans `cours_view`. Le JavaScript utilise cette valeur dans `IS_PREMIUM_COURS`.

Pour chaque chapitre, le navigateur applique :

```text
locked = chapitre marqué guest_locked
      ou (utilisateur non premium et numéro > 1)
```

Un chapitre verrouillé n'a pas de vraie URL. Pour un invité, le clic ouvre la modale de création de compte. Pour un compte gratuit, il ouvre la modale Premium.

Le JavaScript n'est jamais la protection finale : `chapter_cours_view` refait le contrôle.

### Activation du premium

`accounts.payments._activate_subscription_and_pay_commission` crée ou récupère le profil et prolonge `plan_expiration`. La page pricing expose `profile.is_premium`. Le paiement doit donc être confirmé avec un statut MonCash accepté avant d'appeler cette activation.

## 5. Session et progression

### CourseSession

`core.models.CourseSession` représente une session de tuteur pour un chapitre précis.

Champs importants :

- `user` : propriétaire de la session.
- `chapter` : ancienne FK nullable vers `SubjectChapter`.
- `chapter_subject`, `chapter_num` : identifiants du nouveau système JSON.
- `chapter_title`, `chapter_desc` : copie stable des métadonnées du chapitre.
- `messages` : JSON contenant les messages utilisateur/assistant.
- `progress_step` : étape pédagogique courante.
- `status` : `active` ou `completed`.

Lors de l'ouverture d'un chapitre, la dernière session active du même utilisateur, matière et numéro est reprise. Sinon une session vide est créée.

### CourseProgressState

`core.models.CourseProgressState` stocke des états JSON plus riches, indexés par `user` et `course_key`.

Il sert notamment à :

- sauvegarder la position dans les anciennes pages physique/sciences sociales;
- sauvegarder `subchapter_idx` et `chunk_idx` dans le mode hybride;
- conserver des états de lecteur indépendants de l'historique de messages.

La contrainte `unique_together = (user, course_key)` garantit un état par utilisateur et cours.

## 6. Mode hybride

Le mode hybride est le chemin préféré pour les contenus déjà préparés.

Au chargement :

1. `chapter_cours_view` récupère le payload hybride.
2. Le serveur lit l'état sauvegardé dans `CourseProgressState` avec `_hybrid_course_key(subject, num)`.
3. Il borne les index de sous-chapitre et de chunk pour éviter les index invalides.
4. Le template reçoit `hybrid_course_json` et `hybrid_state_json`.
5. `initHybridCourse()` affiche le chunk courant.
6. `renderHybridCurrentChunk()` normalise le texte, rend le Markdown, protège le HTML par DOMPurify quand disponible et programme le rendu KaTeX.
7. L'élève peut revenir en arrière, marquer un chunk compris ou demander une clarification.
8. `persistHybridProgress()` envoie les index à `api/cours/hybrid-progress/`.

Le contenu pré-généré ne consomme pas d'appel IA lors de la lecture. Une clarification de l'élève peut toutefois passer par le tuteur.

## 7. Mode conversationnel IA

Quand `HYBRID_MODE` est faux :

1. La page rend l'historique visible de `CourseSession.messages`.
2. Si une session existante contient un dernier message utilisateur, le navigateur peut relancer automatiquement la réponse.
3. Une nouvelle session affiche une porte de démarrage pour éviter un appel IA au simple chargement.
4. L'élève peut écrire, envoyer une image ou utiliser les boutons de suggestion.
5. Le navigateur appelle `api/cours/chat/` avec `session_id`, message, contexte de leçon, sous-chapitre, chunk et éventuellement image encodée.
6. Le serveur vérifie que la session appartient à `request.user`.
7. Il reconstruit le contexte depuis la session et le chapitre, puis appelle le pipeline IA/local.
8. Les messages et `progress_step` sont sauvegardés dans la session.

Les réponses sont affichées en Markdown avec support des tableaux, du code et des formules. Les contenus générés sont normalisés pour retirer des artefacts JSON et des titres techniques inutiles.

## 8. APIs du cours

Toutes les APIs de cours utilisent les routes de `core/urls.py`.

### APIs communes

- `POST /dashboard/api/cours/chat/` -> `api_course_chat` : question au tuteur, contexte de cours, image facultative.
- `POST /dashboard/api/cours/hybrid-progress/` -> `api_course_hybrid_progress` : sauvegarde des index hybride.
- `POST /dashboard/api/cours/reset/` -> `api_course_reset` : remise à zéro d'une session/progression.
- `POST /dashboard/api/cours/section/` -> `api_course_section` : génération/chargement d'une section.
- `POST /dashboard/api/cours/miniquiz/` -> `api_course_miniquiz` : mini-quiz de cours.
- `POST /dashboard/api/course-question/` -> `api_course_question` : ancienne question IA de cours.
- `POST /dashboard/api/chapter-summary/` -> `api_chapter_summary` : résumé IA d'un chapitre.
- `POST /dashboard/api/generate-exercises/` -> `api_generate_exercises` : génération d'exercices liés au cours.

### APIs spécialisées

- `POST /dashboard/api/cours/physique/section/` -> section physique.
- `POST /dashboard/api/cours/physique/miniquiz/` -> mini-quiz physique.
- `GET/POST /dashboard/api/cours/physique/exercises/` -> exercices physique.
- `POST /dashboard/api/cours/physique/progress/` -> progression physique.
- `POST /dashboard/api/cours/sc-social/progress/` -> progression sciences sociales.
- `POST /dashboard/api/cours/sc-social/correct/` -> correction IA d'une réponse en sciences sociales.

Les APIs sont identifiées par `core.ai_usage.infer_feature_from_path` comme feature `course`, afin de centraliser le suivi des appels IA.

## 9. Limites IA et coûts

`core/premium.py` définit les limites générales :

- gratuit : 2 messages chat par jour;
- gratuit : 1 quiz par jour;
- gratuit : 1 exercice par jour selon le compteur configuré;
- gratuit : 3 réponses Extra Bète par jour;
- cours : chapitre 1 par matière;
- plafond global : 50 appels API IA par jour pour tous les utilisateurs, premium inclus.

Le premium contourne les limites spécifiques de chat/quiz/exercice/Extra Bète, mais pas le plafond global d'appels IA.

Les contenus hybrides et les assets mis en cache évitent normalement un appel IA à chaque ouverture. Les clarifications, mini-quiz, corrections et générations à la demande peuvent consommer des tokens.

## 10. Invités

`_is_guest(request)` permet une session de démonstration distincte de l'authentification Django.

Dans le hub, les matières/chapter cards de la démo reçoivent `guest_locked`. Dans le chapitre, le serveur redirige les invités vers le hub avec `guest_blocked=chapters` au lieu de leur donner une session persistante.

Le template de chapitre affiche une bannière démo et désactive l'entrée IA. Le hub propose une modale d'inscription.

Il faut conserver cette distinction : un invité n'est pas un compte gratuit. Le compte gratuit doit pouvoir ouvrir le chapitre 1 et utiliser les fonctions prévues par son quota.

## 11. Correction appliquée pendant cet audit

Le hub chargeait le profil avec `.only(...)` sans `plan_expiration`, puis le template dérivait `IS_PREMIUM_COURS` de `profile.is_premium`. Cela rendait l'état d'affichage dépendant d'une lecture différée du modèle et pouvait laisser un utilisateur premium avec tous les chapitres visuellement verrouillés, même si le serveur connaissait correctement son abonnement.

Correction :

- `cours_view` inclut explicitement `plan_expiration` dans le profil sélectionné;
- `cours_view` passe `is_premium` calculé par `core.premium.is_premium`;
- `cours.html` utilise ce booléen explicite pour son JavaScript;
- le contrôle serveur existant dans `chapter_cours_view` reste actif comme autorité;
- les sélecteurs CSS partagés du sidebar et de `_page_info.html` ont été renforcés pour empêcher des règles globales de casser les titres de sections et les descriptions en bas de page.

## 12. Points de vigilance pour la suite

1. Les anciennes pages matière et la nouvelle vue chapitre coexistent. Toute nouvelle fonctionnalité de cours doit préciser quel chemin elle vise.
2. Les textes de plusieurs templates annoncent parfois un accès gratuit plus large que la règle serveur actuelle. Les messages UI doivent rester alignés avec `can_access_chapter`.
3. L'accès premium dépend de `plan_expiration`; une activation de paiement qui ne met pas ce champ à jour laissera naturellement l'utilisateur gratuit.
4. Le plafond global de 50 appels IA s'applique aussi aux premiums. Un premium peut donc être bloqué par la limite IA sans que cela signifie que ses chapitres sont verrouillés.
5. Les endpoints doivent conserver la vérification de propriété de session et l'authentification Django. Le frontend ne doit jamais être considéré comme une protection.
6. Les états JSON sont bornés et nettoyés côté serveur dans les endpoints de progression; cette validation doit être conservée pour éviter des payloads arbitraires.
7. Une couverture de tests dédiée devrait vérifier au minimum : gratuit chapitre 1 autorisé, gratuit chapitre 2 refusé, premium chapitre N autorisé, invité redirigé, et hub premium rendu avec `IS_PREMIUM_COURS=true`.

## 13. Fichiers principaux à lire

- `core/views.py` : vues, chargement des chapitres et APIs.
- `core/premium.py` : règles d'accès et quotas.
- `core/models.py` : `CourseSession`, `CourseProgressState`, assets et chapitres historiques.
- `accounts/models.py` : `UserProfile.plan_expiration` et `is_premium`.
- `accounts/payments.py` : activation après paiement.
- `core/urls.py` : routes des pages et APIs.
- `templates/core/cours.html` : hub, drawers et verrous visuels.
- `templates/core/chapter_cours.html` : lecteur hybride et tuteur IA.
- `templates/core/generic_cours.html` : ancien lecteur générique.
- `templates/core/physique.html` et `templates/core/sc_social.html` : lecteurs spécialisés.
- `static/css/v2/layout.css` : sidebar et descriptions de page communes.
- `core/exo_loader.py` / `core/pdf_loader.py` : chargement de contenu selon le chemin utilisé.
