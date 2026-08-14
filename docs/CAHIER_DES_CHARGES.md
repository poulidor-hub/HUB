# Cahier des charges fonctionnel — Le HUB

**Portail d'authentification unique et de supervision d'un parc applicatif auto-hébergé**

| | |
|---|---|
| **Version documentée** | 3.29.0 (état de l'art à la date de rédaction) |
| **Nature du document** | Cahier des charges descriptif — fonctionnalités livrées et démontrables |
| **Diffusion** | Interne / externe (document anonymisé) |

---

## 1. Contexte et problématique

### 1.1 Le constat

Une organisation qui héberge ses propres applications — gestion, productivité, outils
métiers — se heurte rapidement à un problème commun : chaque application réinvente sa
propre gestion des utilisateurs, ses propres mots de passe, ses propres règles de droits.
La multiplication des comptes fragilise la sécurité (mots de passe faibles, réutilisés,
jamais révoqués) et dégrade l'expérience (multiples connexions, aucun point d'entrée
unique).

### 1.2 Les solutions du marché et leurs limites

Les plateformes IAM (Identity & Access Management) open source de référence — Keycloak,
Authentik — répondent à ce besoin, mais au prix d'une complexité notable :

- **courbe d'apprentissage élevée** : concepts nombreux (realms, clients, flows,
  mappers), configuration délicate, documentation volumineuse ;
- **maintenance lourde** : mises à jour fréquentes et parfois intrusives, migrations de
  configuration à surveiller, comportements implicites difficiles à auditer ;
- **empreinte disproportionnée** pour un parc applicatif interne géré par une petite
  équipe, sur un réseau privé (LAN / VPN).

### 1.3 Le besoin

Un point d'entrée unique **léger, autonome et lisible** qui :

1. centralise l'authentification et la gestion des utilisateurs pour l'ensemble du parc ;
2. délègue aux applications la **vérification locale** des accès, sans base utilisateurs
   dupliquée dans chaque application ;
3. garantit que **le parc reste fonctionnel même si le point d'entrée est indisponible** ;
4. offre à l'administrateur une vision et des leviers d'exploitation (santé, journaux,
   sauvegardes, réseau) regroupés en un seul endroit.

Le HUB répond à ce besoin.

---

## 2. Présentation générale du HUB

### 2.1 Définition

Le HUB est un **portail d'authentification unique (SSO) et de supervision** d'un parc
d'applications auto-hébergées. Il centralise les identités, émet des jetons d'accès
vérifiables par chaque application, héberge le portail de lancement des applications et
fournit à l'administrateur et à l'exploitant les outils d'administration du parc.

### 2.2 Les quatre publics cibles

| Public | Ce que le HUB lui apporte |
|---|---|
| **Utilisateur final** | Un portail unique, une seule connexion, l'accès à ses applications, sa messagerie interne et ses préférences |
| **Administrateur** | La gestion des comptes, des applications, des droits, des sauvegardes, du VPN et des paramètres |
| **Exploitant / supervision** | L'audit, la traçabilité, la santé des applications, le suivi des erreurs et des indicateurs |
| **Développeur d'applications** | Un gabarit d'application standardisé, l'intégration SSO et machine-à-machine, la poussée de sauvegardes, les notifications, l'automatisation |

Cette grille de lecture structure la partie 2 du présent document.

### 2.3 Chiffres clés (version 3.29.0)

| Indicateur | Valeur |
|---|---|
| Endpoints d'API REST | ≈ 250 |
| Tables de base de données | 32 |
| Outils d'automatisation exposés (serveur MCP compagnon) | 65 |
| Tests automatisés | ≈ 630 (59 fichiers de tests) |
| Domaines fonctionnels | 19 modules décrits dans ce document |

### 2.4 Empilement technique

| Composant | Technologie |
|---|---|
| API du HUB | Python — FastAPI, SQLAlchemy (asynchrone) |
| Base de données | PostgreSQL |
| Frontal / interface web | Application monopage (JavaScript natif), servie par Nginx |
| Empaquetage | Docker Compose (conteneurs isolés, un seul projet) |
| Chiffrement / signatures | RSA (RS256), AES-256-GCM, Fernet |

L'interface est volontairement dépourvue de framework lourd : elle charge rapidement,
tient dans un navigateur modeste et ne dépend d'aucune chaîne de construction complexe.

---

## 3. Positionnement face aux solutions du marché

### 3.1 Tableau comparatif

