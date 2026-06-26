# HTTPS du Hub

Le Hub fonctionne en **dual-stack** : HTTP et HTTPS coexistent. Les apps legacy continuent d'utiliser HTTP (`:80` frontend, `:8000` API) sans modification ; HTTPS (`:443`) s'ajoute pour les navigateurs et les apps migrées.

## Fonctionnement

- `HUB_HTTPS_ENABLED=true` (défaut) : au 1er démarrage, le Hub génère une **CA interne** et un **certificat serveur** dans `certs/` (s'ils sont absents), puis nginx expose `:443`.
- `HUB_HTTPS_ENABLED=false` : fonctionnement strictement HTTP (identique à l'historique).
- Si vous déposez vos propres `certs/server/hub/server.crt` et `server.key`, ils sont respectés (pas de régénération).

## Importer la CA sur les postes

Pour supprimer l'avertissement navigateur, importez le certificat racine `certs/ca/ca.crt` :

- **Linux (Debian/Ubuntu)** : `sudo cp certs/ca/ca.crt /usr/local/share/ca-certificates/hub-ca.crt && sudo update-ca-certificates`
- **macOS** : Trousseau d'accès → importer `ca.crt` → « Toujours approuver ».
- **Windows** : `certutil -addstore -f Root certs\ca\ca.crt`
- **Firefox** : Paramètres → Certificats → Autorités → Importer `ca.crt`.

Empreinte de contrôle : `python pki.py info` (depuis le conteneur `hub-api` : `docker compose exec hub-api python pki.py info`).

## Domaines / IP du certificat

Le certificat couvre les noms de `PKI_CERT_DOMAINS`, les IP de `PKI_CERT_IPS`, et `HUB_HOST`. Ajoutez tout nom d'hôte utilisé pour accéder au Hub, sinon le navigateur rejettera le certificat.

## Rotation / régénération

```bash
docker compose exec hub-api python pki.py cert create hub --force
docker compose restart hub-frontend
```

## Vérification

```bash
curl -k https://localhost/api/health   # 200 (HTTPS)
curl    http://localhost:8000/health    # 200 (API legacy, inchangé)
curl    http://localhost/                # 200 (frontend HTTP legacy, inchangé)
```
