# Gamekiro Community Platform — Build Plan

Two devs (Sambhav, Prabesh), both full-stack. Every phase after Foundation splits into two
independent vertical slices (models → views → templates) so both people always have something
real to click through at the **Friday 9PM review**. Planning only — no application code in this pass.

---

## Stack & Architecture Decisions

- **Django + PostgreSQL.** Use the built-in `auth.User` as-is — do **not** swap `AUTH_USER_MODEL`.
  Extend it with a 1:1 `Profile` model instead. Swapping the user model is a one-way door that has
  to happen before the first migration and buys nothing here; a `Profile` gets everything we need
  (avatar, socials, karma link, games played) with zero migration risk.
- **No Django Channels / WebSockets in v1.** Notifications and DMs are DB-backed with HTMX polling
  (e.g. poll `/notifications/unread-count/` every 15–20s, swap a nav badge). This is the single
  biggest complexity call in this plan, made deliberately:
  - Channels means an ASGI server, a Redis-backed channel layer, and async-safe ORM access —
    a second runtime model for two people to keep straight on top of Celery, Postgres, and Tailwind.
  - DB-polling ships the *same user-visible feature* (near-real-time badge/DM updates) at a fraction
    of the operational surface, and every part of it is stuff you already know how to debug.
  - **Documented as a Phase 2+ upgrade**: if usage later demands true push (e.g. a live "typing…"
    indicator), swap the polling endpoint for a Channels consumer behind the same URL — the
    Notification/Message models don't change, only the transport does.
- **Celery + Redis** for work that's genuinely async and not needed synchronously in the request:
  karma recalculation, badge-award rule checks, nightly karma safety-net recompute. **Not** used for
  basic "create a Notification row" — that's a plain synchronous DB write inside the triggering view.
- **UI: server-rendered Django templates + Tailwind + HTMX.** No SPA framework, no DRF/API layer —
  there's no separate frontend to serve. HTMX handles the two places that need it (notification
  polling, live-ish reaction counts) without hand-written JS.
- **Media storage:** avoid Google Cloud Storage — recommend an S3-compatible bucket instead
  (**Cloudflare R2** is a good fit here since the account is already set up) via `django-storages`,
  same code path in dev (local filesystem) and prod (swap `DEFAULT_FILE_STORAGE`).

### Python/Django Package List

| Purpose | Package |
|---|---|
| Web framework | `Django` |
| Postgres driver | `psycopg[binary]` |
| Env/config | `django-environ` |
| Async jobs | `celery`, `redis`, `django-celery-beat`, `django-celery-results` |
| Polling/partial updates | `django-htmx` |
| Images | `Pillow` |
| Tags (game tags) | `django-taggit` |
| Prod static files | `whitenoise` |
| Prod media (S3/R2) | `django-storages` |
| WSGI server | `gunicorn` |
| DB URL parsing (Railway/Render style) | `dj-database-url` |
| Form styling w/ Tailwind | `django-widget-tweaks` (or `crispy-tailwind` if forms get complex) |
| Tests | Django `TestCase` / `pytest-django` |
| Lint/format | `ruff`, `black` |
| Error tracking (optional) | `sentry-sdk` |
| Local dev QoL (optional) | `django-extensions` |

Tailwind itself via the **standalone Tailwind CLI binary**, not `django-tailwind`/npm — one less
Node toolchain for a two-person team to keep in sync.

### Follow vs. mutual-friend model — decision

**Chosen: asymmetric Follow** (Twitter-style, not Facebook-style mutual friends).

