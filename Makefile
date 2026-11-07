.PHONY: setup test run build

setup:
	uv venv --python 3.11 .venv
	uv pip sync --python .venv/bin/python backend/requirements.lock
	npm ci

test:
	.venv/bin/python -m pytest -q
	node --test tests/evaluation-mapper.test.mjs

run:
	.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

build:
	npm run build
