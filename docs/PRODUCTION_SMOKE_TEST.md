# GameKiro Production Smoke Test

Use this checklist before promoting a build to production or staging.

- [ ] Homepage loads without errors.
- [ ] Signup works and creates a user profile.
- [ ] Welcome email task is queued via Celery.
- [ ] First-run profile setup loads for a new user.
- [ ] Profile setup can be completed or skipped.
- [ ] Login works.
- [ ] Logout works.
- [ ] Profile page loads.
- [ ] Games page loads.
- [ ] Forum loads.
- [ ] Messaging inbox loads.
- [ ] Notifications load, or the empty state is visible.
- [ ] Empty states render correctly for empty categories, messages, games, and profiles.
- [ ] 404 page renders cleanly.
- [ ] 500 page reviewed and not exposing stack traces.
- [ ] Mobile layout checked at 360px–480px widths.
- [ ] Tablet layout checked at 768px.
- [ ] Desktop layout checked at 1024px+.
- [ ] Static files load correctly.
- [ ] Media files upload and serve correctly.
- [ ] Celery worker is running.
- [ ] Database migrations are applied.
- [ ] Production settings use DEBUG=False.
- [ ] ALLOWED_HOSTS and SECRET_KEY are configured for the environment.
- [ ] Email provider is configured and SMTP/console backend is correct for the environment.

## Marketplace

- [ ] `manage.py check` passes against production settings (fails closed if the DB is SQLite — see `marketplace/checks.py`).
- [ ] `MARKETPLACE_ENABLED` is intentionally set (on for launch, off otherwise) before deploying.
- [ ] `MARKETPLACE_KEY_ENC_KEY` is set and backed up somewhere outside the app server — losing it makes every stored key unrecoverable.
- [ ] Store homepage (`/store/`) loads; product page, cart, and checkout pages load.
- [ ] A full purchase against the configured payment provider completes: order reaches `FULFILLED`, key appears in the library, and reveals correctly.
- [ ] The provider's webhook URL is registered as `https://`, and its host is in `DJANGO_ALLOWED_HOSTS` — `SECURE_SSL_REDIRECT` turns an `http://` webhook into a 301 that drops the POST body.
- [ ] Duplicate webhook delivery (resend the same event from the provider dashboard, or replay via curl) changes nothing and still returns 200.
- [ ] `run_marketplace_jobs` is registered in cron (every minute) and its heartbeat (`CronLease` row `heartbeat`) is recent.
- [ ] A second, unrelated account cannot view another user's order, entitlement, or key (spot-check one IDOR case).
- [ ] The kill switches (`/store/manage/`) can disable purchases/fulfilment without taking the rest of GameKiro offline.
- [ ] Refund policy page (`/refunds/`) and updated Terms of Service (`/terms/`) are live.

## Payments (Stripe / PayPal)

- [ ] Written approval from Stripe and PayPal for this business model (authorized key resale) is on file — required before either goes live. See `marketplace-plan.md` §1.
- [ ] `MARKETPLACE_ALLOW_MOCK` is unset/false in production; `/store/checkout/mock/...` and `/store/webhooks/mock/` return 404.
- [ ] Only the intended providers are in `PAYMENT_PROVIDERS_ENABLED`, and each has its own kill switch enabled at `/store/manage/`.
- [ ] `STRIPE_SECRET_KEY` is a live (or restricted `rk_live_...`) key, not `sk_test_...`; `STRIPE_WEBHOOK_SECRETS` matches the live endpoint's signing secret(s).
- [ ] `PAYPAL_ENV=live` with live `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`/`PAYPAL_WEBHOOK_ID`, not sandbox credentials.
- [ ] `SITE_URL` is `https://gamekiro.com`; webhook URLs are registered as `https://gamekiro.com/store/webhooks/stripe/` and `/paypal/`.
- [ ] `MARKETPLACE_RESERVATION_TTL_MINUTES` is at least 35 (enforced by `marketplace.E008` when Stripe is enabled).
- [ ] **Stripe:** full purchase with test card `4242 4242 4242 4242` (test mode first) — order reaches `FULFILLED`; replaying the same webhook event changes nothing.
- [ ] **Stripe:** cancel/abandon a checkout session and confirm the order stays `PENDING_PAYMENT` with a working "Pay now" retry; confirm a stale session gets expired before a new one is created.
- [ ] **PayPal:** full purchase through a sandbox buyer account, approving and returning to the site — order reaches `FULFILLED`; a second, replayed `CHECKOUT.ORDER.APPROVED` does not double-capture.
- [ ] **PayPal:** closing the tab after approving (without returning to the site) still fulfils the order once the `CHECKOUT.ORDER.APPROVED` webhook arrives.
- [ ] Partial and full refunds from `/store/manage/orders/<ref>/` complete and update the order/keys correctly for both providers; a refund issued from the provider's own dashboard is picked up and alerts staff.
- [ ] A forged/unsigned webhook (wrong signature, or missing PayPal transmission headers) is rejected with 400 and persists nothing.
- [ ] `run_marketplace_jobs` output shows `Pull reconciliation`, `Refund reconciliation`, and `Purged webhook payloads` running without errors.
- [ ] Staff alert emails (`MARKETPLACE_SUPPORT_EMAIL`) arrive for: a dispute/chargeback, a failed refund, and a payment that doesn't match any order.
- [ ] First real transaction is a small, real purchase followed by a staff-initiated refund, before advertising the payment method to users (staged rollout — see `marketplace-plan.md` §12 Phase 8).

## Fraud & abuse controls

- [ ] `MARKETPLACE_REQUIRE_VERIFIED_EMAIL=True`; a new buyer is redirected to `/store/verify-email/` and cannot reach checkout until they click the emailed link.
- [ ] Verification links expire (24h), can't be replayed for a different account, and re-verification is required after a buyer changes their email.
- [ ] `MARKETPLACE_TRUSTED_PROXY_COUNT` matches the site's actual reverse-proxy chain (0 if none) — confirm the value logged/stored as a buyer's IP is their real IP, not a proxy's, by checking a real request's `created_ip` on a `Payment` row.
- [ ] Repeated declines from one account or IP lock out further checkout attempts (`MARKETPLACE_MAX_PAYMENT_FAILURES`) for the cooldown window; the lockout message is shown, not a raw error.
- [ ] The daily per-user order cap (`MARKETPLACE_MAX_ORDERS_PER_DAY`) refuses a new order once hit.
- [ ] A high-value or above-threshold first order lands in `NEEDS_REVIEW` after payment (not auto-fulfilled), staff get an alert, and "Re-drive fulfilment" releases it once cleared.
- [ ] An oversized webhook body (over `MARKETPLACE_WEBHOOK_MAX_BODY_BYTES`) is rejected with 413 before it's parsed.
- [ ] Flooding the webhook endpoint from one IP gets throttled (429) without blocking the real provider's normal retry traffic from other IPs.
- [ ] If Sentry is enabled, trigger a webhook error and confirm the captured event has `Stripe-Signature`/`PAYPAL-*`/`Authorization` headers shown as `[Filtered]`, not the real values.
