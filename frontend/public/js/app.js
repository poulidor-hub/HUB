/**
 * Hub Application - Main JavaScript
 */

let currentUser = null;
let allApps = [];
let allServiceAccounts = [];
let hubHost = null;  // IP/hostname du Hub (chargé depuis l'API)

// Bootstrap instances
let adminModal, appFormModal, userFormModal, serviceAccountFormModal, saCredentialsModal, toast;

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Bootstrap components
    adminModal = new bootstrap.Modal(document.getElementById('adminModal'));
    appFormModal = new bootstrap.Modal(document.getElementById('appFormModal'));
    userFormModal = new bootstrap.Modal(document.getElementById('userFormModal'));
    serviceAccountFormModal = new bootstrap.Modal(document.getElementById('serviceAccountFormModal'));
    saCredentialsModal = new bootstrap.Modal(document.getElementById('saCredentialsModal'));
    toast = new bootstrap.Toast(document.getElementById('toast'));

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
        showDashboard();
    } catch (error) {
        console.error('Failed to load user:', error);
        api.clearToken();
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
    
    // En mode édition, afficher les URLs directes
    document.getElementById('simplePortsSection').classList.add('d-none');
    document.getElementById('advancedUrlsSection').classList.remove('d-none');
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
            const data = {
                name: document.getElementById('appName').value,
                description: document.getElementById('appDescription').value || null,
                icon: document.getElementById('appIcon').value,
                color: document.getElementById('appColor').value,
                api_url: document.getElementById('appApiUrl').value,
                frontend_url: document.getElementById('appFrontendUrl').value || null,
                callback_url: document.getElementById('appCallbackUrl').value || null,
                is_active: document.getElementById('appActive').checked,
                requires_auth: document.getElementById('appRequiresAuth').checked
            };
            
            await api.updateApp(id, data);
            showToast('Succès', 'Application modifiée', 'success');
            appFormModal.hide();
            await loadAdminData();
            adminModal.show();
        } else {
            // Mode création - utiliser les ports (schéma simplifié)
            const customHost = document.getElementById('appCustomHost').checked;
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
                requires_auth: document.getElementById('appRequiresAuth').checked
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
