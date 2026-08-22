/**
 * Hub Application - page utilisateurs et profils
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

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

/**
 * Détecte l'onglet users actif et recharge ses données.
 * Utilisé par SSE (reconnect, permissions changed) pour ne rafraîchir
 * que ce que l'utilisateur voit réellement.
 */
async function loadUsersPageActiveTab() {
    const activeTab = document.querySelector('#usersPageTabs .nav-link.active');
    if (!activeTab) {
        await loadUsersPageData();
        return;
    }
    const target = activeTab.getAttribute('data-bs-target');
    const loaders = {
        '#tabUsersList': loadUsersPageData,
        '#tabProfiles': loadProfilesTab,
        '#tabAccountRequests': loadAccountRequestsTab
    };
    const loader = loaders[target];
    if (loader) await loader();
}

async function loadUsersPageData() {
    try {
        const search = document.getElementById('usersSearchInput')?.value || '';
        const appSlug = document.getElementById('usersFilterApp')?.value || '';
        const role = document.getElementById('usersFilterRole')?.value || '';
        const profileId = document.getElementById('usersFilterProfile')?.value || '';
        const isActive = document.getElementById('usersFilterStatus')?.value || '';
        const overrideFilter = document.getElementById('usersFilterOverride')?.value || '';

        const [paginatedData, activeSessions, apps] = await Promise.all([
            api.getUsers({
                page: usersCurrentPage,
                perPage: usersPerPageVal,
                search: search || undefined,
                role: role || undefined,
                appSlug: appSlug || undefined,
                profileId: profileId !== '' ? parseInt(profileId) : undefined,
                isActive: isActive !== '' ? isActive === 'true' : undefined,
                appRoleOverride: overrideFilter !== '' ? overrideFilter === 'true' : undefined
            }),
            api.getActiveSessions().catch(() => ({ sessions: [] })),
            api.getAllApps()
        ]);

        allApps = apps;
        allUsersPage = paginatedData.users;
        
        // Stats (basées sur un appel stats séparé, ou estimées)
        document.getElementById('usersStatTotal').textContent = paginatedData.total;
        
        // Pour les stats actifs/admins/connectés, on fait un appel séparé si pas de filtre
        if (!search && !appSlug && !role && profileId === '' && isActive === '' && overrideFilter === '') {
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
                <input class="form-check-input user-profile-cb" type="checkbox" value="${p.id}" id="userProfileCb${p.id}" data-action="onUserProfileChange">
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
                            <button class="btn btn-sm btn-link p-0 text-muted user-expand-btn" data-action="toggleUserApps" data-arg1="${user.id}" title="Voir les applications">
                                <i class="bi bi-chevron-right"></i>
                            </button>
                        </td>
                        <td>
                            <strong>${escapeHtml(user.username)}</strong>
                            ${user.must_change_password ? '<i class="bi bi-key-fill text-warning ms-1" title="Doit changer son mot de passe"></i>' : ''}
                            ${user.totp_enabled ? '<i class="bi bi-shield-lock-fill text-info ms-1" title="Authentificateur activé"></i>' : ''}
                            ${user.has_role_overrides ? `<span class="badge bg-warning text-dark ms-1" title="Droits modifiés par rapport au profil"><i class="bi bi-person-badge-fill me-1"></i>Modifié</span>` : ''}
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
                            <span class="badge bg-light text-dark border" title="${(user.app_roles || []).map(ar => ar.app_name + ' (' + ar.role + ')' + ((ar.source || '').startsWith('override') ? ' [modifié]' : ((ar.source || '').startsWith('profile') ? ' [profil]' : ''))).join(', ')}">
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
                                <button class="btn btn-sm btn-outline-secondary" data-action="editUserPage" data-arg1="${user.id}" title="Modifier">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-warning" data-action="revokeUserSessions" data-arg1="${user.id}" data-arg2="${escapeHtml(user.username)}" 
                                    title="Révoquer les sessions" ${user.id === currentUser.id ? 'disabled' : ''}>
                                    <i class="bi bi-door-closed"></i>
                                </button>
                                ${user.totp_enabled ? `<button class="btn btn-sm btn-outline-info" data-action="adminDisableUserTotp" data-arg1="${user.id}" title="Désactiver l'authentificateur">
                                    <i class="bi bi-shield-slash"></i>
                                </button>` : ''}
                                <button class="btn btn-sm btn-outline-danger" data-action="deleteUserPage" data-arg1="${user.id}" data-arg2="${escapeHtml(user.username)}" 
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
                                        ${user.app_roles.map(ar => {
                                            const src = ar.source || '';
                                            const isOverride = src.startsWith('override');
                                            const isProfile = src.startsWith('profile');
                                            const viaLabel = isOverride
                                                ? `Modifié (profil : ${ar.profile_role || '?'})`
                                                : (isProfile ? 'Via profil' : 'Manuel');
                                            return `
                                            <span class="badge bg-white text-dark border me-1 mb-1 ${isOverride ? 'border-warning' : ''}" title="Source: ${viaLabel}">
                                                ${isOverride
                                                    ? '<i class="bi bi-pencil-fill me-1 text-warning" title="Modifié"></i>'
                                                    : (isProfile
                                                        ? '<i class="bi bi-person-badge me-1 text-info" title="Via profil"></i>'
                                                        : '<i class="bi bi-hand-index me-1 text-secondary" title="Manuel"></i>')}
                                                ${escapeHtml(ar.app_name)} 
                                                <span class="badge ${getRoleBadgeColor(ar.role)} ms-1">${ar.role}</span>
                                                ${isOverride ? `<span class="ms-1 small text-warning">modifié (profil : ${ar.profile_role || '?'})</span>` : ''}
                                            </span>`;
                                        }).join('')}
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

async function adminDisableUserTotp(userId) {
    if (!confirm('Désactiver l\'authentificateur de cet utilisateur ?')) return;
    try {
        await api.adminDisableTotp(userId);
        await loadUsersPageData();
    } catch (e) {
        alert(e.message || 'Erreur lors de la désactivation');
    }
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
                <button class="btn btn-primary" data-action="showProfileModal">
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
                        <button class="btn btn-outline-secondary" data-action="showProfileModal" data-arg1="${profile.id}" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-primary" data-action="showApplyProfileModal" data-arg1="${profile.id}" title="Appliquer à des utilisateurs">
                            <i class="bi bi-person-plus"></i>
                        </button>
                        <button class="btn btn-outline-danger" data-action="deleteProfile" data-arg1="${profile.id}" data-arg2="${escapeHtml(profile.name)}" title="Supprimer">
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
    if (profileId != null) profileId = parseInt(profileId, 10);
    if (!profileFormModal) {
        profileFormModal = new bootstrap.Modal(document.getElementById('profileFormModal'));
    }
    
    document.getElementById('profileForm').reset();
    document.getElementById('profileId').value = '';
    document.getElementById('profileColor').value = '#6c757d';
    const profileAppsSearchInput = document.getElementById('profileAppsSearchInput');
    if (profileAppsSearchInput) profileAppsSearchInput.value = '';
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

    const sortedApps = [...allApps].sort(compareAdminAppsByName);

    container.innerHTML = sortedApps.map(app => {
        const currentRole = rolesByAppId[app.id] || '';
        const isChecked = currentRole !== '';
        const searchName = normalizeProfileAppSearchText(app.name);
        const searchSlug = normalizeProfileAppSearchText(app.slug);
        
        return `
            <div class="d-flex align-items-center mb-2 app-role-row" data-app-name="${escapeHtml(searchName)}" data-app-slug="${escapeHtml(searchSlug)}">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input profile-app-checkbox" type="checkbox" value="${app.id}" 
                        id="profileApp${app.id}" ${isChecked ? 'checked' : ''}
                        data-action="toggleProfileRoleSelect" data-arg1="${app.id}">
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

    filterProfileApps();
}

function filterProfileApps() {
    const search = normalizeProfileAppSearchText(document.getElementById('profileAppsSearchInput')?.value).trim();
    document.querySelectorAll('#profileAppsCheckboxes .app-role-row').forEach(row => {
        const appName = row.dataset.appName || '';
        const appSlug = row.dataset.appSlug || '';
        const isMatch = !search || appName.includes(search) || appSlug.includes(search);
        row.style.display = isMatch ? '' : 'none';
    });
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
    // La delegation data-action passe les arguments en CHAINE ("true"/"false")
    checked = (checked === true || checked === 'true');
    document.querySelectorAll('#profileAppsCheckboxes .app-role-row').forEach(row => {
        if (row.style.display === 'none') return;

        const cb = row.querySelector('.profile-app-checkbox');
        if (!cb) return;

        cb.checked = checked;
        const appId = cb.value;
        const roleSelect = document.getElementById(`profileAppRole${appId}`);
        if (!roleSelect) return;

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
    profileId = parseInt(profileId, 10);
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

    // Capturer l'état courant du modal avant reconstruction (overrides inclus)
    const currentRows = {};
    allApps.forEach(app => {
        const checkbox = document.getElementById(`userApp${app.id}`);
        const roleSelect = document.getElementById(`userAppRole${app.id}`);
        if (!checkbox || !roleSelect) return;
        if (checkbox.checked && roleSelect.value) {
            currentRows[app.id] = {
                role: roleSelect.value,
                source: checkbox.dataset.source || 'manual'
            };
        }
    });

    // Rôles attendus par les profils sélectionnés (rôle max par app)
    const roleHierarchy = { admin: 4, manager: 3, user: 2, viewer: 1 };
    const bestProfileRoles = {};  // app_id -> role
    for (const pid of selectedIds) {
        const profile = allProfiles.find(p => p.id === pid);
        if (!profile) continue;
        for (const ar of profile.app_roles) {
            const level = roleHierarchy[ar.role] || 0;
            if (!bestProfileRoles[ar.app_id] || level > (roleHierarchy[bestProfileRoles[ar.app_id]] || 0)) {
                bestProfileRoles[ar.app_id] = ar.role;
            }
        }
    }

    // Combiner : apps profil (overrides existants préservés) + apps manuelles non couvertes
    const combinedRoles = [];
    const seenApps = new Set();

    for (const [appIdStr, expectedRole] of Object.entries(bestProfileRoles)) {
        const appId = parseInt(appIdStr);
        seenApps.add(appId);
        const current = currentRows[appId];
        const isOverride = (current?.source || '').startsWith('override');
        combinedRoles.push({
            app_id: appId,
            role: isOverride && current ? current.role : expectedRole,
            source: isOverride ? current.source : 'profile',
            profile_role: expectedRole
        });
    }

    allApps.forEach(app => {
        if (seenApps.has(app.id)) return;
        const current = currentRows[app.id];
        const src = current?.source || '';
        if (current && !src.startsWith('profile') && !src.startsWith('override')) {
            combinedRoles.push({ app_id: app.id, role: current.role, source: 'manual' });
        }
    });

    loadUserAppsWithRoles(combinedRoles);
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
    resetUserFormOriginalState();
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
    id = parseInt(id, 10);
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
    setUserFormOriginalState(user);
    
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
