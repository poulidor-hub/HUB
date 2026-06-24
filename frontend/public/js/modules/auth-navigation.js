/**
 * Hub Application - authentification, session et navigation
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

function getPendingCallback() {
    return sessionStorage.getItem('sso_pending_callback');
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
        if (currentUser?.beacon_token) {
            beaconToken = currentUser.beacon_token;
        }
        
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
    document.getElementById('accountRequestWaiting').classList.add('d-none');
    document.getElementById('dashboardSection').classList.remove('d-none');
    document.getElementById('userInfo').classList.remove('d-none');
    document.getElementById('logoutBtn').classList.remove('d-none');
    document.getElementById('changePasswordBtn').classList.remove('d-none');
    document.getElementById('username').textContent = currentUser.username;

    if (currentUser.is_admin) {
        document.getElementById('adminBtn').classList.remove('d-none');
        document.getElementById('adminNavigation').classList.remove('d-none');
    }

    // Afficher la cloche de messages
    document.getElementById('messageBellContainer').classList.remove('d-none');

    // Initialiser les mises à jour temps réel via SSE
    initEventSource();

    // Charger tous les badges de notification dès la connexion (cloche, messages,
    // audit, apps en attente, demandes de compte) pour qu'ils soient visibles
    // sans avoir à ouvrir le menu correspondant.
    refreshAllBadges();

    loadApps();
}

// ===== Page Navigation =====

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
    document.getElementById('messagesSection').classList.add('d-none');
    document.getElementById('userInboxSection')?.classList.add('d-none');
    _clearInboxMarkReadTimer();
    _resetInboxDetailView();
    
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
            loadPendingRequestsCount();
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
        case 'messages':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('messagesSection').classList.remove('d-none');
            document.getElementById('navMessages')?.classList.add('active');
            loadMessagesPage();
            break;
        case 'inbox':
            document.getElementById('userInboxSection').classList.remove('d-none');
            loadInboxPage();
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
    document.getElementById('userInfo').classList.add('d-none');
    document.getElementById('logoutBtn').classList.add('d-none');
    document.getElementById('changePasswordBtn').classList.add('d-none');
    document.getElementById('adminBtn').classList.add('d-none');
    document.getElementById('adminNavigation').classList.add('d-none');
    document.getElementById('messagesSection').classList.add('d-none');
    document.getElementById('userInboxSection')?.classList.add('d-none');
    document.getElementById('messageBellContainer')?.classList.add('d-none');
    document.getElementById('loginForm').reset();
    _clearInboxMarkReadTimer();
    _resetInboxDetailView();

    // Fail-closed : masquer le bouton avant le fetch, ne l'afficher que si le setting le confirme
    const accountBtn = document.getElementById('accountRequestButton');
    if (accountBtn) accountBtn.classList.add('d-none');

    api.getSetting('account_requests_enabled').then(data => {
        if (accountBtn) accountBtn.classList.toggle('d-none', data.value !== 'true');
    }).catch(() => {
        // En cas d'erreur, le bouton reste masqué (fail-closed)
    });
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
