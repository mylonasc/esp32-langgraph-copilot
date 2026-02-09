# Tests

This project includes:

- Backend tests with `pytest` in `tests/backend`
- UI + API end-to-end tests with `Playwright` in `tests/e2e`

## Fast test mode

For deterministic and fast test runs, use fake agent mode:

```bash
export AGENT_FAKE_MODE=true
docker compose up --build -d
```

## Run backend tests

```bash
backend/.venv/bin/pytest tests/backend
```

## Run e2e tests

```bash
cd ui
npm run test:e2e
```

## One-shot

```bash
make test-all
```
