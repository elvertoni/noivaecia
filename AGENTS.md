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

- `Rental.penalty_value` preserves the imported `locado.multa` value for legacy audit only. Never use it to calculate or print current contractual penalties; do not rename it because the legacy importer and test payloads depend on it.
  - `Company` is the single operational source for penalties, read when a contract is printed and when the event is registered: damage is `damage_penalty_rate` of each selected damaged item; loss/non-return is `loss_penalty_rate` of each affected item; cancellation is `cancellation_penalty_rate` of the rental total; and late return is `late_return_daily_rate` of `rental.final_value` per day, limited by `late_return_max_days`. Past that cap the garment counts as not returned and clause 6 uses `loss_penalty_rate`, so the two charges do not overlap. Rates may be above 100% (for example, 200%) and must not be artificially capped.
  - A charge records the rate and amount actually applied for audit, but future contract prints and new charge events always read the current Company configuration. This includes legacy rentals that are edited or reprinted.
- A rental may hold at most `MAX_ITEMS_PER_RENTAL` (15) pieces and one entry plus `MAX_FUTURE_INSTALLMENTS` (8) installments — that is what the printed contract fits. Both caps live in `rentals/forms.py`. The 15 is measured, not assumed: two copies share one A4 sheet of 285mm and the pair takes 281.2mm with 15 items but exactly 285.0mm with 16, leaving no margin. It also matches the legacy ceiling. The item cap only blocks *growth*, so a rental that somehow exceeds it stays editable.
- A rental with registered payments may still have its item list adjusted, but customer, dates, and penalty remain locked.
- `wearer_name` exists on both `Rental` and `RentalItem`. The per-item field is deliberately hidden from the dense entry grid but remains part of saved data and printed-contract compatibility; do not remove a model field merely because the grid omits it.
- On a failed rental save, partially entered item rows must be re-rendered. Preserve the `RentalItemForm.has_user_input()` behavior and distinguish an untouched extra row from a half-filled row.
- Customers may be individuals or companies. CPF and CNPJ are optional, separately normalized fields; preserve legacy records that have neither. Customer `district` (Bairro) is printed on the rental contract alongside address, city, and mobile phone.
- Physical deletion of a rental with registered payments, pickups, or returns is strictly blocked to preserve audit history. If a rental is already cancelled but has history, deletion attempts display an explicit audit-preservation message rather than prompting to cancel again.
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

## Legacy Import Post-Processing

Every successful `import_legacy_access --reset --confirm-reset` must be followed, before releasing the system to users, by:

1. Put public writes in maintenance mode, stop schedulers/background writers, and use one isolated administrative container for the operation.
2. Preserve the configurable `Company` fields before the reset. After import, restore only configuration fields; never overwrite the PK or timestamps, and set `last_rental_number` to the maximum of the saved and imported values.
3. Run `python manage.py post_legacy_import --dry-run`, review the counts, then run `python manage.py post_legacy_import --apply` immediately within the same maintenance window.
4. Run the dry-run again and require zero pending city, customer-search, product-search, and positive-product-value changes.
5. Compare the core record counts with the import manifest and run `python manage.py homologation_report`, `python manage.py cpf_duplicate_report`, `python manage.py check`, and the public `/healthz/` check.

Do not treat a successful raw import as a completed migration. The post-processing command defaults to read-only preview, requires `--apply` to write, is idempotent, and intentionally does not modify `Company`. Run it only immediately after a reset import, while writers remain blocked, because positive legacy product values are cleared by business rule. See `docs/deploy/runbook-cutover-legado.md`.

## Production Deployment

- Current production is the Docker Swarm stack `noivaecia` on VPS `169.58.79.15`, checked out at `~/noivaecia` for user `deploy`; the public domain is `https://noivaseciabandeirantes.com.br`.
- There is no Easypanel or Portainer on this VPS. Treat old Easypanel references as historical infrastructure only.
- Before deploying, inspect the Swarm services, confirm the intended commit is pushed to `origin/main`, and preserve existing environment, secrets, mounts, networks, domains, ports, and resource settings.
- Deploy through the repository's Swarm workflow, monitor every service to a converged state, then inspect build/service logs, run Django checks and migrations checks, and verify `https://noivaseciabandeirantes.com.br/healthz/`.
- The Evolution API is a separate service in this stack. The Django setting and required
  instance name are `EVOLUTION_INSTANCE=noivascia`. After a new Evolution deployment or
  a lost `evolution_instances` volume, first create that instance through
  `POST /instance/create` with `WHATSAPP-BAILEYS` and QR enabled, then pair it once from
  `/avisos-whatsapp/?connect=1` using a user with `notifications.manage`. Do not create
  another instance name or print the Evolution API key.
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
