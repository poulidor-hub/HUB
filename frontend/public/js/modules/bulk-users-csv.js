// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - bulk users et import CSV
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

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
                           placeholder="Rechercher..." data-action="filterBulkUsers">
                </div>
            </div>
            <div class="mb-2">
                <button type="button" class="btn btn-sm btn-outline-secondary me-1" data-action="selectAllBulkUsers">
                    <i class="bi bi-check-all"></i> Tout sélectionner
                </button>
                <button type="button" class="btn btn-sm btn-outline-secondary" data-action="deselectAllBulkUsers">
                    <i class="bi bi-x-lg"></i> Tout désélectionner
                </button>
                <span class="ms-2 text-muted small" id="bulkSelectedCount">0 sélectionné(s)</span>
            </div>
            <div class="bulk-users-list border rounded p-2" style="max-height: 250px; overflow-y: auto;">
                ${users.filter(u => u.id !== currentUser?.id).map(user => `
                    <div class="form-check bulk-user-item" data-username="${escapeHtml(user.username.toLowerCase())}">
                        <input class="form-check-input bulk-user-checkbox" type="checkbox" 
                               value="${user.id}" id="bulkUser_${user.id}"
                               data-action="toggleBulkUserSelection" data-args="${user.id}">
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
 * (userId peut venir d'un data-args — chaîne — ou d'un appel JS — nombre)
 */
function toggleBulkUserSelection(userId) {
    const id = parseInt(userId);
    if (selectedUsersForBulk.has(id)) {
        selectedUsersForBulk.delete(id);
    } else {
        selectedUsersForBulk.add(id);
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
                        data-action="toggleBulkAdvRole" data-args="${app.id}">
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
                <button type="button" class="btn btn-sm btn-outline-danger" data-action="removeBulkCreateRow" data-args="$el">
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
