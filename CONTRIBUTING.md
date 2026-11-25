# Contribuer à HUB_base

Merci de votre intérêt ! HUB_base est le centre SSO du stack POULIDOR
(AGPLv3). Les conventions complètes (CLA, double licence, style de code,
process de release) vivent dans le [CONTRIBUTING.md du méta-repo
POULIDOR](https://github.com/POULIDOR-Audit-Developpement/POULIDOR/blob/main/CONTRIBUTING.md).

## En résumé

1. **Fork + branche** : une branche par chantier (`feat/…`, `fix/…`).
2. **Commits** : style Conventional Commits (`feat:`, `fix:`, `docs:`…).
3. **Tests** : toute modification de route/modèle/hub_auth doit passer la
   suite pytest (`make test` dans le conteneur) ; vérification complète via
   le cycle Docker du dépôt.
4. **Hooks** : le pare-feu de dépôt (scan de secrets et contrôle de l'auteur
   au commit, garde des valeurs interdites au push) s'installe depuis le
   méta-repo POULIDOR — `bash scripts/hooks/install-hooks.sh` à sa racine,
   `--all` pour l'étendre aux sous-modules (gitleaks requis :
   https://github.com/gitleaks/gitleaks). Le script de ce dépôt est le même :
   lancé hors du méta-repo, il refuse et n'écrit rien.
5. **Docs** : toute nouvelle route est documentée dans `INTEGRATION.md` et
   l'entrée CHANGELOG associée (bump de `VERSION` obligatoire).
6. **CLA** : toute première contribution est accompagnée d'un CLA
   (voir le méta-repo).

## Questions

Ouvrez une discussion (*Discussions* du dépôt principal) plutôt qu'une issue
pour les questions d'usage.
