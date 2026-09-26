# GameKiro Marketplace — Implementation Plan

**Status:** Planning complete, implementation not started
**Source documents:** `.claude/marketplace/prd.md`, `.claude/marketplace/architecture.md`
**Target:** A secure digital game-key marketplace as a modular subsystem inside the existing GameKiro Django platform.

## Context

GameKiro is a working Django 6.0.8 community platform (forum, games directory, news, messaging, notifications, reputation, gamification). It has **no commerce code of any kind** — no orders, no prices, no payment provider, and not a single `DecimalField` anywhere.

The marketplace introduces money, valuable digital inventory, and external payment integrations into a codebase that currently has **zero role/permission infrastructure, zero row locking, zero atomic counters, and only three `transaction.atomic()` blocks in total**. Every one of those gaps must be filled deliberately rather than assumed.

This plan integrates the marketplace as a new `marketplace` Django app that reuses GameKiro's existing auth, user model, game catalog, design system, moderation, and notification infrastructure, and avoids rewriting anything that already works.

### Decisions locked in before planning

| Decision | Choice | Consequence |
|---|---|---|
| Payment provider | **Provider-agnostic adapter + `MockProvider`** | Checkout/orders/fulfilment are fully buildable and testable now; a real provider is one adapter class later. PRD §52 stays open without blocking. |
| Marketplace theme | **Reuse the existing site theme** | PRD §33's palette (`#0B0D12`/`#7C3AED`) is superseded by the current tokens. PRD's *intent* (dark, premium, purple used sparingly) is already met. No new colour tokens. |
| Fulfilment runtime | **Synchronous inside the verified webhook + cron retry sweep** | Works on the current cPanel host, which starts no Celery worker. Stays Celery-compatible if a worker is added later. |
| MVP key supply | **Manual admin import only**; supplier layer ships as abstraction + mock adapter | Satisfies PRD §25 ("do not build production fulfilment around an unverified key source"). |

---

## 1. Existing architecture findings

Everything below was verified against the codebase, not assumed.

### 1.1 Platform

| Area | Finding |
|---|---|
| Framework | Django **6.0.8**, Python 3.14 local / 3.13 cPanel / 3.12 CI (**three different versions**) |
| Settings | `gamekiro/settings/{base,dev,prod}.py`, `django-environ`, `.env` read even in prod |
| Apps | `core, accounts, forum, reactions, moderation, social, messaging, notifications, reputation, gamification, games, news` |
| User model | **Stock `django.contrib.auth.models.User`** — no `AUTH_USER_MODEL`. `accounts.Profile` auto-created by a `post_save` signal |
| Auth | `django.contrib.auth.urls` at `/accounts/`, custom `accounts.signup`, `django-axes` lockout (5 fails / 30 min), **`AXES_ENABLED = False` in dev so lockout is untested** |
| DB | PostgreSQL via `psycopg` 3 — but `DATABASE_URL` **silently falls back to SQLite, including under `prod.py`** |
| Async | Celery + Redis configured; **no worker is started by the deploy** |
| Frontend | Server-rendered Django templates + htmx 2.0.10; Tailwind v4 standalone CLI (no Node) |
| Deploy | cPanel via `.cpanel.yml`; CI is GitHub Actions (lint + test only, no deploy) |
| Tests | 152 tests, per-app `tests.py`, `django.test.TestCase` only |

### 1.2 Authorization — the most important gap

There is **no role or permission infrastructure at all**:

- `is_superuser`, `has_perm`, `permission_required`, `user_passes_test`, `staff_member_required`, `Group`, custom `Meta.permissions` — **zero occurrences each**.
- `is_staff` appears in exactly one place: `news/views.py:22` (unpublished-article preview).
- Authorization today is three patterns only: `@login_required`; ownership-scoped lookups (`get_object_or_404(ProfileGame, pk=pk, profile=request.user.profile)`); and that single `is_staff` check.

PRD §23 requires Customer / Support / Marketplace Manager / Finance / Administrator. **All of it must be built from scratch.**

### 1.3 Concurrency — no existing precedent

| Mechanism | Occurrences in codebase |
|---|---|
| `transaction.atomic` | 3 (`forum/views.py:146,176`, `seed_demo.py:18`) |
| `select_for_update` | **0** |
| `F()` atomic increment | **0** (the two `F()` uses are a search-rank expression and a check constraint) |
| `transaction.on_commit` | **0** |
| `CheckConstraint` | 1 (`social.Follow.prevent_self_follow`) |
| `UniqueConstraint` | 9 |

Every counter in the project is computed on read or fully recomputed (`reputation.services.recalculate_karma`). **Inventory allocation will be the first genuinely concurrency-sensitive code in this repository.**

Existing rate limiting (`forum/services.py::is_rate_limited`) is a DB `COUNT` over a 1-minute window — an explicit check-then-act race, and **not safe for purchase or key-reveal endpoints**.

### 1.4 Catalog

`games.Game` is a **pure catalog entity**: `title, slug (globally unique), description, cover_image, platforms (M2M), tags (taggit), created_at, updated_at`. It has **no price, SKU, stock, publisher, developer, release date, or external store ID**.

`games.Review` already exists (`rating` 1–5, `body`, unique per `(user, game)`), is reaction-enabled and moderation-aware. `games.Platform` is a **hardware** platform (PC, Switch) — *not* an activation platform (Steam, Epic), which is a distinct concept the marketplace needs.

`accounts.ProfileGame` already has a `WISHLIST` status — a pre-existing hook for "want to buy".

### 1.5 Conventions the marketplace must follow

- **Services** are plain module-level functions taking model instances or `pk` ints (never `request`), with UPPERCASE module constants and registry dicts (`REACTABLE_MODELS`, `REPORTABLE_MODELS`, `TARGET_LOADERS`, `CRITERIA_CHECKS`).
- **Views** are function-based with `@login_required` / `@require_POST` stacked in that order; htmx branches on `request.htmx` and returns `_`-prefixed partials; redirects validated via `url_has_allowed_host_and_scheme`.
- **Templates** extend `base.html` (only 4 blocks: `title`, `extra_head`, `content`, `extra_scripts`), use the `glass-panel … rounded-xl border border-border-subtle/60 … hover:-translate-y-0.5` card idiom, `components/empty_state.html`, and the canonical widget_tweaks input class string.
- **Models** inherit `core.models.TimestampedModel`; indexes named `<shortmodel>_<purpose>_idx`.

### 1.6 Pre-existing defects that block or endanger the marketplace

| # | Finding | Impact |
|---|---|---|
| **F1** | `prod.py` sets `STATICFILES_STORAGE` / `DEFAULT_FILE_STORAGE` — **both removed in Django 5.1**, replaced by `STORAGES`. They are silently ignored. | Product images/screenshots would be written to **ephemeral cPanel disk**, not R2. Must fix before any marketplace media exists. |
| **F2** | `boto3` is not installed and not in `requirements.txt` | R2 storage cannot work even once F1 is fixed. |
| **F3** | `.cpanel.yml` copies a **hardcoded app list** and runs **no `migrate`, no `collectstatic`, no worker** | A new `marketplace` app would silently not deploy; marketplace migrations would never run. |
| **F4** | `EMAIL_BACKEND` defaults to the **console backend** and `prod.py` does not override it | Order confirmations and key-delivery emails would print to a log, not send. **Hard launch blocker.** |
| **F5** | `DATABASE_URL` silently falls back to SQLite under `prod.py` | A misconfigured deploy would run a payment system on a file DB. |
| **F6** | Terms of Service §6 states: *"we do not sell any paid features… we will update these Terms and publish a separate refund policy before introducing any paid features."* (`core/templates/core/terms_of_service.html:82-88`) | A **written commitment** that launch triggers. There is currently no refund policy page. |
| **F7** | No `CSRF_TRUSTED_ORIGINS`, no `CACHES`, no `LOGGING` config | Affects proxied checkout origins, cache-backed limits, and payment audit logging. |
| **F8** | `accounts/tasks.py:20` builds `{SITE_URL}/accounts/setup/` but the real route is `/setup/` | Welcome-email link 404s. Pre-existing; same class of bug to avoid in order emails. |
| **F9** | `gamekiro/celery.py` defaults `DJANGO_SETTINGS_MODULE` to **dev** settings | A production worker started without an explicit env var would run dev settings. |

### 1.7 Django behaviours that silently break the obvious design

Each of these was **verified against the vendored Django 6.0.8**, not assumed. Each one invalidates an approach that would otherwise look correct and pass CI.

