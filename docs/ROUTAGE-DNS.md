# Routage par noms DNS — guide admin (chantier B)

> Depuis la v3.79.0, chaque application du hub est joignable en
> `https://<dns_name>.<hub_dns_zone>` (ex. `https://exemple-app.hub.example.lan`) via
> le résolveur DNS du hub (dnsmasq) et le routage SNI du hub-frontend, avec un
> certificat wildcard `*.<hub_dns_zone>` signé par la PKI interne.
> Voir aussi `DOCKER_SOCKET_PROXY.md` (chantier A) et la roadmap durcissement.

## Première configuration

1. Onglet admin **« Accès & routage »** → état « non configuré ».
2. Saisir la zone (ex. `hub.example.lan`) → **Configurer**.
3. Le hub publie automatiquement : émission du wildcard, génération de la zone
   DNS (`dns-generated/hub.zone`), des vhosts SNI (`gateway/*.conf`) et les
   reloads dnsmasq/nginx. Aucun bouton « propager » — le registre des apps est
   la source de vérité.

## Résolution : qui résout les noms ?

| Contexte | Résolution |
|---|---|
| Clients **WireGuard** | Le résolveur du hub est poussé dans leur config (`DNS = <hub_host>`). Les peers existants : bouton « régénérer la config » (page VPN) pour récupérer la ligne. |
| Postes du **LAN** | V1 : continuent en `IP:ports` (fallback). Plus tard : ajouter l'IP du serveur au DHCP de la fédération (action côté fédération, voir ci-dessous) — le résolveur du hub écoute déjà sur toutes les interfaces (hôte). |

### Guide DHCP LAN (action fédération, optionnel)

Sur le serveur DHCP de la fédération, déclarer le serveur hub comme DNS ou
ajouter une zone de recherche `hub.example.lan` → serveur `192.0.2.10`.
Une fois fait, les postes du LAN résolvent les noms sans VPN (le fallback
IP:ports reste actif — aucune rupture).

## Portée et règles de publication

| Élément | Comportement |
|---|---|
| App créée / déployée / renommée | publiée immédiatement |
| App **stoppée** | reste publiée (statut « conteneur arrêté » — les liens ne cassent pas) |
| App **supprimée** | retirée de la zone et des vhosts |
| Ateliers `-ws` | publiés et marqués « atelier » (test en VPN) |
| Apps distantes | A record vers leur host, proxy_pass vers leur `frontend_url` |
| App système (`hub`) | jamais routée par nom |
| Services tiers (`RoutedService`) | FQDN libre ; s'il est dans la zone → A record + vhost ; sinon vhost seul (nom déjà résoluble publiquement) |

## Changement de zone

Modifier la zone dans le bandeau → confirmation renforcée : **tous les noms
changent** et un nouveau wildcard est émis. Les clients VPN reçoivent la
nouvelle zone dès leur prochaine requête (même résolveur). Le changement est
immédiat (reload dnsmasq + nginx).

## Diagnostic

- Onglet Applications : statuts par app (DNS publié, conteneur, fallback ports).
- Bouton diagnostique : `GET /api/routing/test-resolve/<slug>` interroge le
  résolveur du hub en direct.
- `routing_last_error` : la dernière erreur de publication est affichée dans le
  bandeau (publication automatique en échec = rollback de l'ancien contenu,
  le routage précédent continue de fonctionner).

## Ports : fin de la transition

Les ports `4200-4699 / 9000-9499` restent publiés en fallback pendant la
transition (l'onglet Ports est en lecture seule — allocation automatique au
déploiement). Leur retrait se fera à la fin de la vague B8 (« plus d'app
legacy »), quand le LAN utilisera la résolution par noms.

## Prérequis d'installation

Les dossiers `dns-generated/` et `gateway/` doivent exister et être
inscriptibles par l'utilisateur du hub (uid `HOST_UID`). Ils sont trackés
dans le dépôt (`.gitkeep`) — une install git les crée avec les bons droits.
En reprise manuelle : `chown -R $HOST_UID:$HOST_GID dns-generated gateway`.

## Sécurité du résolveur

`hub-dns` est `network_mode: host` mais **ne jamais l'exposer au-delà du
serveur** : pas de port publié (host network), `except-interface=lo`
(systemd-resolved écoute 127.0.0.53/54), `bind-dynamic` (hot-plug wg0) et
`local-service` (n'accepte que les réseaux direct-attachés : LAN + tunnel —
jamais un résolveur ouvert vers Internet). Il fait autorité QUE sur la zone du hub et forward le
reste au résolveur hôte — il n'est pas un résolveur récursif public.
