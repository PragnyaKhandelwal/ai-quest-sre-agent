# Contributing to SRE Agent

## CI/CD Pipeline

Every push to `main`/`develop` and every PR into `main` triggers our
GitHub Actions pipeline (`.github/workflows/ci.yml`):

| Job | What it does |
|---|---|
| backend-test | pytest with >60% coverage, coverage.xml uploaded as an artifact |
| lint | ruff code quality check |
| frontend-build | Vite production build, dist/ uploaded as an artifact |
| frontend-test | Vitest + React Testing Library |
| docker-build | Builds the backend Docker image and health-checks it |
| security | Bandit static security scan, report uploaded as an artifact |

## Running locally

```bash
make test              # backend tests (pytest --cov)
make lint               # ruff linter
cd frontend && npm test # frontend tests (vitest)
make up                 # full stack via docker compose
```

See the root `Makefile` for the complete list of targets (`make help`).

## Before opening a PR

- `pytest --cov=agents --cov=backend --cov-report=term-missing` passes locally
- `ruff check agents/ backend/` is clean
- `cd frontend && npm test && npm run build` both succeed
- No secrets (API keys, tokens) in the diff -- `.env` stays local and gitignored
