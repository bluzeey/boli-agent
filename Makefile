.PHONY: install dev test lint up down worker

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn app.main:app --reload

worker:
	celery -A app.worker.celery_app worker --loglevel=INFO

test:
	pytest

lint:
	ruff check app tests

up:
	docker compose up --build

down:
	docker compose down