| Critère | Le HUB | Keycloak / Authentik |
|---|---|---|
| Courbe d'apprentissage | Faible — concepts courts, tout est visible dans l'interface | Élevée — realms, clients, flows, mappers |
| Empreinte & prérequis | 1 projet Docker Compose, PostgreSQL inclus | Serveur Java / Python + base + configuration fine |
| Administration au quotidien | Interface unique, actions simples, journal d'audit intégré | Consoles spécialisées, configuration souvent déclarative |
| Autonomie des applications si le HUB est indisponible | **Oui** — vérification locale des jetons par clé publique | Variable — dépend de la configuration des clients |
| Supervision du parc (santé, redémarrage, versions) | Intégrée | Hors périmètre (outils tiers nécessaires) |
| Sauvegardes centralisées des applications | Intégrées (planification, chiffrement, restauration) | Hors périmètre |
| VPN d'accès distant intégré | Oui — serveur WireGuard administré depuis l'interface | Non |
| Fournisseur OIDC pour applications tierces | Oui (bridge OIDC intégré) | Oui (cœur du produit) |
| Fédération d'identités externes (social, AD…) | Non visé | Oui |

### 3.2 L'argument différenciant : l'indépendance des applications

Le HUB signe les jetons d'accès avec une **clé privée RSA** qu'il est seul à détenir.
Chaque application ne possède que la **clé publique** correspondante : elle vérifie
localement l'authenticité des jetons, mais ne peut pas en forger. Il en résulte :

- **aucune application n'héberge de base utilisateurs** — la clé publique suffit ;
- **si le HUB est arrêté, les applications déjà authentifiées continuent de fonctionner**
  pendant la durée de validité de leurs jetons ;
- la compromission d'une application ne permet pas d'usurper les identités sur les autres.

Ce compromis — simplicité maximale côté applications, autorité centrale unique côté HUB —
est le cœur du positionnement produit.

### 3.3 Périmètre assumé

Le HUB ne vise pas la fédération d'identités externes, la gestion d'annuaires d'entreprise
ni le multi-tenant à très grande échelle. Il cible les parcs applicatifs privés (quelques
dizaines d'applications et d'utilisateurs à quelques centaines) hébergés sur un réseau
contrôlé, où la lisibilité et la reprise en main priment sur l'exhaustivité des fonctions.

---

## 4. Architecture simplifiée

### 4.1 Vue d'ensemble

```
        Utilisateurs (postes du réseau local — ou distants via VPN)
                                  │
                                  ▼
                ┌───────────────────────────────────┐
                │        Frontal web (Nginx)        │
                │   Interface du HUB + terminaison  │
                │            TLS (PKI interne)      │
                └──────────────────┬────────────────┘
                                   │
                ┌──────────────────▼────────────────┐
                │            API du HUB             │
                │  (FastAPI — auth, apps, audit,    │
                │   messages, backups, VPN, OIDC…)  │
                └───────┬──────────────┬────────────┘
                        │              │
              ┌─────────▼─────┐  ┌─────▼──────────────┐
              │  PostgreSQL   │  │ Accès Docker (lect. │
              │ (identités,   │  │ + pilotage ciblé)   │
              │  registre…)   │  └─────┬──────────────┘
              └───────────────┘        │
                                   ┌───▼────────────────────────────┐
                                   │   Applications du parc         │
                                   │  App A :42xx  (front)          │
                                   │        :9xxx  (API)            │
                                   │  App B, App C… (même gabarit)  │
                                   └────────────────────────────────┘
```

### 4.2 Les trois flux principaux

**Connexion** — l'utilisateur s'authentifie une fois sur le portail du HUB. L'API vérifie
ses identifiants (et son second facteur le cas échéant) et délivre un jeton d'accès signé
RS256 ainsi qu'un jeton de renouvellement.

**Lancement d'une application** — au clic sur une tuile du portail, le HUB génère un jeton
spécifique à l'application et redirige le navigateur vers elle ; l'application vérifie le
jeton avec la clé publique du HUB et ouvre la session. L'utilisateur ne retape aucun mot
de passe.

**Sauvegarde** — chaque application pousse ses archives de sauvegarde vers le HUB avec ses
propres identifiants machine-à-machine ; le HUB les stocke, les planifie, les chiffre et
peut les restaurer.

### 4.3 Principes de déploiement

