/**
 * Hub Application - approbation applications
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Approbation des Applications =====

let pendingApps = [];
let rejectAppModal = null;

/**
 * Charge et affiche le nombre d'applications en attente (badge)
 */
async function loadPendingAppsCount() {
    try {
        const data = await api.getPendingApps();
        const badge = document.getElementById('pendingAppsBadge');
        if (data.total > 0) {
            badge.textContent = data.total;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
    } catch (error) {
        console.error('Erreur chargement compteur apps en attente:', error);
    }
}

/**
 * Charge et affiche la liste des applications en attente d'approbation
 */
async function loadPendingApps() {
    const table = document.getElementById('pendingAppsTable');
    const alert = document.getElementById('pendingAppsAlert');
    
    table.innerHTML = '<div class="text-center p-4"><span class="spinner-border spinner-border-sm"></span> Chargement...</div>';
    alert.classList.add('d-none');
    
    try {
        const data = await api.getPendingApps();
        pendingApps = data.pending_apps;
        
        // Mettre à jour le badge
        const badge = document.getElementById('pendingAppsBadge');
        if (data.total > 0) {
            badge.textContent = data.total;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
        
        if (pendingApps.length === 0) {
            table.innerHTML = '';
            alert.classList.remove('d-none');
            return;
        }
        
        table.innerHTML = `
            <table class="table table-hover mb-0">
                <thead class="table-light">
                    <tr>
                        <th>Application</th>
                        <th>API URL</th>
                        <th>Date</th>
                        <th class="text-end">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${pendingApps.map(app => `
                        <tr>
                            <td>
                                <div class="d-flex align-items-center">
                                    <i class="${escapeHtml(app.icon)} me-2" style="color: ${escapeHtml(app.color)}"></i>
                                    <div>
                                        <strong>${escapeHtml(app.name)}</strong>
                                        ${app.is_shared_service ? '<i class="bi bi-share text-info ms-1" title="Service partagé"></i>' : ''}
                                        <br><code class="small text-muted">${escapeHtml(app.slug)}</code>
                                    </div>
                                </div>
                            </td>
                            <td><small class="text-muted">${escapeHtml(app.api_url)}</small></td>
                            <td><small class="text-muted">${formatDate(app.created_at)}</small></td>
                            <td class="text-end">
                                <div class="btn-group btn-group-sm">
                                    <button class="btn btn-success" onclick="approveApp('${escapeHtml(app.slug)}')" title="Approuver">
                                        <i class="bi bi-check-lg"></i> Approuver
                                    </button>
                                    <button class="btn btn-outline-danger" onclick="showRejectAppModal('${escapeHtml(app.slug)}', '${escapeHtml(app.name)}')" title="Rejeter">
                                        <i class="bi bi-x-lg"></i>
                                    </button>
                                    <button class="btn btn-outline-secondary" onclick="deletePendingApp('${escapeHtml(app.slug)}')" title="Supprimer">
                                        <i class="bi bi-trash"></i>
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    } catch (error) {
        console.error('Erreur chargement apps en attente:', error);
        table.innerHTML = `<div class="alert alert-danger m-3"><i class="bi bi-exclamation-triangle me-2"></i>Erreur: ${error.message}</div>`;
    }
}

/**
 * Approuve une application
 */
async function approveApp(slug) {
    if (!confirm(`Approuver l'application "${slug}" ?\n\nL'application et son Service Account seront activés.`)) {
        return;
    }
    
    try {
        const result = await api.approveApp(slug);
        showToast('Application approuvée', result.message, 'success');
        await loadAdminData();    // Rafraîchir le tableau principal (inclut les pending)
        await loadApps();         // Rafraîchir le dashboard
    } catch (error) {
        console.error('Erreur approbation:', error);
        showToast('Erreur', `Impossible d'approuver: ${error.message}`, 'danger');
    }
}

/**
 * Affiche le modal de rejet avec champ raison
 */
function showRejectAppModal(slug, name) {
    document.getElementById('rejectAppSlug').value = slug;
    document.getElementById('rejectAppName').textContent = name;
    document.getElementById('rejectAppReason').value = '';
    
    if (!rejectAppModal) {
        rejectAppModal = new bootstrap.Modal(document.getElementById('rejectAppModal'));
    }
    rejectAppModal.show();
}

/**
 * Confirme le rejet d'une application
 */
async function confirmRejectApp() {
    const slug = document.getElementById('rejectAppSlug').value;
    const reason = document.getElementById('rejectAppReason').value.trim();
    
    if (!reason) {
        showToast('Erreur', 'Veuillez saisir une raison de rejet', 'warning');
        return;
    }
    
    try {
        const result = await api.rejectApp(slug, reason);
        rejectAppModal.hide();
        showToast('Application rejetée', result.message, 'warning');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur rejet:', error);
        showToast('Erreur', `Impossible de rejeter: ${error.message}`, 'danger');
    }
}

/**
 * Supprime une application en attente ou rejetée
 */
async function deletePendingApp(slug) {
    if (!confirm(`Supprimer définitivement l'application "${slug}" ?\n\nCette action est irréversible.`)) {
        return;
    }
    
    try {
        await api.deletePendingApp(slug);
        showToast('Application supprimée', `L'application "${slug}" a été supprimée`, 'success');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur suppression:', error);
        showToast('Erreur', `Impossible de supprimer: ${error.message}`, 'danger');
    }
}
