#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Test du mode M2M pour une application en mode "remote" (is_remote_app=true).

Ce script simule une application sur un serveur distant qui :
1. Télécharge le certificat CA du Hub
2. S'enregistre avec custom_host (son propre IP/host)
3. Obtient un token M2M
4. Utilise ce token pour communiquer avec d'autres services

Usage:
    python test_remote_app_m2m.py
"""
import httpx
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# Configuration
# Détecter si on est dans un conteneur Docker
import os
IN_DOCKER = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER', False)

HUB_HOST = "localhost" if IN_DOCKER else "192.0.2.11"  # IP du HUB
HUB_API_PORT = 8000
HUB_API_URL = f"http://{HUB_HOST}:{HUB_API_PORT}"

# Simuler une app sur un "serveur distant" (même machine mais avec custom_host différent)
# Le custom_host doit rester l'IP externe même depuis le conteneur
REMOTE_APP_HOST = "192.0.2.11"  # Dans un vrai cas, ce serait une autre IP
REMOTE_APP_SLUG = "test-remote-app"
REMOTE_APP_NAME = "Test Remote Application"
REMOTE_APP_API_PORT = 19045  # Port fictif pour le test
REMOTE_APP_FRONTEND_PORT = 14250

# Stockage des credentials
credentials = {}


def print_section(title: str):
    """Affiche un titre de section."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(success: bool, message: str):
    """Affiche le résultat d'un test."""
    icon = "✅" if success else "❌"
    print(f"{icon} {message}")


