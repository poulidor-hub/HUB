/**
 * Hub Application - Main JavaScript
 */

let currentUser = null;
let allApps = [];
let allServiceAccounts = [];
let hubHost = null;  // IP/hostname du Hub (chargé depuis l'API)
let beaconToken = null;  // Token HMAC éphémère pour sécuriser beacon-logout
let dashboardFilter = 'all'; // 'all' | 'favorites' | catégorie
let dashboardApps = []; // apps affichées sur le dashboard (après loadApps)

// Tracking des fenêtres d'applications ouvertes pour fermeture automatique au logout
const openedAppWindows = new Map();

// Timestamp du dernier acquittement des erreurs (pour le badge de notifications)
let errorsLastAcknowledgedAt = localStorage.getItem('errorsLastAcknowledgedAt') || null;

// Bootstrap instances
let appFormModal, userFormModal, serviceAccountFormModal, saCredentialsModal, toast;

// Debounce pour les événements SSE apps_updated (évite les cascades de requêtes)
let _sseAppsUpdateTimer = null;
const SSE_DEBOUNCE_DELAY = 800; // ms

// Debounce pour les événements SSE user_permissions_changed (modifications en lot)
let _ssePermissionsUpdateTimer = null;

// Tracking des tooltips Bootstrap pour éviter les fuites mémoire
let _activeTooltips = [];

// ===== Server-Sent Events (SSE) pour mises à jour temps réel =====
let eventSource = null;
let sseWasConnected = false; // Indique si on a déjà été connecté (pour détecter les reconnexions)
let _sseInitializing = false; // Guard contre les initialisations SSE concurrentes

/**
 * Initialise la connexion SSE pour recevoir les mises à jour en temps réel
 * Passe l'user_id en query param pour le tracking de session côté serveur
 * @param {boolean} isReconnect - true si c'est une reconnexion après une déconnexion
 */
function initEventSource(isReconnect = false) {
    // Guard contre les initialisations concurrentes
    if (_sseInitializing) return;
    
    // Fermer toute connexion existante
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    
    // Ne pas connecter si pas authentifié
    if (!api.token) {
        return;
    }
    
    _sseInitializing = true;
    try {
        // Passer l'user_id et beacon_token pour le tracking de session sécurisé
        const userId = currentUser?.id || '';
        let sseUrl = '/api/events/stream';
        if (userId && beaconToken) {
            sseUrl = `/api/events/stream?user_id=${userId}&beacon_token=${beaconToken}`;
        }
        eventSource = new EventSource(sseUrl);
        
        eventSource.onopen = () => {
            console.log('🔌 SSE connecté - mises à jour temps réel actives');
            
            // Si c'est une reconnexion après une déconnexion (ex: redémarrage serveur),
            // rafraîchir les données pour synchroniser l'état
            if (isReconnect && sseWasConnected) {
                console.log('🔄 Reconnexion SSE détectée - rafraîchissement des données...');
                refreshAfterReconnect();
            }
            sseWasConnected = true;
        };
        
        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleSSEEvent(data);
            } catch (e) {
                console.warn('SSE: erreur parsing message', e);
            }
        };
        
        eventSource.onerror = (error) => {
            console.warn('SSE: erreur de connexion, reconnexion dans 5s...', error);
            eventSource.close();
            // Reconnexion automatique après 5 secondes
            // Renouveler le beacon_token AVANT de reconnecter le SSE
            // (le secret serveur est régénéré après un redémarrage/reload)
            setTimeout(async () => {
                if (api.token && currentUser) {
                    try {
                        const userValid = await api.getCurrentUser().catch(() => null);
                        if (userValid) {
                            currentUser = userValid;
                            if (userValid.beacon_token) {
                                beaconToken = userValid.beacon_token;
                                console.log('🔑 Beacon token renouvelé avant reconnexion SSE');
                            }
                            initEventSource(true); // Reconnecter avec le nouveau token
                        } else {
                            // Session expirée, on tente quand même la reconnexion basique
                            initEventSource(true);
                        }
                    } catch (e) {
                        // API pas encore dispo, réessayer la reconnexion simple
                        initEventSource(true);
                    }
                }
            }, 5000);
        };
    } catch (e) {
        console.warn('SSE: impossible de créer la connexion', e);
    } finally {
        _sseInitializing = false;
    }
}

/**
 * Rafraîchit les données après une reconnexion SSE (ex: après redémarrage serveur)
 * Cela garantit que l'interface est synchronisée avec l'état du serveur
 */
async function refreshAfterReconnect() {
    try {
        // Vérifier d'abord que la session est toujours valide
        const userValid = await api.getCurrentUser().catch(() => null);
        if (!userValid) {
            console.log('⚠️ Session invalide après reconnexion - redirection vers login');
            handleSessionExpired('Votre session a expiré suite au redémarrage du serveur.');
            return;
        }
        
        // Mettre à jour currentUser
        currentUser = userValid;
        
        // Renouveler le beacon_token (régénéré côté serveur après redémarrage)
        if (userValid.beacon_token) {
            beaconToken = userValid.beacon_token;
            console.log('🔑 Beacon token renouvelé après reconnexion');
            
            // Reconnecter le SSE avec le nouveau beacon_token
            initEventSource(false);
        }
        
        // Rafraîchir les applications (page principale)
        if (currentPage === 'dashboard' || currentPage === 'apps') {
            await loadApps();
        }
        
        // Rafraîchir selon la page courante
        switch (currentPage) {
            case 'users':
                loadUsersPageData();
                break;
            case 'service-accounts':
                loadAdminData();
                break;
            case 'audit':
                loadAuditPageOverview();
                break;
            case 'apps-admin':
                loadAdminData();
                break;
        }
        
        // Rafraîchir le compteur d'apps en attente si admin
        if (currentUser?.is_admin) {
            loadPendingAppsCount();
        }
        
        console.log('✅ Données rafraîchies après reconnexion SSE');
    } catch (e) {
        console.error('Erreur lors du rafraîchissement après reconnexion:', e);
    }
}

/**
 * Ferme la connexion SSE
 */
function closeEventSource() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
        console.log('🔌 SSE déconnecté');
    }
}

/**
 * Gère les événements SSE reçus
 */
function handleSSEEvent(data) {
    if (data.type === 'ping') {
        // Keep-alive, ignorer silencieusement
        return;
    }
    
    // L'événement 'connected' est maintenant géré par onopen + refreshAfterReconnect
    if (data.type === 'connected') {
        return;
    }
    
    console.log('📡 SSE event:', data.type, data.data);
    
    switch (data.type) {
        case 'apps_updated': {
            // Notification adaptée selon l'action
            let appAction = 'modifiée';
            let toastType = 'info';
            switch (data.data?.action) {
                case 'created':
                    appAction = 'ajoutée';
                    break;
                case 'deleted':
                    appAction = 'supprimée';
                    break;
                case 'approved':
                    appAction = 'approuvée';
                    toastType = 'success';
                    loadPendingAppsCount();  // Mettre à jour le badge
                    break;
                case 'rejected':
                    appAction = 'rejetée';
                    toastType = 'warning';
                    loadPendingAppsCount();  // Mettre à jour le badge
                    break;
                case 'docker_status_changed':
                    appAction = `Docker: ${data.data?.new_status || 'mis à jour'}`;
                    toastType = data.data?.new_status === 'running' ? 'success' : 'warning';
                    break;
                case 'toggled':
                    appAction = data.data?.is_active ? 'activée' : 'désactivée';
                    toastType = data.data?.is_active ? 'success' : 'warning';
                    break;
                case 'batch_import':
                    appAction = `lot importé (${data.data?.created || 0} créée(s))`;
                    toastType = 'success';
                    break;
            }
            const appName = data.data?.app_name || 'Application';
            showToast('Mise à jour', `${appName} ${appAction}`, toastType);
            
            // Debounce : regrouper les mises à jour SSE rapprochées (ex: import batch)
            // pour éviter des dizaines de requêtes API simultanées
            if (_sseAppsUpdateTimer) clearTimeout(_sseAppsUpdateTimer);
            _sseAppsUpdateTimer = setTimeout(() => {
                _sseAppsUpdateTimer = null;
                
                const needsTokenRefresh = ['created', 'updated', 'approved', 'toggled', 'batch_import'].includes(data.data?.action);
                
                const refreshUI = () => {
                    if (currentPage === 'apps-admin') {
                        loadAdminData();
                    } else {
                        loadApps();
                    }
                };
                
                if (needsTokenRefresh && !api._refreshPromise) {
                    refreshTokenAfterAppChange(null, true).then(refreshUI).catch(refreshUI);
                } else {
                    refreshUI();
                }
            }, SSE_DEBOUNCE_DELAY);
            break;
        }
            
        case 'docker_status_changed':
            // Événement dédié pour les changements de statut Docker
            if (currentPage === 'apps-admin') {
                updateAppDockerStatusInTable(data.data?.slug, data.data?.status);
            }
            // Aussi mettre à jour sur le dashboard
            updateAppCardStatus(data.data?.slug, data.data?.status, null);
            break;
        
        case 'app_health_changed':
            // Événement pour les changements de santé (health check)
            const healthAppSlug = data.data?.slug;
            const healthAppName = data.data?.name;
            const newHealthStatus = data.data?.health_status;
            const previousStatus = data.data?.previous_status;
            
            console.log(`🏥 Health status changed: ${healthAppSlug} -> ${newHealthStatus}`);
            
            // Mettre à jour le badge dans le tableau admin
            if (currentPage === 'apps-admin') {
                updateAppHealthStatusInTable(healthAppSlug, newHealthStatus);
            }
            
            // Mettre à jour la carte sur le dashboard
            updateAppCardStatus(healthAppSlug, null, newHealthStatus);
            
            // Notification
            if (newHealthStatus === 'unhealthy') {
                showToast('⚠️ Application hors ligne', `${healthAppName || healthAppSlug} ne répond plus`, 'warning');
            } else if (previousStatus === 'unhealthy' && newHealthStatus === 'healthy') {
                showToast('✅ Application en ligne', `${healthAppName || healthAppSlug} est de nouveau accessible`, 'success');
            }
            break;
            
        case 'users_updated':
            // Rafraîchir la liste des utilisateurs si on est sur cette page
            if (currentPage === 'users') {
                loadUsersPageData();
                allProfiles = [];
                loadProfilesTab(); // Mettre à jour les compteurs de profils
            }
            break;
            
        case 'service_accounts_updated':
            // Rafraîchir la liste des service accounts si on est sur cette page
            if (currentPage === 'service-accounts') {
                loadServiceAccounts();
            }
            break;
            
        case 'stats_updated':
            // Mettre à jour les statistiques affichées en temps réel
            updateStatsDisplay(data.data);
            break;
            
        case 'audit_updated':
            // Rafraîchir les données d'audit si on est sur cette page
            if (currentPage === 'audit') {
                // Rafraîchir les données d'audit sans toast (pour éviter le spam)
                loadAuditPageOverview();
            }
            // Mettre à jour aussi les stats si disponibles
            if (data.data?.stats) {
                updateStatsDisplay(data.data.stats);
            }
            // Mettre à jour le badge d'erreurs dans la nav
            if (currentUser?.is_admin) {
                updateErrorNotificationBadge();
            }
            break;

        case 'user_permissions_changed': {
            // Droits modifiés pour un utilisateur — rafraîchir token + apps si c'est nous
            if (currentUser && data.data?.user_id === currentUser.id) {
                console.log('🔑 Permissions modifiées pour l\'utilisateur courant, synchronisation...');
                // Debounce : les modifications en lot génèrent plusieurs événements
                if (_ssePermissionsUpdateTimer) clearTimeout(_ssePermissionsUpdateTimer);
                _ssePermissionsUpdateTimer = setTimeout(async () => {
                    _ssePermissionsUpdateTimer = null;
                    try {
                        await refreshTokenAfterAppChange('modification de droits', false);
                        await loadApps();
                    } catch (e) {
                        console.error('Erreur lors de la synchronisation des permissions:', e);
                    }
                }, SSE_DEBOUNCE_DELAY);
            }
            // Rafraîchir la liste utilisateurs si l'admin est sur cette page
            if (currentPage === 'users') {
                loadUsersPageData();
            }
            break;
        }

        case 'errors_acknowledged':
            // Réinitialiser le badge de notifications d'erreurs
            errorsLastAcknowledgedAt = data.data?.acknowledged_at || new Date().toISOString();
            localStorage.setItem('errorsLastAcknowledgedAt', errorsLastAcknowledgedAt);
            updateNavErrorBadge(0);
            break;
            
        case 'force_logout':
            // Vérifier si c'est l'utilisateur courant qui doit être déconnecté
            if (currentUser && data.data?.user_id === currentUser.id) {
                handleForceLogout(data.data.reason);
            }
            break;

        case 'user_message':
            handleIncomingMessage(data.data);
            break;

        case 'messages_updated':
            // Rafraîchir la page messages admin si elle est ouverte
            if (currentPage === 'messages' && currentUser?.is_admin) {
                loadMessagesPage();
            }
            break;
    }
}

/**
 * Met à jour l'affichage des statistiques en temps réel (sans recharger depuis l'API)
 */
function updateStatsDisplay(stats) {
    // Mettre à jour les compteurs si les éléments existent sur la page
    const statsElements = {
        'stat-total-apps': stats.total_apps,
        'stat-active-apps': stats.active_apps,
        'stat-total-users': stats.total_users,
        'stat-total-sa': stats.total_service_accounts,
        'stat-active-sessions': stats.active_sessions,
        'stat-sse-clients': stats.connected_sse_clients
    };
    
    for (const [elementId, value] of Object.entries(statsElements)) {
        const element = document.getElementById(elementId);
        if (element && value !== undefined) {
            // Animation de mise à jour
            element.classList.add('stats-updating');
            element.textContent = value;
            setTimeout(() => element.classList.remove('stats-updating'), 300);
        }
    }
    
    // Mettre à jour aussi les cartes de stats du dashboard si présentes
    updateDashboardStats(stats);
}

/**
 * Met à jour les cartes de statistiques du dashboard
 */
function updateDashboardStats(stats) {
    // Stats des applications sur le dashboard
    const appsCountEl = document.querySelector('[data-stat="apps-count"]');
    if (appsCountEl) {
        appsCountEl.textContent = stats.total_apps || 0;
    }
    
    const activeAppsEl = document.querySelector('[data-stat="active-apps"]');
    if (activeAppsEl) {
        activeAppsEl.textContent = stats.active_apps || 0;
    }
    
    const usersCountEl = document.querySelector('[data-stat="users-count"]');
    if (usersCountEl) {
        usersCountEl.textContent = stats.total_users || 0;
    }
    
    const sessionsEl = document.querySelector('[data-stat="active-sessions"]');
    if (sessionsEl) {
        sessionsEl.textContent = stats.active_sessions || 0;
    }
}

/**
 * Gestion du callback SSO - persisté en sessionStorage pour survivre aux reloads
 */
function getPendingCallback() {
    return sessionStorage.getItem('sso_pending_callback');
}

/**
 * Valide qu'une URL de callback est sûre (même origine ou app connue).
 * Bloque les redirections vers des domaines externes.
 */
function isValidCallbackUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        // Autoriser les callbacks same-origin
        if (parsed.origin === window.location.origin) {
            return true;
        }
        // Autoriser les callbacks vers des IP privées (réseau local)
        const hostname = parsed.hostname;
        if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1') {
            return true;
        }
        // Réseaux privés (10.x, 172.16-31.x, 192.168.x)
        const parts = hostname.split('.');
        if (parts.length === 4 && parts.every(p => !isNaN(p))) {
            const first = parseInt(parts[0]);
            const second = parseInt(parts[1]);
            if (first === 10) return true;
            if (first === 172 && second >= 16 && second <= 31) return true;
            if (first === 192 && second === 168) return true;
        }
        // Autoriser les hostnames sans point (noms Docker/internes, ex: "mon-app")
        if (!hostname.includes('.') || hostname.endsWith('.local') || hostname.endsWith('.lan')) {
            return true;
        }
        console.warn('SSO callback bloqué — origine non autorisée:', parsed.origin);
        return false;
    } catch (e) {
        console.warn('SSO callback invalide:', url, e);
        return false;
    }
}

function setPendingCallback(url) {
    if (url && isValidCallbackUrl(url)) {
        sessionStorage.setItem('sso_pending_callback', url);
    } else if (url) {
        console.warn('SSO: callback URL rejetée (non fiable):', url);
    }
}

function clearPendingCallback() {
    sessionStorage.removeItem('sso_pending_callback');
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Bootstrap components
    appFormModal = new bootstrap.Modal(document.getElementById('appFormModal'));
    userFormModal = new bootstrap.Modal(document.getElementById('userFormModal'));
    serviceAccountFormModal = new bootstrap.Modal(document.getElementById('serviceAccountFormModal'));
    saCredentialsModal = new bootstrap.Modal(document.getElementById('saCredentialsModal'));
    toast = new bootstrap.Toast(document.getElementById('toast'));

    // Mettre à jour le preview quand la couleur change
    const appColorInput = document.getElementById('appColor');
    if (appColorInput) {
        appColorInput.addEventListener('input', updateAppFormPreview);
    }

    // Gérer le changement d'onglet dans la page admin
    const adminPageTabsEl = document.getElementById('adminPageTabs');
    if (adminPageTabsEl) {
        adminPageTabsEl.addEventListener('shown.bs.tab', (event) => {
            const targetId = event.target.getAttribute('data-bs-target');
            if (targetId === '#tabApps') {
                currentPage = 'apps-admin';
                refreshAppsStatus();
                startConnectivityRefresh();
            } else {
                // Autre onglet (Sauvegardes, Service Accounts)
                stopConnectivityRefresh();
                if (targetId === '#tabServiceAccounts') {
                    currentPage = 'service-accounts';
                    refreshServiceAccountsStatus();
                } else if (targetId === '#tabAppBackups') {
                    currentPage = 'app-backups';
                }
            }
        });
    }

    // Écouter l'événement de session expirée depuis l'API
    // Cela permet de déconnecter immédiatement l'utilisateur si sa session expire
    // (par exemple après un reboot du serveur)
    window.addEventListener('hub:sessionExpired', (event) => {
        console.log('Événement hub:sessionExpired reçu');
        handleSessionExpired(event.detail?.message);
    });

    // Écouter quand la page redevient visible (utilisateur revient sur l'onglet)
    // Utile pour détecter un retour après un redémarrage serveur
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && api.token && currentUser) {
            // Vérifier si le token est expiré ou proche de l'expiration
            // Les timers setTimeout sont throttlés/suspendus par le navigateur
            // quand l'onglet est en arrière-plan, le refresh proactif peut avoir été raté
            const expiry = api._parseTokenExpiry(api.token);
            if (expiry && expiry.getTime() - Date.now() < 2 * 60 * 1000) {
                console.log('👁️ Token expiré ou proche de l\'expiration au retour — refresh...');
                api.tryRefreshToken();
            }
            // Vérifier si la connexion SSE est toujours active
            if (!eventSource || eventSource.readyState !== EventSource.OPEN) {
                console.log('👁️ Page redevenue visible - reconnexion SSE...');
                initEventSource(true);  // Reconnexion avec rafraîchissement
            }
        }
    });

    // Gérer la fermeture du navigateur/onglet - tenter un logout synchrone
    // Utilise sendBeacon pour envoyer la requête même si la page se ferme
    window.addEventListener('beforeunload', (event) => {
        if (api.token && currentUser) {
            // Utiliser sendBeacon pour tenter de fermer la session côté serveur
            // Note: sendBeacon ne peut pas inclure de headers Authorization
            // On utilise donc un endpoint dédié avec le token en body
            try {
                const data = JSON.stringify({
                    reason: 'browser_closed',
                    user_id: currentUser.id,
                    beacon_token: beaconToken || ''
                });
                navigator.sendBeacon('/api/auth/beacon-logout', data);
                console.log('📤 Beacon logout envoyé');
            } catch (e) {
                console.warn('Beacon logout failed:', e);
            }
        }
    });

    // Vérifier les paramètres URL
    const params = new URLSearchParams(window.location.search);
    const callbackUrl = params.get('callback');
    const action = params.get('action');
    
    // Si action=logout, l'utilisateur a été déconnecté d'une application
    // => Fermer l'onglet automatiquement pour éviter d'accumuler les onglets Hub
    if (action === 'logout') {
        console.log('Déconnexion depuis une application - fermeture de l\'onglet');
        // Effacer tout callback en attente
        clearPendingCallback();
        
        // Tenter de fermer l'onglet
        // Note: window.close() fonctionne uniquement si l'onglet a été ouvert par script (window.open)
        // ce qui est le cas quand on ouvre une app depuis le Hub
        try {
            window.close();
        } catch (e) {
            console.log('Impossible de fermer l\'onglet automatiquement');
        }
        
        // Si l'onglet n'a pas pu être fermé (ex: ouvert manuellement), 
        // afficher une page minimaliste indiquant la déconnexion
        // avec un délai pour laisser le temps à window.close() de s'exécuter
        setTimeout(() => {
            // Si on arrive ici, c'est que l'onglet n'a pas été fermé
            document.body.innerHTML = `
                <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: system-ui, sans-serif; background: #f8f9fa;">
                    <i class="bi bi-check-circle-fill" style="font-size: 4rem; color: #198754; margin-bottom: 1rem;"></i>
                    <h2 style="color: #212529; margin-bottom: 0.5rem;">Déconnecté</h2>
                    <p style="color: #6c757d;">Vous pouvez fermer cet onglet.</p>
                    <button onclick="window.close()" style="margin-top: 1rem; padding: 0.5rem 1.5rem; background: #0d6efd; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem;">
                        Fermer l'onglet
                    </button>
                </div>
            `;
        }, 100);
        
        return; // Stopper l'initialisation normale
    }
    // Sinon, si un callback est présent, c'est une demande de connexion SSO
    // URL format: http://hub/?callback=http://app:port/callback
    else if (callbackUrl) {
        setPendingCallback(decodeURIComponent(callbackUrl));
        // Nettoyer l'URL sans recharger la page
        window.history.replaceState({}, '', window.location.pathname);
        console.log('SSO callback enregistré:', getPendingCallback());
    }

    // Check if already logged in
    // Vérifier l'authentification au chargement
    if (api.token) {
        loadCurrentUser();
    } else {
        // Pas de token - afficher directement le login
        showLoginScreen();
    }

    // Auto-generate slug from name
    document.getElementById('appName').addEventListener('input', (e) => {
        const slugField = document.getElementById('appSlug');
        if (!document.getElementById('appId').value) {
            slugField.value = e.target.value
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/(^-|-$)/g, '');
        }
    });
    
    // Update URL preview when ports change
    document.getElementById('appApiPort').addEventListener('input', updateUrlPreview);
    document.getElementById('appFrontendPort').addEventListener('input', updateUrlPreview);
    document.getElementById('appCustomHostValue').addEventListener('input', updateUrlPreview);
});

// ===== Authentication =====

async function handleLogin(event) {
    event.preventDefault();
    const username = document.getElementById('loginUsername').value;
    const password = document.getElementById('loginPassword').value;
    const errorDiv = document.getElementById('loginError');

    try {
        errorDiv.classList.add('d-none');
        const loginResult = await api.login(username, password);
        
        // Sauvegarder le beacon token pour le logout sécurisé à la fermeture
        if (loginResult.beacon_token) {
            beaconToken = loginResult.beacon_token;
        }
        
        // Si un callback SSO est en attente, rediriger vers l'application externe
        const pendingCallback = getPendingCallback();
        if (pendingCallback) {
            clearPendingCallback();  // Nettoyer AVANT la redirection
            const separator = pendingCallback.includes('?') ? '&' : '?';
            const redirectUrl = `${pendingCallback}${separator}access_token=${api.token}`;
            console.log('Redirection SSO vers:', redirectUrl);
            showToast('Connexion réussie', `Redirection vers l'application...`, 'success');
            // Redirection immédiate
            window.location.href = redirectUrl;
            return;
        }
        
        await loadCurrentUser();
        showToast('Connexion réussie', `Bienvenue ${username} !`, 'success');
        
        // Vérifier si l'utilisateur doit changer son mot de passe
        if (loginResult.must_change_password) {
            showChangePasswordModal(true);  // Mode obligatoire
        }
    } catch (error) {
        console.error('Login error:', error);
        errorDiv.textContent = error.message || `Erreur de connexion à l'API (${API_BASE})`;
        errorDiv.classList.remove('d-none');
    }
}

async function loadCurrentUser() {
    try {
        console.log('loadCurrentUser: Token présent:', !!api.token);
        currentUser = await api.getCurrentUser();
        console.log('loadCurrentUser: Utilisateur chargé:', currentUser?.username, 'is_admin:', currentUser?.is_admin);
        
        // Si l'utilisateur est déjà connecté et qu'un callback SSO est en attente
        // Rediriger directement vers l'application externe
        const pendingCallback = getPendingCallback();
        if (pendingCallback) {
            clearPendingCallback();  // Nettoyer AVANT la redirection
            const separator = pendingCallback.includes('?') ? '&' : '?';
            const redirectUrl = `${pendingCallback}${separator}access_token=${api.token}`;
            console.log('Utilisateur déjà connecté, redirection SSO vers:', redirectUrl);
            // Redirection immédiate
            window.location.href = redirectUrl;
            return;
        }
        
        showDashboard();
    } catch (error) {
        console.error('Failed to load user:', error);
        // Session invalide (ex: après un reboot du serveur)
        // Déconnecter proprement l'utilisateur et afficher le formulaire de login
        handleSessionExpired('Votre session a expiré. Veuillez vous reconnecter.');
    }
}

/**
 * Cache l'écran de chargement initial
 */
function hideLoadingScreen() {
    const loadingScreen = document.getElementById('loadingScreen');
    if (loadingScreen) {
        loadingScreen.classList.add('hidden');
        // Supprimer complètement après l'animation
        setTimeout(() => loadingScreen.remove(), 300);
    }
}

/**
 * Affiche la navbar principale
 */
function showNavbar() {
    const navbar = document.getElementById('mainNavbar');
    if (navbar) {
        navbar.classList.remove('d-none');
    }
}

function showDashboard() {
    // Afficher la navbar et cacher le loading
    showNavbar();
    hideLoadingScreen();
    
    document.getElementById('loginSection').classList.add('d-none');
    document.getElementById('dashboardSection').classList.remove('d-none');
    document.getElementById('userInfo').classList.remove('d-none');
    document.getElementById('logoutBtn').classList.remove('d-none');
    document.getElementById('changePasswordBtn').classList.remove('d-none');
    document.getElementById('username').textContent = currentUser.username;

    if (currentUser.is_admin) {
        document.getElementById('adminBtn').classList.remove('d-none');
        document.getElementById('adminNavigation').classList.remove('d-none');
        // Mettre à jour le badge de notification d'erreurs
        updateErrorNotificationBadge();
    }

    // Afficher la cloche de messages et charger le compteur non-lus
    document.getElementById('messageBellContainer').classList.remove('d-none');
    loadUnreadMessageCount();

    // Initialiser les mises à jour temps réel via SSE
    initEventSource();

    loadApps();
}

// ===== Page Navigation =====

let currentPage = 'dashboard';

function showPage(page) {
    // Fermer le menu mobile si ouvert
    const navbarCollapse = document.getElementById('navbarContent');
    if (navbarCollapse?.classList.contains('show')) {
        bootstrap.Collapse.getInstance(navbarCollapse)?.hide();
    }
    
    // Masquer toutes les sections
    document.getElementById('dashboardSection').classList.add('d-none');
    document.getElementById('auditSection').classList.add('d-none');
    document.getElementById('usersSection').classList.add('d-none');
    document.getElementById('discoverySection').classList.add('d-none');
    document.getElementById('adminSection').classList.add('d-none');
    document.getElementById('cloneMigrateSection').classList.add('d-none');
    document.getElementById('messagesSection').classList.add('d-none');
    
    // Arrêter les timers admin si on quitte la page admin
    stopConnectivityRefresh();
    
    // Réinitialiser les styles de navigation
    document.querySelectorAll('#adminNavigation .nav-link').forEach(link => {
        link.classList.remove('active');
    });
    
    currentPage = page;
    
    switch(page) {
        case 'dashboard':
            document.getElementById('dashboardSection').classList.remove('d-none');
            break;
        case 'audit':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('auditSection').classList.remove('d-none');
            document.getElementById('navAudit')?.classList.add('active');
            loadAuditPageData();
            break;
        case 'users':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('usersSection').classList.remove('d-none');
            document.getElementById('navUsers')?.classList.add('active');
            allProfiles = []; // Forcer le rechargement pour des compteurs à jour
            loadUsersPageData();
            loadProfilesTab();
            break;
        case 'discovery':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('discoverySection').classList.remove('d-none');
            document.getElementById('navDiscovery')?.classList.add('active');
            loadDiscoveryPage();
            break;
        case 'admin':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('adminSection').classList.remove('d-none');
            currentPage = 'apps-admin';
            loadAdminData();
            loadPendingAppsCount();
            refreshAppsStatus();
            startConnectivityRefresh();
            break;
        case 'clone-migrate':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('cloneMigrateSection').classList.remove('d-none');
            currentPage = 'clone-migrate';
            loadBackupInfo();
            break;
        case 'messages':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('messagesSection').classList.remove('d-none');
            document.getElementById('navMessages')?.classList.add('active');
            loadMessagesPage();
            break;
    }
}

async function logout() {
    try {
        // Fermer la connexion SSE
        closeEventSource();
        
        // Fermer tous les onglets d'applications ouverts AVANT la déconnexion
        const closedCount = closeAllAppWindows();
        if (closedCount > 0) {
            console.log(`${closedCount} onglet(s) d'application fermé(s)`);
        }
        
        await api.logout();
        
        // Message personnalisé selon le nombre d'onglets fermés
        if (closedCount > 0) {
            const appText = closedCount === 1 ? 'application fermée' : 'applications fermées';
            showToast('Déconnexion', `À bientôt ! (${closedCount} ${appText})`, 'info');
        } else {
            showToast('Déconnexion', 'À bientôt !', 'info');
        }
    } catch (error) {
        console.error('Erreur lors du logout:', error);
        showToast('Déconnexion', 'À bientôt !', 'info');
    } finally {
        showLoginScreen();
    }
}

/**
 * Réinitialise l'interface pour afficher l'écran de login
 * Utilisé lors de la déconnexion ou quand la session expire
 */
function showLoginScreen() {
    // S'assurer que le loading screen est caché et la navbar visible
    showNavbar();
    hideLoadingScreen();
    
    currentUser = null;
    currentPage = 'dashboard';
    document.getElementById('loginSection').classList.remove('d-none');
    document.getElementById('dashboardSection').classList.add('d-none');
    document.getElementById('auditSection').classList.add('d-none');
    document.getElementById('usersSection').classList.add('d-none');
    document.getElementById('discoverySection').classList.add('d-none');
    document.getElementById('adminSection').classList.add('d-none');
    document.getElementById('cloneMigrateSection').classList.add('d-none');
    document.getElementById('userInfo').classList.add('d-none');
    document.getElementById('logoutBtn').classList.add('d-none');
    document.getElementById('changePasswordBtn').classList.add('d-none');
    document.getElementById('adminBtn').classList.add('d-none');
    document.getElementById('adminNavigation').classList.add('d-none');
    document.getElementById('messageBellContainer').classList.add('d-none');
    document.getElementById('messagePanel').classList.add('d-none');
    document.getElementById('loginForm').reset();
}

/**
 * Gère l'expiration de session (ex: après reboot du serveur)
 * Déconnecte proprement l'utilisateur et affiche un message
 * @param {string} message - Message à afficher à l'utilisateur
 */
function handleSessionExpired(message) {
    console.log('Session expirée, déconnexion...');
    
    // Fermer la connexion SSE si active
    closeEventSource();
    
    // Fermer tous les onglets d'applications ouverts
    closeAllAppWindows();
    
    // Effacer les tokens
    api.clearToken();
    
    // Afficher l'écran de login
    showLoginScreen();
    
    // Afficher le message d'expiration
    if (message) {
        showToast('Session expirée', message, 'warning');
    }
}

/**
 * Gère la déconnexion forcée par un administrateur
 */
function handleForceLogout(reason) {
    // Fermer la connexion SSE
    closeEventSource();
    
    // Fermer tous les onglets d'applications ouverts
    closeAllAppWindows();
    
    // Effacer les tokens
    api.clearToken();
    
    // Message explicatif selon la raison
    let message = 'Votre session a été terminée.';
    if (reason === 'account_disabled') {
        message = 'Votre compte a été désactivé par un administrateur.';
    } else if (reason === 'admin_revoked') {
        message = 'Vos sessions ont été révoquées par un administrateur.';
    }
    
    // Afficher un modal d'alerte
    alert(message);
    
    // Réinitialiser l'interface
    showLoginScreen();
}

// ===== Gestion du changement de mot de passe =====

let changePasswordModal = null;

/**
 * Affiche le modal de changement de mot de passe
 * @param {boolean} mandatory - Si true, l'utilisateur ne peut pas fermer le modal
 */
function showChangePasswordModal(mandatory = false) {
    if (!changePasswordModal) {
        changePasswordModal = new bootstrap.Modal(document.getElementById('changePasswordModal'), {
            backdrop: mandatory ? 'static' : true,
            keyboard: !mandatory
        });
    }
    
    // Réinitialiser le formulaire
    document.getElementById('changePasswordForm').reset();
    document.getElementById('changePasswordError').classList.add('d-none');
    
    // Masquer/afficher le bouton de fermeture selon le mode
    const closeBtn = document.querySelector('#changePasswordModal .btn-close');
    const cancelBtn = document.querySelector('#changePasswordModal .btn-secondary');
    if (closeBtn) closeBtn.style.display = mandatory ? 'none' : 'block';
    if (cancelBtn) cancelBtn.style.display = mandatory ? 'none' : 'block';
    
    // Afficher un message si obligatoire
    const mandatoryAlert = document.getElementById('changePasswordMandatory');
    if (mandatoryAlert) {
        mandatoryAlert.style.display = mandatory ? 'block' : 'none';
    }
    
    changePasswordModal.show();
}

/**
 * Gère la soumission du formulaire de changement de mot de passe
 */
async function handleChangePassword(event) {
    event.preventDefault();
    
    const currentPassword = document.getElementById('currentPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmNewPassword').value;
    const errorDiv = document.getElementById('changePasswordError');
    
    // Validation côté client
    if (newPassword !== confirmPassword) {
        errorDiv.textContent = 'Les mots de passe ne correspondent pas';
        errorDiv.classList.remove('d-none');
        return;
    }
    
    if (newPassword.length < 8) {
        errorDiv.textContent = 'Le mot de passe doit contenir au moins 8 caractères';
        errorDiv.classList.remove('d-none');
        return;
    }
    
    try {
        await api.changePassword(currentPassword, newPassword);
        
        // Fermer le modal
        if (changePasswordModal) {
            changePasswordModal.hide();
        }
        
        // Mettre à jour l'état utilisateur
        if (currentUser) {
            currentUser.must_change_password = false;
        }
        
        showToast('Succès', 'Mot de passe modifié avec succès', 'success');
    } catch (error) {
        errorDiv.textContent = error.message || 'Erreur lors du changement de mot de passe';
        errorDiv.classList.remove('d-none');
    }
}

// ===== Applications Dashboard =====

async function loadApps() {
    console.log('📱 loadApps() appelé, currentPage:', currentPage);
    try {
        const apps = await api.getApps();
        console.log('📱 loadApps: réponse reçue, nombre d\'apps:', Array.isArray(apps) ? apps.length : 'pas un tableau');
        // S'assurer que apps est un tableau
        if (Array.isArray(apps)) {
            dashboardApps = apps;
            buildCategoryFilters(apps);
            filterDashboardApps();
        } else {
            console.error('loadApps: réponse inattendue (pas un tableau):', apps);
            dashboardApps = [];
            renderAppsGrid([]);
        }
    } catch (error) {
        console.error('Failed to load apps:', error);
        // En cas d'erreur, afficher une grille vide plutôt que de laisser l'ancien contenu
        const grid = document.getElementById('appsGrid');
        if (grid) {
            grid.innerHTML = `
                <div class="col-12 empty-state">
                    <i class="bi bi-exclamation-triangle text-warning"></i>
                    <h4>Erreur de chargement</h4>
                    <p>Impossible de charger les applications. <a href="#" onclick="loadApps(); return false;">Réessayer</a></p>
                </div>
            `;
        }
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
    }
}

function buildCategoryFilters(apps) {
    const container = document.getElementById('categoryFilters');
    if (!container) return;
    const categories = [...new Set(apps.map(a => a.category).filter(Boolean))].sort();
    container.innerHTML = categories.map(cat => `
        <button class="btn btn-sm dash-filter-btn" data-filter="cat:${escapeHtml(cat)}" onclick="setDashboardFilter('cat:${escapeHtml(cat)}')">
            ${escapeHtml(cat)}
        </button>
    `).join('');
}

function setDashboardFilter(filter) {
    dashboardFilter = filter;
    document.querySelectorAll('.dash-filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-filter') === filter);
    });
    filterDashboardApps();
}

function clearAppSearch() {
    const input = document.getElementById('appSearchInput');
    if (input) { input.value = ''; }
    filterDashboardApps();
}

function filterDashboardApps() {
    const searchInput = document.getElementById('appSearchInput');
    const clearBtn = document.getElementById('appSearchClear');
    const query = (searchInput ? searchInput.value : '').toLowerCase().trim();

    if (clearBtn) clearBtn.classList.toggle('d-none', !query);

    let filtered = dashboardApps;

    // Filtre par texte
    if (query) {
        filtered = filtered.filter(app =>
            app.name.toLowerCase().includes(query) ||
            (app.description || '').toLowerCase().includes(query) ||
            app.slug.toLowerCase().includes(query) ||
            (app.category || '').toLowerCase().includes(query)
        );
    }

    // Filtre actif
    if (dashboardFilter === 'favorites') {
        filtered = filtered.filter(app => app.is_favorite);
    } else if (dashboardFilter.startsWith('cat:')) {
        const cat = dashboardFilter.slice(4);
        filtered = filtered.filter(app => app.category === cat);
    }

    renderAppsGrid(filtered);
}

async function toggleFavorite(event, appId) {
    event.stopPropagation();
    const app = dashboardApps.find(a => a.id === appId);
    if (!app) return;
    try {
        if (app.is_favorite) {
            await api.removeFavorite(appId);
            app.is_favorite = false;
        } else {
            await api.addFavorite(appId);
            app.is_favorite = true;
        }
        filterDashboardApps();
    } catch (e) {
        showToast('Erreur', 'Impossible de modifier le favori', 'danger');
    }
}

function renderAppsGrid(apps) {
    const grid = document.getElementById('appsGrid');
    
    // Protection contre grid null (page pas encore chargée)
    if (!grid) {
        console.warn('renderAppsGrid: élément appsGrid non trouvé');
        return;
    }
    
    // Protection contre apps invalide
    if (!Array.isArray(apps)) {
        console.warn('renderAppsGrid: apps n\'est pas un tableau:', apps);
        apps = [];
    }
    
    if (apps.length === 0) {
        const msg = dashboardFilter === 'favorites'
            ? `<i class="bi bi-star"></i><h4>Aucun favori</h4><p>Cliquez sur l'étoile d'une application pour l'ajouter à vos favoris.</p>`
            : dashboardFilter.startsWith('cat:')
            ? `<i class="bi bi-funnel"></i><h4>Aucune application dans cette catégorie</h4>`
            : `<i class="bi bi-inbox"></i><h4>Aucune application</h4><p>Contactez un administrateur pour obtenir l'accès aux applications.</p>`;
        grid.innerHTML = `<div class="col-12 empty-state">${msg}</div>`;
        return;
    }

    const isAdmin = currentUser?.is_admin;

    grid.innerHTML = apps.map(app => {
        // Déterminer si l'app est inactive (désactivée par l'admin)
        // Les statuts Docker/health arrivent en temps réel via SSE
        const isUnavailable = !app.is_active;
        const appVersionLabel = formatAppVersionLabel(app.app_version);
        const favIcon = app.is_favorite ? 'bi-star-fill text-warning' : 'bi-star';
        
        return `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card shadow-sm h-100 ${isUnavailable ? 'app-inactive' : ''}" 
                 data-app-slug="${app.slug}"
                 onclick="${!isUnavailable ? `openApp('${app.slug}', '${app.frontend_url || ''}')` : `showToast('Application indisponible', 'Cette application est désactivée.', 'warning')`}">
                <div class="card-body position-relative">
                    <button class="btn btn-sm btn-link favorite-btn" onclick="toggleFavorite(event, ${app.id})" title="${app.is_favorite ? 'Retirer des favoris' : 'Ajouter aux favoris'}">
                        <i class="bi ${favIcon}"></i>
                    </button>
                    <div class="app-card-header">
                        <div class="app-icon" style="background-color: ${app.color}">
                            <i class="${app.icon}"></i>
                        </div>
                        <div class="app-card-info">
                            <h6>${escapeHtml(app.name)}</h6>
                            <p>${escapeHtml(app.description || 'Aucune description')}</p>
                        </div>
                    </div>
                    <div class="app-card-footer">
                        ${app.category ? `<span class="badge app-category-badge">${escapeHtml(app.category)}</span>` : '<span></span>'}
                        ${appVersionLabel ? `<span class="app-version-badge">${escapeHtml(appVersionLabel)}</span>` : '<span></span>'}
                    </div>
                    ${isAdmin ? `
                        <div class="app-role-selector" onclick="event.stopPropagation()">
                            <select class="form-select form-select-sm" 
                                    id="appRole_${app.slug}" 
                                    onchange="updateMyAppRole('${app.slug}', this.value)"
                                    title="Mon rôle sur cette application">
                                <option value="" ${!app.my_role ? 'selected' : ''}>-- Mon rôle --</option>
                                <option value="admin" ${app.my_role === 'admin' ? 'selected' : ''}>Admin</option>
                                <option value="manager" ${app.my_role === 'manager' ? 'selected' : ''}>Manager</option>
                                <option value="user" ${app.my_role === 'user' ? 'selected' : ''}>User</option>
                                <option value="viewer" ${app.my_role === 'viewer' ? 'selected' : ''}>Viewer</option>
                            </select>
                        </div>
                    ` : ''}
                </div>
            </div>
        </div>
    `}).join('');
}

/**
 * Rafraîchit le token après une création/modification d'application
 * Affiche un spinner discret pendant l'opération
 * @param {string|null} actionType - Type d'action ('création', 'modification') ou null si silencieux
 * @param {boolean} silent - Si true, pas de toast (pour les événements SSE)
 */
async function refreshTokenAfterAppChange(actionType = null, silent = false) {
    // Créer et afficher un spinner discret dans la navbar
    const navbar = document.querySelector('.navbar-nav') || document.querySelector('.navbar');
    let spinner = null;
    
    if (navbar && !silent) {
        spinner = document.createElement('span');
        spinner.id = 'token-refresh-spinner';
        spinner.className = 'ms-2 d-inline-flex align-items-center';
        spinner.innerHTML = `
            <span class="spinner-border spinner-border-sm text-light me-1" role="status"></span>
            <small class="text-light opacity-75">Sync...</small>
        `;
        navbar.appendChild(spinner);
    }
    
    try {
        const tokenRefreshed = await api.tryRefreshToken();
        
        // Recharger aussi les données utilisateur pour synchroniser l'état local
        if (tokenRefreshed) {
            try {
                currentUser = await api.getCurrentUser();
            } catch (e) {
                console.log('Impossible de recharger currentUser:', e);
            }
        }
        
        if (!silent && actionType) {
            if (tokenRefreshed) {
                showToast('Session synchronisée', `Vos accès ont été mis à jour après la ${actionType}.`, 'info');
            } else {
                showToast('Info', `La ${actionType} est enregistrée. Reconnectez-vous pour accéder à l'app.`, 'warning');
            }
        }
        
        return tokenRefreshed;
    } catch (e) {
        console.log('Refresh token non disponible:', e);
        if (!silent && actionType) {
            showToast('Info', `La ${actionType} est enregistrée. Le nouvel accès sera actif à la prochaine connexion.`, 'info');
        }
        return false;
    } finally {
        // Supprimer le spinner
        if (spinner) {
            spinner.remove();
        }
    }
}

/**
 * Met à jour le rôle de l'admin courant sur une application
 */
async function updateMyAppRole(appSlug, role) {
    try {
        const result = await api.updateMyAppRole(appSlug, role || null);
        const roleText = role ? `défini à "${role}"` : 'supprimé';
        
        // Rafraîchir le token pour que le nouveau rôle soit pris en compte immédiatement
        // Le refresh token recharge les rôles depuis la base de données
        let tokenRefreshed = false;
        try {
            tokenRefreshed = await api.tryRefreshToken();
        } catch (e) {
            console.log('Refresh token non disponible, le nouveau rôle sera actif après re-login');
        }
        
        // IMPORTANT: Recharger la liste des applications pour refléter le nouveau rôle
        // Cela vide le cache implicite de l'UI et recharge depuis l'API
        await loadApps();
        
        // Message adapté selon que le token a été rafraîchi ou non
        if (tokenRefreshed) {
            showToast('Rôle mis à jour', `Votre rôle sur ${result.app_name} a été ${roleText}. Actif immédiatement.`, 'success');
        } else {
            showToast('Rôle mis à jour', `Votre rôle sur ${result.app_name} a été ${roleText}. Le nouveau rôle sera actif à la prochaine connexion à l'app.`, 'success');
        }
    } catch (error) {
        console.error('Erreur mise à jour rôle:', error);
        showToast('Erreur', error.message || 'Impossible de mettre à jour le rôle', 'danger');
        // Recharger pour remettre la bonne valeur
        await loadApps();
    }
}

async function openApp(slug, frontendUrl) {
    if (!frontendUrl) {
        showToast('Information', 'Cette application n\'a pas de frontend configuré', 'warning');
        return;
    }

    // Nettoyer les références aux fenêtres déjà fermées
    cleanupClosedWindows();

    // 1. Ouvrir la fenêtre IMMÉDIATEMENT (dans le contexte synchrone du clic)
    //    pour éviter le blocage par les popup blockers du navigateur
    const windowName = `hub_app_${slug}_${Date.now()}`;
    const appWindow = window.open('about:blank', windowName);

    if (!appWindow) {
        showToast('Erreur', 'Impossible d\'ouvrir l\'application (popup bloqué ?)', 'warning');
        return;
    }

    // Afficher une page de chargement dans la fenêtre en attendant le token
    try {
        appWindow.document.write(`
            <!DOCTYPE html>
            <html><head><title>Connexion...</title></head>
            <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:system-ui,sans-serif;background:#f8f9fa;">
                <div style="text-align:center;">
                    <div style="border:4px solid #e9ecef;border-top:4px solid #0d6efd;border-radius:50%;width:40px;height:40px;animation:spin 1s linear infinite;margin:0 auto 1rem;"></div>
                    <p style="color:#6c757d;margin:0;">Connexion à l'application...</p>
                </div>
                <style>@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}</style>
            </body></html>
        `);
    } catch (e) {
        // Ignorer si on ne peut pas écrire (cross-origin)
    }

    // Stocker la référence pour fermeture automatique au logout
    openedAppWindows.set(windowName, appWindow);

    try {
        // 2. S'assurer que le token est valide AVANT de naviguer
        await api.ensureFreshToken();

        // 3. Logger l'accès à l'application (non bloquant)
        api.logAppAccess(slug).catch(e => console.warn('Échec log accès app:', e));

        // 4. Naviguer vers l'app avec un token garanti valide
        const separator = frontendUrl.includes('?') ? '&' : '?';
        appWindow.location.href = `${frontendUrl}${separator}access_token=${api.token}`;
        console.log(`Application ${slug} ouverte dans l'onglet ${windowName}`);
    } catch (e) {
        // En cas d'échec du refresh, fermer la fenêtre vierge
        console.error(`Échec ouverture app ${slug}:`, e);
        try { appWindow.close(); } catch (_) {}
        openedAppWindows.delete(windowName);
        showToast('Erreur', 'Session expirée — veuillez vous reconnecter', 'danger');
    }
}

/**
 * Nettoie les références aux fenêtres qui ont été fermées manuellement
 */
function cleanupClosedWindows() {
    for (const [name, win] of openedAppWindows.entries()) {
        if (win.closed) {
            openedAppWindows.delete(name);
        }
    }
}

/**
 * Ferme tous les onglets d'applications ouverts par le Hub
 */
function closeAllAppWindows() {
    let closedCount = 0;
    for (const [name, win] of openedAppWindows.entries()) {
        if (win && !win.closed) {
            try {
                win.close();
                closedCount++;
                console.log(`Onglet ${name} fermé`);
            } catch (e) {
                console.warn(`Impossible de fermer l'onglet ${name}:`, e);
            }
        }
    }
    openedAppWindows.clear();
    return closedCount;
}

// ===== Admin Panel =====

async function loadAdminData() {
    console.log('loadAdminData: Chargement des données admin...');
    try {
        const [apps, serviceAccounts] = await Promise.all([
            api.getAllApps(),
            api.getServiceAccounts()
        ]);
        
        console.log('loadAdminData: Apps chargées:', apps?.length || 0);
        console.log('loadAdminData: Service accounts chargés:', serviceAccounts?.length || 0);
        
        allApps = apps;
        allServiceAccounts = serviceAccounts;
        renderAppsTable(apps);
        renderServiceAccountsTable(serviceAccounts);
    } catch (error) {
        console.error('loadAdminData: Erreur:', error);
        throw error;
    }
}

// ===== Status Monitoring =====

/**
 * Rafraîchit le statut de connectivité des applications
 */
async function refreshAppsStatus() {
    const connectivityIcons = document.querySelectorAll('.app-connectivity-icon');
    const isFirstLoad = connectivityIcons.length > 0 && 
        connectivityIcons[0].querySelector('i') === null;

    if (!isFirstLoad) {
        // Rafraîchissement : indicateur subtil sans détruire l'affichage
        connectivityIcons.forEach(el => {
            el.style.opacity = '0.6';
        });
    }
    
    try {
        const status = await api.getAppsStatus();
        console.log('📡 Statut des applications:', status);
        
        // Mettre à jour chaque icône in-place
        status.apps.forEach(appStatus => {
            const cell = document.querySelector(`.app-status-cell[data-app-slug="${appStatus.slug}"]`);
            if (!cell) return;
            const iconEl = cell.querySelector('.app-connectivity-icon');
            if (!iconEl) return;
            
            iconEl.style.opacity = '1';
            let icon = '';
            switch (appStatus.status) {
                case 'online':
                    icon = `<i class="bi bi-wifi text-success" title="En ligne (${appStatus.response_time_ms || '?'}ms)"></i>`;
                    break;
                case 'offline':
                    icon = `<i class="bi bi-wifi-off text-danger" title="Hors ligne: ${appStatus.error || 'Non joignable'}"></i>`;
                    break;
                case 'error':
                    icon = `<i class="bi bi-exclamation-triangle text-warning" title="Erreur: ${appStatus.error || ''}"></i>`;
                    break;
                default:
                    icon = `<i class="bi bi-wifi text-muted" title="Inconnu"></i>`;
            }
            iconEl.innerHTML = icon;
        });

        // Restaurer l'opacité pour les éléments non mis à jour
        connectivityIcons.forEach(el => {
            el.style.opacity = '1';
        });
        
        // Afficher un résumé dans un toast discret
        const summary = status.summary;
        if (summary.offline > 0 || summary.error > 0) {
            showToast('Connectivité', 
                `${summary.online}/${summary.total} applications en ligne`, 
                summary.offline > 0 ? 'warning' : 'info');
        }
    } catch (error) {
        console.error('Erreur lors du test de connectivité:', error);
        // En cas d'erreur réseau, conserver le dernier état connu
        connectivityIcons.forEach(el => {
            el.style.opacity = '1';
        });
    }
}

/**
 * Met à jour le statut Docker d'une app dans le tableau admin (colonne État fusionnée)
 */
function updateAppDockerStatusInTable(slug, dockerStatus) {
    if (!slug) return;
    
    const cell = document.querySelector(`.app-status-cell[data-app-slug="${slug}"]`);
    if (!cell) return;
    
    // Trouver la 2ème icône dans .status-icons (Docker)
    const icons = cell.querySelectorAll('.status-icons > i, .status-icons > span > i');
    const statusIcons = cell.querySelector('.status-icons');
    if (!statusIcons) return;
    
    // L'icône Docker est le 2ème enfant direct de .status-icons
    const children = Array.from(statusIcons.children);
    if (children.length < 2) return;
    const dockerEl = children[1]; // 2ème élément = Docker
    
    let dockerIcon;
    if (!dockerStatus) {
        dockerIcon = '<i class="bi bi-question-circle text-muted" title="Docker: Inconnu"></i>';
    } else if (dockerStatus === 'running') {
        dockerIcon = '<i class="bi bi-play-circle-fill text-success" title="Docker: Running"></i>';
    } else {
        dockerIcon = '<i class="bi bi-pause-circle-fill text-warning" title="Docker: Maintenance"></i>';
    }
    dockerEl.outerHTML = dockerIcon;
    
    console.log(`🔄 Docker status mis à jour pour ${slug}: ${dockerStatus}`);
}

/**
 * Met à jour le statut de santé (health check) d'une app dans le tableau admin
 */
/**
 * Met à jour le statut de santé (health check) d'une app dans le tableau admin (colonne État fusionnée)
 */
function updateAppHealthStatusInTable(slug, healthStatus) {
    if (!slug) return;
    
    const cell = document.querySelector(`.app-status-cell[data-app-slug="${slug}"]`);
    if (!cell) return;
    
    const iconEl = cell.querySelector('.app-connectivity-icon');
    if (!iconEl) return;
    
    let icon;
    if (healthStatus === 'healthy') {
        icon = '<i class="bi bi-wifi text-success" title="Connectivité: En ligne"></i>';
    } else if (healthStatus === 'unhealthy') {
        icon = '<i class="bi bi-wifi-off text-danger" title="Connectivité: Hors ligne"></i>';
    } else {
        icon = '<i class="bi bi-wifi text-muted" title="Connectivité: Inconnu"></i>';
    }
    iconEl.innerHTML = icon;
    
    console.log(`🏥 Health status mis à jour pour ${slug}: ${healthStatus}`);
}

/**
 * Met à jour le statut d'une carte d'app sur le dashboard
 * @param {string} slug - Slug de l'application
 * @param {string} dockerStatus - Statut Docker ('running', 'exited', etc.)
 * @param {string} healthStatus - Statut santé ('healthy', 'unhealthy')
 */
function updateAppCardStatus(slug, dockerStatus, healthStatus) {
    if (!slug) return;
    
    const card = document.querySelector(`.app-card[data-app-slug="${slug}"]`);
    if (!card) return;
    
    const isDockerStopped = dockerStatus && dockerStatus !== 'running';
    const isHealthUnhealthy = healthStatus === 'unhealthy';
    const isInMaintenance = isDockerStopped || isHealthUnhealthy;
    
    if (isInMaintenance) {
        card.classList.add('app-inactive');
        // Ajouter un badge maintenance si pas déjà présent
        if (!card.querySelector('.maintenance-badge')) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark maintenance-badge position-absolute top-0 end-0 m-2';
            badge.innerHTML = '<i class="bi bi-tools me-1"></i>Maintenance';
            card.style.position = 'relative';
            card.appendChild(badge);
        }
    } else {
        card.classList.remove('app-inactive');
        // Retirer le badge maintenance
        const badge = card.querySelector('.maintenance-badge');
        if (badge) badge.remove();
    }
}

/**
 * Intervalle de rafraîchissement automatique de la connectivité
 */
let connectivityRefreshInterval = null;
const CONNECTIVITY_REFRESH_DELAY = 30000; // 30 secondes

/**
 * Démarre le rafraîchissement périodique de la connectivité
 */
function startConnectivityRefresh() {
    if (connectivityRefreshInterval) return; // Déjà démarré
    
    connectivityRefreshInterval = setInterval(() => {
        // Rafraîchir seulement si on est sur la page apps-admin
        if (currentPage === 'apps-admin') {
            console.log('🔄 Rafraîchissement automatique de la connectivité...');
            refreshAppsStatus();
        }
    }, CONNECTIVITY_REFRESH_DELAY);
    
    console.log(`⏱️ Rafraîchissement connectivité activé (${CONNECTIVITY_REFRESH_DELAY/1000}s)`);
}

/**
 * Arrête le rafraîchissement périodique
 */
function stopConnectivityRefresh() {
    if (connectivityRefreshInterval) {
        clearInterval(connectivityRefreshInterval);
        connectivityRefreshInterval = null;
        console.log('⏱️ Rafraîchissement connectivité désactivé');
    }
}

/**
 * Rafraîchit le statut des Service Accounts
 */
async function refreshServiceAccountsStatus() {
    const credentialsCells = document.querySelectorAll('.sa-credentials-status');
    
    // Indicateur subtil de rafraîchissement
    credentialsCells.forEach(cell => {
        cell.style.opacity = '0.6';
    });
    
    try {
        const status = await api.getServiceAccountsStatus();
        console.log('📡 Statut des Service Accounts:', status);
        
        // Mettre à jour chaque cellule avec une icône
        status.service_accounts.forEach(saStatus => {
            const cell = document.querySelector(`.sa-credentials-status[data-sa-id="${saStatus.id}"]`);
            if (cell) {
                cell.style.opacity = '1';
                let icon = '';
                switch (saStatus.credentials_status) {
                    case 'file_exists':
                        icon = `<i class="bi bi-file-earmark-check-fill text-success" title="Credentials OK: ${saStatus.credentials_file || ''}"></i>`;
                        break;
                    case 'file_missing':
                        icon = `<i class="bi bi-file-earmark-x-fill text-danger" title="Fichier de credentials manquant"></i>`;
                        break;
                    case 'no_app':
                        icon = `<i class="bi bi-dash-circle text-muted" title="Pas d'application associée"></i>`;
                        break;
                    default:
                        icon = `<i class="bi bi-question-circle text-muted" title="Credentials: Inconnu"></i>`;
                }
                cell.innerHTML = icon;
            }
        });

        // Restaurer l'opacité
        credentialsCells.forEach(cell => {
            cell.style.opacity = '1';
        });
        
        // Afficher un résumé
        const summary = status.summary;
        if (summary.missing_credentials > 0) {
            showToast('Service Accounts', 
                `${summary.missing_credentials} SA sans fichier de credentials`, 
                'warning');
        }
    } catch (error) {
        console.error('Erreur lors de la vérification des SA:', error);
        credentialsCells.forEach(cell => {
            cell.style.opacity = '1';
        });
    }
}

function renderAppsTable(apps) {
    const container = document.getElementById('appsTable');
    
    if (apps.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucune application configurée</p>';
        return;
    }

    // Compter les apps pending pour le badge
    const pendingCount = apps.filter(a => a.approval_status === 'pending' || a.approval_status === 'rejected').length;
    const badge = document.getElementById('pendingAppsBadge');
    if (badge) {
        if (pendingCount > 0) {
            badge.textContent = pendingCount;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
    }

    container.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <small class="text-muted">${apps.length} application(s)</small>
            <button class="btn btn-sm btn-outline-secondary" onclick="refreshAppsStatus()" title="Vérifier la connectivité">
                <i class="bi bi-arrow-clockwise me-1"></i>Tester connectivité
            </button>
        </div>
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Application</th>
                    <th>Slug</th>
                    <th>URL Frontend</th>
                    <th>Utilisateurs</th>
                    <th class="text-center">Actif</th>
                    <th>État</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody id="appsTableBody">
                ${apps.map(app => {
                    // Les ic\u00f4nes Docker/Health sont d'abord en \u00e9tat "inconnu"
                    // puis mises \u00e0 jour via SSE (docker_status_changed / app_health_changed)
                    let dockerIcon = '<i class="bi bi-question-circle text-muted" title="Docker: Chargement..."></i>';
                    let healthIcon = '<i class="bi bi-wifi text-muted" title="Connectivit\u00e9: V\u00e9rification..."></i>';
                    
                    // Badge approbation
                    let approvalBadge = '';
                    let approvalActions = '';
                    const approvalStatus = app.approval_status;
                    if (approvalStatus === 'pending') {
                        approvalBadge = '<span class="badge bg-warning text-dark ms-1">En attente</span>';
                        approvalActions = `
                            <button class="btn btn-sm btn-success me-1" onclick="approveApp('${escapeHtml(app.slug)}')" title="Approuver">
                                <i class="bi bi-check-lg"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger me-1" onclick="showRejectAppModal('${escapeHtml(app.slug)}', '${escapeHtml(app.name)}')" title="Rejeter">
                                <i class="bi bi-x-lg"></i>
                            </button>
                        `;
                    } else if (approvalStatus === 'rejected') {
                        approvalBadge = '<span class="badge bg-danger ms-1">Rejeté</span>';
                        approvalActions = `
                            <button class="btn btn-sm btn-success me-1" onclick="approveApp('${escapeHtml(app.slug)}')" title="Approuver">
                                <i class="bi bi-check-lg"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-secondary me-1" onclick="deletePendingApp('${escapeHtml(app.slug)}')" title="Supprimer">
                                <i class="bi bi-trash"></i>
                            </button>
                        `;
                    }
                    
                    // Toggle switch pour activation
                    const isPending = approvalStatus === 'pending' || approvalStatus === 'rejected';
                    const toggleSwitch = isPending ? '' : `
                        <div class="form-check form-switch d-flex justify-content-center mb-0">
                            <input class="form-check-input" type="checkbox" role="switch" 
                                id="toggleApp-${app.id}" 
                                ${app.is_active ? 'checked' : ''} 
                                onchange="toggleAppActive(${app.id})"
                                title="${app.is_active ? 'Désactiver' : 'Activer'}">
                        </div>
                    `;
                    
                    const rowClass = isPending ? 'table-warning' : (approvalStatus === 'rejected' ? 'table-danger' : '');
                    
                    return `
                    <tr data-app-id="${app.id}" class="${rowClass}">
                        <td>
                            <div class="d-flex align-items-center">
                                <span class="me-2 rounded p-1" style="background-color: ${app.color}; color: white;">
                                    <i class="${app.icon}"></i>
                                </span>
                                <div>
                                    <strong>${escapeHtml(app.name)}</strong>
                                    ${app.is_shared_service ? '<span class="badge bg-info ms-1" title="API partagée accessible par tous les SA"><i class="bi bi-share"></i></span>' : ''}
                                    ${approvalBadge}
                                </div>
                            </div>
                        </td>
                        <td><code>${app.slug}</code></td>
                        <td><small class="text-muted">${escapeHtml(app.frontend_url || '-')}</small></td>
                        <td><span class="badge bg-secondary">${app.users?.length || 0}</span></td>
                        <td class="text-center">${toggleSwitch}</td>
                        <td class="app-status-cell" data-app-slug="${app.slug}">
                            <div class="status-icons">
                                ${dockerIcon}
                                <span class="app-connectivity-icon">${healthIcon}</span>
                            </div>
                        </td>
                        <td class="text-end text-nowrap">
                            ${approvalActions}
                            ${!isPending ? `
                                <button class="btn btn-sm btn-outline-info me-1" onclick="showDockerDetails('${app.slug}')" title="Détails Docker">
                                    <i class="bi bi-eye"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-primary me-1" onclick="editApp(${app.id})">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" onclick="deleteApp(${app.id}, '${escapeHtml(app.name)}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            ` : ''}
                        </td>
                    </tr>
                `}).join('')}
            </tbody>
        </table>
    `;
    
    // Note: Les rafraîchissements de connectivité et stats Docker sont gérés
    // par startConnectivityRefresh() déclenché par l'événement shown.bs.modal
    // Ne pas les relancer ici pour éviter des appels API doublonnés
}

/**
 * Bascule l'état actif/inactif d'une application (toggle switch)
 */
async function toggleAppActive(appId) {
    const toggle = document.getElementById(`toggleApp-${appId}`);
    const originalState = !toggle.checked; // L'état vient de changer, on garde l'ancien
    
    try {
        const result = await api.toggleAppActive(appId);
        showToast('Mise à jour', result.message, 'success');
        
        // Rafraîchir le dashboard si la page est visible
        loadApps();
    } catch (error) {
        console.error('Erreur toggle app:', error);
        // Rollback le switch
        toggle.checked = originalState;
        showToast('Erreur', error.message, 'danger');
    }
}

function renderUsersTable(users) {
    const container = document.getElementById('usersTable');
    
    container.innerHTML = `
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Email</th>
                    <th>Rôle Hub</th>
                    <th>Applications (Rôle)</th>
                    <th>Statut</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${users.map(user => `
                    <tr>
                        <td><strong>${escapeHtml(user.username)}</strong></td>
                        <td><small class="text-muted">${escapeHtml(user.email || '-')}</small></td>
                        <td>
                            ${user.is_admin 
                                ? '<span class="badge bg-dark">Admin</span>' 
                                : '<span class="badge bg-light text-dark">User</span>'}
                        </td>
                        <td>
                            ${user.app_roles?.map(ar => `
                                <span class="badge me-1" style="background-color: ${getRoleBadgeColor(ar.role)}">
                                    ${(ar.source || '').startsWith('profile') ? '<i class="bi bi-person-badge"></i> ' : ''}
                                    ${escapeHtml(ar.app_name)} 
                                    <small>(${ar.role})</small>
                                </span>
                            `).join('') || '<span class="text-muted">-</span>'}
                        </td>
                        <td>
                            ${user.is_active 
                                ? '<span class="status-active"><i class="bi bi-check-circle-fill"></i></span>' 
                                : '<span class="status-inactive"><i class="bi bi-x-circle-fill"></i></span>'}
                        </td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-primary me-1" onclick="editUser(${user.id})">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${user.id}, '${escapeHtml(user.username)}')" 
                                ${user.id === currentUser.id ? 'disabled' : ''}>
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// Couleurs des badges selon le rôle
function getRoleBadgeColor(role) {
    const colors = {
        'admin': '#dc3545',     // Rouge
        'manager': '#fd7e14',   // Orange
        'user': '#0d6efd',      // Bleu
        'viewer': '#6c757d',    // Gris
        'service': '#6f42c1'    // Violet
    };
    return colors[role] || '#0d6efd';
}

// ===== App CRUD =====

async function showAddAppForm() {
    // Charger le host du Hub si pas encore fait
    if (!hubHost) {
        try {
            const config = await api.getHubHost();
            hubHost = config.host;
        } catch (e) {
            console.error('Impossible de récupérer le host du Hub:', e);
            hubHost = window.location.hostname;
        }
    }
    
    document.getElementById('appFormTitle').textContent = 'Nouvelle Application';
    document.getElementById('appForm').reset();
    document.getElementById('appId').value = '';
    document.getElementById('appMode').value = 'create';
    document.getElementById('appName').required = true;
    document.getElementById('appIcon').value = 'bi-app';
    document.getElementById('appColor').value = '#0d6efd';
    document.getElementById('appActive').checked = true;
    document.getElementById('appRequiresAuth').checked = true;
    document.getElementById('appCustomHost').checked = false;
    document.getElementById('customHostGroup').classList.add('d-none');
    document.getElementById('appSlug').readOnly = true;
    document.getElementById('slugHint').textContent = 'Généré automatiquement';
    document.getElementById('appFrontendHttps').value = 'false';
    
    // Afficher les éléments de création
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.remove('d-none');
    
    // Mode simplifié (ports)
    document.getElementById('simplePortsSection').classList.remove('d-none');
    document.getElementById('advancedUrlsSection').classList.add('d-none');
    document.getElementById('appApiPort').required = true;
    document.getElementById('appApiUrl').required = false;
    
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    updateAppFormPreview();
    updateUrlPreview();
    appFormModal.show();
}

// ===== Docker Details Modal =====

/**
 * Construit la carte HTML des stats CPU/RAM/Uptime d'un conteneur
 */
function buildContainerStatsCard(stats) {
    const cpuPercent = (stats.cpu_percent || 0).toFixed(1);
    const memUsage = (stats.memory_usage_mb || 0).toFixed(1);
    const memLimit = (stats.memory_limit_mb || 0).toFixed(0);
    const memPercent = memLimit > 0 ? ((stats.memory_usage_mb / stats.memory_limit_mb) * 100).toFixed(1) : 0;
    const uptimeSec = stats.uptime_seconds || 0;

    // Couleur selon le pourcentage
    function barColor(pct) {
        if (pct >= 80) return 'bg-danger';
        if (pct >= 50) return 'bg-warning';
        return 'bg-success';
    }

    // Formatage uptime
    let uptimeStr = '';
    if (uptimeSec >= 86400) {
        const days = Math.floor(uptimeSec / 86400);
        const hours = Math.floor((uptimeSec % 86400) / 3600);
        uptimeStr = days + 'j ' + hours + 'h';
    } else if (uptimeSec >= 3600) {
        const hours = Math.floor(uptimeSec / 3600);
        const mins = Math.floor((uptimeSec % 3600) / 60);
        uptimeStr = hours + 'h ' + mins + 'min';
    } else {
        uptimeStr = Math.floor(uptimeSec / 60) + 'min';
    }

    return '<div class="card mb-3">'
        + '<div class="card-header py-2"><i class="bi bi-speedometer2 me-2"></i>Ressources</div>'
        + '<div class="card-body py-2">'
        + '<div class="mb-2">'
        + '<div class="d-flex justify-content-between"><small>CPU</small><small>' + cpuPercent + '%</small></div>'
        + '<div class="progress" style="height: 8px;">'
        + '<div class="progress-bar ' + barColor(parseFloat(cpuPercent)) + '" style="width: ' + cpuPercent + '%;"></div>'
        + '</div>'
        + '</div>'
        + '<div class="mb-2">'
        + '<div class="d-flex justify-content-between"><small>RAM</small><small>' + memUsage + ' / ' + memLimit + ' Mo (' + memPercent + '%)</small></div>'
        + '<div class="progress" style="height: 8px;">'
        + '<div class="progress-bar ' + barColor(parseFloat(memPercent)) + '" style="width: ' + memPercent + '%;"></div>'
        + '</div>'
        + '</div>'
        + '<div class="d-flex justify-content-between">'
        + '<small class="text-muted">Uptime</small>'
        + '<small><i class="bi bi-clock me-1"></i>' + uptimeStr + '</small>'
        + '</div>'
        + '</div>'
        + '</div>';
}

/**
 * Affiche les détails Docker d'une application
 */
async function showDockerDetails(slug) {
    // Créer la modal si elle n'existe pas
    let modalEl = document.getElementById('dockerInfoModal');
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.className = 'modal fade';
        modalEl.id = 'dockerInfoModal';
        modalEl.tabIndex = -1;
        modalEl.innerHTML = `
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            <i class="bi bi-box me-2"></i>Informations Docker
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="dockerInfoContent">
                        <div class="text-center py-4">
                            <div class="spinner-border text-primary" role="status"></div>
                            <p class="mt-2 text-muted">Chargement...</p>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modalEl);
    }
    
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    // Réinitialiser le contenu avec le spinner de chargement
    document.getElementById('dockerInfoContent').innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status"></div>
            <p class="mt-2 text-muted">Chargement...</p>
        </div>
    `;
    modal.show();
    
    const content = document.getElementById('dockerInfoContent');
    
    try {
        const info = await api.getAppDockerInfo(slug);
        console.log('🐳 Docker info:', info);
        
        if (!info.container_found) {
            content.innerHTML = `
                <div class="alert alert-warning">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    <strong>Conteneur non trouvé</strong>
                    <p class="mb-0 mt-2">${info.developer_info?.warning || 'Le conteneur Docker n\'a pas été trouvé.'}</p>
                    ${info.developer_info?.tip ? `<small class="text-muted">${info.developer_info.tip}</small>` : ''}
                </div>
                <div class="card">
                    <div class="card-header"><i class="bi bi-info-circle me-2"></i>Informations Application</div>
                    <div class="card-body">
                        <table class="table table-sm mb-0">
                            <tr><th style="width: 150px;">Slug</th><td><code>${escapeHtml(info.app_slug)}</code></td></tr>
                            <tr><th>Nom</th><td>${escapeHtml(info.app_name)}</td></tr>
                            <tr><th>URL API</th><td><a href="${escapeHtml(info.app_api_url)}" target="_blank">${escapeHtml(info.app_api_url)}</a></td></tr>
                            ${info.app_frontend_url ? `<tr><th>URL Frontend</th><td><a href="${escapeHtml(info.app_frontend_url)}" target="_blank">${escapeHtml(info.app_frontend_url)}</a></td></tr>` : ''}
                        </table>
                    </div>
                </div>
            `;
            return;
        }
        
        // Conteneur trouvé - afficher toutes les infos
        const statusBadge = info.container_status === 'running' 
            ? '<span class="badge bg-success"><i class="bi bi-play-circle me-1"></i>Running</span>'
            : '<span class="badge bg-warning text-dark"><i class="bi bi-pause-circle me-1"></i>' + escapeHtml(info.container_status) + '</span>';
        
        content.innerHTML = `
            <!-- Statut global -->
            <div class="d-flex align-items-center mb-3">
                <span class="me-2 fs-4"><i class="bi bi-box text-primary"></i></span>
                <div>
                    <h5 class="mb-0">${escapeHtml(info.container_name)}</h5>
                    <small class="text-muted">ID: ${escapeHtml(info.container_id)}</small>
                </div>
                <span class="ms-auto">${statusBadge}</span>
            </div>
            
            <div class="row g-3">
                <!-- Colonne gauche -->
                <div class="col-md-6">
                    <!-- Info Conteneur -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-box me-2"></i>Conteneur</div>
                        <div class="card-body py-2">
                            <table class="table table-sm mb-0">
                                <tr><th style="width: 100px;">Image</th><td><code>${escapeHtml(info.container_image || 'N/A')}</code></td></tr>
                                <tr><th>Créé</th><td>${info.container_created ? formatDateTime(info.container_created) : 'N/A'}</td></tr>
                                <tr><th>Démarré</th><td>${info.container_started_at ? formatDateTime(info.container_started_at) : 'N/A'}</td></tr>
                            </table>
                        </div>
                    </div>
                    
                    <!-- Ports -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-plug me-2"></i>Ports exposés</div>
                        <div class="card-body py-2">
                            ${info.ports && info.ports.length > 0 ? `
                                <table class="table table-sm mb-0">
                                    <thead><tr><th>Conteneur</th><th>→</th><th>Hôte</th><th>Proto</th></tr></thead>
                                    <tbody>
                                        ${info.ports.map(p => `
                                            <tr>
                                                <td>${p.container_port}</td>
                                                <td>→</td>
                                                <td><strong>${p.host_port || '-'}</strong></td>
                                                <td><span class="badge bg-light text-dark">${p.protocol}</span></td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun port exposé</p>'}
                        </div>
                    </div>
                    
                    <!-- Réseaux -->
                    <div class="card">
                        <div class="card-header py-2"><i class="bi bi-diagram-3 me-2"></i>Réseaux Docker</div>
                        <div class="card-body py-2">
                            ${info.networks && info.networks.length > 0 ? `
                                <table class="table table-sm mb-0">
                                    <thead><tr><th>Réseau</th><th>IP</th></tr></thead>
                                    <tbody>
                                        ${info.networks.map(n => `
                                            <tr>
                                                <td><code>${escapeHtml(n)}</code></td>
                                                <td><code>${info.ip_addresses[n] || '-'}</code></td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun réseau</p>'}
                        </div>
                    </div>
                </div>
                
                <!-- Colonne droite -->
                <div class="col-md-6">
                    <!-- Variables APP_* -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-gear me-2"></i>Variables APP_*</div>
                        <div class="card-body py-2">
                            ${info.app_env_vars && Object.keys(info.app_env_vars).length > 0 ? `
                                <table class="table table-sm mb-0">
                                    ${Object.entries(info.app_env_vars).map(([k, v]) => `
                                        <tr><th style="width: 120px;">${escapeHtml(k)}</th><td><code>${escapeHtml(v)}</code></td></tr>
                                    `).join('')}
                                </table>
                            ` : '<p class="text-muted mb-0">Aucune variable APP_*</p>'}
                        </div>
                    </div>
                    
                    <!-- Labels Hub -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-tag me-2"></i>Labels hub.*</div>
                        <div class="card-body py-2">
                            ${info.hub_labels && Object.keys(info.hub_labels).length > 0 ? `
                                <table class="table table-sm mb-0">
                                    ${Object.entries(info.hub_labels).map(([k, v]) => `
                                        <tr><th style="width: 120px;">${escapeHtml(k)}</th><td><code>${escapeHtml(v)}</code></td></tr>
                                    `).join('')}
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun label hub.*</p>'}
                        </div>
                    </div>
                    
                    <!-- Ressources CPU/RAM -->
                    ${info.container_stats ? buildContainerStatsCard(info.container_stats) : ''}
                    
                    <!-- Info développeur -->
                    <div class="card border-info mb-3">
                        <div class="card-header py-2 bg-info text-white"><i class="bi bi-code-slash me-2"></i>Pour développeurs</div>
                        <div class="card-body py-2">
                            <table class="table table-sm mb-0">
                                <tr>
                                    <th style="width: 100px;">URL API</th>
                                    <td>
                                        <a href="${escapeHtml(info.app_api_url)}" target="_blank" class="text-break">
                                            ${escapeHtml(info.app_api_url)}
                                        </a>
                                    </td>
                                </tr>
                                ${info.app_frontend_url ? `
                                <tr>
                                    <th>Frontend</th>
                                    <td>
                                        <a href="${escapeHtml(info.app_frontend_url)}" target="_blank" class="text-break">
                                            ${escapeHtml(info.app_frontend_url)}
                                        </a>
                                    </td>
                                </tr>
                                ` : ''}
                                <tr>
                                    <th>Header</th>
                                    <td><code>X-Slug: ${escapeHtml(info.app_slug)}</code></td>
                                </tr>
                            </table>
                            
                            <div class="mt-2">
                                <small class="text-muted">Commandes utiles :</small>
                                <div class="bg-dark text-light p-2 rounded mt-1" style="font-size: 0.8rem;">
                                    <code class="text-info">docker logs ${escapeHtml(info.container_name)} --tail 100 -f</code><br>
                                    <code class="text-warning">docker exec -it ${escapeHtml(info.container_name)} /bin/bash</code>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    } catch (error) {
        console.error('Erreur récupération Docker info:', error);
        content.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-x-circle me-2"></i>
                <strong>Erreur</strong>
                <p class="mb-0">${escapeHtml(error.message || 'Impossible de récupérer les informations Docker')}</p>
            </div>
        `;
    }
}

/**
 * Formate une date ISO pour l'affichage
 */
function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    try {
        const date = new Date(isoString);
        return date.toLocaleString('fr-FR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        return isoString;
    }
}

async function editApp(id) {
    const app = allApps.find(a => a.id === id);
    if (!app) {
        showToast('Erreur', 'Application non trouvée. Veuillez rafraîchir la page.', 'danger');
        try { await loadAdminData(); } catch (e) { console.error('Impossible de recharger les apps:', e); }
        return;
    }

    document.getElementById('appForm').reset();
    
    document.getElementById('appFormTitle').textContent = 'Modifier l\'Application';
    document.getElementById('appId').value = app.id;
    document.getElementById('appMode').value = 'edit';
    
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.add('d-none');
    
    document.getElementById('appName').value = app.name;
    document.getElementById('appName').required = true;
    document.getElementById('appSlug').value = app.slug;
    document.getElementById('appSlug').readOnly = true;
    document.getElementById('slugHint').textContent = 'Non modifiable';
    document.getElementById('appDescription').value = app.description || '';
    document.getElementById('appIcon').value = app.icon;
    document.getElementById('appColor').value = app.color;
    document.getElementById('appApiUrl').value = app.api_url;
    document.getElementById('appFrontendUrl').value = app.frontend_url || '';
    document.getElementById('appCallbackUrl').value = app.callback_url || '';
    document.getElementById('appActive').checked = app.is_active;
    document.getElementById('appRequiresAuth').checked = app.requires_auth;
    document.getElementById('appIsSharedService').checked = app.is_shared_service || false;
    
    // Mode édition : URLs directes
    document.getElementById('simplePortsSection').classList.add('d-none');
    document.getElementById('advancedUrlsSection').classList.remove('d-none');
    document.getElementById('appApiPort').required = false;
    document.getElementById('appApiUrl').required = true;
    
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    updateAppFormPreview();
    
    appFormModal.show();
}

function toggleCustomHost() {
    const checked = document.getElementById('appCustomHost').checked;
    document.getElementById('customHostGroup').classList.toggle('d-none', !checked);
    updateUrlPreview();
}

function updateUrlPreview() {
    const apiPort = document.getElementById('appApiPort').value;
    const frontendPort = document.getElementById('appFrontendPort').value;
    const customHost = document.getElementById('appCustomHost').checked;
    const customHostValue = document.getElementById('appCustomHostValue').value;
    
    const host = customHost && customHostValue ? customHostValue : (hubHost || 'chargement...');
    
    const previewApi = document.getElementById('previewApiUrl');
    const previewFrontend = document.getElementById('previewFrontendUrl');
    
    if (apiPort) {
        previewApi.innerHTML = `<i class="bi bi-hdd-rack me-1"></i>API: <code>http://${host}:${apiPort}</code>`;
    } else {
        previewApi.innerHTML = '<i class="bi bi-hdd-rack me-1"></i>API: <span class="text-danger">Port requis</span>';
    }
    
    if (frontendPort) {
        previewFrontend.innerHTML = `<i class="bi bi-window me-1"></i>Front: <code>http://${host}:${frontendPort}</code>`;
    } else {
        previewFrontend.innerHTML = '';
    }
}

function updateAppFormPreview() {
    const icon = document.getElementById('appIcon').value;
    const color = document.getElementById('appColor').value;
    const preview = document.getElementById('appFormPreview');
    if (!preview) return;
    preview.style.background = color;
    preview.querySelector('i').className = `bi ${icon}`;
}

async function handleSaveApp(event) {
    event.preventDefault();
    
    const id = document.getElementById('appId').value;
    const mode = document.getElementById('appMode').value;
    
    try {
        if (id && mode === 'edit') {
            // Mode édition - utiliser les URLs directes
            // Ne pas envoyer les champs vides pour éviter d'écraser les valeurs existantes
            const data = {
                name: document.getElementById('appName').value,
                description: document.getElementById('appDescription').value || null,
                icon: document.getElementById('appIcon').value,
                color: document.getElementById('appColor').value,
                is_active: document.getElementById('appActive').checked,
                requires_auth: document.getElementById('appRequiresAuth').checked,
                is_shared_service: document.getElementById('appIsSharedService').checked
            };
            
            // Ajouter les URLs seulement si elles sont renseignées
            const apiUrl = document.getElementById('appApiUrl').value;
            const frontendUrl = document.getElementById('appFrontendUrl').value;
            const callbackUrl = document.getElementById('appCallbackUrl').value;
            
            if (apiUrl) data.api_url = apiUrl;
            if (frontendUrl) data.frontend_url = frontendUrl;
            else if (frontendUrl === '') data.frontend_url = null;  // Permettre de vider explicitement
            if (callbackUrl) data.callback_url = callbackUrl;
            else if (callbackUrl === '') data.callback_url = null;  // Permettre de vider explicitement
            
            await api.updateApp(id, data);
            showToast('Succès', 'Application modifiée', 'success');
            
            appFormModal.hide();
            await loadAdminData();
        } else {
            // Mode création - utiliser les ports (schéma simplifié)
            // Le M2M sera créé automatiquement quand l'app s'auto-enregistre
            const customHost = document.getElementById('appCustomHost').checked;
            const isSharedService = document.getElementById('appIsSharedService')?.checked || false;
            const data = {
                name: document.getElementById('appName').value,
                slug: document.getElementById('appSlug').value,
                description: document.getElementById('appDescription').value || null,
                icon: document.getElementById('appIcon').value,
                color: document.getElementById('appColor').value,
                api_port: parseInt(document.getElementById('appApiPort').value),
                frontend_port: document.getElementById('appFrontendPort').value ? 
                    parseInt(document.getElementById('appFrontendPort').value) : null,
                custom_host: customHost ? document.getElementById('appCustomHostValue').value : null,
                requires_auth: document.getElementById('appRequiresAuth').checked,
                is_shared_service: isSharedService,
                create_service_account: false  // L'app créera son M2M en s'auto-enregistrant
            };
            
            await api.createApp(data);
            appFormModal.hide();
            const sharedMsg = isSharedService ? ' (API partagée M2M)' : '';
            showToast('Succès', `Application créée${sharedMsg} - le M2M sera créé au démarrage de l'app`, 'success');
            
            // Rafraîchir le token pour avoir accès à la nouvelle app immédiatement
            await refreshTokenAfterAppChange('création');
            
            await loadAdminData();
        }
        loadApps(); // Refresh dashboard
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteApp(id, name) {
    if (!confirm(`Supprimer l'application "${name}" ?`)) return;
    
    try {
        await api.deleteApp(id);
        showToast('Succès', 'Application supprimée', 'success');
        await loadAdminData();
        loadApps();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function updateIconPreview() {
    const icon = document.getElementById('appIcon').value;
    document.getElementById('iconPreview').className = `bi ${icon}`;
    updateIconPickerSelection();
    updateAppFormPreview();
}

// ===== Icon Picker =====

// Liste des icônes Bootstrap populaires organisées par catégorie
const BOOTSTRAP_ICONS = [
    // Applications & UI
    'bi-app', 'bi-app-indicator', 'bi-grid', 'bi-grid-3x3', 'bi-grid-3x3-gap', 'bi-grid-3x3-gap-fill',
    'bi-layout-text-window', 'bi-layout-text-sidebar', 'bi-window', 'bi-window-stack', 'bi-window-sidebar',
    'bi-columns', 'bi-columns-gap', 'bi-view-list', 'bi-view-stacked', 'bi-kanban',
    // Documents
    'bi-file-earmark', 'bi-file-earmark-text', 'bi-file-earmark-code', 'bi-file-earmark-pdf', 'bi-file-earmark-spreadsheet',
    'bi-file-earmark-richtext', 'bi-file-earmark-zip', 'bi-file-earmark-image', 'bi-file-earmark-music', 'bi-file-earmark-play',
    'bi-files', 'bi-folder', 'bi-folder-fill', 'bi-folder2', 'bi-folder2-open',
    // Personnes
    'bi-person', 'bi-person-fill', 'bi-person-circle', 'bi-person-badge', 'bi-person-badge-fill',
    'bi-people', 'bi-people-fill', 'bi-person-plus', 'bi-person-plus-fill', 'bi-person-check', 'bi-person-check-fill',
    'bi-person-gear', 'bi-person-workspace', 'bi-person-video', 'bi-person-lines-fill', 'bi-person-vcard',
    // Business
    'bi-briefcase', 'bi-briefcase-fill', 'bi-building', 'bi-buildings', 'bi-buildings-fill',
    'bi-shop', 'bi-shop-window', 'bi-cart', 'bi-cart-fill', 'bi-cart3', 'bi-cart4',
    'bi-basket', 'bi-basket-fill', 'bi-bag', 'bi-bag-fill', 'bi-handbag', 'bi-handbag-fill',
    'bi-wallet', 'bi-wallet-fill', 'bi-wallet2', 'bi-credit-card', 'bi-credit-card-fill',
    'bi-bank', 'bi-bank2', 'bi-piggy-bank', 'bi-piggy-bank-fill', 'bi-cash', 'bi-cash-stack', 'bi-cash-coin',
    'bi-currency-euro', 'bi-currency-dollar', 'bi-receipt', 'bi-receipt-cutoff',
    // Tech & Infra
    'bi-hdd', 'bi-hdd-fill', 'bi-hdd-network', 'bi-hdd-network-fill', 'bi-hdd-rack', 'bi-hdd-rack-fill',
    'bi-hdd-stack', 'bi-hdd-stack-fill', 'bi-server', 'bi-database', 'bi-database-fill',
    'bi-cpu', 'bi-cpu-fill', 'bi-memory', 'bi-gpu-card', 'bi-motherboard', 'bi-motherboard-fill',
    'bi-pc', 'bi-pc-display', 'bi-pc-display-horizontal', 'bi-laptop', 'bi-laptop-fill',
    'bi-phone', 'bi-phone-fill', 'bi-tablet', 'bi-tablet-fill', 'bi-smartwatch',
    'bi-router', 'bi-router-fill', 'bi-modem', 'bi-modem-fill', 'bi-wifi', 'bi-broadcast',
    // Cloud & Réseau
    'bi-cloud', 'bi-cloud-fill', 'bi-cloud-upload', 'bi-cloud-download', 'bi-cloud-check', 'bi-cloud-check-fill',
    'bi-cloud-arrow-up', 'bi-cloud-arrow-down', 'bi-cloud-slash', 'bi-clouds', 'bi-clouds-fill',
    'bi-globe', 'bi-globe2', 'bi-globe-americas', 'bi-globe-europe-africa', 'bi-globe-asia-australia',
    'bi-link', 'bi-link-45deg', 'bi-share', 'bi-share-fill', 'bi-diagram-2', 'bi-diagram-3',
    // Charts & Data
    'bi-graph-up', 'bi-graph-up-arrow', 'bi-graph-down', 'bi-bar-chart', 'bi-bar-chart-fill',
    'bi-bar-chart-line', 'bi-bar-chart-steps', 'bi-pie-chart', 'bi-pie-chart-fill',
    'bi-activity', 'bi-speedometer', 'bi-speedometer2', 'bi-clipboard-data', 'bi-clipboard-data-fill',
    // Sécurité
    'bi-shield', 'bi-shield-fill', 'bi-shield-check', 'bi-shield-lock', 'bi-shield-lock-fill',
    'bi-lock', 'bi-lock-fill', 'bi-unlock', 'bi-unlock-fill', 'bi-key', 'bi-key-fill',
    'bi-fingerprint', 'bi-eye', 'bi-eye-fill', 'bi-eye-slash', 'bi-eye-slash-fill',
    // Outils & Settings
    'bi-gear', 'bi-gear-fill', 'bi-gear-wide', 'bi-gear-wide-connected', 'bi-gears',
    'bi-tools', 'bi-wrench', 'bi-wrench-adjustable', 'bi-hammer', 'bi-screwdriver',
    'bi-sliders', 'bi-sliders2', 'bi-toggles', 'bi-toggles2',
    // Communication
    'bi-chat', 'bi-chat-fill', 'bi-chat-dots', 'bi-chat-dots-fill', 'bi-chat-left-text', 'bi-chat-left-text-fill',
    'bi-chat-square', 'bi-chat-square-fill', 'bi-chat-square-text', 'bi-chat-quote', 'bi-chat-quote-fill',
    'bi-envelope', 'bi-envelope-fill', 'bi-envelope-open', 'bi-envelope-at', 'bi-mailbox', 'bi-mailbox2',
    'bi-telephone', 'bi-telephone-fill', 'bi-telephone-inbound', 'bi-telephone-outbound', 'bi-headset',
    'bi-megaphone', 'bi-megaphone-fill', 'bi-bell', 'bi-bell-fill', 'bi-bell-slash',
    // Médias
    'bi-camera', 'bi-camera-fill', 'bi-camera-video', 'bi-camera-video-fill', 'bi-film',
    'bi-image', 'bi-image-fill', 'bi-images', 'bi-card-image', 'bi-file-image',
    'bi-music-note', 'bi-music-note-beamed', 'bi-music-note-list', 'bi-volume-up', 'bi-volume-up-fill',
    'bi-play', 'bi-play-fill', 'bi-play-circle', 'bi-play-circle-fill', 'bi-play-btn', 'bi-play-btn-fill',
    'bi-mic', 'bi-mic-fill', 'bi-mic-mute', 'bi-mic-mute-fill', 'bi-record', 'bi-record-fill',
    // Maps & Lieux
    'bi-geo', 'bi-geo-fill', 'bi-geo-alt', 'bi-geo-alt-fill', 'bi-pin', 'bi-pin-fill',
    'bi-pin-map', 'bi-pin-map-fill', 'bi-map', 'bi-map-fill', 'bi-compass', 'bi-compass-fill',
    'bi-signpost', 'bi-signpost-fill', 'bi-signpost-2', 'bi-signpost-2-fill', 'bi-signpost-split',
    'bi-house', 'bi-house-fill', 'bi-house-door', 'bi-house-door-fill', 'bi-house-gear', 'bi-house-gear-fill',
    // Transport
    'bi-car-front', 'bi-car-front-fill', 'bi-truck', 'bi-truck-front', 'bi-truck-front-fill',
    'bi-bicycle', 'bi-scooter', 'bi-bus-front', 'bi-bus-front-fill', 'bi-train-front', 'bi-train-front-fill',
    'bi-airplane', 'bi-airplane-fill', 'bi-rocket', 'bi-rocket-fill', 'bi-rocket-takeoff', 'bi-rocket-takeoff-fill',
    // Nature & Météo
    'bi-sun', 'bi-sun-fill', 'bi-moon', 'bi-moon-fill', 'bi-stars', 'bi-star', 'bi-star-fill',
    'bi-cloud-sun', 'bi-cloud-sun-fill', 'bi-cloud-moon', 'bi-cloud-rain', 'bi-cloud-rain-fill',
    'bi-lightning', 'bi-lightning-fill', 'bi-snow', 'bi-snow2', 'bi-tornado',
    'bi-tree', 'bi-tree-fill', 'bi-flower1', 'bi-flower2', 'bi-flower3',
    // Médical & Santé
    'bi-heart', 'bi-heart-fill', 'bi-heart-pulse', 'bi-heart-pulse-fill', 'bi-activity',
    'bi-hospital', 'bi-hospital-fill', 'bi-bandaid', 'bi-bandaid-fill', 'bi-capsule', 'bi-capsule-pill',
    'bi-thermometer', 'bi-thermometer-half', 'bi-clipboard2-pulse', 'bi-clipboard2-pulse-fill',
    // Education
    'bi-book', 'bi-book-fill', 'bi-book-half', 'bi-journals', 'bi-journal', 'bi-journal-text',
    'bi-bookmark', 'bi-bookmark-fill', 'bi-bookmarks', 'bi-bookmarks-fill', 'bi-bookshelf',
    'bi-mortarboard', 'bi-mortarboard-fill', 'bi-backpack', 'bi-backpack-fill', 'bi-pen', 'bi-pen-fill',
    'bi-pencil', 'bi-pencil-fill', 'bi-pencil-square', 'bi-brush', 'bi-brush-fill', 'bi-palette', 'bi-palette-fill',
    // Actions
    'bi-check', 'bi-check2', 'bi-check-circle', 'bi-check-circle-fill', 'bi-check-lg', 'bi-check-all',
    'bi-x', 'bi-x-circle', 'bi-x-circle-fill', 'bi-x-lg', 'bi-x-octagon', 'bi-x-octagon-fill',
    'bi-plus', 'bi-plus-circle', 'bi-plus-circle-fill', 'bi-plus-lg', 'bi-plus-square', 'bi-plus-square-fill',
    'bi-dash', 'bi-dash-circle', 'bi-dash-circle-fill', 'bi-dash-lg', 'bi-dash-square', 'bi-dash-square-fill',
    'bi-arrow-up', 'bi-arrow-down', 'bi-arrow-left', 'bi-arrow-right', 'bi-arrow-clockwise', 'bi-arrow-counterclockwise',
    'bi-arrow-repeat', 'bi-arrows-expand', 'bi-arrows-fullscreen', 'bi-arrows-move',
    // Divers utiles
    'bi-box', 'bi-box-fill', 'bi-box-seam', 'bi-box-seam-fill', 'bi-boxes', 'bi-archive', 'bi-archive-fill',
    'bi-inbox', 'bi-inbox-fill', 'bi-send', 'bi-send-fill', 'bi-reply', 'bi-reply-fill',
    'bi-clipboard', 'bi-clipboard-fill', 'bi-clipboard-check', 'bi-clipboard-check-fill',
    'bi-calendar', 'bi-calendar-fill', 'bi-calendar-event', 'bi-calendar-event-fill', 'bi-calendar-check', 'bi-calendar-check-fill',
    'bi-clock', 'bi-clock-fill', 'bi-clock-history', 'bi-stopwatch', 'bi-stopwatch-fill', 'bi-hourglass', 'bi-hourglass-split',
    'bi-alarm', 'bi-alarm-fill', 'bi-watch',
    'bi-calculator', 'bi-calculator-fill', 'bi-percent', 'bi-hash', 'bi-at',
    'bi-qr-code', 'bi-qr-code-scan', 'bi-upc', 'bi-upc-scan', 'bi-barcode',
    'bi-tag', 'bi-tag-fill', 'bi-tags', 'bi-tags-fill', 'bi-bookmark-star', 'bi-bookmark-star-fill',
    'bi-trophy', 'bi-trophy-fill', 'bi-award', 'bi-award-fill', 'bi-patch-check', 'bi-patch-check-fill',
    'bi-flag', 'bi-flag-fill', 'bi-puzzle', 'bi-puzzle-fill', 'bi-lightbulb', 'bi-lightbulb-fill',
    'bi-magic', 'bi-gem', 'bi-gift', 'bi-gift-fill', 'bi-ticket', 'bi-ticket-fill',
    'bi-cup', 'bi-cup-fill', 'bi-cup-hot', 'bi-cup-hot-fill',
    // Robots & Tech avancée
    'bi-robot', 'bi-cpu', 'bi-bricks', 'bi-plugin', 'bi-terminal', 'bi-terminal-fill',
    'bi-code', 'bi-code-slash', 'bi-code-square', 'bi-braces', 'bi-braces-asterisk',
    'bi-bug', 'bi-bug-fill', 'bi-vector-pen', 'bi-bezier', 'bi-bezier2',
    // Social & Partage
    'bi-hand-thumbs-up', 'bi-hand-thumbs-up-fill', 'bi-hand-thumbs-down', 'bi-hand-thumbs-down-fill',
    'bi-emoji-smile', 'bi-emoji-smile-fill', 'bi-emoji-heart-eyes', 'bi-emoji-heart-eyes-fill',
    'bi-emoji-laughing', 'bi-emoji-laughing-fill', 'bi-emoji-neutral', 'bi-emoji-neutral-fill',
    // Formes
    'bi-circle', 'bi-circle-fill', 'bi-square', 'bi-square-fill', 'bi-triangle', 'bi-triangle-fill',
    'bi-diamond', 'bi-diamond-fill', 'bi-hexagon', 'bi-hexagon-fill', 'bi-pentagon', 'bi-pentagon-fill',
    'bi-octagon', 'bi-octagon-fill', 'bi-heart', 'bi-heart-fill', 'bi-suit-heart', 'bi-suit-heart-fill'
];

let iconPickerVisible = false;

function toggleIconPicker() {
    const container = document.getElementById('iconPickerContainer');
    iconPickerVisible = !iconPickerVisible;
    
    if (iconPickerVisible) {
        container.classList.remove('d-none');
        renderIconPicker();
        document.getElementById('iconSearchInput').focus();
    } else {
        container.classList.add('d-none');
    }
}

function hideIconPicker() {
    iconPickerVisible = false;
    const container = document.getElementById('iconPickerContainer');
    if (container) {
        container.classList.add('d-none');
    }
}

function renderIconPicker() {
    const grid = document.getElementById('iconPickerGrid');
    const currentIcon = document.getElementById('appIcon').value;
    
    grid.innerHTML = BOOTSTRAP_ICONS.map(icon => `
        <div class="icon-picker-item ${icon === currentIcon ? 'selected' : ''}" 
             data-icon="${icon}" 
             onclick="selectIcon('${icon}')"
             title="${icon}">
            <i class="bi ${icon}"></i>
        </div>
    `).join('');
}

function selectIcon(iconName) {
    document.getElementById('appIcon').value = iconName;
    updateIconPreview();
    
    // Fermer le picker après sélection
    toggleIconPicker();
}

function updateIconPickerSelection() {
    const currentIcon = document.getElementById('appIcon').value;
    const items = document.querySelectorAll('.icon-picker-item');
    
    items.forEach(item => {
        if (item.dataset.icon === currentIcon) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
}

function filterIcons() {
    const searchTerm = document.getElementById('iconSearchInput').value.toLowerCase();
    const items = document.querySelectorAll('.icon-picker-item');
    
    items.forEach(item => {
        const iconName = item.dataset.icon.toLowerCase();
        if (iconName.includes(searchTerm)) {
            item.classList.remove('hidden');
        } else {
            item.classList.add('hidden');
        }
    });
}

// Fermer le picker si on clique en dehors
document.addEventListener('click', (e) => {
    const picker = document.getElementById('iconPickerContainer');
    const toggleBtn = e.target.closest('[onclick="toggleIconPicker()"]');
    const pickerContainer = e.target.closest('#iconPickerContainer');
    
    if (iconPickerVisible && !toggleBtn && !pickerContainer) {
        iconPickerVisible = false;
        picker?.classList.add('d-none');
    }
});

// ===== User CRUD =====

function showAddUserForm() {
    document.getElementById('userFormTitle').textContent = 'Nouvel Utilisateur';
    document.getElementById('userForm').reset();
    document.getElementById('userId').value = '';
    document.getElementById('userUsername').disabled = false;
    document.getElementById('userPassword').required = true;
    document.getElementById('passwordHint').classList.add('d-none');
    document.getElementById('userActive').checked = true;
    document.getElementById('userAdmin').checked = false;
    const mustChangePwdEl = document.getElementById('userMustChangePassword');
    if (mustChangePwdEl) mustChangePwdEl.checked = false;
    loadUserAppsCheckboxes([]);
    userFormModal.show();
}

async function editUser(id) {
    const users = await api.getUsers();
    const user = users.find(u => u.id === id);
    if (!user) return;

    document.getElementById('userFormTitle').textContent = 'Modifier l\'Utilisateur';
    document.getElementById('userId').value = user.id;
    document.getElementById('userUsername').value = user.username;
    document.getElementById('userUsername').disabled = true;
    document.getElementById('userEmail').value = user.email || '';
    document.getElementById('userPassword').value = '';
    document.getElementById('userPassword').required = false;
    document.getElementById('passwordHint').classList.remove('d-none');
    document.getElementById('userActive').checked = user.is_active;
    document.getElementById('userAdmin').checked = user.is_admin;
    
    // Charger must_change_password
    const mustChangePwdEl = document.getElementById('userMustChangePassword');
    if (mustChangePwdEl) {
        mustChangePwdEl.checked = user.must_change_password || false;
    }
    
    // Charger les apps avec leurs rôles
    const userAppRoles = user.app_roles || [];
    loadUserAppsWithRoles(userAppRoles);
    
    userFormModal.show();
}

// Rôles disponibles
const AVAILABLE_ROLES = ['admin', 'manager', 'user', 'viewer'];

function loadUserAppsWithRoles(userAppRoles) {
    const container = document.getElementById('userAppsCheckboxes');
    
    if (allApps.length === 0) {
        container.innerHTML = '<small class="text-muted">Aucune application disponible</small>';
        return;
    }

    // Créer un map des rôles et sources par app_id
    const rolesByAppId = {};
    const sourceByAppId = {};
    userAppRoles.forEach(ar => {
        rolesByAppId[ar.app_id] = ar.role;
        sourceByAppId[ar.app_id] = ar.source || 'manual';
    });

    container.innerHTML = allApps.map(app => {
        const currentRole = rolesByAppId[app.id] || '';
        const source = sourceByAppId[app.id] || 'manual';
        const isChecked = currentRole !== '';
        const isFromProfile = source.startsWith('profile');
        
        return `
            <div class="d-flex align-items-center mb-2 app-role-row">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input app-checkbox" type="checkbox" value="${app.id}" 
                        id="userApp${app.id}" ${isChecked ? 'checked' : ''}
                        ${isFromProfile ? 'disabled' : ''}
                        data-source="${source}"
                        onchange="toggleRoleSelect(${app.id})">
                    <label class="form-check-label" for="userApp${app.id}">
                        <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                        ${escapeHtml(app.name)}
                        ${isFromProfile ? '<i class="bi bi-person-badge text-info ms-1" title="Géré par le profil"></i>' : ''}
                    </label>
                </div>
                <select class="form-select form-select-sm role-select" id="userAppRole${app.id}" 
                    style="width: 120px;" ${isChecked && !isFromProfile ? '' : 'disabled'}
                    data-source="${source}">
                    <option value="">-- Rôle --</option>
                    ${AVAILABLE_ROLES.map(role => `
                        <option value="${role}" ${currentRole === role ? 'selected' : ''}>
                            ${role.charAt(0).toUpperCase() + role.slice(1)}
                        </option>
                    `).join('')}
                </select>
                ${isFromProfile ? '<small class="text-info ms-2"><i class="bi bi-lock"></i> Profil</small>' : ''}
            </div>
        `;
    }).join('');
}

function toggleRoleSelect(appId) {
    const checkbox = document.getElementById(`userApp${appId}`);
    const roleSelect = document.getElementById(`userAppRole${appId}`);
    
    if (checkbox.checked) {
        roleSelect.disabled = false;
        if (!roleSelect.value) {
            roleSelect.value = 'user';  // Rôle par défaut
        }
    } else {
        roleSelect.disabled = true;
        roleSelect.value = '';
    }
}

// Ancienne fonction pour compatibilité (utilisée dans showAddUserForm)
function loadUserAppsCheckboxes(selectedIds) {
    // Convertir en format app_roles
    const appRoles = selectedIds.map(id => ({ app_id: id, role: 'user' }));
    loadUserAppsWithRoles(appRoles);
}

async function handleSaveUser(event) {
    event.preventDefault();
    
    const id = document.getElementById('userId').value;
    const isCreating = !id;
    
    const data = {
        username: document.getElementById('userUsername').value,
        email: document.getElementById('userEmail').value || null,
        is_admin: document.getElementById('userAdmin').checked,
        must_change_password: document.getElementById('userMustChangePassword')?.checked || false,
        can_change_password: document.getElementById('userCanChangePassword')?.checked ?? true
    };
    
    // Profils (checkboxes)
    const selectedProfileIds = Array.from(document.querySelectorAll('.user-profile-cb:checked')).map(cb => parseInt(cb.value));
    data.profile_ids = selectedProfileIds.length > 0 ? selectedProfileIds : [];
    
    // is_active n'est pas dans le schéma UserCreate, uniquement pour la mise à jour
    if (!isCreating) {
        data.is_active = document.getElementById('userActive').checked;
    }

    const password = document.getElementById('userPassword').value;
    if (password) {
        data.password = password;
    }

    // Récupérer les apps et leurs rôles (uniquement les manuels, pas ceux du profil)
    const assignments = [];
    allApps.forEach(app => {
        const checkbox = document.getElementById(`userApp${app.id}`);
        const roleSelect = document.getElementById(`userAppRole${app.id}`);
        
        // Ignorer les apps gérées par le profil (source='profile')
        if ((checkbox?.dataset?.source || '').startsWith('profile')) return;
        
        if (checkbox?.checked && roleSelect?.value) {
            assignments.push({
                app_id: app.id,
                role: roleSelect.value
            });
        }
    });

    try {
        let userId = id;
        if (id) {
            await api.updateUser(id, data);
        } else {
            // Ne pas envoyer profile_ids dans la création (pas dans le schéma UserCreate)
            const { profile_ids, ...createData } = data;
            const newUser = await api.createUser(createData);
            userId = newUser.id;
            // Mettre à jour les profils après création si nécessaire
            if (profile_ids && profile_ids.length > 0) {
                await api.updateUser(userId, { profile_ids });
            }
        }
        
        // Mettre à jour les accès et rôles
        await api.setUserApps(userId, assignments);
        
        showToast('Succès', id ? 'Utilisateur modifié' : 'Utilisateur créé', 'success');
        userFormModal.hide();
        
        // Recharger la page appropriée
        if (currentPage === 'users') {
            allProfiles = []; // Forcer le rechargement des profils (les user_count ont changé)
            await loadUsersPageData();
            loadProfilesTab(); // Mettre à jour les compteurs de profils
        }
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteUser(id, username) {
    if (!confirm(`Supprimer l'utilisateur "${username}" ?`)) return;
    
    try {
        await api.deleteUser(id);
        showToast('Succès', 'Utilisateur supprimé', 'success');
        await loadAdminData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== Utilities =====

function showToast(title, message, type = 'info') {
    const toastEl = document.getElementById('toast');
    const icon = document.getElementById('toastIcon');
    
    // Set icon based on type
    const icons = {
        success: 'bi-check-circle-fill text-success',
        danger: 'bi-exclamation-circle-fill text-danger',
        warning: 'bi-exclamation-triangle-fill text-warning',
        info: 'bi-info-circle-fill text-info'
    };
    
    icon.className = `bi ${icons[type] || icons.info} me-2`;
    document.getElementById('toastTitle').textContent = title;
    document.getElementById('toastBody').textContent = message;
    
    toast.show();
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatAppVersionLabel(version) {
    if (version === null || version === undefined) return '';
    const raw = String(version).trim();
    if (!raw) return '';

    // Uniformise l'affichage pour éviter les doublons de préfixe (ex: "vv1.2.3").
    const normalized = raw.replace(/^[vV]\s*/, '');
    return normalized ? `v${normalized}` : '';
}

// ===== Approbation des Applications =====

let pendingApps = [];
let rejectAppModal = null;

/**
 * Charge et affiche le nombre d'applications en attente (badge)
 */
async function loadPendingAppsCount() {
    try {
        const data = await api.getPendingApps();
        const badge = document.getElementById('pendingAppsBadge');
        if (data.total > 0) {
            badge.textContent = data.total;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
    } catch (error) {
        console.error('Erreur chargement compteur apps en attente:', error);
    }
}

/**
 * Charge et affiche la liste des applications en attente d'approbation
 */
async function loadPendingApps() {
    const table = document.getElementById('pendingAppsTable');
    const alert = document.getElementById('pendingAppsAlert');
    
    table.innerHTML = '<div class="text-center p-4"><span class="spinner-border spinner-border-sm"></span> Chargement...</div>';
    alert.classList.add('d-none');
    
    try {
        const data = await api.getPendingApps();
        pendingApps = data.pending_apps;
        
        // Mettre à jour le badge
        const badge = document.getElementById('pendingAppsBadge');
        if (data.total > 0) {
            badge.textContent = data.total;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
        
        if (pendingApps.length === 0) {
            table.innerHTML = '';
            alert.classList.remove('d-none');
            return;
        }
        
        table.innerHTML = `
            <table class="table table-hover mb-0">
                <thead class="table-light">
                    <tr>
                        <th>Application</th>
                        <th>API URL</th>
                        <th>Date</th>
                        <th class="text-end">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${pendingApps.map(app => `
                        <tr>
                            <td>
                                <div class="d-flex align-items-center">
                                    <i class="${escapeHtml(app.icon)} me-2" style="color: ${escapeHtml(app.color)}"></i>
                                    <div>
                                        <strong>${escapeHtml(app.name)}</strong>
                                        ${app.is_shared_service ? '<i class="bi bi-share text-info ms-1" title="Service partagé"></i>' : ''}
                                        <br><code class="small text-muted">${escapeHtml(app.slug)}</code>
                                    </div>
                                </div>
                            </td>
                            <td><small class="text-muted">${escapeHtml(app.api_url)}</small></td>
                            <td><small class="text-muted">${formatDate(app.created_at)}</small></td>
                            <td class="text-end">
                                <div class="btn-group btn-group-sm">
                                    <button class="btn btn-success" onclick="approveApp('${escapeHtml(app.slug)}')" title="Approuver">
                                        <i class="bi bi-check-lg"></i> Approuver
                                    </button>
                                    <button class="btn btn-outline-danger" onclick="showRejectAppModal('${escapeHtml(app.slug)}', '${escapeHtml(app.name)}')" title="Rejeter">
                                        <i class="bi bi-x-lg"></i>
                                    </button>
                                    <button class="btn btn-outline-secondary" onclick="deletePendingApp('${escapeHtml(app.slug)}')" title="Supprimer">
                                        <i class="bi bi-trash"></i>
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    } catch (error) {
        console.error('Erreur chargement apps en attente:', error);
        table.innerHTML = `<div class="alert alert-danger m-3"><i class="bi bi-exclamation-triangle me-2"></i>Erreur: ${error.message}</div>`;
    }
}

/**
 * Approuve une application
 */
async function approveApp(slug) {
    if (!confirm(`Approuver l'application "${slug}" ?\n\nL'application et son Service Account seront activés.`)) {
        return;
    }
    
    try {
        const result = await api.approveApp(slug);
        showToast('Application approuvée', result.message, 'success');
        await loadAdminData();    // Rafraîchir le tableau principal (inclut les pending)
        await loadApps();         // Rafraîchir le dashboard
    } catch (error) {
        console.error('Erreur approbation:', error);
        showToast('Erreur', `Impossible d'approuver: ${error.message}`, 'danger');
    }
}

/**
 * Affiche le modal de rejet avec champ raison
 */
function showRejectAppModal(slug, name) {
    document.getElementById('rejectAppSlug').value = slug;
    document.getElementById('rejectAppName').textContent = name;
    document.getElementById('rejectAppReason').value = '';
    
    if (!rejectAppModal) {
        rejectAppModal = new bootstrap.Modal(document.getElementById('rejectAppModal'));
    }
    rejectAppModal.show();
}

/**
 * Confirme le rejet d'une application
 */
async function confirmRejectApp() {
    const slug = document.getElementById('rejectAppSlug').value;
    const reason = document.getElementById('rejectAppReason').value.trim();
    
    if (!reason) {
        showToast('Erreur', 'Veuillez saisir une raison de rejet', 'warning');
        return;
    }
    
    try {
        const result = await api.rejectApp(slug, reason);
        rejectAppModal.hide();
        showToast('Application rejetée', result.message, 'warning');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur rejet:', error);
        showToast('Erreur', `Impossible de rejeter: ${error.message}`, 'danger');
    }
}

/**
 * Supprime une application en attente ou rejetée
 */
async function deletePendingApp(slug) {
    if (!confirm(`Supprimer définitivement l'application "${slug}" ?\n\nCette action est irréversible.`)) {
        return;
    }
    
    try {
        await api.deletePendingApp(slug);
        showToast('Application supprimée', `L'application "${slug}" a été supprimée`, 'success');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur suppression:', error);
        showToast('Erreur', `Impossible de supprimer: ${error.message}`, 'danger');
    }
}

/**
 * Formate une date ISO en format lisible
 */
function formatDate(isoDate) {
    if (!isoDate) return '-';
    const date = new Date(isoDate);
    return date.toLocaleDateString('fr-FR', { 
        day: '2-digit', 
        month: '2-digit', 
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// ===== Service Accounts CRUD =====

function renderServiceAccountsTable(serviceAccounts) {
    const container = document.getElementById('serviceAccountsTable');
    
    if (serviceAccounts.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4">
                <i class="bi bi-robot display-4 text-muted"></i>
                <p class="text-muted mt-2">Aucun service account configuré</p>
                <p class="small text-muted">Les service accounts permettent aux applications de communiquer entre elles sans intervention utilisateur.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <small class="text-muted">${serviceAccounts.length} service account(s)</small>
            <button class="btn btn-sm btn-outline-secondary" onclick="refreshServiceAccountsStatus()" title="Vérifier les credentials">
                <i class="bi bi-arrow-clockwise me-1"></i>Vérifier credentials
            </button>
        </div>
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Nom</th>
                    <th>Applications</th>
                    <th>Dernière utilisation</th>
                    <th>État</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${serviceAccounts.map(sa => `
                    <tr data-sa-id="${sa.id}">
                        <td>
                            <div>
                                <strong>${escapeHtml(sa.name)}</strong>
                                ${sa.description ? `<br><small class="text-muted">${escapeHtml(sa.description)}</small>` : ''}
                                <br><code class="small text-muted">${sa.client_id}</code>
                            </div>
                        </td>
                        <td>
                            ${sa.applications?.map(a => `<span class="badge bg-light text-dark me-1">${escapeHtml(a.name)}</span>`).join('') || '<span class="text-muted">-</span>'}
                        </td>
                        <td>
                            ${sa.last_used_at 
                                ? `<small class="text-success"><i class="bi bi-check2 me-1"></i>${new Date(sa.last_used_at).toLocaleString('fr-FR')}</small>`
                                : '<small class="text-muted"><i class="bi bi-hourglass me-1"></i>Jamais</small>'}
                        </td>
                        <td>
                            <div class="status-icons">
                                ${sa.is_active 
                                    ? '<i class="bi bi-check-circle-fill text-success" title="Config: Actif"></i>' 
                                    : '<i class="bi bi-x-circle-fill text-danger" title="Config: Inactif"></i>'}
                                <span class="sa-credentials-status" data-sa-id="${sa.id}">
                                    <i class="bi bi-question-circle text-muted" title="Credentials: Non vérifié"></i>
                                </span>
                            </div>
                        </td>
                        <td class="text-end">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-warning" onclick="regenerateServiceAccountSecret(${sa.id}, '${escapeHtml(sa.name)}')" title="Régénérer le secret">
                                    <i class="bi bi-key"></i>
                                </button>
                                <button class="btn btn-outline-primary" onclick="editServiceAccount(${sa.id})" title="Modifier">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-outline-danger" onclick="deleteServiceAccount(${sa.id}, '${escapeHtml(sa.name)}')" title="Supprimer">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
    
    // Lancer automatiquement la vérification des credentials
    refreshServiceAccountsStatus();
}

function showAddServiceAccountForm() {
    document.getElementById('saFormTitle').textContent = 'Nouveau Service Account';
    document.getElementById('serviceAccountForm').reset();
    document.getElementById('saId').value = '';
    document.getElementById('saActive').checked = true;
    loadSaAppsCheckboxes([]);
    serviceAccountFormModal.show();
}

async function editServiceAccount(id) {
    const sa = allServiceAccounts.find(s => s.id === id);
    if (!sa) return;

    document.getElementById('saFormTitle').textContent = 'Modifier le Service Account';
    document.getElementById('saId').value = sa.id;
    document.getElementById('saName').value = sa.name;
    document.getElementById('saDescription').value = sa.description || '';
    document.getElementById('saActive').checked = sa.is_active;
    
    const saAppIds = sa.applications?.map(a => a.id) || [];
    loadSaAppsCheckboxes(saAppIds);
    
    serviceAccountFormModal.show();
}

function loadSaAppsCheckboxes(selectedIds) {
    const container = document.getElementById('saAppsCheckboxes');
    
    if (allApps.length === 0) {
        container.innerHTML = '<small class="text-muted">Aucune application disponible</small>';
        return;
    }

    // Séparer les apps partagées des non-partagées
    const sharedApps = allApps.filter(app => app.is_shared_service);
    const nonSharedApps = allApps.filter(app => !app.is_shared_service);

    let html = '';
    
    // Apps partagées (sélectionnables)
    if (sharedApps.length > 0) {
        html += '<div class="mb-2"><small class="text-success fw-bold"><i class="bi bi-share me-1"></i>Services partagés (accessibles)</small></div>';
        html += sharedApps.map(app => `
            <div class="form-check">
                <input class="form-check-input" type="checkbox" value="${app.id}" 
                    id="saApp${app.id}" ${selectedIds.includes(app.id) ? 'checked' : ''}>
                <label class="form-check-label" for="saApp${app.id}">
                    <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                    ${escapeHtml(app.name)}
                    <span class="badge bg-success ms-1" style="font-size: 0.65em;">partagé</span>
                </label>
            </div>
        `).join('');
    }
    
    // Apps non partagées (grisées, non sélectionnables)
    if (nonSharedApps.length > 0) {
        html += '<div class="mt-3 mb-2"><small class="text-muted fw-bold"><i class="bi bi-lock me-1"></i>Applications privées (non accessibles en M2M)</small></div>';
        html += nonSharedApps.map(app => `
            <div class="form-check" data-bs-toggle="tooltip" data-bs-placement="right" 
                 title="Cette application n'est pas un service partagé. Les Service Accounts ne peuvent accéder qu'aux APIs marquées comme 'partagées'.">
                <input class="form-check-input" type="checkbox" value="${app.id}" 
                    id="saApp${app.id}" disabled 
                    style="opacity: 0.5; cursor: not-allowed;">
                <label class="form-check-label text-muted" for="saApp${app.id}" style="opacity: 0.6;">
                    <i class="${app.icon} me-1" style="color: ${app.color}; opacity: 0.5;"></i>
                    ${escapeHtml(app.name)}
                    <span class="badge bg-secondary ms-1" style="font-size: 0.65em;">privé</span>
                </label>
            </div>
        `).join('');
    }
    
    container.innerHTML = html;
    
    // Disposer les anciens tooltips pour éviter les fuites mémoire
    _activeTooltips.forEach(t => {
        try { t.dispose(); } catch(e) {}
    });
    _activeTooltips = [];
    
    // Initialiser les nouveaux tooltips Bootstrap
    const tooltipTriggerList = container.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(el => {
        _activeTooltips.push(new bootstrap.Tooltip(el));
    });
}

async function handleSaveServiceAccount(event) {
    event.preventDefault();
    
    const id = document.getElementById('saId').value;
    
    // Get selected apps (only shared services can be selected)
    const selectedAppIds = [];
    allApps.filter(app => app.is_shared_service).forEach(app => {
        if (document.getElementById(`saApp${app.id}`)?.checked) {
            selectedAppIds.push(app.id);
        }
    });

    try {
        if (id) {
            // Update existing
            const data = {
                name: document.getElementById('saName').value,
                description: document.getElementById('saDescription').value || null,
                is_active: document.getElementById('saActive').checked
            };
            await api.updateServiceAccount(id, data);
            await api.setServiceAccountApps(id, selectedAppIds);
            showToast('Succès', 'Service Account modifié', 'success');
        } else {
            // Create new
            const data = {
                name: document.getElementById('saName').value,
                description: document.getElementById('saDescription').value || null,
                app_ids: selectedAppIds
            };
            const result = await api.createServiceAccount(data);
            
            // Show credentials modal
            showServiceAccountCredentials(result.client_id, result.client_secret);
            showToast('Succès', 'Service Account créé', 'success');
        }
        
        serviceAccountFormModal.hide();
        await loadAdminData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function showServiceAccountCredentials(clientId, clientSecret, secretsFile = null) {
    document.getElementById('saClientId').value = clientId;
    document.getElementById('saClientSecret').value = clientSecret;
    document.getElementById('exampleClientId').textContent = clientId;
    document.getElementById('exampleClientSecret').textContent = clientSecret;
    
    // Afficher le fichier secrets si disponible
    const secretsFileInfo = document.getElementById('secretsFileInfo');
    if (secretsFileInfo) {
        if (secretsFile) {
            secretsFileInfo.innerHTML = `<div class="alert alert-success mt-3 mb-0">
                <i class="bi bi-check-circle me-2"></i>
                <strong>Sauvegardé :</strong> <code>${secretsFile}</code>
            </div>`;
            secretsFileInfo.classList.remove('d-none');
        } else {
            secretsFileInfo.classList.add('d-none');
        }
    }
    
    saCredentialsModal.show();
}

async function regenerateServiceAccountSecret(id, name) {
    if (!confirm(`Régénérer le secret pour "${name}" ?\n\n⚠️ L'ancien secret sera immédiatement invalidé !`)) return;
    
    try {
        const result = await api.regenerateServiceAccountSecret(id);
        showServiceAccountCredentials(result.client_id, result.client_secret);
        showToast('Succès', 'Secret régénéré', 'success');
        await loadAdminData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteServiceAccount(id, name) {
    if (!confirm(`Supprimer le service account "${name}" ?\n\nLes applications utilisant ce compte ne pourront plus s'authentifier.`)) return;
    
    try {
        await api.deleteServiceAccount(id);
        showToast('Succès', 'Service Account supprimé', 'success');
        await loadAdminData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function copyToClipboard(elementId) {
    const input = document.getElementById(elementId);
    input.select();
    document.execCommand('copy');
    showToast('Copié', 'Valeur copiée dans le presse-papier', 'success');
}

// ===== Audit & Sessions =====

// Variables globales pour l'audit
let auditCurrentAppSlug = null;
let auditLogsPage = 1;
let auditAppLogsPage = 1;

async function loadAuditData() {
    // Charger la vue d'ensemble par défaut
    await loadAuditOverview();
}

async function loadAuditOverview() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        // Charger toutes les données en parallèle
        const [stats, activeSessions, history, appSummary] = await Promise.all([
            api.getConnectionStats(days),
            api.getActiveSessions(),
            api.getSessionHistory(days),
            api.getAppAuditSummary(days).catch(() => ({ apps: [] }))
        ]);
        
        renderAuditStats(stats);
        renderActiveSessions(activeSessions);
        renderTopUsers(stats.top_users || []);
        renderTopApps(appSummary.apps || []);
    } catch (error) {
        console.error('Erreur chargement audit:', error);
        showToast('Erreur', 'Impossible de charger les données d\'audit', 'danger');
    }
}

async function loadAuditApps() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const summary = await api.getAppAuditSummary(days);
        renderAuditAppsGrid(summary.apps || []);
    } catch (error) {
        console.error('Erreur chargement apps audit:', error);
        showToast('Erreur', 'Impossible de charger les données', 'danger');
    }
}

async function loadAuditLogsTab() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    const action = document.getElementById('logsFilterAction')?.value || null;
    const target = document.getElementById('logsFilterApp')?.value || null;
    const userId = document.getElementById('logsFilterUser')?.value || null;
    
    try {
        // Charger les filtres si pas encore fait
        await loadLogsFilters();
        
        const data = await api.getAuditLogs({
            days,
            action: action || null,
            target: target || null,
            userId: userId ? parseInt(userId) : null,
            page: auditLogsPage,
            pageSize: 50
        });
        
        renderAuditLogs(data);
    } catch (error) {
        console.error('Erreur chargement logs:', error);
        showToast('Erreur', 'Impossible de charger les logs', 'danger');
    }
}

async function loadLogsFilters() {
    const actionSelect = document.getElementById('logsFilterAction');
    const appSelect = document.getElementById('logsFilterApp');
    
    // Ne recharger que si vide
    if (actionSelect.options.length <= 1) {
        try {
            const actions = await api.getAvailableActions();
            actions.actions.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.action;
                opt.textContent = `${getActionLabel(a.action)} (${a.count})`;
                actionSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les actions:', e);
        }
    }
    
    if (appSelect.options.length <= 1) {
        try {
            // Utiliser allApps si disponible, sinon charger
            const apps = allApps.length > 0 ? allApps : await api.getAllApps();
            apps.forEach(app => {
                const opt = document.createElement('option');
                opt.value = app.slug;
                opt.textContent = app.name;
                appSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les apps:', e);
        }
    }
}

function resetLogsFilters() {
    document.getElementById('logsFilterAction').value = '';
    document.getElementById('logsFilterApp').value = '';
    document.getElementById('logsFilterUser').value = '';
    auditLogsPage = 1;
    loadAuditLogsTab();
}

function getActionLabel(action) {
    const labels = {
        'access_app': 'Accès application',
        'login': 'Connexion',
        'logout': 'Déconnexion',
        'service_account_login': 'Connexion M2M',
        'create_user': 'Création utilisateur',
        'delete_user': 'Suppression utilisateur',
        'create_app': 'Création app',
        'delete_app': 'Suppression app'
    };
    return labels[action] || action;
}

function renderAuditStats(stats) {
    document.getElementById('statActiveNow').textContent = stats.active_now || 0;
    document.getElementById('statTotalSessions').textContent = stats.total_sessions || 0;
    document.getElementById('statUniqueUsers').textContent = stats.unique_users || 0;
    document.getElementById('statAvgDuration').textContent = stats.avg_session_duration_display || 'N/A';
}

function renderActiveSessions(data) {
    const container = document.getElementById('activeSessionsTable');
    const countBadge = document.getElementById('activeSessionCount');
    
    countBadge.textContent = data.active_count || 0;
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-person-x display-6"></i>
                <p class="mt-2">Aucun utilisateur connecté</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Application active</th>
                    <th>Connecté depuis</th>
                    <th>Inactivité</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td>
                            <span class="badge bg-success me-2"><i class="bi bi-circle-fill" style="font-size: 0.5rem;"></i></span>
                            <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                        </td>
                        <td>
                            ${s.current_app_name 
                                ? `<span class="badge bg-primary"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                : '<span class="text-muted small">Hub</span>'}
                        </td>
                        <td><small>${formatDateTime(s.login_at)}</small> <span class="badge bg-light text-dark">${s.duration_minutes} min</span></td>
                        <td>
                            ${s.idle_minutes > 5 
                                ? `<span class="badge bg-warning text-dark" title="Inactif depuis ${s.idle_minutes} min"><i class="bi bi-hourglass-split me-1"></i>${s.idle_minutes} min</span>`
                                : '<span class="badge bg-success"><i class="bi bi-activity me-1"></i>Actif</span>'}
                        </td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderTopUsers(topUsers) {
    const container = document.getElementById('topUsersTable');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-trophy display-6"></i>
                <p class="mt-2">Aucune donnée disponible</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Utilisateur</th>
                    <th>Sessions</th>
                    <th>Temps total</th>
                </tr>
            </thead>
            <tbody>
                ${topUsers.map((u, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning' : index === 1 ? 'bg-secondary' : 'bg-danger'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td><span class="badge bg-primary">${u.sessions}</span></td>
                        <td>${u.total_time_display}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function formatDateTime(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function getLogoutReasonBadge(reason) {
    if (!reason) return '<span class="badge bg-secondary">Actif</span>';
    
    const badges = {
        'explicit': '<span class="badge bg-light text-dark"><i class="bi bi-box-arrow-right me-1"></i>Déconnexion</span>',
        'timeout': '<span class="badge bg-warning text-dark"><i class="bi bi-hourglass-bottom me-1"></i>Timeout</span>',
        'token_revoked': '<span class="badge bg-danger"><i class="bi bi-x-circle me-1"></i>Révoqué</span>',
        'forced': '<span class="badge bg-danger"><i class="bi bi-exclamation-triangle me-1"></i>Forcé</span>',
        'browser_closed': '<span class="badge bg-info text-dark"><i class="bi bi-window-x me-1"></i>Navigateur fermé</span>',
        'sse_disconnect': '<span class="badge bg-secondary"><i class="bi bi-wifi-off me-1"></i>Connexion perdue</span>'
    };
    
    return badges[reason] || `<span class="badge bg-secondary">${reason}</span>`;
}

// ===== Rendus supplémentaires pour Audit étendu =====

function renderTopApps(apps) {
    const container = document.getElementById('topAppsTable');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-grid display-6"></i>
                <p class="mt-2">Aucun accès enregistré</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Application</th>
                    <th>Accès</th>
                    <th>Utilisateurs uniques</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${apps.map((app, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning' : index === 1 ? 'bg-secondary' : 'bg-danger'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td>
                            <span class="me-2 rounded px-2 py-1" style="background-color: ${app.color}; color: white;">
                                <i class="${app.icon}"></i>
                            </span>
                            <strong>${escapeHtml(app.name)}</strong>
                            <small class="text-muted ms-1">(${app.slug})</small>
                        </td>
                        <td><span class="badge bg-primary">${app.access_count}</span></td>
                        <td><span class="badge bg-info">${app.unique_users}</span></td>
                        <td>
                            <button class="btn btn-sm btn-outline-primary" onclick="showAppAuditDetail('${app.slug}')">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderAuditAppsGrid(apps) {
    const container = document.getElementById('auditAppsGrid');
    document.getElementById('auditAppDetail').classList.add('d-none');
    container.classList.remove('d-none');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="bi bi-grid display-1"></i>
                <h4 class="mt-3">Aucun accès enregistré</h4>
                <p>Les accès aux applications apparaîtront ici</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = apps.map(app => `
        <div class="col-sm-6 col-md-4 col-lg-3 mb-3">
            <div class="card h-100 cursor-pointer" onclick="showAppAuditDetail('${app.slug}')" style="cursor: pointer;">
                <div class="card-body text-center">
                    <div class="mb-2">
                        <span class="rounded-circle d-inline-flex align-items-center justify-content-center" 
                              style="width: 60px; height: 60px; background-color: ${app.color}; color: white; font-size: 1.5rem;">
                            <i class="${app.icon}"></i>
                        </span>
                    </div>
                    <h6 class="card-title mb-1">${escapeHtml(app.name)}</h6>
                    <p class="card-text small text-muted mb-2">${app.slug}</p>
                    <div class="d-flex justify-content-center gap-3">
                        <div class="text-center">
                            <div class="h5 mb-0 text-primary">${app.access_count}</div>
                            <small class="text-muted">Accès</small>
                        </div>
                        <div class="text-center">
                            <div class="h5 mb-0 text-info">${app.unique_users}</div>
                            <small class="text-muted">Users</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

async function showAppAuditDetail(appSlug) {
    auditCurrentAppSlug = appSlug;
    auditAppLogsPage = 1;
    
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(appSlug, { days, page: 1, pageSize: 50 });
        
        // Masquer la grille, afficher le détail
        document.getElementById('auditAppsGrid').classList.add('d-none');
        document.getElementById('auditAppDetail').classList.remove('d-none');
        
        // Remplir les infos de l'app
        document.getElementById('appDetailIcon').innerHTML = `<i class="${data.app.icon}" style="color: ${data.app.color};"></i>`;
        document.getElementById('appDetailIcon').style.backgroundColor = `${data.app.color}20`;
        document.getElementById('appDetailName').textContent = data.app.name;
        document.getElementById('appDetailSlug').textContent = data.app.slug;
        
        // Stats
        document.getElementById('appDetailAccess').textContent = data.stats.total_access;
        document.getElementById('appDetailUsers').textContent = data.stats.unique_users;
        document.getElementById('appDetailPeriod').textContent = `${days} jours`;
        
        // Top users
        renderAppDetailTopUsers(data.top_users);
        
        // Logs
        renderAppDetailLogs(data);
        
    } catch (error) {
        console.error('Erreur détail app:', error);
        showToast('Erreur', 'Impossible de charger les détails', 'danger');
    }
}

function hideAppDetail() {
    document.getElementById('auditAppDetail').classList.add('d-none');
    document.getElementById('auditAppsGrid').classList.remove('d-none');
    auditCurrentAppSlug = null;
}

function renderAppDetailTopUsers(topUsers) {
    const container = document.getElementById('appDetailTopUsers');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-3">Aucune donnée</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm mb-0">
            <tbody>
                ${topUsers.map((u, i) => `
                    <tr>
                        <td class="text-center" style="width: 30px;">
                            ${i < 3 
                                ? `<span class="badge ${i === 0 ? 'bg-warning' : i === 1 ? 'bg-secondary' : 'bg-danger'}">${i + 1}</span>`
                                : `<span class="text-muted">${i + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td class="text-end"><span class="badge bg-primary">${u.access_count}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderAppDetailLogs(data) {
    const container = document.getElementById('appDetailLogs');
    const pagination = document.getElementById('appDetailPagination');
    
    // Pagination
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'loadAppDetailPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun accès enregistré</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function loadAppDetailPage(page) {
    if (!auditCurrentAppSlug) return;
    auditAppLogsPage = page;
    
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(auditCurrentAppSlug, { days, page, pageSize: 50 });
        renderAppDetailLogs(data);
    } catch (error) {
        console.error('Erreur pagination:', error);
    }
}

function renderAuditLogs(data) {
    const container = document.getElementById('auditLogsTable');
    const countSpan = document.getElementById('logsCount');
    const pagination = document.getElementById('logsPagination');
    
    countSpan.textContent = `${data.total_count} résultats`;
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'changeLogsPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-list-ul display-4"></i>
                <p class="mt-2">Aucun log trouvé</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>Action</th>
                    <th>Cible</th>
                    <th>Détails</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td>${getActionBadge(l.action)}</td>
                        <td><code class="small">${l.target || '-'}</code></td>
                        <td><small class="text-muted">${escapeHtml(l.details || '-')}</small></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function getActionBadge(action) {
    const badges = {
        'access_app': '<span class="badge bg-secondary">Accès App</span>',
        'login': '<span class="badge bg-secondary">Connexion</span>',
        'logout': '<span class="badge bg-light text-dark">Déconnexion</span>',
        'service_account_login': '<span class="badge bg-dark">M2M</span>'
    };
    return badges[action] || `<span class="badge bg-light text-dark">${action}</span>`;
}

async function changeLogsPage(page) {
    auditLogsPage = page;
    await loadAuditLogsTab();
}

function renderPagination(currentPage, totalPages, functionName) {
    if (totalPages <= 1) return '';
    
    let html = '<nav><ul class="pagination pagination-sm mb-0">';
    
    // Précédent
    html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="${functionName}(${currentPage - 1}); return false;">&laquo;</a>
    </li>`;
    
    // Pages
    const maxPages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxPages / 2));
    let endPage = Math.min(totalPages, startPage + maxPages - 1);
    
    if (endPage - startPage < maxPages - 1) {
        startPage = Math.max(1, endPage - maxPages + 1);
    }
    
    if (startPage > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" onclick="${functionName}(1); return false;">1</a></li>`;
        if (startPage > 2) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="${functionName}(${i}); return false;">${i}</a>
        </li>`;
    }
    
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        html += `<li class="page-item"><a class="page-link" href="#" onclick="${functionName}(${totalPages}); return false;">${totalPages}</a></li>`;
    }
    
    // Suivant
    html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="${functionName}(${currentPage + 1}); return false;">&raquo;</a>
    </li>`;
    
    html += '</ul></nav>';
    return html;
}

// ===== Export CSV =====

async function exportAuditLogs() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    const action = document.getElementById('logsFilterAction')?.value || null;
    const target = document.getElementById('logsFilterApp')?.value || null;
    
    try {
        showToast('Export', 'Génération du fichier CSV...', 'info');
        await api.exportAuditLogs({ days, action, target });
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showToast('Erreur', 'Impossible d\'exporter les données', 'danger');
    }
}

async function exportSessions() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        showToast('Export', 'Génération du fichier CSV...', 'info');
        await api.exportSessions(days);
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showToast('Erreur', 'Impossible d\'exporter les données', 'danger');
    }
}

// ===== Docker Scanner =====

let dockerScanModal;
let lastDockerScan = null;
let dockerSelection = { api: null, frontend: null };  // Legacy (unused)
let dockerGroupSelection = {};  // { base_slug: true } pour la sélection groupée

/**
 * Affiche la modal de scan Docker et lance le scan
 */
async function showDockerScanModal() {
    if (!dockerScanModal) {
        dockerScanModal = new bootstrap.Modal(document.getElementById('dockerScanModal'));
    }
    
    // Reset UI et sélection
    dockerSelection = { api: null, frontend: null };
    document.getElementById('dockerStatus').classList.remove('d-none');
    document.getElementById('dockerError').classList.add('d-none');
    document.getElementById('dockerInfo').classList.add('d-none');
    document.getElementById('dockerContainers').classList.add('d-none');
    document.getElementById('dockerEmpty').classList.add('d-none');
    document.getElementById('dockerIncludeStopped').checked = false;
    document.getElementById('dockerShowAll').checked = false;
    updateDockerSelectionSummary();
    
    dockerScanModal.show();
    
    // Lancer le scan
    await performDockerScan();
}

/**
 * Effectue le scan Docker
 */
async function performDockerScan() {
    const includeStopped = document.getElementById('dockerIncludeStopped').checked;
    const showAll = document.getElementById('dockerShowAll').checked;
    
    // Reset sélection groupée
    dockerGroupSelection = {};
    updateDockerSelectionSummary();
    
    // Show loading
    document.getElementById('dockerStatus').classList.remove('d-none');
    document.getElementById('dockerStatus').innerHTML = `
        <div class="d-flex align-items-center text-muted">
            <div class="spinner-border spinner-border-sm me-2" role="status"></div>
            Analyse des conteneurs Docker...
        </div>
    `;
    document.getElementById('dockerError').classList.add('d-none');
    document.getElementById('dockerInfo').classList.add('d-none');
    document.getElementById('dockerContainers').classList.add('d-none');
    document.getElementById('dockerEmpty').classList.add('d-none');
    document.getElementById('dockerImportResults').classList.add('d-none');
    
    try {
        const result = await api.scanDockerContainers(includeStopped, showAll);
        lastDockerScan = result;
        
        document.getElementById('dockerStatus').classList.add('d-none');
        
        if (!result.available) {
            document.getElementById('dockerError').classList.remove('d-none');
            document.getElementById('dockerErrorMessage').textContent = 
                result.error || 'Docker n\'est pas accessible depuis le Hub';
            return;
        }
        
        // Afficher les infos Docker
        document.getElementById('dockerInfo').classList.remove('d-none');
        document.getElementById('dockerVersion').textContent = result.docker_version || 'N/A';
        document.getElementById('dockerRunning').textContent = result.containers_running || 0;
        document.getElementById('dockerTotal').textContent = result.containers_total || 0;
        
        // Afficher les groupes d'applications détectés
        if (result.groups && result.groups.length > 0) {
            document.getElementById('dockerContainers').classList.remove('d-none');
            const importableGroups = result.groups.filter(g => !g.is_registered);
            document.getElementById('dockerGroupCount').textContent = `${result.groups.length} application(s) détectée(s)${importableGroups.length < result.groups.length ? ` (${importableGroups.length} importable(s))` : ''}`;
            renderDockerGroups(result.groups);
        } else {
            document.getElementById('dockerEmpty').classList.remove('d-none');
        }
        
    } catch (error) {
        console.error('Erreur scan Docker:', error);
        document.getElementById('dockerStatus').classList.add('d-none');
        document.getElementById('dockerError').classList.remove('d-none');
        document.getElementById('dockerErrorMessage').textContent = error.message;
    }
}

/**
 * Rafraîchit le scan Docker
 */
function refreshDockerScan() {
    performDockerScan();
}

/**
 * Affiche les groupes d'applications détectés par le scanner Docker
 */
function renderDockerGroups(groups) {
    const list = document.getElementById('containersList');
    
    list.innerHTML = groups.map(group => {
        const isRegistered = group.is_registered;
        const isChecked = dockerGroupSelection[group.base_slug] === true;
        const registeredClass = isRegistered ? 'opacity-50' : '';
        const registeredBadge = isRegistered ? '<span class="badge bg-warning text-dark ms-1"><i class="bi bi-check-circle me-1"></i>Déjà enregistré</span>' : '';
        
        // Conteneur API
        let apiInfo = '';
        if (group.api_container) {
            const c = group.api_container;
            const port = group.api_port || '?';
            const statusClass = c.status === 'running' ? 'success' : 'secondary';
            apiInfo = `
                <div class="d-flex align-items-center mb-1">
                    <span class="badge bg-danger me-2">API</span>
                    <i class="bi bi-circle-fill text-${statusClass} me-1" style="font-size: 0.5rem;"></i>
                    <small class="text-muted">${escapeHtml(c.name)}</small>
                    <span class="badge bg-primary ms-2">:${port}</span>
                    <small class="text-muted ms-2">${escapeHtml(c.image)}</small>
                </div>
            `;
        }
        
        // Conteneur Frontend
        let frontInfo = '';
        if (group.frontend_container) {
            const c = group.frontend_container;
            const port = group.frontend_port || '?';
            const statusClass = c.status === 'running' ? 'success' : 'secondary';
            frontInfo = `
                <div class="d-flex align-items-center mb-1">
                    <span class="badge bg-success me-2">Front</span>
                    <i class="bi bi-circle-fill text-${statusClass} me-1" style="font-size: 0.5rem;"></i>
                    <small class="text-muted">${escapeHtml(c.name)}</small>
                    <span class="badge bg-primary ms-2">:${port}</span>
                    <small class="text-muted ms-2">${escapeHtml(c.image)}</small>
                </div>
            `;
        }
        
        // Conteneurs non groupés
        let ungroupedInfo = '';
        if (group.ungrouped_containers && group.ungrouped_containers.length > 0) {
            ungroupedInfo = group.ungrouped_containers.map(c => {
                const port = c.ports[0]?.host_port || '?';
                return `
                    <div class="d-flex align-items-center mb-1">
                        <span class="badge bg-secondary me-2">?</span>
                        <small class="text-muted">${escapeHtml(c.name)} :${port} (${escapeHtml(c.image)})</small>
                    </div>
                `;
            }).join('');
        }
        
        // Si pas d'API mais des ungrouped, considérer le premier comme API
        const hasApi = !!group.api_container || (group.ungrouped_containers && group.ungrouped_containers.length > 0);
        
        return `
            <div class="card mb-2 docker-group-card ${registeredClass} ${isChecked ? 'border-success border-2' : ''}" data-group-slug="${escapeHtml(group.base_slug)}">
                <div class="card-body py-2 px-3">
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="d-flex align-items-start flex-grow-1">
                            <div class="form-check me-3 mt-1">
                                <input class="form-check-input docker-group-check" type="checkbox" 
                                    id="dockerGroup-${escapeHtml(group.base_slug)}" 
                                    data-group-slug="${escapeHtml(group.base_slug)}"
                                    ${isRegistered ? 'disabled' : ''}
                                    ${isChecked ? 'checked' : ''}
                                    onchange="toggleDockerGroupSelection('${escapeHtml(group.base_slug)}')">
                            </div>
                            <div class="flex-grow-1">
                                <div class="d-flex align-items-center mb-1">
                                    <strong>${escapeHtml(group.suggested_name)}</strong>
                                    <code class="ms-2 small text-muted">${escapeHtml(group.base_slug)}</code>
                                    ${registeredBadge}
                                </div>
                                ${group.description ? `<small class="text-muted d-block mb-1">${escapeHtml(group.description)}</small>` : ''}
                                ${apiInfo}
                                ${frontInfo}
                                ${ungroupedInfo}
                            </div>
                        </div>
                        <div class="text-end">
                            ${!isRegistered ? `
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input docker-shared-check" type="checkbox" 
                                        id="dockerShared-${escapeHtml(group.base_slug)}"
                                        data-group-slug="${escapeHtml(group.base_slug)}"
                                        ${group.is_shared ? 'checked' : ''}>
                                    <label class="form-check-label small" for="dockerShared-${escapeHtml(group.base_slug)}">
                                        <i class="bi bi-share text-primary"></i> M2M
                                    </label>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Sélection/désélection d'un groupe d'application
 */
function toggleDockerGroupSelection(baseSlug) {
    if (dockerGroupSelection[baseSlug]) {
        delete dockerGroupSelection[baseSlug];
    } else {
        dockerGroupSelection[baseSlug] = true;
    }
    updateDockerSelectionSummary();
}

/**
 * Sélection/désélection de tous les groupes importables
 */
function toggleDockerSelectAll() {
    const selectAll = document.getElementById('dockerSelectAll').checked;
    const checkboxes = document.querySelectorAll('.docker-group-check:not(:disabled)');
    
    dockerGroupSelection = {};
    checkboxes.forEach(cb => {
        cb.checked = selectAll;
        if (selectAll) {
            dockerGroupSelection[cb.dataset.groupSlug] = true;
        }
    });
    updateDockerSelectionSummary();
}

/**
 * Met à jour le résumé de la sélection
 */
function updateDockerSelectionSummary() {
    const summary = document.getElementById('dockerSelectionSummary');
    const importBtn = document.getElementById('dockerBatchImportBtn');
    
    if (!summary || !importBtn) return;
    
    const selectedCount = Object.keys(dockerGroupSelection).length;
    
    if (selectedCount === 0) {
        summary.innerHTML = '<span class="text-muted">Aucune sélection</span>';
        importBtn.disabled = true;
    } else {
        const slugs = Object.keys(dockerGroupSelection);
        const maxShow = 3;
        let html = slugs.slice(0, maxShow).map(s => `<span class="badge bg-info me-1">${escapeHtml(s)}</span>`).join('');
        if (slugs.length > maxShow) {
            html += `<span class="text-muted small">+${slugs.length - maxShow} autre(s)</span>`;
        }
        summary.innerHTML = html;
        importBtn.disabled = false;
        importBtn.innerHTML = `<i class="bi bi-lightning-charge me-1"></i>Importer (${selectedCount})`;
    }
}

/**
 * Import lot : enregistre toutes les applications sélectionnées avec leurs SA
 */
async function batchImportDockerSelection() {
    const selectedSlugs = Object.keys(dockerGroupSelection);
    if (selectedSlugs.length === 0) {
        showToast('Erreur', 'Aucune application sélectionnée', 'warning');
        return;
    }
    
    if (!lastDockerScan || !lastDockerScan.groups) return;
    
    // Construire la liste d'apps à importer
    const apps = [];
    for (const slug of selectedSlugs) {
        const group = lastDockerScan.groups.find(g => g.base_slug === slug);
        if (!group || group.is_registered) continue;
        
        // Déterminer le port API
        let apiPort = group.api_port;
        if (!apiPort && group.ungrouped_containers && group.ungrouped_containers.length > 0) {
            apiPort = group.ungrouped_containers[0].ports[0]?.host_port;
        }
        if (!apiPort) {
            showToast('Erreur', `Pas de port API détecté pour "${slug}"`, 'warning');
            continue;
        }
        
        // Check shared
        const sharedCb = document.getElementById(`dockerShared-${slug}`);
        const isShared = sharedCb ? sharedCb.checked : group.is_shared;
        
        apps.push({
            base_slug: slug,
            name: group.suggested_name,
            description: group.description,
            api_port: apiPort,
            frontend_port: group.frontend_port || null,
            is_shared: isShared
        });
    }
    
    if (apps.length === 0) {
        showToast('Info', 'Aucune application importable dans la sélection', 'info');
        return;
    }
    
    // Désactiver le bouton pendant l'opération
    const btn = document.getElementById('dockerBatchImportBtn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Import en cours...';
    
    try {
        const result = await api.batchImportApps(apps);
        
        // Afficher les résultats dans le modal (pas de second modal)
        const resultsDiv = document.getElementById('dockerImportResults');
        const resultsList = document.getElementById('dockerImportResultsList');
        resultsDiv.classList.remove('d-none');
        
        resultsList.innerHTML = `
            <div class="alert ${result.errors > 0 ? 'alert-warning' : 'alert-success'} py-2 mb-2">
                <strong>${result.created}</strong> créée(s), <strong>${result.updated}</strong> mise(s) à jour${result.errors > 0 ? `, <strong>${result.errors}</strong> erreur(s)` : ''}
            </div>
            ${result.results.map(r => `
                <div class="d-flex align-items-center py-1 border-bottom">
                    <i class="bi ${r.success ? (r.is_new ? 'bi-check-circle-fill text-success' : 'bi-arrow-clockwise text-info') : 'bi-x-circle-fill text-danger'} me-2"></i>
                    <div class="flex-grow-1">
                        <strong>${escapeHtml(r.name)}</strong> <code class="small">${escapeHtml(r.slug)}</code>
                        <br><small class="text-muted">${escapeHtml(r.message)}</small>
                    </div>
                    ${r.credentials?.client_secret ? `
                        <div class="text-end">
                            <button class="btn btn-sm btn-outline-dark" onclick="copyToClipboard('${escapeHtml(r.credentials.client_id)}\\n${escapeHtml(r.credentials.client_secret)}')" title="Copier credentials">
                                <i class="bi bi-clipboard me-1"></i>Credentials
                            </button>
                        </div>
                    ` : ''}
                </div>
            `).join('')}
        `;
        
        // Reset sélection
        dockerGroupSelection = {};
        updateDockerSelectionSummary();
        
        // Rafraîchir les données admin et le scan
        await loadAdminData();
        loadApps();
        
        // Relancer le scan pour actualiser la liste
        await performDockerScan();
        
        showToast('Import terminé', `${result.created} application(s) importée(s)`, 'success');
        
    } catch (error) {
        console.error('Erreur import lot:', error);
        showToast('Erreur', error.message, 'danger');
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = Object.keys(dockerGroupSelection).length === 0;
    }
}

/**
 * Copie du texte dans le presse-papier
 */
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Copié', 'Credentials copiés dans le presse-papier', 'success');
    }).catch(() => {
        // Fallback
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Copié', 'Credentials copiés', 'success');
    });
}

// ===== Page Audit Dédiée =====

let pageAuditLogsPage = 1;
let pageAuditCurrentAppSlug = null;
let pageAuditAppLogsPage = 1;
let allUsersPage = [];

async function loadAuditPageData() {
    await loadAuditPageOverview();
}

async function loadAuditPageOverview() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const [stats, activeSessions, history, appSummary] = await Promise.all([
            api.getConnectionStats(days),
            api.getActiveSessions(),
            api.getSessionHistory(days),
            api.getAppAuditSummary(days).catch(() => ({ apps: [] }))
        ]);
        
        // Stats
        document.getElementById('pageStatActiveNow').textContent = stats.active_now || 0;
        document.getElementById('pageStatTotalSessions').textContent = stats.total_sessions || 0;
        document.getElementById('pageStatUniqueUsers').textContent = stats.unique_users || 0;
        document.getElementById('pageStatAvgDuration').textContent = stats.avg_session_duration_display || 'N/A';
        
        // Sessions actives
        renderPageActiveSessions(activeSessions);
        
        // Top users
        renderPageTopUsers(stats.top_users || []);
        
        // Top apps
        renderPageTopApps(appSummary.apps || []);
    } catch (error) {
        console.error('Erreur chargement audit:', error);
        showToast('Erreur', 'Impossible de charger les données d\'audit', 'danger');
    }
}

function renderPageActiveSessions(data) {
    const container = document.getElementById('pageActiveSessionsTable');
    const countBadge = document.getElementById('pageActiveSessionCount');
    
    countBadge.textContent = data.active_count || 0;
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-person-x display-6"></i>
                <p class="mt-2">Aucun utilisateur connecté</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Connecté depuis</th>
                    <th>Durée</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td>
                            <span class="badge bg-secondary me-2"><i class="bi bi-circle-fill text-success" style="font-size: 0.5rem;"></i></span>
                            <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                        </td>
                        <td><small>${formatDateTime(s.login_at)}</small></td>
                        <td><span class="badge bg-light text-dark">${s.duration_minutes} min</span></td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageTopUsers(topUsers) {
    const container = document.getElementById('pageTopUsersTable');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-trophy display-6"></i>
                <p class="mt-2">Aucune donnée disponible</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Utilisateur</th>
                    <th>Sessions</th>
                    <th>Temps total</th>
                </tr>
            </thead>
            <tbody>
                ${topUsers.map((u, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td><span class="badge bg-secondary">${u.sessions}</span></td>
                        <td>${u.total_time_display}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageTopApps(apps) {
    const container = document.getElementById('pageTopAppsTable');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-grid display-6"></i>
                <p class="mt-2">Aucun accès enregistré</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Application</th>
                    <th>Accès</th>
                    <th>Utilisateurs uniques</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${apps.map((app, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td>
                            <span class="me-2 rounded px-2 py-1" style="background-color: ${app.color}; color: white;">
                                <i class="${app.icon}"></i>
                            </span>
                            <strong>${escapeHtml(app.name)}</strong>
                            <small class="text-muted ms-1">(${app.slug})</small>
                        </td>
                        <td><span class="badge bg-secondary">${app.access_count}</span></td>
                        <td><span class="badge bg-light text-dark">${app.unique_users}</span></td>
                        <td>
                            <button class="btn btn-sm btn-outline-secondary" onclick="showPageAppAuditDetail('${app.slug}')">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function loadAuditPageApps() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const summary = await api.getAppAuditSummary(days);
        renderPageAuditAppsGrid(summary.apps || []);
    } catch (error) {
        console.error('Erreur chargement apps audit:', error);
        showToast('Erreur', 'Impossible de charger les données', 'danger');
    }
}

function renderPageAuditAppsGrid(apps) {
    const container = document.getElementById('auditPageAppsGrid');
    document.getElementById('auditPageAppDetail').classList.add('d-none');
    container.classList.remove('d-none');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="bi bi-grid display-1"></i>
                <h4 class="mt-3">Aucun accès enregistré</h4>
                <p>Les accès aux applications apparaîtront ici</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = apps.map(app => `
        <div class="col-sm-6 col-md-4 col-lg-3 mb-3">
            <div class="card h-100 cursor-pointer" onclick="showPageAppAuditDetail('${app.slug}')" style="cursor: pointer;">
                <div class="card-body text-center">
                    <div class="mb-2">
                        <span class="rounded-circle d-inline-flex align-items-center justify-content-center" 
                              style="width: 60px; height: 60px; background-color: ${app.color}; color: white; font-size: 1.5rem;">
                            <i class="${app.icon}"></i>
                        </span>
                    </div>
                    <h6 class="card-title mb-1">${escapeHtml(app.name)}</h6>
                    <p class="card-text small text-muted mb-2">${app.slug}</p>
                    <div class="d-flex justify-content-center gap-3">
                        <div class="text-center">
                            <div class="h5 mb-0">${app.access_count}</div>
                            <small class="text-muted">Accès</small>
                        </div>
                        <div class="text-center">
                            <div class="h5 mb-0">${app.unique_users}</div>
                            <small class="text-muted">Users</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

async function showPageAppAuditDetail(appSlug) {
    pageAuditCurrentAppSlug = appSlug;
    pageAuditAppLogsPage = 1;
    
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    // Basculer vers l'onglet "Par Application" si pas déjà dessus
    const appsTab = document.querySelector('[data-bs-target="#auditPageApps"]');
    if (appsTab && !appsTab.classList.contains('active')) {
        appsTab.click();
    }
    
    try {
        const data = await api.getAppAuditDetail(appSlug, { days, page: 1, pageSize: 50 });
        
        // Masquer la grille, afficher le détail
        document.getElementById('auditPageAppsGrid').classList.add('d-none');
        document.getElementById('auditPageAppDetail').classList.remove('d-none');
        
        // Remplir les infos de l'app
        document.getElementById('pageAppDetailIcon').innerHTML = `<i class="${data.app.icon}" style="color: ${data.app.color};"></i>`;
        document.getElementById('pageAppDetailIcon').style.backgroundColor = `${data.app.color}20`;
        document.getElementById('pageAppDetailName').textContent = data.app.name;
        document.getElementById('pageAppDetailSlug').textContent = data.app.slug;
        
        // Stats
        document.getElementById('pageAppDetailAccess').textContent = data.stats.total_access;
        document.getElementById('pageAppDetailUsers').textContent = data.stats.unique_users;
        document.getElementById('pageAppDetailPeriod').textContent = `${days} jours`;
        
        // Top users
        renderPageAppDetailTopUsers(data.top_users);
        
        // Logs
        renderPageAppDetailLogs(data);
        
    } catch (error) {
        console.error('Erreur détail app:', error);
        showToast('Erreur', 'Impossible de charger les détails', 'danger');
    }
}

function hidePageAppDetail() {
    document.getElementById('auditPageAppDetail').classList.add('d-none');
    document.getElementById('auditPageAppsGrid').classList.remove('d-none');
    pageAuditCurrentAppSlug = null;
}

function renderPageAppDetailTopUsers(topUsers) {
    const container = document.getElementById('pageAppDetailTopUsers');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-3">Aucune donnée</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm mb-0">
            <tbody>
                ${topUsers.map((u, i) => `
                    <tr>
                        <td class="text-center" style="width: 30px;">
                            ${i < 3 
                                ? `<span class="badge ${i === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${i + 1}</span>`
                                : `<span class="text-muted">${i + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td class="text-end"><span class="badge bg-secondary">${u.access_count}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageAppDetailLogs(data) {
    const container = document.getElementById('pageAppDetailLogs');
    const pagination = document.getElementById('pageAppDetailPagination');
    
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'loadPageAppDetailPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun accès enregistré</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function loadPageAppDetailPage(page) {
    if (!pageAuditCurrentAppSlug) return;
    pageAuditAppLogsPage = page;
    
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(pageAuditCurrentAppSlug, { days, page, pageSize: 50 });
        renderPageAppDetailLogs(data);
    } catch (error) {
        console.error('Erreur pagination:', error);
    }
}

async function loadAuditPageLogsTab() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    const action = document.getElementById('pageLogsFilterAction')?.value || null;
    const target = document.getElementById('pageLogsFilterApp')?.value || null;
    const userId = document.getElementById('pageLogsFilterUser')?.value || null;
    
    try {
        await loadPageLogsFilters();
        
        const data = await api.getAuditLogs({
            days,
            action: action || null,
            target: target || null,
            userId: userId ? parseInt(userId) : null,
            page: pageAuditLogsPage,
            pageSize: 50
        });
        
        renderPageAuditLogs(data);
    } catch (error) {
        console.error('Erreur chargement logs:', error);
        showToast('Erreur', 'Impossible de charger les logs', 'danger');
    }
}

async function loadPageLogsFilters() {
    const actionSelect = document.getElementById('pageLogsFilterAction');
    const appSelect = document.getElementById('pageLogsFilterApp');
    
    if (actionSelect.options.length <= 1) {
        try {
            const actions = await api.getAvailableActions();
            actions.actions.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.action;
                opt.textContent = `${getActionLabel(a.action)} (${a.count})`;
                actionSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les actions:', e);
        }
    }
    
    if (appSelect.options.length <= 1) {
        try {
            const apps = allApps.length > 0 ? allApps : await api.getAllApps();
            apps.forEach(app => {
                const opt = document.createElement('option');
                opt.value = app.slug;
                opt.textContent = app.name;
                appSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les apps:', e);
        }
    }
}

function resetPageLogsFilters() {
    document.getElementById('pageLogsFilterAction').value = '';
    document.getElementById('pageLogsFilterApp').value = '';
    document.getElementById('pageLogsFilterUser').value = '';
    pageAuditLogsPage = 1;
    loadAuditPageLogsTab();
}

function renderPageAuditLogs(data) {
    const container = document.getElementById('pageAuditLogsTable');
    const countSpan = document.getElementById('pageLogsCount');
    const pagination = document.getElementById('pageLogsPagination');
    
    countSpan.textContent = `${data.total_count} résultats`;
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'changePageLogsPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-list-ul display-4"></i>
                <p class="mt-2">Aucun log trouvé</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>Action</th>
                    <th>Cible</th>
                    <th>Détails</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td>${getActionBadge(l.action)}</td>
                        <td><code class="small">${l.target || '-'}</code></td>
                        <td><small class="text-muted">${escapeHtml(l.details || '-')}</small></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function changePageLogsPage(page) {
    pageAuditLogsPage = page;
    await loadAuditPageLogsTab();
}

async function loadAuditPageSessions() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const history = await api.getSessionHistory(days);
        renderPageSessionHistory(history);
        document.getElementById('pageSessionsCount').textContent = `${history.total_sessions || 0} sessions`;
    } catch (error) {
        console.error('Erreur chargement sessions:', error);
        showToast('Erreur', 'Impossible de charger les sessions', 'danger');
    }
}

function renderPageSessionHistory(data) {
    const container = document.getElementById('pageSessionHistoryTable');
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-clock-history display-4 mb-3"></i>
                <p class="mb-0">Aucune session dans cette période</p>
                <small class="text-muted">Les sessions apparaîtront ici une fois que des utilisateurs se connecteront</small>
            </div>
        `;
        return;
    }
    
    // Séparer sessions actives et terminées
    const activeSessions = data.sessions.filter(s => !s.logout_at);
    const closedSessions = data.sessions.filter(s => s.logout_at);
    
    container.innerHTML = `
        <!-- Sessions actives -->
        ${activeSessions.length > 0 ? `
        <div class="mb-4 px-3 pt-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-success me-2">${activeSessions.length}</span>
                <h6 class="mb-0 text-success"><i class="bi bi-broadcast me-1"></i>Sessions actives</h6>
            </div>
            <table class="table table-sm table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        <th style="min-width: 150px">Utilisateur</th>
                        <th style="min-width: 120px">Application</th>
                        <th style="min-width: 150px">Connecté depuis</th>
                        <th style="min-width: 100px">Durée</th>
                        <th style="min-width: 80px">Activité</th>
                        <th style="min-width: 120px">IP</th>
                    </tr>
                </thead>
                <tbody>
                    ${activeSessions.map(s => `
                        <tr class="table-success table-active">
                            <td>
                                <div class="d-flex align-items-center">
                                    <span class="position-relative me-2">
                                        <i class="bi bi-person-circle fs-5 text-success"></i>
                                        <span class="position-absolute bottom-0 end-0 translate-middle-x">
                                            <i class="bi bi-circle-fill text-success" style="font-size: 0.5rem;"></i>
                                        </span>
                                    </span>
                                    <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                                </div>
                            </td>
                            <td>
                                ${s.current_app_name 
                                    ? `<span class="badge bg-primary bg-gradient"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                    : '<span class="badge bg-secondary bg-gradient"><i class="bi bi-house me-1"></i>Hub</span>'}
                            </td>
                            <td>
                                <small class="text-muted">${formatDateTime(s.login_at)}</small>
                            </td>
                            <td>
                                <span class="badge bg-light text-dark border">
                                    <i class="bi bi-clock me-1"></i>${s.duration_display || 'en cours'}
                                </span>
                            </td>
                            <td>
                                ${s.idle_minutes !== undefined && s.idle_minutes > 5 
                                    ? `<span class="badge bg-warning text-dark" title="Inactif depuis ${s.idle_minutes} min">
                                        <i class="bi bi-hourglass-split me-1"></i>${s.idle_minutes}m
                                       </span>`
                                    : '<span class="badge bg-success"><i class="bi bi-activity"></i></span>'}
                            </td>
                            <td><code class="small bg-light px-2 py-1 rounded">${s.ip_address || '-'}</code></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
        ` : ''}
        
        <!-- Sessions terminées -->
        ${closedSessions.length > 0 ? `
        <div class="px-3 pb-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-secondary me-2">${closedSessions.length}</span>
                <h6 class="mb-0 text-muted"><i class="bi bi-clock-history me-1"></i>Sessions terminées</h6>
            </div>
            <table class="table table-sm table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        <th style="min-width: 140px">Utilisateur</th>
                        <th style="min-width: 120px">Application</th>
                        <th style="min-width: 140px">Connexion</th>
                        <th style="min-width: 140px">Déconnexion</th>
                        <th style="min-width: 80px">Durée</th>
                        <th style="min-width: 100px">Raison</th>
                        <th style="min-width: 100px">IP</th>
                    </tr>
                </thead>
                <tbody>
                    ${closedSessions.map(s => `
                        <tr>
                            <td>
                                <div class="d-flex align-items-center">
                                    <i class="bi bi-person-circle fs-5 text-muted me-2"></i>
                                    <span class="text-muted">${escapeHtml(s.username || 'Unknown')}</span>
                                </div>
                            </td>
                            <td>
                                ${s.current_app_name 
                                    ? `<span class="badge bg-primary bg-opacity-50"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                    : '<span class="text-muted small"><i class="bi bi-house me-1"></i>Hub</span>'}
                            </td>
                            <td><small class="text-muted">${formatDateTime(s.login_at)}</small></td>
                            <td><small class="text-muted">${formatDateTime(s.logout_at)}</small></td>
                            <td>
                                <span class="badge bg-light text-dark border">
                                    ${s.duration_display || '-'}
                                </span>
                            </td>
                            <td>${getLogoutReasonBadge(s.logout_reason)}</td>
                            <td><code class="small text-muted">${s.ip_address || '-'}</code></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
        ` : ''}
    `;
    
    // Résumé dans le footer (hors zone scrollable)
    const summaryContainer = document.getElementById('pageSessionsSummary');
    if (summaryContainer) {
        summaryContainer.innerHTML = `
            <div class="row text-center">
                <div class="col">
                    <div class="text-muted small">Total sessions</div>
                    <strong class="fs-5">${data.total_sessions}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Actives</div>
                    <strong class="fs-5 text-success">${activeSessions.length}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Terminées</div>
                    <strong class="fs-5 text-secondary">${closedSessions.length}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Période</div>
                    <strong class="fs-5">${data.period_days}j</strong>
                </div>
            </div>
        `;
    }
}

// ===== Page Utilisateurs Dédiée =====

let usersCurrentPage = 1;
let usersPerPageVal = 50;
let usersSearchTimer = null;
let allProfiles = [];

function debounceUsersSearch() {
    clearTimeout(usersSearchTimer);
    usersSearchTimer = setTimeout(() => {
        usersCurrentPage = 1;
        loadUsersPageData();
    }, 300);
}

function changeUsersPerPage() {
    usersPerPageVal = parseInt(document.getElementById('usersPerPage').value || 50);
    usersCurrentPage = 1;
    loadUsersPageData();
}

function changeUsersPage(page) {
    usersCurrentPage = page;
    loadUsersPageData();
}

async function loadUsersPageData() {
    try {
        const search = document.getElementById('usersSearchInput')?.value || '';
        const appSlug = document.getElementById('usersFilterApp')?.value || '';
        const role = document.getElementById('usersFilterRole')?.value || '';
        const profileId = document.getElementById('usersFilterProfile')?.value || '';
        const isActive = document.getElementById('usersFilterStatus')?.value || '';

        const [paginatedData, activeSessions, apps] = await Promise.all([
            api.getUsers({
                page: usersCurrentPage,
                perPage: usersPerPageVal,
                search: search || undefined,
                role: role || undefined,
                appSlug: appSlug || undefined,
                profileId: profileId !== '' ? parseInt(profileId) : undefined,
                isActive: isActive !== '' ? isActive === 'true' : undefined
            }),
            api.getActiveSessions().catch(() => ({ sessions: [] })),
            api.getAllApps()
        ]);

        allApps = apps;
        allUsersPage = paginatedData.users;
        
        // Stats (basées sur un appel stats séparé, ou estimées)
        document.getElementById('usersStatTotal').textContent = paginatedData.total;
        
        // Pour les stats actifs/admins/connectés, on fait un appel séparé si pas de filtre
        if (!search && !appSlug && !role && profileId === '' && isActive === '') {
            document.getElementById('usersStatActive').textContent = paginatedData.users.filter(u => u.is_active).length + (paginatedData.pages > 1 ? '+' : '');
            document.getElementById('usersStatAdmins').textContent = paginatedData.users.filter(u => u.is_admin).length + (paginatedData.pages > 1 ? '+' : '');
        }
        document.getElementById('usersStatConnected').textContent = activeSessions.active_count || 0;
        document.getElementById('usersTabCount').textContent = paginatedData.total;

        // Remplir les filtres apps/profils si pas encore fait
        populateUsersFilters(apps);
        
        // Charger les profils pour le filtre
        if (allProfiles.length === 0) {
            try {
                allProfiles = await api.getProfiles();
                populateProfileFilter(allProfiles);
            } catch (e) {
                console.warn('Profils non disponibles:', e);
            }
        }

        // Info pagination
        const start = (paginatedData.page - 1) * paginatedData.per_page + 1;
        const end = Math.min(paginatedData.page * paginatedData.per_page, paginatedData.total);
        document.getElementById('usersPageInfo').textContent = 
            paginatedData.total > 0 ? `${start}-${end} sur ${paginatedData.total}` : 'Aucun résultat';
        
        // Pagination
        document.getElementById('usersPagePagination').innerHTML = 
            renderPagination(paginatedData.page, paginatedData.pages, 'changeUsersPage');
        
        // Afficher/masquer barre pagination
        document.getElementById('usersPagePaginationBar').classList.toggle('d-none', paginatedData.total === 0);

        renderUsersPageTable(paginatedData.users);
    } catch (error) {
        console.error('Erreur chargement utilisateurs:', error);
        showToast('Erreur', 'Impossible de charger les utilisateurs', 'danger');
    }
}

function populateUsersFilters(apps) {
    const appSelect = document.getElementById('usersFilterApp');
    if (appSelect && appSelect.options.length <= 1) {
        apps.forEach(app => {
            const opt = document.createElement('option');
            opt.value = app.slug;
            opt.textContent = app.name;
            appSelect.appendChild(opt);
        });
    }
}

function populateProfileFilter(profiles) {
    const profileSelect = document.getElementById('usersFilterProfile');
    if (!profileSelect) return;
    // Conserver 'Tous les profils' et 'Sans profil'
    while (profileSelect.options.length > 2) {
        profileSelect.remove(2);
    }
    profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        profileSelect.appendChild(opt);
    });
    
    // Aussi les checkboxes dans le dropdown du modal user
    const userProfileCheckboxes = document.getElementById('userProfileCheckboxes');
    if (userProfileCheckboxes) {
        userProfileCheckboxes.innerHTML = profiles.map(p => `
            <div class="form-check mb-1">
                <input class="form-check-input user-profile-cb" type="checkbox" value="${p.id}" id="userProfileCb${p.id}" onchange="onUserProfileChange()">
                <label class="form-check-label small" for="userProfileCb${p.id}">
                    <span class="badge badge-profile me-1" style="background-color: ${p.color};">&nbsp;</span>
                    ${escapeHtml(p.name)}
                </label>
            </div>
        `).join('');
    }
}

function renderUsersPageTable(users) {
    const container = document.getElementById('usersPageTable');
    
    if (!users || users.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-people display-4"></i>
                <p class="mt-2">Aucun utilisateur trouvé</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-hover align-middle mb-0">
            <thead>
                <tr>
                    <th style="width: 30px;"></th>
                    <th>Utilisateur</th>
                    <th>Email</th>
                    <th>Rôle Hub</th>
                    <th>Profil</th>
                    <th>Apps</th>
                    <th>Statut</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${users.map(user => `
                    <tr class="user-row" id="userRow${user.id}">
                        <td>
                            <button class="btn btn-sm btn-link p-0 text-muted user-expand-btn" onclick="toggleUserApps(${user.id})" title="Voir les applications">
                                <i class="bi bi-chevron-right"></i>
                            </button>
                        </td>
                        <td>
                            <strong>${escapeHtml(user.username)}</strong>
                            ${user.must_change_password ? '<i class="bi bi-key-fill text-warning ms-1" title="Doit changer son mot de passe"></i>' : ''}
                        </td>
                        <td><small class="text-muted">${escapeHtml(user.email || '-')}</small></td>
                        <td>
                            ${user.is_admin 
                                ? '<span class="badge bg-dark">Admin</span>' 
                                : '<span class="badge bg-light text-dark">User</span>'}
                        </td>
                        <td>
                            ${(user.profiles && user.profiles.length > 0)
                                ? user.profiles.map(p => `<span class="badge badge-profile me-1" style="background-color: ${p.color};">${escapeHtml(p.name)}</span>`).join('')
                                : '<span class="text-muted small">-</span>'}
                        </td>
                        <td>
                            <span class="badge bg-light text-dark border" title="${(user.app_roles || []).map(ar => ar.app_name + ' (' + ar.role + ')' + ((ar.source || '').startsWith('profile') ? ' [profil]' : '')).join(', ')}">
                                ${(user.app_roles || []).length} app${(user.app_roles || []).length !== 1 ? 's' : ''}
                            </span>
                        </td>
                        <td>
                            ${user.is_active 
                                ? '<span class="badge bg-light text-dark"><i class="bi bi-check-circle text-success me-1"></i>Actif</span>' 
                                : '<span class="badge bg-light text-dark"><i class="bi bi-x-circle text-danger me-1"></i>Inactif</span>'}
                        </td>
                        <td class="text-end">
                            <div class="btn-group">
                                <button class="btn btn-sm btn-outline-secondary" onclick="editUserPage(${user.id})" title="Modifier">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-warning" onclick="revokeUserSessions(${user.id}, '${escapeHtml(user.username)}')" 
                                    title="Révoquer les sessions" ${user.id === currentUser.id ? 'disabled' : ''}>
                                    <i class="bi bi-door-closed"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" onclick="deleteUserPage(${user.id}, '${escapeHtml(user.username)}')" 
                                    title="Supprimer" ${user.id === currentUser.id ? 'disabled' : ''}>
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                    <tr class="user-apps-detail d-none" id="userAppsDetail${user.id}">
                        <td colspan="8" class="py-0 px-4 bg-light">
                            <div class="py-2">
                                ${(user.app_roles || []).length > 0 
                                    ? `<div class="d-flex flex-wrap gap-1">
                                        ${user.app_roles.map(ar => `
                                            <span class="badge bg-white text-dark border me-1 mb-1" title="Source: ${(ar.source || '').startsWith('profile') ? 'Profil' : 'Manuel'}">
                                                ${(ar.source || '').startsWith('profile') ? '<i class="bi bi-person-badge me-1 text-info" title="Via profil"></i>' : '<i class="bi bi-hand-index me-1 text-secondary" title="Manuel"></i>'}
                                                ${escapeHtml(ar.app_name)} 
                                                <span class="badge ${getRoleBadgeColor(ar.role)} ms-1">${ar.role}</span>
                                            </span>
                                        `).join('')}
                                      </div>`
                                    : '<span class="text-muted small"><i class="bi bi-dash-circle me-1"></i>Aucune application assignée</span>'}
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function toggleUserApps(userId) {
    const detailRow = document.getElementById(`userAppsDetail${userId}`);
    const btn = document.querySelector(`#userRow${userId} .user-expand-btn i`);
    
    if (detailRow.classList.contains('d-none')) {
        detailRow.classList.remove('d-none');
        btn.className = 'bi bi-chevron-down';
    } else {
        detailRow.classList.add('d-none');
        btn.className = 'bi bi-chevron-right';
    }
}

function getRoleBadgeColor(role) {
    switch (role) {
        case 'admin': return 'bg-danger';
        case 'manager': return 'bg-warning text-dark';
        case 'user': return 'bg-primary';
        case 'viewer': return 'bg-info text-dark';
        case 'service': return 'bg-dark';
        default: return 'bg-secondary';
    }
}

function filterUsersTable() {
    usersCurrentPage = 1;
    loadUsersPageData();
}

// ===== Onglet Profils =====

let profileFormModal = null;
let applyProfileModal = null;

async function loadProfilesTab() {
    try {
        const [profiles, apps] = await Promise.all([
            api.getProfiles(),
            allApps.length > 0 ? Promise.resolve(allApps) : api.getAllApps()
        ]);
        
        allProfiles = profiles;
        allApps = apps;
        
        // Stats
        const totalAssigned = profiles.reduce((sum, p) => sum + p.user_count, 0);
        document.getElementById('profilesStatTotal').textContent = profiles.length;
        document.getElementById('profilesStatAssigned').textContent = totalAssigned;
        document.getElementById('profilesTabCount').textContent = profiles.length;
        
        // Le nombre sans profil sera mis à jour quand on aura le total users
        const totalUsersEl = document.getElementById('usersStatTotal');
        const totalUsers = parseInt(totalUsersEl?.textContent) || 0;
        document.getElementById('profilesStatUnassigned').textContent = Math.max(0, totalUsers - totalAssigned);
        
        populateProfileFilter(profiles);
        renderProfilesList(profiles);
    } catch (error) {
        console.error('Erreur chargement profils:', error);
        showToast('Erreur', 'Impossible de charger les profils', 'danger');
    }
}

function renderProfilesList(profiles) {
    const container = document.getElementById('profilesList');
    
    if (!profiles || profiles.length === 0) {
        container.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="bi bi-person-badge display-4"></i>
                <h5 class="mt-3">Aucun profil créé</h5>
                <p>Les profils permettent d'assigner un ensemble d'apps et de rôles en un seul clic.</p>
                <button class="btn btn-primary" onclick="showProfileModal()">
                    <i class="bi bi-plus-lg me-1"></i>Créer un profil
                </button>
            </div>
        `;
        return;
    }
    
    container.innerHTML = profiles.map(profile => `
        <div class="col-md-6 col-lg-4">
            <div class="card h-100 profile-card">
                <div class="card-header d-flex justify-content-between align-items-center py-2" style="border-left: 4px solid ${profile.color};">
                    <div>
                        <span class="badge badge-profile" style="background-color: ${profile.color}; font-size: 0.95em;">${escapeHtml(profile.name)}</span>
                        <span class="badge bg-light text-dark ms-2">${profile.user_count} user${profile.user_count !== 1 ? 's' : ''}</span>
                    </div>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary" onclick="showProfileModal(${profile.id})" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-primary" onclick="showApplyProfileModal(${profile.id})" title="Appliquer à des utilisateurs">
                            <i class="bi bi-person-plus"></i>
                        </button>
                        <button class="btn btn-outline-danger" onclick="deleteProfile(${profile.id}, '${escapeHtml(profile.name)}')" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="card-body py-2">
                    ${profile.description ? `<p class="card-text small text-muted mb-2">${escapeHtml(profile.description)}</p>` : ''}
                    <div class="d-flex flex-wrap gap-1">
                        ${profile.app_roles.map(ar => `
                            <span class="badge bg-white text-dark border" style="font-size: 0.75em;">
                                ${escapeHtml(ar.app_name)} <span class="badge ${getRoleBadgeColor(ar.role)}" style="font-size: 0.8em;">${ar.role}</span>
                            </span>
                        `).join('')}
                        ${profile.app_roles.length === 0 ? '<span class="text-muted small">Aucune application</span>' : ''}
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

function showProfileModal(profileId = null) {
    if (!profileFormModal) {
        profileFormModal = new bootstrap.Modal(document.getElementById('profileFormModal'));
    }
    
    document.getElementById('profileForm').reset();
    document.getElementById('profileId').value = '';
    document.getElementById('profileColor').value = '#6c757d';
    updateProfilePreview();
    
    // Charger les apps dans le formulaire
    loadProfileAppsCheckboxes([]);
    
    if (profileId) {
        // Mode édition
        const profile = allProfiles.find(p => p.id === profileId);
        if (profile) {
            document.getElementById('profileFormTitle').textContent = 'Modifier le Profil';
            document.getElementById('profileId').value = profile.id;
            document.getElementById('profileName').value = profile.name;
            document.getElementById('profileDescription').value = profile.description || '';
            document.getElementById('profileColor').value = profile.color || '#6c757d';
            updateProfilePreview();
            loadProfileAppsCheckboxes(profile.app_roles);
        }
    } else {
        document.getElementById('profileFormTitle').textContent = 'Nouveau Profil';
    }
    
    profileFormModal.show();
}

function updateProfilePreview() {
    const color = document.getElementById('profileColor').value;
    const name = document.getElementById('profileName').value || 'Profil';
    const badge = document.getElementById('profilePreviewBadge');
    badge.style.backgroundColor = color;
    badge.textContent = name;
}

// Mettre à jour l'aperçu en temps réel
document.addEventListener('DOMContentLoaded', () => {
    const colorInput = document.getElementById('profileColor');
    const nameInput = document.getElementById('profileName');
    if (colorInput) colorInput.addEventListener('input', updateProfilePreview);
    if (nameInput) nameInput.addEventListener('input', updateProfilePreview);
});

function loadProfileAppsCheckboxes(existingRoles) {
    const container = document.getElementById('profileAppsCheckboxes');
    if (!allApps || allApps.length === 0) {
        container.innerHTML = '<small class="text-muted">Aucune application disponible</small>';
        return;
    }

    const rolesByAppId = {};
    existingRoles.forEach(ar => {
        rolesByAppId[ar.app_id] = ar.role;
    });

    container.innerHTML = allApps.map(app => {
        const currentRole = rolesByAppId[app.id] || '';
        const isChecked = currentRole !== '';
        
        return `
            <div class="d-flex align-items-center mb-2 app-role-row">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input profile-app-checkbox" type="checkbox" value="${app.id}" 
                        id="profileApp${app.id}" ${isChecked ? 'checked' : ''}
                        onchange="toggleProfileRoleSelect(${app.id})">
                    <label class="form-check-label" for="profileApp${app.id}">
                        <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                        ${escapeHtml(app.name)}
                    </label>
                </div>
                <select class="form-select form-select-sm profile-role-select" id="profileAppRole${app.id}" 
                    style="width: 120px;" ${isChecked ? '' : 'disabled'}>
                    <option value="">-- Rôle --</option>
                    ${AVAILABLE_ROLES.map(role => `
                        <option value="${role}" ${currentRole === role ? 'selected' : ''}>
                            ${role.charAt(0).toUpperCase() + role.slice(1)}
                        </option>
                    `).join('')}
                </select>
            </div>
        `;
    }).join('');
}

function toggleProfileRoleSelect(appId) {
    const checkbox = document.getElementById(`profileApp${appId}`);
    const roleSelect = document.getElementById(`profileAppRole${appId}`);
    if (checkbox.checked) {
        roleSelect.disabled = false;
        if (!roleSelect.value) roleSelect.value = 'user';
    } else {
        roleSelect.disabled = true;
        roleSelect.value = '';
    }
}

function profileSelectAllApps(checked) {
    document.querySelectorAll('.profile-app-checkbox').forEach(cb => {
        cb.checked = checked;
        const appId = cb.value;
        const roleSelect = document.getElementById(`profileAppRole${appId}`);
        if (checked) {
            roleSelect.disabled = false;
            if (!roleSelect.value) roleSelect.value = 'user';
        } else {
            roleSelect.disabled = true;
            roleSelect.value = '';
        }
    });
}

async function handleSaveProfile(event) {
    event.preventDefault();
    
    const id = document.getElementById('profileId').value;
    const isCreating = !id;
    
    // Collecter les app_roles
    const appRoles = [];
    document.querySelectorAll('.profile-app-checkbox:checked').forEach(cb => {
        const appId = parseInt(cb.value);
        const role = document.getElementById(`profileAppRole${appId}`).value;
        if (role) {
            appRoles.push({ app_id: appId, role: role });
        }
    });
    
    const data = {
        name: document.getElementById('profileName').value,
        description: document.getElementById('profileDescription').value || null,
        color: document.getElementById('profileColor').value,
        app_roles: appRoles
    };
    
    try {
        if (isCreating) {
            await api.createProfile(data);
            showToast('Succès', `Profil "${data.name}" créé`, 'success');
        } else {
            await api.updateProfile(parseInt(id), data);
            showToast('Succès', `Profil "${data.name}" modifié`, 'success');
        }
        
        profileFormModal.hide();
        allProfiles = []; // Forcer le rechargement
        await loadProfilesTab();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteProfile(id, name) {
    if (!confirm(`Supprimer le profil "${name}" ?\n\nLes utilisateurs assignés à ce profil garderont leurs accès actuels.`)) return;
    
    try {
        await api.deleteProfile(id);
        showToast('Succès', `Profil "${name}" supprimé`, 'success');
        allProfiles = [];
        await loadProfilesTab();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}



async function showApplyProfileModal(profileId) {
    if (!applyProfileModal) {
        applyProfileModal = new bootstrap.Modal(document.getElementById('applyProfileModal'));
    }
    
    const profile = allProfiles.find(p => p.id === profileId);
    if (!profile) return;
    
    document.getElementById('applyProfileId').value = profileId;
    const nameBadge = document.getElementById('applyProfileName');
    nameBadge.textContent = profile.name;
    nameBadge.style.backgroundColor = profile.color;
    
    // Charger tous les users (sans pagination pour le sélecteur)
    try {
        const users = await api.getUsers({ page: 0 });  // page=0 = tous
        renderApplyProfileUsersList(users, profileId);
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les utilisateurs', 'danger');
        return;
    }
    
    applyProfileModal.show();
}

function renderApplyProfileUsersList(users, profileId) {
    const container = document.getElementById('applyProfileUsersList');
    container.innerHTML = users.map(user => {
        const hasProfile = (user.profiles || []).some(p => p.id === profileId);
        return `
        <div class="form-check apply-profile-user-item mb-1">
            <input class="form-check-input apply-profile-user-cb" type="checkbox" value="${user.id}" id="applyUser${user.id}"
                data-had-profile="${hasProfile}"
                ${hasProfile ? 'checked' : ''}>
            <label class="form-check-label small" for="applyUser${user.id}">
                ${escapeHtml(user.username)}
                ${(user.profiles || []).map(p => `<span class="badge badge-profile ms-1" style="background-color: ${p.color}; font-size: 0.7em;">${escapeHtml(p.name)}</span>`).join('')}
                ${hasProfile ? '<span class="text-muted ms-1">(assigné)</span>' : ''}
            </label>
        </div>
    `}).join('');
    
    // Stocker pour le filtre
    container.dataset.allUsers = JSON.stringify(users.map(u => ({ id: u.id, username: u.username })));
}

function filterApplyProfileUsers() {
    const search = document.getElementById('applyProfileSearchInput').value.toLowerCase();
    document.querySelectorAll('.apply-profile-user-item').forEach(item => {
        const label = item.querySelector('label').textContent.toLowerCase();
        item.style.display = label.includes(search) ? '' : 'none';
    });
}

async function handleApplyProfile() {
    const profileId = parseInt(document.getElementById('applyProfileId').value);
    const syncRoles = document.getElementById('applyProfileSyncRoles').checked;
    
    // Utilisateurs à ajouter : cochés maintenant mais qui n'avaient pas le profil
    const toAdd = [];
    // Utilisateurs à retirer : décochés maintenant mais qui avaient le profil
    const toRemove = [];
    
    document.querySelectorAll('.apply-profile-user-cb').forEach(cb => {
        const userId = parseInt(cb.value);
        const hadProfile = cb.dataset.hadProfile === 'true';
        const isChecked = cb.checked;
        
        if (isChecked && !hadProfile) {
            toAdd.push(userId);
        } else if (!isChecked && hadProfile) {
            toRemove.push(userId);
        }
    });
    
    if (toAdd.length === 0 && toRemove.length === 0) {
        showToast('Info', 'Aucune modification', 'warning');
        return;
    }
    
    try {
        const messages = [];
        
        if (toAdd.length > 0) {
            const result = await api.applyProfile(profileId, toAdd, syncRoles);
            messages.push(result.message);
        }
        
        if (toRemove.length > 0) {
            const result = await api.detachProfile(profileId, toRemove);
            messages.push(result.message);
        }
        
        showToast('Succès', messages.join(' — '), 'success');
        applyProfileModal.hide();
        allProfiles = [];
        await Promise.all([loadProfilesTab(), loadUsersPageData()]);
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// Fonctions pour le sélecteur de profil dans le modal user
async function onUserProfileChange() {
    const selectedIds = Array.from(document.querySelectorAll('.user-profile-cb:checked')).map(cb => parseInt(cb.value));
    
    // Mettre à jour le label du dropdown
    updateUserProfileDropdownLabel(selectedIds);
    
    // Récupérer les rôles manuels actuels de l'utilisateur (ceux cochés manuellement)
    const currentManualRoles = [];
    allApps.forEach(app => {
        const checkbox = document.getElementById(`userApp${app.id}`);
        const roleSelect = document.getElementById(`userAppRole${app.id}`);
        if (checkbox?.dataset?.source !== 'profile' && checkbox?.checked && roleSelect?.value) {
            currentManualRoles.push({ app_id: app.id, role: roleSelect.value, source: 'manual' });
        }
    });
    
    // Construire la liste combinée : rôles de tous les profils + rôles manuels
    const combinedRoles = [...currentManualRoles];
    const profileAppIds = new Set();

    if (selectedIds.length > 0) {
        // Rôle le plus élevé par app parmi tous les profils sélectionnés
        const roleHierarchy = { admin: 4, manager: 3, user: 2, viewer: 1 };
        const bestProfileRoles = {};  // app_id -> { role, level }

        for (const pid of selectedIds) {
            const profile = allProfiles.find(p => p.id === pid);
            if (!profile) continue;
            for (const ar of profile.app_roles) {
                const level = roleHierarchy[ar.role] || 0;
                if (!bestProfileRoles[ar.app_id] || level > bestProfileRoles[ar.app_id].level) {
                    bestProfileRoles[ar.app_id] = { role: ar.role, level };
                }
                profileAppIds.add(ar.app_id);
            }
        }

        for (const [appId, val] of Object.entries(bestProfileRoles)) {
            combinedRoles.push({ app_id: parseInt(appId), role: val.role, source: 'profile' });
        }

        // Retirer les manuels déjà couverts par les profils
        const filteredRoles = combinedRoles.filter(ar => !(ar.source === 'manual' && profileAppIds.has(ar.app_id)));
        loadUserAppsWithRoles(filteredRoles);
        return;
    }
    
    // Pas de profil sélectionné : tout est manuel
    loadUserAppsWithRoles(currentManualRoles);
}

function updateUserProfileDropdownLabel(selectedIds) {
    const label = document.getElementById('userProfileDropdownLabel');
    if (!label) return;
    if (selectedIds.length === 0) {
        label.textContent = 'Aucun profil';
    } else if (selectedIds.length === 1) {
        const profile = allProfiles.find(p => p.id === selectedIds[0]);
        label.textContent = profile ? profile.name : '1 profil';
    } else {
        label.textContent = `${selectedIds.length} profils`;
    }
}

function showAddUserFormPage() {
    document.getElementById('userFormTitle').textContent = 'Nouvel Utilisateur';
    document.getElementById('userForm').reset();
    document.getElementById('userId').value = '';
    document.getElementById('userUsername').disabled = false;
    document.getElementById('userPassword').required = true;
    document.getElementById('passwordHint').classList.add('d-none');
    document.getElementById('userActive').checked = true;
    document.getElementById('userAdmin').checked = false;
    const mustChangePwdEl = document.getElementById('userMustChangePassword');
    if (mustChangePwdEl) mustChangePwdEl.checked = false;
    const canChangePwdEl = document.getElementById('userCanChangePassword');
    if (canChangePwdEl) canChangePwdEl.checked = true;  // Par défaut, l'utilisateur peut changer son mdp
    // Réinitialiser les profils (checkboxes)
    document.querySelectorAll('.user-profile-cb').forEach(cb => { cb.checked = false; });
    updateUserProfileDropdownLabel([]);
    loadUserAppsCheckboxes([]);
    userFormModal.show();
}

async function editUserPage(id) {
    let user = allUsersPage.find(u => u.id === id);
    if (!user) {
        try {
            const users = await api.getUsers({ page: 0 });
            user = users.find(u => u.id === id);
        } catch (e) {
            console.error('Erreur chargement user:', e);
        }
        if (!user) return;
    }

    document.getElementById('userFormTitle').textContent = 'Modifier l\'Utilisateur';
    document.getElementById('userId').value = user.id;
    document.getElementById('userUsername').value = user.username;
    document.getElementById('userUsername').disabled = true;
    document.getElementById('userEmail').value = user.email || '';
    document.getElementById('userPassword').value = '';
    document.getElementById('userPassword').required = false;
    document.getElementById('passwordHint').classList.remove('d-none');
    document.getElementById('userActive').checked = user.is_active;
    document.getElementById('userAdmin').checked = user.is_admin;
    
    // Charger must_change_password
    const mustChangePwdEl = document.getElementById('userMustChangePassword');
    if (mustChangePwdEl) {
        mustChangePwdEl.checked = user.must_change_password || false;
    }
    
    // Charger can_change_password
    const canChangePwdEl = document.getElementById('userCanChangePassword');
    if (canChangePwdEl) {
        canChangePwdEl.checked = user.can_change_password !== false;  // Par défaut true
    }
    
    // Sélectionner les profils (checkboxes)
    const userProfileIds = (user.profiles || []).map(p => p.id);
    document.querySelectorAll('.user-profile-cb').forEach(cb => {
        cb.checked = userProfileIds.includes(parseInt(cb.value));
    });
    updateUserProfileDropdownLabel(userProfileIds);
    
    const userAppRoles = user.app_roles || [];
    loadUserAppsWithRoles(userAppRoles);
    
    userFormModal.show();
}

async function revokeUserSessions(userId, username) {
    if (!confirm(`Révoquer toutes les sessions de "${username}" ?\n\nL'utilisateur sera déconnecté de tous ses appareils.`)) return;
    
    try {
        await api.revokeUserSessions(userId);
        showToast('Succès', `Sessions de ${username} révoquées`, 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteUserPage(id, username) {
    if (!confirm(`Supprimer l'utilisateur "${username}" ?`)) return;
    
    try {
        await api.deleteUser(id);
        showToast('Succès', 'Utilisateur supprimé', 'success');
        allProfiles = [];
        await loadUsersPageData();
        loadProfilesTab();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== Vue Erreurs HTTP (Audit) =====

let errorsPage = 1;

/**
 * Met à jour le badge de notification d'erreurs dans la navigation principale.
 */
function updateNavErrorBadge(count) {
    const navBadge = document.getElementById('navAuditErrorBadge');
    if (navBadge) {
        if (count > 0) {
            navBadge.textContent = count > 99 ? '99+' : count;
            navBadge.style.display = 'inline';
        } else {
            navBadge.style.display = 'none';
        }
    }
}

/**
 * Interroge l'API pour connaître le nombre de nouvelles erreurs
 * depuis le dernier acquittement, puis met à jour le badge nav.
 */
async function updateErrorNotificationBadge() {
    try {
        const result = await api.getNewErrorsCount(errorsLastAcknowledgedAt);
        updateNavErrorBadge(result.count || 0);
    } catch (e) {
        // Silencieux — ne pas bloquer le reste de l'UI
    }
}

/**
 * Purge les erreurs (avec confirmation).
 */
async function purgeErrors() {
    const hours = parseInt(document.getElementById('errorsFilterHours')?.value || 24);
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    
    const desc = errorCode ? `les erreurs ${errorCode}` : 'toutes les erreurs';
    const period = `des ${hours} dernières heures`;
    
    if (!confirm(`Purger ${desc} ${period} ?\n\nCette action est irréversible.`)) return;
    
    try {
        const result = await api.purgeErrors(hours, errorCode ? parseInt(errorCode) : null);
        showToast('Succès', result.message, 'success');
        await loadAuditPageErrors();
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible de purger les erreurs', 'danger');
    }
}

/**
 * Purge toutes les erreurs (sans filtre de période).
 */
async function purgeAllErrors() {
    if (!confirm('Purger TOUTES les erreurs ?\n\nCette action est irréversible.')) return;
    
    try {
        const result = await api.purgeErrors();
        showToast('Succès', result.message, 'success');
        await loadAuditPageErrors();
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible de purger les erreurs', 'danger');
    }
}

async function loadAuditPageErrors() {
    const hours = parseInt(document.getElementById('errorsFilterHours')?.value || 24);
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    const errorType = document.getElementById('errorsFilterType')?.value || null;
    const sourceApp = document.getElementById('errorsFilterApp')?.value || null;
    const path = document.getElementById('errorsFilterPath')?.value || null;
    
    try {
        // Charger les apps pour le filtre si pas encore fait
        await loadErrorsAppFilter();
        
        // Charger stats et logs en parallèle
        const [stats, errors] = await Promise.all([
            api.getErrorStats(hours),
            api.getErrorLogs({
                page: errorsPage,
                perPage: 50,
                errorCode: errorCode ? parseInt(errorCode) : null,
                errorType,
                sourceApp,
                path,
                hours
            })
        ]);
        
        // Mettre à jour les stats
        document.getElementById('errorStatTotal').textContent = stats.total || 0;
        
        // Calculer 4xx et 5xx
        const count4xx = stats.by_code?.filter(c => c.code >= 400 && c.code < 500)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        const count5xx = stats.by_code?.filter(c => c.code >= 500)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        const countAuth = stats.by_code?.filter(c => c.code === 401 || c.code === 403)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        
        document.getElementById('errorStat4xx').textContent = count4xx;
        document.getElementById('errorStat5xx').textContent = count5xx;
        document.getElementById('errorStatAuth').textContent = countAuth;
        
        // Mettre à jour le badge dans l'onglet
        const badge = document.getElementById('errorCountBadge');
        if (stats.total > 0) {
            badge.textContent = stats.total > 99 ? '99+' : stats.total;
            badge.style.display = 'inline';
        } else {
            badge.style.display = 'none';
        }
        
        // Afficher la table
        renderErrorsTable(errors);
        
        // Marquer les erreurs comme consultées (acquittement automatique)
        // L'admin a vu la page, on considère les erreurs comme prises en compte
        try {
            const ack = await api.acknowledgeErrors();
            errorsLastAcknowledgedAt = ack.acknowledged_at;
            localStorage.setItem('errorsLastAcknowledgedAt', errorsLastAcknowledgedAt);
            updateNavErrorBadge(0);
        } catch (e) {
            // Non bloquant
        }
        
    } catch (error) {
        console.error('Erreur chargement erreurs:', error);
        showToast('Erreur', 'Impossible de charger les logs d\'erreurs', 'danger');
    }
}

async function loadErrorsAppFilter() {
    const appSelect = document.getElementById('errorsFilterApp');
    if (appSelect.options.length <= 1) {
        try {
            const apps = allApps.length > 0 ? allApps : await api.getAllApps();
            apps.forEach(app => {
                const opt = document.createElement('option');
                opt.value = app.slug;
                opt.textContent = app.name;
                appSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les apps:', e);
        }
    }
}

function resetErrorsFilters() {
    document.getElementById('errorsFilterCode').value = '';
    document.getElementById('errorsFilterType').value = '';
    document.getElementById('errorsFilterApp').value = '';
    document.getElementById('errorsFilterPath').value = '';
    document.getElementById('errorsFilterHours').value = '24';
    errorsPage = 1;
    loadAuditPageErrors();
}

function renderErrorsTable(data) {
    const container = document.getElementById('pageErrorsTable');
    const countSpan = document.getElementById('errorsCount');
    const pagination = document.getElementById('errorsPagination');
    
    countSpan.textContent = `${data.total} erreurs`;
    pagination.innerHTML = renderPagination(data.page, data.pages, 'changeErrorsPage');
    
    if (!data.errors || data.errors.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-check-circle display-4 text-success"></i>
                <p class="mt-2">Aucune erreur dans cette période 🎉</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Code</th>
                    <th>Type</th>
                    <th>Méthode</th>
                    <th>Chemin</th>
                    <th>App Source</th>
                    <th>Message</th>
                    <th>IP</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${data.errors.map(e => `
                    <tr class="${e.error_code >= 500 ? 'table-danger' : ''}">
                        <td><small>${formatDateTime(e.created_at)}</small></td>
                        <td>${getErrorCodeBadge(e.error_code)}</td>
                        <td><small class="text-muted">${e.error_type || '-'}</small></td>
                        <td><code class="small">${e.request_method || '-'}</code></td>
                        <td><code class="small" title="${escapeHtml(e.request_path || '')}">${truncateText(e.request_path, 30)}</code></td>
                        <td>${e.source_app_slug ? `<span class="badge bg-info">${escapeHtml(e.source_app_slug)}</span>` : '-'}</td>
                        <td><small class="text-muted" title="${escapeHtml(e.error_message || '')}">${truncateText(e.error_message, 40)}</small></td>
                        <td><code class="small">${e.ip_address || '-'}</code></td>
                        <td>
                            ${e.has_stack_trace ? `<button class="btn btn-outline-secondary btn-sm" onclick="showErrorDetail(${e.id})" title="Voir le détail"><i class="bi bi-eye"></i></button>` : ''}
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function getErrorCodeBadge(code) {
    let bgClass = 'bg-secondary';
    if (code === 401) bgClass = 'bg-warning text-dark';
    else if (code === 403) bgClass = 'bg-danger';
    else if (code === 404) bgClass = 'bg-info';
    else if (code === 422) bgClass = 'bg-warning text-dark';
    else if (code >= 500) bgClass = 'bg-danger';
    
    return `<span class="badge ${bgClass}">${code}</span>`;
}

function truncateText(text, maxLength) {
    if (!text) return '-';
    if (text.length <= maxLength) return escapeHtml(text);
    return escapeHtml(text.substring(0, maxLength)) + '...';
}

async function changeErrorsPage(page) {
    errorsPage = page;
    await loadAuditPageErrors();
}

async function showErrorDetail(errorId) {
    try {
        const error = await api.getErrorDetail(errorId);
        
        const modal = `
            <div class="modal fade" id="errorDetailModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="bi bi-exclamation-triangle me-2"></i>
                                Erreur ${error.error_code} - ${error.error_type || 'Inconnu'}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row mb-3">
                                <div class="col-md-6">
                                    <small class="text-muted">Date/Heure</small>
                                    <div>${formatDateTime(error.created_at)}</div>
                                </div>
                                <div class="col-md-6">
                                    <small class="text-muted">Request ID</small>
                                    <div><code>${error.request_id || '-'}</code></div>
                                </div>
                            </div>
                            <div class="row mb-3">
                                <div class="col-md-4">
                                    <small class="text-muted">Méthode</small>
                                    <div><code>${error.request_method || '-'}</code></div>
                                </div>
                                <div class="col-md-8">
                                    <small class="text-muted">Chemin</small>
                                    <div><code>${escapeHtml(error.request_path || '-')}</code></div>
                                </div>
                            </div>
                            <div class="row mb-3">
                                <div class="col-md-4">
                                    <small class="text-muted">App Source</small>
                                    <div>${error.source_app_slug ? `<span class="badge bg-info">${escapeHtml(error.source_app_slug)}</span>` : '-'}</div>
                                </div>
                                <div class="col-md-4">
                                    <small class="text-muted">Utilisateur</small>
                                    <div>${error.username || (error.user_id ? `ID: ${error.user_id}` : '-')}</div>
                                </div>
                                <div class="col-md-4">
                                    <small class="text-muted">IP</small>
                                    <div><code>${error.ip_address || '-'}</code></div>
                                </div>
                            </div>
                            <div class="mb-3">
                                <small class="text-muted">Message d'erreur</small>
                                <div class="alert alert-danger mb-0">${escapeHtml(error.error_message || 'Aucun message')}</div>
                            </div>
                            ${error.stack_trace ? `
                                <div class="mb-3">
                                    <small class="text-muted">Stack Trace</small>
                                    <pre class="bg-dark text-light p-3 rounded small" style="max-height: 300px; overflow-y: auto;">${escapeHtml(error.stack_trace)}</pre>
                                </div>
                            ` : ''}
                            <div class="mb-0">
                                <small class="text-muted">User Agent</small>
                                <div class="small text-muted">${escapeHtml(error.user_agent || '-')}</div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Supprimer l'ancien modal s'il existe
        const existing = document.getElementById('errorDetailModal');
        if (existing) existing.remove();
        
        // Ajouter et afficher le modal
        document.body.insertAdjacentHTML('beforeend', modal);
        const modalEl = new bootstrap.Modal(document.getElementById('errorDetailModal'));
        modalEl.show();
        
    } catch (error) {
        console.error('Erreur chargement détail:', error);
        showToast('Erreur', 'Impossible de charger le détail de l\'erreur', 'danger');
    }
}

async function exportErrors() {
    const hours = parseInt(document.getElementById('errorsFilterHours')?.value || 24);
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    
    try {
        await api.exportErrors(hours, errorCode ? parseInt(errorCode) : null);
        showToast('Succès', 'Export CSV téléchargé', 'success');
    } catch (error) {
        showToast('Erreur', 'Impossible d\'exporter les erreurs', 'danger');
    }
}


// =============================================================================
// DISCOVERY — Explorateur d'APIs (nouvelle version)
// =============================================================================

// État global Discovery
let discoveryApps = [];                // Toutes les apps chargées
let discoverySelectedApp = null;       // App actuellement sélectionnée {slug, name, ...}
let discoveryOpenApiSpec = null;       // Spec OpenAPI de l'app sélectionnée
let discoverySelectedEndpoint = null;  // Endpoint sélectionné depuis la spec
let discoveryRawData = null;           // Données brutes de la dernière réponse (pour filtrage)
let discoveryVisibleColumns = null;    // Set de colonnes visibles (null = toutes)
let discoveryHasOpenApi = false;       // L'app sélectionnée a-t-elle une spec OpenAPI ?
let discoveryMode = 'requester';       // Mode actuel : 'requester' ou 'm2m'
let discoveryM2MMarkdown = null;       // Markdown M2M mis en cache pour l'app sélectionnée

/**
 * Charge la page Discovery — liste les apps dans la sidebar
 */
async function loadDiscoveryPage() {
    try {
        const result = await api.getDiscoveryApps();
        discoveryApps = result.apps || [];
        document.getElementById('discoveryAppCount').textContent = result.total || 0;
        renderDiscoveryAppList(discoveryApps);

        // Restaurer la dernière app sélectionnée
        const lastSlug = sessionStorage.getItem('discovery_last_app');
        if (lastSlug) {
            const app = discoveryApps.find(a => a.slug === lastSlug);
            if (app) selectDiscoveryApp(app.slug);
        }
    } catch (error) {
        console.error('Erreur chargement Discovery:', error);
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
    }
}

/**
 * Affiche la liste des apps dans la sidebar
 */
function renderDiscoveryAppList(apps) {
    const container = document.getElementById('discoveryAppList');

    if (!apps.length) {
        container.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-inbox fs-3"></i><p class="mt-2 small">Aucune application active</p></div>';
        return;
    }

    container.innerHTML = apps.map(app => `
        <div class="discovery-app-item ${discoverySelectedApp?.slug === app.slug ? 'active' : ''}"
             onclick="selectDiscoveryApp('${escapeHtml(app.slug)}')" data-slug="${escapeHtml(app.slug)}">
            <div class="d-flex align-items-center gap-2">
                <i class="bi ${escapeHtml(app.icon || 'bi-app')}" style="color: ${escapeHtml(app.color || '#6c757d')}; font-size: 1.1rem;"></i>
                <div class="flex-grow-1 overflow-hidden">
                    <div class="fw-semibold text-truncate" style="font-size: 0.85rem;">${escapeHtml(app.name)}</div>
                    <div class="text-muted text-truncate" style="font-size: 0.72rem;">${escapeHtml(app.slug)}</div>
                </div>
                ${app.is_shared_service ? '<span class="badge bg-info-subtle text-info-emphasis" style="font-size: 0.65rem;">shared</span>' : ''}
            </div>
        </div>
    `).join('');
}

/**
 * Filtre la liste des apps dans la sidebar
 */
function filterDiscoveryApps() {
    const q = document.getElementById('discoveryAppSearch').value.toLowerCase().trim();
    const filtered = q
        ? discoveryApps.filter(a => a.name.toLowerCase().includes(q) || a.slug.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q))
        : discoveryApps;
    renderDiscoveryAppList(filtered);
}

/**
 * Sélectionne une app dans la sidebar et charge sa spec OpenAPI
 */
async function selectDiscoveryApp(slug) {
    const app = discoveryApps.find(a => a.slug === slug);
    if (!app) return;

    discoverySelectedApp = app;
    sessionStorage.setItem('discovery_last_app', slug);

    // Mettre à jour la sidebar
    document.querySelectorAll('.discovery-app-item').forEach(el => {
        el.classList.toggle('active', el.dataset.slug === slug);
    });

    // Mettre à jour l'en-tête
    document.getElementById('discoveryAppTitle').textContent = app.name;
    document.getElementById('discoveryAppDesc').textContent = app.description || '';

    // Afficher le toggle mode et revenir au requêteur
    document.getElementById('discoveryModeToggle').style.display = '';
    discoveryM2MMarkdown = null;
    switchDiscoveryMode('requester');

    // Réinitialiser
    discoveryOpenApiSpec = null;
    discoverySelectedEndpoint = null;
    discoveryHasOpenApi = false;
    clearDiscoveryResult();

    const endpointSelect = document.getElementById('discoveryEndpointSelect');
    endpointSelect.innerHTML = '<option value="">⏳ Chargement des endpoints…</option>';
    endpointSelect.disabled = true;
    document.getElementById('discoveryEndpointInfo').classList.add('d-none');
    document.getElementById('discoveryParamsSection').classList.add('d-none');
    document.getElementById('discoveryBodySection').classList.add('d-none');
    document.getElementById('discoveryPath').value = '/';
    document.getElementById('discoveryMethod').value = 'GET';

    // Charger la spec OpenAPI via le proxy backend
    try {
        const result = await api.getAppOpenApi(slug);

        if (result.spec && result.spec.paths) {
            discoveryOpenApiSpec = result.spec;
            discoveryHasOpenApi = true;
            populateDiscoveryEndpointSelect(result.spec);
            document.getElementById('discoveryEndpointRow').classList.remove('d-none');
            document.getElementById('discoveryQuerySection').classList.add('d-none');
        } else {
            // Pas de spec OpenAPI — mode manuel
            discoveryHasOpenApi = false;
            endpointSelect.innerHTML = '<option value="">Pas de spec OpenAPI — mode manuel</option>';
            endpointSelect.disabled = true;
            document.getElementById('discoveryQuerySection').classList.remove('d-none');
            showToast('Info', `${app.name} n'a pas de spécification OpenAPI — mode manuel activé`, 'info');
        }
    } catch (error) {
        console.error('Erreur chargement OpenAPI:', error);
        endpointSelect.innerHTML = '<option value="">❌ Erreur de chargement</option>';
        document.getElementById('discoveryQuerySection').classList.remove('d-none');
    }
}

/**
 * Remplit le sélecteur d'endpoints depuis la spec OpenAPI
 */
function populateDiscoveryEndpointSelect(spec) {
    const select = document.getElementById('discoveryEndpointSelect');
    select.innerHTML = '<option value="">— Sélectionner un endpoint —</option>';

    const endpointsByTag = {};

    for (const [path, methods] of Object.entries(spec.paths || {})) {
        for (const [method, details] of Object.entries(methods)) {
            if (!['get', 'post', 'put', 'patch', 'delete'].includes(method.toLowerCase())) continue;
            const tag = (details.tags || ['Autre'])[0];
            if (!endpointsByTag[tag]) endpointsByTag[tag] = [];

            endpointsByTag[tag].push({
                method: method.toUpperCase(),
                path,
                summary: details.summary || '',
                description: details.description || '',
                operationId: details.operationId || '',
                parameters: details.parameters || [],
                requestBody: details.requestBody || null,
                responses: details.responses || {},
                tags: details.tags || ['Autre'],
            });
        }
    }

    for (const [tag, endpoints] of Object.entries(endpointsByTag)) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📁 ${tag}`;
        endpoints.forEach(ep => {
            const opt = document.createElement('option');
            opt.value = JSON.stringify(ep);
            opt.textContent = `[${ep.method}] ${ep.path} — ${ep.summary || ep.operationId || 'Sans description'}`;
            optgroup.appendChild(opt);
        });
        select.appendChild(optgroup);
    }

    select.disabled = false;
    const total = Object.values(endpointsByTag).flat().length;
    showToast('Endpoints chargés', `${total} endpoint(s) disponible(s)`, 'success');
}

/**
 * Appelé quand on sélectionne un endpoint dans le dropdown
 */
function onDiscoveryEndpointChange() {
    const select = document.getElementById('discoveryEndpointSelect');
    const val = select.value;

    if (!val) {
        document.getElementById('discoveryEndpointInfo').classList.add('d-none');
        document.getElementById('discoveryParamsSection').classList.add('d-none');
        document.getElementById('discoveryBodySection').classList.add('d-none');
        discoverySelectedEndpoint = null;
        return;
    }

    try {
        const ep = JSON.parse(val);
        discoverySelectedEndpoint = ep;

        // Badge méthode
        const methodBadge = document.getElementById('discoveryEndpointMethod');
        const colors = { GET: 'bg-success', POST: 'bg-primary', PUT: 'bg-warning text-dark', PATCH: 'bg-info', DELETE: 'bg-danger' };
        methodBadge.className = `badge ${colors[ep.method] || 'bg-secondary'}`;
        methodBadge.textContent = ep.method;
        document.getElementById('discoveryEndpointPath').textContent = ep.path;
        document.getElementById('discoveryEndpointDescription').textContent = ep.description || ep.summary || '';
        document.getElementById('discoveryEndpointInfo').classList.remove('d-none');

        // Remplir les champs de requête
        document.getElementById('discoveryMethod').value = ep.method;
        document.getElementById('discoveryPath').value = ep.path;

        // Paramètres
        generateDiscoveryParams(ep);

        // Body
        if (['POST', 'PUT', 'PATCH'].includes(ep.method) && ep.requestBody) {
            document.getElementById('discoveryBodySection').classList.remove('d-none');
            generateDiscoveryBodyExample(ep.requestBody);
        } else {
            document.getElementById('discoveryBodySection').classList.add('d-none');
            document.getElementById('discoveryBody').value = '';
        }
    } catch (e) {
        console.error('Erreur parsing endpoint:', e);
    }
}

/**
 * Appelé quand l'utilisateur tape manuellement un path
 */
function onDiscoveryPathInput() {
    // Désélectionner l'endpoint du dropdown si l'utilisateur modifie le path
    const select = document.getElementById('discoveryEndpointSelect');
    if (select.value) {
        // Vérifier si le path correspond encore
        try {
            const ep = JSON.parse(select.value);
            if (document.getElementById('discoveryPath').value.trim() !== ep.path) {
                select.value = '';
                discoverySelectedEndpoint = null;
                document.getElementById('discoveryEndpointInfo').classList.add('d-none');
            }
        } catch (e) { /* ignore */ }
    }
}

/**
 * Génère les champs de paramètres pour un endpoint
 */
function generateDiscoveryParams(endpoint) {
    const section = document.getElementById('discoveryParamsSection');
    const container = document.getElementById('discoveryParams');
    const params = endpoint.parameters || [];

    if (!params.length) {
        section.classList.add('d-none');
        return;
    }

    section.classList.remove('d-none');
    container.innerHTML = '';

    params.forEach(param => {
        const typeIcon = { path: 'bi-link-45deg', query: 'bi-question-circle', header: 'bi-card-heading' }[param.in] || 'bi-tag';
        const row = document.createElement('div');
        row.className = 'row g-2 mb-1 align-items-center';
        row.innerHTML = `
            <div class="col-md-3">
                <div class="input-group input-group-sm">
                    <span class="input-group-text"><i class="bi ${typeIcon}"></i></span>
                    <input type="text" class="form-control" value="${escapeHtml(param.name)}" readonly>
                </div>
            </div>
            <div class="col-md-5">
                <input type="text" class="form-control form-control-sm discovery-param-value"
                       data-param-name="${escapeHtml(param.name)}"
                       data-param-in="${param.in}"
                       placeholder="${escapeHtml(param.description || param.schema?.type || 'valeur')}"
                       ${param.required ? 'required' : ''}>
            </div>
            <div class="col-md-4">
                <small class="text-muted">
                    ${param.required ? '<span class="badge bg-danger me-1">Requis</span>' : ''}
                    <span class="badge bg-secondary">${param.in}</span>
                    ${param.schema?.type ? `<span class="badge bg-light text-dark">${param.schema.type}</span>` : ''}
                </small>
            </div>
        `;
        container.appendChild(row);
    });

    // Mettre à jour le path en temps réel
    container.querySelectorAll('.discovery-param-value').forEach(input => {
        input.addEventListener('input', updateDiscoveryPathWithParams);
    });
}

/**
 * Met à jour le path avec les paramètres remplis
 */
function updateDiscoveryPathWithParams() {
    if (!discoverySelectedEndpoint) return;

    let path = discoverySelectedEndpoint.path;
    const queryParams = [];

    document.querySelectorAll('.discovery-param-value').forEach(input => {
        const name = input.dataset.paramName;
        const loc = input.dataset.paramIn;
        const val = input.value.trim();
        if (!val) return;
        if (loc === 'path') path = path.replace(`{${name}}`, encodeURIComponent(val));
        else if (loc === 'query') queryParams.push(`${encodeURIComponent(name)}=${encodeURIComponent(val)}`);
    });

    let fullPath = path;
    if (queryParams.length) fullPath += '?' + queryParams.join('&');
    document.getElementById('discoveryPath').value = fullPath;
}

/**
 * Génère un exemple de body depuis la spec OpenAPI
 */
function generateDiscoveryBodyExample(requestBody) {
    const textarea = document.getElementById('discoveryBody');
    try {
        const jsonContent = requestBody?.content?.['application/json'];
        if (jsonContent?.schema) {
            textarea.value = JSON.stringify(generateSchemaExample(jsonContent.schema), null, 2);
        } else if (jsonContent?.example) {
            textarea.value = JSON.stringify(jsonContent.example, null, 2);
        }
    } catch (e) {
        console.warn('Impossible de générer exemple body:', e);
    }
}

/**
 * Génère un exemple à partir d'un schéma JSON (récursif)
 */
function generateSchemaExample(schema, depth = 0) {
    if (depth > 5) return null;
    if (schema.example !== undefined) return schema.example;
    if (schema.$ref) return { '_ref': schema.$ref.split('/').pop() };

    switch (schema.type) {
        case 'object': {
            const obj = {};
            for (const [k, v] of Object.entries(schema.properties || {})) obj[k] = generateSchemaExample(v, depth + 1);
            return obj;
        }
        case 'array':
            return schema.items ? [generateSchemaExample(schema.items, depth + 1)] : [];
        case 'string':
            if (schema.enum) return schema.enum[0];
            if (schema.format === 'date') return '2024-01-01';
            if (schema.format === 'date-time') return '2024-01-01T00:00:00Z';
            if (schema.format === 'email') return 'user@example.com';
            return schema.default || 'string';
        case 'integer':
        case 'number':
            return schema.default || schema.minimum || 0;
        case 'boolean':
            return schema.default !== undefined ? schema.default : true;
        default:
            return null;
    }
}

/**
 * Ajoute une ligne de query param manuel
 */
function addDiscoveryQueryParam() {
    const container = document.getElementById('discoveryQueryParams');
    const row = document.createElement('div');
    row.className = 'row g-1 mb-1 discovery-qp-row';
    row.innerHTML = `
        <div class="col-5">
            <input type="text" class="form-control form-control-sm" placeholder="clé">
        </div>
        <div class="col-6">
            <input type="text" class="form-control form-control-sm" placeholder="valeur">
        </div>
        <div class="col-1">
            <button class="btn btn-outline-danger btn-sm py-0" onclick="this.closest('.discovery-qp-row').remove()">
                <i class="bi bi-x"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
}

// ─── Exécution de la requête ─────────────────────────────────────────

/**
 * Exécute la requête via le proxy backend
 */
async function executeDiscoveryRequest() {
    if (!discoverySelectedApp) {
        showToast('Erreur', 'Sélectionnez d\'abord une application', 'warning');
        return;
    }

    const method = document.getElementById('discoveryMethod').value;
    let path = document.getElementById('discoveryPath').value.trim();

    if (!path) {
        showToast('Erreur', 'Chemin de l\'endpoint requis', 'warning');
        return;
    }

    // Collecter les query params manuels (mode fallback)
    let queryParams = null;
    const qpRows = document.querySelectorAll('.discovery-qp-row');
    if (qpRows.length > 0) {
        queryParams = {};
        qpRows.forEach(row => {
            const inputs = row.querySelectorAll('input');
            const k = inputs[0]?.value.trim();
            const v = inputs[1]?.value.trim();
            if (k) queryParams[k] = v;
        });
        if (Object.keys(queryParams).length === 0) queryParams = null;
    }

    // Body
    let body = null;
    if (['POST', 'PUT', 'PATCH'].includes(method)) {
        const bodyText = document.getElementById('discoveryBody').value.trim();
        if (bodyText) {
            try {
                body = JSON.parse(bodyText);
            } catch (e) {
                showToast('Erreur', 'Corps JSON invalide', 'danger');
                return;
            }
        }
    }

    const btn = document.getElementById('discoveryExecBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>…';

    try {
        const result = await api.proxyRequest(
            discoverySelectedApp.slug,
            method,
            path,
            queryParams,
            null, // headers gérés par le proxy
            body
        );

        renderDiscoveryResults(result);

    } catch (error) {
        console.error('Erreur proxy:', error);
        showToast('Erreur', error.message, 'danger');

        // Afficher l'erreur dans la zone résultats
        document.getElementById('discoveryResultsArea').style.display = '';
        document.getElementById('discoveryResultStatus').textContent = 'Erreur';
        document.getElementById('discoveryResultStatus').className = 'badge bg-danger';
        document.getElementById('discoveryResultTime').textContent = '';
        document.getElementById('discoveryResultCount').textContent = '';
        document.getElementById('discoveryTableWrapper').classList.add('d-none');
        const raw = document.getElementById('discoveryResultRaw');
        raw.classList.remove('d-none');
        raw.textContent = `Erreur: ${error.message}`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Exécuter';
    }
}

// ─── Rendu des résultats ─────────────────────────────────────────────

/**
 * Affiche les résultats dans la zone dédiée
 */
function renderDiscoveryResults(result) {
    const area = document.getElementById('discoveryResultsArea');
    area.style.display = '';

    // Status badge
    const statusBadge = document.getElementById('discoveryResultStatus');
    statusBadge.textContent = `${result.status} ${result.status_text || ''}`.trim();
    statusBadge.className = `badge ${result.status >= 200 && result.status < 300 ? 'bg-success' : result.status >= 400 ? 'bg-danger' : 'bg-warning text-dark'}`;

    document.getElementById('discoveryResultTime').textContent = `${result.elapsed_ms}ms`;

    // Erreur de connexion ?
    if (result.error) {
        document.getElementById('discoveryResultCount').textContent = '';
        document.getElementById('discoveryTableWrapper').classList.add('d-none');
        const raw = document.getElementById('discoveryResultRaw');
        raw.classList.remove('d-none');
        raw.textContent = `Erreur: ${result.error}`;
        discoveryRawData = null;
        return;
    }

    // Déterminer les données
    const data = result.body != null ? result.body : result.body_text;
    discoveryRawData = data;
    discoveryVisibleColumns = null;

    // Essayer le rendu tableau
    if (data && typeof data === 'object') {
        // Si c'est un tableau d'objets → tableau multi-lignes
        let rows = null;
        if (Array.isArray(data)) {
            rows = data;
        } else if (Array.isArray(data.items)) {
            rows = data.items;
        } else if (Array.isArray(data.data)) {
            rows = data.data;
        } else if (Array.isArray(data.results)) {
            rows = data.results;
        }

        if (rows && rows.length > 0 && typeof rows[0] === 'object' && !Array.isArray(rows[0])) {
            renderDiscoveryTable(rows);
            return;
        }

        // Objet unique → tableau clé/valeur
        if (!Array.isArray(data)) {
            renderDiscoveryObjectTable(data);
            return;
        }
    }

    // Fallback : JSON brut
    document.getElementById('discoveryResultCount').textContent = '';
    document.getElementById('discoveryTableWrapper').classList.add('d-none');
    const raw = document.getElementById('discoveryResultRaw');
    raw.classList.remove('d-none');
    raw.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

/**
 * Rend un tableau multi-lignes (array d'objets)
 */
function renderDiscoveryTable(rows) {
    document.getElementById('discoveryTableWrapper').classList.remove('d-none');
    document.getElementById('discoveryResultRaw').classList.add('d-none');
    document.getElementById('discoveryResultCount').textContent = `${rows.length} résultat(s)`;

    // Collecter toutes les clés
    const allKeys = new Set();
    rows.forEach(row => {
        if (row && typeof row === 'object') Object.keys(row).forEach(k => allKeys.add(k));
    });
    const columns = Array.from(allKeys);

    // Initialiser les colonnes visibles (toutes par défaut)
    if (!discoveryVisibleColumns) {
        discoveryVisibleColumns = new Set(columns);
    }

    // Peupler le sélecteur de colonnes
    populateDiscoveryColumnSelector(columns);

    // Peupler le filtre par champ
    const fieldSelect = document.getElementById('discoveryFilterField');
    const prevField = fieldSelect.value;
    fieldSelect.innerHTML = '<option value="">Champ</option>';
    columns.forEach(col => {
        const opt = document.createElement('option');
        opt.value = col;
        opt.textContent = col;
        if (col === prevField) opt.selected = true;
        fieldSelect.appendChild(opt);
    });

    // Détecter les types par colonne
    const colTypes = {};
    columns.forEach(col => {
        colTypes[col] = detectDiscoveryFieldType(rows.map(r => r?.[col]));
    });

    // Construire le thead
    const thead = document.getElementById('discoveryTableHead');
    thead.innerHTML = '';
    const headerRow = document.createElement('tr');
    columns.forEach(col => {
        if (!discoveryVisibleColumns.has(col)) return;
        const th = document.createElement('th');
        th.className = 'discovery-th';
        const typeColors = { string: 'text-bg-secondary', number: 'text-bg-primary', boolean: 'text-bg-info', object: 'text-bg-warning', array: 'text-bg-warning', null: 'text-bg-light' };
        th.innerHTML = `${escapeHtml(col)} <span class="badge ${typeColors[colTypes[col]] || 'text-bg-light'}" style="font-size: 0.6rem;">${colTypes[col]}</span>`;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    // Construire le tbody
    const tbody = document.getElementById('discoveryTableBody');
    tbody.innerHTML = '';

    // Appliquer les filtres
    let filtered = applyDiscoveryDataFilters(rows, columns);
    document.getElementById('discoveryResultCount').textContent = `${filtered.length}/${rows.length} résultat(s)`;

    filtered.forEach(row => {
        const tr = document.createElement('tr');
        columns.forEach(col => {
            if (!discoveryVisibleColumns.has(col)) return;
            const td = document.createElement('td');
            td.className = 'discovery-td';
            const val = row?.[col];
            td.innerHTML = formatDiscoveryCellValue(val);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
}

/**
 * Rend un objet unique sous forme clé/valeur
 */
function renderDiscoveryObjectTable(obj) {
    document.getElementById('discoveryTableWrapper').classList.remove('d-none');
    document.getElementById('discoveryResultRaw').classList.add('d-none');
    document.getElementById('discoveryResultCount').textContent = `${Object.keys(obj).length} champ(s)`;

    // Pas de sélecteur de colonnes pour un objet
    document.getElementById('discoveryColumnSelector').innerHTML = '';

    const thead = document.getElementById('discoveryTableHead');
    thead.innerHTML = '<tr><th class="discovery-th">Champ</th><th class="discovery-th">Type</th><th class="discovery-th">Valeur</th></tr>';

    const tbody = document.getElementById('discoveryTableBody');
    tbody.innerHTML = '';

    for (const [key, val] of Object.entries(obj)) {
        const tr = document.createElement('tr');
        const typeName = val === null ? 'null' : Array.isArray(val) ? 'array' : typeof val;
        const typeColors = { string: 'text-bg-secondary', number: 'text-bg-primary', boolean: 'text-bg-info', object: 'text-bg-warning', array: 'text-bg-warning', null: 'text-bg-light' };
        tr.innerHTML = `
            <td class="discovery-td fw-semibold">${escapeHtml(key)}</td>
            <td class="discovery-td"><span class="badge ${typeColors[typeName] || 'text-bg-light'}" style="font-size: 0.65rem;">${typeName}</span></td>
            <td class="discovery-td">${formatDiscoveryCellValue(val)}</td>
        `;
        tbody.appendChild(tr);
    }
}

/**
 * Formate une valeur pour l'affichage dans une cellule
 */
function formatDiscoveryCellValue(val) {
    if (val === null || val === undefined) return '<span class="text-muted fst-italic">null</span>';
    if (typeof val === 'boolean') return `<span class="badge ${val ? 'text-bg-success' : 'text-bg-secondary'}">${val}</span>`;
    if (typeof val === 'number') return `<span class="text-primary">${val}</span>`;
    if (typeof val === 'object') {
        const json = JSON.stringify(val);
        if (json.length > 80) return `<code class="small" title="${escapeHtml(json)}">${escapeHtml(json.substring(0, 77))}…</code>`;
        return `<code class="small">${escapeHtml(json)}</code>`;
    }
    // String
    const str = String(val);
    if (str.length > 100) return `<span title="${escapeHtml(str)}">${escapeHtml(str.substring(0, 97))}…</span>`;
    return escapeHtml(str);
}

/**
 * Détecte le type dominant d'une colonne
 */
function detectDiscoveryFieldType(values) {
    const types = {};
    for (const v of values) {
        if (v === null || v === undefined) { types['null'] = (types['null'] || 0) + 1; continue; }
        if (Array.isArray(v)) { types['array'] = (types['array'] || 0) + 1; continue; }
        const t = typeof v;
        types[t] = (types[t] || 0) + 1;
    }
    // Retourner le type le plus fréquent (hors null)
    let best = 'null', bestCount = 0;
    for (const [t, c] of Object.entries(types)) {
        if (t !== 'null' && c > bestCount) { best = t; bestCount = c; }
    }
    return best;
}

// ─── Filtres ─────────────────────────────────────────────────────────

/**
 * Peuple le sélecteur de colonnes visibles
 */
function populateDiscoveryColumnSelector(columns) {
    const container = document.getElementById('discoveryColumnSelector');
    container.innerHTML = '';
    columns.forEach(col => {
        const div = document.createElement('div');
        div.className = 'form-check';
        div.innerHTML = `
            <input class="form-check-input" type="checkbox" id="disc_col_${escapeHtml(col)}"
                   value="${escapeHtml(col)}" ${discoveryVisibleColumns?.has(col) ? 'checked' : ''} onchange="toggleDiscoveryColumn('${escapeHtml(col)}')">
            <label class="form-check-label small" for="disc_col_${escapeHtml(col)}">${escapeHtml(col)}</label>
        `;
        container.appendChild(div);
    });
}

/**
 * Toggle la visibilité d'une colonne
 */
function toggleDiscoveryColumn(col) {
    if (!discoveryVisibleColumns) return;
    if (discoveryVisibleColumns.has(col)) {
        discoveryVisibleColumns.delete(col);
    } else {
        discoveryVisibleColumns.add(col);
    }
    applyDiscoveryFilters();
}

/**
 * Applique tous les filtres et re-rend le tableau
 */
function applyDiscoveryFilters() {
    if (!discoveryRawData) return;

    // Retrouver les rows
    let rows = null;
    if (Array.isArray(discoveryRawData)) {
        rows = discoveryRawData;
    } else if (typeof discoveryRawData === 'object') {
        rows = discoveryRawData.items || discoveryRawData.data || discoveryRawData.results;
    }

    if (rows && Array.isArray(rows) && rows.length > 0 && typeof rows[0] === 'object') {
        renderDiscoveryTable(rows);
    }
}

/**
 * Applique les filtres de données sur les rows
 */
function applyDiscoveryDataFilters(rows, columns) {
    let result = [...rows];

    // Filtre par champ
    const field = document.getElementById('discoveryFilterField')?.value;
    const op = document.getElementById('discoveryFilterOp')?.value;
    const val = document.getElementById('discoveryFilterValue')?.value?.trim();
    if (field && val) {
        result = result.filter(row => {
            const cellVal = String(row?.[field] ?? '').toLowerCase();
            const searchVal = val.toLowerCase();
            switch (op) {
                case 'eq': return cellVal === searchVal;
                case 'neq': return cellVal !== searchVal;
                case 'contains':
                default: return cellVal.includes(searchVal);
            }
        });
    }

    // Recherche texte globale
    const search = document.getElementById('discoveryFilterSearch')?.value?.trim().toLowerCase();
    if (search) {
        result = result.filter(row => {
            return columns.some(col => {
                const v = row?.[col];
                return String(v ?? '').toLowerCase().includes(search);
            });
        });
    }

    return result;
}

/**
 * Copie le JSON brut dans le presse-papier
 */
function copyDiscoveryResult() {
    if (discoveryRawData) {
        const text = typeof discoveryRawData === 'string' ? discoveryRawData : JSON.stringify(discoveryRawData, null, 2);
        navigator.clipboard.writeText(text);
        showToast('Copié', 'Résultat copié dans le presse-papier', 'success');
    }
}

/**
 * Efface les résultats
 */
function clearDiscoveryResult() {
    document.getElementById('discoveryResultsArea').style.display = 'none';
    document.getElementById('discoveryTableBody').innerHTML = '';
    document.getElementById('discoveryTableHead').innerHTML = '';
    document.getElementById('discoveryResultRaw').textContent = '';
    document.getElementById('discoveryFilterSearch').value = '';
    document.getElementById('discoveryFilterField').value = '';
    document.getElementById('discoveryFilterValue').value = '';
    discoveryRawData = null;
    discoveryVisibleColumns = null;
}

// ─── Discovery : M2M Setup ───────────────────────────────────────────

/**
 * Bascule entre le mode requêteur et le mode M2M Setup
 */
function switchDiscoveryMode(mode) {
    discoveryMode = mode;
    const requester = document.getElementById('discoveryRequester');
    const results = document.getElementById('discoveryResultsArea');
    const m2mPanel = document.getElementById('discoveryM2MPanel');
    const btnReq = document.getElementById('discoveryModeRequester');
    const btnM2M = document.getElementById('discoveryModeM2M');

    if (mode === 'm2m') {
        requester.classList.add('d-none');
        results.style.display = 'none';
        m2mPanel.classList.remove('d-none');
        btnReq.classList.remove('active');
        btnM2M.classList.add('active');
        // Charger le M2M si une app est sélectionnée
        if (discoverySelectedApp) {
            loadM2MSetup(discoverySelectedApp.slug);
        }
    } else {
        m2mPanel.classList.add('d-none');
        requester.classList.remove('d-none');
        btnReq.classList.add('active');
        btnM2M.classList.remove('active');
    }
}

/**
 * Charge le bloc M2M Setup pour une app
 */
async function loadM2MSetup(slug) {
    const content = document.getElementById('discoveryM2MContent');
    const copyBtn = document.getElementById('discoveryM2MCopyBtn');

    // Si déjà en cache pour cette app
    if (discoveryM2MMarkdown && discoverySelectedApp?.slug === slug) {
        return;
    }

    content.innerHTML = `
        <div class="text-center text-muted py-5">
            <div class="spinner-border spinner-border-sm"></div>
            <p class="mt-2 small">Génération du bloc M2M…</p>
        </div>`;
    copyBtn.disabled = true;

    try {
        const result = await api.getM2MSetup(slug);
        discoveryM2MMarkdown = result.markdown;

        content.innerHTML = `<pre class="discovery-m2m-pre mb-0">${escapeHtml(result.markdown)}</pre>`;
        copyBtn.disabled = false;
    } catch (error) {
        console.error('Erreur chargement M2M Setup:', error);
        content.innerHTML = `
            <div class="text-center text-danger py-5">
                <i class="bi bi-exclamation-triangle fs-3 d-block mb-2"></i>
                Erreur lors du chargement du setup M2M
                <div class="small text-muted mt-1">${escapeHtml(error.message || String(error))}</div>
            </div>`;
        copyBtn.disabled = true;
    }
}

/**
 * Copie le Markdown M2M dans le presse-papier
 */
function copyM2MSetup() {
    if (!discoveryM2MMarkdown) return;
    navigator.clipboard.writeText(discoveryM2MMarkdown).then(() => {
        showToast('Copié', 'Bloc M2M copié dans le presse-papier — collez-le dans votre IA coder', 'success');
    }).catch(() => {
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = discoveryM2MMarkdown;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('Copié', 'Bloc M2M copié dans le presse-papier', 'success');
    });
}

// ===== Cloner / Migrer — Export & Import d'environnement =====

let backupFileData = null;
let backupFileRef = null;  // Référence au fichier original pour upload ZIP
let backupIsZip = false;   // Le fichier sélectionné est-il un ZIP ?

/**
 * Charge le résumé de l'environnement courant (appelé à l'ouverture de l'onglet)
 */
async function loadBackupInfo() {
    // Reset du formulaire d'import
    document.getElementById('importBackupFile').value = '';
    document.getElementById('backupPreview').classList.add('d-none');
    document.getElementById('importBackupBtn').disabled = true;
    document.getElementById('importResultCard').classList.add('d-none');
    backupFileData = null;
    backupFileRef = null;
    backupIsZip = false;

    // Charger les compteurs de l'environnement courant
    try {
        const usersCount = (await api.request('/admin/users')).length;
        const appsCount = allApps.length || (await api.request('/apps')).length;
        const saCount = allServiceAccounts.length || 0;
        // Compteur de rôles : somme des rôles de tous les users
        let rolesCount = 0;
        try {
            const users = await api.request('/admin/users');
            rolesCount = users.reduce((sum, u) => sum + (u.apps || []).length, 0);
        } catch (_) { /* ignore */ }

        document.getElementById('envSummaryUsers').textContent = usersCount;
        document.getElementById('envSummaryApps').textContent = appsCount;
        document.getElementById('envSummaryRoles').textContent = rolesCount;
        document.getElementById('envSummarySA').textContent = saCount;
    } catch (e) {
        console.warn('Impossible de charger le résumé environnement', e);
    }
}

/**
 * Télécharge l'export du backup
 */
async function downloadBackupExport() {
    try {
        const includeJwt = document.getElementById('exportIncludeJwt').checked;
        const includeSaSecrets = document.getElementById('exportIncludeSaSecrets').checked;
        const includeAppBackups = document.getElementById('exportIncludeAppBackups').checked;
        
        showToast('Export', 'Génération de l\'export en cours...', 'info');
        
        const data = await api.exportBackup(includeJwt, includeSaSecrets, includeAppBackups);
        
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        
        if (includeAppBackups && data instanceof Blob) {
            // Télécharger le ZIP
            const url = URL.createObjectURL(data);
            const a = document.createElement('a');
            a.href = url;
            a.download = `hub_export_${timestamp}.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Export terminé', 'Archive ZIP avec backups d\'applications téléchargée', 'success');
        } else {
            // Télécharger le JSON
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `hub_export_${timestamp}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Export terminé', `${data.users.length} utilisateurs, ${data.applications.length} apps, ${(data.service_accounts || []).length} SA`, 'success');
        }
        
    } catch (error) {
        console.error('Erreur export backup:', error);
        showToast('Erreur', error.message, 'danger');
    }
}

/**
 * Prévisualise le fichier de backup sélectionné
 */
async function previewBackupFile() {
    const fileInput = document.getElementById('importBackupFile');
    const file = fileInput.files[0];
    
    if (!file) {
        document.getElementById('backupPreview').classList.add('d-none');
        document.getElementById('importBackupBtn').disabled = true;
        backupFileData = null;
        backupFileRef = null;
        backupIsZip = false;
        return;
    }
    
    backupFileRef = file;
    
    // Déterminer si c'est un ZIP en regardant l'extension ou les magic bytes
    const isZipByName = file.name.toLowerCase().endsWith('.zip');
    
    if (isZipByName) {
        // Fichier ZIP : utiliser l'endpoint serveur pour le preview
        try {
            const preview = await api.previewBackupUpload(file);
            backupIsZip = true;
            
            // Stocker les données de preview (pas les données complètes)
            backupFileData = {
                _isZip: true,
                users: { length: preview.users_count },
                applications: { length: preview.apps_count },
                service_accounts: { length: preview.service_accounts_count },
                app_backups: { length: preview.app_backups_count },
                jwt_private_key_b64: preview.has_jwt_keys,
                version: preview.version,
                exported_at: preview.exported_at,
                exported_by: preview.exported_by,
                app_backups_by_app: preview.app_backups_by_app
            };
            
            document.getElementById('previewUsers').textContent = preview.users_count;
            document.getElementById('previewApps').textContent = preview.apps_count;
            document.getElementById('previewSA').textContent = preview.service_accounts_count;
            document.getElementById('previewJwt').textContent = preview.has_jwt_keys ? 'Oui' : 'Non';
            document.getElementById('previewBackups').textContent = preview.app_backups_count;
            document.getElementById('previewVersion').textContent = preview.version || '–';
            
            // Mettre à jour les compteurs des checkboxes de sélection
            document.getElementById('importCountUsers').textContent = preview.users_count;
            document.getElementById('importCountApps').textContent = preview.apps_count;
            document.getElementById('importCountRoles').textContent = preview.user_app_roles_count || 0;
            document.getElementById('importCountSA').textContent = preview.service_accounts_count;
            document.getElementById('importCountBackups').textContent = preview.app_backups_count;
            
            if (preview.exported_at) {
                const date = new Date(preview.exported_at);
                document.getElementById('previewDate').textContent = date.toLocaleString('fr-FR');
            } else {
                document.getElementById('previewDate').textContent = '–';
            }
            document.getElementById('previewBy').textContent = preview.exported_by || '-';
            
            document.getElementById('backupPreview').classList.remove('d-none');
            document.getElementById('importBackupBtn').disabled = false;
            
        } catch (error) {
            console.error('Erreur preview ZIP:', error);
            showToast('Erreur', 'Impossible de lire l\'archive ZIP: ' + error.message, 'danger');
            document.getElementById('backupPreview').classList.add('d-none');
            document.getElementById('importBackupBtn').disabled = true;
            backupFileData = null;
            backupFileRef = null;
            backupIsZip = false;
        }
    } else {
        // Fichier JSON classique
        backupIsZip = false;
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                backupFileData = JSON.parse(e.target.result);
                
                document.getElementById('previewUsers').textContent = (backupFileData.users || []).length;
                document.getElementById('previewApps').textContent = (backupFileData.applications || []).length;
                document.getElementById('previewSA').textContent = (backupFileData.service_accounts || []).length;
                document.getElementById('previewJwt').textContent = backupFileData.jwt_private_key_b64 ? 'Oui' : 'Non';
                document.getElementById('previewBackups').textContent = (backupFileData.app_backups || []).length;
                document.getElementById('previewVersion').textContent = backupFileData.version || '–';
                
                // Mettre à jour les compteurs des checkboxes de sélection
                document.getElementById('importCountUsers').textContent = (backupFileData.users || []).length;
                document.getElementById('importCountApps').textContent = (backupFileData.applications || []).length;
                document.getElementById('importCountRoles').textContent = (backupFileData.user_app_roles || []).length;
                document.getElementById('importCountSA').textContent = (backupFileData.service_accounts || []).length;
                document.getElementById('importCountBackups').textContent = (backupFileData.app_backups || []).length;
                
                if (backupFileData.exported_at) {
                    const date = new Date(backupFileData.exported_at);
                    document.getElementById('previewDate').textContent = date.toLocaleString('fr-FR');
                } else {
                    document.getElementById('previewDate').textContent = '–';
                }
                document.getElementById('previewBy').textContent = backupFileData.exported_by || '-';
                
                document.getElementById('backupPreview').classList.remove('d-none');
                document.getElementById('importBackupBtn').disabled = false;
                
            } catch (error) {
                console.error('Erreur parsing backup:', error);
                showToast('Erreur', 'Fichier JSON invalide', 'danger');
                document.getElementById('backupPreview').classList.add('d-none');
                document.getElementById('importBackupBtn').disabled = true;
                backupFileData = null;
            }
        };
        reader.readAsText(file);
    }
}

/**
 * Exécute l'import du backup
 */
async function executeBackupImport() {
    if (!backupFileData) {
        showToast('Erreur', 'Aucun fichier de backup sélectionné', 'warning');
        return;
    }
    
    const importMode = document.getElementById('importMode').value;
    const restoreJwt = document.getElementById('importRestoreJwt').checked;
    
    // Catégories sélectionnées
    const includeOptions = {
        include_users: document.getElementById('importIncludeUsers').checked,
        include_applications: document.getElementById('importIncludeApps').checked,
        include_user_app_roles: document.getElementById('importIncludeRoles').checked,
        include_service_accounts: document.getElementById('importIncludeSA').checked,
        include_app_backups: document.getElementById('importIncludeBackups').checked
    };
    
    // Compteurs pour la confirmation
    const usersCount = backupIsZip ? backupFileData.users.length : (backupFileData.users || []).length;
    const appsCount = backupIsZip ? backupFileData.applications.length : (backupFileData.applications || []).length;
    const saCount = backupIsZip ? backupFileData.service_accounts.length : (backupFileData.service_accounts || []).length;
    const backupsCount = backupIsZip ? backupFileData.app_backups.length : (backupFileData.app_backups || []).length;
    
    let confirmMsg = `Vous allez importer:`;
    if (includeOptions.include_users) confirmMsg += `\n- ${usersCount} utilisateurs`;
    if (includeOptions.include_applications) confirmMsg += `\n- ${appsCount} applications`;
    if (includeOptions.include_service_accounts) confirmMsg += `\n- ${saCount} service accounts`;
    
    if (includeOptions.include_app_backups && backupsCount > 0) {
        confirmMsg += `\n- ${backupsCount} backups d'applications`;
    }
    
    // Catégories exclues
    const excluded = [];
    if (!includeOptions.include_users) excluded.push('utilisateurs');
    if (!includeOptions.include_applications) excluded.push('applications');
    if (!includeOptions.include_user_app_roles) excluded.push('associations user-app');
    if (!includeOptions.include_service_accounts) excluded.push('service accounts');
    if (!includeOptions.include_app_backups) excluded.push('backups apps');
    if (excluded.length > 0) {
        confirmMsg += `\n\n🚫 Exclus: ${excluded.join(', ')}`;
    }
    
    confirmMsg += `\n\nMode: ${importMode.toUpperCase()}`;
    
    if (restoreJwt) {
        confirmMsg += '\n⚠️ Les clés JWT seront restaurées (redémarrage nécessaire)';
    }
    
    confirmMsg += '\n\nContinuer ?';
    
    if (!confirm(confirmMsg)) {
        return;
    }
    
    try {
        const btn = document.getElementById('importBackupBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Import en cours...';
        
        let result;
        
        if (backupIsZip) {
            // Upload du fichier ZIP via le nouvel endpoint
            result = await api.importBackupFile(backupFileRef, importMode, restoreJwt, includeOptions);
        } else {
            // Import JSON classique
            result = await api.importBackup(backupFileData, importMode, restoreJwt, includeOptions);
        }
        
        displayImportResult(result);
        
        if (result.success) {
            showToast('Import réussi', 'Les données ont été importées avec succès', 'success');
            loadAdminData();
            loadUsersPageData();
        } else {
            showToast('Import partiel', 'L\'import a terminé avec des erreurs', 'warning');
        }
        
    } catch (error) {
        console.error('Erreur import backup:', error);
        showToast('Erreur', error.message, 'danger');
        
        displayImportResult({
            success: false,
            errors: [error.message],
            warnings: []
        });
    } finally {
        const btn = document.getElementById('importBackupBtn');
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-upload me-2"></i>Lancer l\'import';
    }
}

/**
 * Affiche le résultat de l'import
 */
function displayImportResult(result) {
    const card = document.getElementById('importResultCard');
    const header = document.getElementById('importResultHeader');
    const statsDiv = document.getElementById('importResultStats');
    const warningsDiv = document.getElementById('importResultWarnings');
    const errorsDiv = document.getElementById('importResultErrors');
    
    // Header styling
    if (result.success) {
        header.className = 'card-header bg-success text-white';
        header.innerHTML = '<i class="bi bi-check-circle me-2"></i>Import réussi';
    } else {
        header.className = 'card-header bg-danger text-white';
        header.innerHTML = '<i class="bi bi-x-circle me-2"></i>Import échoué';
    }
    
    // Stats
    statsDiv.innerHTML = `
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Utilisateurs</div>
                <span class="badge bg-success">${result.users_created || 0} créés</span>
                <span class="badge bg-info">${result.users_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.users_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Applications</div>
                <span class="badge bg-success">${result.apps_created || 0} créées</span>
                <span class="badge bg-info">${result.apps_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.apps_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Service Accounts</div>
                <span class="badge bg-success">${result.service_accounts_created || 0} créés</span>
                <span class="badge bg-info">${result.service_accounts_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.service_accounts_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-3">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Backups Apps</div>
                <span class="badge bg-success">${result.app_backups_created || 0} créés</span>
                <span class="badge bg-secondary">${result.app_backups_skipped || 0} skip</span>
                ${(result.app_backups_errors || 0) > 0 ? `<span class="badge bg-danger">${result.app_backups_errors} err</span>` : ''}
            </div>
        </div>
    `;
    
    // Warnings
    if (result.warnings && result.warnings.length > 0) {
        document.getElementById('importWarningsList').innerHTML = result.warnings.map(w => `<li>${w}</li>`).join('');
        warningsDiv.classList.remove('d-none');
    } else {
        warningsDiv.classList.add('d-none');
    }
    
    // Errors
    if (result.errors && result.errors.length > 0) {
        document.getElementById('importErrorsList').innerHTML = result.errors.map(e => `<li>${e}</li>`).join('');
        errorsDiv.classList.remove('d-none');
    } else {
        errorsDiv.classList.add('d-none');
    }
    
    card.classList.remove('d-none');
}


// =============================================================================
// APP BACKUPS - SAUVEGARDES CENTRALISÉES DES APPLICATIONS
// =============================================================================

let appBackupConfigModal = null;
let appBackupsListModal = null;

/**
 * Charge la vue d'ensemble des sauvegardes de toutes les applications
 */
async function loadAppBackupsOverview() {
    try {
        const data = await api.request('/admin/backups');
        
        // Mettre à jour les stats globales
        document.getElementById('statsAppsCount').textContent = data.total_apps || 0;
        document.getElementById('statsTotalBackups').textContent = data.total_backups || 0;
        document.getElementById('statsTotalSize').textContent = data.total_size_human || '0 B';
        
        // Compter les apps planifiées
        const scheduledCount = (data.apps || []).filter(a => a.backup_mode === 'scheduled').length;
        document.getElementById('statsScheduledApps').textContent = scheduledCount;
        
        // Générer le tableau
        const tbody = document.getElementById('appBackupsTableBody');
        
        if (!data.apps || data.apps.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted py-4">
                        <i class="bi bi-inbox me-2"></i>Aucune application trouvée
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = data.apps.map(app => {
            // Badge du mode
            let modeBadge;
            switch (app.backup_mode) {
                case 'disabled':
                    modeBadge = '<span class="badge bg-secondary">Désactivé</span>';
                    break;
                case 'manual':
                    modeBadge = '<span class="badge bg-info">Manuel</span>';
                    break;
                case 'scheduled':
                    modeBadge = `<span class="badge bg-success">Planifié</span>`;
                    break;
                default:
                    modeBadge = '<span class="badge bg-secondary">Manuel</span>';
            }
            
            // Dernier backup
            let lastBackupInfo = '-';
            if (app.last_backup_at) {
                const lastDate = new Date(app.last_backup_at);
                const statusIcon = app.last_backup_status === 'success' ? '✅' : 
                                   app.last_backup_status === 'failed' ? '❌' : '⏳';
                lastBackupInfo = `${statusIcon} ${lastDate.toLocaleDateString('fr-FR')} ${lastDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
            }
            
            // Prochain backup
            let nextBackupInfo = '-';
            if (app.backup_mode === 'scheduled' && app.next_backup_at) {
                const nextDate = new Date(app.next_backup_at);
                nextBackupInfo = `${nextDate.toLocaleDateString('fr-FR')} ${nextDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
            } else if (app.backup_mode === 'scheduled' && app.backup_cron) {
                nextBackupInfo = `<code class="small">${app.backup_cron}</code>`;
            }
            
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(app.app_name)}</strong>
                        <br><small class="text-muted">${escapeHtml(app.app_slug)}</small>
                    </td>
                    <td>${modeBadge}</td>
                    <td><small>${lastBackupInfo}</small></td>
                    <td><small>${nextBackupInfo}</small></td>
                    <td>
                        <span class="badge bg-light text-dark">${app.backups_count}</span>
                    </td>
                    <td>${app.total_size_human}</td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">
                            <button class="btn btn-outline-success" onclick="showTriggerBackupConfirm('${app.app_slug}', '${escapeHtml(app.app_name).replace(/'/g, "\\'")}')"
                             title="Déclencher sauvegarde" ${app.backup_mode === 'disabled' ? 'style="display: none;"' : ''}>
                                <i class="bi bi-play-circle"></i>
                            </button>
                            <button class="btn btn-outline-primary" onclick="showAppBackupsList('${app.app_slug}')" title="Voir les sauvegardes">
                                <i class="bi bi-hdd-stack"></i>
                            </button>
                            <button class="btn btn-outline-secondary" onclick="showAppBackupConfig('${app.app_slug}', '${escapeHtml(app.app_name).replace(/'/g, "\\'")}')"
                             title="Configurer">
                                <i class="bi bi-gear"></i>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        
    } catch (error) {
        console.error('Erreur chargement backups apps:', error);
        showToast('Erreur', 'Impossible de charger les sauvegardes', 'danger');
    }
}

/**
 * Affiche la modale de configuration de backup pour une application
 */
async function showAppBackupConfig(slug, name) {
    // Initialiser la modale si nécessaire
    if (!appBackupConfigModal) {
        appBackupConfigModal = new bootstrap.Modal(document.getElementById('appBackupConfigModal'));
    }
    
    document.getElementById('backupConfigAppSlug').value = slug;
    document.getElementById('backupConfigAppName').textContent = name;
    
    try {
        // Charger la config actuelle
        const config = await api.request(`/apps/${slug}/backup/config`);
        
        document.getElementById('backupConfigMode').value = config.backup_mode || 'manual';
        document.getElementById('backupConfigCron').value = config.backup_cron || '';
        document.getElementById('backupConfigRetention').value = config.backup_retention ?? 5;
        
        toggleCronInput();
        appBackupConfigModal.show();
        
    } catch (error) {
        console.error('Erreur chargement config backup:', error);
        showToast('Erreur', 'Impossible de charger la configuration', 'danger');
    }
}

/**
 * Affiche/masque le champ cron selon le mode
 */
function toggleCronInput() {
    const mode = document.getElementById('backupConfigMode').value;
    const cronGroup = document.getElementById('cronInputGroup');
    cronGroup.style.display = mode === 'scheduled' ? 'block' : 'none';
}

/**
 * Enregistre la configuration de backup
 */
async function saveAppBackupConfig(event) {
    event.preventDefault();
    
    const slug = document.getElementById('backupConfigAppSlug').value;
    const config = {
        backup_mode: document.getElementById('backupConfigMode').value,
        backup_cron: document.getElementById('backupConfigCron').value || null,
        backup_retention: parseInt(document.getElementById('backupConfigRetention').value) || 5
    };
    
    try {
        await api.request(`/apps/${slug}/backup/config`, {
            method: 'PUT',
            body: JSON.stringify(config)
        });
        
        showToast('Succès', 'Configuration de sauvegarde mise à jour', 'success');
        appBackupConfigModal.hide();
        loadAppBackupsOverview();
        
    } catch (error) {
        console.error('Erreur sauvegarde config:', error);
        showToast('Erreur', error.message || 'Impossible de sauvegarder', 'danger');
    }
}

/**
 * Affiche la liste des backups d'une application
 */
async function showAppBackupsList(slug) {
    // Initialiser la modale si nécessaire
    if (!appBackupsListModal) {
        appBackupsListModal = new bootstrap.Modal(document.getElementById('appBackupsListModal'));
    }
    
    document.getElementById('backupsListAppSlug').value = slug;
    
    try {
        const data = await api.request(`/apps/${slug}/backup`);
        
        document.getElementById('backupsListAppName').textContent = data.app_name;
        
        // Config résumé
        const modeLabels = {
            'disabled': 'Désactivé',
            'manual': 'Manuel',
            'scheduled': 'Planifié'
        };
        document.getElementById('backupsListMode').textContent = modeLabels[data.backup_config?.backup_mode] || 'Manuel';
        document.getElementById('backupsListCron').textContent = data.backup_config?.backup_cron || '-';
        document.getElementById('backupsListRetention').textContent = data.backup_config?.backup_retention ?? 5;
        
        // Afficher/masquer le bouton de déclenchement selon le mode
        const triggerBtn = document.getElementById('triggerBackupBtn');
        if (triggerBtn) {
            triggerBtn.style.display = data.backup_config?.backup_mode !== 'disabled' ? '' : 'none';
        }
        
        // Liste des backups
        const tbody = document.getElementById('backupsListBody');
        
        if (!data.backups || data.backups.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-muted py-3">
                        <i class="bi bi-inbox me-1"></i>Aucune sauvegarde disponible
                    </td>
                </tr>
            `;
        } else {
            tbody.innerHTML = data.backups.map(backup => {
                const date = new Date(backup.created_at);
                const dateStr = `${date.toLocaleDateString('fr-FR')} ${date.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
                const escapedAppName = escapeHtml(data.app_name).replace(/'/g, "\\'");
                
                // Afficher le contenu: description si disponible, sinon compteurs
                let contentDisplay = '';
                if (backup.description && backup.description.trim()) {
                    contentDisplay = backup.description;
                } else if (backup.total_items > 0 || backup.total_files > 0 || backup.total_preferences > 0) {
                    const parts = [];
                    if (backup.total_items > 0) parts.push(`${backup.total_items} items`);
                    if (backup.total_preferences > 0) parts.push(`${backup.total_preferences} prefs`);
                    if (backup.total_files > 0) parts.push(`${backup.total_files} fichiers`);
                    contentDisplay = parts.join(', ');
                } else {
                    contentDisplay = 'Backup vide';
                }
                
                return `
                    <tr>
                        <td>
                            <small>${dateStr}</small>
                        </td>
                        <td><small>${backup.app_version || '-'}</small></td>
                        <td>
                            <small class="text-muted" title="${escapeHtml(backup.description || '')}">
                                ${escapeHtml(contentDisplay)}
                            </small>
                        </td>
                        <td><small>${backup.total_size_human}</small></td>
                        <td>
                            <span class="badge bg-light text-dark">${backup.created_by_type}</span>
                        </td>
                        <td class="text-end">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-warning" onclick="showRestoreBackupModal('${slug}', '${backup.backup_id}', '${escapedAppName}')" title="Restaurer">
                                    <i class="bi bi-arrow-counterclockwise"></i>
                                </button>
                                <button class="btn btn-outline-success" onclick="exportEncryptedBackup('${slug}', '${backup.backup_id}')" title="Exporter chiffré">
                                    <i class="bi bi-shield-lock"></i>
                                </button>
                                <button class="btn btn-outline-danger" onclick="deleteAppBackup('${slug}', '${backup.backup_id}', '${escapedAppName}', '${dateStr}')" title="Supprimer">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }
        
        appBackupsListModal.show();
        
    } catch (error) {
        console.error('Erreur chargement backups:', error);
        showToast('Erreur', 'Impossible de charger les sauvegardes', 'danger');
    }
}

// =============================================================================
// RESTAURATION DE BACKUPS
// =============================================================================

let restoreBackupModal = null;
let importRestoreModal = null;

/**
 * Affiche le modal de restauration pour un backup existant
 */
function showRestoreBackupModal(slug, backupId, appName) {
    if (!restoreBackupModal) {
        restoreBackupModal = new bootstrap.Modal(document.getElementById('restoreBackupModal'));
    }
    
    document.getElementById('restoreBackupSlug').value = slug;
    document.getElementById('restoreBackupId').value = backupId;
    document.getElementById('restoreAppName').textContent = appName;
    
    // Réinitialiser les options
    document.getElementById('restoreClearExisting').checked = false;
    document.getElementById('restoreItems').checked = true;
    document.getElementById('restorePreferences').checked = true;
    document.getElementById('restoreFiles').checked = true;
    document.getElementById('restoreTestData').checked = true;
    
    restoreBackupModal.show();
}

/**
 * Exécute la restauration d'un backup existant vers l'application
 */
async function executeRestore() {
    const slug = document.getElementById('restoreBackupSlug').value;
    const backupId = document.getElementById('restoreBackupId').value;
    
    if (!slug || !backupId) return;
    
    const options = {
        clear_existing: document.getElementById('restoreClearExisting').checked,
        restore_items: document.getElementById('restoreItems').checked,
        restore_preferences: document.getElementById('restorePreferences').checked,
        restore_files: document.getElementById('restoreFiles').checked,
        restore_test_data: document.getElementById('restoreTestData').checked
    };
    
    // Confirmation si clear_existing
    if (options.clear_existing) {
        if (!confirm('⚠️ ATTENTION : Toutes les données existantes de l\'application seront SUPPRIMÉES avant la restauration.\n\nContinuer ?')) {
            return;
        }
    }
    
    restoreBackupModal.hide();
    showToast('Restauration', 'Restauration en cours...', 'info');
    
    try {
        const result = await api.request(`/apps/${slug}/backup/${backupId}/restore`, {
            method: 'POST',
            body: JSON.stringify(options)
        });
        
        if (result.success) {
            let message = `Restauration terminée : ${result.items_restored} items, ${result.preferences_restored} prefs, ${result.files_restored} fichiers`;
            if (result.warnings && result.warnings.length > 0) {
                message += `\n⚠️ ${result.warnings.length} avertissement(s)`;
            }
            showToast('Succès', message, 'success');
        } else {
            showToast('Attention', result.message || 'Restauration avec erreurs', 'warning');
        }
        
        // Afficher les erreurs éventuelles
        if (result.errors && result.errors.length > 0) {
            console.warn('Erreurs de restauration:', result.errors);
        }
        
    } catch (error) {
        console.error('Erreur restauration:', error);
        showToast('Erreur', error.message || 'Impossible de restaurer le backup', 'danger');
    }
}

/**
 * Affiche le modal d'import et restauration depuis fichier
 */
function showImportRestoreModal() {
    if (!importRestoreModal) {
        importRestoreModal = new bootstrap.Modal(document.getElementById('importRestoreModal'));
    }
    
    const slug = document.getElementById('backupsListAppSlug').value;
    document.getElementById('importRestoreSlug').value = slug;
    
    // Réinitialiser
    document.getElementById('importRestoreFile').value = '';
    document.getElementById('importRestorePreview').classList.add('d-none');
    document.getElementById('executeImportRestoreBtn').disabled = true;
    document.getElementById('importClearExisting').checked = false;
    document.getElementById('importRestoreItems').checked = true;
    document.getElementById('importRestorePreferences').checked = true;
    document.getElementById('importRestoreFiles').checked = true;
    document.getElementById('importRestoreTestData').checked = true;
    
    importRestoreModal.show();
}

/**
 * Prévisualise le fichier d'import sélectionné
 */
function previewImportRestoreFile() {
    const fileInput = document.getElementById('importRestoreFile');
    const file = fileInput.files[0];
    
    if (!file) {
        document.getElementById('importRestorePreview').classList.add('d-none');
        document.getElementById('executeImportRestoreBtn').disabled = true;
        return;
    }
    
    // Vérifier l'extension
    if (!file.name.endsWith('.zip') && !file.name.endsWith('.encrypted')) {
        showToast('Erreur', 'Le fichier doit être un ZIP ou un fichier chiffré (.encrypted)', 'warning');
        fileInput.value = '';
        document.getElementById('importRestorePreview').classList.add('d-none');
        document.getElementById('executeImportRestoreBtn').disabled = true;
        return;
    }
    
    // Afficher l'aperçu
    document.getElementById('importRestoreFilename').textContent = file.name;
    document.getElementById('importRestoreFilesize').textContent = formatFileSize(file.size);
    document.getElementById('importRestorePreview').classList.remove('d-none');
    document.getElementById('executeImportRestoreBtn').disabled = false;
}

/**
 * Formate une taille de fichier
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/**
 * Exécute l'import et restauration depuis fichier
 */
async function executeImportRestore() {
    const slug = document.getElementById('importRestoreSlug').value;
    const fileInput = document.getElementById('importRestoreFile');
    const file = fileInput.files[0];
    
    if (!slug || !file) return;
    
    const clearExisting = document.getElementById('importClearExisting').checked;
    
    // Confirmation si clear_existing
    if (clearExisting) {
        if (!confirm('⚠️ ATTENTION : Toutes les données existantes de l\'application seront SUPPRIMÉES avant la restauration.\n\nContinuer ?')) {
            return;
        }
    }
    
    importRestoreModal.hide();
    showToast('Import', 'Envoi et restauration en cours...', 'info');
    
    try {
        // Préparer le FormData
        const formData = new FormData();
        formData.append('file', file);
        formData.append('clear_existing', clearExisting);
        formData.append('restore_items', document.getElementById('importRestoreItems').checked);
        formData.append('restore_preferences', document.getElementById('importRestorePreferences').checked);
        formData.append('restore_files', document.getElementById('importRestoreFiles').checked);
        formData.append('restore_test_data', document.getElementById('importRestoreTestData').checked);
        
        // Envoyer la requête
        const response = await fetch(`/api/apps/${slug}/backup/restore/upload`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${api.token}`
            },
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Erreur lors de l\'import');
        }
        
        const result = await response.json();
        
        if (result.success) {
            let message = `Restauration terminée : ${result.items_restored} items, ${result.preferences_restored} prefs, ${result.files_restored} fichiers`;
            showToast('Succès', message, 'success');
        } else {
            showToast('Attention', result.message || 'Restauration avec erreurs', 'warning');
        }
        
        // Rafraîchir la liste des backups
        showAppBackupsList(slug);
        
    } catch (error) {
        console.error('Erreur import restauration:', error);
        showToast('Erreur', error.message || 'Impossible d\'importer le backup', 'danger');
    }
}

/**
 * Exporte et télécharge un backup chiffré
 */
async function exportEncryptedBackup(slug, backupId) {
    try {
        showToast('Chiffrement', 'Chiffrement du backup en cours...', 'info');
        
        // D'abord, chiffrer le backup
        const exportResult = await api.request(`/apps/${slug}/backup/${backupId}/export`);
        
        if (!exportResult.success) {
            throw new Error(exportResult.message || 'Erreur de chiffrement');
        }
        
        // Ensuite, télécharger le fichier chiffré
        const url = `/api/apps/${slug}/backup/${backupId}/export/download`;
        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${api.token}`
            }
        });
        
        if (!response.ok) {
            throw new Error('Erreur lors du téléchargement');
        }
        
        const blob = await response.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = exportResult.encrypted_filename || `backup_${slug}_${backupId}.encrypted`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
        
        showToast('Succès', `Backup chiffré exporté (${exportResult.encrypted_size_human})`, 'success');
        
        // Rafraîchir la liste pour voir le statut is_encrypted
        showAppBackupsList(slug);
        
    } catch (error) {
        console.error('Erreur export chiffré:', error);
        showToast('Erreur', error.message || 'Impossible d\'exporter le backup', 'danger');
    }
}

/**
 * Variables pour la suppression de backup
 */
let confirmDeleteBackupModal = null;

/**
 * Affiche le modal de confirmation de suppression d'un backup
 */
function deleteAppBackup(slug, backupId, appName, backupDate) {
    if (!confirmDeleteBackupModal) {
        confirmDeleteBackupModal = new bootstrap.Modal(document.getElementById('confirmDeleteBackupModal'));
    }
    
    // Stocker les infos du backup à supprimer
    document.getElementById('deleteBackupSlug').value = slug;
    document.getElementById('deleteBackupId').value = backupId;
    document.getElementById('deleteBackupAppName').textContent = appName || slug;
    document.getElementById('deleteBackupDate').textContent = backupDate || '-';
    
    // Réinitialiser le champ de confirmation
    document.getElementById('confirmDeleteInput').value = '';
    document.getElementById('confirmDeleteBackupBtn').disabled = true;
    
    confirmDeleteBackupModal.show();
}

/**
 * Vérifie si la confirmation de suppression est valide
 */
function checkDeleteConfirmation() {
    const input = document.getElementById('confirmDeleteInput').value;
    const btn = document.getElementById('confirmDeleteBackupBtn');
    btn.disabled = input !== 'SUPPRIMER';
}

/**
 * Exécute la suppression après confirmation
 */
async function confirmDeleteBackup() {
    const slug = document.getElementById('deleteBackupSlug').value;
    const backupId = document.getElementById('deleteBackupId').value;
    
    if (!slug || !backupId) return;
    
    // Fermer le modal
    confirmDeleteBackupModal.hide();
    
    try {
        await api.request(`/apps/${slug}/backup/${backupId}`, {
            method: 'DELETE'
        });
        
        showToast('Succès', 'Backup supprimé définitivement', 'success');
        showAppBackupsList(slug);
        loadAppBackupsOverview();
        
    } catch (error) {
        console.error('Erreur suppression:', error);
        showToast('Erreur', error.message || 'Impossible de supprimer le backup', 'danger');
    }
}


// Variables pour le déclenchement de backup
let confirmBackupTriggerModal = null;
let pendingBackupSlug = null;
let pendingBackupAppName = null;

/**
 * Affiche le modal de confirmation pour déclencher un backup
 */
function showTriggerBackupConfirm(slug, appName) {
    if (!confirmBackupTriggerModal) {
        confirmBackupTriggerModal = new bootstrap.Modal(document.getElementById('confirmBackupTriggerModal'));
    }
    
    pendingBackupSlug = slug;
    pendingBackupAppName = appName;
    
    document.getElementById('confirmBackupAppName').textContent = appName;
    confirmBackupTriggerModal.show();
}

/**
 * Déclenche le backup après confirmation (depuis le modal de confirmation)
 */
async function confirmTriggerBackup() {
    if (!pendingBackupSlug) return;
    
    // Fermer le modal de confirmation
    confirmBackupTriggerModal.hide();
    
    await doTriggerBackup(pendingBackupSlug, pendingBackupAppName);
    
    pendingBackupSlug = null;
    pendingBackupAppName = null;
}

/**
 * Déclenche le backup depuis le modal de liste des backups
 */
async function triggerAppBackup() {
    const slug = document.getElementById('backupsListAppSlug').value;
    const appName = document.getElementById('backupsListAppName').textContent;
    
    if (!slug) return;
    
    // Afficher le modal de confirmation
    showTriggerBackupConfirm(slug, appName);
}

/**
 * Effectue le déclenchement du backup
 */
async function doTriggerBackup(slug, appName) {
    // Toast de progression
    showToast('Sauvegarde', `Déclenchement de la sauvegarde pour ${appName}...`, 'info');
    
    try {
        const response = await api.request(`/apps/${slug}/backup/trigger`, {
            method: 'POST'
        });
        
        if (response.success) {
            showToast('Succès', response.message || `Sauvegarde créée pour ${appName}`, 'success');
            
            // Rafraîchir les données
            loadAppBackupsOverview();
            
            // Si le modal de liste est ouvert, le rafraîchir
            if (appBackupsListModal && document.getElementById('backupsListAppSlug').value === slug) {
                showAppBackupsList(slug);
            }
        } else {
            showToast('Information', response.message || 'Backup non créé', 'warning');
        }
        
    } catch (error) {
        console.error('Erreur déclenchement backup:', error);
        showToast('Erreur', error.message || 'Impossible de déclencher la sauvegarde', 'danger');
    }
}


// =============================================================================
// ===== GESTION EN MASSE DES UTILISATEURS (Bulk Operations) =====
// =============================================================================

let bulkOperationsModal = null;
let selectedUsersForBulk = new Set();
let bulkOperationMode = null; // 'assign-roles', 'remove-access', 'delete'

/**
 * Initialise le modal de gestion en masse
 */
function initBulkOperationsModal() {
    const modalEl = document.getElementById('bulkOperationsModal');
    if (modalEl && !bulkOperationsModal) {
        bulkOperationsModal = new bootstrap.Modal(modalEl);
    }
}

/**
 * Ouvre le modal de gestion en masse
 * @param {string} mode - Mode d'opération: 'assign-roles', 'remove-access', 'delete', 'create'
 */
function showBulkOperationsModal(mode) {
    initBulkOperationsModal();
    bulkOperationMode = mode;
    selectedUsersForBulk.clear();
    
    // Configurer le modal selon le mode
    const modalTitle = document.getElementById('bulkModalTitle');
    const assignSection = document.getElementById('bulkAssignSection');
    const removeSection = document.getElementById('bulkRemoveSection');
    const deleteSection = document.getElementById('bulkDeleteSection');
    const createSection = document.getElementById('bulkCreateSection');
    const csvImportSection = document.getElementById('bulkCsvImportSection');
    const userSelectionSection = document.getElementById('bulkUserSelectionSection');
    
    // Masquer toutes les sections
    [assignSection, removeSection, deleteSection, createSection, csvImportSection].forEach(s => {
        if (s) s.classList.add('d-none');
    });
    
    // Afficher la section appropriée
    switch (mode) {
        case 'assign-roles':
            modalTitle.textContent = 'Assigner des rôles en masse';
            if (assignSection) assignSection.classList.remove('d-none');
            if (userSelectionSection) userSelectionSection.classList.remove('d-none');
            loadBulkUserSelection();
            loadBulkAppSelection('bulkAssignAppSelect');
            break;
        case 'remove-access':
            modalTitle.textContent = 'Retirer des accès en masse';
            if (removeSection) removeSection.classList.remove('d-none');
            if (userSelectionSection) userSelectionSection.classList.remove('d-none');
            loadBulkUserSelection();
            loadBulkAppSelection('bulkRemoveAppSelect');
            break;
        case 'delete':
            modalTitle.textContent = 'Supprimer des utilisateurs en masse';
            if (deleteSection) deleteSection.classList.remove('d-none');
            if (userSelectionSection) userSelectionSection.classList.remove('d-none');
            loadBulkUserSelection();
            break;
        case 'create':
            modalTitle.textContent = 'Créer des utilisateurs en masse';
            if (createSection) createSection.classList.remove('d-none');
            if (userSelectionSection) userSelectionSection.classList.add('d-none');
            initBulkCreateForm();
            break;
        case 'csv-import':
            modalTitle.innerHTML = '<i class="bi bi-filetype-csv me-2"></i>Importer depuis CSV';
            if (csvImportSection) csvImportSection.classList.remove('d-none');
            if (userSelectionSection) userSelectionSection.classList.add('d-none');
            resetCsvImport();
            break;
    }
    
    // Réinitialiser le résultat
    document.getElementById('bulkResultSection').classList.add('d-none');
    
    bulkOperationsModal.show();
}

/**
 * Charge la liste des utilisateurs pour sélection en masse
 */
async function loadBulkUserSelection() {
    const container = document.getElementById('bulkUsersList');
    if (!container) return;
    
    try {
        const users = allUsersPage.length > 0 ? allUsersPage : await api.getUsers();
        
        if (users.length === 0) {
            container.innerHTML = '<p class="text-muted">Aucun utilisateur disponible</p>';
            return;
        }
        
        container.innerHTML = `
            <div class="mb-2">
                <div class="input-group input-group-sm">
                    <span class="input-group-text"><i class="bi bi-search"></i></span>
                    <input type="text" class="form-control" id="bulkUserSearch" 
                           placeholder="Rechercher..." onkeyup="filterBulkUsers()">
                </div>
            </div>
            <div class="mb-2">
                <button type="button" class="btn btn-sm btn-outline-secondary me-1" onclick="selectAllBulkUsers()">
                    <i class="bi bi-check-all"></i> Tout sélectionner
                </button>
                <button type="button" class="btn btn-sm btn-outline-secondary" onclick="deselectAllBulkUsers()">
                    <i class="bi bi-x-lg"></i> Tout désélectionner
                </button>
                <span class="ms-2 text-muted small" id="bulkSelectedCount">0 sélectionné(s)</span>
            </div>
            <div class="bulk-users-list border rounded p-2" style="max-height: 250px; overflow-y: auto;">
                ${users.filter(u => u.id !== currentUser?.id).map(user => `
                    <div class="form-check bulk-user-item" data-username="${escapeHtml(user.username.toLowerCase())}">
                        <input class="form-check-input bulk-user-checkbox" type="checkbox" 
                               value="${user.id}" id="bulkUser_${user.id}"
                               onchange="toggleBulkUserSelection(${user.id})">
                        <label class="form-check-label d-flex justify-content-between w-100" for="bulkUser_${user.id}">
                            <span>
                                <strong>${escapeHtml(user.username)}</strong>
                                ${user.is_admin ? '<span class="badge bg-dark ms-1">Admin</span>' : ''}
                                ${!user.is_active ? '<span class="badge bg-secondary ms-1">Inactif</span>' : ''}
                            </span>
                            <small class="text-muted">${user.app_roles?.length || 0} app(s)</small>
                        </label>
                    </div>
                `).join('')}
            </div>
        `;
    } catch (error) {
        console.error('Erreur chargement utilisateurs bulk:', error);
        container.innerHTML = '<p class="text-danger">Erreur de chargement</p>';
    }
}

/**
 * Charge la liste des applications pour sélection
 */
async function loadBulkAppSelection(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    
    try {
        const apps = allApps.length > 0 ? allApps : await api.getAllApps();
        
        select.innerHTML = `
            <option value="">-- Sélectionner une application --</option>
            ${apps.map(app => `
                <option value="${app.id}">${escapeHtml(app.name)} (${app.slug})</option>
            `).join('')}
        `;
    } catch (error) {
        console.error('Erreur chargement apps bulk:', error);
        select.innerHTML = '<option value="">Erreur de chargement</option>';
    }
}

/**
 * Filtre les utilisateurs dans la liste de sélection bulk
 */
function filterBulkUsers() {
    const search = document.getElementById('bulkUserSearch')?.value.toLowerCase() || '';
    document.querySelectorAll('.bulk-user-item').forEach(item => {
        const username = item.dataset.username || '';
        item.style.display = username.includes(search) ? '' : 'none';
    });
}

/**
 * Toggle la sélection d'un utilisateur
 */
function toggleBulkUserSelection(userId) {
    if (selectedUsersForBulk.has(userId)) {
        selectedUsersForBulk.delete(userId);
    } else {
        selectedUsersForBulk.add(userId);
    }
    updateBulkSelectedCount();
}

/**
 * Sélectionne tous les utilisateurs visibles
 */
function selectAllBulkUsers() {
    document.querySelectorAll('.bulk-user-item').forEach(item => {
        if (item.style.display !== 'none') {
            const checkbox = item.querySelector('.bulk-user-checkbox');
            if (checkbox) {
                checkbox.checked = true;
                selectedUsersForBulk.add(parseInt(checkbox.value));
            }
        }
    });
    updateBulkSelectedCount();
}

/**
 * Désélectionne tous les utilisateurs
 */
function deselectAllBulkUsers() {
    document.querySelectorAll('.bulk-user-checkbox').forEach(cb => {
        cb.checked = false;
    });
    selectedUsersForBulk.clear();
    updateBulkSelectedCount();
}

/**
 * Met à jour le compteur de sélection
 */
function updateBulkSelectedCount() {
    const countEl = document.getElementById('bulkSelectedCount');
    if (countEl) {
        countEl.textContent = `${selectedUsersForBulk.size} sélectionné(s)`;
    }
}

/**
 * Initialise le formulaire de création en masse
 */
async function initBulkCreateForm() {
    const container = document.getElementById('bulkCreateUsersList');
    if (!container) return;
    
    container.innerHTML = '';
    addBulkCreateUserRow();
    
    // Charger les profils
    await loadBulkProfileSelection();
    
    // Charger les apps pour le mode avancé
    await loadBulkAdvancedApps();
    
    // Réinitialiser l'aperçu profil
    const preview = document.getElementById('bulkProfilePreview');
    if (preview) {
        preview.classList.add('d-none');
        preview.innerHTML = '';
    }
    
    // Fermer le collapse avancé
    const advancedEl = document.getElementById('bulkCreateAdvanced');
    if (advancedEl) {
        advancedEl.classList.remove('show');
    }
}

/**
 * Charge les profils dans le sélecteur de création en masse
 */
async function loadBulkProfileSelection() {
    const select = document.getElementById('bulkCreateProfileSelect');
    if (!select) return;
    
    try {
        const profiles = allProfiles.length > 0
            ? allProfiles
            : await api.getProfiles();
        allProfiles = profiles;
        
        select.innerHTML = '<option value="">-- Aucun profil --</option>' +
            profiles.map(p => `
                <option value="${p.id}" data-color="${p.color}">
                    ${escapeHtml(p.name)} (${p.app_roles.length} app${p.app_roles.length !== 1 ? 's' : ''})
                </option>
            `).join('');
    } catch (error) {
        console.warn('Profils non disponibles:', error);
        select.innerHTML = '<option value="">-- Aucun profil --</option>';
    }
}

/**
 * Charge les apps avec checkboxes + role selects pour le mode avancé
 */
async function loadBulkAdvancedApps() {
    const container = document.getElementById('bulkCreateAdvancedApps');
    if (!container) return;
    
    try {
        const apps = allApps.length > 0
            ? allApps
            : await api.getAllApps();
        allApps = apps;
        
        if (apps.length === 0) {
            container.innerHTML = '<small class="text-muted">Aucune application disponible</small>';
            return;
        }
        
        container.innerHTML = apps.map(app => `
            <div class="d-flex align-items-center mb-2 bulk-adv-app-row">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input bulk-adv-app-cb" type="checkbox" 
                        value="${app.id}" id="bulkAdvApp${app.id}"
                        onchange="toggleBulkAdvRole(${app.id})">
                    <label class="form-check-label" for="bulkAdvApp${app.id}">
                        <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                        ${escapeHtml(app.name)}
                    </label>
                </div>
                <select class="form-select form-select-sm bulk-adv-role-select" 
                    id="bulkAdvRole${app.id}" style="width: 120px;" disabled>
                    <option value="">-- Rôle --</option>
                    ${AVAILABLE_ROLES.map(r => `
                        <option value="${r}"${r === 'user' ? ' selected' : ''}>
                            ${r.charAt(0).toUpperCase() + r.slice(1)}
                        </option>
                    `).join('')}
                </select>
            </div>
        `).join('');
    } catch (error) {
        console.error('Erreur chargement apps avancé:', error);
        container.innerHTML = '<small class="text-danger">Erreur de chargement</small>';
    }
}

/**
 * Toggle le select de rôle pour une app en mode avancé
 */
function toggleBulkAdvRole(appId) {
    const cb = document.getElementById(`bulkAdvApp${appId}`);
    const sel = document.getElementById(`bulkAdvRole${appId}`);
    if (cb.checked) {
        sel.disabled = false;
        if (!sel.value) sel.value = 'user';
    } else {
        sel.disabled = true;
        sel.value = '';
    }
}

/**
 * Réagit au changement de profil dans la création en masse
 */
function onBulkProfileChange() {
    const select = document.getElementById('bulkCreateProfileSelect');
    const preview = document.getElementById('bulkProfilePreview');
    if (!select || !preview) return;
    
    const profileId = parseInt(select.value);
    if (!profileId) {
        preview.classList.add('d-none');
        preview.innerHTML = '';
        return;
    }
    
    const profile = allProfiles.find(p => p.id === profileId);
    if (!profile) {
        preview.classList.add('d-none');
        return;
    }
    
    preview.classList.remove('d-none');
    preview.innerHTML = `
        <div class="border rounded p-2 bg-white">
            <span class="badge badge-profile mb-1" style="background-color: ${profile.color};">
                ${escapeHtml(profile.name)}
            </span>
            <div class="d-flex flex-wrap gap-1 mt-1">
                ${profile.app_roles.length > 0 
                    ? profile.app_roles.map(ar => `
                        <span class="badge bg-white text-dark border" style="font-size: 0.75em;">
                            ${escapeHtml(ar.app_name)}
                            <span class="badge ${getRoleBadgeColor(ar.role)}" style="font-size: 0.8em;">${ar.role}</span>
                        </span>
                    `).join('')
                    : '<small class="text-muted">Aucune app</small>'
                }
            </div>
        </div>
    `;
}

/**
 * Ajoute une ligne pour créer un utilisateur
 */
function addBulkCreateUserRow() {
    const container = document.getElementById('bulkCreateUsersList');
    if (!container) return;
    
    const rowIndex = container.children.length;
    const row = document.createElement('div');
    row.className = 'bulk-create-row border rounded p-2 mb-2';
    row.dataset.index = rowIndex;
    
    row.innerHTML = `
        <div class="row g-2">
            <div class="col-md-4">
                <input type="text" class="form-control form-control-sm" 
                       placeholder="Nom d'utilisateur *" name="username_${rowIndex}" required>
            </div>
            <div class="col-md-4">
                <input type="email" class="form-control form-control-sm" 
                       placeholder="Email (optionnel)" name="email_${rowIndex}">
            </div>
            <div class="col-md-3">
                <input type="text" class="form-control form-control-sm" 
                       placeholder="Mot de passe *" name="password_${rowIndex}" required>
            </div>
            <div class="col-md-1 d-flex align-items-center">
                <button type="button" class="btn btn-sm btn-outline-danger" onclick="removeBulkCreateRow(this)">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
        <div class="row g-2 mt-1">
            <div class="col-md-6">
                <div class="form-check form-check-inline">
                    <input class="form-check-input" type="checkbox" name="isAdmin_${rowIndex}" id="isAdmin_${rowIndex}">
                    <label class="form-check-label small" for="isAdmin_${rowIndex}">Admin Hub</label>
                </div>
                <div class="form-check form-check-inline">
                    <input class="form-check-input" type="checkbox" name="mustChangePwd_${rowIndex}" id="mustChangePwd_${rowIndex}">
                    <label class="form-check-label small" for="mustChangePwd_${rowIndex}">Changer mdp</label>
                </div>
            </div>
        </div>
    `;
    
    container.appendChild(row);
}

/**
 * Supprime une ligne de création
 */
function removeBulkCreateRow(btn) {
    const row = btn.closest('.bulk-create-row');
    if (row && document.querySelectorAll('.bulk-create-row').length > 1) {
        row.remove();
    }
}

/**
 * Exécute l'opération en masse selon le mode
 */
async function executeBulkOperation() {
    const resultSection = document.getElementById('bulkResultSection');
    const resultContent = document.getElementById('bulkResultContent');
    
    try {
        let result;
        
        switch (bulkOperationMode) {
            case 'assign-roles':
                result = await executeBulkAssignRoles();
                break;
            case 'remove-access':
                result = await executeBulkRemoveAccess();
                break;
            case 'delete':
                result = await executeBulkDelete();
                break;
            case 'create':
                result = await executeBulkCreate();
                break;
            case 'csv-import':
                result = await executeCsvImport();
                break;
            default:
                throw new Error('Mode d\'opération non reconnu');
        }
        
        // Fermer la modale et afficher le résultat en toast
        if (bulkOperationsModal) bulkOperationsModal.hide();
        displayBulkResult(result);
        
        // Rafraîchir la liste des utilisateurs
        await loadUsersPageData();
        
    } catch (error) {
        console.error('Erreur opération bulk:', error);
        resultSection.classList.remove('d-none');
        resultContent.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle me-2"></i>
                ${escapeHtml(error.message)}
            </div>
        `;
    }
}

/**
 * Exécute l'assignation de rôles en masse
 */
async function executeBulkAssignRoles() {
    const appId = parseInt(document.getElementById('bulkAssignAppSelect')?.value);
    const role = document.getElementById('bulkAssignRoleSelect')?.value;
    
    if (!appId) {
        throw new Error('Veuillez sélectionner une application');
    }
    if (!role) {
        throw new Error('Veuillez sélectionner un rôle');
    }
    if (selectedUsersForBulk.size === 0) {
        throw new Error('Veuillez sélectionner au moins un utilisateur');
    }
    
    return await api.bulkAssignAppRoles([{
        app_id: appId,
        user_ids: Array.from(selectedUsersForBulk),
        role: role
    }]);
}

/**
 * Exécute la suppression d'accès en masse
 */
async function executeBulkRemoveAccess() {
    const appId = parseInt(document.getElementById('bulkRemoveAppSelect')?.value);
    
    if (!appId) {
        throw new Error('Veuillez sélectionner une application');
    }
    if (selectedUsersForBulk.size === 0) {
        throw new Error('Veuillez sélectionner au moins un utilisateur');
    }
    
    return await api.bulkRemoveAppAccess([{
        app_id: appId,
        user_ids: Array.from(selectedUsersForBulk)
    }]);
}

/**
 * Exécute la suppression d'utilisateurs en masse avec confirmation
 */
async function executeBulkDelete() {
    if (selectedUsersForBulk.size === 0) {
        throw new Error('Veuillez sélectionner au moins un utilisateur');
    }
    
    // Demander confirmation
    const usernames = [];
    document.querySelectorAll('.bulk-user-checkbox:checked').forEach(cb => {
        const label = cb.closest('.bulk-user-item')?.querySelector('strong');
        if (label) usernames.push(label.textContent);
    });
    
    const confirmed = confirm(
        `⚠️ ATTENTION: Vous allez supprimer ${selectedUsersForBulk.size} utilisateur(s):\n\n` +
        `${usernames.slice(0, 10).join('\n')}` +
        (usernames.length > 10 ? `\n... et ${usernames.length - 10} autre(s)` : '') +
        `\n\nCette action est IRRÉVERSIBLE.\n\nConfirmer la suppression ?`
    );
    
    if (!confirmed) {
        throw new Error('Opération annulée par l\'utilisateur');
    }
    
    return await api.bulkDeleteUsers(Array.from(selectedUsersForBulk), true);
}

/**
 * Exécute la création d'utilisateurs en masse
 */
async function executeBulkCreate() {
    const rows = document.querySelectorAll('.bulk-create-row');
    const users = [];
    
    // Profil sélectionné
    const profileId = parseInt(
        document.getElementById('bulkCreateProfileSelect')?.value
    ) || null;
    
    // Apps manuelles (mode avancé)
    const manualAppRoles = [];
    document.querySelectorAll('.bulk-adv-app-cb:checked').forEach(cb => {
        const appId = parseInt(cb.value);
        const role = document.getElementById(
            `bulkAdvRole${appId}`
        )?.value || 'user';
        if (role) {
            manualAppRoles.push({ app_id: appId, role: role });
        }
    });
    
    rows.forEach((row, index) => {
        const ri = row.dataset.index;
        const username = row.querySelector(
            `[name="username_${ri}"]`
        )?.value?.trim();
        const email = row.querySelector(
            `[name="email_${ri}"]`
        )?.value?.trim() || null;
        const password = row.querySelector(
            `[name="password_${ri}"]`
        )?.value;
        const isAdmin = row.querySelector(
            `[name="isAdmin_${ri}"]`
        )?.checked || false;
        const mustChangePwd = row.querySelector(
            `[name="mustChangePwd_${ri}"]`
        )?.checked || false;
        
        if (username && password) {
            const userData = {
                username,
                email,
                password,
                is_admin: isAdmin,
                is_active: true,
                must_change_password: mustChangePwd,
                can_change_password: true,
                app_roles: [...manualAppRoles]
            };
            
            // Ajouter le profil si sélectionné
            if (profileId) {
                userData.profile_ids = [profileId];
            }
            
            users.push(userData);
        }
    });
    
    if (users.length === 0) {
        throw new Error('Veuillez remplir au moins un utilisateur (nom et mot de passe obligatoires)');
    }
    
    // Demander confirmation
    const profileName = profileId 
        ? allProfiles.find(p => p.id === profileId)?.name 
        : null;
    const advCount = manualAppRoles.length;
    
    let confirmMsg = `Vous allez créer ${users.length} utilisateur(s):\n\n` +
        `${users.slice(0, 10).map(u => u.username).join('\n')}` +
        (users.length > 10 ? `\n... et ${users.length - 10} autre(s)` : '');
    
    if (profileName) {
        confirmMsg += `\n\nProfil: ${profileName}`;
    }
    if (advCount > 0) {
        confirmMsg += `\n${advCount} accès app manuel(s) supplémentaire(s)`;
    }
    confirmMsg += '\n\nConfirmer la création ?';
    
    const confirmed = confirm(confirmMsg);
    
    if (!confirmed) {
        throw new Error('Opération annulée par l\'utilisateur');
    }
    
    return await api.bulkCreateUsers(users);
}

/**
 * Affiche le résultat d'une opération bulk
 */
function displayBulkResult(result) {
    const resultSection = document.getElementById('bulkResultSection');
    const resultContent = document.getElementById('bulkResultContent');
    
    if (!resultSection || !resultContent) return;
    
    resultSection.classList.remove('d-none');
    
    // Déterminer le type de résultat
    let html = '';
    
    if (result.total_created !== undefined) {
        // Résultat création
        const alertClass = result.total_failed > 0 ? 'warning' : 'success';
        html = `
            <div class="alert alert-${alertClass}">
                <h6><i class="bi bi-person-plus me-2"></i>Création terminée</h6>
                <p class="mb-1">
                    <strong>${result.total_created}</strong> utilisateur(s) créé(s) sur ${result.total_requested}
                    ${result.total_failed > 0 ? `<br><span class="text-danger">${result.total_failed} échec(s)</span>` : ''}
                </p>
            </div>
        `;
    } else if (result.total_deleted !== undefined) {
        // Résultat suppression
        const alertClass = result.total_failed > 0 ? 'warning' : 'success';
        html = `
            <div class="alert alert-${alertClass}">
                <h6><i class="bi bi-trash me-2"></i>Suppression terminée</h6>
                <p class="mb-1">
                    <strong>${result.total_deleted}</strong> utilisateur(s) supprimé(s) sur ${result.total_requested}
                    ${result.total_failed > 0 ? `<br><span class="text-danger">${result.total_failed} échec(s)</span>` : ''}
                </p>
            </div>
        `;
    } else if (result.total_updated !== undefined) {
        // Résultat mise à jour rôles
        const alertClass = result.total_failed > 0 ? 'warning' : 'success';
        html = `
            <div class="alert alert-${alertClass}">
                <h6><i class="bi bi-pencil me-2"></i>Mise à jour terminée</h6>
                <p class="mb-1">
                    <strong>${result.total_updated}</strong> utilisateur(s) mis à jour sur ${result.total_requested}
                    ${result.total_failed > 0 ? `<br><span class="text-danger">${result.total_failed} échec(s)</span>` : ''}
                </p>
            </div>
        `;
    } else if (result.total_assignments !== undefined) {
        // Résultat assignation
        const alertClass = result.total_failed > 0 ? 'warning' : 'success';
        html = `
            <div class="alert alert-${alertClass}">
                <h6><i class="bi bi-check-circle me-2"></i>Assignation terminée</h6>
                <p class="mb-1">
                    <strong>${result.total_success}</strong> assignation(s) réussie(s) sur ${result.total_assignments}
                    ${result.total_failed > 0 ? `<br><span class="text-danger">${result.total_failed} échec(s)</span>` : ''}
                </p>
            </div>
        `;
    } else if (result.total_removals !== undefined) {
        // Résultat suppression accès
        const alertClass = result.total_failed > 0 ? 'warning' : 'success';
        html = `
            <div class="alert alert-${alertClass}">
                <h6><i class="bi bi-x-circle me-2"></i>Suppression d'accès terminée</h6>
                <p class="mb-1">
                    <strong>${result.total_success}</strong> accès supprimé(s) sur ${result.total_removals}
                    ${result.total_failed > 0 ? `<br><span class="text-danger">${result.total_failed} échec(s)</span>` : ''}
                </p>
            </div>
        `;
    }
    
    // Afficher les détails des erreurs si présentes
    if (result.results && result.results.some(r => !r.success)) {
        html += `
            <details class="mt-2">
                <summary class="text-danger cursor-pointer">Voir les erreurs</summary>
                <ul class="list-unstyled mt-2 small">
                    ${result.results.filter(r => !r.success).map(r => `
                        <li class="text-danger">
                            <i class="bi bi-x-circle me-1"></i>
                            ${escapeHtml(r.username || r.user_id || 'Inconnu')}: ${escapeHtml(r.error || 'Erreur inconnue')}
                        </li>
                    `).join('')}
                </ul>
            </details>
        `;
    }
    
    resultContent.innerHTML = html;
    
    // Notification toast
    if (result.total_failed > 0) {
        showToast('Opération terminée', 'Certaines opérations ont échoué', 'warning');
    } else {
        showToast('Succès', 'Opération en masse terminée', 'success');
    }
}

// =============================================================================
// CSV Import
// =============================================================================

// Stockage des données CSV parsées côté client (pour conserver les mots de passe)
let csvParsedData = [];
let csvPreviewData = null;

/**
 * Télécharge le template CSV
 */
async function downloadCsvTemplate() {
    try {
        await api.downloadCsvTemplate();
        showToast('Succès', 'Template CSV téléchargé', 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

/**
 * Callback quand un fichier CSV est sélectionné dans l'input
 */
function onCsvFileSelected(input) {
    const btn = document.getElementById('csvAnalyzeBtn');
    if (btn) {
        btn.disabled = !input.files || input.files.length === 0;
    }
}

/**
 * Réinitialise l'interface d'import CSV
 */
function resetCsvImport() {
    csvParsedData = [];
    csvPreviewData = null;
    
    const step1 = document.getElementById('csvStep1');
    const step2 = document.getElementById('csvStep2');
    if (step1) step1.classList.remove('d-none');
    if (step2) step2.classList.add('d-none');
    
    const fileInput = document.getElementById('csvFileInput');
    if (fileInput) fileInput.value = '';
    
    const analyzeBtn = document.getElementById('csvAnalyzeBtn');
    if (analyzeBtn) analyzeBtn.disabled = true;
    
    document.getElementById('bulkResultSection')?.classList.add('d-none');
}

/**
 * Parse un CSV côté client pour extraire les mots de passe
 */
function parseCsvLocally(text) {
    const lines = text.split(/\r?\n/);
    if (lines.length < 2) return [];
    
    // Détecter le séparateur
    const sep = lines[0].includes(';') ? ';' : ',';
    
    const results = [];
    for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line || line.startsWith('#')) continue;
        
        const parts = line.split(sep);
        results.push({
            username: (parts[0] || '').trim(),
            password: (parts[1] || '').trim(),
            profile: (parts[2] || '').trim()
        });
    }
    return results;
}

/**
 * Analyse le fichier CSV : parse local + envoi au backend pour validation
 */
async function analyzeCsvFile() {
    const fileInput = document.getElementById('csvFileInput');
    if (!fileInput?.files?.length) return;
    
    const file = fileInput.files[0];
    
    if (file.size > 1048576) {
        showToast('Erreur', 'Le fichier CSV ne doit pas dépasser 1 Mo', 'danger');
        return;
    }
    
    const analyzeBtn = document.getElementById('csvAnalyzeBtn');
    if (analyzeBtn) {
        analyzeBtn.disabled = true;
        analyzeBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Analyse...';
    }
    
    try {
        // Parser le CSV localement pour conserver les mots de passe
        const text = await file.text();
        csvParsedData = parseCsvLocally(text);
        
        // Envoyer au backend pour validation
        csvPreviewData = await api.previewCsvUpload(file);
        
        // Afficher le preview
        renderCsvPreview(csvPreviewData);
        
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    } finally {
        if (analyzeBtn) {
            analyzeBtn.disabled = false;
            analyzeBtn.innerHTML = '<i class="bi bi-search me-1"></i>Analyser';
        }
    }
}

/**
 * Affiche le tableau de preview CSV
 */
function renderCsvPreview(data) {
    const step1 = document.getElementById('csvStep1');
    const step2 = document.getElementById('csvStep2');
    if (step1) step1.classList.add('d-none');
    if (step2) step2.classList.remove('d-none');
    
    // Résumé
    const summary = document.getElementById('csvPreviewSummary');
    if (summary) {
        summary.innerHTML = `
            <div class="d-flex gap-3 flex-wrap">
                <span class="badge bg-primary fs-6">${data.total_lines} ligne(s)</span>
                <span class="badge bg-success fs-6">${data.total_valid} valide(s)</span>
                ${data.total_warnings > 0 ? `<span class="badge bg-warning text-dark fs-6">${data.total_warnings} avertissement(s)</span>` : ''}
                ${data.total_errors > 0 ? `<span class="badge bg-danger fs-6">${data.total_errors} erreur(s)</span>` : ''}
            </div>
        `;
    }
    
    // Tableau
    const tbody = document.getElementById('csvPreviewBody');
    if (!tbody) return;
    
    tbody.innerHTML = data.lines.map(line => {
        const statusIcon = line.is_valid
            ? '<i class="bi bi-check-circle-fill text-success"></i>'
            : '<i class="bi bi-x-circle-fill text-danger"></i>';
        
        // Profil
        let profileHtml = '<span class="text-muted">—</span>';
        if (line.profile_matched) {
            profileHtml = `<span class="badge" style="background-color: ${escapeHtml(line.profile_matched.color)}">${escapeHtml(line.profile_matched.name)}</span>`;
        } else if (line.profile_ignored && line.profile_requested) {
            profileHtml = `<span class="badge bg-danger text-decoration-line-through" title="Profil non trouvé">${escapeHtml(line.profile_requested)}</span>`;
        }
        
        // Erreurs/warnings
        let details = '';
        if (line.errors.length > 0) {
            details += line.errors.map(e => `<div class="text-danger small"><i class="bi bi-x-circle me-1"></i>${escapeHtml(e)}</div>`).join('');
        }
        if (line.warnings.length > 0) {
            details += line.warnings.map(w => `<div class="text-warning small"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(w)}</div>`).join('');
        }
        
        const rowClass = line.is_valid ? '' : 'table-danger';
        
        return `
            <tr class="${rowClass}">
                <td>${line.line_number}</td>
                <td>${escapeHtml(line.username)}${details}</td>
                <td><code>${'•'.repeat(Math.min(line.password_length, 12))}</code> <small class="text-muted">(${line.password_length})</small></td>
                <td>${profileHtml}</td>
                <td class="text-center">${statusIcon}</td>
            </tr>
        `;
    }).join('');
    
    // Message de confirmation
    const confirmMsg = document.getElementById('csvConfirmMessage');
    const confirmBtn = document.getElementById('csvConfirmBtn');
    
    if (data.total_valid > 0) {
        confirmMsg.innerHTML = `
            <i class="bi bi-info-circle me-1"></i>
            <strong>${data.total_valid}</strong> utilisateur(s) seront créés avec le rôle <strong>user</strong>.<br>
            Ils devront changer leur mot de passe à la première connexion.
            ${data.total_errors > 0 ? `<br><span class="text-danger">${data.total_errors} ligne(s) en erreur seront ignorées.</span>` : ''}
        `;
        confirmBtn.disabled = false;
    } else {
        confirmMsg.innerHTML = `
            <i class="bi bi-exclamation-triangle me-1"></i>
            Aucun utilisateur valide à créer. Corrigez les erreurs dans votre fichier CSV.
        `;
        confirmBtn.disabled = true;
    }
    
    // Masquer le bouton Exécuter du modal (on utilise notre propre bouton Confirmer)
    const modalFooterBtn = document.querySelector('#bulkOperationsModal .modal-footer .btn-primary');
    if (modalFooterBtn) modalFooterBtn.classList.add('d-none');
}

/**
 * Exécute l'import CSV après confirmation
 */
async function executeCsvImport() {
    if (!csvPreviewData || !csvParsedData.length) {
        throw new Error('Aucune donnée CSV à importer');
    }
    
    const validLines = csvPreviewData.lines.filter(l => l.is_valid);
    if (validLines.length === 0) {
        throw new Error('Aucun utilisateur valide à créer');
    }
    
    // Construire les users à créer en combinant les données du preview (profil résolu) 
    // avec les données parsées localement (mots de passe)
    const users = validLines.map(line => {
        // Retrouver le mdp depuis les données parsées localement
        const localData = csvParsedData.find(d => d.username === line.username);
        
        return {
            username: line.username,
            password: localData ? localData.password : '',
            is_admin: false,
            is_active: true,
            must_change_password: true,
            can_change_password: true,
            profile_ids: line.profile_matched ? [line.profile_matched.id] : [],
            app_roles: []
        };
    });
    
    // Appeler l'endpoint bulk/create existant
    return await api.bulkCreateUsers(users);
}

/**
 * Wrapper pour le bouton "Confirmer la création" CSV : exécute l'import puis ferme la modale
 */
async function confirmCsvImport() {
    try {
        const result = await executeCsvImport();
        if (bulkOperationsModal) bulkOperationsModal.hide();
        displayBulkResult(result);
        await loadUsersPageData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// =============================================================================
// MESSAGES — Admin + User overlay
// =============================================================================

let messagesPage = 1;
let sendMessageModal = null;

// ===== Admin : Page Messages =====

async function loadMessagesPage() {
    const priority = document.getElementById('msgFilterPriority')?.value || '';
    const senderType = document.getElementById('msgFilterSender')?.value || '';
    const senderApp = document.getElementById('msgFilterApp')?.value || '';
    const search = document.getElementById('msgFilterSearch')?.value || '';
    const hours = document.getElementById('msgFilterHours')?.value || '';

    try {
        const [stats, data] = await Promise.all([
            api.getMessageStats(hours || null),
            api.getMessages({
                page: messagesPage,
                perPage: 50,
                priority: priority || undefined,
                senderType: senderType || undefined,
                senderApp: senderApp || undefined,
                search: search || undefined,
                hours: hours || undefined,
            })
        ]);

        // Stats cards
        document.getElementById('msgStatTotal').textContent = stats.total || 0;
        document.getElementById('msgStatInfo').textContent = stats.by_priority?.info || 0;
        document.getElementById('msgStatWarning').textContent = stats.by_priority?.warning || 0;
        document.getElementById('msgStatUrgent').textContent = stats.by_priority?.urgent || 0;

        // Nav badge
        const navBadge = document.getElementById('navMessagesBadge');
        if (stats.total > 0) {
            navBadge.textContent = stats.total > 99 ? '99+' : stats.total;
            navBadge.style.display = 'inline';
        } else {
            navBadge.style.display = 'none';
        }

        renderMessagesTable(data);
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les messages: ' + error.message, 'danger');
    }
}

function renderMessagesTable(data) {
    const tbody = document.getElementById('messagesTableBody');
    // Reset select all checkbox
    const selectAll = document.getElementById('msgSelectAll');
    if (selectAll) selectAll.checked = false;
    updateDeleteSelectedButton();

    if (!data.messages || data.messages.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Aucun message</td></tr>';
        document.getElementById('messagesPagination').innerHTML = '';
        return;
    }

    tbody.innerHTML = data.messages.map(m => {
        const date = new Date(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
        const priorityBadge = getMessagePriorityBadge(m.priority);
        const sender = m.sender_app_slug
            ? `<i class="bi bi-robot me-1"></i>${escapeHtml(m.sender_app_slug)}`
            : (escapeHtml(m.sender_username || m.sender_type));
        const target = m.target_type === 'broadcast'
            ? '<span class="badge bg-secondary">Tous</span>'
            : escapeHtml(m.target_username || `User #${m.target_user_id}`);
        const readInfo = `${m.read_count}/${m.total_targets}`;
        const rowClass = m.priority === 'urgent' ? 'table-danger' : (m.priority === 'warning' ? 'table-warning' : '');

        return `<tr class="${rowClass}">
            <td><input type="checkbox" class="msg-select-checkbox" value="${m.id}" onchange="updateDeleteSelectedButton()"></td>
            <td class="small">${date}</td>
            <td>${priorityBadge}</td>
            <td class="small">${sender}</td>
            <td>${escapeHtml(m.title)}</td>
            <td class="small">${target}</td>
            <td class="small">${readInfo}</td>
            <td>
                <button class="btn btn-outline-primary btn-sm py-0 px-1" onclick="showMessageDetail(${m.id})" title="Détail">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        </tr>`;
    }).join('');

    // Pagination
    renderMessagesPagination(data.page, data.pages, data.total);
}

function getMessagePriorityBadge(priority) {
    switch (priority) {
        case 'urgent': return '<span class="badge bg-danger msg-priority-urgent">Urgent</span>';
        case 'warning': return '<span class="badge bg-warning text-dark">Warning</span>';
        default: return '<span class="badge bg-info">Info</span>';
    }
}

function renderMessagesPagination(currentPageNum, totalPages, total) {
    const container = document.getElementById('messagesPagination');
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = `<ul class="pagination pagination-sm justify-content-center">`;
    html += `<li class="page-item ${currentPageNum <= 1 ? 'disabled' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${currentPageNum - 1})">«</a></li>`;
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPageNum - 2 && i <= currentPageNum + 2)) {
            html += `<li class="page-item ${i === currentPageNum ? 'active' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${i})">${i}</a></li>`;
        } else if (i === currentPageNum - 3 || i === currentPageNum + 3) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
    }
    html += `<li class="page-item ${currentPageNum >= totalPages ? 'disabled' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${currentPageNum + 1})">»</a></li>`;
    html += `</ul><div class="text-center text-muted small">${total} message(s)</div>`;
    container.innerHTML = html;
}

function messagesGoToPage(page) {
    messagesPage = page;
    loadMessagesPage();
}

async function showMessageDetail(messageId) {
    try {
        const data = await api.getMessageDetail(messageId);
        const m = data.message;
        const receipts = data.read_receipts || [];

        const receiptsHtml = receipts.length > 0
            ? receipts.map(r => `<tr><td>${escapeHtml(r.username)}</td><td>${new Date(r.read_at).toLocaleString('fr-FR')}</td></tr>`).join('')
            : '<tr><td colspan="2" class="text-muted text-center">Aucun accusé de lecture</td></tr>';

        const modal = `
        <div class="modal fade" id="messageDetailModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">${getMessagePriorityBadge(m.priority)} ${escapeHtml(m.title)}</h5>
                        <button class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <small class="text-muted">Expéditeur</small><br>
                                <strong>${escapeHtml(m.sender_username || m.sender_app_slug || m.sender_type)}</strong>
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">Destinataire</small><br>
                                <strong>${m.target_type === 'broadcast' ? 'Tous (broadcast)' : escapeHtml(m.target_username || 'User #' + m.target_user_id)}</strong>
                            </div>
                        </div>
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <small class="text-muted">Date</small><br>${new Date(m.created_at).toLocaleString('fr-FR')}
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">Expiration</small><br>${m.expires_at ? new Date(m.expires_at).toLocaleString('fr-FR') : 'Aucune'}
                            </div>
                        </div>
                        ${m.body ? `<div class="alert alert-light mb-3">${escapeHtml(m.body)}</div>` : ''}
                        ${m.action_url ? `<div class="mb-3"><a href="${escapeHtml(m.action_url)}" target="_blank" class="btn btn-outline-primary btn-sm"><i class="bi bi-box-arrow-up-right me-1"></i>${escapeHtml(m.action_label || 'Ouvrir')}</a></div>` : ''}
                        <hr>
                        <h6>Accusés de lecture (${receipts.length}/${m.total_targets})</h6>
                        <table class="table table-sm"><thead><tr><th>Utilisateur</th><th>Lu le</th></tr></thead><tbody>${receiptsHtml}</tbody></table>
                    </div>
                </div>
            </div>
        </div>`;

        // Injecter et afficher
        const existing = document.getElementById('messageDetailModal');
        if (existing) existing.remove();
        document.body.insertAdjacentHTML('beforeend', modal);
        new bootstrap.Modal(document.getElementById('messageDetailModal')).show();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function showSendMessageModal() {
    // Charger la liste des utilisateurs pour le select
    try {
        const usersData = await api.request('/users/');
        const select = document.getElementById('msgTargetUsers');
        select.innerHTML = usersData.map(u =>
            `<option value="${u.id}">${escapeHtml(u.username)}</option>`
        ).join('');
    } catch (e) {
        // Pas critique, on continue
    }

    // Réinitialiser le formulaire
    document.getElementById('msgTitle').value = '';
    document.getElementById('msgBody').value = '';
    document.getElementById('msgPriority').value = 'info';
    document.getElementById('msgTargetType').value = 'broadcast';
    document.getElementById('msgExpiresAt').value = '';
    document.getElementById('msgActionUrl').value = '';
    document.getElementById('msgActionLabel').value = '';
    document.getElementById('msgTargetUsersGroup').classList.add('d-none');

    if (!sendMessageModal) {
        sendMessageModal = new bootstrap.Modal(document.getElementById('sendMessageModal'));
    }
    sendMessageModal.show();
}

function toggleMsgTargetUsers() {
    const type = document.getElementById('msgTargetType').value;
    document.getElementById('msgTargetUsersGroup').classList.toggle('d-none', type !== 'user');
}

async function confirmSendMessage() {
    const title = document.getElementById('msgTitle').value.trim();
    if (!title) {
        showToast('Erreur', 'Le titre est obligatoire', 'warning');
        return;
    }

    const targetType = document.getElementById('msgTargetType').value;
    let targetUserIds = null;
    if (targetType === 'user') {
        const select = document.getElementById('msgTargetUsers');
        targetUserIds = Array.from(select.selectedOptions).map(o => parseInt(o.value));
        if (targetUserIds.length === 0) {
            showToast('Erreur', 'Sélectionnez au moins un destinataire', 'warning');
            return;
        }
    }

    const expiresAt = document.getElementById('msgExpiresAt').value;
    const data = {
        title,
        body: document.getElementById('msgBody').value || null,
        priority: document.getElementById('msgPriority').value,
        target_type: targetType,
        target_user_ids: targetUserIds,
        action_url: document.getElementById('msgActionUrl').value || null,
        action_label: document.getElementById('msgActionLabel').value || null,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
    };

    try {
        const result = await api.sendMessage(data);
        showToast('Succès', `${result.sent} message(s) envoyé(s)`, 'success');
        if (sendMessageModal) sendMessageModal.hide();
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function purgeMessagesFiltered() {
    const hours = parseInt(document.getElementById('msgFilterHours')?.value) || null;
    const priority = document.getElementById('msgFilterPriority')?.value || null;
    const senderApp = document.getElementById('msgFilterApp')?.value || null;
    if (!confirm('Purger les messages correspondant aux filtres ?')) return;
    try {
        const result = await api.purgeMessages({ hours, priority, senderApp });
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function purgeAllMessages() {
    if (!confirm('Purger TOUS les messages ? Cette action est irréversible.')) return;
    try {
        const result = await api.purgeMessages({});
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function toggleSelectAllMessages(checkbox) {
    document.querySelectorAll('.msg-select-checkbox').forEach(cb => {
        cb.checked = checkbox.checked;
    });
    updateDeleteSelectedButton();
}

function updateDeleteSelectedButton() {
    const selected = document.querySelectorAll('.msg-select-checkbox:checked');
    const btn = document.getElementById('btnDeleteSelected');
    const count = document.getElementById('deleteSelectedCount');
    if (btn) {
        if (selected.length > 0) {
            btn.style.display = 'inline-flex';
            count.textContent = selected.length;
        } else {
            btn.style.display = 'none';
        }
    }
}

async function deleteSelectedMessages() {
    const selected = Array.from(document.querySelectorAll('.msg-select-checkbox:checked')).map(cb => parseInt(cb.value));
    if (selected.length === 0) return;
    if (!confirm(`Supprimer ${selected.length} message(s) sélectionné(s) ?`)) return;
    try {
        const result = await api.deleteMessagesByIds(selected);
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function exportMessages() {
    const hours = parseInt(document.getElementById('msgFilterHours')?.value) || null;
    const priority = document.getElementById('msgFilterPriority')?.value || null;
    const senderApp = document.getElementById('msgFilterApp')?.value || null;
    try {
        await api.exportMessages({ hours, priority, senderApp });
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== User : Notification bell + overlay =====

async function loadUnreadMessageCount() {
    try {
        const result = await api.getUnreadCount();
        updateMessageBellBadge(result.count || 0);
    } catch (e) {
        // Silencieux
    }
}

function updateMessageBellBadge(count) {
    const badge = document.getElementById('messageBellBadge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

function toggleMessagePanel() {
    const panel = document.getElementById('messagePanel');
    if (panel.classList.contains('d-none')) {
        panel.classList.remove('d-none');
        loadUserMessages();
    } else {
        panel.classList.add('d-none');
    }
}

async function loadUserMessages() {
    try {
        const messages = await api.getMyMessages();
        const body = document.getElementById('messagePanelBody');
        if (!messages || messages.length === 0) {
            body.innerHTML = '<p class="text-muted text-center small mt-3">Aucun message</p>';
            return;
        }

        const priorityIcons = {
            urgent: '<svg class="notif-card-icon-svg notif-icon-urgent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
            warning: '<svg class="notif-card-icon-svg notif-icon-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
            normal: '<svg class="notif-card-icon-svg notif-icon-normal" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
        };

        body.innerHTML = messages.map(m => {
            const time = new Date(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
            const readClass = m.is_read ? 'message-item-read' : 'message-item-unread';
            const priority = m.priority || 'normal';
            const priorityClass = `message-item-${priority}`;
            const icon = priorityIcons[priority] || priorityIcons.normal;

            return `<div class="message-item ${readClass} ${priorityClass}" data-msg-id="${m.id}">
                <div class="message-item-icon">${icon}</div>
                <div class="message-item-content">
                    <div class="message-item-header">
                        <strong class="message-item-title">${escapeHtml(m.title)}</strong>
                        ${!m.is_read ? `<button class="message-item-mark-read" onclick="markSingleMessageRead(${m.id})" title="Marquer comme lu">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                        </button>` : ''}
                    </div>
                    ${m.body ? `<p class="message-item-body">${escapeHtml(m.body)}</p>` : ''}
                    ${m.action_url ? `
                        <div class="message-item-action-wrapper">
                            <a href="${escapeHtml(m.action_url)}" target="_blank" rel="noopener noreferrer" class="message-item-action">
                                ${escapeHtml(m.action_label || 'Consulter')}
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                            </a>
                        </div>
                    ` : ''}
                    <div class="message-item-meta">
                        ${(m.sender_app_slug || m.sender_type) ? `
                            <span class="message-item-sender">
                                <span class="message-item-sender-dot"></span>
                                ${escapeHtml(m.sender_app_slug || m.sender_type)}
                            </span>
                        ` : ''}
                        <span class="message-item-time">
                            <svg class="message-item-time-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            ${time}
                        </span>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (error) {
        document.getElementById('messagePanelBody').innerHTML = '<p class="text-danger text-center small mt-3">Erreur chargement</p>';
    }
}

async function markSingleMessageRead(messageId) {
    try {
        await api.markMessageRead(messageId);
        // Mettre à jour visuellement
        const item = document.querySelector(`.message-item[data-msg-id="${messageId}"]`);
        if (item) {
            item.classList.remove('message-item-unread');
            item.classList.add('message-item-read');
            const btn = item.querySelector('.message-item-mark-read');
            if (btn) btn.remove();
        }
        // Décrémenter le badge immédiatement (optimiste)
        const badge = document.getElementById('messageBellBadge');
        const current = parseInt(badge?.textContent) || 0;
        updateMessageBellBadge(Math.max(0, current - 1));
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function markAllUserMessagesRead() {
    try {
        await api.markAllMessagesRead();
        updateMessageBellBadge(0);
        loadUserMessages();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function handleIncomingMessage(msgData) {
    // Incrémenter le badge
    const badge = document.getElementById('messageBellBadge');
    if (badge) {
        const current = parseInt(badge.textContent) || 0;
        updateMessageBellBadge(current + 1);
    }

    // Afficher une carte de notification flottante (style HUBapp_TEMPLATE)
    showNotificationCard(msgData);

    // Si le panneau de messages est ouvert, le rafraîchir
    const panel = document.getElementById('messagePanel');
    if (panel && !panel.classList.contains('d-none')) {
        loadUserMessages();
    }
}

// === Notification Cards (floating, top-right, HUBapp_TEMPLATE style) ===

const _activeNotificationCards = [];

function showNotificationCard(msgData) {
    const container = document.getElementById('notificationBanner');
    if (!container) return;

    const id = msgData.id || Date.now();
    const priority = msgData.priority || 'normal';

    // Icon SVG selon la priorité (style lucide)
    const icons = {
        urgent: '<svg class="notif-card-icon-svg notif-icon-urgent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
        warning: '<svg class="notif-card-icon-svg notif-icon-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        normal: '<svg class="notif-card-icon-svg notif-icon-normal" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    };

    const time = msgData.created_at
        ? new Date(msgData.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
        : new Date().toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

    const card = document.createElement('div');
    card.className = `notif-card notif-card-${priority}`;
    card.dataset.notifId = id;

    card.innerHTML = `
        <div class="notif-card-icon">${icons[priority] || icons.normal}</div>
        <div class="notif-card-content">
            <div class="notif-card-header">
                <strong class="notif-card-title">${escapeHtml(msgData.title || 'Notification')}</strong>
                <button class="notif-card-close" onclick="dismissNotificationCard(${id}, true)" title="Fermer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            ${msgData.body ? `<p class="notif-card-body">${escapeHtml(msgData.body)}</p>` : ''}
            ${msgData.action_url ? `
                <div class="notif-card-action-wrapper">
                    <a href="${escapeHtml(msgData.action_url)}" target="_blank" rel="noopener noreferrer" class="notif-card-action" onclick="dismissNotificationCard(${id}, true)">
                        ${escapeHtml(msgData.action_label || 'Consulter')}
                        <svg class="notif-card-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </a>
                </div>
            ` : ''}
            <div class="notif-card-meta">
                ${(msgData.sender_app_slug || msgData.sender_type) ? `
                    <span class="notif-card-sender">
                        <span class="notif-card-sender-dot"></span>
                        ${escapeHtml(msgData.sender_app_slug || msgData.sender_type)}
                    </span>
                ` : ''}
                <span class="notif-card-time">
                    <svg class="notif-card-time-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    ${time}
                </span>
            </div>
        </div>
    `;

    _activeNotificationCards.push(id);
    container.appendChild(card);
    updateNotificationBannerControls();

    // Auto-dismiss après 15s (sauf urgent) — sans marquer comme lu
    if (priority !== 'urgent') {
        setTimeout(() => dismissNotificationCard(id, false), 15000);
    }
}

function dismissNotificationCard(id, markAsRead = false) {
    const container = document.getElementById('notificationBanner');
    const card = container?.querySelector(`[data-notif-id="${id}"]`);
    if (card) {
        card.style.animation = 'notifCardSlideOut 0.25s ease-in forwards';
        setTimeout(() => {
            card.remove();
            const idx = _activeNotificationCards.indexOf(id);
            if (idx !== -1) _activeNotificationCards.splice(idx, 1);
            updateNotificationBannerControls();
        }, 250);

        // Marquer le message comme lu et décrémenter le badge immédiatement
        if (markAsRead && id) {
            api.markMessageRead(id).then(() => {
                const badge = document.getElementById('messageBellBadge');
                const current = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, current - 1));
                // Rafraîchir le panneau de messages s'il est ouvert
                const panel = document.getElementById('messagePanel');
                if (panel && !panel.classList.contains('d-none')) {
                    loadUserMessages();
                }
            }).catch(() => {});
        }
    }
}

function dismissAllNotificationCards() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    // Marquer tous les messages des cartes visibles comme lus
    const cardIds = Array.from(container.querySelectorAll('.notif-card'))
        .map(c => parseInt(c.dataset.notifId))
        .filter(id => !isNaN(id));
    container.querySelectorAll('.notif-card').forEach(card => {
        card.style.animation = 'notifCardSlideOut 0.2s ease-in forwards';
    });
    setTimeout(() => {
        container.querySelectorAll('.notif-card').forEach(c => c.remove());
        _activeNotificationCards.length = 0;
        updateNotificationBannerControls();
    }, 250);
    // Marquer comme lus et rafraîchir le badge
    if (cardIds.length > 0) {
        Promise.all(cardIds.map(id => api.markMessageRead(id).catch(() => {})))
            .then(() => loadUnreadMessageCount());
    }
}

function updateNotificationBannerControls() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    let controls = container.querySelector('.notif-banner-controls');

    const cards = container.querySelectorAll('.notif-card');
    if (cards.length > 1) {
        if (!controls) {
            controls = document.createElement('div');
            controls.className = 'notif-banner-controls';
            container.prepend(controls);
        }
        controls.innerHTML = `
            <span class="notif-banner-count">${cards.length} notifications</span>
            <button class="notif-banner-dismiss-all" onclick="dismissAllNotificationCards()">
                Tout fermer
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        `;
    } else if (controls) {
        controls.remove();
    }
}

// =============================================================================