- Tout est conteneurisé dans un unique projet Docker Compose ; le HUB peut piloter les
  conteneurs des applications (redémarrage, arrêt, lecture des journaux) via un accès
  Docker volontairement restreint.
- Chaque application du parc est un projet Docker Compose **indépendant**, créé depuis un
  gabarit commun (voir § 9.1), avec ses propres plages de ports allouées par le HUB.
- L'accès distant des utilisateurs passe par un serveur **WireGuard** intégré : seul le
  port VPN est exposé hors du réseau local ; tout le reste reste sur le LAN ou dans le
  tunnel.
- Le HTTPS est assuré par une **PKI interne** : autorité de certification générée par le
  HUB, certificats serveur émis pour le HUB et, au besoin, pour des applications
  (notamment les applications relais/proxy hébergées dans le parc, qui bénéficient ainsi
  de certificats signés sans configuration manuelle).

---

## 5. Principes de sécurité

| Couche | Mécanisme mis en œuvre |
|---|---|
| Authentification | Jetons JWT signés **RS256** (clé privée unique au HUB), identifiant de jeton (`jti`) révocable, **rotation des jetons de renouvellement** stockés hashés |
| Second facteur | **TOTP** (application d'authentification) avec code QR de provisioning, codes de récupération, révocation par l'administrateur |
| Machine-à-machine | Octroi `client_credentials`, secrets chiffrés au repos (**Fernet**), clé dédiée et obligatoire en production |
| Limitation de débit | 5 tentatives de connexion / minute / IP, 10 requêtes / minute sur le pont OIDC, réponses 429 avec délai indicatif |
| En-têtes HTTP | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, **CSP** stricte |
| Secrets | Clés RSA hors du dépôt de code, validation de la configuration de sécurité **au démarrage** (refus de boot en production si invalide) |
| Sauvegardes | Export chiffré hybride **RSA-OAEP + AES-256-GCM** avec clés dédiées distinctes des clés de signature |
| Traçabilité | Identifiant de requête sur chaque appel (`X-Request-ID`), journalisation des actions sensibles, exceptions capturées en base avec pile d'appels |
| Réseau | Seul le port VPN est exposé ; enregistrement des applications restreint par liste blanche réseau |
| Vérification continue | ≈ 630 tests automatisés (authentification, OIDC, sauvegardes, snapshots, VPN, PKI, migrations…) |

---

# Partie 2 — Inventaire fonctionnel

Chaque module est décrit selon la même grille : **objectif → fonctionnalités → bénéfice**.

## 6. Parcours « Utilisateur final »

### 6.1 Portail d'applications

**Objectif** — offrir un point d'entrée unique et lisible vers les applications du parc.

**Fonctionnalités**
- Tableau de bord en grille des applications accessibles à l'utilisateur connecté ;
- **recherche instantanée** par nom ;
- **favoris** : épinglage des applications utilisées quotidiennement ;
- **filtres par catégorie** (construits dynamiquement depuis le catalogue) et filtre
  atelier / production (voir § 9.8) ;
- lancement en un clic : redirection transparente avec jeton d'accès (SSO).

**Bénéfice** — l'utilisateur ne connaît et ne mémorise qu'une seule adresse : celle du
portail.

### 6.2 Authentification unique

**Objectif** — une seule identité, une seule connexion pour tout le parc.

**Fonctionnalités**
- connexion par identifiant / mot de passe, session avec expiration et déconnexion
  (y compris à la fermeture de l'onglet) ;
- **second facteur TOTP** : activation volontaire, code QR, codes de récupération ;
- **mot de passe oublié** : réinitialisation via codes de récupération ;
- **demande de compte** : formulaire public (activable/désactivable par
  l'administrateur), suivi du statut de la demande, connexion automatique après
  approbation ; l'administrateur notifie des administrateurs désignés et approuve ou
  rejette avec motif ;
- changement de mot de passe forcé à la première connexion pour les comptes créés par
  l'administrateur avec un mot de passe par défaut.

**Bénéfice** — la sécurité du parc repose sur un seul point de contrôle, durci
(second facteur, limitation de débit, révocation).

### 6.3 Profil et préférences

**Objectif** — laisser à chaque utilisateur la maîtrise de son environnement.

**Fonctionnalités**
- consultation du profil et changement de mot de passe ;
- **préférences utilisateur** (étiquettes de personnalisation) : catégories d'étiquettes
  définies par l'administrateur, affectées à l'utilisateur, avec **accusé de
  consultation** (fenêtre d'accueil jusqu'à validation — utile pour l'acceptation de
  chartes ou l'onboarding) ;
- vue par l'administrateur des étiquettes de chaque utilisateur.

**Bénéfice** — l'administrateur communique des informations durables (chartes, rôles
métier) directement dans l'environnement de chaque utilisateur.

### 6.4 Messagerie interne

**Objectif** — diffuser des informations ciblées aux utilisateurs, depuis le HUB ou
depuis les applications.

**Fonctionnalités**
- **boîte de réception** par utilisateur : messages lus/non lus, compteur dynamique,
  archivage, suppression ;
- **priorités** : information / avertissement / urgent, avec bannière d'alerte ;
- **accusés** : lecture, réception, clic (suivi d'engagement) ;
- **notifications temps réel** par flux d'événements (SSE) — le message s'affiche sans
  rechargement ;
- côté administration : envoi **ciblé** (un ou plusieurs destinataires) ou **général**
  (diffusion à tous), **programmation différée** de l'envoi, **modèles de messages**
  réutilisables, lien d'action optionnel, statistiques, export CSV, gestion complète
  (archivage, restauration, suppression) ;
- côté applications : envoi machine-à-machine (une application notifie un utilisateur),
  avec synchronisation et reçus.

**Bénéfice** — un canal de communication officiel, traçable, qui ne dépend d'aucune
messagerie externe.

---

## 7. Parcours « Administrateur »

### 7.1 Gestion des utilisateurs

**Objectif** — administrer les identités et les droits de tout le parc en un seul endroit.

**Fonctionnalités**
- création, modification, désactivation, suppression d'utilisateurs ;
- **opérations en masse** : création, attribution de rôles, suppression, attribution de
  droits applicatifs, retrait d'accès à une application ;
- **import CSV** avec modèle téléchargeable et **prévisualisation** avant application ;
- **rôles** : rôle global (administrateur / utilisateur) et **rôles par application** ;
- **profils** : gabarits d'ensembles « applications + rôles » applicables en une action à
  des utilisateurs (avec synchronisation et détachement) ;
- gestion des **sessions** : révocation immédiate des sessions d'un utilisateur
  (déconnexion forcée propagée en temps réel) ;
- désactivation du second facteur d'un utilisateur (support) ;
- recherche et pagination.

**Bénéfice** — l'arrivée, le changement de poste ou le départ d'un collaborateur se
traitent en quelques actions, droits applicatifs compris.

### 7.2 Gestion des applications

**Objectif** — tenir le registre du parc et contrôler ce qui y entre.

**Fonctionnalités**
- **registre des applications** : fiche complète (slug, nom, description, version,
  catégorie, icône, URLs, dépôt Git), activation/désactivation, partage en service
  commun ;
- **workflow d'approbation** : toute application qui s'enregistre d'elle-même arrive en
  statut « en attente » ; l'administrateur approuve ou rejette avec motif ;
- **comptes de service** (machine-à-machine) : création avec secret affiché une seule
  fois, régénération, périmètre d'applications attaché, secrets chiffrés au repos ;
- **allocation de ports** : le HUB gère un pool de plages (front / API) et alloue les
  ports des nouvelles applications sans collision, avec vue d'état et libération ;
- **catalogue GitHub** : mise en cache des dépôts de l'organisation, marquage des dépôts
  déjà intégrés, pré-remplissage de la création d'application depuis un dépôt (avec
  allocation automatique des ports) ;
- **scanner Docker** : découverte des conteneurs présents sur l'hôte, rattachement des
  conteneurs orphelins, resynchronisation ;
- **graphe de dépendances** : déclaration et consultation des dépendances entre
  applications ;
- gestion des **favoris** et des rôles applicatifs par utilisateur.

**Bénéfice** — le parc est inventorié, versionné et contrôlé ; rien n'y entre sans
validation explicite.

### 7.3 Sauvegardes centralisées des applications

**Objectif** — centraliser, planifier et protéger les sauvegardes de tout le parc.

**Fonctionnalités**
- **réception** des archives poussées par les applications (ZIP, somme de contrôle
  SHA-256) ou **déclenchement par le HUB** (y compris avec mise au repos du conteneur
  pendant la sauvegarde — mode « quiesce ») ;
- **planification cron** par application (mode désactivé / manuel / planifié),
  exécution surveillée, statuts en cours / succès / échec, annulation d'une sauvegarde
  en cours ;
- **rétention** paramétrable par application (nombre d'archives conservées) ;
- **export chiffré** hybride RSA-OAEP + AES-256-GCM, avec clés dédiées distinctes des
  clés de signature des jetons ;
- **restauration** : depuis une archive stockée ou depuis un fichier importé ;
- **administration globale** : vue consolidée, statistiques de stockage, sauvegardes en
  cours, **détection et purge des archives orphelines** (réassociées automatiquement si
  l'application se réenregistre).

**Bénéfice** — la politique de sauvegarde du parc est définie, appliquée et vérifiée
depuis une seule console.

### 7.4 Réseau : VPN d'accès distant intégré

**Objectif** — donner un accès distant sûr au parc sans exposer les services.

**Fonctionnalités**
- serveur **WireGuard** intégré au projet Docker du HUB, activable/désactivable en un
  clic ;
- configuration serveur (point de terminaison, port, sous-réseau) depuis l'interface ;
- gestion des **pairs** : un pair = un appareil d'un utilisateur ; allocation
  automatique de l'adresse dans le sous-réseau VPN, activation/désactivation,
  suppression ;
- **statistiques temps réel** par pair (poignée de main, présence) et du serveur ;
- génération de la **configuration client** téléchargeable et du **code QR**
  d'enrôlement sur mobile.

**Bénéfice** — l'accès distant est un objet administré comme le reste du parc, sans
compétence WireGuard particulière.

### 7.5 Certificats et HTTPS (PKI interne)

**Objectif** — garantir des échanges chiffrés sans dépendre d'un autorité externe.

**Fonctionnalités**
- génération de l'**autorité de certification interne** au premier démarrage (clé
  protégée) ;
- émission de **certificats serveur** pour le HUB (domaines et adresses déclarés, avec
  extensions SAN) ;
- émission de certificats pour des besoins applicatifs dédiés — notamment les
  **applications relais/proxy** du parc, qui reçoivent automatiquement un certificat
  signé par la CA interne (routage par SNI côté frontal), sans manipulation manuelle ;
- empreinte de la CA consultable pour déploiement sur les postes de travail.

**Bénéfice** — HTTPS partout, y compris pour les applications qui n'ont aucune
compétence certificat ; suppression des avertissements navigateur sur les postes ayant
importé la CA.

### 7.6 Paramétrage et restauration du HUB

**Objectif** — garder la maîtrise de la configuration et de l'état du HUB lui-même.

**Fonctionnalités**
- **paramètres à chaud** (clé/valeur) : activation des demandes de compte, intégration
  GitHub, configuration VPN…, avec propagation temps réel aux interfaces connectées ;
- **snapshots système** : image complète du HUB (export de la base de données, fichiers,
  sauvegardes d'applications, configuration des conteneurs), compressée puis
  **chiffrée** (RSA-OAEP + AES-256-GCM), avec :
  - planification cron, rétention paramétrable, statistiques,
  - téléchargement de l'archive, **archivage optionnel vers stockage S3**,
  - **restauration avec prévisualisation** avant application ;
- **export / import d'environnement** : export logique de la configuration (utilisateurs,
  applications, rôles, comptes de service) et réimport sur une autre instance, en modes
  création/mise à jour.

**Bénéfice** — le HUB est lui-même un objet sauvegardé, restaurable et relocalisable.

---

## 8. Parcours « Exploitant / Supervision »

### 8.1 Audit et traçabilité

**Objectif** — savoir qui a fait quoi, quand et d'où.

**Fonctionnalités**
- **journal d'audit** : toutes les actions sensibles (connexions, gestions de comptes,
  approbations, sauvegardes…), filtrable par utilisateur, action, statut, adresse IP et
  période ;
- **sessions** : sessions actives (avec activité temps réel), historique, **révocation**
  individuelle ;
- **exports CSV** : journal, sessions, erreurs ;
- **purge automatique** par durée de rétention configurable.

**Bénéfice** — la reconstitution d'incidents et le contrôle périodique se font en
quelques clics.

### 8.2 Supervision des applications

**Objectif** — connaître en continu l'état de santé du parc.

**Fonctionnalités**
- **vérificateur de santé périodique** : état de chaque application, changement d'état
  diffusé en temps réel aux interfaces connectées ;
- **cycle de vie des conteneurs** : démarrage, arrêt, redémarrage d'une application
  depuis l'interface ;
- **journaux** d'une application consultables directement ;
- **gestion des versions** : comparaison version installée / version publiée sur le
  dépôt Git, consultation du journal des versions, des publications et de **l'écart
  entre deux versions** (aide à la décision de mise à jour).

**Bénéfice** — les dégradations sont vues avant d'être subies ; les mises à jour sont
décidées sur information, pas au hasard.

### 8.3 Erreurs et alertes

**Objectif** — ne pas laisser une erreur silencieuse pourrir.

**Fonctionnalités**
- **journal des erreurs** de l'API : capture automatique des exceptions avec **pile
  d'appels** complète ;
- statistiques d'erreurs, compteur de nouvelles erreurs (badge), détail par erreur ;
- **acquittement** global des erreurs traitées, purge ;
- **indicateurs (KPIs) et alertes** : vue de synthèse de l'activité (connexions,
  utilisateurs, applications, sauvegardes) et points d'attention.

**Bénéfice** — un seul endroit pour juger de la santé générale du système.

---

## 9. Parcours « Développeur d'applications »

### 9.1 Gabarit d'application (template)

**Objectif** — créer une application du parc standardisée en quelques minutes.

**Fonctionnalités**
- script de création d'application depuis le **gabarit commun** : structure projet,
  fichiers de configuration, ports, amorce d'enregistrement au HUB ;
- conventions immédiatement opérationnelles : interface (design system partagé, thème
  clair/sombre automatique), API backend avec authentification déjà branchée sur le
  HUB, tests, sauvegarde, notifications ;
- **allocation automatique des ports** par le HUB (plages front / API sans collision) ;
- premier démarrage avec vérification de santé, puis enregistrement et approbation.

**Bénéfice** — le développeur démarre sur des rails : sécurité, apparence, exploitation
et intégration sont déjà câblées.

### 9.2 Intégration machine-à-machine (M2M)

**Objectif** — permettre aux applications d'appeler le HUB (et d'autres applications)
sans intervention humaine.

**Fonctionnalités**
- **auto-enregistrement** : l'application naissante s'annonce au HUB et passe en attente
  d'approbation ; import en masse possible côté administration ;
- octroi **client_credentials** : l'application obtient des jetons avec ses identifiants
  de compte de service (secret régénérable, chiffré au repos) ;
- **découverte de services** : les applications déclarées « services partagés »
  publient leurs points d'entrée ; les autres applications les découvrent et les
  appellent avec leur jeton M2M ;
- **publication de spécification OpenAPI** par chaque application (catalogue consultable,
  export) ;
- **boîte d'envoi transactionnelle** (outbox) : les appels sortants différés sont
  persistés puis rejoués — pas de perte d'événement en cas d'indisponibilité passagère
  (statistiques, rejeu, nettoyage administrables) ;
- **rapports de dépendance** : une application déclare les services qu'elle consomme et
  synchronise son état de conformité.

**Bénéfice** — les applications composent entre elles de manière sécurisée et tracée,
avec résilience aux incidents réseau.

### 9.3 Authentification applicative (SSO)

**Objectif** — l'utilisateur entre dans une application sans nouveau mot de passe.

**Fonctionnalités**
- **lancement par le portail** : le HUB génère un jeton propre à l'application et
  redirige le navigateur ;
- **vérification locale** : l'application valide le jeton JWT avec la **clé publique**
  du HUB (point d'entrée JWKS standard) — aucune base d'utilisateurs, aucun appel au
  HUB à chaque requête ;
- **introspection** des jetons (RFC 7662) pour les applications qui préfèrent déléguer
  la vérification ;
- rôles transmis dans le jeton, enrichis des rôles applicatifs.

**Bénéfice** — une intégration SSO qui tient en quelques lignes côté application.

### 9.4 Fournisseur d'identité OIDC (applications tierces)

**Objectif** — faire du HUB le fournisseur d'identité d'applications tierces incapables
de vérifier nativement les jetons du HUB.

**Fonctionnalités**
- **point de découverte** OIDC standard (`/.well-known/openid-configuration`) ;
- flux **Authorization Code** (+ **PKCE S256**) et renouvellement — le flux le plus
  répandu dans les applications tierces ;
- **point d'information utilisateur** (userinfo) ;
- **déconnexion par canal arrière** (Back-Channel Logout) : la déconnexion au HUB
  révoque les jetons et notifie les applications tierces ;
- administration des **clients OIDC** : URI de redirection en liste stricte, scopes,
  secrets régénérables, activation ;
- lancement du flux depuis le tableau de bord du HUB ;
- limitation de débit dédiée sur le point d'émission des jetons.

**Bénéfice** — une application tierce du marché s'intègre au parc sans compte
dédupliqué et sans toucher à son code.

### 9.5 Déconnexion partout (SLO)

**Objectif** — quitter proprement toutes les applications, y compris sur un poste
partagé.

**Fonctionnalités**
- déconnexion du HUB **propagée** : les applications connectées sont purgées (fenêtres
  masquées de déconnexion) ;
- **détection de changement d'utilisateur** sur un poste partagé : au changement de
  session, les sessions résiduelles de l'utilisateur précédent sont collectées et
  révoquées, y compris auprès des applications tierces OIDC via des relais de
  révocation.

**Bénéfice** — pas de session fantôme sur un poste utilisé par plusieurs personnes.

### 9.6 Poussée de sauvegardes vers le HUB

**Objectif** — l'application participe à la politique de sauvegarde centrale.

**Fonctionnalités** (côté application)
- pousser une archive (ZIP + somme de contrôle) avec son jeton M2M ;
- consulter ses archives, télécharger, restaurer ;
- lire et appliquer sa configuration de sauvegarde (mode, cron, rétention) définie par
  l'administrateur — la planification est pilotée par le HUB.

**Bénéfice** — la sauvegarde applicative n'est pas un script oublié dans un coin : elle
est configurée, exécutée et surveillée centralement.

### 9.7 Notifications temps réel

**Objectif** — informer l'utilisateur depuis une application.

**Fonctionnalités**
- **flux d'événements SSE** : une connexion authentifiée par utilisateur (multi-onglets),
  battement de cœur 30 s (sert aussi au suivi d'activité des sessions), reconnexion
  automatique côté client ;
- diffusion d'événements de rafraîchissement (applications, utilisateurs, droits,
  santé, sauvegardes, messages…) — les interfaces se mettent à jour sans rechargement ;
- **messagerie applicative** : une application envoie un message à un utilisateur via
  le module de messagerie du HUB (§ 6.4).

**Bénéfice** — les applications héritent d'un canal temps réel sans infrastructure
additionnelle.

### 9.8 Mode atelier (workshop)

**Objectif** — permettre le développement et l'essai sans toucher à la production.

**Fonctionnalités**
- toute application dont le slug porte le suffixe d'atelier est identifiée comme
  **atelier** : elle tourne dans un projet de conteneurs séparé, aux côtés de la
  production (qui peut être à l'arrêt pendant l'atelier) ;
- **isolation stricte des clés M2M** : un compte de service d'un côté ne peut pas
  appeler l'autre côté ; la découverte de services filtre selon le monde
  production/atelier ;
- filtre tableau de bord « production / ateliers / tout » ; les ateliers sont exclus
  des rôles proposés aux utilisateurs.

**Bénéfice** — tester une nouvelle version d'application sur une copie, sans risque de
croisement avec le parc en service.

### 9.9 Automatisation (serveur MCP compagnon)

**Objectif** — piloter le HUB depuis des outils d'automatisation et assistants IA.

**Fonctionnalités**
- un composant compagnon expose **65 outils** couvrant l'ensemble de l'API : santé,
  gestion des applications, des utilisateurs, jetons, sauvegardes/restaurations,
  messages, audit, documentation, découverte de services — plus un outil d'**appel
  arbitraire** vers tout point d'API ;
- authentification par compte de service M2M (aucun partage de compte humain) ;
- utilisable en intégré (depuis une application du parc) ou en autonome (outils
  d'automatisation externes).

**Bénéfice** — les opérations courantes (créer un compte, déclencher une sauvegarde,
acquitter des erreurs…) s'exécutent par script ou en langage naturel.

### 9.10 Documentation d'intégration intégrée

**Objectif** — que le développeur n'ait jamais à chercher la doc.

**Fonctionnalités**
- **documentation d'intégration rendue par l'API** (consultable par les applications et
  les agents) : sections ciblées, raccourcis, version ;
- **journal des versions** de l'API consultable programmatiquement, filtrable par
  version, avec **écart entre deux versions** — base de la vérification de
  compatibilité.

**Bénéfice** — les outils (et les agents IA) découvrent et respectent les conventions du
HUB sans documentation externe à maintenir.

---

## Annexe A — Synthèse des domaines d'API

| Domaine | Périmètre (synthèse) |
|---|---|
| Authentification | Jetons, renouvellement, introspection, JWKS, TOTP, récupération, SSO/lancement |
| Utilisateurs | CRUD, rôles, opérations en masse, import CSV, sessions |
| Demandes de compte | Formulaire public, suivi, approbation/rejet, notification des administrateurs |
| Applications | Registre, approbation, ports, scanner Docker, dépendances, découverte M2M |
| Comptes de service | CRUD M2M, secrets, périmètre d'applications |
| Santé des applications | État, cycle de vie conteneurs, journaux, versions/dépôt Git |
| Sauvegardes d'applications | Réception, planification, rétention, export chiffré, restauration, orphelins |
| Snapshots système | Déclenchement, configuration, restauration avec aperçu, S3 |
| Audit | Journal, sessions, erreurs, statistiques, exports CSV |
| Messages | Envoi ciblé/général/programmé, modèles, reçus, messagerie M2M |
| Événements | Flux SSE, état |
| OIDC | Découverte, autorisation, jetons, userinfo, déconnexion arrière, clients |
| VPN | Serveur, pairs, configurations client, QR codes |
| PKI / certificats | Génération CA et certificats serveur (outils intégrés) |
| Paramètres | Clé/valeur à chaud |
| Catalogue GitHub | Dépôts en cache, rafraîchissement, préparation de création d'app |
| Étiquettes utilisateur | Catégories, affectation, accusés de consultation |
| Profils | Gabarits de rôles applicables |
| Documentation | Intégration, sections, journal des versions, écarts |

## Annexe B — Glossaire

| Terme | Définition |
|---|---|
| **SSO** | Single Sign-On — authentification unique donnant accès à plusieurs applications |
| **JWT** | Jeton d'accès signé (JSON Web Token) transportant l'identité et les rôles |
| **RS256 / JWKS** | Signature RSA asymétrique / point de publication standard des clés publiques |
| **M2M** | Machine-to-Machine — échanges entre applications sans utilisateur humain |
| **OIDC** | OpenID Connect — protocole standard de fournisseur d'identité (au-dessus d'OAuth 2.0) |
| **PKCE** | Protection du flux d'autorisation sur clients publics |
| **TOTP** | Second facteur par code à usage unique généré par application d'authentification |
| **SSE** | Server-Sent Events — flux d'événements temps réel du serveur vers le navigateur |
| **PKI** | Infrastructure de clés et certificats (autorité de certification interne) |
| **SNI** | Server Name Indication — routage TLS par nom de domaine demandé |
| **WireGuard / VPN** | Réseau privé chiffré donnant un accès distant au parc |
| **Quiesce** | Mise au repos temporaire d'un service pendant une sauvegarde |
| **Outbox transactionnelle** | File persistante garantissant la livraison différée des appels sortants |
| **Snapshot** | Image complète et chiffrée du HUB (à distinguer des sauvegardes d'applications) |
| **Atelier (workshop)** | Copie sandbox d'une application pour développement/essais, isolée de la production |
| **MCP** | Protocole d'outillage pour assistants IA — expose l'API du HUB comme outils |
| **Cron** | Expression de planification horaire d'une tâche récurrente |

## Annexe C — Fiche technique récapitulative

| Élément | Valeur |
|---|---|
| Version documentée | 3.29.0 |
| Composants livrés | API (FastAPI), interface web (SPA native), PostgreSQL, frontal Nginx, serveur VPN WireGuard, serveur MCP compagnon (65 outils) |
| Empaquetage | Docker Compose (projet unique pour le HUB ; un projet par application du parc) |
| Plages réservées aux applications | Front : 4200-4699 · API : 9000-9499 (allocation gérée par le HUB) |
| Ports exposés | Frontal web (HTTP/HTTPS) et UDP VPN — tous les autres services restent internes |
| Prérequis d'exécution | Hôte Docker, accès socket Docker (lecture + pilotage ciblé) ; clés RSA et clé de chiffrement des secrets générées au premier démarrage |
| Comptes | 1 administrateur minimum (mot de passe forcément changé à la première connexion) |
| Sauvegardes | Applications : planification cron + rétention par nombre · HUB : snapshots chiffrés (rétention 7 / 24 h par défaut), archivage S3 optionnel |
| Vérification | ≈ 630 tests automatisés — authentification, OIDC, SLO, M2M, sauvegardes, snapshots, VPN, PKI, migrations |
