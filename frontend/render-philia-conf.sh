#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# =============================================================================
# Rendu du vhost PhiliaNG : remplace UNIQUEMENT le jeton __PHILIA_NG_DOMAIN__
# par le domaine donné.
#
# Ni envsubst ni substitution générale : les variables nginx ($host,
# $request_uri, $remote_addr...) doivent survivre intactes.
#
# Usage : render-philia-conf.sh <fichier_modèle> <fichier_sortie> <domaine>
# Code 0 : vhost écrit. Code 1 : refus ou échec — rien n'est écrit, la sortie
# existante reste intacte et aucun fichier temporaire ne subsiste.
#
# POSIX sh et sed uniquement : l'image hub-frontend (nginx:alpine) n'a que
# BusyBox — ni bash, ni envsubst.
# =============================================================================

set -eu

TOKEN=__PHILIA_NG_DOMAIN__

echec() {
    printf '❌ render-philia-conf.sh : %s\n' "$*" >&2
    exit 1
}

[ "$#" -eq 3 ] || echec "usage : render-philia-conf.sh <fichier_modèle> <fichier_sortie> <domaine> — $# argument(s) reçu(s) au lieu de 3"

TEMPLATE=$1
OUTPUT=$2
DOMAIN=$3

# Le domaine finit dans une directive server_name et dans le script sed :
# rien d'autre que des lettres, chiffres, points et tirets.
case "$DOMAIN" in
    ""|*[!A-Za-z0-9.-]*)
        echec "domaine PhiliaNG refusé, vide ou hors de A-Za-z0-9.- : '$DOMAIN'"
        ;;
esac

# Rendu dans un fichier voisin de la sortie, renommé seulement une fois
# complet : une sortie à moitié écrite ne remplace jamais l'ancienne.
TMP="$OUTPUT.tmp"
if ! sed "s/$TOKEN/$DOMAIN/g" "$TEMPLATE" > "$TMP"; then
    rm -f "$TMP"
    echec "rendu impossible : $TEMPLATE → $OUTPUT"
fi
if ! mv "$TMP" "$OUTPUT"; then
    rm -f "$TMP"
    echec "écriture impossible vers $OUTPUT"
fi
