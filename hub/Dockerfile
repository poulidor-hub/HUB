FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# UID de l'utilisateur hôte pour matcher les permissions des volumes montés
ARG HOST_UID=1000
ARG HOST_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    gosu \
    wireguard-tools \
    && rm -rf /var/lib/apt/lists/*

# === Déploiement automatique des apps (spec 2026-08-18) ===
# git/make pour le pipeline, CLI docker + compose pour piloter le daemon hôte
# via le socket déjà monté. Binaires statiques officiels, versions pinées.
ARG DOCKER_CLI_VERSION=27.5.1
ARG DOCKER_COMPOSE_VERSION=2.34.0
RUN apt-get update && apt-get install -y --no-install-recommends \
        git make curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
        | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o /usr/local/bin/docker-compose \
    && chmod +x /usr/local/bin/docker-compose \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && ln -sf /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose \
    && git config --system --add safe.directory '*'

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY ./app /code/app
COPY ./migrations /code/migrations
COPY pki.py /code/pki.py
# Suite de tests exécutable dans le conteneur (parity make test / verify) :
# pytest.ini porte asyncio_mode=auto — sans lui, pytest-asyncio ignore les
# fixtures async et toute exécution de hub/tests échoue (bug 2026-10-03).
COPY pytest.ini /code/pytest.ini
COPY ./tests /code/tests

# Creer appuser avec l'UID/GID de l'hôte pour les volumes montés.
# Le GID est aussi accordé au frontend pour lire uniquement server.key.
RUN if ! getent group "${HOST_GID}" >/dev/null; then groupadd --gid "${HOST_GID}" appuser; fi \
    && APP_GROUP=$(getent group "${HOST_GID}" | cut -d: -f1) \
    && useradd --uid "${HOST_UID}" --gid "${APP_GROUP}" --create-home --shell /bin/bash appuser

# Copier et configurer l'entrypoint
COPY entrypoint.sh /code/entrypoint.sh
RUN chmod +x /code/entrypoint.sh

# Créer les dossiers nécessaires avec les bonnes permissions
RUN mkdir -p /code/certs/ca /code/certs/server /code/certs/export \
    /code/secrets/service_accounts /code/storage/backups \
    && chown -R appuser:"$(id -gn appuser)" /code

EXPOSE 8000

# L'entrypoint s'exécute en root pour ajuster les permissions,
# puis lance la commande en tant que appuser via gosu
ENTRYPOINT ["/code/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
