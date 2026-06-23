/**
 * Hub Application - Snapshots complets
 * Page admin de gestion des snapshots systeme.
 */

let snapshotConfigModal;

const SNAPSHOT_STEP_LABELS = {
    queued: 'En attente...',
    pg_dump: 'Dump PostgreSQL',
    files_tar: 'Archivage fichiers',
    app_backups: 'Backups applicatifs',
    docker_config: 'Config Docker',
    assembling: 'Assemblage archive',
    encrypt: 'Chiffrement',
    upload: 'Upload S3',
    done: 'Termine',
};

async function loadSnapshots() {
    try {
        const data = await api.request('/admin/snapshots/');
        renderSnapshotTable(data.snapshots || []);
    } catch (e) {
        console.error('Erreur chargement snapshots', e);
    }
}

function renderSnapshotTable(snapshots) {
    const container = document.getElementById('snapshotsTableBody');
    if (!container) return;

    if (!snapshots.length) {
        container.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aucun snapshot</td></tr>';
        return;
    }

    container.innerHTML = snapshots.map(s => {
        const badgeClass = s.status === 'success' ? 'bg-success' :
            s.status === 'running' ? 'bg-warning text-dark' :
            s.status === 'failed' ? 'bg-danger' : 'bg-secondary';
        const statusText = s.status === 'running' ?
            `${SNAPSHOT_STEP_LABELS[s.progress_step] || s.progress_step} (${s.progress_percent}%)` :
            s.status;

        return `<tr>
            <td><code>${escapeHtml(s.snapshot_id).substring(0, 8)}...</code></td>
            <td><span class="badge ${badgeClass}">${escapeHtml(statusText)}</span></td>
            <td>${escapeHtml(s.total_size_human || '-')}</td>
            <td>${escapeHtml(formatDate(s.created_at))}</td>
            <td>
                <div class="dropdown">
                    <button class="btn btn-sm btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown">...</button>
                    <ul class="dropdown-menu">
                        <li><a class="dropdown-item" href="#" onclick="showSnapshotDetail('${escapeHtml(s.snapshot_id)}')">Details</a></li>
                        ${s.status === 'success' ? `<li><a class="dropdown-item" href="#" onclick="downloadSnapshot('${escapeHtml(s.snapshot_id)}')">Telecharger</a></li>` : ''}
                        ${s.status === 'success' ? `<li><a class="dropdown-item text-danger" href="#" onclick="confirmRestoreSnapshot('${escapeHtml(s.snapshot_id)}')">Restaurer</a></li>` : ''}
                        <li><hr class="dropdown-divider"></li>
                        ${s.status !== 'running' ? `<li><a class="dropdown-item text-danger" href="#" onclick="deleteSnapshot('${escapeHtml(s.snapshot_id)}')">Supprimer</a></li>` : ''}
                    </ul>
                </div>
            </td>
        </tr>`;
    }).join('');
}

async function showSnapshotDetail(snapshotId) {
    try {
        const data = await api.request(`/admin/snapshots/${snapshotId}`);
        const preview = await api.request(`/admin/snapshots/${snapshotId}/restore/preview`, { method: 'POST' });

        const modalId = 'snapshotDetailModal';
        let modalEl = document.getElementById(modalId);
        if (!modalEl) {
            modalEl = document.createElement('div');
            modalEl.id = modalId;
            modalEl.className = 'modal fade';
            modalEl.innerHTML = `<div class="modal-dialog modal-lg"><div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">Snapshot</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                <div id="${modalId}Body" class="modal-body"></div>
                <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button></div>
            </div></div>`;
            document.body.appendChild(modalEl);
        }

        const body = document.getElementById(`${modalId}Body`);
        body.innerHTML = `
            <p><strong>ID:</strong> <code>${escapeHtml(data.snapshot_id)}</code></p>
            <p><strong>Statut:</strong> ${escapeHtml(data.status)}</p>
            <p><strong>Taille:</strong> ${escapeHtml(data.total_size_human)}</p>
            <p><strong>Duree:</strong> ${escapeHtml(data.duration_human || '-')}</p>
            <p><strong>Cree par:</strong> ${escapeHtml(data.created_by || '-')}</p>
            <p><strong>Cree le:</strong> ${escapeHtml(formatDate(data.created_at))}</p>
            ${data.error_message ? `<p class="text-danger"><strong>Erreur:</strong> ${escapeHtml(data.error_message)}</p>` : ''}

            <h6 class="mt-3">Apps (${escapeHtml(String(data.apps_succeeded))}/${escapeHtml(String(data.apps_count))})</h6>
            <ul class="list-group mb-3">
                ${(preview.apps || []).map(a => `
                    <li class="list-group-item d-flex justify-content-between">
                        <span>${escapeHtml(a.slug)}</span>
                        <span class="badge ${a.status === 'success' ? 'bg-success' : 'bg-danger'}">${escapeHtml(a.status)}</span>
                    </li>
                `).join('')}
            </ul>
        `;

        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    } catch (e) {
        console.error('Erreur detail snapshot', e);
        snapshotToast('Erreur', 'Impossible de charger les details du snapshot', 'danger');
    }
}

