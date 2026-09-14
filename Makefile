# Override chargé seulement s'il existe (-f sur un fichier absent casse docker compose).
OVERRIDE := $(strip $(shell test -f docker-compose.override.yml && echo -f docker-compose.override.yml))
COMPOSE      = docker compose -f docker-compose.yml $(OVERRIDE)
COMPOSE_DEV  = docker compose -f docker-compose.yml $(OVERRIDE) -f docker-compose.dev.yml

.PHONY: up-prod up-dev down logs ps build

build:
	$(COMPOSE) build

up-prod:
	$(COMPOSE) up -d

up-dev:
	$(COMPOSE_DEV) up

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

XDIST_WORKERS ?= auto

.PHONY: test test-seq test-cov test-docker

# ── TESTS ────────────────────────────────────────────────────────────────────
# Hôte : SQLite en mémoire (un engine par worker, cf. hub/tests/conftest.py).
# Docker : stack de test isolée (-p pouly-test, cf. docker-compose.test.yml).

test: ## Tests pytest parallèles (hôte, SQLite mémoire) — XDIST_WORKERS=auto
	cd hub && python3 -m pytest -n $(XDIST_WORKERS)

test-seq: ## Tests pytest séquentiels (debug flaky / ordre-dépendant)
	cd hub && python3 -m pytest

test-cov: ## Tests parallèles + couverture HTML (hôte)
	cd hub && python3 -m pytest -n $(XDIST_WORKERS) --cov=app --cov-report=html

test-docker: ## Tests dans la stack de test isolée (-p pouly-test, image reconstruite)
	docker compose -p pouly-test -f docker-compose.yml -f docker-compose.test.yml up -d --build hub-api
	docker compose -p pouly-test -f docker-compose.yml -f docker-compose.test.yml exec -T hub-api python -m pytest -n $(XDIST_WORKERS)
