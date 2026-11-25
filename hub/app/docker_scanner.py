# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
# Note: nginx n'est PAS exclu car les frontends d'applications hub utilisent souvent nginx
EXCLUDED_NAME_PATTERNS = [
    r".*postgres.*",
    r".*mysql.*",
    r".*mariadb.*",
    r".*mongo.*",
    r".*redis.*",
    r".*memcache.*",
    r".*elasticsearch.*",
    r".*kibana.*",
    r".*grafana.*",
    r".*prometheus.*",
    r".*traefik.*",
    r".*haproxy.*",
    r".*caddy.*",
    r".*portainer.*",
    r".*watchtower.*",
    r".*adminer.*",
    r".*phpmyadmin.*",
    r".*pgadmin.*",
    r"^nginx$",          # nginx seul (reverse proxy), mais pas "mon-app-nginx" ou "app-frontend"
]

# Suffixes indicateurs du type de conteneur
_API_SUFFIXES = ('-api', '-backend', '-back', '-server')
_FRONTEND_SUFFIXES = ('-frontend', '-front', '-ui', '-web', '-client')

# Images typiques frontend
_FRONTEND_IMAGE_PATTERNS = [r'nginx', r'httpd', r'apache']
# Images typiques backend
_API_IMAGE_PATTERNS = [r'python', r'fastapi', r'uvicorn', r'gunicorn', r'flask', r'django', r'node', r'express']

# Ports typiques
_FRONTEND_PORTS = {80, 443, 3000, 8080}
_API_PORTS = {8000, 5000, 5001, 8001, 8888, 9000}


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
    # Auto-pairing : type détecté et slug de base pour le regroupement
    container_type: str = 'unknown'           # 'api', 'frontend', 'unknown'
    base_slug: str = ''                       # Slug sans suffixe de type (pour regrouper API+Frontend)


def generate_slug(name: str) -> str:
    """Génère un slug à partir du nom du conteneur Docker.
    
    Gère les formats Docker Compose modernes:
    - project-service-1 (ancien format)
    - project_service-service-1 (nouveau format avec underscores)
    - service-1 (format simple)
    
    Exemples:
    - "exemple_app_13-frontend-1" -> "exemple-app-13-frontend"
    - "exemple_app_10-api-1" -> "exemple-app-10-api"
    - "mon-app-backend-2" -> "mon-app-backend"
    """
    slug = name.lower()
    
    # Retirer les suffixes numériques de réplicas (-1, -2, etc.)
    slug = re.sub(r'-\d+$', '', slug)
    
    # Remplacer underscores par tirets (Docker Compose utilise _ pour le projet)
    slug = slug.replace('_', '-')
    
    # Nettoyer les caractères non alphanumériques
    slug = re.sub(r'[^a-z0-9-]+', '-', slug)
    
    # Éviter les doubles tirets
    slug = re.sub(r'-+', '-', slug)
    
    # Nettoyer les tirets en début/fin
    slug = slug.strip('-')
    
    # Essayer de détecter et supprimer la répétition du suffixe de service
    # Ex: "exemple-app-10-api-api" -> "exemple-app-10-api"
    # Ex: "mon-app-frontend-frontend" -> "mon-app-frontend"
    parts = slug.split('-')
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        slug = '-'.join(parts[:-1])
    
    return slug or "app"


def format_container_name(name: str) -> str:
    """Formate le nom du conteneur pour l'affichage"""
    # Retirer les prefixes courants et formater
    display_name = name.replace('-', ' ').replace('_', ' ')
    # Capitaliser chaque mot
    display_name = ' '.join(word.capitalize() for word in display_name.split())
    return display_name


def generate_base_slug(slug: str) -> str:
    """Retire les suffixes de type (api, frontend, backend...) pour obtenir le slug applicatif.
    
    Exemples:
    - "exemple-app-13-api" -> "exemple-app-13"
    - "mon-app-frontend" -> "mon-app"
    - "exemple-app-10" -> "exemple-app-10" (pas de suffixe)
    - "mon-app-api-frontend" -> "mon-app-api" (retire seulement le dernier)
    """
    for suffix in ('-api', '-backend', '-back', '-server', '-frontend', '-front', '-ui', '-web', '-client'):
        if slug.endswith(suffix):
            base = slug[:-len(suffix)]
            return base if base else slug
    return slug


