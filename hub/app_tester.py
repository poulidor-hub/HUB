"""
Testeur d'Application pour le Hub d'Authentification
=====================================================

Ce module permet de valider le bon fonctionnement d'une application
avec le Hub d'authentification. Il teste :

1. La connectivité avec le Hub
2. L'authentification (login, refresh, logout)
3. L'intégration SSO
4. La validation des tokens JWT
5. La communication M2M (service accounts)
6. Les endpoints de l'application cible

Usage:
------
    # Mode ligne de commande
    python -m app_tester --hub-url http://localhost:8000 --app-slug salarie
    
    # Mode interactif
    python -m app_tester --interactive
    
    # Mode programmatique
    from app_tester import AppTester
    tester = AppTester(hub_url="http://localhost:8000")
    await tester.run_all_tests("salarie")
"""

import asyncio
import argparse
import sys
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import httpx
import jwt


class TestStatus(Enum):
    """Statuts possibles pour un test"""
    PENDING = "⏳"
    PASSED = "✅"
    FAILED = "❌"
    SKIPPED = "⏭️"
    WARNING = "⚠️"


@dataclass
class TestResult:
    """Résultat d'un test"""
    name: str
    status: TestStatus
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.name,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms
        }


@dataclass
class TestSuite:
    """Suite de tests"""
    name: str
    results: List[TestResult] = field(default_factory=list)
    
    @property
    def total(self) -> int:
        return len(self.results)
    
    @property
    def passed(self) -> int:
        return len([r for r in self.results if r.status == TestStatus.PASSED])
    
    @property
    def failed(self) -> int:
        return len([r for r in self.results if r.status == TestStatus.FAILED])
    
    @property
    def warnings(self) -> int:
        return len([r for r in self.results if r.status == TestStatus.WARNING])
    
    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.passed / self.total) * 100


