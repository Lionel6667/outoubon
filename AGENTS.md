# AGENTS.md — OU TOU BON (BacIA Django)

Consignes permanentes pour les agents travaillant sur ce dépôt.

## Tests & preuves

- **Ne jamais enregistrer de vidéo** pour démontrer les changements. Préférence permanente du mainteneur.
  - Utiliser uniquement des **captures d'écran** et/ou des **logs/sorties de tests** comme preuves.
  - Les tests eux-mêmes restent obligatoires : valider les changements (tests automatisés, vérif navigateur par captures, ou logs), mais sans capture vidéo.

## Sécurité

- Ne **jamais** committer de secrets (clés API, secrets webhook, mots de passe, clés privées) dans le code, les templates, les scripts ou les logs.
  - Les valeurs sensibles se lisent via des variables d'environnement (section **Secrets**). Ex. paiement : `MONCASH_SECRET_KEY`, `MONCASH_WEBHOOK_SECRET`.
  - Les clés **publiques**/publishable (`pk_...`, VAPID public) ne sont pas sensibles et peuvent servir de valeur par défaut de réglage.

## Environnement de dev

- Django 4.2. Créer un venv puis `pip install -r requirements.txt`.
- Dev local : `.env` avec `USE_LOCAL_DB=1` (SQLite), `DEBUG=True`, un `SECRET_KEY` aléatoire.
- Lancer : `python manage.py runserver 0.0.0.0:8001`.
- Voir `scripts/cloud-agent-install.sh` pour un bootstrap idempotent.
