// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - scanner Docker
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Docker Scanner =====

let dockerScanModal;
let lastDockerScan = null;
let dockerSelection = { api: null, frontend: null };  // Legacy (unused)
let dockerGroupSelection = {};  // { base_slug: true } pour la sélection groupée

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
    document.getElementById('dockerShowAll').checked = false;
    updateDockerSelectionSummary();
    
    dockerScanModal.show();
    
    // Lancer le scan
    await performDockerScan();
}

/**
 * Effectue le scan Docker
 */
async function performDockerScan() {
    const includeStopped = document.getElementById('dockerIncludeStopped').checked;
    const showAll = document.getElementById('dockerShowAll').checked;
    
    // Reset sélection groupée
    dockerGroupSelection = {};
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
    document.getElementById('dockerImportResults').classList.add('d-none');
    
    try {
        const result = await api.scanDockerContainers(includeStopped, showAll);
        lastDockerScan = result;
        
        document.getElementById('dockerStatus').classList.add('d-none');
        
        if (!result.available) {
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
        
        // Afficher les groupes d'applications détectés
        if (result.groups && result.groups.length > 0) {
            document.getElementById('dockerContainers').classList.remove('d-none');
            const importableGroups = result.groups.filter(g => !g.is_registered);
            document.getElementById('dockerGroupCount').textContent = `${result.groups.length} application(s) détectée(s)${importableGroups.length < result.groups.length ? ` (${importableGroups.length} importable(s))` : ''}`;
            renderDockerGroups(result.groups);
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
 * Affiche les groupes d'applications détectés par le scanner Docker
 */
function renderDockerGroups(groups) {
    const list = document.getElementById('containersList');
    
    list.innerHTML = groups.map(group => {
        const isRegistered = group.is_registered;
        const isChecked = dockerGroupSelection[group.base_slug] === true;
        const registeredClass = isRegistered ? 'opacity-50' : '';
        const registeredBadge = isRegistered ? '<span class="badge bg-warning text-dark ms-1"><i class="bi bi-check-circle me-1"></i>Déjà enregistré</span>' : '';
        
        // Conteneur API
        let apiInfo = '';
        if (group.api_container) {
            const c = group.api_container;
            const port = group.api_port || '?';
            const statusClass = c.status === 'running' ? 'success' : 'secondary';
            apiInfo = `
                <div class="d-flex align-items-center mb-1">
                    <span class="badge bg-danger me-2">API</span>
                    <i class="bi bi-circle-fill text-${statusClass} me-1" style="font-size: 0.5rem;"></i>
                    <small class="text-muted">${escapeHtml(c.name)}</small>
                    <span class="badge bg-primary ms-2">:${port}</span>
                    <small class="text-muted ms-2">${escapeHtml(c.image)}</small>
                </div>
            `;
        }
        
        // Conteneur Frontend
        let frontInfo = '';
        if (group.frontend_container) {
            const c = group.frontend_container;
            const port = group.frontend_port || '?';
            const statusClass = c.status === 'running' ? 'success' : 'secondary';
            frontInfo = `
                <div class="d-flex align-items-center mb-1">
                    <span class="badge bg-success me-2">Front</span>
                    <i class="bi bi-circle-fill text-${statusClass} me-1" style="font-size: 0.5rem;"></i>
                    <small class="text-muted">${escapeHtml(c.name)}</small>
                    <span class="badge bg-primary ms-2">:${port}</span>
                    <small class="text-muted ms-2">${escapeHtml(c.image)}</small>
                </div>
            `;
        }
        
        // Conteneurs non groupés
        let ungroupedInfo = '';
        if (group.ungrouped_containers && group.ungrouped_containers.length > 0) {
            ungroupedInfo = group.ungrouped_containers.map(c => {
                const port = c.ports[0]?.host_port || '?';
                return `
                    <div class="d-flex align-items-center mb-1">
                        <span class="badge bg-secondary me-2">?</span>
                        <small class="text-muted">${escapeHtml(c.name)} :${port} (${escapeHtml(c.image)})</small>
                    </div>
                `;
            }).join('');
        }
        
        // Si pas d'API mais des ungrouped, considérer le premier comme API
        const hasApi = !!group.api_container || (group.ungrouped_containers && group.ungrouped_containers.length > 0);
        
        return `
            <div class="card mb-2 docker-group-card ${registeredClass} ${isChecked ? 'border-success border-2' : ''}" data-group-slug="${escapeHtml(group.base_slug)}">
                <div class="card-body py-2 px-3">
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="d-flex align-items-start flex-grow-1">
                            <div class="form-check me-3 mt-1">
                                <input class="form-check-input docker-group-check" type="checkbox" 
                                    id="dockerGroup-${escapeHtml(group.base_slug)}" 
                                    data-group-slug="${escapeHtml(group.base_slug)}"
                                    ${isRegistered ? 'disabled' : ''}
                                    ${isChecked ? 'checked' : ''}
                                    data-action="toggleDockerGroupSelection" data-arg1="${escapeHtml(group.base_slug)}">
                            </div>
                            <div class="flex-grow-1">
                                <div class="d-flex align-items-center mb-1">
                                    <strong>${escapeHtml(group.suggested_name)}</strong>
                                    <code class="ms-2 small text-muted">${escapeHtml(group.base_slug)}</code>
                                    ${registeredBadge}
                                </div>
                                ${group.description ? `<small class="text-muted d-block mb-1">${escapeHtml(group.description)}</small>` : ''}
                                ${apiInfo}
                                ${frontInfo}
                                ${ungroupedInfo}
                            </div>
                        </div>
                        <div class="text-end">
                            ${!isRegistered ? `
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input docker-shared-check" type="checkbox" 
                                        id="dockerShared-${escapeHtml(group.base_slug)}"
                                        data-group-slug="${escapeHtml(group.base_slug)}"
                                        ${group.is_shared ? 'checked' : ''}>
                                    <label class="form-check-label small" for="dockerShared-${escapeHtml(group.base_slug)}">
                                        <i class="bi bi-share text-primary"></i> M2M
                                    </label>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Sélection/désélection d'un groupe d'application
 */
function toggleDockerGroupSelection(baseSlug) {
    if (dockerGroupSelection[baseSlug]) {
        delete dockerGroupSelection[baseSlug];
    } else {
        dockerGroupSelection[baseSlug] = true;
    }
    updateDockerSelectionSummary();
}

/**
 * Sélection/désélection de tous les groupes importables
 */
function toggleDockerSelectAll() {
    const selectAll = document.getElementById('dockerSelectAll').checked;
    const checkboxes = document.querySelectorAll('.docker-group-check:not(:disabled)');
    
    dockerGroupSelection = {};
    checkboxes.forEach(cb => {
        cb.checked = selectAll;
        if (selectAll) {
            dockerGroupSelection[cb.dataset.groupSlug] = true;
        }
    });
    updateDockerSelectionSummary();
}

/**
 * Met à jour le résumé de la sélection
 */
function updateDockerSelectionSummary() {
    const summary = document.getElementById('dockerSelectionSummary');
    const importBtn = document.getElementById('dockerBatchImportBtn');
    
    if (!summary || !importBtn) return;
    
    const selectedCount = Object.keys(dockerGroupSelection).length;
    
    if (selectedCount === 0) {
        summary.innerHTML = '<span class="text-muted">Aucune sélection</span>';
        importBtn.disabled = true;
    } else {
        const slugs = Object.keys(dockerGroupSelection);
        const maxShow = 3;
        let html = slugs.slice(0, maxShow).map(s => `<span class="badge bg-info text-dark me-1">${escapeHtml(s)}</span>`).join('');
        if (slugs.length > maxShow) {
            html += `<span class="text-muted small">+${slugs.length - maxShow} autre(s)</span>`;
        }
        summary.innerHTML = html;
        importBtn.disabled = false;
        importBtn.innerHTML = `<i class="bi bi-lightning-charge me-1"></i>Importer (${selectedCount})`;
    }
}

/**
 * Import lot : enregistre toutes les applications sélectionnées avec leurs SA
 */
async function batchImportDockerSelection() {
    const selectedSlugs = Object.keys(dockerGroupSelection);
    if (selectedSlugs.length === 0) {
        showToast('Erreur', 'Aucune application sélectionnée', 'warning');
        return;
    }
    
    if (!lastDockerScan || !lastDockerScan.groups) return;
    
    // Construire la liste d'apps à importer
    const apps = [];
    for (const slug of selectedSlugs) {
        const group = lastDockerScan.groups.find(g => g.base_slug === slug);
        if (!group || group.is_registered) continue;
        
        // Déterminer le port API
        let apiPort = group.api_port;
        if (!apiPort && group.ungrouped_containers && group.ungrouped_containers.length > 0) {
            apiPort = group.ungrouped_containers[0].ports[0]?.host_port;
        }
        if (!apiPort) {
            showToast('Erreur', `Pas de port API détecté pour "${slug}"`, 'warning');
            continue;
        }
        
        // Check shared
        const sharedCb = document.getElementById(`dockerShared-${slug}`);
        const isShared = sharedCb ? sharedCb.checked : group.is_shared;
        
        apps.push({
            base_slug: slug,
            name: group.suggested_name,
            description: group.description,
            api_port: apiPort,
            frontend_port: group.frontend_port || null,
            is_shared: isShared
        });
    }
    
    if (apps.length === 0) {
        showToast('Info', 'Aucune application importable dans la sélection', 'info');
        return;
    }
    
    // Désactiver le bouton pendant l'opération
    const btn = document.getElementById('dockerBatchImportBtn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Import en cours...';
    
    try {
        const result = await api.batchImportApps(apps);
        
        // Afficher les résultats dans le modal (pas de second modal)
        const resultsDiv = document.getElementById('dockerImportResults');
        const resultsList = document.getElementById('dockerImportResultsList');
        resultsDiv.classList.remove('d-none');
        
        resultsList.innerHTML = `
            <div class="alert ${result.errors > 0 ? 'alert-warning' : 'alert-success'} py-2 mb-2">
                <strong>${result.created}</strong> créée(s), <strong>${result.updated}</strong> mise(s) à jour${result.errors > 0 ? `, <strong>${result.errors}</strong> erreur(s)` : ''}
            </div>
            ${result.results.map(r => `
                <div class="d-flex align-items-center py-1 border-bottom">
                    <i class="bi ${r.success ? (r.is_new ? 'bi-check-circle-fill text-success' : 'bi-arrow-clockwise text-info') : 'bi-x-circle-fill text-danger'} me-2"></i>
                    <div class="flex-grow-1">
                        <strong>${escapeHtml(r.name)}</strong> <code class="small">${escapeHtml(r.slug)}</code>
                        <br><small class="text-muted">${escapeHtml(r.message)}</small>
                    </div>
                    ${r.credentials?.client_secret ? `
                        <div class="text-end">
                            <button class="btn btn-sm btn-outline-dark" data-action="copyToClipboard" data-arg1="${escapeHtml(r.credentials.client_id)}&#10;${escapeHtml(r.credentials.client_secret)}" title="Copier credentials">
                                <i class="bi bi-clipboard me-1"></i>Credentials
                            </button>
                        </div>
                    ` : ''}
                </div>
            `).join('')}
        `;
        
        // Reset sélection
        dockerGroupSelection = {};
        updateDockerSelectionSummary();
        
        // Rafraîchir les données admin et le scan
        await loadAdminData();
        loadApps();
        
        // Relancer le scan pour actualiser la liste
        await performDockerScan();
        
        showToast('Import terminé', `${result.created} application(s) importée(s)`, 'success');
        
    } catch (error) {
        console.error('Erreur import lot:', error);
        showToast('Erreur', error.message, 'danger');
    } finally {
        btn.innerHTML = originalHtml;
        btn.disabled = Object.keys(dockerGroupSelection).length === 0;
    }
}

/**
 * Copie du texte dans le presse-papier
 */
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Copié', 'Credentials copiés dans le presse-papier', 'success');
    }).catch(() => {
        // Fallback
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Copié', 'Credentials copiés', 'success');
    });
}
