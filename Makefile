.PHONY: help install test lint build up down logs clean

help:
	@echo "Available targets:"
	@echo "  install   Install backend + frontend dependencies"
	@echo "  test      Run the pytest suite with coverage"
	@echo "  lint      Run ruff against agents/ and backend/"
	@echo "  build     Build the backend and frontend Docker images"
	@echo "  up        Start the full stack via docker compose"
	@echo "  down      Stop the docker compose stack"
	@echo "  logs      Tail logs from all docker compose services"
	@echo "  clean     Remove caches, coverage reports, and build artifacts"

install:
	pip install -r backend/requirements.txt
	cd frontend && npm install

test:
	pytest --cov=agents --cov=backend --cov-report=term-missing

lint:
	ruff check agents/ backend/

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
