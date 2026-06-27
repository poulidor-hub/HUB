#!/usr/bin/env bash
# =============================================================================
# Script de migration post-upgrade — sécurise les secrets faibles hérités.
#
# À exécuter UNE SEULE FOIS après une mise à jour du Hub, dans le dossier HUB_base.
# Prérequis : le Hub doit être en cours d'exécution (docker compose up -d).
#
# ⚠️  SÉCURITÉ : Ce script ne touche JAMAIS aux données applicatives.
#     Il modifie uniquement les credentials d'authentification (.env + PostgreSQL).
#     Aucune application, backup, utilisateur, rôle ou donnée métier n'est affecté.
#     Une sauvegarde du .env est créée avant toute modification.
#     Rollback : restaurer la sauvegarde .env.bak.* + ALTER USER avec l'ancien mdp.
#
# Ce script :
#   1. Détecte les mots de passe DB/admin faibles dans .env
#   2. Génère de nouveaux secrets forts
#   3. Met à jour .env ET la base de données (ALTER USER)
#   4. Affiche un résumé
#
# Usage :
#   cd HUB_base
#   chmod +x scripts/migrate-legacy-secrets.sh
#   bash scripts/migrate-legacy-secrets.sh
# =============================================================================
set -euo pipefail

ENV_FILE=".env"
BACKUP_SUFFIX=".bak.$(date +%Y%m%d_%H%M%S)"

# ── Couleurs ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}🔐 migrate-legacy-secrets — v1.0${NC}"
echo -e "${GREEN}✅ Ce script ne modifie QUE les credentials (.env + ALTER USER).${NC}"
echo -e "${GREEN}   Aucune donnée applicative, backup ou utilisateur n'est touché.${NC}"
echo ""

# ── 1. Vérifier que .env existe ──
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}❌ $ENV_FILE introuvable. Lancez ce script depuis le dossier HUB_base.${NC}"
    exit 1
fi

# Charger les valeurs actuelles
source <(grep -E '^(POSTGRES_USER|POSTGRES_PASSWORD|ADMIN_PASSWORD|DATABASE_URL)=' "$ENV_FILE" | sed 's/"/\\"/g')
CURRENT_PG_USER="${POSTGRES_USER:-hubadmin}"
CURRENT_PG_PWD="${POSTGRES_PASSWORD:-}"
CURRENT_ADMIN_PWD="${ADMIN_PASSWORD:-}"
CURRENT_DB_URL="${DATABASE_URL:-}"

WEAK_DB=false
WEAK_ADMIN=false

# ── 2. Détecter les secrets faibles ──
WEAK_PATTERNS=("admin" "password" "admin123" "CHANGEZ_MOI" "changeme")

for pattern in "${WEAK_PATTERNS[@]}"; do
    if echo "${CURRENT_PG_PWD,,}" | grep -q "$pattern"; then
        WEAK_DB=true
        break
    fi
