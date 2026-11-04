// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - service accounts
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

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
        <div class="d-flex justify-content-between align-items-center mb-2">
            <small class="text-muted">${serviceAccounts.length} service account(s)</small>
            <button class="btn btn-sm btn-outline-secondary" data-action="refreshServiceAccountsStatus" title="Vérifier les credentials">
                <i class="bi bi-arrow-clockwise me-1"></i>Vérifier credentials
            </button>
        </div>
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Nom</th>
                    <th>Applications</th>
                    <th>Dernière utilisation</th>
                    <th>État</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${serviceAccounts.map(sa => `
                    <tr data-sa-id="${sa.id}">
                        <td>
                            <div>
                                <strong>${escapeHtml(sa.name)}</strong>
                                ${sa.description ? `<br><small class="text-muted">${escapeHtml(sa.description)}</small>` : ''}
                                <br><code class="small text-muted">${sa.client_id}</code>
                            </div>
                        </td>
                        <td>
                            ${sa.applications?.map(a => `<span class="badge bg-light text-dark me-1">${escapeHtml(a.name)}</span>`).join('') || '<span class="text-muted">-</span>'}
                        </td>
                        <td>
                            ${sa.last_used_at 
                                ? `<small class="text-success"><i class="bi bi-check2 me-1"></i>${parseUTCDate(sa.last_used_at).toLocaleString('fr-FR')}</small>`
                                : '<small class="text-muted"><i class="bi bi-hourglass me-1"></i>Jamais</small>'}
                        </td>
                        <td>
                            <div class="status-icons">
                                ${sa.is_active 
                                    ? '<i class="bi bi-check-circle-fill text-success" title="Config: Actif"></i>' 
                                    : '<i class="bi bi-x-circle-fill text-danger" title="Config: Inactif"></i>'}
                                <span class="sa-credentials-status" data-sa-id="${sa.id}">
                                    <i class="bi bi-question-circle text-muted" title="Credentials: Non vérifié"></i>
                                </span>
                            </div>
                        </td>
                        <td class="text-end">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-warning" data-action="regenerateServiceAccountSecret" data-arg1="${sa.id}" data-arg2="${escapeHtml(sa.name)}" title="Régénérer le secret">
                                    <i class="bi bi-key"></i>
                                </button>
                                <button class="btn btn-outline-primary" data-action="editServiceAccount" data-arg1="${sa.id}" title="Modifier">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-outline-danger" data-action="deleteServiceAccount" data-arg1="${sa.id}" data-arg2="${escapeHtml(sa.name)}" title="Supprimer">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
    
    // Lancer automatiquement la vérification des credentials
    refreshServiceAccountsStatus();
}

function showAddServiceAccountForm() {
    document.getElementById('saFormTitle').textContent = 'Nouveau Service Account';
    document.getElementById('serviceAccountForm').reset();
    document.getElementById('saId').value = '';
    document.getElementById('saActive').checked = true;
    loadSaAppsCheckboxes([]);
    serviceAccountFormModal.show();
}

async function editServiceAccount(id) {
    id = parseInt(id, 10);
    const sa = allServiceAccounts.find(s => s.id === id);
    if (!sa) return;

    document.getElementById('saFormTitle').textContent = 'Modifier le Service Account';
    document.getElementById('saId').value = sa.id;
    document.getElementById('saName').value = sa.name;
    document.getElementById('saDescription').value = sa.description || '';
    document.getElementById('saActive').checked = sa.is_active;
    
    const saAppIds = sa.applications?.map(a => a.id) || [];
    loadSaAppsCheckboxes(saAppIds);
    
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
    
    // Disposer les anciens tooltips pour éviter les fuites mémoire
    _activeTooltips.forEach(t => {
        try { t.dispose(); } catch(e) {}
    });
    _activeTooltips = [];
    
    // Initialiser les nouveaux tooltips Bootstrap
    const tooltipTriggerList = container.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(el => {
        _activeTooltips.push(new bootstrap.Tooltip(el));
    });
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