async function createSnapshot() {
    try {
        const data = await api.request('/admin/snapshots/', { method: 'POST' });
        snapshotToast('Snapshot', 'Snapshot declenche !', 'success');
        loadSnapshots();
        startProgressPolling();
    } catch (e) {
        snapshotToast('Erreur', e.message || 'Echec creation snapshot', 'danger');
    }
}

async function deleteSnapshot(snapshotId) {
    if (!confirm('Supprimer ce snapshot definitivement ?')) return;
    try {
        await api.request(`/admin/snapshots/${snapshotId}`, { method: 'DELETE' });
        snapshotToast('Supprime', 'Snapshot supprime', 'success');
        loadSnapshots();
    } catch (e) {
        snapshotToast('Erreur', e.message || 'Echec suppression', 'danger');
    }
}

function confirmRestoreSnapshot(snapshotId) {
    if (!confirm('ATTENTION: Ceci va ecraser TOUTES les donnees actuelles du Hub ! Confirmer ?')) return;
    if (!confirm('Dernier avertissement: la restauration est irreversible. Continuer ?')) return;

    api.request(`/admin/snapshots/${snapshotId}/restore`, {
        method: 'POST',
        body: JSON.stringify({ confirm_full_restore: true }),
    }).then(() => {
        snapshotToast('Restauration', 'Restauration en cours...', 'info');
    }).catch(e => {
        snapshotToast('Erreur', e.message || 'Echec restauration', 'danger');
    });
}

function downloadSnapshot(snapshotId) {
    const token = localStorage.getItem('hub_token');
    const a = document.createElement('a');
    a.href = `/api/admin/snapshots/${snapshotId}/download`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function loadSnapshotConfig() {
    try {
        const config = await api.request('/admin/snapshots/config');
        const el = document.getElementById('snapshotCron');
        if (el) el.value = config.snapshot_cron || '';
        const el2 = document.getElementById('snapshotRetentionMax');
        if (el2) el2.value = config.snapshot_retention_max;
        const el3 = document.getElementById('snapshotRetentionMinAge');
        if (el3) el3.value = config.snapshot_retention_min_age_hours;
        const s3Status = document.getElementById('snapshotS3Status');
        if (s3Status) {
            s3Status.textContent = config.s3_configured ? 'Configure' : 'Non configure';
            s3Status.className = config.s3_configured ? 'badge bg-success' : 'badge bg-warning text-dark';
        }
    } catch (e) {
        console.error('Erreur chargement config snapshot', e);
    }
}

async function saveSnapshotConfig() {
    const cron = document.getElementById('snapshotCron')?.value;
    const max = document.getElementById('snapshotRetentionMax')?.value;
    const minAge = document.getElementById('snapshotRetentionMinAge')?.value;

    const payload = {};
    if (cron !== undefined) payload.snapshot_cron = cron;
    if (max) payload.snapshot_retention_max = parseInt(max);
    if (minAge !== undefined) payload.snapshot_retention_min_age_hours = parseInt(minAge);

    try {
        await api.request('/admin/snapshots/config', {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        snapshotToast('Config', 'Configuration enregistree', 'success');
    } catch (e) {
        snapshotToast('Erreur', e.message || 'Echec enregistrement', 'danger');
    }
}

let _progressPollTimer = null;

function startProgressPolling() {
    if (_progressPollTimer) return;
    _progressPollTimer = setInterval(async () => {
        try {
            const data = await api.request('/admin/snapshots/in-progress');
            const container = document.getElementById('snapshotProgressContainer');
            const bar = document.getElementById('snapshotProgressBar');
            const label = document.getElementById('snapshotProgressLabel');
            if (data.status === 'idle') {
                if (container) container.classList.add('d-none');
                if (bar) bar.style.width = '0%';
                if (label) label.textContent = '';
                clearInterval(_progressPollTimer);
                _progressPollTimer = null;
                if (typeof loadSnapshots === 'function') loadSnapshots();
            } else {
                if (container) container.classList.remove('d-none');
                if (bar) bar.style.width = `${data.progress_percent}%`;
                if (label) label.textContent = `${SNAPSHOT_STEP_LABELS[data.progress_step] || data.progress_step} (${data.progress_percent}%)`;
            }
        } catch (e) {}
    }, 2000);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    try {
        return new Date(dateStr).toLocaleString('fr-FR');
    } catch (e) {
        return dateStr;
    }
}

function snapshotToast(title, msg, type) {
    if (typeof window.showToast === 'function') {
        window.showToast(title, msg, type);
        return;
    }
    const container = document.getElementById('toastContainer');
    if (!container) {
        console.warn(`[${type}] ${title}: ${msg}`);
        return;
    }
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-bg-${type} border-0`;
    toast.innerHTML = `<div class="d-flex"><div class="toast-body"><strong>${title}:</strong> ${msg}</div><button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
    container.appendChild(toast);
    new bootstrap.Toast(toast).show();
}

// Ne pas utiliser DOMContentLoaded — le chargement est gere par showPage()
// Les fonctions loadSnapshots(), loadSnapshotConfig(), startProgressPolling()
// sont globales et appelees par auth-navigation.js au moment du showPage('snapshots')
