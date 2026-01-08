# Dossier de stockage des sauvegardes d'applications

Ce dossier contient les sauvegardes centralisées des applications du HUB.

## Structure

```
backups/
├── {app_slug}/
│   ├── backup_{uuid}.zip          # Backup original
│   └── backup_{uuid}.encrypted    # Backup chiffré (après export)
├── {autre_app}/
│   └── ...
└── README.md
```

## Format des fichiers

### Backups originaux (*.zip)
Archives ZIP contenant:
- `data.json` : Données de l'application (items, préférences, métadonnées)
- `files/` : Fichiers uploadés organisés par owner/category

### Backups chiffrés (*.encrypted)
Fichiers chiffrés avec un schéma hybride RSA + AES-256-GCM:
- En-tête JSON avec métadonnées et clé AES chiffrée
- Données chiffrées avec AES-256-GCM

## Sécurité

⚠️ Ces fichiers contiennent des données sensibles.
- Les backups originaux ne sont pas chiffrés
- Utilisez l'export chiffré pour les transferts externes
- Seule la clé privée RSA du HUB peut déchiffrer les exports
