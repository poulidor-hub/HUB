// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - administration des groupes d'applications
 *
 * Groupes = conteneurs purement visuels du dashboard.
 * Mêmes conventions que admin-apps.js : fonctions globales + handlers inline/data-action.
 */

// ===== Admin App Groups =====

let allAppGroups = [];
let appGroupFormModal = null;
let appGroupDeleteModal = null;
let pendingDeleteGroupId = null;

async function loadAppGroups() {
    try {
        const groups = await api.getAppGroups();
        allAppGroups = Array.isArray(groups) ? groups : [];
        renderAppGroupsTable(allAppGroups);
        refreshAppGroupOptions();
    } catch (error) {
        console.error('loadAppGroups: Erreur:', error);
        const table = document.getElementById('appGroupsTable');
        if (table) {
            table.innerHTML = `<div class="alert alert-danger mb-0">Impossible de charger les groupes : ${escapeHtml(error.message || 'erreur inconnue')}</div>`;
        }
    }
}

function renderAppGroupsTable(groups) {
    const container = document.getElementById('appGroupsTable');
    if (!container) return;

    if (!groups.length) {
        container.innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-folder2" style="font-size:2rem"></i>
                <p class="mt-2 mb-1">Aucun groupe pour le moment</p>
                <small>Créez un groupe, puis affectez-y des applications depuis leur formulaire.</small>
            </div>`;
        return;
    }

    container.innerHTML = `
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Groupe</th>
                    <th>Description</th>
                    <th class="text-center">Applications</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${groups.map(g => `
                    <tr>
                        <td>
                            <div class="d-flex align-items-center gap-2">
                                <span class="app-group-modal-icon" style="background-color: ${g.color}"><i class="${g.icon || 'bi-collection'}"></i></span>
                                <strong>${escapeHtml(g.name)}</strong>
                            </div>
                        </td>
                        <td class="text-muted">${escapeHtml(g.description || '—')}</td>
                        <td class="text-center"><span class="badge app-group-badge">${g.app_count}</span></td>
                        <td class="text-end">
                            <button class="btn btn-outline-secondary btn-sm me-1" data-action="editAppGroup" data-arg1="${g.id}" title="Modifier">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-outline-danger btn-sm" data-action="confirmDeleteAppGroup" data-arg1="${g.id}" title="Supprimer">
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>`;
}

/** Met à jour les options du select "Groupe" du formulaire application */
function refreshAppGroupOptions(selectedId) {
    const select = document.getElementById('appGroup');
    if (!select) return;
    select.innerHTML = [
        '<option value="">— Aucun —</option>',
        ...allAppGroups.map(g =>
            `<option value="${g.id}" ${String(selectedId) === String(g.id) ? 'selected' : ''}>${escapeHtml(g.name)}</option>`
        )
    ].join('');
    if (selectedId === undefined || selectedId === null) select.value = '';
}

function showAddGroupForm() {
    document.getElementById('appGroupFormTitle').textContent = 'Nouveau groupe';
    document.getElementById('appGroupId').value = '';
    document.getElementById('appGroupName').value = '';
    document.getElementById('appGroupDescription').value = '';
    document.getElementById('appGroupIcon').value = 'bi-collection';
    document.getElementById('appGroupColor').value = '#0d6efd';
    updateGroupFormPreview();
    if (!appGroupFormModal) {
        appGroupFormModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('appGroupFormModal'));
    }
    appGroupFormModal.show();
}

function editAppGroup(id) {
    id = parseInt(id, 10);
    const group = allAppGroups.find(g => g.id === id);
    if (!group) return;
    document.getElementById('appGroupFormTitle').textContent = 'Modifier le groupe';
    document.getElementById('appGroupId').value = group.id;
    document.getElementById('appGroupName').value = group.name;
    document.getElementById('appGroupDescription').value = group.description || '';
    document.getElementById('appGroupIcon').value = group.icon || 'bi-collection';
    document.getElementById('appGroupColor').value = group.color || '#0d6efd';
    updateGroupFormPreview();
    if (!appGroupFormModal) {
        appGroupFormModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('appGroupFormModal'));
    }
    appGroupFormModal.show();
}

/** Aperçu live icône + couleur du formulaire groupe */
function updateGroupFormPreview() {
    const icon = document.getElementById('appGroupIcon')?.value || 'bi-collection';
    const color = document.getElementById('appGroupColor')?.value || '#0d6efd';
    const preview = document.getElementById('appGroupFormPreview');
    if (!preview) return;
    preview.style.background = color;
    preview.querySelector('i').className = `bi ${icon}`;
}

async function handleSaveAppGroup(event) {
    event.preventDefault();
    const id = document.getElementById('appGroupId').value;
    const data = {
        name: document.getElementById('appGroupName').value.trim(),
        description: document.getElementById('appGroupDescription').value.trim() || null,
        icon: document.getElementById('appGroupIcon').value.trim() || 'bi-collection',
        color: document.getElementById('appGroupColor').value,
    };
    if (!data.name || data.name.length < 2) {
        showToast('Erreur', 'Le nom du groupe doit contenir au moins 2 caractères', 'warning');
        return;
    }
    try {
        if (id) {
            await api.updateAppGroup(id, data);
            showToast('Succès', 'Groupe modifié', 'success');
        } else {
            await api.createAppGroup(data);
            showToast('Succès', 'Groupe créé', 'success');
        }
        appGroupFormModal.hide();
        await loadAppGroups();
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible d\'enregistrer le groupe', 'danger');
    }
}

function confirmDeleteAppGroup(id) {
    id = parseInt(id, 10);
    const group = allAppGroups.find(g => g.id === id);
    if (!group) return;
    pendingDeleteGroupId = id;
    document.getElementById('appGroupDeleteName').textContent = group.name;
    if (!appGroupDeleteModal) {
        appGroupDeleteModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('appGroupDeleteModal'));
    }
    appGroupDeleteModal.show();
}

async function handleDeleteAppGroup() {
    if (!pendingDeleteGroupId) return;
    try {
        await api.deleteAppGroup(pendingDeleteGroupId);
        showToast('Succès', 'Groupe supprimé — les applications ont été dégrouppées', 'success');
        appGroupDeleteModal.hide();
        pendingDeleteGroupId = null;
        // Recharger apps + groupes (les apps ont perdu leur groupe)
        await Promise.all([loadAppGroups(), loadAdminData()]);
        loadApps(); // Refresh dashboard
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible de supprimer le groupe', 'danger');
    }
}