class AppTester:
    """
    Testeur d'application pour valider l'intégration avec le Hub
    """
    
    def __init__(
        self,
        hub_url: str = "http://localhost:8000",
        timeout: float = 30.0,
        verbose: bool = True
    ):
        self.hub_url = hub_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self.client: Optional[httpx.AsyncClient] = None
        
        # Données de test
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.public_key: Optional[str] = None
        self.app_info: Optional[Dict] = None
        
    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=self.timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
    
    def log(self, message: str, level: str = "INFO"):
        """Affiche un message si verbose est activé"""
        if self.verbose:
            timestamp = datetime.now().strftime("%H:%M:%S")
            prefix = {
                "INFO": "ℹ️",
                "SUCCESS": "✅",
                "ERROR": "❌",
                "WARNING": "⚠️",
                "DEBUG": "🔍"
            }.get(level, "  ")
            print(f"[{timestamp}] {prefix} {message}")
    
    async def _timed_test(self, test_func, name: str) -> TestResult:
        """Exécute un test avec mesure du temps"""
        start = datetime.now()
        try:
            result = await test_func()
            duration = (datetime.now() - start).total_seconds() * 1000
            result.duration_ms = duration
            return result
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"Exception: {str(e)}",
                duration_ms=duration
            )
    
    # ==================== Tests de connectivité ====================
    
    async def test_hub_connectivity(self) -> TestResult:
        """Test la connectivité avec le Hub"""
        name = "Hub Connectivity"
        try:
            response = await self.client.get(f"{self.hub_url}/api/health")
            
            if response.status_code == 200:
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message="Hub accessible",
                    details={"status_code": response.status_code}
                )
            elif response.status_code == 404:
                # Pas d'endpoint /health, essayer la racine
                response = await self.client.get(f"{self.hub_url}/")
                if response.status_code in [200, 307]:
                    return TestResult(
                        name=name,
                        status=TestStatus.PASSED,
                        message="Hub accessible (pas d'endpoint /health)",
                        details={"status_code": response.status_code}
                    )
            
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"Status code inattendu: {response.status_code}"
            )
        except httpx.ConnectError:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"Impossible de se connecter à {self.hub_url}"
            )
    
    async def test_public_key_endpoint(self) -> TestResult:
        """Test la récupération de la clé publique"""
        name = "Public Key Endpoint"
        try:
            response = await self.client.get(f"{self.hub_url}/api/auth/public-key")
            
            if response.status_code == 200:
                data = response.json()
                if "public_key" in data:
                    self.public_key = data["public_key"]
                    is_valid = "-----BEGIN PUBLIC KEY-----" in self.public_key
                    return TestResult(
                        name=name,
                        status=TestStatus.PASSED if is_valid else TestStatus.WARNING,
                        message="Clé publique récupérée" if is_valid else "Format de clé invalide",
                        details={"key_length": len(self.public_key)}
                    )
            
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"Erreur: {response.status_code}"
            )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    # ==================== Tests d'authentification ====================
    
    async def test_login(self, username: str, password: str) -> TestResult:
        """Test le login utilisateur"""
        name = "User Login"
        try:
            response = await self.client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message=f"Login réussi pour {username}",
                    details={
                        "has_access_token": bool(self.access_token),
                        "has_refresh_token": bool(self.refresh_token),
                        "expires_at": data.get("expires_at")
                    }
                )
            elif response.status_code == 401:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message="Identifiants invalides"
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Erreur: {response.status_code}",
                    details={"response": response.text}
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_token_validation(self) -> TestResult:
        """Test la validation du token JWT"""
        name = "Token Validation"
        
        if not self.access_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas de token (login requis)"
            )
        
        try:
            # Décoder sans vérification pour inspecter
            payload = jwt.decode(self.access_token, options={"verify_signature": False})
            
            # Vérifier avec la clé publique si disponible
            if self.public_key:
                try:
                    verified_payload = jwt.decode(
                        self.access_token,
                        self.public_key,
                        algorithms=["RS256"]
                    )
                    return TestResult(
                        name=name,
                        status=TestStatus.PASSED,
                        message="Token valide et vérifié avec la clé publique",
                        details={
                            "sub": payload.get("sub"),
                            "roles": payload.get("roles"),
                            "apps": payload.get("apps"),
                            "exp": payload.get("exp"),
                            "type": payload.get("type")
                        }
                    )
                except jwt.InvalidSignatureError:
                    return TestResult(
                        name=name,
                        status=TestStatus.FAILED,
                        message="Signature du token invalide"
                    )
            
            return TestResult(
                name=name,
                status=TestStatus.WARNING,
                message="Token décodé (signature non vérifiée - pas de clé publique)",
                details=payload
            )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_refresh_token(self) -> TestResult:
        """Test le renouvellement du token"""
        name = "Refresh Token"
        
        if not self.refresh_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas de refresh token"
            )
        
        try:
            old_access_token = self.access_token
            old_refresh_token = self.refresh_token
            
            response = await self.client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                
                # Vérifier la rotation
                tokens_rotated = self.refresh_token != old_refresh_token
                
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message="Token renouvelé avec succès",
                    details={
                        "new_token_received": self.access_token != old_access_token,
                        "refresh_token_rotated": tokens_rotated
                    }
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Erreur: {response.status_code}"
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_token_introspection(self) -> TestResult:
        """Test l'introspection du token"""
        name = "Token Introspection"
        
        if not self.access_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas de token"
            )
        
        try:
            # Essayer les deux endpoints possibles
            for endpoint in ["/api/auth/token/introspect", "/api/auth/introspect"]:
                response = await self.client.post(
                    f"{self.hub_url}{endpoint}",
                    data={"token": self.access_token}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return TestResult(
                        name=name,
                        status=TestStatus.PASSED if data.get("active") else TestStatus.WARNING,
                        message="Token actif" if data.get("active") else "Token inactif",
                        details=data
                    )
            
            return TestResult(
                name=name,
                status=TestStatus.WARNING,
                message="Endpoint introspection non disponible"
            )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_me_endpoint(self) -> TestResult:
        """Test l'endpoint /me"""
        name = "Me Endpoint"
        
        if not self.access_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas de token"
            )
        
        try:
            response = await self.client.get(
                f"{self.hub_url}/api/auth/me",
                headers={"Authorization": f"Bearer {self.access_token}"}
            )
            
            if response.status_code == 200:
                data = response.json()
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message=f"Utilisateur: {data.get('username')}",
                    details=data
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Erreur: {response.status_code}"
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    # ==================== Tests d'application ====================
    
    async def test_get_app_info(self, app_slug: str) -> TestResult:
        """Récupère les informations de l'application"""
        name = f"App Info ({app_slug})"
        
        if not self.access_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas de token"
            )
        
        try:
            # D'abord, lister toutes les apps pour trouver celle avec le bon slug
            response = await self.client.get(
                f"{self.hub_url}/api/apps/",
                headers={"Authorization": f"Bearer {self.access_token}"}
            )
            
            if response.status_code == 200:
                apps = response.json()
                # Chercher l'app avec le bon slug
                for app in apps:
                    if app.get("slug") == app_slug:
                        self.app_info = app
                        return TestResult(
                            name=name,
                            status=TestStatus.PASSED,
                            message=f"Application trouvée: {self.app_info.get('name')}",
                            details=self.app_info
                        )
                
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Application '{app_slug}' non trouvée parmi {len(apps)} apps"
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Erreur: {response.status_code}"
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_app_api_connectivity(self) -> TestResult:
        """Test la connectivité avec l'API de l'application"""
        name = "App API Connectivity"
        
        if not self.app_info:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas d'info application"
            )
        
        api_url = self.app_info.get("api_url")
        if not api_url:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message="URL API non définie"
            )
        
        try:
            # Tenter /api/health ou / en fallback
            for endpoint in ["/api/health", "/", "/api/"]:
                try:
                    response = await self.client.get(f"{api_url}{endpoint}")
                    if response.status_code in [200, 401, 403]:  # 401/403 = API accessible mais auth requise
                        return TestResult(
                            name=name,
                            status=TestStatus.PASSED,
                            message=f"API accessible sur {api_url}",
                            details={
                                "endpoint_tested": endpoint,
                                "status_code": response.status_code
                            }
                        )
                except:
                    continue
            
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=f"Impossible de joindre {api_url}"
            )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    async def test_app_authenticated_request(self) -> TestResult:
        """Test une requête authentifiée vers l'API de l'application"""
        name = "App Authenticated Request"
        
        if not self.app_info or not self.access_token:
            return TestResult(
                name=name,
                status=TestStatus.SKIPPED,
                message="Pas d'info app ou de token"
            )
        
        api_url = self.app_info.get("api_url")
        
        try:
            # Essayer différents endpoints courants
            for endpoint in ["/api/", "/", "/api/health"]:
                response = await self.client.get(
                    f"{api_url}{endpoint}",
                    headers={"Authorization": f"Bearer {self.access_token}"}
                )
                
                if response.status_code == 200:
                    return TestResult(
                        name=name,
                        status=TestStatus.PASSED,
                        message="Requête authentifiée réussie",
                        details={
                            "endpoint": endpoint,
                            "status_code": response.status_code
                        }
                    )
            
            return TestResult(
                name=name,
                status=TestStatus.WARNING,
                message="Aucun endpoint accessible (vérifier la configuration)"
            )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    # ==================== Tests M2M ====================
    
    async def test_service_account_login(self, client_id: str, client_secret: str) -> TestResult:
        """Test l'authentification M2M avec service account"""
        name = "Service Account Login"
        
        try:
            response = await self.client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                sa_token = data.get("access_token")
                
                # Vérifier le type de token
                payload = jwt.decode(sa_token, options={"verify_signature": False})
                
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED,
                    message="Authentification M2M réussie",
                    details={
                        "token_type": payload.get("type"),
                        "apps_access": payload.get("apps", {})
                    }
                )
            elif response.status_code == 401:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message="Credentials service account invalides"
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Erreur: {response.status_code}"
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    # ==================== Tests SSO ====================
    
    async def test_sso_flow(self, username: str, password: str, app_slug: str) -> TestResult:
        """Test le flow SSO complet"""
        name = "SSO Flow"
        
        try:
            response = await self.client.post(
                f"{self.hub_url}/api/auth/sso",
                data={
                    "username": username,
                    "password": password,
                    "app_slug": app_slug
                },
                follow_redirects=False
            )
            
            if response.status_code == 303:
                location = response.headers.get("Location", "")
                has_token = "access_token=" in location
                
                return TestResult(
                    name=name,
                    status=TestStatus.PASSED if has_token else TestStatus.WARNING,
                    message="Redirection SSO correcte" if has_token else "Redirection sans token",
                    details={
                        "redirect_url": location[:100] + "..." if len(location) > 100 else location,
                        "has_token_in_url": has_token
                    }
                )
            elif response.status_code == 403:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Accès refusé à l'application {app_slug}"
                )
            elif response.status_code == 404:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Application {app_slug} non trouvée"
                )
            else:
                return TestResult(
                    name=name,
                    status=TestStatus.FAILED,
                    message=f"Code inattendu: {response.status_code}"
                )
        except Exception as e:
            return TestResult(
                name=name,
                status=TestStatus.FAILED,
                message=str(e)
            )
    
    # ==================== Exécution des tests ====================
    
    async def run_connectivity_tests(self) -> TestSuite:
        """Exécute les tests de connectivité"""
        suite = TestSuite(name="Connectivity Tests")
        
        suite.results.append(await self._timed_test(
            self.test_hub_connectivity, "Hub Connectivity"
        ))
        suite.results.append(await self._timed_test(
            self.test_public_key_endpoint, "Public Key Endpoint"
        ))
        
        return suite
    
    async def run_auth_tests(self, username: str, password: str) -> TestSuite:
        """Exécute les tests d'authentification"""
        suite = TestSuite(name="Authentication Tests")
        
        suite.results.append(await self._timed_test(
            lambda: self.test_login(username, password), "User Login"
        ))
        suite.results.append(await self._timed_test(
            self.test_token_validation, "Token Validation"
        ))
        suite.results.append(await self._timed_test(
            self.test_me_endpoint, "Me Endpoint"
        ))
        suite.results.append(await self._timed_test(
            self.test_refresh_token, "Refresh Token"
        ))
        # Introspection après refresh pour tester le nouveau token
        suite.results.append(await self._timed_test(
            self.test_token_introspection, "Token Introspection"
        ))
        
        return suite
    
    async def run_app_tests(self, app_slug: str) -> TestSuite:
        """Exécute les tests d'application"""
        suite = TestSuite(name=f"Application Tests ({app_slug})")
        
        suite.results.append(await self._timed_test(
            lambda: self.test_get_app_info(app_slug), f"App Info ({app_slug})"
        ))
        suite.results.append(await self._timed_test(
            self.test_app_api_connectivity, "App API Connectivity"
        ))
        suite.results.append(await self._timed_test(
            self.test_app_authenticated_request, "App Authenticated Request"
        ))
        
        return suite
    
    async def run_sso_tests(self, username: str, password: str, app_slug: str) -> TestSuite:
        """Exécute les tests SSO"""
        suite = TestSuite(name="SSO Tests")
        
        suite.results.append(await self._timed_test(
            lambda: self.test_sso_flow(username, password, app_slug), "SSO Flow"
        ))
        
        return suite
    
    async def run_m2m_tests(self, client_id: str, client_secret: str) -> TestSuite:
        """Exécute les tests M2M"""
        suite = TestSuite(name="M2M Tests")
        
        suite.results.append(await self._timed_test(
            lambda: self.test_service_account_login(client_id, client_secret),
            "Service Account Login"
        ))
        
        return suite
    
    async def run_all_tests(
        self,
        app_slug: str,
        username: str = "admin",
        password: str = "admin123",
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None
    ) -> Dict[str, TestSuite]:
        """Exécute tous les tests"""
        results = {}
        
        self.log("=" * 60)
        self.log("🧪 TESTEUR D'APPLICATION - HUB")
        self.log("=" * 60)
        self.log(f"Hub URL: {self.hub_url}")
        self.log(f"Application: {app_slug}")
        self.log("-" * 60)
        
        # Tests de connectivité
        self.log("\n📡 Tests de connectivité...")
        results["connectivity"] = await self.run_connectivity_tests()
        self._print_suite_summary(results["connectivity"])
        
        # Tests d'authentification
        self.log("\n🔐 Tests d'authentification...")
        results["auth"] = await self.run_auth_tests(username, password)
        self._print_suite_summary(results["auth"])
        
        # Tests d'application
        self.log(f"\n📱 Tests d'application ({app_slug})...")
        results["app"] = await self.run_app_tests(app_slug)
        self._print_suite_summary(results["app"])
        
        # Tests SSO
        self.log("\n🔑 Tests SSO...")
        results["sso"] = await self.run_sso_tests(username, password, app_slug)
        self._print_suite_summary(results["sso"])
        
        # Tests M2M si credentials fournis
        if client_id and client_secret:
            self.log("\n🤖 Tests M2M...")
            results["m2m"] = await self.run_m2m_tests(client_id, client_secret)
            self._print_suite_summary(results["m2m"])
        
        # Résumé final
        self._print_final_summary(results)
        
        return results
    
    def _print_suite_summary(self, suite: TestSuite):
        """Affiche le résumé d'une suite de tests"""
        for result in suite.results:
            status_icon = result.status.value
            duration = f"({result.duration_ms:.0f}ms)"
            print(f"  {status_icon} {result.name}: {result.message} {duration}")
    
    def _print_final_summary(self, results: Dict[str, TestSuite]):
        """Affiche le résumé final"""
        total_tests = sum(s.total for s in results.values())
        total_passed = sum(s.passed for s in results.values())
        total_failed = sum(s.failed for s in results.values())
        total_warnings = sum(s.warnings for s in results.values())
        
        print("\n" + "=" * 60)
        print("📊 RÉSUMÉ FINAL")
        print("=" * 60)
        print(f"  Total: {total_tests} tests")
        print(f"  ✅ Réussis: {total_passed}")
        print(f"  ❌ Échoués: {total_failed}")
        print(f"  ⚠️  Avertissements: {total_warnings}")
        
        success_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        print(f"\n  Taux de réussite: {success_rate:.1f}%")
        
        if total_failed == 0:
            print("\n🎉 Tous les tests sont passés !")
        else:
            print(f"\n⚠️  {total_failed} test(s) ont échoué. Vérifiez les détails ci-dessus.")
        print("=" * 60)


