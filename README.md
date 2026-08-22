# eKuraa Community Platform

Django project scaffold. See [PLAN.md](PLAN.md) for the full build plan, architecture
decisions, and phased roadmap.

## Project layout

```
gamekiro/settings/    # base.py (shared) + dev.py + prod.py — see PLAN.md Phase 0
core/                 # shared, no user-facing feature of its own
accounts/             # Profile, ProfileGame
forum/                # Category, Thread, Post
reactions/             # generic Reaction (Post/Thread/Review)
social/                # Follow
messaging/             # 1:1 DMs
notifications/          # Notification + notify() helper
reputation/            # KarmaEvent, KarmaScore
gamification/           # Badge, UserBadge
games/                  # Platform, Game, Review
```

Each app above is currently an empty `startapp` scaffold (models/views/admin are the
Django defaults) — no models, views, or templates have been implemented yet. That's the
next step per PLAN.md's phase breakdown.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in DATABASE_URL etc.

python manage.py migrate
python manage.py runserver
```

`manage.py` defaults to `gamekiro.settings.dev` (sqlite fallback if `DATABASE_URL` is
unset). `wsgi.py`/`asgi.py` default to `gamekiro.settings.prod` for deployment.

## Notes

- Local dev falls back to sqlite if `DATABASE_URL` isn't set in `.env`; set it to a
  Postgres URL to match production (see PLAN.md — Postgres is the target DB).
- Celery/Redis config is wired in settings but no tasks exist yet (Phase 3+).
- Tailwind (standalone CLI) and the deploy pipeline are not set up yet — see PLAN.md
  Phase 0 checklist for what's still open.