async def test_1_download_ca_certificate():
    """Test 1: Téléchargement du certificat CA du Hub."""
    print_section("Test 1: Téléchargement du certificat CA")
    
    ca_url = f"{HUB_API_URL}/api/pki/ca.crt"
    print(f"📥 URL: {ca_url}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ca_url)
            
            if response.status_code == 200:
                cert_content = response.text
                if cert_content.startswith("-----BEGIN CERTIFICATE-----"):
                    print_result(True, f"Certificat CA téléchargé ({len(cert_content)} caractères)")
                    
                    # Sauvegarder pour les tests suivants
                    cert_path = Path("/tmp/hub-ca-test.crt")
                    cert_path.write_text(cert_content)
                    print(f"   💾 Sauvegardé dans: {cert_path}")
                    return True
                else:
                    print_result(False, "Le contenu n'est pas un certificat PEM valide")
                    return False
            elif response.status_code == 404:
                print_result(False, "CA non initialisée sur le Hub (404)")
                print("   ℹ️  Exécutez 'python pki.py init' sur le Hub")
                return False
            else:
                print_result(False, f"Erreur HTTP {response.status_code}: {response.text}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_2_register_remote_app():
    """Test 2: Enregistrement d'une app distante avec custom_host."""
    global credentials
    print_section("Test 2: Enregistrement de l'app distante")
    
    register_url = f"{HUB_API_URL}/api/apps/register"
    
    payload = {
        "slug": REMOTE_APP_SLUG,
        "name": REMOTE_APP_NAME,
        "description": "Application de test pour le mode distant M2M",
        "api_port": REMOTE_APP_API_PORT,
        "frontend_port": REMOTE_APP_FRONTEND_PORT,
        "custom_host": REMOTE_APP_HOST,  # <- Le host personnalisé
        "is_shared": False
    }
    
    print(f"📝 Enregistrement avec custom_host={REMOTE_APP_HOST}")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(register_url, json=payload)
            data = response.json()
            
            if response.status_code == 200:
                is_new = data.get("is_new", False)
                pending = data.get("pending_approval", False)
                app_info = data.get("application", {})
                creds = data.get("credentials", {})
                
                print_result(True, f"App {'créée' if is_new else 'existante'}")
                print(f"   📋 Message: {data.get('message', 'N/A')}")
                print(f"   🔐 Pending approval: {pending}")
                print(f"   🌐 is_remote: {app_info.get('is_remote', 'N/A')}")
                print(f"   🌍 registered_from_ip: {app_info.get('registered_from_ip', 'N/A')}")
                print(f"   🔗 api_url: {app_info.get('api_url', 'N/A')}")
                
                if creds:
                    credentials = {
                        "client_id": creds.get("client_id"),
                        "client_secret": creds.get("client_secret"),
                        "hub_host": creds.get("hub_host"),
                    }
                    print(f"   🔑 client_id: {credentials['client_id']}")
                    print(f"   🔑 client_secret: {'*' * 10}...{credentials['client_secret'][-4:] if credentials.get('client_secret') else 'N/A'}")
                
                return True
            elif response.status_code == 403:
                print_result(False, f"Accès refusé (IP non autorisée): {data}")
                print("   ℹ️  Vérifiez APP_WHITELIST_NETWORKS dans le .env du Hub")
                return False
            else:
                print_result(False, f"Erreur {response.status_code}: {data}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_3_regenerate_credentials():
    """Test 3: Régénération des credentials si nécessaire."""
    global credentials
    print_section("Test 3: Régénération des credentials M2M")
    
    if not credentials.get("client_id"):
        print("   ⚠️  Pas de client_id, skip ce test")
        return False
    
    # Si on a déjà le secret, pas besoin de régénérer
    if credentials.get("client_secret"):
        print_result(True, "Secret déjà disponible depuis l'enregistrement")
        return True
    
    regen_url = f"{HUB_API_URL}/api/apps/me/regenerate-secret"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                regen_url,
                headers={"X-Slug": REMOTE_APP_SLUG}
            )
            data = response.json()
            
            if response.status_code == 200:
                credentials["client_secret"] = data.get("client_secret")
                print_result(True, "Secret régénéré avec succès")
                print(f"   🔑 Nouveau secret: {'*' * 10}...{credentials['client_secret'][-4:]}")
                return True
            else:
                print_result(False, f"Erreur {response.status_code}: {data}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_4_get_m2m_token():
    """Test 4: Obtention d'un token M2M."""
    global credentials
    print_section("Test 4: Obtention du token M2M")
    
    if not credentials.get("client_id") or not credentials.get("client_secret"):
        print("   ⚠️  Credentials manquants, skip ce test")
        return False
    
    token_url = f"{HUB_API_URL}/api/auth/token"
    
    # Utiliser form data pour OAuth2
    form_data = {
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "grant_type": "client_credentials"
    }
    
    print(f"📝 Demande de token avec client_id={credentials['client_id']}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=form_data)
            data = response.json()
            
            if response.status_code == 200:
                access_token = data.get("access_token")
                expires_in = data.get("expires_in", 0)
                
                credentials["access_token"] = access_token
                
                print_result(True, "Token M2M obtenu")
                print(f"   🎫 Token: {access_token[:50]}...")
                print(f"   ⏱️  Expire dans: {expires_in}s")
                return True
            elif response.status_code == 401:
                print_result(False, "Authentification échouée (credentials invalides ou app non approuvée)")
                print(f"   ℹ️  Message: {data.get('detail', data)}")
                print("   ℹ️  L'app doit être approuvée par un admin avant de pouvoir obtenir un token")
                return False
            else:
                print_result(False, f"Erreur {response.status_code}: {data}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_5_use_token_to_call_api():
    """Test 5: Utilisation du token M2M pour appeler une API."""
    print_section("Test 5: Appel API avec token M2M")
    
    if not credentials.get("access_token"):
        print("   ⚠️  Pas de token M2M, skip ce test")
        return False
    
    # Appeler l'endpoint de découverte des services
    discover_url = f"{HUB_API_URL}/api/apps/discover"
    
    headers = {
        "Authorization": f"Bearer {credentials['access_token']}",
        "X-Slug": REMOTE_APP_SLUG
    }
    
    print(f"📡 Appel: GET {discover_url}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(discover_url, headers=headers)
            data = response.json()
            
            if response.status_code == 200:
                services = data.get("services", [])
                print_result(True, f"Découverte réussie: {len(services)} service(s) trouvé(s)")
                
                for svc in services:
                    print(f"   🔗 {svc.get('slug')}: {svc.get('api_url')} (shared={svc.get('is_shared')})")
                
                return True
            elif response.status_code == 401:
                print_result(False, f"Token invalide ou expiré: {data}")
                return False
            else:
                print_result(False, f"Erreur {response.status_code}: {data}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_6_verify_app_info():
    """Test 6: Vérifier les informations de l'app enregistrée."""
    print_section("Test 6: Vérification des infos de l'app")
    
    # Utiliser l'endpoint /api/apps/{slug} pour vérifier l'app
    app_url = f"{HUB_API_URL}/api/apps/{REMOTE_APP_SLUG}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(app_url)
            
            if response.status_code == 200:
                app = response.json()
                
                is_remote = app.get("is_remote", False)
                registered_ip = app.get("registered_from_ip", "N/A")
                api_url = app.get("api_url", "N/A")
                approval_status = app.get("approval_status", "N/A")
                
                print_result(True, f"App '{REMOTE_APP_SLUG}' trouvée")
                print(f"   🌐 is_remote: {is_remote}")
                print(f"   🌍 registered_from_ip: {registered_ip}")
                print(f"   🔗 api_url: {api_url}")
                print(f"   📋 approval_status: {approval_status}")
                print(f"   ✅ is_active: {app.get('is_active', False)}")
                
                # Vérifier que l'URL contient bien le custom_host
                if REMOTE_APP_HOST in api_url:
                    print_result(True, f"L'api_url utilise bien le custom_host ({REMOTE_APP_HOST})")
                else:
                    print_result(False, f"L'api_url n'utilise pas le custom_host attendu")
                
                return is_remote
            elif response.status_code == 404:
                print_result(False, "App non trouvée")
                return False
            else:
                print_result(False, f"Erreur {response.status_code}")
                return False
                
    except httpx.RequestError as e:
        print_result(False, f"Erreur de connexion: {e}")
        return False


async def test_7_cleanup():
    """Test 7: Nettoyage - Suppression de l'app de test."""
    print_section("Test 7: Nettoyage (optionnel)")
    
    print("   ℹ️  Pour supprimer l'app de test, un admin doit utiliser:")
    print(f"   DELETE {HUB_API_URL}/api/apps/{REMOTE_APP_SLUG}")
    print("   ⚠️  Nettoyage non effectué automatiquement (nécessite auth admin)")
    
    return True


async def main():
    """Exécute tous les tests."""
    print("\n" + "="*60)
    print("  🧪 TESTS M2M POUR APPLICATION DISTANTE (is_remote=true)")
    print("="*60)
    print(f"\n📍 Configuration:")
    print(f"   HUB: {HUB_API_URL}")
    print(f"   Remote App Host: {REMOTE_APP_HOST}")
    print(f"   Remote App Slug: {REMOTE_APP_SLUG}")
    print(f"   Remote App Ports: API={REMOTE_APP_API_PORT}, Frontend={REMOTE_APP_FRONTEND_PORT}")
    print(f"\n⏰ Début des tests: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    
    # Test 1: Téléchargement CA
    results["1_download_ca"] = await test_1_download_ca_certificate()
    
    # Test 2: Enregistrement avec custom_host
    results["2_register_remote"] = await test_2_register_remote_app()
    
    # Test 3: Régénération credentials
    results["3_regen_credentials"] = await test_3_regenerate_credentials()
    
    # Test 4: Token M2M
    results["4_get_token"] = await test_4_get_m2m_token()
    
    # Test 5: Appel API avec token
    results["5_use_token"] = await test_5_use_token_to_call_api()
    
    # Test 6: Vérification app info
    results["6_verify_app"] = await test_6_verify_app_info()
    
    # Test 7: Cleanup info
    results["7_cleanup"] = await test_7_cleanup()
    
    # Résumé
    print_section("RÉSUMÉ DES TESTS")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, result in results.items():
        icon = "✅" if result else "❌"
        print(f"   {icon} {name}")
    
    print(f"\n   📊 Résultat: {passed}/{total} tests réussis")
    
    if passed == total:
        print("\n   🎉 TOUS LES TESTS SONT PASSÉS!")
    elif results["2_register_remote"] and not results["4_get_token"]:
        print("\n   ⚠️  L'app est enregistrée mais en attente d'approbation admin.")
        print("   ℹ️  Approuvez l'app via l'interface Hub, puis relancez les tests.")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
