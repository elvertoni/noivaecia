# Repository Guidelines

## Current Technical Baseline

Noivas & Cia is a Django monolith for rental-store operations. The verified stack is Python 3.12+, Django 5.2.15, Django templates, Tailwind CSS 3.4, Gunicorn, WhiteNoise, and Pillow. Local development defaults to SQLite; Docker and production use PostgreSQL through `DATABASE_URL`. Production UI and operational text must be Brazilian Portuguese.

Before relying on a remembered state, inspect `git status`, recent commits, and migrations. Do not assume the developer SQLite database has every versioned migration applied.

## Project Structure & Domain Boundaries

Project settings and root URLs live in `noivas_cia/`. Root Django apps map to business domains:

- `accounts`: custom user, module permissions, and action permissions.
- `billing`: receivables, payments, cash accounts, financial movements, and reconciliation.
- `catalog`: categories, products, search, and availability.
- `company`: singleton company and WhatsApp-report configuration.
- `core`: shared models, mixins, UI helpers, dashboard, health check, and legacy/import commands.
- `customers`: customer records, including optional CPF or CNPJ.
- `maintenance`: protected administrative and reconciliation screens.
- `movements`: pickups, returns, damage tracking, and overdue penalties.
- `notifications`: customer messages and WhatsApp integration through Evolution API.
- `rentals`: rental contracts, items, proof photos, discounts, cancellation, and printing.
- `reports`: operational and financial reports.
- `website`: public home page.

Shared templates are in `templates/`, with reusable fragments in `templates/includes/`. Tailwind input is `static/src/input.css`; `static/css/output.css` is generated. Operational scripts are in `scripts/`, and migration/import utilities are in `tools/` and `core/management/commands/`. Product and architecture references live in `PRD.md`, `README.md`, `docs/`, and `tela-cliente.md`.

`correcoes/` is a local intake folder for client-provided videos, audio, screenshots, and derived review material. Treat those files as private local inputs: do not commit raw media or generated transcripts unless the user explicitly asks.

## Build, Test, and Development Commands

- `pip install -r requirements.txt` installs Python dependencies.
- `npm install` installs Tailwind and browser-test tooling.
- `python manage.py migrate` applies database migrations.
- `python manage.py showmigrations --plan` shows applied and pending migrations.
- `python manage.py makemigrations --check --dry-run` verifies that model changes have migrations.
- `python manage.py check` runs Django system checks.
- `python manage.py runserver` starts the local server.
- `python manage.py test` runs the full Django suite.
- `python manage.py test catalog.tests.AvailabilityTests` runs a focused test class.
- `python manage.py test rentals.tests_footer_ui.RentalItemRowPersistenceTests` covers recent rental-grid behavior.
- `npm run watch:css` rebuilds Tailwind during development.
- `npm run build:css` writes minified release CSS.
- `docker compose up --build` starts PostgreSQL and the app.
- `docker compose exec app python manage.py test` runs tests in the container.

On Windows, prefer the repository environment when it exists, for example `venv\Scripts\python.exe manage.py check`.

## Coding Style & Framework Conventions

Use PEP 8, 4-space indentation, descriptive English identifiers, and single quotes in Python. Keep comments and internal names in English; all user-facing text must be Brazilian Portuguese. Prefer Django class-based views and built-in framework features. All concrete business models must inherit `core.models.TimeStampedModel` unless a framework base makes that inappropriate.

Use project permission mixins (`ModuleAccessMixin`, `ActionRequiredMixin`, or the domain-specific access mixin) instead of ad-hoc permission checks. Destructive or privileged operations require the relevant action permission as well as module access.

Use Tailwind utilities and tokens from `tailwind.config.js`; do not introduce another CSS framework. Edit `static/src/input.css`, not generated `static/css/output.css`.

## Domain Invariants and Recent Decisions

- `Rental.penalty_value` holds the **replacement price of the pieces** ("valor de reposição"), not a late fee — confirmed with the shop on 2026-08-02 and by the legacy migration, where `locado.multa` is constant per rental and worth 1.2x–3x the rental. It prints in clause 3 of the contract. The field name is historical; do not rename it (the legacy importer and ~30 test payloads depend on it).
  - **Known inconsistency, not yet resolved with the shop:** `movements.services.compute_penalty` still charges this whole amount as a late-return fee and raises a real `Receivable` (`movements/views.py:180-187`). Do not "fix" this unilaterally — it changes what customers are billed.
- A rental may hold at most `MAX_ITEMS_PER_RENTAL` (15) pieces and one entry plus `MAX_FUTURE_INSTALLMENTS` (8) installments — that is what the printed contract fits. Both caps live in `rentals/forms.py`. The 15 is measured, not assumed: two copies share one A4 sheet of 285mm and the pair takes 281.2mm with 15 items but exactly 285.0mm with 16, leaving no margin. It also matches the legacy ceiling. The item cap only blocks *growth*, so a rental that somehow exceeds it stays editable.
- A rental with registered payments may still have its item list adjusted, but customer, dates, and penalty remain locked.
- `wearer_name` exists on both `Rental` and `RentalItem`. The per-item field is deliberately hidden from the dense entry grid but remains part of saved data and printed-contract compatibility; do not remove a model field merely because the grid omits it.
- On a failed rental save, partially entered item rows must be re-rendered. Preserve the `RentalItemForm.has_user_input()` behavior and distinguish an untouched extra row from a half-filled row.
- Customers may be individuals or companies. CPF and CNPJ are optional, separately normalized fields; preserve legacy records that have neither.
- Financial writes should go through the services in `billing/services.py`, preserving transaction, reconciliation, and audit behavior.

