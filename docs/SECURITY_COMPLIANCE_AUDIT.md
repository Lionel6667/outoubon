# Audit sécurité et conformité

Date de l'audit : 15 septembre 2026

Ce document est une base technique et opérationnelle. Il ne remplace pas l'avis d'un avocat, d'un DPO ou d'un expert sécurité indépendant. L'objectif est de fermer les risques concrets du dépôt sans promettre une invulnérabilité impossible.

## Sources officielles consultées

- Django Security: https://docs.djangoproject.com/en/5.2/topics/security/
- OWASP Top 10: https://owasp.org/Top10/
- OWASP ASVS: https://owasp.github.io/www-project-application-security-verification-standard/
- CNIL, cookies et traceurs: https://www.cnil.fr/fr/cookies-et-traceurs-que-dit-la-loi
- CNIL, droits des personnes: https://www.cnil.fr/fr/les-droits-pour-maitriser-vos-donnees-personnelles
- RGPD, texte officiel EUR-Lex: https://eur-lex.europa.eu/eli/reg/2016/679/oj

## Ce que le site doit publier

Avant l'ouverture commerciale, le site doit avoir des pages accessibles sans compte et des liens visibles depuis l'inscription, le pied de page et le paiement :

1. Mentions légales : identité de l'éditeur, forme/adresse ou siège, moyen de contact, directeur de publication, hébergeur et coordonnées.
2. Politique de confidentialité : responsable du traitement, catégories de données, finalités, base légale par finalité, destinataires/sous-traitants, transferts hors pays, durées de conservation, sécurité, droits et moyen de les exercer, réclamation auprès de l'autorité compétente.
3. Conditions générales d'utilisation : compte, règles d'utilisation, contenus élèves, modération, disponibilité, suspension, propriété intellectuelle, support et responsabilité.
4. Conditions générales de vente/abonnement : prix et devise, renouvellement ou absence de renouvellement, durée, activation, paiement MonCash, échec/remboursement, résiliation, preuve d'achat et support.
5. Politique cookies/traceurs : liste par finalité, nom, fournisseur, durée, type et lien de retrait.
6. Contact vie privée : adresse de demande d'accès, rectification, effacement, opposition, limitation et portabilité.
7. Politique mineurs : âge minimum, rôle des parents/tuteurs et traitement des données scolaires. Le site vise des lycéens : ce point doit être décidé et rédigé avec conseil juridique local.
8. Accessibilité et signalement sécurité : canal de contact, délai de réponse et procédure de divulgation responsable.

Informations qui manquent dans le dépôt et doivent être fournies par l'éditeur avant rédaction finale : nom légal, pays d'établissement, adresse, email juridique, politique de remboursement, âge minimum, responsables/sous-traitants exacts, durées de conservation et outil analytics choisi.

## Cookies et consentement

La CNIL rappelle que le consentement doit être préalable pour les traceurs non strictement nécessaires, libre, spécifique, éclairé et univoque. Refuser doit être aussi simple qu'accepter, le retrait doit rester accessible et le consentement doit pouvoir être prouvé. Accepter les CGU ne remplace pas le consentement cookies.

À appliquer :

- Ne déposer aucun analytics, publicité, pixel social ou fingerprinting non nécessaire avant le choix.
- Laisser fonctionner les cookies strictement nécessaires : session, CSRF, authentification demandée, préférence de langue et sécurité.
- Ajouter une bannière avec `Tout accepter`, `Tout refuser` et `Personnaliser`, mêmes niveaux visuels.
- Enregistrer version, date, finalités et choix; permettre la modification depuis chaque page.
- Bloquer les scripts tiers jusqu'au consentement et documenter les fournisseurs.
- Le fingerprinting appareil utilisé pour la sécurité doit être documenté comme finalité de sécurité; ne pas le réutiliser pour du tracking marketing sans consentement.

## Risques techniques constatés

### Critiques

