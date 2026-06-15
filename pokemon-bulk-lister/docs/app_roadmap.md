# Roadmap: from personal pipeline → small private-group app

Goal: evolve `pokemon-bulk-lister` from a single-operator tool into a usable
multi-user application (think a lightweight TCGPlayer/Acorn) for a **small
private group** — a handful of trusted users, not the general public.

This is staged so each step ships something usable and is independently
reversible. Nothing here throws away the existing pricing engine, identification
flow, or marketplace publishing — those are the assets we wrap.

## Audience & licensing guardrail

"Small private group" sets two constraints that shape every stage:

1. **Auth is required but not public sign-up.** Invite-only accounts, not open
   registration. Simplifies abuse/rate-limiting concerns.
2. **PriceCharting stays internal.** Its data license forbids displaying prices
   to third parties without written permission. If the group is your selling
   operation (team/contractors) you're inside the license; if it extends to
   outside friends, get written permission first. Either way, PriceCharting
   fields must be **flaggable as internal-only** at the data layer so we can't
   accidentally leak them into a shared view (Stage 5 enforces this in code).

## Where we are (baseline)

- Single-slot SQLite (`output/db.sqlite`), one in-process job lock, `localhost`.
- One ops page (`webapp/templates/index.html`) + vanilla JS.
- Prices computed at list-time and overwritten — **no history retained**.
- No users, no auth, no catalog browse.

---

## Stage 1 — Multi-user foundation (auth + ownership)

**Outcome:** several people log in; each sees only their own cards/grids.

- Introduce SQLAlchemy + a portable schema so we can run SQLite locally and
  Postgres in prod without rewriting queries. (Keep the existing `db.py`
  functions working during migration — adapter, not big-bang.)
- New `users` table; add `user_id` FK to `grids` and `cards`. Backfill existing
  rows to a seed admin user.
- Auth: Flask-Login + password hashing (`werkzeug.security`), **invite codes**
  for account creation (no open signup). Session cookies, CSRF on mutating
  routes.
- Scope every query in `db.py` / `app.py` by `user_id`. This is the security-
  critical step — add tests that user A cannot read/patch user B's cards.

**Decisions:** Postgres now or defer to Stage 4? (Recommend: add the SQLAlchemy
layer now, stay on SQLite until hosting.)

## Stage 2 — Catalog + price history (the "real app" core)

**Outcome:** browse/search a card catalog; see a price *chart* over time.

- `card_catalog` table seeded from pokemontcg.io (sets, names, numbers, images,
  ids). A background sync job refreshes it.
- `price_points` time-series table: `(catalog_id, source, price, captured_at)`.
  Every pricing run appends instead of overwriting — this is what unlocks
  trends/charts and is the single biggest "usable app" upgrade.
- Link a user's `cards` to `card_catalog` so identification becomes
  pick-from-catalog (and gets more accurate).
- Read API: `/api/catalog/search`, `/api/catalog/<id>/history`.

## Stage 3 — UX surfaces

**Outcome:** more than one screen; it feels like an app.

- Split the single ops page into: **Collection** (your cards, portfolio value),
  **Catalog** (browse/search + price chart), **Review** (low-confidence queue,
  reusing existing logic), **Publish** (existing panel).
- Portfolio value over time (sum of holdings against `price_points`).
- Watchlist: track catalog cards you don't own yet.
- Keep server-rendered + vanilla JS to start; revisit a component framework only
  if the UI complexity demands it.

## Stage 4 — Hosting & background work

**Outcome:** runs off `localhost`, survives restarts, handles concurrent jobs.

- Managed Postgres; run the Stage-1 SQLAlchemy models against it.
- Replace the in-process job lock with a real queue (RQ + Redis is the lightest
  fit) so pricing/publish jobs survive restarts and don't block the web worker.
- Deploy target (Fly.io / Render / Railway — all fit a small Flask+Postgres+Redis
  app), HTTPS, secrets via env, automated DB backups.

## Stage 5 — Polish & guardrails

- Roles/permissions (admin vs member); per-user API rate limits.
- **Enforce the PriceCharting internal-only boundary in code**: mark those
  source fields internal, exclude them from any multi-user/shared serializer, and
  add a test that they never appear in a non-owner response.
- Audit log of listing/publish actions; basic observability.

---

## Suggested order & sizing

| Stage | Theme | Rough size | Unlocks |
|---|---|---|---|
| 1 | Auth + ownership | M | Multiple users, safely |
| 2 | Catalog + history | L | Charts, trends, better IDs |
| 3 | UX surfaces | M | "Feels like an app" |
| 4 | Hosting + queue | M | Off localhost, durable |
| 5 | Polish + guardrails | S–M | Safe to widen the group |

Recommend building in this order; Stage 1 is the prerequisite for everything and
the highest-risk-if-wrong (data isolation), so it goes first and gets the most
test coverage.
