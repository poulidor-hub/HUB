/**
 * Hub Application - Main JavaScript
 */

let currentUser = null;
let allApps = [];

// Bootstrap instances
let adminModal, appFormModal, userFormModal, toast;

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Bootstrap components
    adminModal = new bootstrap.Modal(document.getElementById('adminModal'));
    appFormModal = new bootstrap.Modal(document.getElementById('appFormModal'));
    userFormModal = new bootstrap.Modal(document.getElementById('userFormModal'));
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
    const [apps, users] = await Promise.all([
        api.getAllApps(),
        api.getUsers()
    ]);
    
    allApps = apps;
    renderAppsTable(apps);
    renderUsersTable(users);
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
                    <th>Rôle</th>
                    <th>Applications</th>
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
                            ${user.applications?.map(a => `<span class="badge bg-light text-dark me-1">${escapeHtml(a.name)}</span>`).join('') || '-'}
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

// ===== App CRUD =====

function showAddAppForm() {
    document.getElementById('appFormTitle').textContent = 'Nouvelle Application';
    document.getElementById('appForm').reset();
    document.getElementById('appId').value = '';
    document.getElementById('appIcon').value = 'bi-app';
    document.getElementById('appColor').value = '#0d6efd';
    document.getElementById('appActive').checked = true;
    document.getElementById('appRequiresAuth').checked = true;
    updateIconPreview();
    adminModal.hide();
    appFormModal.show();
}

async function editApp(id) {
    const app = allApps.find(a => a.id === id);
    if (!app) return;

    document.getElementById('appFormTitle').textContent = 'Modifier l\'Application';
    document.getElementById('appId').value = app.id;
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
    updateIconPreview();
    
    adminModal.hide();
    appFormModal.show();
}

async function handleSaveApp(event) {
    event.preventDefault();
    
    const id = document.getElementById('appId').value;
    const data = {
        name: document.getElementById('appName').value,
        slug: document.getElementById('appSlug').value,
        description: document.getElementById('appDescription').value || null,
        icon: document.getElementById('appIcon').value,
        color: document.getElementById('appColor').value,
        api_url: document.getElementById('appApiUrl').value,
        frontend_url: document.getElementById('appFrontendUrl').value || null,
        callback_url: document.getElementById('appCallbackUrl').value || null,
        is_active: document.getElementById('appActive').checked,
        requires_auth: document.getElementById('appRequiresAuth').checked
    };

    try {
        if (id) {
            await api.updateApp(id, data);
            showToast('Succès', 'Application modifiée', 'success');
        } else {
            await api.createApp(data);
            showToast('Succès', 'Application créée', 'success');
        }
        appFormModal.hide();
        await loadAdminData();
        adminModal.show();
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
    
    const userAppIds = user.applications?.map(a => a.id) || [];
    loadUserAppsCheckboxes(userAppIds);
    
    adminModal.hide();
    userFormModal.show();
}

function loadUserAppsCheckboxes(selectedIds) {
    const container = document.getElementById('userAppsCheckboxes');
    
    if (allApps.length === 0) {
        container.innerHTML = '<small class="text-muted">Aucune application disponible</small>';
        return;
    }

    container.innerHTML = allApps.map(app => `
        <div class="form-check">
            <input class="form-check-input" type="checkbox" value="${app.id}" 
                id="userApp${app.id}" ${selectedIds.includes(app.id) ? 'checked' : ''}>
            <label class="form-check-label" for="userApp${app.id}">
                <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                ${escapeHtml(app.name)}
            </label>
        </div>
    `).join('');
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

    // Get selected apps
    const selectedAppIds = [];
    allApps.forEach(app => {
        if (document.getElementById(`userApp${app.id}`)?.checked) {
            selectedAppIds.push(app.id);
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
        
        // Update apps access
        await api.setUserApps(userId, selectedAppIds);
        
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
