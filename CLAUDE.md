# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Pre-implementation. The repo currently contains only specs and docs: `PRD.md` (full product spec), `docs/` (guidelines split by topic), and `texto.md` (a working note). No Django project, virtualenv, or `manage.py` exists yet. The first task is Sprint 0 of `PRD.md` §13 — bootstrapping the Django project.

## Sources of truth — read before implementing

- **`PRD.md`** — the authoritative spec: data model (§8.2 erDiagram), app map (§8.3), design system (§9), functional requirements (§6), and the sprint task list (§13). Mark §13 tasks `- [x]` as completed.
- **`docs/`** — the same guidelines in digestible form. Start at [`docs/README.md`](docs/README.md):
  - [`docs/arquitetura.md`](docs/arquitetura.md) — apps, data model, cross-cutting conventions.
  - [`docs/padroes-de-codigo.md`](docs/padroes-de-codigo.md) — language/style rules.
  - [`docs/design-system.md`](docs/design-system.md) — palette, typography, component markup.
  - [`docs/roadmap.md`](docs/roadmap.md) — sprint order.

Read the relevant PRD/docs section before building a module rather than inferring.

## Stack

Python 3.12+, Django 5.x, full-stack monolith — server-side DTL rendering, no SPA, no DRF. TailwindCSS 3.x (CLI build) for all styling; no custom CSS, no other CSS framework. SQLite (native). Native `django.contrib.auth` with a **custom user keyed on email** (no username).

## Architecture (one Django app per business domain — PRD §8.3)

`core` (abstract base, dashboard, mixins, base/layout templates) · `accounts` (email-login user, signup/login/logout, per-module permissions) · `website` (public site) · `customers` · `catalog` (categories, products, availability lookup) · `company` (singleton config) · `rentals` (rentals + items) · `movements` (pickups + returns) · `billing` (receivables + late interest) · `reports` (no models) · `maintenance` (restricted DB routines).

### Cross-cutting conventions (span multiple files — follow them)

- **All models inherit `core.TimeStampedModel`** (abstract) → every table gets `created_at` (`auto_now_add`) + `updated_at` (`auto_now`).
- **Class-based views everywhere** — generic CBVs (`ListView`/`CreateView`/`UpdateView`/`DeleteView`/`DetailView`) and native Django features over custom code. No premature abstraction.
- **Module access control = one reusable mixin** in `core`, applied to every module view. Per-user access lives in `accounts.ModulePermission` (`user`, `module_key`, `allowed`). Do not reimplement checks per view.
- **Interest/penalty calc lives in ONE service**, not scattered. Late interest = `Company.daily_interest_rate` × days late. Incorrect interest is a flagged high-impact risk (PRD §12).
- **Rental numbering** is sequential from the `Company` singleton's `last_rental_number`, via a `company` helper — don't duplicate the increment.
- **`Company` is a singleton** — enforce a single row.
- **Signals go in each app's `signals.py`** (e.g. syncing `Rental.status` on pickup/return); add only when needed.

### Templates & design system

Global `templates/` and `static/` at project root. `templates/base.html` = sidebar + content layout. Reusable components extracted to `templates/includes/`, pulled in via `{% include %}`. Single design system: light bg, rose→pink brand gradient, Inter font. Copy exact tokens/markup from [`docs/design-system.md`](docs/design-system.md) (or PRD §9) rather than inventing classes.

## Language & style rules (non-negotiable — PRD RNF-05)

- **UI text: 100% Brazilian Portuguese. Code (identifiers, comments): 100% English.**
- PEP 8; **single quotes** for strings.
- Settings: `LANGUAGE_CODE = 'pt-br'`, `TIME_ZONE = 'America/Sao_Paulo'`.
- Anti-over-engineering: build nothing beyond what a requirement asks. **Docker and automated tests are deferred to the final sprints (12–13)** — do not add earlier unless asked.

## Commands

No tooling exists yet; expected commands once Sprint 0 sets up the project (Django defaults):

```bash
python manage.py runserver          # dev server
python manage.py makemigrations     # generate migrations after model changes
python manage.py migrate            # apply migrations
python manage.py createsuperuser    # admin user (custom user → prompts email)
python manage.py test               # run all tests (added in Sprint 12)
python manage.py test app.tests.TestClass.test_method   # run a single test
```

Tailwind build (exact invocation set in Sprint 0.2 — CLI watch/build against the DTL templates as `content`):

```bash
# e.g. npx tailwindcss -i ./static/src/input.css -o ./static/css/output.css --watch
```

Replace these placeholders with the real invocations as the project is bootstrapped.