| # | Behaviour | Why it matters here |
|---|---|---|
| **D1** | **`select_for_update()` is a silent no-op on SQLite.** `django/db/models/sql/compiler.py:840` reads `if self.query.select_for_update and features.has_select_for_update:` — SQLite inherits `has_select_for_update = False` and never overrides it, so the whole block (including every `NotSupportedError` raise) is skipped and the `FOR UPDATE` clause is **never emitted**. No exception, no warning. | A lock-based allocator would pass every CI test on PostgreSQL 16 and **silently double-sell keys** on SQLite. This is the single most dangerous fact for this feature and the reason locking is rejected as the primary mechanism. |
| **D2** | **Conditional (`condition=`) `UniqueConstraint`s are silently not created on MySQL/MariaDB.** `supports_partial_indexes = False` (`mysql/features.py:48`) and `base/schema.py:1793` gates creation on it; Django emits only a `checks.Warning`. | **cPanel's native database is MySQL/MariaDB** — a likelier production backend here than SQLite. A partial unique index therefore cannot be the structural backstop. It must be a **plain** unique index. |
| **D3** | **`DecimalField` is float round-tripped on SQLite** — read back via `decimal.Context(prec=15).create_decimal_from_float(...)` (`sqlite3/operations.py:326`). | Settles the money representation: **integer minor units**, never `Decimal`. A webhook amount check must be an exact integer comparison. |
| **D4** | **`QuerySet.update()` bypasses `Model.save()`, so `auto_now` never fires.** | `TimestampedModel.updated_at` will silently lie on every compare-and-swap unless `updated_at=now` is passed explicitly in each `.update()`. |
| **D5** | **Catching `IntegrityError` without a nested `transaction.atomic()` poisons the transaction on PostgreSQL** — subsequent queries raise `TransactionManagementError`. | The webhook dedupe (insert-then-fallback-to-get) must wrap the failing insert in its own `atomic()` savepoint. |
| **D6** | **SQLite's default `BEGIN DEFERRED` fails lock upgrades without honouring `busy_timeout`.** A read-then-write transaction returns `SQLITE_BUSY` immediately. `transaction_mode` in `OPTIONS` (Django 5.1+, present here) fixes it. | The checkout transaction reads (price) then writes (reserve). Without `transaction_mode: "IMMEDIATE"` + WAL it fails intermittently under exactly the concurrency being designed for. |
| **D7** | **No `CACHES` is configured**, so Django falls back to `LocMemCache`, which under Passenger is **per-process**. | Any `cache.add()` lock or cache-backed idempotency would appear to work in dev and grant the lock to every worker in production. **Every mutual-exclusion primitive here must be a database row CAS.** |
| **D8** | **`SECURE_SSL_REDIRECT = True` in `prod.py`** turns an `http://` webhook URL into a 301 — which **drops the POST body**. | The provider endpoint must be registered as `https://` and its host must be in `DJANGO_ALLOWED_HOSTS`. Belongs in the smoke test. |
| **D9** | **SQLite sets `test_db_allows_multiple_connections = False`** (`sqlite3/features.py:15`). | Threaded concurrency tests are meaningless on SQLite; they must run on PostgreSQL (CI already provisions `postgres:16`). |

---

## 2. Proposed marketplace architecture

A single new Django app, `marketplace/`, organised into the modules the architecture doc prescribes — implemented as service modules, matching this codebase's existing plain-function service convention rather than introducing classes or a framework.

```
marketplace/
├── models/                  catalog.py  orders.py  inventory.py  payments.py  promotions.py  ops.py
├── services/
│   ├── pricing.py           server-side price & discount computation (single source of truth)
│   ├── cart.py              cart mutation + validation
│   ├── checkout.py          order creation, inventory reservation
│   ├── inventory.py         atomic allocation, reservation, release
│   ├── fulfillment.py       key assignment, entitlement creation, idempotent
│   ├── payments.py          provider-agnostic orchestration
│   ├── webhooks.py          signature verify → dedupe → dispatch
│   ├── keys.py              encrypt/decrypt, masked display, access logging
│   ├── coupons.py           validation + redemption limits
│   ├── audit.py             append-only audit events
│   └── flags.py             kill switches (cached singleton)
├── providers/               base.py (interface)  mock.py  stripe.py (later)
├── suppliers/               base.py (interface)  manual.py  mock.py
├── views/                   storefront.py  cart.py  checkout.py  orders.py  library.py  webhooks.py  admin_views.py
├── templates/marketplace/
├── management/commands/     run_marketplace_jobs.py  import_keys.py
├── checks.py                deploy-time system check: refuse SQLite in production
└── tests/                   test_*.py
```

**Boundary rules**

1. Views never compute money. All pricing goes through `services/pricing.py`.
2. Nothing outside `services/keys.py` ever touches key plaintext.
3. Payment providers and suppliers are reached **only** through their adapter interfaces.
4. Every state transition that touches money or inventory happens inside an explicit `transaction.atomic()` block.

**Mounting** — mount as `path("store/", include("marketplace.urls"))`, i.e. **with an explicit prefix, deliberately not under the `path("")` catch-all** every other app uses. Two reasons: `accounts.urls` is included last and ends with a greedy `path("<str:username>/")` that would otherwise shadow single-segment marketplace paths; and the webhook route must be unambiguous and impossible for another app to shadow. `/store/` also avoids colliding with the existing `/games/` directory.

---

## 3. Database models and schema changes

All new models live in `marketplace`. Money is stored as **integer minor units** (`*_minor`, `PositiveBigIntegerField`) — never float, never `Decimal` arithmetic in views — with `currency` (ISO-4217, `CharField(max_length=3)`) stored alongside every monetary record for auditability. This is a greenfield decision: the project has no existing money representation.

### 3.1 Catalog (new)

| Model | Key fields | Notes |
|---|---|---|
| `ActivationPlatform` | `name`, `slug` unique | Steam/Epic/GOG/Ubisoft — **distinct from `games.Platform`** (hardware) |
| `Region` | `name`, `code` unique | Global / EU / NA / restricted |
| `Product` | `game` FK→`games.Game` **PROTECT**, `slug` unique, `activation_platform` FK, `region` FK, `edition`, `unit_price_minor`, `compare_at_price_minor` null, `currency`, `status` (DRAFT/ACTIVE/UNLISTED/ARCHIVED), `is_purchasable`, `description` (sanitised HTML), `max_per_order`, `publisher`, `developer`, `released_on` | `UniqueConstraint(game, activation_platform, region, edition)` |
| `ProductImage` | `product` FK, `image`, `sort_order` | `upload_to="marketplace_products/"`, reuse `core.validators.validate_image_file_size` |

**Reuse, not rebuild:** genres/categories reuse `games.Game.tags` (taggit, already installed). Community rating and reviews reuse `games.Review`. Cover art reuses `games.Game.cover_image`. A `Product` is a *sellable SKU of an existing Game*, which is why `PROTECT` is used — deleting a Game that has sold must be impossible.

### 3.2 Cart (new)

| Model | Key fields | Notes |
|---|---|---|
| `Cart` | `user` OneToOne, timestamps | **Authenticated users only** for MVP — avoids session-merge complexity and anonymous-cart abuse. "Add to cart" while logged out redirects to login with `?next=`. |
| `CartItem` | `cart` FK, `product` FK, `quantity` | `UniqueConstraint(cart, product)`. **Stores no price** — price is always recomputed server-side. |

### 3.3 Orders (new)

| Model | Key fields |
|---|---|
| `Order` | `reference` unguessable public string, unique (**never expose sequential PKs** — PRD §26 order enumeration), `user` FK PROTECT, `status`, `subtotal_minor`, `discount_minor`, `tax_minor`, `total_minor`, `currency`, `coupon` FK null, `provider`, `provider_session_id`, `payment_reference`, `reservation_expires_at`, `paid_at`, `fulfilled_at`, `review_reason` |
| `OrderItem` | `order` FK, `product` FK PROTECT, `unit_price_minor`, `line_group` UUID, `delivered_at`, plus **denormalised snapshots** `product_title`, `platform_name`, `region_name` so historical orders survive catalog edits |

**`OrderItem` represents exactly one unit / one key.** Quantity 3 creates three rows, grouped for display by `line_group`. This is a deliberate modelling choice: it is what makes the structural anti-double-allocation backstop expressible as a plain `OneToOneField` (§3.4) rather than a conditional constraint that MySQL would silently discard (D2).

`UniqueConstraint(fields=["provider", "provider_session_id"], name="uniq_order_provider_session")` prevents two orders binding the same checkout session.

`Order.status` (`TextChoices`, extending PRD §14 with the states the failure matrix requires): `PENDING_PAYMENT, PAID, PAID_LATE, PARTIALLY_FULFILLED, FULFILLED, PAYMENT_FAILED, FULFILLMENT_FAILED, EXPIRED, CANCELLED, REFUNDED, PARTIALLY_REFUNDED, CHARGEBACK, NEEDS_REVIEW`.