- No pending/accept state machine → one model (`Follow`), one notification type (*"X followed
  you"*), instead of request/accept/reject/cancel × 2 notification types.
- Feed/notification logic stays a single query: "who follows me" is the audience, full stop — no
  branching on friendship status.
- Fits a forum/gamer-community pattern (like Reddit) better than a closed friends network.
- **Consequence:** DMs are *not* gated by mutual-follow in v1 — any authenticated user can message
  any other. Flagged as an open risk below (spam potential); "message requests" gated by
  follow-back is a clean Phase 2+ addition if it becomes a problem.

---

## App/Module Breakdown

### `core`
Shared, no user-facing feature of its own.
- `TimestampedModel` (abstract: `created_at`, `updated_at`)
- Base templates (`base.html`, nav, footer), 404/500 templates, home page view
- Shared context processor (e.g. unread-notification count for the nav badge)

### `accounts`
- `Profile` (1:1 `User`; bio, avatar, discord_username, steam_id, psn_id, other social links)
- `ProfileGame` (through model: `profile` FK, `game` FK → `games.Game`, `status` choices
  [Playing / Completed / Wishlist / Dropped]) — powers "games played"

### `forum`
- `Category` (name, slug, description, order)
- `Thread` (category FK, author FK, title, slug, is_pinned, is_locked, created_at)
- `Post` (thread FK, author FK, body, created_at, edited_at) — this *is* the comment/reply unit

### `reactions` (small, cross-cutting)
- `Reaction` (user FK, generic FK target [`content_type` + `object_id`], `created_at`,
  unique-together on user+target)
- Built generic on purpose so both `forum.Post`/`Thread` and `games.Review` can reuse it without
  either app depending on the other.

### `social`
- `Follow` (`follower` FK User, `following` FK User, `created_at`, unique-together)

### `messaging`
- `Message` (`sender` FK, `recipient` FK, `body`, `is_read`, `created_at`) — 1:1 DMs only in v1, no
  group conversations (flagged below)

### `notifications`
- `Notification` (`recipient` FK, `actor` FK nullable, `verb` choices, generic FK `target`
  nullable, `is_read`, `created_at`)
- One helper: `notify(recipient, actor, verb, target=None)` — every other app calls this from its
  own signal handlers; the `notifications` app never edits another app's files.

### `reputation`
- `KarmaEvent` (`user` FK, `points`, `reason`, generic FK to the triggering object, `created_at`) —
  append-only log
- `KarmaScore` (1:1 `User` (or `Profile`), `score` int, `updated_at`) — denormalized cache,
  recalculated by a Celery task

**Karma formula (v1, documented — confirm with team):**
```
karma = (likes_received_on_posts   × 2)
      + (likes_received_on_threads × 3)
      + (threads_created           × 1)
      + (reviews_written           × 1)
```
No "accepted answer" term — see Open Risks, that concept was never actually scoped for the forum
and would mean a real feature addition (thread type, an "accept" action restricted to the thread
author), not just a formula tweak.

### `gamification`
- `Badge` (name, description, icon, `criteria_key`, is_active)
- `UserBadge` (`user` FK, `badge` FK, `awarded_at`)
- Rule checks run inside the same Celery task that recalculates karma (cheap: check thresholds
  after the score updates)

### `games`
- `Platform` (name) — simple lookup (PC, PlayStation, Xbox, Switch, Mobile)
- `Game` (title, slug, platforms M2M, tags via `django-taggit`, cover_image, description,
  release_year)
- `Review` (`game` FK, `user` FK, `rating` 1–5, `body`, `created_at`, unique-together
  game+user — one review per user per game)

---

## Roadmap

7 phases total (1 shared foundation week + 6 parallel weeks). Each table is the scannable
overview; the checklist under it is what actually gets checked off in git as work lands.

### Phase 0 — Foundation (shared) — Week 1

Done together, front-loaded early in the week so there's slack before Friday — nobody should be
touching this after Wednesday night.

- [ ] `django-admin startproject`, repo structure, `requirements.txt`/`pyproject.toml`
- [ ] Postgres running locally + `django-environ` settings split (base/dev/prod)
- [ ] `core` app: `TimestampedModel`, base template + nav + footer, 404/500, home view
- [ ] Tailwind standalone CLI wired into the build/watch step
- [ ] Auth wiring: Django's built-in login/logout/password-reset views + templates
- [ ] `accounts.Profile` model + post-save signal to auto-create one per new `User`
- [ ] Signup view (username/email/password → creates `User` + `Profile`)
- [ ] Deploy pipeline: Railway or Render (Postgres add-on) + GitHub Actions (lint + test on PR) +
      auto-deploy `main` to staging
- [ ] Celery + Redis wired up (even with zero real tasks yet) so later phases don't fight infra

**Friday demo (shared):** Both people log into the *staging URL* (not localhost), see the styled
home page and nav, and open their own empty profile page.

---

### Phase 1 — Community Core I — Week 2

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Forum: categories, threads, posts | Post a new thread, reply to it, see it listed by category |
| B | Prabesh | Social graph + Profile finish | Follow another user from their profile, see follower/following counts update |

**Track A — Sambhav**
- [ ] `forum` app: `Category`, `Thread`, `Post` models + migrations
- [ ] Forum home (category list) → category detail (thread list) → thread detail (posts) views
- [ ] Thread create form, reply (Post create) form
- [ ] Templates styled with Tailwind, pagination on thread list

**Track B — Prabesh**
- [ ] `social` app: `Follow` model, follow/unfollow view (toggle button)
- [ ] Followers / following list pages
- [ ] Finish `accounts.Profile` edit form (bio, avatar, Discord/Steam/PSN, other links)
- [ ] Public profile page shows bio, socials, follower/following counts

**Sync points this week:** Thread/post authors link out to profile pages — lock the profile URL
pattern (`/u/<username>/`) by **Wednesday** so Track A's templates don't need rework.

---

### Phase 2 — Community Core II — Week 3

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Reactions + forum polish | Like a post, see the count update instantly (HTMX) |
| B | Prabesh | Direct messaging | Send and receive a DM between two accounts, unread badge updates |

**Track A — Sambhav**
- [ ] `reactions` app: generic `Reaction` model (works against any model via `ContentType`)
- [ ] Like/unlike button on `Post` and `Thread`, HTMX partial swap for the count
- [ ] Forum polish: search/filter within a category, thread pagination cleanup

**Track B — Prabesh**
- [ ] `messaging` app: `Message` model (sender/recipient/body/is_read)
- [ ] Inbox view (grouped by conversation partner), thread view, send-message form
- [ ] Unread-count badge in nav, HTMX polling

**Sync points this week:** `reactions` needs to work against `games.Review` in Phase 4 without
changes — Track A shares the exact `Reaction`/helper signature with the team by **Wednesday** so
nobody has to touch that app again later.

---

### Phase 3 — Reputation & Game Directory — Week 4

Reputation is sequenced here on purpose: it needs Reactions (done in Phase 2) to have real signal
to compute from. Game directory has no such dependency, so it runs in parallel rather than making
Track B wait on Track A.

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Reputation / karma | Like someone's post live, karma score on their profile ticks up within seconds |
| B | Prabesh | Game directory (listings) | Browse games, filter by platform/tag, open a game detail page |

**Track A — Sambhav**
- [ ] `reputation` app: `KarmaEvent`, `KarmaScore` models
- [ ] Celery task: recalc karma on `Reaction` created/deleted signal (+ nightly safety-net recompute
      via `django-celery-beat`)
- [ ] Karma score displayed on profile page

**Track B — Prabesh**
- [ ] `games` app: `Platform`, `Game` models (cover image via Pillow, tags via `django-taggit`)
- [ ] Game directory browse page (filter by platform/tag), game detail page

**Sync points this week:** Track A's karma display needs a slot on the profile template Track B
already owns — agree on a small `profile_stats.html` include by **Wednesday** so both aren't
editing `profile.html` directly the same week.

---

### Phase 4 — Gamification & Reviews — Week 5

Gamification is sequenced after Reputation for the same reason: badge thresholds read from karma.

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Gamification (badges) | Trigger an action live, badge appears on profile in real time |
| B | Prabesh | Game reviews/ratings | Submit a star review on a game, see it listed + average rating update |

**Track A — Sambhav**
- [ ] `gamification` app: `Badge`, `UserBadge` models
- [ ] Rule checks (e.g. "50 karma", "10 posts", "first thread") run in the same Celery task as
      karma recalc
- [ ] Achievements showcase section on profile page

**Track B — Prabesh**
- [ ] `games.Review` model (rating 1–5 + body, one per user per game)
- [ ] Review form on game detail page, review list, average-rating aggregate
- [ ] Wire Track A's `reactions` app onto reviews ("helpful" like) — no changes needed to that app,
      confirming the Phase 2 contract held

**Sync points this week:** Both touch `games/templates/game_detail.html`? No — Track B owns that
template fully; Track A's badge work stays inside `profile.html`/`gamification`. No file overlap
this week; just confirm the `Reaction` generic-FK contract by **Monday** before Track B builds on it.

---

### Phase 5 — Notifications & Profile Completion — Week 6

By this point every producing app (forum, social, messaging, reputation, gamification) already
exists — Track A wires the generic system in, and each producer's `notify()` call is a one-line
addition inside that app's *own* signal file, not a cross-team edit.

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Notification center | Trigger a follow/reply/DM live, watch the nav badge and notification list update |
| B | Prabesh | "Games played" + messaging polish | Add a game to "Currently Playing" on profile; DM unread badge + delete conversation |

**Track A — Sambhav**
- [ ] `notifications` app: `Notification` model + `notify()` helper
- [ ] Notification center page (list, mark-as-read), nav bell with HTMX polling for unread count
- [ ] Add the `notify()` call into each existing app's own signal handler: new follower, thread
      reply, DM received, reaction received, badge earned

**Track B — Prabesh**
- [ ] `accounts.ProfileGame` model + "Games I Play" section on profile (status: Playing / Completed
      / Wishlist / Dropped), links into the game directory
- [ ] Messaging polish: unread badge refinement, delete/archive a conversation

**Sync points this week:** Track A is adding `notify()` calls inside apps Track B doesn't currently
own (forum, social from earlier phases) — those are additive one-liners in existing signal files,
agree on the exact `verb` string names by **Wednesday** so nothing collides mid-week.

---

### Phase 6 — MVP Hardening — Week 7

No new features — this is the "actually shippable" week. Ends with the MVP.

| Track | Owner | Focus | Friday Demo |
|---|---|---|---|
| A | Sambhav | Moderation & search | Report a post, admin hides it; search returns matching threads |
| B | Prabesh | Onboarding & polish | New signup → guided profile setup → styled empty states, live on production |

**Track A — Sambhav**
- [ ] Django admin customized for every app (list filters, search fields)
- [ ] Basic content report/flag (post or review) → admin can hide
- [ ] Postgres full-text search across threads/posts
- [ ] Basic posting rate-limit (spam guard)

**Track B — Prabesh**
- [ ] Signup polish: welcome email via Celery, first-run profile setup prompt
- [ ] Mobile/responsive pass across all templates
- [ ] Styled empty states (no threads yet, no games yet, etc.), 404/500 review
- [ ] Seed-data management command for demo/staging, production deploy + smoke-test checklist

**Sync points this week:** Both are touching many templates for polish — claim apps by directory
(A: `forum/`, `games/` admin+search; B: everything under `templates/` for empty-state pass) and
call out any shared file (`base.html`) before editing it.

---

## Open Risks / Decisions Needed

- **Steam/PSN "games played" scope:** assumed **manual, user-entered** status tags (Playing /
  Completed / Wishlist), *not* a live Steam/PlayStation API integration pulling real playtime or
  achievement data. A real API sync is a substantially bigger feature (API keys, OAuth-ish linking
  flows, rate limits, background sync jobs) and would blow past a 1-week slice — confirm this
  assumption before Phase 5, since it changes that phase's scope significantly.
- **No "accepted answer" in the forum:** the original brief's karma example mentioned it, but
  categories/threads/posts/comments/reactions never asked for a Q&A "mark as answer" mechanic.
  Karma formula above omits it. If it's actually wanted, it's a real forum feature (thread type,
  an accept action gated to the thread author) — flag before Phase 3 if so.
- **Reactions are like-only, no downvote**, matching "like/upvote style, not full emoji
  reactions." A downvote system adds real moderation surface (brigading, negative-karma spirals) —
  keeping it upvote-only for v1, downvote as a documented Phase 2+ option.
- **DMs are open to any user, not gated by follow/mutual-follow** — flagged as a spam risk. Cheap
  v1 mitigation: the Phase 6 rate-limiter also covers messages. "Message requests" gated by
  follow-back is a clean Phase 2+ addition if abuse shows up.
- **1:1 DMs only, no group conversations** in v1 — confirm this is acceptable for launch.
- **Media storage:** recommending Cloudflare R2 (S3-compatible) over Google Cloud Storage for
  avatars/cover art — confirm the account/budget for that before Phase 3 needs image uploads.
- **Hosting platform** assumed to be Railway or Render (managed Postgres, simple GitHub Actions
  deploy) — neither was specified; confirm before Phase 0 locks in the deploy pipeline.
- **Effort balance assumes Sambhav and Prabesh are roughly equal full-stack strength.** If one is
  meaningfully stronger on either end, the "equal effort" pairing per phase (not equal item count)
  may need rebalancing — worth a quick gut-check after Phase 1's first real demo.
- **Weekly email digest** was mentioned in the original brief only as an example of "genuinely
  async" work, not a required feature — left out of the roadmap; easy Celery-beat addition later
  if wanted.
