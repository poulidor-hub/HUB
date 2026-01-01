/**
 * Hub Application - Main JavaScript
 */

let currentUser = null;
let allApps = [];
let allServiceAccounts = [];
let hubHost = null;  // IP/hostname du Hub (chargé depuis l'API)

// Bootstrap instances
let adminModal, appFormModal, userFormModal, serviceAccountFormModal, saCredentialsModal, toast;

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
    // => Ne PAS rediriger automatiquement, afficher le portail
    if (action === 'logout') {
        // Nettoyer l'URL sans recharger la page
        window.history.replaceState({}, '', window.location.pathname);
        // Effacer tout callback en attente pour éviter une reconnexion automatique
        clearPendingCallback();
        console.log('Déconnexion depuis une application - affichage du portail');
        // Continuer normalement (afficher dashboard si connecté, sinon login)
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
        currentUser = await api.getCurrentUser();
        
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
    }

    loadApps();
}

async function logout() {
    try {
        await api.logout();
    } finally {
        currentUser = null;
        document.getElementById('loginSection').classList.remove('d-none');
        document.getElementById('dashboardSection').classList.add('d-none');
        document.getElementById('userInfo').classList.add('d-none');
        document.getElementById('logoutBtn').classList.add('d-none');
        document.getElementById('adminBtn').classList.add('d-none');
        document.getElementById('loginForm').reset();
        showToast('Déconnexion', 'À bientôt !', 'info');
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

    grid.innerHTML = apps.map(app => `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card shadow-sm h-100" onclick="openApp('${app.slug}', '${app.frontend_url || ''}')">
                <div class="card-body">
                    <div class="app-icon" style="background-color: ${app.color}">
                        <i class="${app.icon}"></i>
                    </div>
                    <h5>${escapeHtml(app.name)}</h5>
                    <p>${escapeHtml(app.description || 'Aucune description')}</p>
                </div>
            </div>
        </div>
    `).join('');
}

function openApp(slug, frontendUrl) {
    if (!frontendUrl) {
        showToast('Information', 'Cette application n\'a pas de frontend configuré', 'warning');
        return;
    }

    // Logger l'accès à l'application (async, non bloquant)
    api.logAppAccess(slug).catch(e => console.warn('Échec log accès app:', e));

    // Rediriger avec le token
    const separator = frontendUrl.includes('?') ? '&' : '?';
    const url = `${frontendUrl}${separator}access_token=${api.token}`;
    window.open(url, '_blank');
}

// ===== Admin Panel =====

async function showAdminPanel() {
    try {
        await loadAdminData();
        adminModal.show();
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les données admin', 'danger');
    }
}

async function loadAdminData() {
    const [apps, users, serviceAccounts] = await Promise.all([
        api.getAllApps(),
        api.getUsers(),
        api.getServiceAccounts()
    ]);
    
    allApps = apps;
    allServiceAccounts = serviceAccounts;
    renderAppsTable(apps);
    renderUsersTable(users);
    renderServiceAccountsTable(serviceAccounts);
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
                                <strong>${escapeHtml(app.name)}</strong>
                            </div>
                        </td>
                        <td><code>${app.slug}</code></td>
                        <td><small class="text-muted">${escapeHtml(app.frontend_url || '-')}</small></td>
                        <td><span class="badge bg-secondary">${app.users?.length || 0}</span></td>
                        <td>
                            ${app.is_active 
                                ? '<span class="badge bg-success">Actif</span>' 
                                : '<span class="badge bg-danger">Inactif</span>'}
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
                                ? '<span class="badge bg-warning text-dark">Admin</span>' 
                                : '<span class="badge bg-info">User</span>'}
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
    document.getElementById('appIcon').value = 'bi-app';
    document.getElementById('appColor').value = '#0d6efd';
    document.getElementById('appActive').checked = true;
    document.getElementById('appRequiresAuth').checked = true;
    document.getElementById('appCustomHost').checked = false;
    document.getElementById('customHostGroup').classList.add('d-none');
    document.getElementById('appCreateM2M').checked = false;
    document.getElementById('createM2MSection').classList.remove('d-none');
    
    // Afficher mode simplifié (ports), masquer mode avancé (URLs)
    document.getElementById('simplePortsSection').classList.remove('d-none');
    document.getElementById('advancedUrlsSection').classList.add('d-none');
    document.getElementById('appApiPort').required = true;
    document.getElementById('appApiUrl').required = false;
    
    updateIconPreview();
    updateUrlPreview();
    adminModal.hide();
    appFormModal.show();
}

