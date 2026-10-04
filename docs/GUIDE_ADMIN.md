# POULIDOR Hub — Guide d'administration

Opérations courantes pour gérer le Hub et ses applications au quotidien.

---

## Gestion des utilisateurs

### Ajouter un utilisateur

1. **Administration → Utilisateurs → Ajouter**
2. Saisir identifiant, mot de passe, email
3. Choisir un rôle : **Admin**, **Manager**, **User**, **Viewer**
4. Optionnel : assigner à une ou plusieurs applications

**Opérations en masse :**
- **Importer CSV** : le bouton `⋮` → Importer depuis CSV permet de créer des dizaines d'utilisateurs d'un coup
- **Assigner des rôles** : sélectionner plusieurs utilisateurs → `⋮` → Assigner des rôles

### Profils d'accès

Un profil est un **modèle de rôles** applicable en un clic.

1. **Administration → Utilisateurs → Profils → Nouveau profil**
2. Définir un nom (ex : "Cadre", "Agent terrain")
3. Sélectionner les applications et rôles associés
4. Appliquer le profil aux utilisateurs dans leur fiche

### Supprimer / désactiver un utilisateur

1. Table des utilisateurs → bouton crayon sur la ligne → **Désactiver**
2. L'utilisateur ne peut plus se connecter mais ses données sont conservées

---

## Gestion des applications

### Ajouter une application

1. Déployez l'app sur le serveur (voir GUIDE_INSTALLATION.md)
2. L'app s'auto-enregistre → elle apparaît en **En attente** dans Admin → Applications
3. **Approuver** : bouton ✓ pour passer en Actif
4. **Rejeter** : bouton ✗ (avec motif)

### Application inactive (maintenance)

Si un conteneur Docker est arrêté, le Hub le détecte automatiquement (`DOCKER_SYNC_INTERVAL=300`) et passe l'app en **Inactive**. Elle réapparaît active dès que le conteneur redémarre.

### Supprimer une application

1. **Administration → Applications → bouton ✗**
2. ⚠️ Supprime aussi le service account associé
3. Les backups existants sont conservés

### Pool de ports

Le Hub alloue automatiquement les ports des nouvelles apps (frontend + API) depuis des plages définies :

```env
APP_PORT_API_START=9000    APP_PORT_API_END=9499
APP_PORT_FRONTEND_START=4200  APP_PORT_FRONTEND_END=4699
```

Voir l'état du pool : **Administration → Pool de ports**

---

## Sauvegardes

### Sauvegarde automatique (planifiée)

1. **Administration → Sauvegardes**
2. Par application : cliquer sur le bouton de configuration
3. Définir :
   - **Mode** : `scheduled` (planifié) ou `manual` (à la demande)
   - **Cron** : expression cron (ex : `0 2 * * *` = tous les jours à 2h)
   - **Rétention** : nombre de backups à conserver

### Sauvegarde manuelle

Depuis l'application distante (M2M) :

```bash
curl -X POST http://<IP_HUB>:8000/api/apps/mon-app/backup \
  -H "Authorization: Bearer <M2M_TOKEN>" \
  -F "file=@backup.zip"
```

### Restaurer

1. **Administration → Sauvegardes → bouton ↺ sur l'app**
2. Sélectionner le backup à restaurer
3. ⚠️ La restauration avec `clear_existing=true` **efface toutes les données existantes**

### Exporter un backup (chiffré)

1. **Administration → Sauvegardes → Export**
2. Le fichier `.encrypted` ne peut être déchiffré qu'avec la clé RSA du Hub

### Snapshot système

Un snapshot sauvegarde la configuration du Hub (applications, service accounts, utilisateurs) :

1. **Administration → Sauvegardes → Snapshots → Créer maintenant**
2. Configurer la rétention et le cron pour automatiser

---

## Audit et monitoring

### Tableau de bord audit

**Hub → Audit & Suivi** offre une vue complète :

| Onglet | Contenu |
|---|---|
| Vue d'ensemble | KPIs (accès, utilisateurs), alertes, sessions actives |
| Par application | Stats par app, top utilisateurs, accès récents |
| Logs détaillés | Recherche par action, app, utilisateur, IP |
| Sessions | Historique des connexions/déconnexions |
| Erreurs | Erreurs HTTP (401, 403, 500...) avec filtres |

### Alertes

Les alertes remontent :
- Tentatives d'accès non authentifiées (401)
- Accès refusés (403)
- Erreurs serveur (500)

Configurer la rétention :

```env
AUDIT_RETENTION_DAYS=90    # logs d'audit
ERROR_RETENTION_DAYS=30    # logs d'erreurs
```

### Santé des applications

**Hub → Apps (icône cœur)** : état de toutes les apps, bouton rafraîchir.

