/**
 * Hub Application - CRUD utilisateurs modal
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== User CRUD =====

function showAddUserForm() {
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
    if (canChangePwdEl) canChangePwdEl.checked = true;
    document.querySelectorAll('.user-profile-cb').forEach(checkbox => { checkbox.checked = false; });
    updateUserProfileDropdownLabel([]);
    loadUserAppsCheckboxes([]);
    userFormModal.show();
}

async function editUser(id) {
    id = parseInt(id, 10);
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

    const canChangePwdEl = document.getElementById('userCanChangePassword');
    if (canChangePwdEl) {
        canChangePwdEl.checked = user.can_change_password !== false;
    }

    const userProfileIds = (user.profiles || []).map(profile => profile.id);
    document.querySelectorAll('.user-profile-cb').forEach(checkbox => {
        checkbox.checked = userProfileIds.includes(parseInt(checkbox.value, 10));
    });
    updateUserProfileDropdownLabel(userProfileIds);
    
    // Charger les apps avec leurs rôles
    const userAppRoles = user.app_roles || [];
    loadUserAppsWithRoles(userAppRoles);
    setUserFormOriginalState(user);
    
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

    // Map des rôles, sources et rôles attendus par app_id
    const rolesByAppId = {};
    const sourceByAppId = {};
    const profileRoleByAppId = {};
    userAppRoles.forEach(ar => {
        rolesByAppId[ar.app_id] = ar.role;
        sourceByAppId[ar.app_id] = ar.source || 'manual';
        if (ar.profile_role) profileRoleByAppId[ar.app_id] = ar.profile_role;
    });

    container.innerHTML = allApps.map(app => {
        const currentRole = rolesByAppId[app.id] || '';
        const source = sourceByAppId[app.id] || 'manual';
        const profileRole = profileRoleByAppId[app.id] || null;
        const isOverridden = source.startsWith('override');
        const isChecked = currentRole !== '' || profileRole !== null;
        const effectiveRole = currentRole || profileRole || '';

        return `
            <div class="d-flex align-items-center mb-2 app-role-row">
                <div class="form-check me-3" style="min-width: 200px;">
                    <input class="form-check-input app-checkbox" type="checkbox" value="${app.id}" 
                        id="userApp${app.id}" ${isChecked ? 'checked' : ''}
                        ${profileRole !== null ? 'disabled' : ''}
                        data-source="${source}"
                        data-profile-role="${profileRole || ''}"
                        data-action="toggleRoleSelect" data-arg1="${app.id}">
                    <label class="form-check-label" for="userApp${app.id}">
                        <i class="${app.icon} me-1" style="color: ${app.color}"></i>
                        ${escapeHtml(app.name)}
                        ${profileRole !== null ? '<i class="bi bi-person-badge text-info ms-1" title="Géré par le profil"></i>' : ''}
                    </label>
                </div>
                <select class="form-select form-select-sm role-select" id="userAppRole${app.id}" 
                    style="width: 120px;" ${isChecked ? '' : 'disabled'}
                    data-source="${source}" data-profile-role="${profileRole || ''}">
                    <option value="">-- Rôle --</option>
                    ${AVAILABLE_ROLES.map(role => `
                        <option value="${role}" ${effectiveRole === role ? 'selected' : ''}>
                            ${role.charAt(0).toUpperCase() + role.slice(1)}
                        </option>
                    `).join('')}
                </select>
                ${profileRole !== null ? `<small class="text-info ms-2"><i class="bi bi-person-badge"></i> Profil : ${profileRole}</small>` : ''}
                ${isOverridden ? `
                    <span class="badge bg-warning text-dark ms-2" data-override-badge="${app.id}">
                        <i class="bi bi-pencil-fill"></i> Modifié
                    </span>
                    <button type="button" class="btn btn-sm btn-outline-secondary ms-1" 
                        data-action="resetAppRoleToProfile" data-arg1="${app.id}" title="Réinitialiser selon profil">
                        <i class="bi bi-arrow-counterclockwise"></i>
                    </button>
                ` : ''}
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

function resetAppRoleToProfile(appId) {
    const roleSelect = document.getElementById(`userAppRole${appId}`);
    const profileRole = roleSelect?.dataset?.profileRole;
    if (!roleSelect || !profileRole) return;
    roleSelect.value = profileRole;
    roleSelect.dataset.reset = 'true';
    const badge = document.querySelector(`[data-override-badge="${appId}"]`);
    if (badge) badge.remove();
}

// Ancienne fonction pour compatibilité (utilisée dans showAddUserForm)
function loadUserAppsCheckboxes(selectedIds) {
    // Convertir en format app_roles
    const appRoles = selectedIds.map(id => ({ app_id: id, role: 'user' }));
    loadUserAppsWithRoles(appRoles);
}

function getSelectedUserProfileIds() {
    return normalizeUserProfileIds(
        Array.from(document.querySelectorAll('.user-profile-cb:checked'))
            .map(checkbox => checkbox.value)
    );
}

function hasUserProfileControls() {
    return document.querySelectorAll('.user-profile-cb').length > 0;
}

function sameUserProfileIds(leftIds, rightIds) {
    const normalizedLeftIds = normalizeUserProfileIds(leftIds);
    const normalizedRightIds = normalizeUserProfileIds(rightIds);
    if (normalizedLeftIds.length !== normalizedRightIds.length) return false;
    return normalizedLeftIds.every((profileId, index) => profileId === normalizedRightIds[index]);
}

function resetUserFormOriginalState() {
    const form = document.getElementById('userForm');
    if (!form) return;
    delete form.dataset.originalUserState;
}

function setUserFormOriginalState(user) {
    const form = document.getElementById('userForm');
    if (!form) return;
    form.dataset.originalUserState = JSON.stringify({
        email: user.email || null,
        is_admin: Boolean(user.is_admin),
        is_active: Boolean(user.is_active),
        must_change_password: Boolean(user.must_change_password),
        can_change_password: user.can_change_password !== false,
        profile_ids: normalizeUserProfileIds((user.profiles || []).map(profile => profile.id))
    });
}

function getUserFormOriginalState() {
    const form = document.getElementById('userForm');
    if (!form?.dataset?.originalUserState) return null;
    try {
        return JSON.parse(form.dataset.originalUserState);
    } catch (error) {
        console.warn('État utilisateur initial illisible:', error);
        return null;
    }
}

function buildChangedUserUpdateData(data, selectedProfileIds, password) {
    const originalState = getUserFormOriginalState();
    const updateData = {};
    const normalizedEmail = data.email || null;
    const normalizedProfileIds = normalizeUserProfileIds(selectedProfileIds);

    if (!originalState || normalizedEmail !== (originalState.email || null)) {
        updateData.email = normalizedEmail;
    }

    ['is_admin', 'is_active', 'must_change_password', 'can_change_password'].forEach(field => {
        if (!originalState || data[field] !== originalState[field]) {
            updateData[field] = data[field];
        }
    });

    if (hasUserProfileControls() && (!originalState || !sameUserProfileIds(normalizedProfileIds, originalState.profile_ids))) {
        updateData.profile_ids = normalizedProfileIds;
    }

    if (password) {
        updateData.password = password;
    }

    return updateData;
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
    const selectedProfileIds = getSelectedUserProfileIds();
    data.profile_ids = selectedProfileIds;
    
    // is_active n'est pas dans le schéma UserCreate, uniquement pour la mise à jour
    if (!isCreating) {
        data.is_active = document.getElementById('userActive').checked;
    }

    const password = document.getElementById('userPassword').value;
    if (password) {
        data.password = password;
    }

    // Récupérer les apps et leurs rôles (manuelles + overrides sur apps profilées)
    const assignments = [];
    allApps.forEach(app => {
        const checkbox = document.getElementById(`userApp${app.id}`);
        const roleSelect = document.getElementById(`userAppRole${app.id}`);
        if (!checkbox || !roleSelect) return;

        const profileRole = checkbox.dataset.profileRole || null;
        if (profileRole) {
            // App couverte par un profil : niveau modifiable (override), accès non retirable
            const chosenRole = roleSelect.value || profileRole;
            const currentSource = checkbox.dataset.source || '';
            if (chosenRole !== profileRole) {
                // Rôle différent du rôle attendu → override
                assignments.push({ app_id: app.id, role: chosenRole });
            } else if (currentSource.startsWith('override') || roleSelect.dataset.reset === 'true') {
                // Retour au rôle attendu alors qu'un override existe → reset explicite
                assignments.push({ app_id: app.id, role: chosenRole, reset: true });
            }
            // Sinon : conforme au profil, rien à envoyer
            return;
        }

        if (checkbox.checked && roleSelect.value) {
            assignments.push({ app_id: app.id, role: roleSelect.value });
        }
    });

    try {
        let userId = id;
        if (id) {
            const updateData = buildChangedUserUpdateData(data, selectedProfileIds, password);
            if (Object.keys(updateData).length > 0) {
                await api.updateUser(id, updateData);
            }
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
