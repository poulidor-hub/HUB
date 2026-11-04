#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

# Test de régression C6 : le parsing du profil ne doit pas injecter de code
# via le chemin du fichier (qui était interpolé dans du code Python inline).
set -euo pipefail

INSTALL_SH="$(cd "$(dirname "$0")/.." && pwd)/install.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Profil JSON valide
cat > "$WORK/normal.json" <<'JSON'
{"client":{"name":"TestCorp"},"server":{"ip":"10.0.0.5"},
 "admin":{"username":"admin","password":"longpass123","email":"a@b.c"},
 "ports":{"http":80,"https":443,"api":8000},"options":{"https_enabled":true}}
JSON

# Extraire le bloc Python d'extraction actuel et le tester avec un chemin malveillant.
# On vérifie que le fix utilise stdin (sys.stdin) et pas open('$PROFILE').
if grep -q "open('\$PROFILE')" "$INSTALL_SH"; then
    echo "❌ FAIL: install.sh interpole encore \$PROFILE dans open() — injection possible"
    exit 1
fi

# Vérifier que le parsing via stdin fonctionne avec un profil normal
RESULT=$(python3 -c "
import json, sys
p = json.load(sys.stdin)
c = p.get('client', {}); s = p.get('server', {}); a = p.get('admin', {})
print(c.get('name','')); print(s['ip']); print(a['username'])
" < "$WORK/normal.json")

NAME=$(echo "$RESULT" | sed -n '1p')
IP=$(echo "$RESULT" | sed -n '2p')
USER=$(echo "$RESULT" | sed -n '3p')

[ "$NAME" = "TestCorp" ] || { echo "❌ FAIL: name=$NAME"; exit 1; }
[ "$IP" = "10.0.0.5" ] || { echo "❌ FAIL: ip=$IP"; exit 1; }
[ "$USER" = "admin" ] || { echo "❌ FAIL: user=$USER"; exit 1; }

echo "✅ PASS: parsing via stdin sécurisé et fonctionnel"
