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

/**
 * Initialise la connexion SSE pour recevoir les mises à jour en temps réel
 */
function initEventSource() {
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
        eventSource = new EventSource('/api/events/stream');
        
        eventSource.onopen = () => {
            console.log('🔌 SSE connecté - mises à jour temps réel actives');
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
                    initEventSource();
                }
            }, 5000);
        };
    } catch (e) {
        console.warn('SSE: impossible de créer la connexion', e);
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
    if (data.type === 'ping' || data.type === 'connected') {
        // Keep-alive ou connexion initiale, ignorer
        return;
    }
    
    console.log('📡 SSE event:', data.type, data.data);
    
    switch (data.type) {
        case 'apps_updated':
            // Rafraîchir la liste des applications
            loadApps();
            // Notification discrète
            const appAction = data.data?.action === 'created' ? 'ajoutée' : 
                             data.data?.action === 'deleted' ? 'supprimée' : 'modifiée';
            const appName = data.data?.app_name || 'Application';
            showToast('Mise à jour', `${appName} ${appAction}`, 'info');
            
            // Rafraîchir le token si une app a été créée ou modifiée (par un autre admin)
            if (data.data?.action === 'created' || data.data?.action === 'updated') {
                refreshTokenAfterAppChange(null, true); // silencieux via SSE
            }
            break;
            
        case 'users_updated':
            // Rafraîchir la liste des utilisateurs si on est sur cette page
            if (currentPage === 'users') {
                loadUsers();
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
    if (api.token) {
        loadCurrentUser();
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
        await api.login(username, password);
        
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
        api.clearToken();
        // Session expirée - afficher le formulaire de login
        // Le callback SSO reste en sessionStorage pour après la reconnexion
        console.log('Session expirée, affichage du formulaire de login');
        // Rien à faire de plus, le formulaire de login est déjà visible par défaut
    }
}

function showDashboard() {
    document.getElementById('loginSection').classList.add('d-none');
    document.getElementById('dashboardSection').classList.remove('d-none');
    document.getElementById('userInfo').classList.remove('d-none');
    document.getElementById('logoutBtn').classList.remove('d-none');
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
        currentUser = null;
        currentPage = 'dashboard';
        document.getElementById('loginSection').classList.remove('d-none');
        document.getElementById('dashboardSection').classList.add('d-none');
        document.getElementById('auditSection').classList.add('d-none');
        document.getElementById('usersSection').classList.add('d-none');
        document.getElementById('userInfo').classList.add('d-none');
        document.getElementById('logoutBtn').classList.add('d-none');
        document.getElementById('adminBtn').classList.add('d-none');
        document.getElementById('adminNavigation').classList.add('d-none');
        document.getElementById('loginForm').reset();
    }
}

// ===== Applications Dashboard =====

async function loadApps() {
    try {
        const apps = await api.getApps();
        renderAppsGrid(apps);
    } catch (error) {
        console.error('Failed to load apps:', error);
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
    }
}

function renderAppsGrid(apps) {
    const grid = document.getElementById('appsGrid');
    
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

    grid.innerHTML = apps.map(app => `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card shadow-sm h-100 ${!app.is_active ? 'app-inactive' : ''}" 
                 onclick="${app.is_active ? `openApp('${app.slug}', '${app.frontend_url || ''}')` : `showToast('Application hors ligne', 'Le conteneur Docker de cette application est arrêté.', 'warning')`}">
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
    `).join('');
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
        adminModal.show();
    } catch (error) {
        console.error('Erreur chargement admin:', error);
        // Si session expirée, forcer la déconnexion
        if (error.message === 'Session expirée') {
            showToast('Session expirée', 'Veuillez vous reconnecter', 'warning');
            await logout();
            return;
        }
        showToast('Erreur', 'Impossible de charger les données admin', 'danger');
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

function renderAppsTable(apps) {
    const container = document.getElementById('appsTable');
    
    if (apps.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucune application configurée</p>';
        return;
    }

    container.innerHTML = `
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Application</th>
                    <th>Slug</th>
                    <th>URL Frontend</th>
                    <th>Utilisateurs</th>
                    <th>Statut</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${apps.map(app => `
                    <tr>
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
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-primary me-1" onclick="editApp(${app.id})">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="deleteApp(${app.id}, '${escapeHtml(app.name)}')">
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
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
                requires_auth: document.getElementById('appRequiresAuth').checked
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
    const data = {
        username: document.getElementById('userUsername').value,
        email: document.getElementById('userEmail').value || null,
        is_active: document.getElementById('userActive').checked,
        is_admin: document.getElementById('userAdmin').checked
    };

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
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Nom</th>
                    <th>Client ID</th>
                    <th>Applications</th>
                    <th>Dernière utilisation</th>
                    <th>Statut</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${serviceAccounts.map(sa => `
                    <tr>
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
                                ? `<small class="text-muted">${new Date(sa.last_used_at).toLocaleString('fr-FR')}</small>`
                                : '<small class="text-muted">Jamais</small>'}
                        </td>
                        <td>
                            ${sa.is_active 
                                ? '<span class="badge bg-light text-dark"><i class="bi bi-check-circle text-success me-1"></i>Actif</span>' 
                                : '<span class="badge bg-light text-dark"><i class="bi bi-x-circle text-danger me-1"></i>Inactif</span>'}
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

async function loadAuditSessions() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const history = await api.getSessionHistory(days);
        renderSessionHistory(history);
        document.getElementById('sessionsCount').textContent = `${history.total_sessions || 0} sessions`;
    } catch (error) {
        console.error('Erreur chargement sessions:', error);
        showToast('Erreur', 'Impossible de charger les sessions', 'danger');
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
                    <th>Connecté depuis</th>
                    <th>Durée</th>
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
                        <td><small>${formatDateTime(s.login_at)}</small></td>
                        <td><span class="badge bg-light text-dark">${s.duration_minutes} min</span></td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderSessionHistory(data) {
    const container = document.getElementById('sessionHistoryTable');
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-clock-history display-6"></i>
                <p class="mt-2">Aucune session dans cette période</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Utilisateur</th>
                    <th>Connexion</th>
                    <th>Déconnexion</th>
                    <th>Durée</th>
                    <th>Raison</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td><strong>${escapeHtml(s.username || 'Unknown')}</strong></td>
                        <td><small>${formatDateTime(s.login_at)}</small></td>
                        <td><small>${s.logout_at ? formatDateTime(s.logout_at) : '<span class="badge bg-secondary">En cours</span>'}</small></td>
                        <td><span class="badge bg-light text-dark">${s.duration_display || '-'}</span></td>
                        <td>${getLogoutReasonBadge(s.logout_reason)}</td>
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
        'explicit': '<span class="badge bg-light text-dark">Déconnexion</span>',
        'timeout': '<span class="badge bg-light text-dark">Timeout</span>',
        'token_revoked': '<span class="badge bg-light text-dark">Révoqué</span>',
        'forced': '<span class="badge bg-light text-dark">Forcé</span>'
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
    
    try {
        // Désactiver le bouton pendant l'opération
        const btn = document.getElementById('dockerQuickImportBtn');
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Import...';
        
        // Appeler l'API auto-register
        const result = await api.autoRegisterApp(appSlug, appName, apiPort, frontendPort, isShared);
        
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
            showToast('Info', `Application "${appName}" existe déjà`, 'info');
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
            <div class="text-center py-4 text-muted">
                <i class="bi bi-clock-history display-6"></i>
                <p class="mt-2">Aucune session dans cette période</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Utilisateur</th>
                    <th>Connexion</th>
                    <th>Déconnexion</th>
                    <th>Durée</th>
                    <th>Raison</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td><strong>${escapeHtml(s.username || 'Unknown')}</strong></td>
                        <td><small>${formatDateTime(s.login_at)}</small></td>
                        <td><small>${s.logout_at ? formatDateTime(s.logout_at) : '<span class="badge bg-secondary">En cours</span>'}</small></td>
                        <td><span class="badge bg-light text-dark">${s.duration_display || '-'}</span></td>
                        <td>${getLogoutReasonBadge(s.logout_reason)}</td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
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
                        <td><strong>${escapeHtml(user.username)}</strong></td>
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
                            <button class="btn btn-sm btn-outline-secondary me-1" onclick="editUserPage(${user.id})">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" onclick="deleteUserPage(${user.id}, '${escapeHtml(user.username)}')" 
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
    
    const userAppRoles = user.app_roles || [];
    loadUserAppsWithRoles(userAppRoles);
    
    userFormModal.show();
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
