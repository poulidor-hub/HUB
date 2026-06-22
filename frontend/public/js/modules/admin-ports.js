/**
 * Hub Application - Pool d'allocation des ports (admin)
 *
 * Affiche l'état du pool de ports : plages, compteurs libres,
 * ports occupés par Docker sur l'hôte (exclus de l'attribution)
 * et la liste des allocations.
 *
 * Les fonctions restent globales pour conserver la compatibilité avec
 * les handlers HTML inline (cf. autres modules admin-*).
 */

// ===== Pool de ports =====

let portsPoolModal;

/**
 * Affiche la modal du pool de ports et charge l'état
 */
async function showPortsPoolModal() {
    if (!portsPoolModal) {
        portsPoolModal = new bootstrap.Modal(document.getElementById('portsPoolModal'));
    }
    portsPoolModal.show();
    await loadPortsPool();
}

/**
 * Rafraîchit l'état du pool de ports
 */
function refreshPortsPool() {
    loadPortsPool();
}

/**
 * Charge l'état du pool depuis l'API et le rend
 */
async function loadPortsPool() {
    const statusEl = document.getElementById('portsPoolStatus');
    const errorEl = document.getElementById('portsPoolError');
    const contentEl = document.getElementById('portsPoolContent');

    statusEl.classList.remove('d-none');
    errorEl.classList.add('d-none');
    contentEl.classList.add('d-none');

    try {
        const data = await api.getPortsStatus();
        statusEl.classList.add('d-none');
        contentEl.classList.remove('d-none');
        renderPortsPool(data);
    } catch (error) {
        console.error('Erreur chargement pool de ports:', error);
        statusEl.classList.add('d-none');
        errorEl.classList.remove('d-none');
        document.getElementById('portsPoolErrorMessage').textContent = error.message;
    }
}

/**
 * Rend l'état du pool de ports
 */
function renderPortsPool(data) {
    const apiRange = data.api_range || [];
    const feRange = data.frontend_range || [];
    const occupied = data.host_occupied_ports || [];
    const allocations = data.allocations || [];

    // Cartes plages / compteurs
    document.getElementById('portsApiRange').textContent =
        apiRange.length === 2 ? `${apiRange[0]} – ${apiRange[1]}` : '—';
    document.getElementById('portsApiFree').textContent = data.free_api_count ?? '—';
    document.getElementById('portsFeRange').textContent =
        feRange.length === 2 ? `${feRange[0]} – ${feRange[1]}` : '—';
    document.getElementById('portsFeFree').textContent = data.free_frontend_count ?? '—';

    // Ports occupés par Docker (exclus de l'attribution)
    const occEl = document.getElementById('portsOccupied');
    if (occupied.length > 0) {
        occEl.innerHTML = occupied
            .map(p => `<span class="badge bg-warning text-dark me-1 mb-1">${escapeHtml(String(p))}</span>`)
            .join('');
    } else {
        occEl.innerHTML = '<span class="text-muted small">Aucun port Docker occupé dans les plages</span>';
    }

    // Liste des allocations
    const tbody = document.getElementById('portsAllocationsBody');
    document.getElementById('portsAllocationsCount').textContent = `${allocations.length} allocation(s)`;

    if (allocations.length === 0) {
        tbody.innerHTML = `
            <tr><td colspan="6" class="text-center text-muted py-3">Aucune allocation</td></tr>
        `;
        return;
    }

    tbody.innerHTML = allocations.map(a => `
        <tr>
            <td><code>${escapeHtml(a.app_slug)}</code></td>
            <td><small class="text-muted">${escapeHtml(a.host || '—')}</small></td>
            <td><span class="badge bg-danger">${escapeHtml(String(a.api_port))}</span></td>
            <td><span class="badge bg-success">${escapeHtml(String(a.frontend_port))}</span></td>
            <td><span class="badge bg-secondary">${escapeHtml(String(a.frontend_https_port))}</span></td>
            <td class="text-end">
                <button class="btn btn-sm btn-outline-danger" title="Libérer (supprimer l'allocation)"
                    onclick="removePortAllocation(${a.id}, '${escapeHtml(a.app_slug)}')">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>
    `).join('');
}

/**
 * Supprime une allocation orpheline et rend les ports au pool
 */
async function removePortAllocation(allocId, slug) {
    if (!confirm(`Libérer les ports de « ${slug} » ? L'allocation sera supprimée du pool.`)) {
        return;
    }
    try {
        await api.deletePortAllocation(allocId);
        showToast('Ports libérés', `Allocation de « ${slug} » supprimée`, 'success');
        await loadPortsPool();
    } catch (error) {
        console.error('Erreur suppression allocation:', error);
        showToast('Erreur', error.message, 'danger');
    }
}
