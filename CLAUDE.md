# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Noivas & Cia is a Django 5 monolith for managing bridal/event clothing rentals.
Server-rendered Django templates + TailwindCSS 3, deployed to a VPS via
Docker/EasyPanel (GitHub `main` → EasyPanel build/deploy, project `work`,
service `noivaecia`). If Docker is not running locally, validate container
builds through EasyPanel build logs after push — a local daemon failure is
not proof of a project build error.

### Database

`noivas_cia/settings.py` picks the engine from environment: if `DATABASE_URL`
is set, it's parsed via `dj_database_url` (production runs **Postgres 16**);
otherwise, when `DEBUG` is on, it falls back to local SQLite
(`db.sqlite3`); with `DEBUG` off and no `DATABASE_URL`, startup raises
`ImproperlyConfigured`. Local dev defaults to SQLite unless you export
`DATABASE_URL` yourself. Transfer/verification tooling for the SQLite→Postgres
migration lives in `tools/db_transfer/`.

## Commands

Local venv lives in `venv/`; on Windows use `.\venv\Scripts\python.exe` for
`manage.py` commands.

```bash
pip install -r requirements.txt   # Python deps
npm install                       # Tailwind tooling
python manage.py migrate
python manage.py runserver

npm run watch:css                 # Tailwind watch mode
npm run build:css                 # Tailwind production build

python manage.py test                     # all tests
python manage.py test --keepdb            # faster reruns
python manage.py test catalog.tests.AvailabilityTests           # one test class
python manage.py test billing.tests.SomeTests.test_one_thing    # one test
python manage.py makemigrations <app>
```

Docker (optional locally):

```bash
docker compose up --build
docker compose exec app python manage.py test
```

### Pre-commit validation checklist

Before committing UI, template, or static changes:

```bash
npm run build:css
python manage.py check
git diff --check
python manage.py collectstatic --noinput --dry-run
```

For behavior changes, also run `python manage.py test --keepdb`. If the change
affects the deploy path, validate `docker build -t noivas-cia-local .` when the
local daemon is available.

## Architecture

Project settings live in `noivas_cia/`. One root-level Django app per business
domain:

| App | Responsibility |
|---|---|
| `core` | `TimeStampedModel`, access mixins, dashboard, `BRDecimalInput`, template tags |
| `accounts` | Custom `User` (email login, no username), `ModulePermission`, action permissions |
| `company` | Singleton `Company` config (interest rate, rental numbering, print footer) |
| `customers` | Customer records |
| `catalog` | `Category`, `Product`, availability lookup |
| `rentals` | `Rental` + `RentalItem`, contract printing |
| `movements` | `Pickup` / `Return` of rented items |
| `billing` | `Receivable` installments, payments, interest/penalties |
| `reports` | Read-only tracking reports (no models) |
| `maintenance` | Admin routines |
| `notifications` | Customer WhatsApp messaging (`CustomerMessage`) via Evolution API; pickup/return reminders + daily report |
| `website` | Public institutional site |

Domain flow: a `Rental` (numbered sequentially) belongs to a `Customer` and
holds `RentalItem`s referencing `Product`s; it generates `Receivable`
installments in billing and gets a `Pickup`/`Return` in movements, which syncs
`Rental.status` via signals.

### WhatsApp notifications (`notifications`)

- `notifications/evolution.py` wraps the Evolution API (self-hosted WhatsApp
  gateway); `notifications/services.py` builds the pickup-reminder /
  return-reminder queues and records one `CustomerMessage` per send attempt
  (including failures, so retries don't re-notify).
- `send_daily_whatsapp_report` management command sends the daily report per
  recipient and is idempotency-guarded via `AuditLog` (`--if-due` checks the
  configured time and pending recipients). `scripts/report_scheduler.sh` polls
  it every 30s in the deployed container.
- Message templates are centralized (see `refactor(notifications): centralize
  templates` in history) — don't inline WhatsApp copy in views/services.

`docs/arquitetura.md` has the full ER diagram; `PRD.md` and `docs/` hold
product references. `brcom/` is the legacy VB6/Access system kept for reference
(imported via `core/management/commands/import_legacy_access.py`) — never touch
it.

### Centralized business rules (never duplicate)

- **Access control**: `core/mixins.py` — `ModuleAccessMixin` (set `module_key`)
  gates whole views; `ActionRequiredMixin` (set `action_key`, e.g.
  `'customers.delete'`) gates mutations. Checks live in `accounts.User.has_module`
  / `has_action`. Never reimplement per-view checks.
- **Interest/penalty math**: all in `billing/services.py` (`compute_interest`,
  `register_payment`, `reverse_payment`, `financial_kpis`, …).
- **Rental numbering**: `Company.next_rental_number()` classmethod — the only
  place that increments `last_rental_number`.
- **Company is a singleton** — one row, always fetched via its helper.
- Signals go in the app's `signals.py`, only when truly needed.

### Frontend

- Shared templates in `templates/`, reusable fragments in `templates/includes/`
  (form fields render through `templates/includes/form_field.html` +
  `core/templatetags/core_tags.py`).
- Tailwind source `static/src/input.css` → generated `static/css/output.css`
  (ignored, never commit). Use project tokens from `tailwind.config.js`; no
  other CSS frameworks.
- Brazilian monetary inputs use `core.ui.BRDecimalInput` with masking/parsing
  in `static/js/app.js` (`1.234,56` → `1234.56`).

### Management commands worth knowing

`accounts/ensure_admins`, `core/import_legacy_access`, `core/golive_backup`
(SQLite backup with manifest), `core/homologation_report`,
`core/normalize_cities`, `core/rebuild_search_fields`,
`core/cpf_duplicate_report` / `core/merge_duplicate_customers` (duplicate-CPF
customer cleanup), `core/reconcile_negative_balances`, `core/legacy_reset`,
`notifications/send_daily_whatsapp_report`.

`tools/db_transfer/` (`sqlite_to_pg.py`, `verify_pg_migration.py`) handled the
one-time SQLite→Postgres migration to prod; `tools/legacy_migration/` holds
Access-export helpers feeding `core/import_legacy_access`.

## Conventions

- **UI text: Brazilian Portuguese. Code identifiers and comments: English.**
- PEP 8, 4-space indent, single quotes in Python.
- Prefer Django class-based generic views and built-in framework features; no
  premature abstraction, no unjustified dependencies.
- All concrete models inherit `core.models.TimeStampedModel`
  (`created_at`/`updated_at`).
- Customer-facing receivables vocabulary uses "receber/recebimento/recebido",
  not "pagar/pagamento/pago".
- Conventional Commit subjects, matching history (e.g.
  `fix(print): improve contract copy spacing`).
- Never commit local/generated artifacts: `venv/`, `node_modules/`,
  `db.sqlite3`, `.env`, `staticfiles/`, `static/css/output.css`,
  `.playwright-mcp/`, generated rental contract PDFs.

## Deployment notes

- Tailwind build must produce `static/css/output.css` during the container
  build, and `collectstatic` must pick it up.
- After push, check EasyPanel build/deploy logs if the deployment does not
  refresh.
- For print contract changes (`templates/rentals/rental_contract.html`),
  validate by regenerating the PDF from the running app, not an old download.