done
if [[ ${#CURRENT_PG_PWD} -lt 8 ]]; then
    WEAK_DB=true
fi

for pattern in "${WEAK_PATTERNS[@]}"; do
    if echo "${CURRENT_ADMIN_PWD,,}" | grep -q "$pattern"; then
        WEAK_ADMIN=true
        break
    fi
done
if [[ ${#CURRENT_ADMIN_PWD} -lt 8 ]]; then
    WEAK_ADMIN=true
fi

if ! $WEAK_DB && ! $WEAK_ADMIN; then
    echo -e "${GREEN}✅ Aucun mot de passe faible détecté. Rien à faire.${NC}"
    exit 0
fi

# ── 3. Générer de nouveaux secrets ──
NEW_PG_PWD="$CURRENT_PG_PWD"
NEW_ADMIN_PWD="$CURRENT_ADMIN_PWD"

if $WEAK_DB; then
    NEW_PG_PWD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    echo -e "${YELLOW}⚠️  Mot de passe DB faible détecté → régénération${NC}"
fi

if $WEAK_ADMIN; then
    NEW_ADMIN_PWD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    echo -e "${YELLOW}⚠️  Mot de passe ADMIN faible détecté → régénération${NC}"
fi

echo ""
echo -e "${BOLD}Résumé des changements :${NC}"

if $WEAK_DB; then
    echo -e "  🔑 POSTGRES_PASSWORD    → régénéré (DB mise à jour via ALTER USER)"
fi
if $WEAK_ADMIN; then
    echo -e "  🔑 ADMIN_PASSWORD       → régénéré (pas d'impact DB, utilisateur Hub uniquement)"
fi

echo ""
echo -e "${YELLOW}⚠️  Confirmez-vous la migration ? (oui/non)${NC}"
read -r CONFIRM
if [[ "${CONFIRM,,}" != "oui" && "${CONFIRM,,}" != "o" && "${CONFIRM,,}" != "yes" && "${CONFIRM,,}" != "y" ]]; then
    echo -e "${YELLOW}⏭️  Migration annulée.${NC}"
    exit 0
fi
echo ""

# ── 4. Sauvegarder .env ──
cp "$ENV_FILE" "${ENV_FILE}${BACKUP_SUFFIX}"
echo -e "  📄 Sauvegarde : ${ENV_FILE}${BACKUP_SUFFIX}"

# ── 5. Mettre à jour .env ──
if $WEAK_DB; then
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$NEW_PG_PWD/" "$ENV_FILE"
    # Mettre à jour le mot de passe dans DATABASE_URL
    sed -i "s|://${CURRENT_PG_USER}:[^@]*@|://${CURRENT_PG_USER}:${NEW_PG_PWD}@|" "$ENV_FILE"
    echo -e "  🔑 POSTGRES_PASSWORD    : ${RED}${CURRENT_PG_PWD}${NC} → ${GREEN}${NEW_PG_PWD}${NC}"
fi

if $WEAK_ADMIN; then
    sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$NEW_ADMIN_PWD/" "$ENV_FILE"
    echo -e "  🔑 ADMIN_PASSWORD       : ${RED}${CURRENT_ADMIN_PWD}${NC} → ${GREEN}${NEW_ADMIN_PWD}${NC}"
fi

# ── 6. Mettre à jour la base de données (si le conteneur db tourne) ──
if $WEAK_DB; then
    if docker compose ps db 2>/dev/null | grep -q "Up"; then
        echo ""
        echo -n "  🗄️  Mise à jour du mot de passe dans PostgreSQL... "
        if docker compose exec -T db psql -U "$CURRENT_PG_USER" -d hub_auth \
            -c "ALTER USER \"$CURRENT_PG_USER\" WITH PASSWORD '$NEW_PG_PWD';" > /dev/null 2>&1; then
            echo -e "${GREEN}OK${NC}"
        else
            echo -e "${RED}ÉCHEC${NC}"
            echo -e "     → Exécutez manuellement dans le conteneur db :"
            echo -e "       ALTER USER \"$CURRENT_PG_USER\" WITH PASSWORD '...';"
        fi
    else
        echo ""
        echo -e "  ${YELLOW}⚠️  Conteneur db arrêté — exécutez après redémarrage :${NC}"
        echo -e "     ALTER USER \"$CURRENT_PG_USER\" WITH PASSWORD '$NEW_PG_PWD';"
    fi
fi

# ── 7. Résumé final ──
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Migration terminée.${NC}"
echo ""
echo -e "  ${BOLD}Prochaines étapes :${NC}"
echo -e "  1. Copiez ${GREEN}SECRETS_ENCRYPTION_KEY${NC} dans .env si vide :"
echo -e "     $(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
echo ""
echo "  2. Redémarrez le Hub avec les nouveaux secrets :"
echo "     docker compose up -d --force-recreate"
echo ""
echo -e "  3. Vérifiez les logs (aucun warning ne doit apparaître) :"
echo "     docker compose logs hub-api | grep -E '⚠️|ADMIN_PASSWORD|base de données'"
echo ""
echo -e "  4. Supprimez la sauvegarde quand tout est OK :"
echo -e "     rm ${ENV_FILE}${BACKUP_SUFFIX}"
echo ""
echo -e "  ${RED}🔄 ROLLBACK (si problème) :${NC}"
echo -e "     cp ${ENV_FILE}${BACKUP_SUFFIX} ${ENV_FILE}"
if $WEAK_DB; then
echo -e "     docker compose exec db psql -U ${CURRENT_PG_USER} -d hub_auth -c \"ALTER USER \\\"${CURRENT_PG_USER}\\\" WITH PASSWORD '${CURRENT_PG_PWD}';\""
fi
echo -e "     docker compose up -d --force-recreate"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
