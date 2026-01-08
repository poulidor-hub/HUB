FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY ./app /code/app
COPY ./export_env.py /code/export_env.py
COPY ./import_env.py /code/import_env.py

# Créer l'utilisateur appuser
RUN adduser --disabled-password --gecos '' appuser

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