def detect_container_type(name: str, image: str, ports: List[ContainerPort]) -> str:
    """Détecte si un conteneur est un backend API ou un frontend.
    
    Priorité de détection :
    1. Label hub.type (pas encore implémenté, réservé)
    2. Suffixe du nom du conteneur (-api, -frontend, etc.)
    3. Image Docker (nginx → frontend, python/fastapi → api)
    4. Port typique (80/443 → frontend, 8000/5000 → api)
    
    Returns:
        'api', 'frontend', ou 'unknown'
    """
    slug = generate_slug(name)
    
    # 1. Détection par suffixe du slug
    for suffix in _API_SUFFIXES:
        if slug.endswith(suffix):
            return 'api'
    for suffix in _FRONTEND_SUFFIXES:
        if slug.endswith(suffix):
            return 'frontend'
    
    # 2. Détection par image Docker
    image_lower = image.lower()
    for pattern in _FRONTEND_IMAGE_PATTERNS:
        if re.search(pattern, image_lower):
            return 'frontend'
    for pattern in _API_IMAGE_PATTERNS:
        if re.search(pattern, image_lower):
            return 'api'
    
    # 3. Détection par port (fallback)
    host_ports = {p.host_port for p in ports if p.host_port}
    if host_ports & _FRONTEND_PORTS and not host_ports & _API_PORTS:
        return 'frontend'
    if host_ports & _API_PORTS and not host_ports & _FRONTEND_PORTS:
        return 'api'
    
    return 'unknown'


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
        self._last_check_time = None
        self._retry_interval = 30  # Réessayer toutes les 30 secondes si échec
    
    def reset(self):
        """Réinitialise le client Docker (pour retry après échec)"""
        self._client = None
        self._available = None
        self._last_error = None
        self._last_check_time = None
    
    @property
    def client(self):
        """Client Docker (lazy loading avec retry automatique si échec précédent)"""
        import time
        current_time = time.time()
        
        # Si connexion échouée, réessayer après l'intervalle de retry
        if self._client is False and self._last_check_time:
            elapsed = current_time - self._last_check_time
            if elapsed >= self._retry_interval:
                logger.info(f"🔄 Tentative de reconnexion Docker (après {elapsed:.0f}s)")
                self._client = None  # Permettre un nouveau retry
            else:
                return None  # Pas encore temps de réessayer
        
        if self._client is None:
            self._last_check_time = current_time
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
                app_slug_from_env = env_vars.get('APP_SLUG')
                generated_slug = generate_slug(name)
                final_suggested_slug = app_slug_from_env or generated_slug
                
                # Log pour debug si le slug vient d'une variable d'env
                if app_slug_from_env:
                    logger.debug(f"Conteneur {name}: slug depuis APP_SLUG={app_slug_from_env}")
                else:
                    logger.debug(f"Conteneur {name}: slug généré={generated_slug}")
                
                # Détecter le type de conteneur (api/frontend/unknown) et le slug de base
                container_type_label = labels.get("hub.type")  # Priorité au label explicite
                if container_type_label and container_type_label in ('api', 'frontend'):
                    ctype = container_type_label
                else:
                    ctype = detect_container_type(name, image_name, ports)
                
                base_slug = generate_base_slug(final_suggested_slug)
                
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
                    hub_shared=hub_shared,
                    container_type=ctype,
                    base_slug=base_slug,
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
        
        # Host network mode : les ports ne sont pas visibles via Docker port bindings
        # On les détecte via labels hub.port, variables d'env ou directives EXPOSE
        if not ports:
            try:
                network_mode = container.attrs.get('HostConfig', {}).get('NetworkMode', '')
                if network_mode == 'host':
                    ports = self._extract_host_mode_ports(container)
            except Exception as e:
                logger.warning(f"Erreur extraction ports host mode pour {container.name}: {e}")
        
        return ports
    
    def _extract_host_mode_ports(self, container) -> List[ContainerPort]:
        """Extrait les ports d'un conteneur en mode host network.
        
        En mode host, le conteneur partage le réseau de l'hôte.
        Les ports ne sont pas visibles via les Docker port bindings classiques.
        On utilise (par ordre de priorité) :
          1. Le label hub.port (déclaration explicite)
          2. Les variables d'environnement (LISTEN_PORT, API_PORT, FRONTEND_PORT)
          3. Les ports EXPOSE de l'image Docker
        """
        ports = []
        labels = container.labels or {}
        
        # 1. Label explicite hub.port (priorité maximale)
        hub_port = labels.get("hub.port")
        if hub_port:
            try:
                port = int(hub_port)
                ports.append(ContainerPort(container_port=port, host_port=port))
                logger.debug(f"Port host mode {container.name}: {port} (label hub.port)")
                return ports
            except ValueError:
                logger.warning(f"Label hub.port invalide pour {container.name}: {hub_port}")
        
        # 2. Variables d'environnement (LISTEN_PORT > API_PORT > FRONTEND_PORT)
        try:
            env_list = container.attrs.get('Config', {}).get('Env', [])
            env_dict = {}
            for env in env_list:
                if '=' in env:
                    key, value = env.split('=', 1)
                    env_dict[key] = value
            
            for env_key in ('LISTEN_PORT', 'API_PORT', 'FRONTEND_PORT'):
                env_port = env_dict.get(env_key)
                if env_port:
                    try:
                        port = int(env_port)
                        ports.append(ContainerPort(container_port=port, host_port=port))
                        logger.debug(f"Port host mode {container.name}: {port} (env {env_key})")
                        return ports
                    except ValueError:
                        continue
        except Exception:
            pass
        
        # 3. Fallback : ports EXPOSE de l'image
        try:
            exposed = container.attrs.get('Config', {}).get('ExposedPorts', {})
            for port_str in exposed:
                parts = port_str.split('/')
                port = int(parts[0])
                protocol = parts[1] if len(parts) > 1 else "tcp"
                ports.append(ContainerPort(container_port=port, host_port=port, protocol=protocol))
                logger.debug(f"Port host mode {container.name}: {port} (EXPOSE)")
        except Exception:
            pass
        
        return ports
    
    def get_used_host_ports(self) -> set:
        """Retourne tous les host_port TCP liés par les conteneurs en cours.

        Best-effort : contrairement à scan_containers (qui ne renvoie que les
        conteneurs « app-like »), on prend ici TOUS les conteneurs running
        (postgres, redis, etc.) car n'importe quel port lié peut entrer en
        conflit avec une allocation. Ne lève jamais : renvoie set() en cas
        d'erreur ou si Docker est indisponible.
        """
        if not self.is_available:
            logger.warning("Docker indisponible : scan des ports occupés ignoré")
            return set()

        used: set = set()
        try:
            for container in self.client.containers.list(all=False):
                for cp in self._extract_ports(container):
                    if cp.host_port:
                        used.add(cp.host_port)
        except Exception as e:
            logger.warning(f"Scan des ports Docker occupés échoué: {e}")
            return set()
        return used

    def get_used_network_subnets(self) -> set:
        """Retourne les sous-réseaux (CIDR) des réseaux Docker existants.

        Best-effort, symétrique de get_used_host_ports : capte les épinglages
        manuels (apps épinglées à la main, stacks tiers) invisibles de la table
        port_allocations du Hub. Ne lève jamais : renvoie set() en cas d'erreur
        ou si Docker est indisponible.
        """
        if not self.is_available:
            logger.warning("Docker indisponible : scan des sous-réseaux ignoré")
            return set()

        used: set = set()
        try:
            for net in self.client.networks.list():
                ipam = (net.attrs or {}).get("IPAM") or {}
                for cfg in ipam.get("Config") or []:
                    subnet = cfg.get("Subnet")
                    if subnet:
                        used.add(subnet)
        except Exception as e:
            logger.warning(f"Scan des sous-réseaux Docker échoué: {e}")
            return set()
        return used

    def get_project_network_subnets(self, project: str) -> set:
        """Sous-réseaux des réseaux Docker du projet Compose `project`.

        Filtre sur le label `com.docker.compose.project` posé par Docker
        Compose — indépendant du nom du réseau (souvent épinglé en dur dans le
        compose de l'app, ex. exemple-app-network).

        Best-effort : renvoie set() si Docker est indisponible ou en cas
        d'erreur ; ne lève jamais.
        """
        if not self.is_available:
            logger.warning("Docker indisponible : scan réseau projet ignoré")
            return set()

        subnets: set = set()
        try:
            for net in self.client.networks.list():
                labels = (net.attrs or {}).get("Labels") or {}
                if not isinstance(labels, dict):
                    continue
                if labels.get("com.docker.compose.project") != project:
                    continue
                ipam = (net.attrs or {}).get("IPAM") or {}
                for cfg in ipam.get("Config") or []:
                    subnet = cfg.get("Subnet")
                    if subnet:
                        subnets.add(subnet)
        except Exception as e:
            logger.warning(f"Scan des réseaux du projet {project} échoué: {e}")
            return set()
        return subnets

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
