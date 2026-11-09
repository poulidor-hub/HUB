// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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

/**
 * Reprise de flow OIDC (/oidc/authorize) : la re-navigation vers cette URL
 * s'appuie sur le cookie hub_oidc_session (posé au login / refresh token),
 * PAS sur le JWT HUB. On ne doit donc JAMAIS y ajouter access_token (fuite
 * du JWT dans l'historique navigateur, paramètre ignoré par l'endpoint).
 */
function isOidcResumeCallback(url) {
    try {
        return new URL(url).pathname.endsWith('/oidc/authorize');
    } catch (e) {
        return false;
    }
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
            if (isOidcResumeCallback(pendingCallback)) {
                // Reprise OIDC : le login vient de (re)poser le cookie
                // hub_oidc_session → relancer l'authorize tel quel.
                console.log('Login: reprise du flow OIDC vers:', pendingCallback);
                showToast('Connexion réussie', `Reprise de la connexion SSO...`, 'success');
                window.location.href = pendingCallback;
                return;
            }
            const separator = pendingCallback.includes('?') ? '&' : '?';
            const redirectUrl = `${pendingCallback}${separator}access_token=${api.token}`;
            console.log('Redirection SSO vers:', redirectUrl);
            showToast('Connexion réussie', `Redirection vers l'application...`, 'success');
            // Redirection immédiate
            window.location.href = redirectUrl;
            return;
        }

        // SLO poste partagé : purger les sessions distantes si le backend l'a demandé
        // (cas : un user B se connecte par-dessus un user A qui avait laissé des
        // sessions OIDC ouvertes sur des apps externes). Best-effort : ne pas bloquer
        // l'affichage du dashboard si la purge échoue. On attend la fin de la purge
        // avant d'afficher le dashboard pour éviter que l'utilisateur n'ouvre une app
        // dont la session distante n'est pas encore purgée.
        if (Array.isArray(loginResult.remote_logout) && loginResult.remote_logout.length > 0) {
            try {
                await purgeRemoteSessions(loginResult.remote_logout);
            } catch (e) {
                console.warn('[SLO] purgeRemoteSessions failed:', e);
            }
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
        if (pendingCallback && isOidcResumeCallback(pendingCallback)) {
            // Reprise OIDC : le cookie hub_oidc_session a un TTL de 8h alors que
            // la session dashboard dure jusqu'à 7j (refresh token). Re-poser le
            // cookie via un refresh token AVANT de relancer l'authorize, sinon
            // l'authorize renvoie « Session HUB requise » malgré une session
            // dashboard valide.
            const refreshed = await api.tryRefreshToken().catch(() => false);
            if (!refreshed) {
                // Session dashboard réellement expirée : le callback reste en
                // sessionStorage, handleLogin le consommera après reconnexion.
                handleSessionExpired('Votre session a expiré. Veuillez vous reconnecter pour poursuivre vers l\'application.');
                return;
            }
            clearPendingCallback();
            console.log('Session valide (cookie OIDC re-posé), reprise du flow OIDC vers:', pendingCallback);
            window.location.href = pendingCallback;
            return;
        }
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
    
    var userNav = document.getElementById('userNavigation');
    if (userNav) userNav.classList.remove('d-none');
    
    document.getElementById('loginSection').classList.add('d-none');
    document.getElementById('accountRequestWaiting').classList.add('d-none');
    document.getElementById('dashboardSection').classList.remove('d-none');
    document.getElementById('userInfo').classList.remove('d-none');
    document.getElementById('logoutBtn').classList.remove('d-none');
    document.getElementById('changePasswordBtn').classList.remove('d-none');
    document.getElementById('downloadCertBtn')?.classList.remove('d-none');
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

    // Initialiser le flux des préférences (visibilité navbar + popup d'onboarding)
    if (typeof initPreferencesFlow === 'function') initPreferencesFlow();
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
    document.getElementById('appsHealthSection').classList.add('d-none');
    document.getElementById('adminSection').classList.add('d-none');
    document.getElementById('notificationsSection').classList.add('d-none');
    document.getElementById('adminUserTagsSection')?.classList.add('d-none');
    window._clearNotifMarkReadTimer?.();
    window._resetNotifDetailView?.();
    
    // Arrêter les timers admin si on quitte la page admin
    stopConnectivityRefresh();
    
    // Réinitialiser les styles de navigation
    document.querySelectorAll('#adminNavigation .nav-link').forEach(link => {
        link.classList.remove('active');
    });
    document.querySelectorAll('#userNavigation .nav-link').forEach(function(link) {
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
        case 'appsHealth':
            if (!currentUser?.is_admin) {
                showToast('Accès refusé', 'Vous devez être administrateur', 'danger');
                return;
            }
            document.getElementById('appsHealthSection').classList.remove('d-none');
            document.getElementById('navAppsHealth')?.classList.add('active');
            loadAppsHealth();
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
            // Page publique : Ma boîte pour tous, Gestion si admin (le mode se
            // garde lui-même — plus de garde ici).
            document.getElementById('notificationsSection').classList.remove('d-none');
            document.getElementById('navMessages')?.classList.add('active');
            if (typeof showNotificationsPage === 'function') {
                showNotificationsPage(currentUser?.is_admin ? 'manage' : 'inbox');
            }
            break;
        case 'admin-user-tags':
            document.getElementById('adminUserTagsSection').classList.remove('d-none');
            document.getElementById('navUserTags')?.classList.add('active');
            applyUserTagsTabsVisibility();
            loadAdminMyTags();
            if (currentUser?.is_admin) {
                loadUserTagsAdmin();
                loadAdminTagUsers();
            }
            break;
        case 'inbox':
        case 'notifications':
            document.getElementById('notificationsSection').classList.remove('d-none');
            document.getElementById('navMessages')?.classList.add('active');
            if (typeof showNotificationsPage === 'function') {
                showNotificationsPage('inbox');
            }
            break;
    }
}

/**
 * Onglets de la page Préférences selon le rôle.
 * Non-admin : barre d'onglets masquée, contenu "Mes préférences" direct.
 * Admin : 3 onglets, premier (Catégories) actif.
 */
function applyUserTagsTabsVisibility() {
    const section = document.getElementById('adminUserTagsSection');
    if (!section) return;
    const isAdmin = !!currentUser?.is_admin;

    const tabsNav = section.querySelector('.nav-tabs');
    if (tabsNav) tabsNav.classList.toggle('d-none', !isAdmin);

    section.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('show', 'active'));
    section.querySelectorAll('.nav-tabs .nav-link').forEach(l => l.classList.remove('active'));

    if (isAdmin) {
        section.querySelector('.nav-tabs .nav-link[data-bs-target="#tabTagCategories"]')?.classList.add('active');
        document.getElementById('tabTagCategories')?.classList.add('show', 'active');
    } else {
        document.getElementById('tabTagMine')?.classList.add('show', 'active');
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
    
    var userNav = document.getElementById('userNavigation');
    if (userNav) userNav.classList.add('d-none');
    
    currentUser = null;
    currentPage = 'dashboard';
    document.getElementById('loginSection').classList.remove('d-none');
    document.getElementById('dashboardSection').classList.add('d-none');
    document.getElementById('auditSection').classList.add('d-none');
    document.getElementById('usersSection').classList.add('d-none');
    document.getElementById('appsHealthSection').classList.add('d-none');
    document.getElementById('adminSection').classList.add('d-none');
    document.getElementById('userInfo').classList.add('d-none');
    document.getElementById('logoutBtn').classList.add('d-none');
    document.getElementById('changePasswordBtn').classList.add('d-none');
    document.getElementById('downloadCertBtn')?.classList.add('d-none');
    document.getElementById('adminBtn').classList.add('d-none');
    document.getElementById('adminNavigation').classList.add('d-none');
    document.getElementById('notificationsSection').classList.add('d-none');
    document.getElementById('messageBellContainer')?.classList.add('d-none');
    document.getElementById('loginForm').reset();
    window._clearNotifMarkReadTimer?.();
    window._resetNotifDetailView?.();

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
    } else if (reason === 'password_reset') {
        message = 'Votre mot de passe a été réinitialisé. Veuillez vous reconnecter.';
    } else if (reason === 'password_changed') {
        message = 'Votre mot de passe a été modifié. Veuillez vous reconnecter avec votre nouveau mot de passe.';
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
    // La délégation data-action passe les arguments en CHAÎNE ("false"/"true").
    // Or "false" est truthy en JS : sans coercition, l'ouverture via l'icône
    // (data-args="false") basculerait en mode obligatoire (× et Annuler masqués,
    // backdrop static, Échap inactif) -> modale impossible à fermer.
    mandatory = (mandatory === true || mandatory === 'true');

    // Recréer l'instance à chaque ouverture pour que backdrop/keyboard reflètent
    // le mode courant. Sinon, un modal d'abord ouvert en mode obligatoire reste
    // "static" (impossible à fermer par clic extérieur / Échap) lors des ouvertures
    // suivantes via l'icône, même quand il n'est plus obligatoire.
    if (changePasswordModal) {
        changePasswordModal.dispose();
    }
    changePasswordModal = new bootstrap.Modal(document.getElementById('changePasswordModal'), {
        backdrop: mandatory ? 'static' : true,
        keyboard: !mandatory
    });
    
    // Réinitialiser le formulaire
    document.getElementById('changePasswordForm').reset();
    document.getElementById('changePasswordError').classList.add('d-none');
    
    // Réinitialiser l'état visuel (Direction A)
    document.querySelectorAll('#changePasswordModal .cpw-inp input').forEach(inp => {
        inp.type = 'password';
    });
    document.querySelectorAll('#changePasswordModal .cpw-eye i').forEach(icon => {
        icon.className = 'bi bi-eye';
    });
    const wrap = document.getElementById('pwdStrengthWrap');
    if (wrap) {
        wrap.classList.add('d-none');
        wrap.querySelectorAll('.cpw-seg').forEach(s => { s.className = 'cpw-seg'; });
    }
    document.querySelectorAll('#pwdChecklist li').forEach(li => {
        li.classList.remove('ok');
        const icon = li.querySelector('i');
        if (icon) icon.className = 'bi bi-circle';
    });
    const submitBtn = document.getElementById('changePasswordSubmit');
    if (submitBtn) submitBtn.disabled = true;

    // Masquer/afficher les boutons de fermeture selon le mode
    const closeBtn = document.querySelector('#changePasswordModal .cpw-close');
    const cancelBtn = document.querySelector('#changePasswordModal .cpw-foot .btn-light');
    if (closeBtn) closeBtn.style.display = mandatory ? 'none' : 'block';
    if (cancelBtn) cancelBtn.style.display = mandatory ? 'none' : 'inline-block';
    
    // Afficher un message si obligatoire
    const mandatoryAlert = document.getElementById('changePasswordMandatory');
    if (mandatoryAlert) {
        mandatoryAlert.style.display = mandatory ? 'block' : 'none';
    }
    
    changePasswordModal.show();
}

/**
 * Affiche/masque un champ mot de passe et bascule l'icône œil.
 */
function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const icon = btn.querySelector('i');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    if (icon) icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
}