async def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description="Testeur d'application pour le Hub d'authentification"
    )
    parser.add_argument(
        "--hub-url",
        default="http://localhost:8000",
        help="URL du Hub (défaut: http://localhost:8000)"
    )
    parser.add_argument(
        "--app-slug",
        default="salarie",
        help="Slug de l'application à tester"
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Nom d'utilisateur pour les tests"
    )
    parser.add_argument(
        "--password",
        default="admin123",
        help="Mot de passe pour les tests"
    )
    parser.add_argument(
        "--client-id",
        help="Client ID pour les tests M2M"
    )
    parser.add_argument(
        "--client-secret",
        help="Client secret pour les tests M2M"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Mode silencieux"
    )
    
    args = parser.parse_args()
    
    async with AppTester(
        hub_url=args.hub_url,
        verbose=not args.quiet
    ) as tester:
        results = await tester.run_all_tests(
            app_slug=args.app_slug,
            username=args.username,
            password=args.password,
            client_id=args.client_id,
            client_secret=args.client_secret
        )
        
        if args.json:
            output = {
                name: {
                    "total": suite.total,
                    "passed": suite.passed,
                    "failed": suite.failed,
                    "results": [r.to_dict() for r in suite.results]
                }
                for name, suite in results.items()
            }
            print(json.dumps(output, indent=2, default=str))
        
        # Code de sortie basé sur les résultats
        total_failed = sum(s.failed for s in results.values())
        sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
