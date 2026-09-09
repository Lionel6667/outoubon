#!/usr/bin/env bash
# Idempotent bootstrap for the BacIA Django project (Cloud Agent environment).
set -euo pipefail

cd "$(dirname "$0")/.."

# ── Python virtualenv ──
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

# ── Local dev .env (SQLite, DEBUG) — only created if missing ──
if [ ! -f .env ]; then
  SECRET=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
  cat > .env <<EOF
SECRET_KEY=${SECRET}
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
USE_LOCAL_DB=1
AI_USAGE_LOG=false
EOF
fi

# ── Database + static assets ──
python manage.py migrate --noinput
python manage.py collectstatic --noinput
