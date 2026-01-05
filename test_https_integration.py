#!/usr/bin/env python3
"""
Test d'intégration HTTPS avec le Hub.

Ce script vérifie que le certificat CA du Hub permet une connexion sécurisée.
"""
import httpx
import json
from pathlib import Path

# Configuration
HUB_HTTPS_URL = "https://localhost"
CA_CERT_PATH = Path("certs/ca/ca.crt")

def test_without_ca():
    """Test sans certificat CA (devrait échouer ou warning)."""
    print("=" * 60)
    print("Test 1: Connexion HTTPS SANS certificat CA")
    print("=" * 60)
    
    try:
        # verify=False pour ignorer les erreurs SSL
        response = httpx.get(f"{HUB_HTTPS_URL}/api/users/me", verify=False, timeout=5)
        print(f"  ⚠️  Connexion OK mais non sécurisée (verify=False)")
        print(f"  Status: {response.status_code}")
    except Exception as e:
        print(f"  ❌ Erreur: {e}")


def test_with_ca():
    """Test avec certificat CA (doit fonctionner sans warning)."""
    print("\n" + "=" * 60)
    print("Test 2: Connexion HTTPS AVEC certificat CA")
    print("=" * 60)
    
    if not CA_CERT_PATH.exists():
        print(f"  ❌ Certificat CA non trouvé: {CA_CERT_PATH}")
        return
    
    print(f"  📜 CA: {CA_CERT_PATH}")
    
    try:
        # verify=CA_CERT_PATH pour utiliser notre CA
        response = httpx.get(
            f"{HUB_HTTPS_URL}/health", 
            verify=str(CA_CERT_PATH),
            timeout=5
        )
        print(f"  ✅ Connexion sécurisée réussie!")
        print(f"  Status: {response.status_code}")
        print(f"  Body: {response.text[:100]}...")
    except httpx.ConnectError as e:
        print(f"  ❌ Erreur de connexion: {e}")
    except httpx.SSLError as e:
        print(f"  ❌ Erreur SSL: {e}")
    except Exception as e:
        print(f"  ❌ Erreur: {type(e).__name__}: {e}")


def test_login_with_ca():
    """Test de login complet avec CA."""
    print("\n" + "=" * 60)
    print("Test 3: Login API via HTTPS avec certificat CA")
    print("=" * 60)
    
    if not CA_CERT_PATH.exists():
        print(f"  ❌ Certificat CA non trouvé: {CA_CERT_PATH}")
        return
    
    try:
        response = httpx.post(
            f"{HUB_HTTPS_URL}/api/auth/login",
            json={"username": "admin", "password": "admin123"},
            verify=str(CA_CERT_PATH),
            timeout=10
        )
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ Login réussi!")
            print(f"  Token: {data.get('access_token', 'N/A')[:50]}...")
            print(f"  Expire: {data.get('expires_at', 'N/A')}")
            return data.get('access_token')
        else:
            print(f"  ❌ Échec login: {response.text}")
    except Exception as e:
        print(f"  ❌ Erreur: {type(e).__name__}: {e}")
    
    return None


def test_pki_api(token: str):
    """Test API PKI avec authentification."""
    print("\n" + "=" * 60)
    print("Test 4: API PKI via HTTPS")
    print("=" * 60)
    
    if not token:
        print("  ⏭️  Skipped (pas de token)")
        return
    
    try:
        response = httpx.get(
            f"{HUB_HTTPS_URL}/api/pki/status",
            headers={"Authorization": f"Bearer {token}"},
            verify=str(CA_CERT_PATH),
            timeout=10
        )
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ PKI Status récupéré!")
            print(f"  CA initialisée: {data.get('initialized')}")
            if data.get('ca'):
                print(f"  CA CN: {data['ca'].get('common_name')}")
                print(f"  CA expire: {data['ca'].get('days_remaining')} jours")
            print(f"  Certificats: {len(data.get('certificates', []))}")
        else:
            print(f"  ❌ Erreur: {response.text}")
    except Exception as e:
        print(f"  ❌ Erreur: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("\n🔐 TEST D'INTÉGRATION HTTPS AVEC PKI HUB\n")
    
    test_without_ca()
    test_with_ca()
    token = test_login_with_ca()
    test_pki_api(token)
    
    print("\n" + "=" * 60)
    print("✅ Tests terminés")
    print("=" * 60)
