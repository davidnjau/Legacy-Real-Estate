# Django API

This module is the Django backend for the Legacy Real Estate platform.
Its initial endpoint is `GET /api/v1/health`.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py test
python manage.py runserver
```

For the same checks used in CI, install the development requirements and run:

```bash
pip install -r requirements-dev.txt
ruff check --select E4,E7,E9,F .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
python manage.py test
```

The migration checks and tests require PostgreSQL, as configured by the
environment variables below.

The example database settings are for local development only. Do not use
the live Supabase database as the Django database. Existing records must be
copied through an explicit, tested migration plan before any production
cutover.
