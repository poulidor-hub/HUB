"""
Docker Scanner - Détection automatique des conteneurs Docker
=============================================================
Scanne les conteneurs Docker en cours d'exécution sur le serveur
pour faciliter l'ajout d'applications dans le Hub.

⚠️ ATTENTION : Nécessite l'accès au socket Docker
   Volume requis : /var/run/docker.sock:/var/run/docker.sock:ro
"""

import re
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Conteneurs à exclure par défaut (services internes du Hub)
DEFAULT_EXCLUDED_CONTAINERS = {
    "hub-api",
    "hub-frontend", 
    "hub-db",
}

# Patterns de noms à exclure (bases de données, reverse proxy, etc.)
EXCLUDED_NAME_PATTERNS = [
    r".*postgres.*",
    r".*mysql.*",
    r".*mariadb.*",
    r".*mongo.*",
    r".*redis.*",
    r".*nginx.*",
    r".*traefik.*",
    r".*haproxy.*",
    r".*caddy.*",
]


@dataclass
class ContainerPort:
    """Port exposé par un conteneur"""
    container_port: int
    host_port: Optional[int]
    protocol: str = "tcp"


@dataclass
class DiscoveredContainer:
    """Conteneur Docker découvert"""
    container_id: str
    name: str
    image: str
    status: str
    ports: List[ContainerPort]
    suggested_slug: str
    suggested_name: str
    labels: Dict[str, str]
    # Labels Hub spécifiques (extraits des labels Docker hub.*)
    hub_name: Optional[str] = None           # hub.name - Nom personnalisé
    hub_description: Optional[str] = None    # hub.description - Description
    hub_shared: bool = False                  # hub.shared - Service partagé M2M


def generate_slug(name: str) -> str:
    """Génère un slug à partir du nom du conteneur"""
    # Retirer les prefixes courants (docker-compose)
    slug = name.lower()
    slug = re.sub(r'^[a-z]+-', '', slug)  # Retirer préfixe projet
    slug = re.sub(r'-\d+$', '', slug)     # Retirer suffixes numériques
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or "app"


def format_container_name(name: str) -> str:
    """Formate le nom du conteneur pour l'affichage"""
    # Retirer les prefixes courants et formater
    display_name = name.replace('-', ' ').replace('_', ' ')
    # Capitaliser chaque mot
    display_name = ' '.join(word.capitalize() for word in display_name.split())
    return display_name


def should_exclude_container(name: str) -> bool:
    """Vérifie si un conteneur doit être exclu du scan"""
    # Vérifier les noms exacts
    if name in DEFAULT_EXCLUDED_CONTAINERS:
        return True
    
    # Vérifier les patterns
    name_lower = name.lower()
    for pattern in EXCLUDED_NAME_PATTERNS:
        if re.match(pattern, name_lower):
            return True
    
    return False


