# Politique de sécurité

## Signaler une vulnérabilité

**Ne pas ouvrir d'issue publique.** Signalez toute vulnérabilité par e-mail à
l'adresse de contact indiquée dans le dépôt principal du projet
([POULIDOR](../POULIDOR/SECURITY.md)) ou via GitHub Security Advisories
(onglet *Security* → *Report a vulnerability*).

- Réponse visée : accusé de réception sous 72 h, évaluation sous 7 jours.
- Merci d'inclure : version concernée (`VERSION`), étapes de reproduction,
  impact estimé.
- Divulgation coordonnée : crédit au rapporteur après correction, sauf demande
  contraire.

## Périmètre

Sont concernés : le backend FastAPI (`hub/`), le frontend React (`frontend/`),
la PKI interne, le routage DNS/SNI, les flux d'authentification (JWT RS256,
M2M) et les images Docker publiées.

## Versions supportées

La dernière version publiée est supportée ; les versions antérieures ne
reçoivent de correctifs que pour des vulnérabilités critiques.
