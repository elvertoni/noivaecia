# Repository Guidelines

## Project Structure & Module Organization

This is a Django 5 monolith for Noivas & Cia. Project settings live in `noivas_cia/`; root-level Django apps map to business domains: `accounts`, `billing`, `catalog`, `company`, `core`, `customers`, `maintenance`, `movements`, `notifications` (WhatsApp messaging via Evolution API), `rentals`, `reports`, and `website`. Shared templates are in `templates/`, with reusable fragments in `templates/includes/`. Static assets are under `static/`; edit Tailwind input in `static/src/input.css` and generate `static/css/output.css`. Product and architecture references live in `PRD.md`, `README.md`, and `docs/`.

## Build, Test, and Development Commands

- `pip install -r requirements.txt` installs Python dependencies.
- `npm install` installs Tailwind tooling.
- `python manage.py migrate` applies database migrations.
- `python manage.py check` runs Django system checks.
- `python manage.py runserver` starts the local Django server.
- `python manage.py makemigrations <app>` creates migrations after model changes.
- `python manage.py test` runs the full Django test suite.
- `python manage.py test catalog.tests.AvailabilityTests` runs a focused test class.
- `npm run watch:css` rebuilds Tailwind during development.
- `npm run build:css` writes the minified CSS output for release.
- `docker compose up --build` builds and starts the optional containerized app.
- `docker compose exec app python manage.py test` runs tests inside the container.

## Coding Style & Naming Conventions

Use PEP 8 for Python: 4-space indentation, descriptive names, and single quotes for strings. Keep code identifiers and comments in English; all user-facing UI text should be Brazilian Portuguese. Prefer Django class-based views and built-in framework features. All concrete models should inherit `core.models.TimeStampedModel`. Use Tailwind utility classes in templates and follow the project design tokens in `tailwind.config.js`; avoid unrelated CSS frameworks.

## Testing Guidelines

Tests use Django `TestCase` and currently live in each app's `tests.py`. Name classes with a `Tests` suffix and methods with `test_`, for example `CatalogModelTests.test_category_str`. Add focused tests for model constraints, services, permissions, and important workflows such as rentals, returns, receivables, and availability checks.

## Commit & Pull Request Guidelines

Use Conventional Commit-style subjects. Existing history includes `feat: ensure_admins command, bigger logo, prominent nav sections`, `fix: gate maintenance by module permission`, `style: enlarge brand logo`, and scoped variants such as `refactor(ui): design system review and optimization`. Pull requests should include a short purpose statement, linked issue or PRD section when relevant, test commands run, migration notes, and screenshots for visible UI changes.

## Security & Configuration Tips

Do not commit local artifacts such as `venv/`, `node_modules/`, `db.sqlite3`, `.env`, `staticfiles/`, `var/`, or generated `static/css/output.css`; these are already ignored. Use `.env.example` as the template for local or Docker configuration, and keep secrets and production settings out of `settings.py` before deployment.

## Easypanel Deployment

- Production target: project `work`, service `noivaecia`, repository `elvertoni/noivaecia`, branch `main`.
- Before deploying, inspect the service, confirm the intended commit is pushed to `origin/main`, and preserve the existing source, environment, mounts, domains, ports, and resource settings.
- Deploy with the Easypanel MCP, monitor the deployment action until it reaches a terminal state, then check service/build logs and the application health endpoint.
- The container entrypoint applies Django migrations and collects static files before starting Gunicorn.
- Never print production secret values. Use masked environment inspection and report only variable names or whether required values are configured.