/**
 * Heuristique maison : score 0..4 -> nombre de blocs allumés + classes couleur.
 */
function computePasswordStrength(pwd) {
    let score = 0;
    if (pwd.length >= 8) score++;
    if (pwd.length >= 12) score++;
    if (/[a-z]/.test(pwd) && /[A-Z]/.test(pwd)) score++;
    if (/\d/.test(pwd)) score++;
    if (/[^A-Za-z0-9]/.test(pwd)) score++;
    score = Math.min(score, 4);
    const buckets = [
        { label: 'Très faible', segs: 1, seg: 'weak',    lbl: 'weak'    },
        { label: 'Faible',      segs: 1, seg: 'weak',    lbl: 'weak'    },
        { label: 'Moyen',       segs: 2, seg: 'mid',     lbl: 'mid'     },
        { label: 'Fort',        segs: 3, seg: 'strong',  lbl: 'strong'  },
        { label: 'Très fort',   segs: 4, seg: 'vstrong', lbl: 'vstrong' },
    ];
    return buckets[score];
}

/**
 * Validation temps réel : checklist + jauge de force + activation du bouton.
 */
function validateChangePasswordForm() {
    const current = document.getElementById('currentPassword').value;
    const next = document.getElementById('newPassword').value;
    const confirm = document.getElementById('confirmNewPassword').value;

    const rules = {
        length: next.length >= 8,
        different: next.length > 0 && current.length > 0 && next !== current,
        match: confirm.length > 0 && next === confirm,
    };

    document.querySelectorAll('#pwdChecklist li').forEach(li => {
        const ok = rules[li.dataset.rule];
        const icon = li.querySelector('i');
        if (icon) icon.className = ok ? 'bi bi-check-circle-fill' : 'bi bi-circle';
        li.classList.toggle('ok', ok);
    });

    const wrap = document.getElementById('pwdStrengthWrap');
    const label = document.getElementById('pwdStrengthLabel');
    const segs = wrap.querySelectorAll('.cpw-seg');
    if (next.length === 0) {
        wrap.classList.add('d-none');
        segs.forEach(s => { s.className = 'cpw-seg'; });
    } else {
        wrap.classList.remove('d-none');
        const s = computePasswordStrength(next);
        segs.forEach((seg, i) => {
            seg.className = 'cpw-seg' + (i < s.segs ? ' on-' + s.seg : '');
        });
        label.textContent = s.label;
        label.className = 'cpw-strength-label ' + s.lbl;
    }

    const allOk = current.length > 0 && rules.length && rules.different && rules.match;
    document.getElementById('changePasswordSubmit').disabled = !allOk;
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

// ===== Authentification par token (TOTP) =====

async function renderTotpTab() {
    const errorDiv = document.getElementById('totpError');
    errorDiv.classList.add('d-none');
    const enabledBox = document.getElementById('totpEnabledBox');
    const disabledBox = document.getElementById('totpDisabledBox');
    document.getElementById('totpSetupArea').classList.add('d-none');
    try {
        const me = await api.getCurrentUser();
        if (me.totp_enabled) {
            enabledBox.classList.remove('d-none');
            disabledBox.classList.add('d-none');
        } else {
            enabledBox.classList.add('d-none');
            disabledBox.classList.remove('d-none');
        }
    } catch (e) {
        errorDiv.textContent = 'Impossible de charger l\'état de l\'authentificateur';
        errorDiv.classList.remove('d-none');
    }
}

async function handleTotpSetup() {
    const errorDiv = document.getElementById('totpError');
    errorDiv.classList.add('d-none');
    try {
        const data = await api.totpSetup();
        document.getElementById('totpQr').src = data.qr_data_uri;
        document.getElementById('totpSecret').textContent = data.secret;
        document.getElementById('totpSetupArea').classList.remove('d-none');
    } catch (e) {
        errorDiv.textContent = e.message || 'Erreur lors de l\'initialisation';
        errorDiv.classList.remove('d-none');
    }
}

async function handleTotpConfirm() {
    const errorDiv = document.getElementById('totpError');
    errorDiv.classList.add('d-none');
    const code = document.getElementById('totpConfirmCode').value.trim();
    try {
        await api.totpConfirm(code);
        await renderTotpTab();
    } catch (e) {
        errorDiv.textContent = e.message || 'Code invalide';
        errorDiv.classList.remove('d-none');
    }
}

async function handleTotpDisable() {
    const errorDiv = document.getElementById('totpError');
    errorDiv.classList.add('d-none');
    const pw = document.getElementById('totpDisablePassword').value;
    try {
        await api.totpDisable(pw);
        await renderTotpTab();
    } catch (e) {
        errorDiv.textContent = e.message || 'Erreur lors de la désactivation';
        errorDiv.classList.remove('d-none');
    }
}

// ===== Récupération de mot de passe (TOTP) =====

let recoverModal = null;
let recoverResetToken = null;
let recoverMode = 'totp';   // 'totp' | 'code' — onglet actif de la modale de récupération

function switchRecoverTab(mode) {
    recoverMode = mode === 'code' ? 'code' : 'totp';
    document.querySelectorAll('#recoverTabs [data-recover-tab]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.recoverTab === recoverMode);
    });
    document.querySelectorAll('#recoverVerifyForm [data-recover-pane]').forEach(pane => {
        const active = pane.dataset.recoverPane === recoverMode;
        pane.classList.toggle('d-none', !active);
        const input = pane.querySelector('input');
        if (input) input.required = active;
    });
}

