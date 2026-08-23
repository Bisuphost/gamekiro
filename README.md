# Gamekiro Community Platform

Django project. See [PLAN.md](PLAN.md) for the full build plan, architecture decisions, and
phased roadmap.

## Project layout

```
gamekiro/settings/    base.py + dev.py + prod.py
gamekiro/celery.py    Celery app instance
core/                 TimestampedModel, base template, nav, footer, 404/500, home view
accounts/             Profile, ProfileGame, signup, profile page, auto-create-Profile signal
forum/                Category, Thread, Post
reactions/            generic Reaction (Post/Thread/Review)
social/               Follow
messaging/            1:1 DMs
notifications/        Notification + notify() helper
reputation/           KarmaEvent, KarmaScore
gamification/         Badge, UserBadge
games/                Platform, Game, Review
static_src/           Tailwind input.css
static/css/           Tailwind build output (generated, not committed)
```

Every app past `core` and `accounts` is still an empty scaffold — no models/views/templates
implemented yet beyond Phase 0. That's the next step per PLAN.md's phase breakdown.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

brew install postgresql@16 redis
brew services start postgresql@16
brew services start redis
createuser gamekiro --pwprompt
createdb -O gamekiro gamekiro

cp .env.example .env

./scripts/install-tailwind.sh
./scripts/build-css.sh

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

`manage.py` defaults to `gamekiro.settings.dev`; `wsgi.py`/`asgi.py` default to
`gamekiro.settings.prod`. Local dev falls back to sqlite if `DATABASE_URL` is unset in `.env`.

## Tailwind

Standalone CLI binary, no Node toolchain. `./scripts/watch-css.sh` rebuilds
`static/css/output.css` on change while developing; `./scripts/build-css.sh` does a one-off
minified build.

## Celery

```bash
celery -A gamekiro worker -l info
celery -A gamekiro beat -l info
```

No real tasks exist yet — this is infra-only for now (Phase 3+ adds karma/badge tasks).

## CI

`.github/workflows/ci.yml` runs `ruff`, `black --check`, and `manage.py test` (against a
Postgres service container) on every PR and push to `main`.

## Still open

- Deploy pipeline (hosting platform, GitHub Actions auto-deploy to staging) — deliberately not
  set up yet; see PLAN.md's open risks on hosting.
- Media storage (Cloudflare R2) — settings are wired in `prod.py` but untested against a real
  bucket.
