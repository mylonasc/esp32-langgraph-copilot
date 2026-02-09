.PHONY: test-backend test-e2e test-all

test-backend:
	backend/.venv/bin/pytest tests/backend

test-e2e:
	cd ui && npm run test:e2e

test-all: test-backend test-e2e
