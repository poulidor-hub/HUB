FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

# UID de l'utilisateur hôte pour matcher les permissions des volumes montés
ARG HOST_UID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY ./app /code/app
COPY ./migrations /code/migrations

# Creer l'utilisateur appuser avec le même UID que l'utilisateur hôte
# Cela permet d'accéder aux fichiers des volumes montés (ex: certs/ca/ca.key)
RUN adduser --disabled-password --gecos '' --uid ${HOST_UID} appuser

# Copier et configurer l'entrypoint
COPY entrypoint.sh /code/entrypoint.sh
RUN chmod +x /code/entrypoint.sh

# Créer les dossiers nécessaires avec les bonnes permissions
RUN mkdir -p /code/certs/ca /code/certs/server /code/certs/export \
    /code/secrets/service_accounts /code/storage/backups \
    && chown -R appuser:appuser /code

EXPOSE 8000

# L'entrypoint s'exécute en root pour ajuster les permissions,
# puis lance la commande en tant que appuser via gosu
ENTRYPOINT ["/code/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
