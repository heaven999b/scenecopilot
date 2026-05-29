PYTHON ?= python3

.PHONY: backend-dev backend-seed backend-test docker-up

backend-dev:
	cd backend && $(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8002

backend-seed:
	cd backend && $(PYTHON) -m app.seed

backend-test:
	cd backend && $(PYTHON) -m compileall app && pytest -q

docker-up:
	docker compose up --build