## Testing Guidelines

Tests use Django `TestCase` and are split across `tests.py` and focused `tests_*.py` / `tests_r*.py` modules. The verified 2026-08-01 baseline discovers 775 tests across 39 files. Name classes with a `Tests` suffix and methods with `test_`.

Add focused regression tests for model constraints, forms, services, permission boundaries, and important workflows: rental creation/editing, returns, fixed penalties, receivables, payments, reconciliation, availability, imports, and WhatsApp idempotency. UI fixes should assert both valid submission and invalid-form re-rendering. Evolution API tests must mock the network.

Before handing off a non-trivial change, run at least:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test <focused module or class>
```

Run the full suite for cross-domain, migration, permission, billing, import, or deployment-sensitive changes.

## Client Adjustment Media Workflow

When reviewing client media in `correcoes/`:

1. Inventory every file without modifying the originals.
2. Transcribe audio and video, keeping the source filename and timestamps.
3. Convert requests into a deduplicated requirement matrix: source/time, requested behavior, affected screen/domain, and acceptance check.
4. Classify each request as implemented, partially implemented, missing, contradictory, or not verifiable.
5. Verify claims against code and tests; do not infer completion from UI text alone.
6. Produce a short conference checklist suitable for a user who should not need to replay the media.
7. Implement missing items only after the audit identifies a clear intended behavior; add focused regression tests and report any ambiguity separately.

## Security, Data, and Local Artifacts

Never commit `.env`, secrets, `db.sqlite3`, `venv/`, `node_modules/`, `staticfiles/`, `var/`, `media/`, generated CSS, backups, client media, or production data. Use `.env.example` as the configuration template and never print secret values.

Development may use SQLite, but production requires `DATABASE_URL`. Production settings require a strong `DJANGO_SECRET_KEY`, allowed hosts, secure cookies, HTTPS/proxy configuration, and trusted CSRF origins. The `/healthz/` endpoint is exempt from HTTPS redirect for container health checks.

### Local dev login (development only)

Logging into the running app — to check a screen or regenerate the printed contract — uses the superuser of the local `db.sqlite3`:

- **`admin@noivascia.com.br` / `teste-contrato-2026`** at `/login/`

Scope and rules:

- **Local only.** `db.sqlite3` is gitignored and never leaves the machine. This is not a production credential and must never be set on production.
- Reset it with `User.objects.get(email='admin@noivascia.com.br').set_password('teste-contrato-2026')` if the local database is rebuilt from a fresh legacy import.
- The local SQLite is often behind on migrations. Run `python manage.py migrate` before concluding that a screen is broken.
- Never reuse this password anywhere reachable from the internet, and never copy it into `.env`, fixtures, or committed test data.

## Docker and Runtime Behavior

The Docker image builds Tailwind in a Node 20 stage and runs Python 3.12 in the final stage. `docker-entrypoint.sh` applies migrations, collects static files, optionally starts `scripts/report_scheduler.sh`, and then launches Gunicorn. `WHATSAPP_SCHEDULER_ENABLED=0` disables the scheduler. Keep a single scheduler instance unless an explicit distributed-locking design is added.

## Easypanel Deployment

- Production target: project `work`, service `noivaecia`, repository `elvertoni/noivaecia`, branch `main`.
- Before deploying, inspect the service, confirm the intended commit is pushed to `origin/main`, and preserve existing source, environment, mounts, domains, ports, and resource settings.
- Deploy with the Easypanel MCP, monitor the action to a terminal state, then inspect build/service logs and the application health endpoint.
- Never print production secret values. Report only variable names or whether required settings are configured.

## Rental Form UI/UX Conventions

- Use a dense Data Grid (`<tr>`) for rental items and switch to a stacked presentation below the `sm` breakpoint.
- Preserve keyboard-first shortcuts: `Enter` navigation, `F2` to add a row, and `Ctrl+S`/`Cmd+S` to submit.
- Keep “Salvar e Imprimir” posting `save_and_print=1` so the print view opens after a successful save.
- Keep item proof-photo upload through `proof_photo_upload` and the expandable `tr.item-photo-row`.
- Avoid nested vertical scrolling inside the item grid; the page owns vertical scrolling.
- Rationale/history: `tela-cliente.md` and ai-memory page `decisions/refactor-tela-locacao-grid-antigravity.md`.

## Commit and Pull Request Guidelines

Use Conventional Commit subjects, preferably scoped when helpful, such as `fix(rentals): ...`, `feat(customers): ...`, or `refactor(ui): ...`. Keep commits cohesive and include migrations with their model change.

Pull requests should state purpose, linked issue or PRD section when relevant, test commands run, migration/data notes, deployment impact, and screenshots for visible UI changes.