async function editApp(id) {
    const app = allApps.find(a => a.id === id);
    if (!app) return;

    // Réinitialiser le formulaire avant de remplir les valeurs
    document.getElementById('appForm').reset();
    
    document.getElementById('appFormTitle').textContent = 'Modifier l\'Application';
    document.getElementById('appId').value = app.id;
    document.getElementById('appMode').value = 'edit';
    document.getElementById('appName').value = app.name;
    document.getElementById('appSlug').value = app.slug;
    document.getElementById('appDescription').value = app.description || '';
    document.getElementById('appIcon').value = app.icon;
    document.getElementById('appColor').value = app.color;
    document.getElementById('appApiUrl').value = app.api_url;
    document.getElementById('appFrontendUrl').value = app.frontend_url || '';
    document.getElementById('appCallbackUrl').value = app.callback_url || '';
    document.getElementById('appActive').checked = app.is_active;
    document.getElementById('appRequiresAuth').checked = app.requires_auth;
    
    // En mode édition, afficher les URLs directes et masquer l'option M2M
    document.getElementById('simplePortsSection').classList.add('d-none');
    document.getElementById('advancedUrlsSection').classList.remove('d-none');
    document.getElementById('createM2MSection').classList.add('d-none');
    document.getElementById('appApiPort').required = false;
    document.getElementById('appApiUrl').required = true;
    
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
        previewFrontend.innerHTML = `<i class="bi bi-window me-1"></i>Frontend: <code>http://${host}:${frontendPort}</code>`;
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
            appFormModal.hide();
            await loadAdminData();
            adminModal.show();
        } else {
            // Mode création - utiliser les ports (schéma simplifié)
            const customHost = document.getElementById('appCustomHost').checked;
            const createM2M = document.getElementById('appCreateM2M').checked;
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
                create_service_account: createM2M
            };
            
            const result = await api.createApp(data);
            appFormModal.hide();
            
            // Afficher les credentials du service account créé
            if (result.service_account) {
                showServiceAccountCredentials(
                    result.service_account.client_id,
                    result.service_account.client_secret
                );
                showToast('Succès', 'Application et Service Account créés', 'success');
            } else {
                showToast('Succès', 'Application créée', 'success');
                adminModal.show();
            }
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
}

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
        await loadAdminData();
        adminModal.show();
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
                                ? '<span class="badge bg-success">Actif</span>' 
                                : '<span class="badge bg-danger">Inactif</span>'}
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

    container.innerHTML = allApps.map(app => `
        <div class="form-check">
            <input class="form-check-input" type="checkbox" value="${app.id}" 
                id="saApp${app.id}" ${selectedIds.includes(app.id) ? 'checked' : ''}>
            <label class="form-check-label" for="saApp${app.id}">
                <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                ${escapeHtml(app.name)}
            </label>
        </div>
    `).join('');
}

async function handleSaveServiceAccount(event) {
    event.preventDefault();
    
    const id = document.getElementById('saId').value;
    
    // Get selected apps
    const selectedAppIds = [];
    allApps.forEach(app => {
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

function showServiceAccountCredentials(clientId, clientSecret) {
    document.getElementById('saClientId').value = clientId;
    document.getElementById('saClientSecret').value = clientSecret;
    document.getElementById('exampleClientId').textContent = clientId;
    document.getElementById('exampleClientSecret').textContent = clientSecret;
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
                        <td><small>${s.logout_at ? formatDateTime(s.logout_at) : '<span class="badge bg-success">En cours</span>'}</small></td>
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
    if (!reason) return '<span class="badge bg-success">Actif</span>';
    
    const badges = {
        'explicit': '<span class="badge bg-info">Déconnexion</span>',
        'timeout': '<span class="badge bg-warning text-dark">Timeout</span>',
        'token_revoked': '<span class="badge bg-danger">Révoqué</span>',
        'forced': '<span class="badge bg-danger">Forcé</span>'
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
        'access_app': '<span class="badge bg-primary">Accès App</span>',
        'login': '<span class="badge bg-success">Connexion</span>',
        'logout': '<span class="badge bg-info">Déconnexion</span>',
        'service_account_login': '<span class="badge bg-purple" style="background-color: #6f42c1;">M2M</span>'
    };
    return badges[action] || `<span class="badge bg-secondary">${action}</span>`;
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
        const result = await api.scanDockerContainers(includeStopped);
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
        
        return `
            <div class="list-group-item ${selectedClass}" id="container-${container.container_id}">
                <div class="d-flex w-100 justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-${statusIcon} text-${statusClass} me-2"></i>
                            <strong>${escapeHtml(container.name)}</strong>
                            ${selectedBadge}
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
                                    title="Définir comme API Backend">
                                <i class="bi bi-hdd-rack"></i> API
                            </button>
                            <button type="button" class="btn ${isFrontend ? 'btn-success' : 'btn-outline-success'}" 
                                    onclick="selectDockerContainer('${container.container_id}', 'frontend')"
                                    title="Définir comme Frontend">
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
    
    if (!summary || !importBtn) return;
    
    const hasApi = dockerSelection.api !== null;
    const hasFrontend = dockerSelection.frontend !== null;
    
    if (!hasApi && !hasFrontend) {
        summary.innerHTML = '<span class="text-muted">Aucune sélection</span>';
        importBtn.disabled = true;
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
    }
}

/**
 * Importe la sélection Docker comme application
 */
function importDockerSelection() {
    if (!dockerSelection.api) {
        showToast('Erreur', 'Veuillez sélectionner au moins un conteneur API', 'warning');
        return;
    }
    
    const apiContainer = dockerSelection.api;
    const frontendContainer = dockerSelection.frontend;
    
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
    document.getElementById('appActive').checked = apiContainer.status === 'running';
    document.getElementById('appRequiresAuth').checked = true;
    document.getElementById('appCustomHost').checked = false;
    document.getElementById('customHostGroup').classList.add('d-none');
    document.getElementById('appCreateM2M').checked = false;
    document.getElementById('createM2MSection').classList.remove('d-none');
    
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