function showRecoverModal() {
    recoverResetToken = null;
    switchRecoverTab('totp');
    document.getElementById('recoverError').classList.add('d-none');
    document.getElementById('recoverSuccess').classList.add('d-none');
    document.getElementById('recoverVerifyForm').classList.remove('d-none');
    document.getElementById('recoverResetForm').classList.add('d-none');
    document.getElementById('recoverVerifyForm').reset();
    document.getElementById('recoverResetForm').reset();
    if (!recoverModal) {
        recoverModal = new bootstrap.Modal(document.getElementById('recoverModal'));
    }
    recoverModal.show();
}

async function handleRecoverVerify(event) {
    event.preventDefault();
    const errorDiv = document.getElementById('recoverError');
    errorDiv.classList.add('d-none');
    const username = document.getElementById('recoverUsername').value.trim();
    try {
        let data;
        if (recoverMode === 'code') {
            const resetCode = document.getElementById('recoverResetCodeInput').value.trim();
            if (!resetCode) throw new Error('Saisissez le code de réinitialisation remis par un administrateur');
            data = await api.recoverVerify(username, null, resetCode);
        } else {
            const code = document.getElementById('recoverCode').value.trim();
            data = await api.recoverVerify(username, code);
        }
        recoverResetToken = data.reset_token;
        document.getElementById('recoverVerifyForm').classList.add('d-none');
        document.getElementById('recoverResetForm').classList.remove('d-none');
    } catch (e) {
        errorDiv.textContent = e.message || 'Code de récupération invalide';
        errorDiv.classList.remove('d-none');
    }
}

async function handleRecoverReset(event) {
    event.preventDefault();
    const errorDiv = document.getElementById('recoverError');
    const successDiv = document.getElementById('recoverSuccess');
    errorDiv.classList.add('d-none');
    const newPassword = document.getElementById('recoverNewPassword').value;
    try {
        await api.recoverReset(recoverResetToken, newPassword);
        document.getElementById('recoverResetForm').classList.add('d-none');
        successDiv.textContent = 'Mot de passe réinitialisé. Vous pouvez vous connecter.';
        successDiv.classList.remove('d-none');
    } catch (e) {
        errorDiv.textContent = e.message || 'Échec de la réinitialisation';
        errorDiv.classList.remove('d-none');
    }
}
