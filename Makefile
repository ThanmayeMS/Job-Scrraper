.PHONY: help install dev up down logs migrate revision api worker beat test lint fmt typecheck

help:
	@echo "install     install package + dev deps into current venv"
	@echo "up/down     start/stop the full stack (api, worker, beat, db, redis)"
	@echo "logs        tail docker-compose logs"
	@echo "migrate     apply alembic migrations"
	@echo "revision    autogenerate a new migration (m=\"message\")"
	@echo "api         run the API locally (reload)"
	@echo "worker/beat run celery worker / beat locally"
	@echo "test        run pytest    lint  run ruff    fmt  format    typecheck  mypy"

install:
	pip install -e ".[dev]"
	pre-commit install

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

api:
	uvicorn jobradar.main:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A jobradar.workers.celery_app.celery_app worker --loglevel=info

beat:
	celery -A jobradar.workers.celery_app.celery_app beat --loglevel=info

test:
	pytest

lint:
	ruff check .

fmt:
	ruff format .

typecheck:
	mypy src
