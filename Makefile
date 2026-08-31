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
