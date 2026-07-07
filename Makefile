COMPOSE      = docker compose -f docker-compose.yml
COMPOSE_DEV  = docker compose -f docker-compose.yml -f docker-compose.dev.yml

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