- `accounts/views.py` utilise un token persistant accessible au JavaScript/localStorage et un endpoint `csrf_exempt` de vérification. Une XSS peut voler une session longue durée. Remplacer progressivement par un cookie HttpOnly opaque, rotation, révocation et durée bornée.
- `core/admin_panel.py` doit être vérifié : un panneau protégé uniquement par une URL secrète ou un hash rapide n'est pas une authentification suffisante. Exiger `is_staff`/`is_superuser`, mot de passe Django ou SSO, MFA pour les fonctions financières, expiration courte et journalisation.
- Les webhooks/paiements doivent vérifier signature, référence, montant, devise et idempotence sous transaction avec verrou DB. Une confirmation concurrente ne doit pas doubler abonnement ou commission.

### Élevés

- Uploads : vérifier taille, type réel par décodage, extension allowlist, dimensions et quota. Ne jamais faire confiance à `Content-Type`; servir les médias utilisateurs depuis un domaine isolé si possible.
- Rate limiting : le stockage mémoire ne couvre pas plusieurs workers/instances; utiliser Redis/cache partagé en production. Ne faire confiance à `X-Forwarded-For` que si `REMOTE_ADDR` est un proxy explicitement configuré.
- Hosts/CSRF : les domaines ngrok ne doivent pas être approuvés en production. Cette correction est maintenant appliquée : ils nécessitent `DEBUG` ou `ALLOW_NGROK_HOSTS=1`.
- CSP : supprimer progressivement `unsafe-eval` et réduire les CDN; remplacer les scripts inline par nonce/hash. Ajouter `form-action 'self'`, `frame-ancestors 'none'`, `base-uri 'self'` et `upgrade-insecure-requests` quand les scripts le permettent.
- Liens cadeau : ne jamais exposer école, téléphone ou données personnelles dans une page bearer-token publique; minimiser le prénom, expirer le lien, limiter le polling et lier les statuts au token.

### Moyens

- Session d'un an : décider explicitement si “se souvenir de moi” est nécessaire; sinon réduire l'âge et révoquer toutes les sessions lors d'un changement de mot de passe.
- Ajouter `SECURE_PROXY_SSL_HEADER` uniquement avec un proxy maîtrisé, vérifier HTTPS réel et garder HSTS après validation des sous-domaines.
- Ajouter des logs structurés sans mots de passe, tokens, images ou contenu pédagogique privé.
- Vérifier dépendances avec `pip-audit`, secrets avec un scanner CI, et images/conteneurs avec un scanner de vulnérabilités.
- Sauvegarder la base chiffrée, tester la restauration, limiter les accès Railway/Postgres et séparer les environnements.

## Correctifs appliqués dans ce lot

- Désactivation par défaut des hôtes/origines ngrok hors développement.
- Le rate limiter utilise `REMOTE_ADDR` par défaut et ne lit `X-Forwarded-For` que pour les proxies explicitement listés dans `TRUSTED_PROXY_IPS`.
- Les routes réelles `/login/` et `/api/auth/token/verify/` sont désormais limitées, en plus des anciens fragments API.

## Tests de sécurité à ajouter

- Production avec `DEBUG=False`: host non autorisé, ngrok non autorisé, HTTPS/cookies/HSTS.
- Brute-force login et vérification token: 429 après le seuil, reset de fenêtre.
- `X-Forwarded-For` forgé depuis un client direct: ne doit pas contourner la limite.
- CSRF sur chaque POST authentifié et absence d'exception non justifiée.
- Upload réel avec MIME falsifié, fichier trop grand, extension double et image polyglotte.
- Admin : utilisateur normal, staff, session expirée, première initialisation et opération financière.
- Paiement : mauvaise signature, mauvais montant, double webhook concurrent, répétition idempotente.
- Cadeau : expiration, accès sans token, minimisation des données publiques.
- Cookies : aucun traceur non essentiel avant consentement, refus réversible et preuve de version/finalité.

## Feuille de route recommandée

1. Fermer l'administration et l'authentification persistante.
2. Sécuriser paiement/webhooks et uploads.
3. Passer le rate limit sur Redis et ajouter les tests CI.
4. Installer le CMP cookies et publier les pages légales avec les informations réelles de l'éditeur.
5. Durcir CSP et retirer les exceptions inline par zones.
6. Faire un pentest externe avant de présenter le site comme sécurisé.

Aucune application ne peut garantir “99% contre toutes les attaques récentes”. La cible réaliste est une réduction mesurée du risque, des contrôles indépendants, des logs/alertes et une capacité de correction rapide.