Transitions are governed by a single declarative `ALLOWED_TRANSITIONS` table plus one helper, `order_transition(order, *, to, allowed_from, **extra)`, which is **itself a compare-and-swap with a rowcount check** — the same primitive as key allocation. A returned `False` means "someone else already moved it", which is the idempotent no-op path, not an error.

### 3.4 Inventory (new) — the security-critical table

| Field | Purpose |
|---|---|
| `product` FK PROTECT | |
| `status` | `AVAILABLE, RESERVED, ASSIGNED, DELIVERED, REDEEMED, REFUNDED, REVOKED, INVALID` (PRD §15) |
| `encrypted_key` | Fernet ciphertext — **plaintext is never stored, logged, or serialised** |
| `key_fingerprint` | `sha256(plaintext)`, **unique** — blocks duplicate imports without storing plaintext |
| `masked_hint` | e.g. `XXXX-XXXX-7Q4B` for admin/user display without reveal |
| `reserved_by_item` **`OneToOneField(OrderItem, null=True, SET_NULL)`** | The structural backstop — see below |
| `claim_token` UUID null | Ties a reservation to the attempt that made it |
| `reserved_at`, `reservation_expires_at`, `sold_at`, `revoked_at` | |
| `import_batch` FK, `imported_by` FK | provenance |

Constraints and indexes — **all portable across PostgreSQL, MySQL/MariaDB and SQLite**:

- `UniqueConstraint(fields=["product", "key_fingerprint"], name="uniq_gamekey_product_key_hash")` — plain, unconditional
- `CheckConstraint(condition=…, name="ck_gamekey_status_allocation_consistent")` — status and allocation must agree (`available` ⇒ no item; `reserved`/`sold` ⇒ has item). Supported on all three backends (MySQL 8.0.16+). Note Django 6.0 removed the `check=` kwarg — use `condition=`.
- `Index(fields=["product", "status"])` — drives availability counts and candidate selection
- `Index(fields=["status", "reservation_expires_at"])` — drives the expiry sweep

**Why `OneToOneField` and not a partial `UniqueConstraint`:** the O2O creates a *plain* `UNIQUE` index on `reserved_by_item_id`, which exists on all three backends, and NULL is distinct on all three so unlimited unallocated keys coexist. A `UniqueConstraint(..., condition=...)` would be **silently dropped on MySQL** (D2) — exactly the likeliest cPanel backend. The two halves of the guarantee:

- *One key cannot go to two items* — structural: the key row has exactly one `reserved_by_item_id` slot, protected against overwrite by the CAS predicate `reserved_by_item__isnull=True`.
- *One item cannot receive two keys* — the `UNIQUE` index raises `IntegrityError` **at the database**, not in Python.

**Guard rail:** `GameKey.save()` raises unless called by the allocation service. A stray `key.status = "sold"; key.save()` writes the whole row and destroys the CAS guarantee — it is the single way to defeat this design, so it is blocked at the model.

Supporting models: `InventoryImportBatch` (uploader, product, counts, source, created_at) and `KeyAccessLog` (user, key, order, action, ip, user_agent, created_at) — the latter doubles as the **audit trail and the rate-limit source** for key reveals.

### 3.5 Payments (new)

| Model | Key fields |
|---|---|
| `Payment` | `order` FK, `provider`, `provider_payment_id` (unique per provider), `status` (PRD §13: `CREATED, PENDING, PAID, FAILED, CANCELLED, REFUNDED, DISPUTED, EXPIRED`), `amount_minor`, `currency`, `idempotency_key` unique, raw provider status, timestamps |
| `WebhookEvent` | `provider`, `event_id`, `event_type`, `order` FK null, `amount_minor`, `currency`, `payload` JSON, `status` (RECEIVED/PROCESSED/DUPLICATE_TRANSITION/ORPHANED/REJECTED_AMOUNT/FAILED), **`attempts`**, **`locked_until`**, `processed_at`, `last_error` |
| `Refund` | `order` FK, `payment` FK, `amount_minor`, `reason`, `status`, `requested_by` FK, `provider_refund_id`, timestamps |

`UniqueConstraint(fields=["provider", "event_id"], name="uniq_webhookevent_provider_event_id")` is the replay/duplicate-delivery defence — but note it is **not sufficient on its own**: the row must also carry `attempts`/`locked_until`/`processed_at` so an interrupted processing attempt can be re-driven rather than swallowed as a duplicate (§9.3). `JSONField` is native on all three backends in Django 6.0.

### 3.5b Job and outbox models (required by the no-worker design)

| Model | Key fields | Purpose |
|---|---|---|
| `FulfillmentJob` | **`order` OneToOne**, `status` (pending/running/done/failed/needs_review), `attempts`, `run_after`, `locked_until`, `last_error` | Transactional outbox. The O2O plain-unique index is the **exactly-once backstop** — at most one fulfilment job can exist per order. |
| `OutboundEmail` | `status`, `attempts`, `run_after`, `locked_until`, payload | Decouples the slow SMTP call from the webhook request (§9.4). |
| `CronLease` | `name` unique, `holder` UUID, `expires_at` | Portable stand-in for a PostgreSQL advisory lock; stops cron runs stacking. Required because cache locks are illusory here (D7). |
| `OrderAuditLog` | `order`, `actor` FK null, `action`, `detail` JSON | Every non-automatic transition (manual key replacement, refund, review resolution) — what makes human review auditable. |

### 3.6 Entitlements, promotions, ops (new)

| Model | Key fields |
|---|---|
| `Entitlement` | `user` FK, `product` FK, `order_item` **OneToOne**, `granted_at`, `revoked_at` null — the user library, and the authority for "verified purchase" |
| `Coupon` | `code` unique uppercase, `kind` (PERCENT/FIXED), `value`, `max_redemptions`, `max_per_user`, `min_subtotal_minor`, `starts_at`, `ends_at`, `is_active`, product/tag scoping |
| `CouponRedemption` | `coupon` FK, `user` FK, `order` FK, `UniqueConstraint(coupon, order)` — enforces limits **and** provides the audit trail |
| `WishlistItem` | `user`, `product`, `UniqueConstraint(user, product)` |
| `MarketplaceSettings` | singleton (pk=1): `marketplace_enabled`, `purchases_enabled`, `fulfillment_enabled`, `maintenance_message` — PRD §49 kill switch |
| `SupplierIntegration` | `name`, `slug`, `adapter_path`, `enabled`, non-secret `config` JSON (**credentials stay in env**) |
| `AuditLog` | `actor` FK null, `action`, `target_type`, `target_id`, `summary`, `metadata` JSON, `ip`, `created_at` — **append-only**, PRD §32 |

### 3.7 Changes to existing models

| File | Change | Type |
|---|---|---|
| `games/models.py` | Optionally add `Review.is_verified_purchase` (denormalised bool, set from `Entitlement`) | **Modifies existing** |
| `notifications/models.py` | Add marketplace verbs to `Notification.Verb`. **`verb` is `max_length=20`** — `order_confirmed` (15) and `payment_failed` (14) fit; anything longer needs a column widening migration | **Modifies existing** |

No other existing model changes are required.

---

## 4. Backend modules and services