class DockerScanner:
    """Scanner de conteneurs Docker"""
    
    def __init__(self):
        self._client = None
        self._available = None
        self._last_error = None
    
    def reset(self):
        """Réinitialise le client Docker (pour retry après échec)"""
        self._client = None
        self._available = None
        self._last_error = None
    
    @property
    def client(self):
        """Client Docker (lazy loading avec retry si échec précédent)"""
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
                # Test de connexion
                self._client.ping()
                self._available = True
                self._last_error = None
                logger.info("✅ Docker SDK connecté")
            except Exception as e:
                logger.warning(f"Docker SDK non disponible: {e}")
                self._client = False
                self._available = False
                self._last_error = str(e)
        return self._client if self._client else None
    
    @property
    def is_available(self) -> bool:
        """Vérifie si Docker est accessible"""
        if self._available is None:
            _ = self.client  # Déclenche le chargement
        return self._available
    
    @property
    def last_error(self) -> Optional[str]:
        """Retourne la dernière erreur de connexion"""
        return self._last_error
    
    def scan_containers(
        self, 
        include_stopped: bool = False,
        excluded_names: Optional[List[str]] = None
    ) -> List[DiscoveredContainer]:
        """
        Scanne les conteneurs Docker et retourne ceux qui semblent être des applications.
        
        Args:
            include_stopped: Inclure les conteneurs arrêtés
            excluded_names: Noms de conteneurs supplémentaires à exclure
            
        Returns:
            Liste des conteneurs découverts avec leurs informations
        """
        if not self.is_available:
            logger.warning("Docker n'est pas disponible pour le scan")
            return []
        
        excluded = set(DEFAULT_EXCLUDED_CONTAINERS)
        if excluded_names:
            excluded.update(excluded_names)
        
        discovered = []
        
        try:
            containers = self.client.containers.list(all=include_stopped)
            
            for container in containers:
                name = container.name
                
                # Filtrer les conteneurs à exclure
                if should_exclude_container(name):
                    logger.debug(f"Conteneur exclu: {name}")
                    continue
                
                if name in excluded:
                    logger.debug(f"Conteneur exclu (liste personnalisée): {name}")
                    continue
                
                # Récupérer les ports exposés
                ports = self._extract_ports(container)
                
                # Ignorer les conteneurs sans ports exposés
                if not ports:
                    logger.debug(f"Conteneur sans ports: {name}")
                    continue
                
                # Extraire les labels utiles
                labels = container.labels or {}
                
                # Récupérer le nom de l'image (avec gestion d'erreur si image orpheline)
                try:
                    image_name = container.image.tags[0] if container.image.tags else "unknown"
                except Exception:
                    # Image orpheline ou supprimée
                    image_name = container.attrs.get('Config', {}).get('Image', 'unknown')
                
                # Extraire les variables d'environnement APP_* du conteneur
                env_vars = {}
                try:
                    env_list = container.attrs.get('Config', {}).get('Env', [])
                    for env in env_list:
                        if '=' in env and env.startswith('APP_'):
                            key, value = env.split('=', 1)
                            env_vars[key] = value
                except Exception as e:
                    logger.debug(f"Impossible de lire les variables d'env de {name}: {e}")
                
                # Extraire les labels hub.* spécifiques
                hub_name = labels.get("hub.name")
                hub_description = labels.get("hub.description")
                hub_shared_str = labels.get("hub.shared", "").lower()
                hub_shared = hub_shared_str in ("true", "1", "yes", "on")
                
                # Priorité: labels hub.* > variables APP_* > valeurs générées
                # Pour le nom
                final_suggested_name = hub_name or env_vars.get('APP_NAME') or format_container_name(name)
                # Pour la description
                final_description = hub_description or env_vars.get('APP_DESCRIPTION')
                # Pour le slug
                final_suggested_slug = env_vars.get('APP_SLUG') or generate_slug(name)
                
                discovered.append(DiscoveredContainer(
                    container_id=container.short_id,
                    name=name,
                    image=image_name,
                    status=container.status,
                    ports=ports,
                    suggested_slug=final_suggested_slug,
                    suggested_name=final_suggested_name,
                    labels={k: v for k, v in labels.items() if not k.startswith("com.")},
                    hub_name=hub_name,
                    hub_description=final_description,
                    hub_shared=hub_shared
                ))
                
        except Exception as e:
            logger.error(f"Erreur lors du scan Docker: {e}")
            raise
        
        return discovered
    
    def _extract_ports(self, container) -> List[ContainerPort]:
        """Extrait les ports exposés d'un conteneur"""
        ports = []
        
        try:
            # container.ports format: {'8000/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8000'}]}
            for container_port_str, host_bindings in (container.ports or {}).items():
                # Parser le port du conteneur
                parts = container_port_str.split('/')
                container_port = int(parts[0])
                protocol = parts[1] if len(parts) > 1 else "tcp"
                
                # Récupérer le port hôte si disponible
                host_port = None
                if host_bindings:
                    for binding in host_bindings:
                        if binding.get('HostPort'):
                            host_port = int(binding['HostPort'])
                            break
                
                # On s'intéresse surtout aux ports exposés sur l'hôte
                if host_port:
                    ports.append(ContainerPort(
                        container_port=container_port,
                        host_port=host_port,
                        protocol=protocol
                    ))
                    
        except Exception as e:
            logger.warning(f"Erreur extraction ports pour {container.name}: {e}")
        
        return ports
    
    def get_docker_info(self) -> Dict[str, Any]:
        """Retourne des informations sur l'environnement Docker"""
        if not self.is_available:
            return {"available": False, "error": "Docker non accessible"}
        
        try:
            info = self.client.info()
            return {
                "available": True,
                "version": self.client.version().get("Version", "unknown"),
                "containers_running": info.get("ContainersRunning", 0),
                "containers_total": info.get("Containers", 0),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}


# Instance singleton
docker_scanner = DockerScanner()
