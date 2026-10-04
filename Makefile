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
# Conteneur de test limité à 2g (mem_limit hub-api de la prod) : « auto » (16 workers)
# déclenche l'OOM du cgroup ⇒ workers tués. Défaut borné, surchargeable.
XDIST_WORKERS_DOCKER ?= 4
# Stack de test isolée (une seule définition — utilisée par test-docker)
COMPOSE_TEST ?= docker compose -p pouly-test -f docker-compose.yml -f docker-compose.test.yml

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

test-docker: ## Tests dans la stack de test isolée (pouly-test, tests copiés via compose cp)
	$(COMPOSE_TEST) up -d --build hub-api
	# Purge avant cp : le conteneur persiste entre les runs, et compose cp niche
	# le dossier source dans la destination si elle existe déjà (tests/tests/...).
	$(COMPOSE_TEST) exec -T hub-api sh -c "rm -rf /code/tests /code/pytest.ini"
	$(COMPOSE_TEST) cp hub/tests hub-api:/code/tests
	$(COMPOSE_TEST) cp hub/pytest.ini hub-api:/code/pytest.ini
	# test_sync_version_guard importe sync_version.py (racine du repo) —
	# absente du contexte image (./hub) : copiée à côté des tests.
	$(COMPOSE_TEST) cp sync_version.py hub-api:/code/sync_version.py
	$(COMPOSE_TEST) exec -T hub-api sh -c "cd /code && python -m pytest tests -n $(XDIST_WORKERS_DOCKER)"