| Module | Responsibility | Dependencies | Type |
|---|---|---|---|
| `services/flags.py` | `marketplace_enabled()`, `purchases_enabled()`, `fulfillment_enabled()` — cached singleton reads | `MarketplaceSettings` | New |
| `services/pricing.py` | `price_cart(cart, coupon)` → line items + subtotal/discount/tax/total. **The only place money is computed.** Pure function, heavily unit-tested | Catalog, coupons | New |
| `services/cart.py` | `add_item`, `update_quantity`, `remove_item`, `validate_cart` (availability, `max_per_order`, product status) | Catalog, inventory | New |
| `services/checkout.py` | `create_order(user, cart, coupon)` — recompute prices, validate, create `Order`+`OrderItem`s, **reserve inventory**, all in one transaction | pricing, inventory, coupons | New |
| `services/inventory.py` | `available_count(product)`, `reserve_keys(order_item, qty)`, `release_reservation(order)`, `assign_keys(order_item)` — see §8 | Inventory models | New |
| `services/fulfillment.py` | `fulfill_order(order_id)` — **idempotent**, transactional, sets `FULFILLED` or `REQUIRES_REVIEW` | inventory, entitlements, notifications | New |
| `services/payments.py` | `begin_payment(order)`, `apply_payment_result(...)` — provider-agnostic | providers/*, orders | New |
| `services/webhooks.py` | `handle(provider_slug, raw_body, headers)` — verify → dedupe → dispatch | payments, fulfillment | New |
| `services/keys.py` | `encrypt(plaintext)`, `decrypt(key)`, `fingerprint(plaintext)`, `mask(plaintext)`, `record_access(...)` | `cryptography` (**new dep**) | New |
| `services/coupons.py` | `validate_coupon(code, user, cart)` — expiry, limits, scope, min-subtotal | promotions | New |
| `services/audit.py` | `log(actor, action, target, **meta)` | `AuditLog` | New |
| `notifications/services.py` | **Extend `TARGET_LOADERS`** with marketplace targets, else notification targets silently render blank | — | **Modifies existing** |
| `reactions/views.py` | Optionally add `"marketplace": {"product"}` to `REACTABLE_MODELS` | — | **Modifies existing** |
| `moderation/services.py` | Optionally add marketplace reviews to `REPORTABLE_MODELS` | — | **Modifies existing** |

---

## 5. Endpoints and authorization

All under `/store/`. `app_name = "marketplace"` (the sidebar active-state logic keys off `request.resolver_match.app_name`).

| Route | Method | Auth | Notes |
|---|---|---|---|
| `/store/` | GET | Public | Homepage: featured, trending, deals, new releases |
| `/store/browse/` | GET | Public | Search + filters + sort (PRD §9) |
| `/store/p/<slug>/` | GET | Public | Product page; **indexable**, OG tags |
| `/store/cart/` | GET | Login | Server-priced every render |
| `/store/cart/add/` | POST | Login | Rate-limited |
| `/store/cart/update/`, `/remove/` | POST | Login | Ownership-scoped |
| `/store/coupon/validate/` | POST | Login | htmx; **strict rate limit** (PRD §27) |
| `/store/checkout/` | GET | Login | Order review; recomputed total |
| `/store/checkout/confirm/` | POST | Login | Creates order + reserves keys + begins payment. **Strict rate limit + idempotent** |
| `/store/orders/` | GET | Login | Own orders only |
| `/store/orders/<uuid:public_id>/` | GET | Login | **Ownership enforced in the queryset**, UUID not PK |
| `/store/library/` | GET | Login | Entitlements only for `request.user` |
| `/store/library/<uuid:entitlement_id>/reveal/` | **POST** | Login | Key reveal — see §12.4 |
| `/store/wishlist/`, `/toggle/` | GET/POST | Login | |
| `/store/webhooks/<provider>/` | POST | **None** (`@csrf_exempt`) | **Signature-verified**; the first `csrf_exempt` in the codebase |
| `/store/manage/…` | GET/POST | **Permission-gated** | Admin surfaces (§11) |

**Authorization rules**

1. Every owned-object lookup is **scoped in the queryset** (`Order.objects.get(public_id=…, user=request.user)`), following the existing `ProfileGame` pattern — never fetch-then-check.
2. Orders are addressed by **UUID**, never sequential PK.
3. Staff/admin routes use Django permissions via `permission_required`, never `is_staff` alone.
4. Private pages (`cart`, `checkout`, `orders`, `library`) emit `<meta name="robots" content="noindex">` (PRD §37).

---

## 6. Frontend pages and components

Reuses the existing design system exactly — **no new colour tokens**.

| Page | Template | Reuses |
|---|---|---|
| Store home | `marketplace/home.html` | Hero pattern from `core/home.html`, `animate-fade-in-up`, 2/3 + 1/3 grid |
| Browse | `marketplace/browse.html` | `games/game_list.html` filter-panel + card-grid pattern |
| Product | `marketplace/product_detail.html` | `games/game_detail.html` layout; `mx-auto max-w-4xl` |
| Cart | `marketplace/cart.html` | Divided-list pattern |
| Checkout | `marketplace/checkout.html` | `max-w-2xl` form pattern |
| Orders / Order detail | `marketplace/orders.html`, `order_detail.html` | Divided-list + `empty_state.html` |
| Library | `marketplace/library.html` | Card grid |
| Wishlist | `marketplace/wishlist.html` | Card grid |

**New shared components**

- `templates/components/product_card.html` — follows the existing card idiom (`glass-panel … hover:-translate-y-0.5 hover:border-accent-end/40`), with the first-letter fallback for missing art and a discount chip (`rounded-full bg-accent-start/20 … text-accent-end`).
- `marketplace/_key_reveal.html` — htmx fragment, self-replacing via `hx-swap="outerHTML"`.
- `marketplace/_cart_summary.html` — re-rendered server-side on every cart mutation.

**Required edits to existing files**

| File | Change |
|---|---|
| `static_src/input.css` | **Add `@source "../marketplace/templates";`** — without this, Tailwind emits none of the new utility classes. Then rebuild via `./scripts/build-css.sh`. |
| `templates/components/sidebar_nav.html` | Add a Store entry + `request.resolver_match.app_name == 'marketplace'` active branch |
| `templates/components/bottom_nav.html` | **Already full at 5 slots** — decide what Store displaces on mobile |
| `templates/base.html` | Optional cart-count badge (htmx poller, following the unread-count pattern) |

**Conventions to honour:** progressive enhancement (every htmx control is also a real link/form), `_`-prefixed partials, htmx CSRF handled globally by `base.html`, and `clean_rich_text_body` from `forum/forms.py` for any rich-text product description (`|safe` is only sound because of it).

---

## 7. Cart → checkout → payment → fulfilment flow

```
Browse → Add to cart (server validates availability + max_per_order)
   ↓
Cart page — prices recomputed server-side on every render
   ↓
Checkout review — pricing.price_cart() is authoritative; client sends product IDs + qty ONLY
   ↓
POST /checkout/confirm/   ─ one transaction ─────────────────────────┐
   • re-validate cart, product status, coupon                        │
   • recompute total (never trust anything posted)                   │
   • create Order (PENDING_PAYMENT) + OrderItems with price snapshots│
   • RESERVE keys atomically (§8) with reservation_expires_at        │
   • create Payment (CREATED) with an idempotency key                │
   └──────────────────────────────────────────────────────────────────┘
   ↓
Redirect to provider (amount comes from Order.total_minor, never the client)
   ↓
Provider webhook → verify signature → dedupe on provider_event_id
   → assert amount + currency + order match → Order: PAID
   → fulfil synchronously in the same request (§8/§9)
   ↓
Keys RESERVED → ASSIGNED → DELIVERED · Entitlements created · Order FULFILLED
   ↓
Email + in-app notification (no full key in either) → Library
```

**Reservation timing — a deliberate, documented deviation.** PRD §14 lists inventory reservation *after* payment verification. This plan reserves at **order creation**, before payment, because reserving afterwards makes "customer paid, no key available" a routine occurrence — precisely the failure PRD §14/§39 say must become a manual-review state. Pre-payment reservation with a short TTL converts that into a clean "out of stock" message *before* money moves. Abandoned-cart lock-up is bounded by `reservation_expires_at` and the sweep command (§8.4).

---

## 8. Inventory and atomic allocation

**Constraint:** the design cannot assume PostgreSQL. `forum/models.py` gates schema on `connection.vendor`, the project was deliberately made portable, and the production backend on cPanel is most likely **MySQL/MariaDB**, possibly SQLite. Critically, `select_for_update()` does not fail on SQLite — it is **silently ignored** (D1).

### 8.0 Database policy — state it, then don't depend on it

**Recommendation: require PostgreSQL in production; keep SQLite working for dev and schema-portability CI; ensure the design is also correct on MySQL/MariaDB.**

The portability precedent (commit `3917ea7`) made *search* optional — a degradable feature where the fallback is merely worse UX. Money and inventory are not degradable: a wrong answer is a financial loss. The precedent does not transfer. SQLite is additionally unfit here because cPanel home directories are often NFS-backed (where SQLite's POSIX byte-range locking is unreliable), because it serialises **all** site writes, and because of D3.

Make it loud rather than silent: a deploy-time system check in `marketplace/checks.py` that inspects `settings.DATABASES["default"]["ENGINE"]` as a string (no connection needed) and raises `checks.Error` when `DEBUG is False` and the engine is SQLite unless an explicit override is set. That fails `manage.py check` and therefore `migrate`.

**But the design below is correct on all three backends regardless** — the policy is a safety net, not a load-bearing assumption.

### 8.1 Primary mechanism — conditional-UPDATE compare-and-swap

Claiming uses a single conditional `UPDATE` whose **row count is the proof of ownership**:

```python
claimed = GameKey.objects.filter(
    pk=key_id, status=AVAILABLE, reserved_by_item__isnull=True,
).update(
    status=RESERVED, reserved_by_item=item, claim_token=claim,
    reserved_at=now, reservation_expires_at=expires,
    updated_at=now,            # D4: .update() bypasses auto_now — must be explicit
)
# claimed == 1 → this request owns the key.  claimed == 0 → someone else won; try the next candidate.
```

Candidates are gathered with `.order_by("pk").values_list("pk", flat=True)[:CANDIDATE_FANOUT]` (≈25) and claimed one at a time until the required count is reached, or the pool is exhausted → `OutOfStock`. Deterministic ordering keeps tests reproducible and any future multi-row path deadlock-free. Log a warning when the fanout is exhausted *while stock still exists* — that means the fanout is undersized, not that the product sold out.

**Why this is safe on each backend, without locks:**

| Backend | Guarantee |
|---|---|
| **PostgreSQL** (READ COMMITTED) | The second `UPDATE` blocks on the first's row lock, then EvalPlanQual **re-evaluates the `WHERE` against the newly committed row version** — not its original snapshot. It sees `status='reserved'`, matches nothing, returns 0. If the first rolls back instead, the row is still available and the second wins. Correct in both directions. |
| **MySQL/InnoDB** (REPEATABLE READ) | `UPDATE` performs a *current* read (not a snapshot read) and takes an X lock per examined row. The waiter re-reads the latest committed version, the predicate fails, 0 rows. |
| **SQLite** | At most one writer exists for the whole database at any instant. The statements cannot interleave; the loser evaluates its `WHERE` against state that already contains the winner's write. |

### 8.2 Structural backstop

Correctness does not rest on application logic alone — see §3.4: the `OneToOneField` plain unique index, the status/allocation `CheckConstraint`, the `(product, key_fingerprint)` unique constraint, and the `save()` guard rail.

### 8.3 Why row locking is rejected as the primary mechanism

Not merely "unavailable on SQLite" — **silently ignored** (D1). A `select_for_update(skip_locked=True)` allocator would pass every CI test on PostgreSQL and double-sell on SQLite with no error, no warning, and no failing test. It also costs an extra round trip, still needs the same backstop, and requires a `connection.features` branch that duplicates the CAS path anyway — leaving two implementations of which only one is ever exercised.

Where `connection.features.has_select_for_update_skip_locked` is true it may be added later purely as a contention optimisation. **The CAS remains the correctness guarantee on every backend.**

### 8.4 SQLite transaction mode — a required settings change

Django's default `atomic()` on SQLite issues `BEGIN DEFERRED`, taking only a SHARED lock at first read. The checkout transaction reads (price the cart) then writes (reserve keys); that lock upgrade can collide with another connection and SQLite returns `SQLITE_BUSY` **immediately, without honouring `busy_timeout`**. The single-statement CAS is immune, but the surrounding transaction is not.

```python
# gamekiro/settings/base.py, after DATABASES = {...}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"].setdefault("OPTIONS", {}).update({
        "transaction_mode": "IMMEDIATE",        # Django 5.1+; verified present in 6.0.8
        "init_command": (
            "PRAGMA journal_mode=WAL;"
            "PRAGMA synchronous=NORMAL;"
            "PRAGMA busy_timeout=5000;"
            "PRAGMA foreign_keys=ON;"
        ),
    })
```

### 8.5 Checkout transaction — exact ordering

One `transaction.atomic()`, in this order:

1. Opportunistic expiry sweep scoped to this product (cheap; self-heals if cron is dead).
2. Re-read `Product`, compute `total_minor` **server-side**. Never trust a posted price.
3. Create `Order` (`PENDING_PAYMENT`, `reservation_expires_at = now + TTL`).
4. `bulk_create` the `OrderItem`s — one row per unit.
5. Allocate keys. `OutOfStock` → the whole transaction rolls back → **reservations release automatically**. This is precisely why allocation must share the transaction with order creation; splitting them is the difference between instant release and waiting for the sweeper.
6. Create the provider checkout session **outside** the transaction (via `transaction.on_commit`). Never hold an open transaction across a network round trip — on SQLite that holds the global write lock for the duration and stalls the entire site.

Set the provider session's expiry to **exactly** `order.reservation_expires_at`; divergent clocks manufacture the late-payment case for free.

### 8.6 Reservation expiry — order first, then keys

TTL 30 minutes. The sweep must expire the **order** before releasing its keys:

1. `PENDING_PAYMENT ∧ expired → EXPIRED` (CAS).
2. Then release keys belonging to orders that actually reached `EXPIRED`, with `status=RESERVED` re-checked in the `WHERE`.

**The ordering is the whole design.** Releasing keys first would let a `payment_succeeded` webhook land in the gap, find `PENDING_PAYMENT`, CAS to `PAID`, and fulfil an order whose keys had just been handed to someone else — the worst possible outcome. Expiring the order first means a concurrent webhook's paid-CAS matches nothing and falls into the explicit late-payment branch (§9.5). The sweep and a webhook may interleave at any point and the result is always one of two well-defined states.

Collect ids then filter `pk__in=` rather than updating across a join — this avoids MySQL's "can't specify target table for update in FROM clause" and keeps one statement shape on all backends. Chunk at 500.

---

## 9. Payment and webhook architecture

### 9.1 Provider adapter

```python
class PaymentProvider(Protocol):
    slug: str
    def create_payment(self, order, idempotency_key) -> PaymentIntent  # redirect/client ref
    def verify_webhook(self, raw_body: bytes, headers: Mapping) -> VerifiedEvent
    def refund(self, payment, amount_minor, idempotency_key) -> RefundResult
```

Ships with `MockProvider` (deterministic, used by tests and local dev). A real provider is added as one module under `providers/` plus env credentials — no changes to orders, inventory, or fulfilment.

### 9.2 Webhook processing order (non-negotiable)

1. Read **`request.body` once, as bytes**, before touching `request.POST` or `json.loads` — the signature covers exact raw bytes.
2. **Verify signature** with `hmac.compare_digest`, plus a ±300s timestamp tolerance for replay defence. Failure → `400`, **persist nothing**. Persisting unverified events would let an attacker pre-poison the `(provider, event_id)` dedupe table so a later genuine event is discarded as a duplicate.
3. Parse. Unhandled `event_type` → record as seen, return `200` (acknowledge-and-ignore; unhandled types must not trigger provider retries).
4. **Dedupe *and claim*** (§9.3).
5. Load the `Order` **by our own reference**, never trusting arbitrary webhook fields.
6. Assert `event.amount_minor == order.total_minor` **and** currency matches — exact integer comparison (D3 is why). Mismatch in **either** direction → `NEEDS_REVIEW`, never fulfil: under-payment is fraud, over-payment is a config bug needing a human.
7. Transition via the guarded state machine, create the fulfilment job, `transaction.on_commit(...)` → fulfil.
8. Return `200`.

The endpoint is the codebase's **first** `@csrf_exempt` view. Mitigations: a dedicated non-catch-all URL, `@require_POST`, mandatory signature verification, and no session or auth use anywhere inside the handler.

### 9.3 Dedupe must carry processing state — not just "insert, catch, 200"

The naive pattern **loses events**: if the dedupe row commits but processing then crashes, the provider's retry is swallowed as a duplicate and the order is never fulfilled. The row must carry its own processing state and the duplicate path must be able to re-drive unfinished work.

```python
try:
    with transaction.atomic():            # D5: MANDATORY savepoint, else the
        event = WebhookEvent.objects.create(...)   # IntegrityError poisons the
except IntegrityError:                    # transaction on PostgreSQL
    event = WebhookEvent.objects.get(provider=PROVIDER, event_id=event_id)

claimed = WebhookEvent.objects.filter(pk=event.pk, processed_at__isnull=True)\
    .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))\
    .update(locked_until=now + LEASE, attempts=F("attempts") + 1, updated_at=now)
if claimed != 1:
    return HttpResponse(status=200)       # already processed, or in flight elsewhere
```

Two concurrent identical deliveries: one inserts, the other fails the unique index, fetches the row, then loses the claim CAS because `locked_until` is in the future — and returns 200 having done nothing. `locked_until` is a **lease, not a lock**: if the process is killed mid-processing it expires and the next delivery or cron run re-drives it safely, because processing is idempotent.

`F("attempts") + 1` is the project's first `F()` expression, and is required — a read-then-write increment here would reintroduce the very race being eliminated.

### 9.4 Fulfilment without a worker

**Fulfilment is defined as DB-only**: flip reserved keys to sold, stamp `delivered_at`, set `fulfilled_at`, transition the order. A handful of single-row `UPDATE`s — tens of milliseconds. **Email is explicitly not part of fulfilment**; it becomes an `OutboundEmail` row drained by cron. This split is what makes synchronous in-request fulfilment viable: keys appear in the library the instant the webhook returns, and no SMTP handshake ever sits inside the webhook request.

Every step is a CAS with a rowcount check and a no-op on re-run. Per-item atomic blocks (not one large transaction) mean a crash at item 7 of 10 leaves items 1–6 **permanently delivered** — correct, because a delivered key must never be un-delivered. A wall-clock budget returns `PARTIAL` and lets cron finish the rest.

Job pickup is itself a lease CAS, so overlapping cron runs and a concurrent webhook cannot double-run the same order.

**The webhook always returns 200 regardless of what fulfilment does** — the payment is already durably recorded, and a fulfilment failure must never trigger provider retries, since the remedy is ours, not theirs.

If fulfilment fails after payment: order → **`FULFILLMENT_FAILED`/`NEEDS_REVIEW`**, audit row, staff alert, and **no second key is issued automatically** (PRD §14, §39).

`transaction.on_commit()` — the project's first use — guarantees the job row and `PAID` status are durable before anything acts on them, and critically **does not run at all if the transaction rolls back**.

**Celery compatibility** mirrors the existing `reputation/signals.py` guard: one function, two dispatchers, gated by a `MARKETPLACE_USE_CELERY` flag defaulting to `False`, falling through to the synchronous path if the broker is unreachable. The task body *is* the synchronous body, so enabling a worker later changes no behaviour.

### 9.5 Pull reconciliation — the primary defence on shared hosting

A dropped webhook is a realistic failure on cPanel, so the redirect/return page must **never** be what marks an order paid (a closed tab or a prefetching bot would corrupt state). Instead:

- The order page htmx-polls a partial while `PENDING_PAYMENT` (reusing the existing polling convention). Providers frequently beat the browser redirect, so the first render often already shows the keys.
- After the poll window, a "Check payment status" action performs a **synchronous provider API lookup** and feeds the result into `apply_payment_confirmation(...)` — **the same service the webhook calls**, with the same CAS, amount check and state machine.
- Cron additionally pull-reconciles `PENDING_PAYMENT` orders with a session id within a 72h window.

Push and pull converge on one code path, so they cannot disagree and there is only one thing to test.

### 9.6 Late payment after expiry — and the line the PRD draws

If the paid-CAS matches nothing because the order already `EXPIRED`, and it is within a 72h window: CAS `EXPIRED → PAID_LATE`, then attempt **normal allocation** for items lacking keys. If all succeed → `PAID`, fulfil. If `OutOfStock` → `NEEDS_REVIEW`; **never auto-refund** (money does not move without a human).

Re-allocating here is **not** a PRD violation. PRD §14/§39 forbid auto-issuing another key when fulfilment failed *after* a key was consumed or delivered. Here no key was ever delivered — the reservation merely lapsed, making this identical to a fresh purchase. The mechanism that enforces the PRD rule is structural: **exactly one code path calls the allocator, and it is unreachable from the "key already consumed" branch**, which raises non-retryable errors straight to review.

---

## 10. Supplier abstraction

```python
class SupplierAdapter(Protocol):
    slug: str
    def sync_products(self) -> Iterable[ProductData]
    def sync_prices(self) -> Iterable[PriceData]
    def acquire_keys(self, product, quantity) -> Iterable[str]
    def status(self) -> SupplierStatus
```

MVP ships `ManualSupplier` (admin CSV/paste import — the only path actually used) and `MockSupplier` (tests). `SupplierIntegration.enabled` is a per-supplier kill switch (PRD §49). Credentials live in env, never in `SupplierIntegration.config`.

Per PRD §25, no live supplier is integrated until identity, distribution rights, reseller agreement, and API security are verified.

---

## 11. Admin functionality

Django admin is the base (it already hosts all moderation), extended with marketplace-specific permissions and a small custom dashboard.

| Surface | Capability | Permission |
|---|---|---|
| Products | CRUD, publish/unpublish, pricing | `marketplace.change_product` |
| Inventory | Import batches, counts, status — **never bulk-visible plaintext keys** | `marketplace.import_inventory` |
| Key reveal | Single-key reveal, reason required, **audited** | `marketplace.reveal_key` (separate, rarely granted) |
| Orders | View, re-drive fulfilment, mark reviewed | `marketplace.view_order`, `marketplace.fulfill_order` |
| Refunds | Initiate/approve | `marketplace.refund_order` |
| Coupons | CRUD | `marketplace.change_coupon` |
| Kill switches | Toggle marketplace/purchases/fulfilment/supplier | `marketplace.toggle_killswitch` |
| Audit log | Read-only, no delete | `marketplace.view_auditlog` |
| Dashboard | Revenue, orders, failures, low stock, supplier errors | `marketplace.view_dashboard` |

Roles are Django **Groups** seeded by a data migration (following `gamification/migrations/0002_seed_badges.py`): *Marketplace Support*, *Marketplace Manager*, *Marketplace Finance*, *Marketplace Admin* — matching PRD §23. Custom `Meta.permissions` supply the verbs Django's default four don't cover.

`AuditLog` and `KeyAccessLog` admin classes override `has_add_permission`/`has_change_permission`/`has_delete_permission` to return `False` (append-only).

---

## 12. Security implementation

### 12.1 Server-authoritative values
Client submits **product IDs and quantities only**. Price, discount, tax, total, order ownership, payment status, key ownership, and inventory availability are all derived server-side. `pricing.price_cart()` is the single source of truth and is re-run at cart render, checkout render, and order creation.

### 12.2 Key protection (PRD §15)
Fernet encryption at rest (`cryptography`, **new dependency**) with the key from env; `key_fingerprint` (SHA-256) for dedupe without plaintext; `masked_hint` for display; plaintext returned only through the authorised reveal path, never in list endpoints, never logged, never in notifications or email. Sentry already runs `send_default_pii=False`; add explicit scrubbing for marketplace payloads.

### 12.3 Authorization
Queryset-scoped ownership everywhere; UUID order references; Django permissions for all staff surfaces; **no frontend-only role checks**.

### 12.4 Key retrieval — the six gates (PRD §18)
`POST` only → authenticated → entitlement belongs to `request.user` → order is `FULFILLED` → product entitlement matches → rate-limit check → **write `KeyAccessLog`** → decrypt and return a one-shot htmx fragment.

### 12.5 Rate limiting — and why cache-based locking is not an option
The existing `is_rate_limited` is a racy `COUNT`-then-act and is **not** sufficient for money endpoints. Reuse it only for low-risk actions (cart mutations, wishlist). For checkout, coupon validation and key reveal, use a CAS-incremented counter row plus a DB-unique idempotency guard, so a lost race cannot double-charge.

**Do not reach for a cache lock.** With no `CACHES` configured, Django falls back to `LocMemCache`, which under Passenger is **per-process** (D7) — a `cache.add()` lock would appear to work in dev and grant the lock to every worker in production. **Every mutual-exclusion primitive in this design is a database row CAS.** If Redis is later added as a cache backend, none of this design needs to change.

### 12.6 Webhooks
Signature verification before parsing; unique `provider_event_id`; amount/currency assertions; guarded state machine; `csrf_exempt` scoped to the single webhook path.

### 12.7 Application security
Reuse `clean_rich_text_body` for all rich text (XSS); Django ORM only (SQLi); CSRF everywhere except the verified webhook; `url_has_allowed_host_and_scheme` for redirects (existing pattern); explicit form field allowlists (mass assignment); `validate_image_file_size` + `ImageField` for uploads; `SECURE_*` settings already correct in `prod.py`; add `CSRF_TRUSTED_ORIGINS` (F7).

### 12.8 Audit logging (PRD §32)
`AuditLog` records: admin actions, price/product changes, inventory imports, key allocation, key access, refunds, payment state changes, permission changes, kill-switch toggles. Append-only, never contains secrets or key plaintext.

---

## 13. Testing strategy

Follows existing conventions (`TestCase`, `self.client.login`, `reverse()`, descriptive method names) with one deviation: a `marketplace/tests/` **package** rather than a single `tests.py`, given the volume. Both the Django runner and the configured pytest `python_files` already discover `test_*.py`.

| Layer | Coverage |
|---|---|
| **Unit** | `price_cart` (discounts, rounding, negative-total prevention), coupon validation (expiry, per-user/global limits, scope, min-subtotal), order state machine, payment state machine, permission matrix, key encrypt/decrypt/fingerprint/mask |
| **Integration** | Full checkout with `MockProvider`; webhook → PAID → fulfilled → entitlement → library; refund flow; manual key import |
| **Security** | IDOR on orders/entitlements/keys (user B cannot read user A's anything); privilege escalation across all four groups; **price manipulation** (tampered POST totals ignored); coupon abuse; **key never appears in any list/API/log response**; CSRF on state-changing routes; XSS through product description |
| **Idempotency** | Duplicate webhook (same `provider_event_id`) fulfils exactly once; double checkout submit creates one order; re-running `fulfill_order` is a no-op |
| **Concurrency** | **`TransactionTestCase` + threads** — the suite currently uses only `TestCase`, which wraps each test in a rollback so a second thread on a second connection cannot see the first's writes; such a test would pass while proving nothing. 20 threads / 5 keys ⇒ exactly 5 succeed, 15 clean `OutOfStock`, and 5 distinct `reserved_by_item` values. 10 threads posting an identical signed webhook ⇒ one `WebhookEvent`, one `paid_at`, one fulfilment job, keys sold exactly once |
| **Failure modes** | Payment succeeds + fulfilment fails ⇒ review, **no second key**; expired reservation then late webhook (assert **both** interleavings end in exactly one well-defined state and no key has two owners); webhook amount mismatch in both directions |

**CI split (the existing workflow already provisions `postgres:16`, so this is free):**

- **PostgreSQL job** — full suite *including* the threaded concurrency tests.
- **SQLite job** — `migrate` + `check` + the non-threaded tests only, to keep the schema-portability promise honest. Threaded tests are meaningless there: SQLite sets `test_db_allows_multiple_connections = False` (D9).

Backend-agnostic unit tests cover the CAS rowcount branches by pre-mutating rows between calls — proving the *logic* everywhere without needing real concurrency.

---

## 14. Migrations and seed data

| Migration | Contents |
|---|---|
| `0001_initial` | All marketplace models |
| `0002_seed_reference_data` | `ActivationPlatform` (Steam/Epic/GOG/Ubisoft/Battle.net), `Region` (Global/EU/NA/…) via `get_or_create` — idempotent, following `gamification/0002_seed_badges.py` |
| `0003_seed_rbac_groups` | The four Groups + permission assignments |
| `0004_marketplace_settings` | `MarketplaceSettings` singleton, **defaulting to disabled** so the marketplace is dark until explicitly switched on |
| `notifications/00XX` | Only if a marketplace verb exceeds `max_length=20` |

`seed_demo.py` gains optional marketplace demo data (demo products + test keys) behind the existing `demo_`/`demo-` prefix convention so `--clear` still works.

**No periodic-task migration is needed** under the cron-based design; if Celery is later introduced, follow `reputation/migrations/0002_nightly_karma_schedule.py` exactly.

---

## 15. Environment variables and secrets

New (none committed; `.env.example` gains documented placeholders):

| Variable | Purpose | Required |
|---|---|---|
| `MARKETPLACE_ENABLED` | Build-level kill switch | No (default off) |
| `MARKETPLACE_KEY_ENC_KEY` | Fernet key for key encryption at rest | **Yes, once inventory exists** |
| `MARKETPLACE_CURRENCY` | Single MVP currency | Yes |
| `PAYMENT_PROVIDER` | Adapter slug (`mock` in dev) | Yes |
| `PAYMENT_API_KEY` / `PAYMENT_WEBHOOK_SECRET` | Provider credentials | Yes in prod |
| `MARKETPLACE_SUPPORT_EMAIL` | Customer-facing contact | Yes |

Also fix the two **existing undocumented** settings while here: `EMAIL_BACKEND` and `SITE_URL` are env-driven but absent from `.env.example`.

**Losing `MARKETPLACE_KEY_ENC_KEY` renders all stored inventory unrecoverable** — it needs a documented backup/rotation procedure (`MultiFernet` supports rotation).

---

## 16. Deployment considerations

Ordered prerequisites — several are pre-existing defects that become launch blockers:

1. **`.cpanel.yml`** — add `marketplace` to the copied directory list (F3). Without this the app never reaches the server.
2. **Run `migrate` on deploy** (F3) — currently never runs. Marketplace migrations will not self-apply.
3. **Fix `STORAGES`** (F1) + **add `boto3`** (F2) — otherwise product images land on ephemeral disk.
4. **Configure a real `EMAIL_BACKEND`** (F4) — order/key emails are a launch blocker.
5. **Guarantee the production DB** (F5) — add the `marketplace/checks.py` system check (§8.0) so `manage.py check`/`migrate` fails on SQLite in production rather than silently accepting it. Add the SQLite `transaction_mode: "IMMEDIATE"` + WAL `OPTIONS` (§8.4) for dev/CI correctness.
6. **Add `CSRF_TRUSTED_ORIGINS`** and a `LOGGING` config (F7).
7. **Register cron** — one entry, every minute, running a single `run_marketplace_jobs --budget 50` command that drains in order: expire reservations → fulfilment jobs → webhook-event reconciliation → **provider pull reconciliation** → outbound email → heartbeat. A wall-clock budget plus a `CronLease` row CAS keeps runs from stacking. The heartbeat is essential: without it, "cron stopped running" is a completely silent failure.
8. **Webhook URL must be registered as `https://`** and its host present in `DJANGO_ALLOWED_HOSTS`. `prod.py` sets `SECURE_SSL_REDIRECT = True`, and a 301 on a POST **drops the body** (D8) — this belongs in the smoke test.
9. **`./scripts/build-css.sh` must run** after adding the `@source` line; CSS is committed, not built on deploy.
10. **Rollback** — `MarketplaceSettings` + `MARKETPLACE_ENABLED` allow disabling the marketplace without taking GameKiro offline (PRD §48/§49).

Add a marketplace section to `docs/PRODUCTION_SMOKE_TEST.md` (the project's existing release gate).

---

## 17. Implementation phases

Each task notes **[NEW]** (new marketplace component) or **[MOD]** (modifies existing GameKiro component).

### Phase 0 — Prerequisites (blocks everything)
| Task | Dep |
|---|---|
| Publish refund policy page + update ToS §6 (F6 — a written commitment) **[MOD]** | — |
| Fix `STORAGES` / add `boto3` (F1, F2) **[MOD]** | — |
| Configure real email backend (F4) **[MOD]** | — |
| `.cpanel.yml` + deploy `migrate` (F3) **[MOD]** | — |
| Add `@source "../marketplace/templates"` (F-frontend) **[MOD]** | — |
| Scaffold app, add to `LOCAL_APPS`, mount at `path("store/", …)` outside the `path("")` catch-all **[NEW/MOD]** | — |
| SQLite `transaction_mode`/WAL `OPTIONS` + prod-DB system check (§8.0, §8.4) **[MOD]** | — |
| Split CI into PostgreSQL (full + threaded) and SQLite (migrate/check only) jobs **[MOD]** | — |

### Phase 1 — Catalog (no money)
Models `ActivationPlatform`/`Region`/`Product`/`ProductImage` **[NEW]**; admin **[NEW]**; store home, browse, product page **[NEW]**; `product_card.html` **[NEW]**; sidebar/bottom-nav entries **[MOD]**. *Dep: Phase 0.*

### Phase 2 — Cart & wishlist
`Cart`/`CartItem`/`WishlistItem` **[NEW]**; `services/pricing.py` + `services/cart.py` **[NEW]**; cart UI **[NEW]**. *Dep: Phase 1.*

### Phase 3 — RBAC, audit, kill switches (early, because later phases depend on them)
Groups + custom permissions + seed migration **[NEW]**; `AuditLog` + `services/audit.py` **[NEW]**; `MarketplaceSettings` + `services/flags.py` **[NEW]**. *Dep: Phase 0.*

### Phase 4 — Inventory & key security
`GameKey`/`InventoryImportBatch`/`KeyAccessLog` **[NEW]**; `services/keys.py` (Fernet, + `cryptography` dep) **[NEW]**; `services/inventory.py` CAS allocation **[NEW]**; admin import + gated reveal **[NEW]**; **concurrency tests** **[NEW]**. *Dep: Phases 1, 3.*

### Phase 5 — Orders, checkout, fulfilment (with `MockProvider`)
`Order`/`OrderItem`/`Entitlement` **[NEW]**; `services/checkout.py`, `services/fulfillment.py` **[NEW]**; checkout/orders/library UI **[NEW]**; `run_marketplace_jobs` command + cron entry **[NEW]**; order notifications — extend `Notification.Verb` and `TARGET_LOADERS` **[MOD]**. *Dep: Phases 2, 4.*

### Phase 6 — Payments & webhooks
`Payment`/`WebhookEvent`/`Refund` **[NEW]**; provider interface + `MockProvider` **[NEW]**; webhook endpoint **[NEW]**; refund workflow **[NEW]**; idempotency + duplicate-webhook tests **[NEW]**. *Dep: Phase 5.*

### Phase 7 — Promotions
`Coupon`/`CouponRedemption` + `services/coupons.py` **[NEW]**; coupon UI + abuse tests **[NEW]**. *Dep: Phase 5.*

### Phase 8 — Supplier abstraction
Adapter interface, `ManualSupplier`, `MockSupplier`, `SupplierIntegration` + kill switch **[NEW]**. *Dep: Phase 4.*

### Phase 9 — Hardening & launch
Admin dashboard **[NEW]**; verified-purchase review badges **[MOD]**; SEO/OG/sitemap + `noindex` on private pages **[NEW/MOD]**; full security test pass; smoke-test checklist **[MOD]**; real provider adapter in sandbox; staged rollout with the kill switch on. *Dep: all.*

---

## 18. Risks, edge cases, and architectural conflicts

### Architectural conflicts identified

| # | Conflict | Resolution |
|---|---|---|
| C1 | **PRD §33 palette vs. current theme** | Resolved: keep the existing theme. PRD colours predate the re-theme. |
| C2 | **PRD §14 reserves inventory *after* payment; this plan reserves *before*** | Deliberate deviation, justified in §7 — prevents the "paid, no stock" manual-review case PRD itself wants avoided. |
| C3 | **PRD/architecture assume a background worker; production starts none** | Resolved: synchronous fulfilment + cron sweep, Celery-compatible. |
| C4 | **Architecture doc says "atomic"/transactional inventory; row locking is silently unavailable on SQLite and partial unique indexes silently unavailable on MySQL** | Resolved: CAS conditional-UPDATE + plain-unique structural backstop, correct on PostgreSQL, MySQL/MariaDB and SQLite alike (§8). |
| C8 | **PRD §14 forbids auto-issuing another key after a fulfilment failure, but late-payment recovery must allocate one** | Resolved by structure, not by policy: exactly one code path calls the allocator, and it is unreachable from the "key already consumed" branch, which raises non-retryable errors straight to review (§9.6). |
| C5 | **PRD §22 wants admin inventory management; PRD §15 says keys must not be broadly visible** | Resolved: masked hints everywhere, separate `reveal_key` permission, mandatory audit entry. |
| C6 | **PRD wants community reviews on product pages; `games.Review` already exists** | Reuse `games.Review`, add verified-purchase from `Entitlement` — do not build a second review system. |
| C7 | **`games.Platform` (hardware) ≠ activation platform (Steam/Epic)** | Separate `ActivationPlatform` model; do not overload the existing one. |

### Key risks

| Risk | Severity | Mitigation |
|---|---|---|
| Duplicate key delivery under concurrency | **Critical** | CAS allocation + single-valued FK + check constraint + concurrency tests (§8) |
| Duplicate webhook ⇒ double fulfilment | **Critical** | Unique `provider_event_id` + idempotent `fulfill_order` + guarded state machine (§9) |
| Price manipulation | High | Client sends IDs/quantities only; `price_cart()` authoritative; provider amount from `Order.total_minor` |
| Key leakage | **Critical** | Encryption at rest, masked display, six-gate reveal, access log, never logged/emailed |
| Paid but unfulfillable | High | `REQUIRES_REVIEW`; never auto-issue a second key; sweep + admin re-drive |
| Lost encryption key | **Critical** | Documented backup + `MultiFernet` rotation (§15) |
| Deploy omits migrations / new app | High | F3 fixes are Phase 0 blockers |
| Media on ephemeral disk | High | F1/F2 fixes are Phase 0 blockers |
| Racy rate limiter abused at checkout | Medium | DB row CAS + DB-unique idempotency guard (**not** a cache lock — D7) |
| SQLite in production under load | High | System check fails `migrate` on prod+SQLite (§8.0); `transaction_mode=IMMEDIATE` + WAL for dev/CI |
| **Lock-based allocator silently no-ops on SQLite** | **Critical** | Locking rejected as the primary mechanism; CAS + plain-unique backstop instead (D1, §8.3) |
| **Partial unique constraint silently dropped on MySQL** | **Critical** | Backstop is a plain unique index via `OneToOneField`, never `condition=` (D2) |
| Webhook dropped by shared hosting | High | Pull reconciliation via provider API — same code path as push (§9.5) |
| Dedupe row committed, processing crashed | High | Dedupe row carries processing state + expiring lease, so retries re-drive (§9.3) |
| Cron silently stops running | High | Heartbeat row + external monitor; webhook still fulfils inline, only sweeps/email stall |
| `updated_at` silently stale across the whole subsystem | Medium | Every `.update()` sets `updated_at` explicitly (D4) |
| Webhook POST body dropped by SSL redirect | High | Register the endpoint as `https://`; add to smoke test (D8) |

### Failure and recovery matrix

Governing principle: **money never moves automatically, and a delivered key is never un-delivered.** Every ambiguous state resolves toward human review with an audit trail rather than toward an automated guess.

| Failure | Detection | Recovery | Auto / Human |
|---|---|---|---|
| Webhook handler 500s before commit | Provider retry | Nothing committed — the dedupe row rolls back with everything else; the retry processes cleanly | Auto |
| Dedupe row committed, processing then crashed | `processed_at` null, lease expired | Retry hits the duplicate path which **re-drives** via the claim CAS; cron also re-drives | Auto |
| Webhook never delivered (dropped by host) | Order stuck `PENDING_PAYMENT` with a session id | Cron pull-reconciliation + the user-facing "Check payment status" action (§9.5) | Auto |
| Keys reserved, payment abandoned | `reservation_expires_at < now` | Sweep: order → `EXPIRED`, then keys released (§8.6) | Auto |
| Order write fails after keys claimed | — | Impossible by construction: allocation shares the checkout transaction, so rollback releases reservations (§8.5) | Auto |
| Multi-item order partially fulfilled | `PARTIALLY_FULFILLED`, job pending | Re-run skips items with `delivered_at` set; delivered keys are never rolled back | Auto |
| Paid, but allocation missing/lost | Non-retryable exception | → review, audit row, staff alert. **No automatic re-allocation** (PRD-mandated) | **Human** |
| Fulfilment fails transiently N times | `attempts >= MAX` | → review | **Human** |
| Refund after delivery | `refund_succeeded` | Key → `revoked`, **never returned to the available pool** (the buyer has seen the plaintext). Re-listing is a deliberate admin action | Auto-mark, human re-list |
| Chargeback | `dispute_created` | Key → `revoked`, order → `CHARGEBACK`, audit row. Never auto-refund, never auto-ban | **Human** |
| Duplicate webhook, concurrent | Unique index + claim CAS | Loser returns 200 having done nothing | Auto |
| Webhook after reservation expiry | Paid-CAS matches 0 rows, order `EXPIRED` | Late-payment recovery (§9.6) | Auto if stock exists, else **human** |
| Amount / currency mismatch | Exact integer comparison | → review, `rejected_amount`. **Never fulfil**, in either direction | **Human** |
| Webhook references unknown order | `Order.DoesNotExist` | → `orphaned`, return 200, cron re-drives (covers "webhook beat the order commit"); alert after 24h | Auto, then human |
| Duplicate key in an import | `(product, key_fingerprint)` unique | Importer reports and skips — prevents one key selling twice from two rows | Auto |
| Two buyers, one last key | CAS returns 0 on every candidate | `OutOfStock` → transaction rolls back → clean "just sold out", no partial order | Auto |
| Process killed mid-fulfilment | Lease expires | Next cron drain re-claims and resumes idempotently | Auto |
| Cron stops running entirely | Heartbeat row stale | Dashboard banner + external monitor. Orders still fulfil inline via the webhook; only sweeps, email and pull-reconciliation stall | **Human** |
| Receipt email fails | `attempts` | Retried with backoff; keys are already in the library, so this is cosmetic (§9.4) | Auto |

### Edge cases to handle explicitly
Last unit bought concurrently by two users · webhook arriving before the browser redirect · webhook arriving after reservation expiry · partial fulfilment of a multi-item order · refund after key delivery (PRD §41 — do **not** auto-restore a delivered key) · coupon expiring between cart render and order creation · product unpublished mid-checkout · duplicate key string in an import batch · user deleted with live orders (`PROTECT` on `Order.user`) · currency mismatch in webhook payload.

---

## Open questions

These are unresolved and must be answered before the phases that depend on them:

1. **Payment provider** (PRD §52) — blocks Phase 9's real adapter, not Phases 1–8.
2. **Supported currencies and countries**; **tax/VAT handling** — a merchant-of-record provider would remove most VAT obligation; otherwise digital-goods VAT is a genuine compliance question. Blocks pricing finalisation.
3. **Legal entity and compliance** — Cosmic Store LLC's jurisdiction is still `[to confirm]` in the ToS governing-law clause; a marketplace makes this materially more important.
4. **Refund policy content** — required by F6 and by PRD §10/§12 (checkout must display it).
5. **Is Redis + a persistent worker available in production?** If yes, fulfilment can move async; if no, the cron design stands.
6. **Production DB backend** — confirm PostgreSQL, or accept SQLite and keep the portable allocation path as the only path.
7. **Which bottom-nav slot Store displaces** on mobile (the bar is already full at five).
8. **Initial supplier** and its commercial agreement (PRD §25) — blocks Phase 8's real adapter only.

---

## Verification

Per phase, and before launch:

```bash
# Build CSS after adding the @source line
./scripts/build-css.sh

# Full suite (must stay green; currently 152 tests)
python manage.py test --noinput

# Concurrency suite specifically (TransactionTestCase — real commits)
python manage.py test marketplace.tests.test_concurrency --noinput

# Deployment config check
python manage.py check --deploy

# Lint/format as CI does
ruff check . && black --check .
```

End-to-end, against `MockProvider`: add to cart → checkout → simulated webhook → order `FULFILLED` → key visible in library exactly once → `KeyAccessLog` written → duplicate webhook replay changes nothing → second user cannot read the first user's order, entitlement, or key.