---

## Messagerie interne

### Envoyer un message global

1. **Messages → Nouveau**
2. Cible : **Tous les utilisateurs** ou utilisateurs spécifiques
3. Priorité : Info / Warning / Urgent
4. Expiration : définir une date ou "Sans expiration"

Les messages apparaissent dans le Hub (cloche de notification) et dans chaque application.

### Messages depuis une application

Les apps peuvent envoyer des messages via leur service account M2M. Voir la doc `HUB_MESSAGES.md` pour l'intégration développeur.

---

## VPN WireGuard — Serveur (accès distant)

Le HUB agit comme **serveur WireGuard**, permettant aux utilisateurs HUB
de se connecter au HUB et à ses applications à distance via un tunnel VPN
chiffré (split tunnel : seul le trafic local au HUB passe par le VPN).

### 1. Initialisation du serveur

1. **Administration → VPN**
2. Saisir l'**endpoint public** du serveur (IP publique ou nom de domaine)
3. Cliquer **Initialiser**
4. La clé privée du serveur est générée automatiquement, la config
   `wg0.conf` est créée.

### 2. Ajout de peers (utilisateurs)

1. Dans l'onglet VPN, cliquer **Ajouter un peer**
2. Sélectionner un **utilisateur HUB** dans la liste déroulante
3. Saisir un **nom de device** (ex : « iPhone de Paul », « PC maison »)
4. Valider — le peer est créé avec une paire de clés WireGuard

### 3. Distribution de la configuration

Pour chaque peer créé :
- **Télécharger le .conf** : bouton 📥 — fichier prêt à importer dans
  l'application WireGuard du device
- **Scanner le QR code** : bouton 📱 — affiche un QR code importable
  directement depuis l'app mobile WireGuard

### 4. Activation du serveur

1. Basculer le **toggle VPN** sur ON dans l'interface admin
2. Exécuter la synchronisation côté hôte :
   ```bash
   hubctl vpn sync
   ```
   Cette commande applique la configuration et démarre le conteneur
   `wireguard-server`.

Cycle OFF/ON et réparation (≥ 3.62.4) :

- La **désactivation** (toggle OFF) supprime `wg0.conf` du serveur ; les
  clés et les peers restent intacts en base.
- La **réactivation** (toggle ON) régénère automatiquement `wg0.conf`
  si elle est absente. En état résiduel (activé + conf absente), la page
  VPN affiche une alerte avec un bouton **Réparer**.
- Après un **redémarrage du poste hôte**, le conteneur ne redémarre pas
  seul (choix de design : pas de conteneur qui tourne à vide) — exécuter
  `hubctl vpn sync`.

### 5. Ouverture du port firewall

Le serveur WireGuard écoute sur le port **51820/udp**. Ouvrir ce port
dans le firewall/pare-feu du serveur HUB :

```bash
# Exemple avec ufw
sudo ufw allow 51820/udp
```

> ⚠️ Le port 51820/udp est le seul port exposé sur Internet.
> L'accès aux apps et au dashboard HUB se fait exclusivement via le tunnel VPN.

---

## Commandes utiles (terminal)

```bash
# État des services
docker compose ps

# Logs en direct
docker compose logs -f hub-api

# Logs d'un service spécifique
docker compose logs hub-frontend
docker compose logs db

# Redémarrer après modification du .env
docker compose down && docker compose up -d

# Arrêter proprement
docker compose down

# Mise à jour
git pull && docker compose up -d --build

# Accéder au shell du conteneur
docker compose exec hub-api bash

# Sauvegarde manuelle de la DB
docker compose exec db pg_dump -U hubadmin hub_auth > backup_$(date +%Y%m%d).sql
```

---

## Sécurité — checklist

- [ ] Mot de passe admin changé au premier login
- [ ] `SECRETS_ENCRYPTION_KEY` définie (clé Fernet unique)
- [ ] `ALLOWED_ORIGINS` restreint aux domaines connus
- [ ] `APP_WHITELIST_NETWORKS` limité aux CIDR Docker + LAN/VPN
- [ ] `ACCESS_TOKEN_EXPIRE_MINUTES=480` (8h, aligné cookie OIDC — valable uniquement en LAN ; si exposition externe, revenir à 30)
- [ ] Clés JWT présentes dans `secrets/jwt_private_key.pem` (+ `.env` en `chmod 600`)
- [ ] `.env` non versionné (pas dans Git)
- [ ] Ports non exposés sur Internet (LAN/VPN seulement)

---

## Support

- **Documentation développeur** : `INTEGRATION.md` (contrat d'intégration Hub ↔ Apps)
- **Sécurité** : `SECURITY_AUDIT.md` (audit complet)
- **Messages** : `exemple-app.md` (système de notifications)
