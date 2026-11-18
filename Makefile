# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# Override chargé seulement s'il existe (-f sur un fichier absent casse docker compose).
OVERRIDE := $(strip $(shell test -f docker-compose.override.yml && echo -f docker-compose.override.yml))
COMPOSE      = docker compose -f docker-compose.yml $(OVERRIDE)
COMPOSE_DEV  = docker compose -f docker-compose.yml $(OVERRIDE) -f docker-compose.dev.yml

# ── APPS_HOST_PATH (fallback automatique) ────────────────────────────────────
# Source de vérité : APPS_HOST_PATH dans .env (écrit par install.sh depuis la
# 3.71.1). Pour les serveurs installés AVANT (pas de clé dans .env), le
# Makefile calcule le chemin réel de APPS/ — voisin de HUB_base — depuis son
# propre emplacement et l'exporte pour docker compose : `hubctl update` (qui
# passe par make build/up-prod) corrige le serveur sans retouche manuelle.
# Priorité : environnement du shell > .env (lu par compose) > calcul ci-dessous.
APPS_HOST_PATH_FROM_ENV := $(shell sed -n 's/^APPS_HOST_PATH=//p' .env 2>/dev/null | head -n1)
ifeq ($(strip $(APPS_HOST_PATH_FROM_ENV)),)
ifndef APPS_HOST_PATH
HUB_BASE_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
export APPS_HOST_PATH := $(abspath $(HUB_BASE_DIR)/../APPS)
endif
endif

# ── POSTGRES_USER / POSTGRES_DB (fallback automatique) ───────────────────────
# Même logique que APPS_HOST_PATH : source de vérité .env (écrit par install.sh),
# sinon les défauts du compose (service db). Utilisé par `make db-pgss-enable`.
POSTGRES_USER_FROM_ENV := $(shell sed -n 's/^POSTGRES_USER=//p' .env 2>/dev/null | head -n1)
POSTGRES_DB_FROM_ENV   := $(shell sed -n 's/^POSTGRES_DB=//p' .env 2>/dev/null | head -n1)
PG_USER := $(or $(POSTGRES_USER_FROM_ENV),hubadmin)
PG_DB   := $(or $(POSTGRES_DB_FROM_ENV),hub_auth)

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
	$(COMPOSE_TEST) exec -T hub-api sh -c "rm -rf /code/tests /code/pytest.ini /code/sync_version.py"
	$(COMPOSE_TEST) cp hub/tests hub-api:/code/tests
	$(COMPOSE_TEST) cp hub/pytest.ini hub-api:/code/pytest.ini
	# test_sync_version_guard importe sync_version.py (racine du repo) —
	# absente du contexte image (./hub) : copiée à côté des tests.
	$(COMPOSE_TEST) cp sync_version.py hub-api:/code/sync_version.py
	# test_purge_orphan_backups importe scripts/purge_orphan_backups.py (racine
	# du repo) — copié sous /code/scripts (docker cp ne crée pas le parent).
	$(COMPOSE_TEST) exec -T hub-api mkdir -p /code/scripts
	$(COMPOSE_TEST) cp scripts/purge_orphan_backups.py hub-api:/code/scripts/purge_orphan_backups.py
	$(COMPOSE_TEST) exec -T hub-api sh -c "cd /code && python -m pytest tests -n $(XDIST_WORKERS_DOCKER)"

# ── OBSERVABILITÉ DB ─────────────────────────────────────────────────────────
# pg_stat_statements : la bibliothèque est préchargée par le command: du service
# db (shared_preload_libraries, cf. docker-compose.yml). L'extension doit être
# créée une fois par base — aucun dossier docker-entrypoint-initdb.d n'est
# monté (l'initdb ne tourne que sur un volume neuf), donc les déploiements
# existants utilisent cette cible idempotente. NB : nom du SERVICE compose = db
# (container_name hub-db) — `docker compose exec` prend le nom de service.

.PHONY: db-pgss-enable

db-pgss-enable: ## Créer l'extension pg_stat_statements sur la base du Hub (1 fois, idempotent)
	$(COMPOSE) exec -T db psql -U $(PG_USER) -d $(PG_DB) -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
