/**
 * Hub Application - Main JavaScript
 */

let currentUser = null;
let allApps = [];
let allServiceAccounts = [];
let hubHost = null;  // IP/hostname du Hub (chargé depuis l'API)

// Tracking des fenêtres d'applications ouvertes pour fermeture automatique au logout
const openedAppWindows = new Map();

// Bootstrap instances
let adminModal, appFormModal, userFormModal, serviceAccountFormModal, saCredentialsModal, toast;

// ===== Server-Sent Events (SSE) pour mises à jour temps réel =====
let eventSource = null;
let sseWasConnected = false; // Indique si on a déjà été connecté (pour détecter les reconnexions)

/**
 * Initialise la connexion SSE pour recevoir les mises à jour en temps réel
 * Passe l'user_id en query param pour le tracking de session côté serveur
 * @param {boolean} isReconnect - true si c'est une reconnexion après une déconnexion
 */
function initEventSource(isReconnect = false) {
    // Fermer toute connexion existante
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    
    // Ne pas connecter si pas authentifié
    if (!api.token) {
        return;
    }
    
    try {
        // Passer l'user_id pour le tracking de session
        const userId = currentUser?.id || '';
        const sseUrl = userId ? `/api/events/stream?user_id=${userId}` : '/api/events/stream';
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
            setTimeout(() => {
                if (api.token && currentUser) {
                    initEventSource(true); // Marquer comme reconnexion
                }
            }, 5000);
        };
    } catch (e) {
        console.warn('SSE: impossible de créer la connexion', e);
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
        
        // Rafraîchir les applications (page principale)
        if (currentPage === 'dashboard' || currentPage === 'apps') {
            await loadApps();
        }
        
        // Rafraîchir selon la page courante
        switch (currentPage) {
            case 'users':
                loadUsers();
                break;
            case 'service-accounts':
                loadServiceAccounts();
                break;
            case 'audit':
                loadAuditPageOverview();
                break;
            case 'apps-admin':
                loadAllApps();
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
        case 'apps_updated':
            // Rafraîchir la liste des applications selon la page
            if (currentPage === 'apps-admin') {
                // Page admin : recharger tout le tableau et relancer les tests
                loadAllApps().then(() => {
                    // Relancer le test de connectivité après rechargement
                    setTimeout(() => refreshAppsStatus(), 500);
                });
            } else {
                loadApps();
            }
            
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
            }
            const appName = data.data?.app_name || 'Application';
            showToast('Mise à jour', `${appName} ${appAction}`, toastType);
            
            // Rafraîchir le token si une app a été créée ou modifiée (par un autre admin)
            // MAIS seulement si ce n'est pas l'utilisateur courant qui a fait l'action
            // (pour éviter un double refresh qui invaliderait le token)
            if (data.data?.action === 'created' || data.data?.action === 'updated') {
                // Vérifier si un refresh n'est pas déjà en cours (éviter double refresh)
                if (!api._refreshPromise) {
                    refreshTokenAfterAppChange(null, true); // silencieux via SSE
                }
            }
            break;
            
        case 'docker_status_changed':
            // Événement dédié pour les changements de statut Docker
            if (currentPage === 'apps-admin') {
                updateAppDockerStatusInTable(data.data?.slug, data.data?.status);
            }
            // Aussi mettre à jour sur le dashboard
            updateAppCardStatus(data.data?.slug, data.data?.status);
            break;
            
        case 'users_updated':
            // Rafraîchir la liste des utilisateurs si on est sur cette page
            if (currentPage === 'users') {
                loadUsersPageData();
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
            break;
            
        case 'force_logout':
            // Vérifier si c'est l'utilisateur courant qui doit être déconnecté
            if (currentUser && data.data?.user_id === currentUser.id) {
                handleForceLogout(data.data.reason);
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

function setPendingCallback(url) {
    if (url) {
        sessionStorage.setItem('sso_pending_callback', url);
    }
}

function clearPendingCallback() {
    sessionStorage.removeItem('sso_pending_callback');
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Bootstrap components
    adminModal = new bootstrap.Modal(document.getElementById('adminModal'));
    appFormModal = new bootstrap.Modal(document.getElementById('appFormModal'));
    userFormModal = new bootstrap.Modal(document.getElementById('userFormModal'));
    serviceAccountFormModal = new bootstrap.Modal(document.getElementById('serviceAccountFormModal'));
    saCredentialsModal = new bootstrap.Modal(document.getElementById('saCredentialsModal'));
    toast = new bootstrap.Toast(document.getElementById('toast'));

    // Démarrer/arrêter le rafraîchissement de connectivité selon l'état de la modal admin
    const adminModalEl = document.getElementById('adminModal');
    adminModalEl.addEventListener('shown.bs.modal', () => {
        // La modal admin s'ouvre - on est par défaut sur l'onglet apps
        currentPage = 'apps-admin';
        startConnectivityRefresh();
    });
    adminModalEl.addEventListener('hidden.bs.modal', () => {
        // La modal admin se ferme - arrêter le rafraîchissement
        currentPage = 'dashboard';
        stopConnectivityRefresh();
    });
    
    // Gérer le changement d'onglet dans la modal admin
    const adminTabsEl = document.getElementById('adminTabs');
    if (adminTabsEl) {
        adminTabsEl.addEventListener('shown.bs.tab', (event) => {
            const targetId = event.target.getAttribute('data-bs-target');
            if (targetId === '#tabApps') {
                currentPage = 'apps-admin';
                startConnectivityRefresh();
            } else {
                // Autre onglet (Approbations, Service Accounts)
                stopConnectivityRefresh();
                if (targetId === '#tabPendingApps') {
                    currentPage = 'pending-apps';
                } else if (targetId === '#tabServiceAccounts') {
                    currentPage = 'service-accounts';
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
                    user_id: currentUser.id
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
    const httpsEl = document.getElementById('appFrontendHttps');
    if (httpsEl) httpsEl.addEventListener('change', updateUrlPreview);
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
    }

    // Initialiser les mises à jour temps réel via SSE
    initEventSource();

    loadApps();
}

// ===== Page Navigation =====

let currentPage = 'dashboard';

function showPage(page) {
    // Masquer toutes les sections
    document.getElementById('dashboardSection').classList.add('d-none');
    document.getElementById('auditSection').classList.add('d-none');
    document.getElementById('usersSection').classList.add('d-none');
    document.getElementById('discoverySection').classList.add('d-none');
    
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
            loadUsersPageData();
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
    document.getElementById('userInfo').classList.add('d-none');
    document.getElementById('logoutBtn').classList.add('d-none');
    document.getElementById('changePasswordBtn').classList.add('d-none');
    document.getElementById('adminBtn').classList.add('d-none');
    document.getElementById('adminNavigation').classList.add('d-none');
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
            renderAppsGrid(apps);
        } else {
            console.error('loadApps: réponse inattendue (pas un tableau):', apps);
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
        grid.innerHTML = `
            <div class="col-12 empty-state">
                <i class="bi bi-inbox"></i>
                <h4>Aucune application</h4>
                <p>Contactez un administrateur pour obtenir l'accès aux applications.</p>
            </div>
        `;
        return;
    }

    const isAdmin = currentUser?.is_admin;

    grid.innerHTML = apps.map(app => {
        // Déterminer si l'app est en maintenance (conteneur Docker arrêté)
        const isInMaintenance = app.docker_status && app.docker_status !== 'running';
        const isUnavailable = !app.is_active || isInMaintenance;
        
        return `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card shadow-sm h-100 ${isUnavailable ? 'app-inactive' : ''}" 
                 data-app-slug="${app.slug}"
                 onclick="${!isUnavailable ? `openApp('${app.slug}', '${app.frontend_url || ''}')` : `showToast('Application en maintenance', 'Cette application est temporairement indisponible.', 'warning')`}">
                <div class="card-body position-relative">
                    <div class="app-icon" style="background-color: ${app.color}">
                        <i class="${app.icon}"></i>
                    </div>
                    <h5>${escapeHtml(app.name)}</h5>
                    <p>${escapeHtml(app.description || 'Aucune description')}</p>
                    ${isAdmin ? `
                        <div class="app-role-selector mt-2" onclick="event.stopPropagation()">
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

function openApp(slug, frontendUrl) {
    if (!frontendUrl) {
        showToast('Information', 'Cette application n\'a pas de frontend configuré', 'warning');
        return;
    }

    // Logger l'accès à l'application (async, non bloquant)
    api.logAppAccess(slug).catch(e => console.warn('Échec log accès app:', e));

    // Nettoyer les références aux fenêtres déjà fermées
    cleanupClosedWindows();

    // Rediriger avec le token dans un nouvel onglet
    const separator = frontendUrl.includes('?') ? '&' : '?';
    const url = `${frontendUrl}${separator}access_token=${api.token}`;
    
    // Ouvrir avec un nom unique pour pouvoir tracker la fenêtre
    const windowName = `hub_app_${slug}_${Date.now()}`;
    const appWindow = window.open(url, windowName);
    
    // Stocker la référence pour fermeture automatique au logout
    if (appWindow) {
        openedAppWindows.set(windowName, appWindow);
        console.log(`Application ${slug} ouverte dans l'onglet ${windowName}`);
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

async function showAdminPanel() {
    try {
        await loadAdminData();
        await loadPendingAppsCount();  // Charger le compteur d'apps en attente
        adminModal.show();
    } catch (error) {
        console.error('Erreur chargement admin:', error);
        // Si session expirée, l'événement hub:sessionExpired sera déclenché automatiquement
        // On n'a plus besoin de gérer ça ici manuellement
        if (error.message !== 'Session expirée') {
            showToast('Erreur', 'Impossible de charger les données admin', 'danger');
        }
    }
}

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
    // Afficher le spinner sur toutes les cellules de connectivité
    document.querySelectorAll('.app-connectivity').forEach(cell => {
        cell.innerHTML = '<span class="spinner-border spinner-border-sm text-primary" role="status"></span>';
    });
    
    try {
        const status = await api.getAppsStatus();
        console.log('📡 Statut des applications:', status);
        
        // Mettre à jour chaque cellule
        status.apps.forEach(appStatus => {
            const cell = document.querySelector(`.app-connectivity[data-app-slug="${appStatus.slug}"]`);
            if (cell) {
                let badge = '';
                switch (appStatus.status) {
                    case 'online':
                        badge = `<span class="badge bg-success" title="Temps de réponse: ${appStatus.response_time_ms}ms">
                            <i class="bi bi-wifi me-1"></i>En ligne
                        </span>`;
                        if (appStatus.response_time_ms) {
                            badge += `<br><small class="text-muted">${appStatus.response_time_ms}ms</small>`;
                        }
                        break;
                    case 'offline':
                        badge = `<span class="badge bg-danger" title="${appStatus.error || 'Non joignable'}">
                            <i class="bi bi-wifi-off me-1"></i>Hors ligne
                        </span>`;
                        break;
                    case 'error':
                        badge = `<span class="badge bg-warning text-dark" title="${appStatus.error || 'Erreur'}">
                            <i class="bi bi-exclamation-triangle me-1"></i>Erreur
                        </span>`;
                        break;
                    default:
                        badge = `<span class="badge bg-light text-muted">
                            <i class="bi bi-question-circle me-1"></i>Inconnu
                        </span>`;
                }
                cell.innerHTML = badge;
            }
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
        // Afficher erreur dans les cellules
        document.querySelectorAll('.app-connectivity').forEach(cell => {
            cell.innerHTML = '<span class="badge bg-secondary"><i class="bi bi-x-circle me-1"></i>Erreur</span>';
        });
    }
}

/**
 * Met à jour le statut Docker d'une app dans le tableau admin
 */
function updateAppDockerStatusInTable(slug, dockerStatus) {
    if (!slug) return;
    
    // Trouver la ligne correspondante
    const row = document.querySelector(`tr[data-app-id] .app-connectivity[data-app-slug="${slug}"]`)?.closest('tr');
    if (!row) return;
    
    // Trouver la cellule Docker (6ème colonne, index 5)
    const dockerCell = row.children[5];
    if (!dockerCell) return;
    
    // Mettre à jour le badge Docker
    let dockerBadge;
    if (!dockerStatus) {
        dockerBadge = '<span class="badge bg-light text-muted"><i class="bi bi-question-circle me-1"></i>Inconnu</span>';
    } else if (dockerStatus === 'running') {
        dockerBadge = '<span class="badge bg-light text-dark"><i class="bi bi-play-circle text-success me-1"></i>Running</span>';
    } else {
        dockerBadge = '<span class="badge bg-warning text-dark"><i class="bi bi-pause-circle me-1"></i>Maintenance</span>';
    }
    dockerCell.innerHTML = dockerBadge;
    
    console.log(`🔄 Docker status mis à jour pour ${slug}: ${dockerStatus}`);
}

/**
 * Met à jour le statut d'une carte d'app sur le dashboard
 */
function updateAppCardStatus(slug, dockerStatus) {
    if (!slug) return;
    
    const card = document.querySelector(`.app-card[data-app-slug="${slug}"]`);
    if (!card) return;
    
    const isInMaintenance = dockerStatus && dockerStatus !== 'running';
    
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
    // Afficher le spinner sur toutes les cellules de statut SA
    document.querySelectorAll('.sa-credentials-status').forEach(cell => {
        cell.innerHTML = '<span class="spinner-border spinner-border-sm text-primary" role="status"></span>';
    });
    
    try {
        const status = await api.getServiceAccountsStatus();
        console.log('📡 Statut des Service Accounts:', status);
        
        // Mettre à jour chaque cellule
        status.service_accounts.forEach(saStatus => {
            const cell = document.querySelector(`.sa-credentials-status[data-sa-id="${saStatus.id}"]`);
            if (cell) {
                let badge = '';
                switch (saStatus.credentials_status) {
                    case 'file_exists':
                        badge = `<span class="badge bg-success" title="Fichier: ${saStatus.credentials_file}">
                            <i class="bi bi-file-earmark-check me-1"></i>Credentials OK
                        </span>`;
                        break;
                    case 'file_missing':
                        badge = `<span class="badge bg-danger" title="Fichier de credentials manquant">
                            <i class="bi bi-file-earmark-x me-1"></i>Fichier manquant
                        </span>`;
                        break;
                    case 'no_app':
                        badge = `<span class="badge bg-secondary" title="Pas d'application associée">
                            <i class="bi bi-dash-circle me-1"></i>Pas d'app
                        </span>`;
                        break;
                    default:
                        badge = `<span class="badge bg-light text-muted">
                            <i class="bi bi-question-circle me-1"></i>Inconnu
                        </span>`;
                }
                cell.innerHTML = badge;
            }
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
        document.querySelectorAll('.sa-credentials-status').forEach(cell => {
            cell.innerHTML = '<span class="badge bg-secondary"><i class="bi bi-x-circle me-1"></i>Erreur</span>';
        });
    }
}

function renderAppsTable(apps) {
    const container = document.getElementById('appsTable');
    
    if (apps.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucune application configurée</p>';
        return;
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
                    <th>Config</th>
                    <th>Docker</th>
                    <th>Connectivité</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody id="appsTableBody">
                ${apps.map(app => {
                    // Déterminer le statut Docker
                    const dockerStatus = app.docker_status;
                    const isRunning = !dockerStatus || dockerStatus === 'running';
                    const dockerBadge = !dockerStatus 
                        ? '<span class="badge bg-light text-muted"><i class="bi bi-question-circle me-1"></i>Inconnu</span>'
                        : dockerStatus === 'running'
                            ? '<span class="badge bg-light text-dark"><i class="bi bi-play-circle text-success me-1"></i>Running</span>'
                            : '<span class="badge bg-warning text-dark"><i class="bi bi-pause-circle me-1"></i>Maintenance</span>';
                    
                    return `
                    <tr data-app-id="${app.id}">
                        <td>
                            <div class="d-flex align-items-center">
                                <span class="me-2 rounded p-1" style="background-color: ${app.color}; color: white;">
                                    <i class="${app.icon}"></i>
                                </span>
                                <div>
                                    <strong>${escapeHtml(app.name)}</strong>
                                    ${app.is_shared_service ? '<span class="badge bg-info ms-1" title="API partagée accessible par tous les SA"><i class="bi bi-share"></i></span>' : ''}
                                </div>
                            </div>
                        </td>
                        <td><code>${app.slug}</code></td>
                        <td><small class="text-muted">${escapeHtml(app.frontend_url || '-')}</small></td>
                        <td><span class="badge bg-secondary">${app.users?.length || 0}</span></td>
                        <td>
                            ${app.is_active 
                                ? '<span class="badge bg-light text-dark"><i class="bi bi-check-circle text-success me-1"></i>Actif</span>' 
                                : '<span class="badge bg-light text-dark"><i class="bi bi-x-circle text-danger me-1"></i>Inactif</span>'}
                        </td>
                        <td>${dockerBadge}</td>
                        <td class="app-connectivity" data-app-slug="${app.slug}">
                            <span class="badge bg-light text-muted"><i class="bi bi-question-circle me-1"></i>Non testé</span>
                        </td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-info me-1" onclick="showDockerDetails('${app.slug}')" title="Détails Docker">
                                <i class="bi bi-eye"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-primary me-1" onclick="editApp(${app.id})">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="deleteApp(${app.id}, '${escapeHtml(app.name)}')">
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    </tr>
                `}).join('')}
            </tbody>
        </table>
    `;
    
    // Lancer automatiquement le test de connectivité
    refreshAppsStatus();
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
            hubHost = window.location.hostname;  // Fallback
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
    const httpsCheckboxAdd = document.getElementById('appFrontendHttps');
    if (httpsCheckboxAdd) httpsCheckboxAdd.checked = false;
    
    // Afficher les champs de création
    document.getElementById('optionalFieldsSection').classList.remove('d-none');
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.remove('d-none');
    
    // Afficher mode simplifié (ports), masquer mode avancé (URLs)
    document.getElementById('simplePortsSection').classList.remove('d-none');
    document.getElementById('advancedUrlsSection').classList.add('d-none');
    document.getElementById('appApiPort').required = true;
    document.getElementById('appApiUrl').required = false;
    
    // Bouton
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    // Réinitialiser et masquer le picker d'icônes
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    updateUrlPreview();
    adminModal.hide();
    appFormModal.show();
}

// ===== Docker Details Modal =====

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
    
    const modal = new bootstrap.Modal(modalEl);
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
                    
                    <!-- Info développeur -->
                    <div class="card border-info">
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
        // Tenter de recharger les données
        try {
            await loadAdminData();
        } catch (e) {
            console.error('Impossible de recharger les apps:', e);
        }
        return;
    }

    // Réinitialiser le formulaire avant de remplir les valeurs
    document.getElementById('appForm').reset();
    
    document.getElementById('appFormTitle').textContent = 'Modifier l\'Application';
    document.getElementById('appId').value = app.id;
    document.getElementById('appMode').value = 'edit';
    
    // Afficher les champs d'édition
    document.getElementById('optionalFieldsSection').classList.remove('d-none');
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.add('d-none');  // Masquer l'info M2M en édition
    
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
    
    // En mode édition, afficher les URLs directes
    document.getElementById('simplePortsSection').classList.add('d-none');
    document.getElementById('advancedUrlsSection').classList.remove('d-none');
    document.getElementById('appApiPort').required = false;
    document.getElementById('appApiUrl').required = true;
    
    // Bouton en mode édition
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    // Réinitialiser et masquer le picker d'icônes
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    
    adminModal.hide();
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
    const frontendHttpsEl = document.getElementById('appFrontendHttps');
    const frontendHttps = frontendHttpsEl ? frontendHttpsEl.checked : false;
    
    // Utiliser le hub host (chargé depuis l'API) ou le custom host
    const host = customHost && customHostValue ? customHostValue : (hubHost || 'chargement...');
    
    const previewApi = document.getElementById('previewApiUrl');
    const previewFrontend = document.getElementById('previewFrontendUrl');
    
    if (apiPort) {
        previewApi.innerHTML = `<i class="bi bi-hdd-rack me-1"></i>API: <code>http://${host}:${apiPort}</code>`;
    } else {
        previewApi.innerHTML = '<i class="bi bi-hdd-rack me-1"></i>API: <span class="text-danger">Port requis</span>';
    }
    
    if (frontendPort) {
        const frontendProtocol = frontendHttps ? 'https' : 'http';
        previewFrontend.innerHTML = `<i class="bi bi-window me-1"></i>Frontend: <code>${frontendProtocol}://${host}:${frontendPort}</code>`;
    } else {
        previewFrontend.innerHTML = '<i class="bi bi-window me-1"></i>Frontend: <span class="text-muted">Non défini</span>';
    }
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
            
            // Rafraîchir le token pour refléter les modifications
            await refreshTokenAfterAppChange('modification');
            
            appFormModal.hide();
            await loadAdminData();
            adminModal.show();
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
                frontend_https: document.getElementById('appFrontendHttps')?.checked || false,
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
            adminModal.show();
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
    // Met à jour la sélection dans le picker si ouvert
    updateIconPickerSelection();
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
    adminModal.hide();
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
    
    adminModal.hide();
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

    // Créer un map des rôles par app_id
    const rolesByAppId = {};
    userAppRoles.forEach(ar => {
        rolesByAppId[ar.app_id] = ar.role;
    });

    container.innerHTML = allApps.map(app => {
        const currentRole = rolesByAppId[app.id] || '';
        const isChecked = currentRole !== '';
        
        return `
            <div class="d-flex align-items-center mb-2 app-role-row">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input app-checkbox" type="checkbox" value="${app.id}" 
                        id="userApp${app.id}" ${isChecked ? 'checked' : ''}
                        onchange="toggleRoleSelect(${app.id})">
                    <label class="form-check-label" for="userApp${app.id}">
                        <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                        ${escapeHtml(app.name)}
                    </label>
                </div>
                <select class="form-select form-select-sm role-select" id="userAppRole${app.id}" 
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
    
    // is_active n'est pas dans le schéma UserCreate, uniquement pour la mise à jour
    if (!isCreating) {
        data.is_active = document.getElementById('userActive').checked;
    }

    const password = document.getElementById('userPassword').value;
    if (password) {
        data.password = password;
    }

    // Récupérer les apps et leurs rôles
    const assignments = [];
    allApps.forEach(app => {
        const checkbox = document.getElementById(`userApp${app.id}`);
        const roleSelect = document.getElementById(`userAppRole${app.id}`);
        
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
            const newUser = await api.createUser(data);
            userId = newUser.id;
        }
        
        // Mettre à jour les accès et rôles
        await api.setUserApps(userId, assignments);
        
        showToast('Succès', id ? 'Utilisateur modifié' : 'Utilisateur créé', 'success');
        userFormModal.hide();
        
        // Recharger la page appropriée
        if (currentPage === 'users') {
            await loadUsersPageData();
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
                        <th>Slug</th>
                        <th>API URL</th>
                        <th>Service partagé</th>
                        <th>Date</th>
                        <th class="text-end">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${pendingApps.map(app => `
                        <tr>
                            <td>
                                <i class="${escapeHtml(app.icon)} me-2" style="color: ${escapeHtml(app.color)}"></i>
                                <strong>${escapeHtml(app.name)}</strong>
                            </td>
                            <td><code>${escapeHtml(app.slug)}</code></td>
                            <td><small class="text-muted">${escapeHtml(app.api_url)}</small></td>
                            <td>
                                ${app.is_shared_service 
                                    ? '<span class="badge bg-info"><i class="bi bi-share me-1"></i>Partagé</span>' 
                                    : '<span class="text-muted">-</span>'}
                            </td>
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
        await loadPendingApps();  // Rafraîchir la liste
        await loadAdminData();    // Rafraîchir les apps principales
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
        await loadPendingApps();  // Rafraîchir la liste
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
        await loadPendingApps();  // Rafraîchir la liste
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
                    <th>Client ID</th>
                    <th>Applications</th>
                    <th>Dernière utilisation</th>
                    <th>Config</th>
                    <th>Credentials</th>
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
                            </div>
                        </td>
                        <td><code class="small">${sa.client_id}</code></td>
                        <td>
                            ${sa.applications?.map(a => `<span class="badge bg-light text-dark me-1">${escapeHtml(a.name)}</span>`).join('') || '<span class="text-muted">-</span>'}
                        </td>
                        <td>
                            ${sa.last_used_at 
                                ? `<small class="text-success"><i class="bi bi-check2 me-1"></i>${new Date(sa.last_used_at).toLocaleString('fr-FR')}</small>`
                                : '<small class="text-muted"><i class="bi bi-hourglass me-1"></i>Jamais</small>'}
                        </td>
                        <td>
                            ${sa.is_active 
                                ? '<span class="badge bg-light text-dark"><i class="bi bi-check-circle text-success me-1"></i>Actif</span>' 
                                : '<span class="badge bg-light text-dark"><i class="bi bi-x-circle text-danger me-1"></i>Inactif</span>'}
                        </td>
                        <td class="sa-credentials-status" data-sa-id="${sa.id}">
                            <span class="badge bg-light text-muted"><i class="bi bi-question-circle me-1"></i>Non vérifié</span>
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
    adminModal.hide();
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
    
    adminModal.hide();
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
    
    // Initialiser les tooltips Bootstrap
    const tooltipTriggerList = container.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(el => new bootstrap.Tooltip(el));
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
        if (id) {
            adminModal.show();
        }
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
let dockerSelection = { api: null, frontend: null };

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
    
    adminModal.hide();
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
    
    // Reset sélection
    dockerSelection = { api: null, frontend: null };
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
    
    try {
        const result = await api.scanDockerContainers(includeStopped, showAll);
        lastDockerScan = result;
        
        document.getElementById('dockerStatus').classList.add('d-none');
        
        if (!result.available) {
            // Docker non disponible
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
        
        // Afficher les conteneurs découverts
        if (result.discovered && result.discovered.length > 0) {
            document.getElementById('dockerContainers').classList.remove('d-none');
            renderDockerContainers(result.discovered);
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
 * Affiche la liste des conteneurs découverts avec sélection API/Frontend
 */
function renderDockerContainers(containers) {
    const list = document.getElementById('containersList');
    
    list.innerHTML = containers.map(container => {
        const ports = container.ports.map(p => {
            if (p.host_port) {
                return `<span class="badge bg-primary me-1">${p.host_port}:${p.container_port}</span>`;
            }
            return `<span class="badge bg-secondary me-1">${p.container_port}</span>`;
        }).join('');
        
        const statusClass = container.status === 'running' ? 'success' : 'secondary';
        const statusIcon = container.status === 'running' ? 'play-circle-fill' : 'stop-circle-fill';
        
        // Vérifier si ce conteneur est sélectionné
        const isApi = dockerSelection.api?.container_id === container.container_id;
        const isFrontend = dockerSelection.frontend?.container_id === container.container_id;
        const selectedClass = isApi ? 'border-danger border-2' : (isFrontend ? 'border-success border-2' : '');
        const selectedBadge = isApi ? '<span class="badge bg-danger ms-2">API</span>' : 
                            (isFrontend ? '<span class="badge bg-success ms-2">Frontend</span>' : '');
        
        // Conteneur déjà enregistré ?
        const isRegistered = container.is_registered;
        const registeredBadge = isRegistered ? '<span class="badge bg-warning text-dark ms-2"><i class="bi bi-check-circle me-1"></i>Déjà enregistré</span>' : '';
        const registeredClass = isRegistered ? 'opacity-50' : '';
        
        return `
            <div class="list-group-item ${selectedClass} ${registeredClass}" id="container-${container.container_id}">
                <div class="d-flex w-100 justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-${statusIcon} text-${statusClass} me-2"></i>
                            <strong>${escapeHtml(container.name)}</strong>
                            ${selectedBadge}
                            ${registeredBadge}
                            <small class="text-muted ms-2">(${escapeHtml(container.container_id)})</small>
                        </div>
                        <small class="text-muted d-block mb-2">
                            <i class="bi bi-box me-1"></i>${escapeHtml(container.image)}
                        </small>
                        <div>
                            <i class="bi bi-ethernet me-1 small text-muted"></i>
                            ${ports || '<span class="text-muted small">Aucun port exposé</span>'}
                        </div>
                    </div>
                    <div class="text-end">
                        <div class="btn-group btn-group-sm mb-2">
                            <button type="button" class="btn ${isApi ? 'btn-danger' : 'btn-outline-danger'}" 
                                    onclick="selectDockerContainer('${container.container_id}', 'api')"
                                    title="Définir comme API Backend"
                                    ${isRegistered ? 'disabled' : ''}>
                                <i class="bi bi-hdd-rack"></i> API
                            </button>
                            <button type="button" class="btn ${isFrontend ? 'btn-success' : 'btn-outline-success'}" 
                                    onclick="selectDockerContainer('${container.container_id}', 'frontend')"
                                    title="Définir comme Frontend"
                                    ${isRegistered ? 'disabled' : ''}>
                                <i class="bi bi-window"></i> Front
                            </button>
                        </div>
                        <br>
                        <span class="badge bg-light text-dark">
                            <i class="bi bi-tag me-1"></i>${escapeHtml(container.suggested_slug)}
                        </span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Sélectionne un conteneur comme API ou Frontend
 */
function selectDockerContainer(containerId, type) {
    if (!lastDockerScan || !lastDockerScan.discovered) return;
    
    const container = lastDockerScan.discovered.find(c => c.container_id === containerId);
    if (!container) return;
    
    // Si déjà sélectionné pour ce type, désélectionner
    if (dockerSelection[type]?.container_id === containerId) {
        dockerSelection[type] = null;
    } else {
        // Si ce conteneur était sélectionné pour l'autre type, le retirer
        const otherType = type === 'api' ? 'frontend' : 'api';
        if (dockerSelection[otherType]?.container_id === containerId) {
            dockerSelection[otherType] = null;
        }
        dockerSelection[type] = container;
    }
    
    // Rafraîchir l'affichage
    renderDockerContainers(lastDockerScan.discovered);
    updateDockerSelectionSummary();
}

/**
 * Met à jour le résumé de la sélection
 */
function updateDockerSelectionSummary() {
    const summary = document.getElementById('dockerSelectionSummary');
    const importBtn = document.getElementById('dockerImportBtn');
    const quickImportBtn = document.getElementById('dockerQuickImportBtn');
    
    if (!summary || !importBtn) return;
    
    const hasApi = dockerSelection.api !== null;
    const hasFrontend = dockerSelection.frontend !== null;
    
    if (!hasApi && !hasFrontend) {
        summary.innerHTML = '<span class="text-muted">Aucune sélection</span>';
        importBtn.disabled = true;
        if (quickImportBtn) quickImportBtn.disabled = true;
    } else {
        let html = '';
        if (hasApi) {
            const port = dockerSelection.api.ports[0]?.host_port || '?';
            html += `<span class="badge bg-danger me-2"><i class="bi bi-hdd-rack me-1"></i>API: ${escapeHtml(dockerSelection.api.name)} (port ${port})</span>`;
        }
        if (hasFrontend) {
            const port = dockerSelection.frontend.ports[0]?.host_port || '?';
            html += `<span class="badge bg-success"><i class="bi bi-window me-1"></i>Frontend: ${escapeHtml(dockerSelection.frontend.name)} (port ${port})</span>`;
        }
        summary.innerHTML = html;
        importBtn.disabled = !hasApi; // Au minimum une API est requise
        if (quickImportBtn) quickImportBtn.disabled = !hasApi;
    }
}

/**
 * Import rapide : enregistre automatiquement l'application avec son Service Account
 */
async function quickImportDockerSelection() {
    if (!dockerSelection.api) {
        showToast('Erreur', 'Veuillez sélectionner au moins un conteneur API', 'warning');
        return;
    }
    
    const apiContainer = dockerSelection.api;
    const frontendContainer = dockerSelection.frontend;
    const isShared = document.getElementById('dockerIsShared')?.checked || false;
    
    // Trouver les ports
    const apiPort = apiContainer.ports[0]?.host_port || null;
    const frontendPort = frontendContainer?.ports[0]?.host_port || null;
    
    if (!apiPort) {
        showToast('Erreur', 'Le conteneur API doit avoir un port exposé sur l\'hôte', 'warning');
        return;
    }
    
    // Générer slug et nom
    let appSlug = apiContainer.suggested_slug.replace(/-?(api|backend)$/i, '') || apiContainer.suggested_slug;
    let appName = apiContainer.suggested_name.replace(/\s*(api|backend)\s*/gi, '').trim() || apiContainer.suggested_name;
    
    // Récupérer la description depuis les variables d'environnement du conteneur si disponible
    let appDescription = apiContainer.hub_description || `Importé depuis Docker: ${apiContainer.image}`;
    
    try {
        // Désactiver le bouton pendant l'opération
        const btn = document.getElementById('dockerQuickImportBtn');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Import...';
        
        // Appeler l'API auto-register
        const result = await api.autoRegisterApp(appSlug, appName, apiPort, frontendPort, isShared, appDescription);
        
        // Restaurer le bouton
        btn.innerHTML = originalHtml;
        btn.disabled = false;
        
        // Fermer la modal Docker
        dockerScanModal.hide();
        
        // Afficher le résultat
        const sharedMsg = isShared ? ' (API partagée M2M)' : '';
        if (result.is_new) {
            showToast('Succès', `Application "${appName}" créée${sharedMsg} avec Service Account !`, 'success');
            
            // Afficher les credentials si disponibles
            if (result.credentials?.client_secret) {
                setTimeout(() => {
                    alert(`🔐 Credentials M2M générés pour ${appName}:\n\nClient ID: ${result.credentials.client_id}\nClient Secret: ${result.credentials.client_secret}\n\n⚠️ Le secret ne sera plus affiché ! Fichier sauvé: ${result.secrets_file || 'secrets/service_accounts/sa_' + appSlug + '.env'}`);
                }, 500);
            }
        } else {
            // Application existante - vérifier si des mises à jour ont été faites
            if (result.message.includes('mise à jour')) {
                showToast('Mise à jour', result.message, 'success');
            } else {
                const currentShared = result.credentials?.is_shared ? 'partagée' : 'privée';
                showToast('Info', `Application "${appName}" existe déjà (${currentShared})`, 'info');
            }
        }
        
        // Rafraîchir les données admin
        await loadAdminData();
        loadApps();
        adminModal.show();
        
    } catch (error) {
        console.error('Erreur import rapide:', error);
        showToast('Erreur', error.message, 'danger');
        
        // Restaurer le bouton
        const btn = document.getElementById('dockerQuickImportBtn');
        if (btn) {
            btn.innerHTML = '<i class="bi bi-lightning-charge me-1"></i>Import rapide';
            btn.disabled = false;
        }
    }
}

/**
 * Importe la sélection Docker comme application (mode personnalisé)
 */
function importDockerSelection() {
    if (!dockerSelection.api) {
        showToast('Erreur', 'Veuillez sélectionner au moins un conteneur API', 'warning');
        return;
    }
    
    const apiContainer = dockerSelection.api;
    const frontendContainer = dockerSelection.frontend;
    const isShared = document.getElementById('dockerIsShared')?.checked || false;
    
    // Fermer la modal Docker
    dockerScanModal.hide();
    
    // Trouver les ports
    const apiPort = apiContainer.ports[0]?.host_port || null;
    const frontendPort = frontendContainer?.ports[0]?.host_port || null;
    
    // Générer un nom/slug à partir de la sélection
    let appName, appSlug, description;
    if (frontendContainer) {
        // On a API + Frontend - essayer de trouver un nom commun
        const apiName = apiContainer.suggested_name.replace(/\s*(api|backend)\s*/gi, '').trim();
        const frontName = frontendContainer.suggested_name.replace(/\s*(frontend|front|ui)\s*/gi, '').trim();
        // Prendre le nom le plus court ou le plus commun
        appName = apiName.length <= frontName.length ? apiName : frontName;
        if (!appName) appName = apiContainer.suggested_name;
        appSlug = apiContainer.suggested_slug.replace(/-?(api|backend)$/i, '');
        if (!appSlug) appSlug = apiContainer.suggested_slug;
        description = `API: ${apiContainer.image}\nFrontend: ${frontendContainer.image}`;
    } else {
        appName = apiContainer.suggested_name;
        appSlug = apiContainer.suggested_slug;
        description = `Importé depuis Docker: ${apiContainer.image}`;
    }
    
    // Pré-remplir le formulaire de création d'app
    document.getElementById('appFormTitle').textContent = 'Importer depuis Docker';
    document.getElementById('appForm').reset();
    document.getElementById('appId').value = '';
    document.getElementById('appMode').value = 'create';
    document.getElementById('appName').value = appName;
    document.getElementById('appSlug').value = appSlug;
    document.getElementById('appDescription').value = description;
    document.getElementById('appIcon').value = 'bi-box-seam';  // Icône Docker
    document.getElementById('appColor').value = '#0db7ed';     // Couleur Docker
    document.getElementById('appApiPort').value = apiPort || '';
    document.getElementById('appFrontendPort').value = frontendPort || '';
    const httpsCheckbox = document.getElementById('appFrontendHttps');
    if (httpsCheckbox) httpsCheckbox.checked = frontendPort === 443;
    document.getElementById('appActive').checked = apiContainer.status === 'running';
    document.getElementById('appRequiresAuth').checked = true;
    document.getElementById('appCustomHost').checked = false;
    document.getElementById('customHostGroup').classList.add('d-none');
    
    // Pré-remplir l'option is_shared depuis le scanner Docker
    const appIsSharedCheckbox = document.getElementById('appIsSharedService');
    if (appIsSharedCheckbox) appIsSharedCheckbox.checked = isShared;
    
    // Note: M2M est créé automatiquement lors de l'auto-enregistrement
    
    // Afficher mode simplifié (ports)
    document.getElementById('simplePortsSection').classList.remove('d-none');
    document.getElementById('advancedUrlsSection').classList.add('d-none');
    document.getElementById('appApiPort').required = true;
    document.getElementById('appApiUrl').required = false;
    
    updateIconPreview();
    updateUrlPreview();
    
    // Afficher le formulaire
    appFormModal.show();
    
    const msg = frontendContainer 
        ? `API "${apiContainer.name}" + Frontend "${frontendContainer.name}" pré-chargés.`
        : `Conteneur "${apiContainer.name}" pré-chargé.`;
    showToast('Import Docker', msg + ' Vérifiez et ajustez les informations.', 'info');
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

async function loadUsersPageData() {
    try {
        const [users, activeSessions, apps] = await Promise.all([
            api.getUsers(),
            api.getActiveSessions().catch(() => ({ sessions: [] })),
            api.getAllApps()
        ]);
        
        allUsersPage = users;
        allApps = apps;  // Charger aussi les apps pour le formulaire d'édition utilisateur
        
        // Stats
        document.getElementById('usersStatTotal').textContent = users.length;
        document.getElementById('usersStatActive').textContent = users.filter(u => u.is_active).length;
        document.getElementById('usersStatAdmins').textContent = users.filter(u => u.is_admin).length;
        document.getElementById('usersStatConnected').textContent = activeSessions.active_count || 0;
        
        renderUsersPageTable(users);
    } catch (error) {
        console.error('Erreur chargement utilisateurs:', error);
        showToast('Erreur', 'Impossible de charger les utilisateurs', 'danger');
    }
}

function renderUsersPageTable(users) {
    const container = document.getElementById('usersPageTable');
    
    if (!users || users.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-people display-4"></i>
                <p class="mt-2">Aucun utilisateur</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-hover align-middle mb-0">
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
                            ${user.app_roles?.map(ar => `
                                <span class="badge bg-secondary me-1">
                                    ${escapeHtml(ar.app_name)} 
                                    <small>(${ar.role})</small>
                                </span>
                            `).join('') || '<span class="text-muted">-</span>'}
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
                `).join('')}
            </tbody>
        </table>
    `;
}

function filterUsersTable() {
    const search = document.getElementById('usersSearchInput').value.toLowerCase();
    const filtered = allUsersPage.filter(user => 
        user.username.toLowerCase().includes(search) ||
        (user.email && user.email.toLowerCase().includes(search))
    );
    renderUsersPageTable(filtered);
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
    loadUserAppsCheckboxes([]);
    userFormModal.show();
}

async function editUserPage(id) {
    const user = allUsersPage.find(u => u.id === id);
    if (!user) {
        const users = await api.getUsers();
        const foundUser = users.find(u => u.id === id);
        if (!foundUser) return;
        Object.assign(user, foundUser);
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
        await loadUsersPageData();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== Vue Erreurs HTTP (Audit) =====

let errorsPage = 1;

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
// DISCOVERY M2M - Test et exploration des endpoints
// =============================================================================

// État global Discovery
let discoveryM2MToken = null;
let discoveryM2MTokenExpiry = null;
let discoveryServices = [];
let discoveryOpenApiSpecs = {}; // Cache des specs OpenAPI par service slug
let discoverySelectedEndpoint = null; // Endpoint actuellement sélectionné

/**
 * Charge la page Discovery - initialise les Service Accounts et l'état
 */
async function loadDiscoveryPage() {
    try {
        // Charger les Service Accounts pour le sélecteur
        const sas = await api.getServiceAccounts();
        const select = document.getElementById('discoverySaSelect');
        
        // Vider et repeupler
        select.innerHTML = '<option value="">-- Sélectionner un Service Account --</option>';
        sas.forEach(sa => {
            const opt = document.createElement('option');
            opt.value = sa.id;
            opt.textContent = `${sa.name} (${sa.is_active ? 'Actif' : 'Inactif'})`;
            opt.dataset.clientId = sa.client_id;
            opt.dataset.name = sa.name;
            select.appendChild(opt);
        });
        
        // Pré-sélectionner "Mon application" si disponible
        const monAppSa = sas.find(sa => sa.name.toLowerCase().includes('mon-app') || sa.name.toLowerCase().includes('mon application'));
        if (monAppSa) {
            select.value = monAppSa.id;
            onDiscoverySaChange();
        }
        
    } catch (error) {
        console.error('Erreur chargement Discovery:', error);
        showToast('Erreur', 'Impossible de charger les Service Accounts', 'danger');
    }
}

/**
 * Appelé quand on change le Service Account sélectionné
 */
function onDiscoverySaChange() {
    const select = document.getElementById('discoverySaSelect');
    const selected = select.options[select.selectedIndex];
    
    if (selected && selected.dataset.clientId) {
        document.getElementById('discoveryClientId').value = selected.dataset.clientId;
        document.getElementById('discoveryClientSecret').value = '';
        
        // Essayer de charger le secret depuis le fichier (via API)
        loadDiscoverySecretForSa(selected.dataset.name);
    } else {
        document.getElementById('discoveryClientId').value = '';
        document.getElementById('discoveryClientSecret').value = '';
    }
    
    // Réinitialiser l'état du token
    discoveryM2MToken = null;
    discoveryM2MTokenExpiry = null;
    document.getElementById('discoveryTokenInfo').classList.add('d-none');
    document.getElementById('discoveryTokenStatus').innerHTML = '';
}

/**
 * Tente de charger le secret depuis les fichiers secrets (si disponible)
 */
async function loadDiscoverySecretForSa(saName) {
    // Le secret n'est pas stocké en base - il faut le saisir manuellement
    // ou le lire depuis les fichiers secrets (non accessible depuis le frontend)
    // On peut afficher une aide pour l'utilisateur
    
    const slug = saName.toLowerCase()
        .replace(/[àâä]/g, 'a')
        .replace(/[éèêë]/g, 'e')
        .replace(/[îï]/g, 'i')
        .replace(/[ôö]/g, 'o')
        .replace(/[ùûü]/g, 'u')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    
    document.getElementById('discoveryClientSecret').placeholder = `Entrez le secret ou consultez secrets/service_accounts/sa_${slug}.env`;
}

/**
 * Toggle visibility du champ secret
 */
function toggleDiscoverySecretVisibility() {
    const input = document.getElementById('discoveryClientSecret');
    const icon = document.getElementById('discoverySecretEyeIcon');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('bi-eye');
        icon.classList.add('bi-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.remove('bi-eye-slash');
        icon.classList.add('bi-eye');
    }
}

/**
 * Obtient un token M2M avec les credentials fournis
 */
async function getM2MToken() {
    const clientId = document.getElementById('discoveryClientId').value.trim();
    const clientSecret = document.getElementById('discoveryClientSecret').value.trim();
    
    if (!clientId || !clientSecret) {
        showToast('Erreur', 'Client ID et Client Secret requis', 'warning');
        return;
    }
    
    const btn = document.getElementById('discoveryGetTokenBtn');
    const statusSpan = document.getElementById('discoveryTokenStatus');
    
    btn.disabled = true;
    statusSpan.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Authentification...';
    
    try {
        const result = await api.getM2MToken(clientId, clientSecret);
        
        discoveryM2MToken = result.access_token;
        // Calculer l'expiration (par défaut 1h si non spécifié)
        const expiresIn = result.expires_in || 3600;
        discoveryM2MTokenExpiry = new Date(Date.now() + expiresIn * 1000);
        
        // Afficher le succès
        statusSpan.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>Authentifié</span>';
        
        const tokenInfo = document.getElementById('discoveryTokenInfo');
        tokenInfo.classList.remove('d-none');
        document.getElementById('discoveryTokenExpiry').textContent = `Expire à ${discoveryM2MTokenExpiry.toLocaleTimeString()}`;
        document.getElementById('discoveryTokenPreview').textContent = discoveryM2MToken.substring(0, 50) + '...';
        
        // Charger automatiquement les services partagés
        await discoverSharedServices();
        
        showToast('Succès', 'Token M2M obtenu avec succès', 'success');
        
    } catch (error) {
        console.error('Erreur obtention token M2M:', error);
        statusSpan.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle me-1"></i>${error.message}</span>`;
        discoveryM2MToken = null;
        discoveryM2MTokenExpiry = null;
        document.getElementById('discoveryTokenInfo').classList.add('d-none');
    } finally {
        btn.disabled = false;
    }
}

/**
 * Découvre les services partagés via l'endpoint /api/apps/discover
 */
async function discoverSharedServices() {
    if (!discoveryM2MToken) {
        showToast('Erreur', 'Obtenez d\'abord un token M2M', 'warning');
        return;
    }
    
    const container = document.getElementById('discoveryServicesList');
    container.innerHTML = '<div class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Découverte...</div>';
    
    try {
        const result = await api.discoverServices(discoveryM2MToken);
        discoveryServices = result.shared_services || [];
        
        document.getElementById('discoveryServicesCount').textContent = result.total_shared || 0;
        
        // Mettre à jour le sélecteur de services pour le test d'endpoint
        updateDiscoveryServiceSelect();
        
        if (discoveryServices.length === 0) {
            container.innerHTML = `
                <div class="text-center py-4 text-muted">
                    <i class="bi bi-inbox display-4"></i>
                    <p class="mt-2">Aucun service partagé disponible</p>
                    <small>Les applications marquées "is_shared_service" apparaîtront ici</small>
                </div>
            `;
            return;
        }
        
        container.innerHTML = `
            <div class="list-group list-group-flush">
                ${discoveryServices.map(svc => `
                    <div class="list-group-item list-group-item-action" style="cursor: pointer;" onclick="selectDiscoveryService('${escapeHtml(svc.api_url)}')">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <strong>${escapeHtml(svc.name)}</strong>
                                <span class="badge bg-secondary ms-2">${escapeHtml(svc.slug)}</span>
                            </div>
                            <i class="bi bi-arrow-right text-muted"></i>
                        </div>
                        <small class="text-muted">${escapeHtml(svc.description || 'Aucune description')}</small>
                        <div class="mt-1">
                            <code class="small">${escapeHtml(svc.api_url)}</code>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        
    } catch (error) {
        console.error('Erreur découverte services:', error);
        container.innerHTML = `
            <div class="text-center py-4 text-danger">
                <i class="bi bi-exclamation-triangle display-4"></i>
                <p class="mt-2">${escapeHtml(error.message)}</p>
            </div>
        `;
    }
}

/**
 * Sélectionne un service et pré-remplit l'URL
 */
function selectDiscoveryService(apiUrl) {
    document.getElementById('discoveryUrl').value = apiUrl;
    showToast('Service sélectionné', 'URL pré-remplie, ajoutez l\'endpoint souhaité', 'info');
}

/**
 * Met à jour le sélecteur de services après découverte
 */
function updateDiscoveryServiceSelect() {
    const select = document.getElementById('discoveryServiceSelect');
    select.innerHTML = '<option value="">-- Sélectionner un service --</option>';
    
    discoveryServices.forEach(svc => {
        const opt = document.createElement('option');
        opt.value = svc.slug;
        opt.textContent = `${svc.name} (${svc.slug})`;
        opt.dataset.apiUrl = svc.api_url;
        opt.dataset.name = svc.name;
        select.appendChild(opt);
    });
    
    // Réinitialiser le sélecteur d'endpoints
    const endpointSelect = document.getElementById('discoveryEndpointSelect');
    endpointSelect.innerHTML = '<option value="">-- Sélectionnez d\'abord un service --</option>';
    endpointSelect.disabled = true;
    document.getElementById('discoveryEndpointInfo').classList.add('d-none');
    document.getElementById('discoveryParamsSection').classList.add('d-none');
}

/**
 * Appelé quand on change le service sélectionné
 */
async function onDiscoveryServiceChange() {
    const select = document.getElementById('discoveryServiceSelect');
    const selected = select.options[select.selectedIndex];
    const endpointSelect = document.getElementById('discoveryEndpointSelect');
    
    if (!selected || !selected.value) {
        endpointSelect.innerHTML = '<option value="">-- Sélectionnez d\'abord un service --</option>';
        endpointSelect.disabled = true;
        document.getElementById('discoveryEndpointInfo').classList.add('d-none');
        document.getElementById('discoveryParamsSection').classList.add('d-none');
        return;
    }
    
    const slug = selected.value;
    const apiUrl = selected.dataset.apiUrl;
    
    // Pré-remplir l'URL de base
    document.getElementById('discoveryUrl').value = apiUrl;
    
    // Charger la spec OpenAPI du service
    endpointSelect.innerHTML = '<option value="">⏳ Chargement des endpoints...</option>';
    endpointSelect.disabled = true;
    
    try {
        const spec = await loadServiceOpenApiSpec(slug, apiUrl);
        
        if (spec && spec.paths) {
            populateEndpointSelect(spec, apiUrl);
        } else {
            endpointSelect.innerHTML = '<option value="">❌ Spec OpenAPI non disponible</option>';
            showToast('Info', 'Ce service ne fournit pas de spécification OpenAPI', 'warning');
        }
    } catch (error) {
        console.error('Erreur chargement spec OpenAPI:', error);
        endpointSelect.innerHTML = '<option value="">❌ Erreur chargement</option>';
        showToast('Erreur', 'Impossible de charger les endpoints: ' + error.message, 'danger');
    }
}

/**
 * Charge la spécification OpenAPI d'un service
 */
async function loadServiceOpenApiSpec(slug, apiUrl) {
    // Vérifier le cache
    if (discoveryOpenApiSpecs[slug]) {
        return discoveryOpenApiSpecs[slug];
    }
    
    // Essayer différents chemins pour OpenAPI
    const openApiPaths = ['/openapi.json', '/api/openapi.json', '/docs/openapi.json', '/swagger.json'];
    
    for (const path of openApiPaths) {
        try {
            const url = apiUrl.replace(/\/$/, '') + path;
            const response = await fetch(url, {
                headers: {
                    'Authorization': `Bearer ${discoveryM2MToken}`,
                    'Accept': 'application/json'
                }
            });
            
            if (response.ok) {
                const spec = await response.json();
                discoveryOpenApiSpecs[slug] = spec;
                console.log(`✅ Spec OpenAPI chargée depuis ${url}`);
                return spec;
            }
        } catch (e) {
            // Continuer avec le chemin suivant
        }
    }
    
    return null;
}

/**
 * Remplit le sélecteur d'endpoints depuis la spec OpenAPI
 */
function populateEndpointSelect(spec, baseUrl) {
    const select = document.getElementById('discoveryEndpointSelect');
    select.innerHTML = '<option value="">-- Sélectionner un endpoint --</option>';
    
    // Grouper par tag
    const endpointsByTag = {};
    const methodColors = {
        'get': 'success',
        'post': 'primary',
        'put': 'warning',
        'patch': 'info',
        'delete': 'danger'
    };
    
    for (const [path, methods] of Object.entries(spec.paths || {})) {
        for (const [method, details] of Object.entries(methods)) {
            if (['get', 'post', 'put', 'patch', 'delete'].includes(method.toLowerCase())) {
                const tags = details.tags || ['Autre'];
                const tag = tags[0];
                
                if (!endpointsByTag[tag]) {
                    endpointsByTag[tag] = [];
                }
                
                endpointsByTag[tag].push({
                    method: method.toUpperCase(),
                    path: path,
                    summary: details.summary || '',
                    description: details.description || '',
                    operationId: details.operationId || '',
                    parameters: details.parameters || [],
                    requestBody: details.requestBody || null,
                    responses: details.responses || {},
                    tags: tags
                });
            }
        }
    }
    
    // Créer les optgroups
    for (const [tag, endpoints] of Object.entries(endpointsByTag)) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📁 ${tag}`;
        
        endpoints.forEach((ep, idx) => {
            const opt = document.createElement('option');
            const methodBadge = `[${ep.method}]`;
            opt.value = JSON.stringify(ep);
            opt.textContent = `${methodBadge} ${ep.path} - ${ep.summary || ep.operationId || 'Sans description'}`;
            opt.dataset.method = ep.method;
            opt.dataset.path = ep.path;
            optgroup.appendChild(opt);
        });
        
        select.appendChild(optgroup);
    }
    
    select.disabled = false;
    showToast('Endpoints chargés', `${Object.values(endpointsByTag).flat().length} endpoints disponibles`, 'success');
}

/**
 * Appelé quand on sélectionne un endpoint
 */
function onDiscoveryEndpointChange() {
    const select = document.getElementById('discoveryEndpointSelect');
    const selected = select.options[select.selectedIndex];
    
    if (!selected || !selected.value) {
        document.getElementById('discoveryEndpointInfo').classList.add('d-none');
        document.getElementById('discoveryParamsSection').classList.add('d-none');
        discoverySelectedEndpoint = null;
        return;
    }
    
    try {
        const endpoint = JSON.parse(selected.value);
        discoverySelectedEndpoint = endpoint;
        
        // Afficher les infos de l'endpoint
        const infoDiv = document.getElementById('discoveryEndpointInfo');
        infoDiv.classList.remove('d-none');
        
        // Méthode avec couleur
        const methodBadge = document.getElementById('discoveryEndpointMethod');
        const methodColors = {
            'GET': 'bg-success',
            'POST': 'bg-primary',
            'PUT': 'bg-warning text-dark',
            'PATCH': 'bg-info',
            'DELETE': 'bg-danger'
        };
        methodBadge.className = `badge ${methodColors[endpoint.method] || 'bg-secondary'}`;
        methodBadge.textContent = endpoint.method;
        
        document.getElementById('discoveryEndpointPath').textContent = endpoint.path;
        document.getElementById('discoveryEndpointDescription').textContent = 
            endpoint.description || endpoint.summary || 'Pas de description disponible';
        document.getElementById('discoveryEndpointTags').textContent = endpoint.tags.join(', ');
        
        // Mettre à jour la méthode et l'URL
        document.getElementById('discoveryMethod').value = endpoint.method;
        
        // Construire l'URL complète
        const serviceSelect = document.getElementById('discoveryServiceSelect');
        const baseUrl = serviceSelect.options[serviceSelect.selectedIndex]?.dataset.apiUrl || '';
        document.getElementById('discoveryUrl').value = baseUrl.replace(/\/$/, '') + endpoint.path;
        
        // Générer les paramètres s'il y en a
        generateEndpointParams(endpoint);
        
        // Générer le body exemple si POST/PUT/PATCH
        if (['POST', 'PUT', 'PATCH'].includes(endpoint.method) && endpoint.requestBody) {
            generateRequestBodyExample(endpoint.requestBody);
        } else {
            document.getElementById('discoveryBody').value = '';
        }
        
    } catch (e) {
        console.error('Erreur parsing endpoint:', e);
    }
}

/**
 * Génère les champs de paramètres pour l'endpoint
 */
function generateEndpointParams(endpoint) {
    const paramsSection = document.getElementById('discoveryParamsSection');
    const paramsContainer = document.getElementById('discoveryParams');
    
    const params = endpoint.parameters || [];
    
    if (params.length === 0) {
        paramsSection.classList.add('d-none');
        return;
    }
    
    paramsSection.classList.remove('d-none');
    paramsContainer.innerHTML = '';
    
    params.forEach(param => {
        const row = document.createElement('div');
        row.className = 'row g-2 mb-2 align-items-center';
        
        const isRequired = param.required || false;
        const paramType = param.in; // path, query, header
        const typeIcon = {
            'path': 'bi-link-45deg',
            'query': 'bi-question-circle',
            'header': 'bi-card-heading'
        }[paramType] || 'bi-tag';
        
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
                       data-param-in="${paramType}"
                       placeholder="${escapeHtml(param.description || param.schema?.type || 'valeur')}"
                       ${isRequired ? 'required' : ''}>
            </div>
            <div class="col-md-4">
                <small class="text-muted">
                    ${isRequired ? '<span class="badge bg-danger me-1">Requis</span>' : ''}
                    <span class="badge bg-secondary">${paramType}</span>
                    ${param.schema?.type ? `<span class="badge bg-light text-dark">${param.schema.type}</span>` : ''}
                </small>
            </div>
        `;
        
        paramsContainer.appendChild(row);
    });
    
    // Ajouter listener pour mettre à jour l'URL en temps réel
    paramsContainer.querySelectorAll('.discovery-param-value').forEach(input => {
        input.addEventListener('input', updateDiscoveryUrlWithParams);
    });
}

/**
 * Met à jour l'URL avec les paramètres remplis
 */
function updateDiscoveryUrlWithParams() {
    if (!discoverySelectedEndpoint) return;
    
    const serviceSelect = document.getElementById('discoveryServiceSelect');
    const baseUrl = serviceSelect.options[serviceSelect.selectedIndex]?.dataset.apiUrl || '';
    let path = discoverySelectedEndpoint.path;
    
    const queryParams = [];
    
    document.querySelectorAll('.discovery-param-value').forEach(input => {
        const paramName = input.dataset.paramName;
        const paramIn = input.dataset.paramIn;
        const value = input.value.trim();
        
        if (value) {
            if (paramIn === 'path') {
                // Remplacer {param} dans le path
                path = path.replace(`{${paramName}}`, encodeURIComponent(value));
            } else if (paramIn === 'query') {
                queryParams.push(`${encodeURIComponent(paramName)}=${encodeURIComponent(value)}`);
            }
        }
    });
    
    let fullUrl = baseUrl.replace(/\/$/, '') + path;
    if (queryParams.length > 0) {
        fullUrl += '?' + queryParams.join('&');
    }
    
    document.getElementById('discoveryUrl').value = fullUrl;
}

/**
 * Génère un exemple de body pour les requêtes POST/PUT/PATCH
 */
function generateRequestBodyExample(requestBody) {
    const bodyTextarea = document.getElementById('discoveryBody');
    
    try {
        const content = requestBody.content;
        const jsonContent = content?.['application/json'];
        
        if (jsonContent?.schema) {
            const example = generateSchemaExample(jsonContent.schema);
            bodyTextarea.value = JSON.stringify(example, null, 2);
        } else if (jsonContent?.example) {
            bodyTextarea.value = JSON.stringify(jsonContent.example, null, 2);
        }
    } catch (e) {
        console.warn('Impossible de générer exemple body:', e);
    }
}

/**
 * Génère un exemple à partir d'un schéma JSON
 */
function generateSchemaExample(schema, depth = 0) {
    if (depth > 5) return null; // Éviter la récursion infinie
    
    if (schema.example !== undefined) {
        return schema.example;
    }
    
    if (schema.$ref) {
        // Référence à un composant - retourner un placeholder
        return { "_ref": schema.$ref.split('/').pop() };
    }
    
    switch (schema.type) {
        case 'object':
            const obj = {};
            if (schema.properties) {
                for (const [key, prop] of Object.entries(schema.properties)) {
                    obj[key] = generateSchemaExample(prop, depth + 1);
                }
            }
            return obj;
        case 'array':
            if (schema.items) {
                return [generateSchemaExample(schema.items, depth + 1)];
            }
            return [];
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
            return schema.default || true;
        default:
            return null;
    }
}

/**
 * Affiche la spec OpenAPI complète de l'endpoint sélectionné
 */
function loadEndpointOpenApiSpec() {
    if (!discoverySelectedEndpoint) {
        showToast('Info', 'Sélectionnez d\'abord un endpoint', 'info');
        return;
    }
    
    // Afficher dans le résultat
    document.getElementById('discoveryResultCard').style.display = 'block';
    document.getElementById('discoveryResultStatus').textContent = 'OpenAPI Spec';
    document.getElementById('discoveryResultStatus').className = 'badge bg-info';
    document.getElementById('discoveryResultTime').textContent = '';
    document.getElementById('discoveryResultBody').textContent = JSON.stringify(discoverySelectedEndpoint, null, 2);
}

/**
 * Copie le token M2M dans le presse-papier
 */
function copyDiscoveryToken() {
    if (discoveryM2MToken) {
        navigator.clipboard.writeText(discoveryM2MToken);
        showToast('Copié', 'Token M2M copié dans le presse-papier', 'success');
    }
}

/**
 * Ajoute une ligne de header personnalisé
 */
function addDiscoveryHeader() {
    const container = document.getElementById('discoveryHeaders');
    const row = document.createElement('div');
    row.className = 'row g-2 mb-2 discovery-header-row';
    row.innerHTML = `
        <div class="col-5">
            <input type="text" class="form-control form-control-sm" placeholder="Nom">
        </div>
        <div class="col-6">
            <input type="text" class="form-control form-control-sm" placeholder="Valeur">
        </div>
        <div class="col-1">
            <button class="btn btn-outline-danger btn-sm" onclick="removeDiscoveryHeader(this)">
                <i class="bi bi-x"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
}

/**
 * Supprime une ligne de header
 */
function removeDiscoveryHeader(btn) {
    btn.closest('.discovery-header-row').remove();
}

/**
 * Teste un endpoint avec le token M2M
 */
async function testDiscoveryEndpoint() {
    if (!discoveryM2MToken) {
        showToast('Erreur', 'Obtenez d\'abord un token M2M', 'warning');
        return;
    }
    
    const method = document.getElementById('discoveryMethod').value;
    const url = document.getElementById('discoveryUrl').value.trim();
    
    if (!url) {
        showToast('Erreur', 'URL de l\'endpoint requise', 'warning');
        return;
    }
    
    // Récupérer les headers personnalisés
    const headers = {};
    document.querySelectorAll('.discovery-header-row').forEach(row => {
        const inputs = row.querySelectorAll('input');
        const name = inputs[0]?.value.trim();
        const value = inputs[1]?.value.trim();
        if (name) {
            headers[name] = value;
        }
    });
    
    // Ajouter le token Authorization
    headers['Authorization'] = `Bearer ${discoveryM2MToken}`;
    
    // Body pour POST/PUT/PATCH
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
    
    const btn = document.getElementById('discoveryTestBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Exécution...';
    
    const startTime = Date.now();
    
    try {
        const response = await fetch(url, {
            method: method,
            headers: headers,
            body: body ? JSON.stringify(body) : null
        });
        
        const duration = Date.now() - startTime;
        
        // Afficher le résultat
        document.getElementById('discoveryResultCard').style.display = 'block';
        
        const statusBadge = document.getElementById('discoveryResultStatus');
        statusBadge.textContent = `${response.status} ${response.statusText}`;
        statusBadge.className = `badge ${response.ok ? 'bg-success' : 'bg-danger'}`;
        
        document.getElementById('discoveryResultTime').textContent = `${duration}ms`;
        
        // Parser la réponse
        let responseText;
        const contentType = response.headers.get('content-type') || '';
        
        if (contentType.includes('application/json')) {
            const json = await response.json();
            responseText = JSON.stringify(json, null, 2);
        } else {
            responseText = await response.text();
        }
        
        document.getElementById('discoveryResultBody').textContent = responseText;
        
        if (response.ok) {
            showToast('Succès', `${method} ${response.status} - ${duration}ms`, 'success');
        } else {
            showToast('Erreur', `${method} ${response.status} ${response.statusText}`, 'warning');
        }
        
    } catch (error) {
        console.error('Erreur test endpoint:', error);
        
        document.getElementById('discoveryResultCard').style.display = 'block';
        
        const statusBadge = document.getElementById('discoveryResultStatus');
        statusBadge.textContent = 'Erreur';
        statusBadge.className = 'badge bg-danger';
        
        document.getElementById('discoveryResultTime').textContent = `${Date.now() - startTime}ms`;
        document.getElementById('discoveryResultBody').textContent = `Erreur: ${error.message}\n\nCela peut être dû à:\n- CORS bloqué par le serveur distant\n- URL incorrecte\n- Serveur non accessible`;
        
        showToast('Erreur', error.message, 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Exécuter';
    }
}

/**
 * Copie le résultat dans le presse-papier
 */
function copyDiscoveryResult() {
    const result = document.getElementById('discoveryResultBody').textContent;
    if (result) {
        navigator.clipboard.writeText(result);
        showToast('Copié', 'Résultat copié dans le presse-papier', 'success');
    }
}

/**
 * Efface le résultat
 */
function clearDiscoveryResult() {
    document.getElementById('discoveryResultCard').style.display = 'none';
    document.getElementById('discoveryResultBody').textContent = '';
}

// ===== Backup/Restore Functions =====

let backupFileData = null;

/**
 * Charge les informations de backup (appelé à l'ouverture de l'onglet)
 */
function loadBackupInfo() {
    // Reset du formulaire d'import
    document.getElementById('importBackupFile').value = '';
    document.getElementById('backupPreview').classList.add('d-none');
    document.getElementById('importBackupBtn').disabled = true;
    document.getElementById('importResultCard').classList.add('d-none');
    backupFileData = null;
}

/**
 * Télécharge l'export du backup
 */
async function downloadBackupExport() {
    try {
        const includeJwt = document.getElementById('exportIncludeJwt').checked;
        const includeSaSecrets = document.getElementById('exportIncludeSaSecrets').checked;
        
        showToast('Export', 'Génération du backup en cours...', 'info');
        
        const data = await api.exportBackup(includeJwt, includeSaSecrets);
        
        // Créer le fichier JSON
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        
        // Télécharger
        const a = document.createElement('a');
        a.href = url;
        a.download = `hub_backup_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showToast('Succès', `Backup exporté: ${data.users.length} utilisateurs, ${data.applications.length} applications`, 'success');
        
    } catch (error) {
        console.error('Erreur export backup:', error);
        showToast('Erreur', error.message, 'danger');
    }
}

/**
 * Prévisualise le fichier de backup sélectionné
 */
function previewBackupFile() {
    const fileInput = document.getElementById('importBackupFile');
    const file = fileInput.files[0];
    
    if (!file) {
        document.getElementById('backupPreview').classList.add('d-none');
        document.getElementById('importBackupBtn').disabled = true;
        backupFileData = null;
        return;
    }
    
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            backupFileData = JSON.parse(e.target.result);
            
            // Afficher la preview
            document.getElementById('previewUsers').textContent = (backupFileData.users || []).length;
            document.getElementById('previewApps').textContent = (backupFileData.applications || []).length;
            document.getElementById('previewSA').textContent = (backupFileData.service_accounts || []).length;
            document.getElementById('previewJwt').textContent = backupFileData.jwt_private_key_b64 ? 'Oui' : 'Non';
            
            // Date et exporteur
            if (backupFileData.exported_at) {
                const date = new Date(backupFileData.exported_at);
                document.getElementById('previewDate').textContent = date.toLocaleString('fr-FR');
            } else {
                document.getElementById('previewDate').textContent = '-';
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
    
    // Confirmation
    const confirmMsg = `Vous allez importer:
- ${(backupFileData.users || []).length} utilisateurs
- ${(backupFileData.applications || []).length} applications
- ${(backupFileData.service_accounts || []).length} service accounts

Mode: ${importMode.toUpperCase()}
${restoreJwt ? '⚠️ Les clés JWT seront restaurées (redémarrage nécessaire)' : ''}

Continuer ?`;
    
    if (!confirm(confirmMsg)) {
        return;
    }
    
    try {
        const btn = document.getElementById('importBackupBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Import en cours...';
        
        const result = await api.importBackup(backupFileData, importMode, restoreJwt);
        
        // Afficher le résultat
        displayImportResult(result);
        
        // Rafraîchir les données si succès
        if (result.success) {
            showToast('Import réussi', 'Les données ont été importées avec succès', 'success');
            loadAllApps();
            loadServiceAccounts();
            loadUsers();
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
        btn.innerHTML = '<i class="bi bi-upload me-2"></i>Importer le backup';
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
        <div class="col-md-4">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Utilisateurs</div>
                <span class="badge bg-success">${result.users_created || 0} créés</span>
                <span class="badge bg-info">${result.users_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.users_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-4">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Applications</div>
                <span class="badge bg-success">${result.apps_created || 0} créées</span>
                <span class="badge bg-info">${result.apps_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.apps_skipped || 0} skip</span>
            </div>
        </div>
        <div class="col-md-4">
            <div class="text-center p-2 border rounded">
                <div class="small text-muted">Service Accounts</div>
                <span class="badge bg-success">${result.service_accounts_created || 0} créés</span>
                <span class="badge bg-info">${result.service_accounts_updated || 0} maj</span>
                <span class="badge bg-secondary">${result.service_accounts_skipped || 0} skip</span>
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
                            <button class="btn btn-outline-success" onclick="showTriggerBackupConfirm('${app.app_slug}', '${escapeHtml(app.app_name)}')" title="Déclencher sauvegarde" ${app.backup_mode === 'disabled' ? 'style="display: none;"' : ''}>
                                <i class="bi bi-play-circle"></i>
                            </button>
                            <button class="btn btn-outline-primary" onclick="showAppBackupsList('${app.app_slug}')" title="Voir les sauvegardes">
                                <i class="bi bi-hdd-stack"></i>
                            </button>
                            <button class="btn btn-outline-secondary" onclick="showAppBackupConfig('${app.app_slug}', '${escapeHtml(app.app_name)}')" title="Configurer">
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
    if (!file.name.endsWith('.zip')) {
        showToast('Erreur', 'Le fichier doit être un ZIP', 'warning');
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
    const userSelectionSection = document.getElementById('bulkUserSelectionSection');
    
    // Masquer toutes les sections
    [assignSection, removeSection, deleteSection, createSection].forEach(s => {
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
function initBulkCreateForm() {
    const container = document.getElementById('bulkCreateUsersList');
    if (!container) return;
    
    container.innerHTML = '';
    addBulkCreateUserRow();
    loadBulkAppSelection('bulkCreateAppSelect');
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
            default:
                throw new Error('Mode d\'opération non reconnu');
        }
        
        // Afficher le résultat
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
    const appId = parseInt(document.getElementById('bulkCreateAppSelect')?.value) || null;
    const role = document.getElementById('bulkCreateRoleSelect')?.value || 'user';
    
    rows.forEach((row, index) => {
        const username = row.querySelector(`[name="username_${row.dataset.index}"]`)?.value?.trim();
        const email = row.querySelector(`[name="email_${row.dataset.index}"]`)?.value?.trim() || null;
        const password = row.querySelector(`[name="password_${row.dataset.index}"]`)?.value;
        const isAdmin = row.querySelector(`[name="isAdmin_${row.dataset.index}"]`)?.checked || false;
        const mustChangePwd = row.querySelector(`[name="mustChangePwd_${row.dataset.index}"]`)?.checked || false;
        
        if (username && password) {
            const userData = {
                username,
                email,
                password,
                is_admin: isAdmin,
                is_active: true,
                must_change_password: mustChangePwd,
                can_change_password: true,
                app_roles: []
            };
            
            // Ajouter le rôle sur l'application si sélectionnée
            if (appId) {
                userData.app_roles.push({ app_id: appId, role: role });
            }
            
            users.push(userData);
        }
    });
    
    if (users.length === 0) {
        throw new Error('Veuillez remplir au moins un utilisateur (nom et mot de passe obligatoires)');
    }
    
    // Demander confirmation
    const confirmed = confirm(
        `Vous allez créer ${users.length} utilisateur(s):\n\n` +
        `${users.slice(0, 10).map(u => u.username).join('\n')}` +
        (users.length > 10 ? `\n... et ${users.length - 10} autre(s)` : '') +
        `\n\nConfirmer la création ?`
    );
    
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
