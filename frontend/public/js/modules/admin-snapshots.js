/**
 * Hub Application - Snapshots complets
 * Page admin de gestion des snapshots systeme.
 */

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

function formatBytes(bytes) {
    if (bytes == null || bytes === 0) return '-';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) {
        size /= 1024;
        i++;
    }
    return `${size.toFixed(1)} ${units[i]}`;
}

function formatRelativeTime(dateStr) {
    if (!dateStr) return '-';
    try {
        const now = new Date();
        const date = new Date(dateStr);
        const diffSec = Math.floor((now - date) / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffH = Math.floor(diffMin / 60);
        const diffD = Math.floor(diffH / 24);

        if (diffSec < 60) return "A l'instant";
        if (diffMin < 60) return `Il y a ${diffMin} min`;
        if (diffH < 24) return `Il y a ${diffH} h`;
        if (diffD < 7) return `Il y a ${diffD} j`;
        return date.toLocaleDateString('fr-FR');
    } catch (e) {
        return dateStr;
    }
}

function toggleSnapshotEmpty(visible) {
    const empty = document.getElementById('snapshotEmptyState');
    const table = document.getElementById('snapshotTableCard');
    if (empty) empty.classList.toggle('d-none', !visible);
    if (table) table.classList.toggle('d-none', visible);
}

async function loadSnapshots() {
    try {
        const data = await api.request('/admin/snapshots/');
        renderSnapshotTable(data.snapshots || []);
    } catch (e) {
        console.error('Erreur chargement snapshots', e);
        toggleSnapshotEmpty(true);
        const countLabel = document.getElementById('snapshotCountLabel');
        if (countLabel) countLabel.textContent = 'Erreur de chargement';
    }
}

function renderSnapshotTable(snapshots) {
    const container = document.getElementById('snapshotsTableBody');
    const countLabel = document.getElementById('snapshotCountLabel');
    if (!container) return;

    if (!snapshots.length) {
        toggleSnapshotEmpty(true);
        if (countLabel) countLabel.textContent = 'Aucun snapshot';
        return;
    }

    toggleSnapshotEmpty(false);
    if (countLabel) {
        countLabel.textContent = `${snapshots.length} snapshot(s)`;
    }

    container.innerHTML = snapshots.map(s => {
        let statusIcon, badgeClass;
        if (s.status === 'success') {
            statusIcon = 'bi-check-circle-fill';
            badgeClass = 'bg-success';
        } else if (s.status === 'running') {
            statusIcon = 'bi-arrow-repeat';
            badgeClass = 'bg-warning text-dark';
        } else if (s.status === 'failed') {
            statusIcon = 'bi-x-circle-fill';
            badgeClass = 'bg-danger';
        } else {
            statusIcon = 'bi-clock';
            badgeClass = 'bg-secondary';
        }

        const statusAnim = s.status === 'running' ? 'snapshot-spin' : '';
        const statusText = s.status === 'running'
            ? `${SNAPSHOT_STEP_LABELS[s.progress_step] || s.progress_step} (${s.progress_percent}%)`
            : s.status;

        return `<tr>
            <td>
                <span class="badge ${badgeClass}">
                    <i class="bi ${statusIcon} ${statusAnim} me-1"></i>${escapeHtml(statusText)}
                </span>
            </td>
            <td><code class="text-muted">${escapeHtml(s.snapshot_id).substring(0, 12)}...</code></td>
            <td>${escapeHtml(s.total_size_human || formatBytes(s.total_size_bytes))}</td>
            <td>
                <span title="${escapeHtml(formatDate(s.created_at))}">${escapeHtml(formatRelativeTime(s.created_at))}</span>
            </td>
            <td class="text-end">
                <div class="dropdown">
                    <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="dropdown" title="Actions">
                        <i class="bi bi-three-dots"></i>
                    </button>
                    <ul class="dropdown-menu dropdown-menu-end">
                        <li><a class="dropdown-item" href="#" onclick="showSnapshotDetail('${escapeHtml(s.snapshot_id)}')"><i class="bi bi-eye me-2"></i>Details</a></li>
                        ${s.status === 'success' ? `<li><a class="dropdown-item" href="#" onclick="downloadSnapshot('${escapeHtml(s.snapshot_id)}')"><i class="bi bi-download me-2"></i>Telecharger</a></li>` : ''}
                        ${s.status === 'success' ? `<li><a class="dropdown-item text-warning" href="#" onclick="showSnapshotDetail('${escapeHtml(s.snapshot_id)}', 'restore')"><i class="bi bi-arrow-counterclockwise me-2"></i>Restaurer</a></li>` : ''}
                        <li><hr class="dropdown-divider"></li>
                        ${s.status !== 'running' ? `<li><a class="dropdown-item text-danger" href="#" onclick="deleteSnapshot('${escapeHtml(s.snapshot_id)}')"><i class="bi bi-trash me-2"></i>Supprimer</a></li>` : ''}
                    </ul>
                </div>
            </td>
        </tr>`;
    }).join('');
}

async function showSnapshotDetail(snapshotId, openTab) {
    try {
        const data = await api.request(`/admin/snapshots/${snapshotId}`);
        const preview = await api.request(`/admin/snapshots/${snapshotId}/restore/preview`, { method: 'POST' });

        const modalId = 'snapshotDetailModal';
        let modalEl = document.getElementById(modalId);
        if (!modalEl) {
            modalEl = document.createElement('div');
            modalEl.id = modalId;
            modalEl.className = 'modal fade';
            modalEl.setAttribute('tabindex', '-1');
            modalEl.innerHTML = `<div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title"><i class="bi bi-camera me-2"></i>Snapshot</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body p-0">
                        <ul class="nav nav-tabs px-3 pt-3" id="${modalId}Tabs" role="tablist">
                            <li class="nav-item" role="presentation">
                                <button class="nav-link active" id="${modalId}TabInfos" data-bs-toggle="tab" data-bs-target="#${modalId}PaneInfos" type="button">Infos</button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="${modalId}TabContenu" data-bs-toggle="tab" data-bs-target="#${modalId}PaneContenu" type="button">Contenu</button>
                            </li>
                            <li class="nav-item" role="presentation">
                                <button class="nav-link" id="${modalId}TabRestore" data-bs-toggle="tab" data-bs-target="#${modalId}PaneRestore" type="button">Restaurer</button>
                            </li>
                        </ul>
                        <div class="tab-content p-3" id="${modalId}TabsContent">
                            <div class="tab-pane fade show active" id="${modalId}PaneInfos"></div>
                            <div class="tab-pane fade" id="${modalId}PaneContenu"></div>
                            <div class="tab-pane fade" id="${modalId}PaneRestore"></div>
                        </div>
                    </div>
                    <div class="modal-footer">
                        ${data.status === 'success' ? `<button class="btn btn-outline-primary" onclick="downloadSnapshot('${escapeHtml(data.snapshot_id)}')"><i class="bi bi-download me-1"></i>Telecharger</button>` : ''}
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                    </div>
                </div>
            </div>`;
            document.body.appendChild(modalEl);
        }

        const statusBadge = data.status === 'success' ? '<span class="badge bg-success"><i class="bi bi-check-circle-fill me-1"></i>Succes</span>' :
            data.status === 'running' ? '<span class="badge bg-warning text-dark"><i class="bi bi-arrow-repeat me-1"></i>En cours</span>' :
            data.status === 'failed' ? '<span class="badge bg-danger"><i class="bi bi-x-circle-fill me-1"></i>Echec</span>' :
            `<span class="badge bg-secondary"><i class="bi bi-clock me-1"></i>${escapeHtml(data.status)}</span>`;

        const manifest = data.manifest || {};
        const collectors = manifest.collectors || {};
        const contentTypeIcons = {
            pg_dump: '<i class="bi bi-database me-2"></i>',
            files: '<i class="bi bi-folder me-2"></i>',
            app_backups: '<i class="bi bi-box-seam me-2"></i>',
            docker_config: '<i class="bi bi-gear me-2"></i>',
        };
        const contentTypeLabels = {
            pg_dump: 'Base PostgreSQL',
            files: 'Fichiers uploads',
            app_backups: 'Backups applicatifs',
            docker_config: 'Config Docker',
        };
        const contentRows = Object.keys(collectors).map(k => {
            const icon = contentTypeIcons[k] || '<i class="bi bi-file-earmark me-2"></i>';
            const label = contentTypeLabels[k] || k;
            const size = collectors[k] && collectors[k].size_bytes
                ? formatBytes(collectors[k].size_bytes)
                : '-';
            return `<li class="list-group-item d-flex justify-content-between align-items-center">
                <span>${icon}${escapeHtml(label)}</span>
                <span class="text-muted">${escapeHtml(size)}</span>
            </li>`;
        }).join('');

        document.getElementById(`${modalId}PaneInfos`).innerHTML = `
            <div class="row g-3">
                <div class="col-sm-6"><strong>ID</strong><br><code class="text-muted">${escapeHtml(data.snapshot_id)}</code></div>
                <div class="col-sm-6"><strong>Statut</strong><br>${statusBadge}</div>
                <div class="col-sm-6"><strong>Taille</strong><br>${escapeHtml(data.total_size_human || '-')}</div>
                <div class="col-sm-6"><strong>Duree</strong><br>${escapeHtml(data.duration_human || '-')}</div>
                <div class="col-sm-6"><strong>Cree le</strong><br>${escapeHtml(formatDate(data.created_at))}</div>
                <div class="col-sm-6"><strong>Cree par</strong><br>${escapeHtml(data.created_by || '-')}</div>
                ${data.checksum_sha256 ? `<div class="col-12"><strong>SHA256</strong><br><code class="text-muted" style="word-break:break-all">${escapeHtml(data.checksum_sha256)}</code></div>` : ''}
                ${data.s3_key ? `<div class="col-12"><strong>S3</strong><br><code class="text-muted">${escapeHtml(data.s3_bucket || '')}/${escapeHtml(data.s3_key)}</code></div>` : ''}
                ${data.error_message ? `<div class="col-12"><div class="alert alert-danger py-2 mb-0"><strong>Erreur :</strong> ${escapeHtml(data.error_message)}</div></div>` : ''}
            </div>`;

        document.getElementById(`${modalId}PaneContenu`).innerHTML = `
            <h6 class="mb-2">Collecteurs</h6>
            <ul class="list-group mb-3">${contentRows || '<li class="list-group-item text-muted">Aucun collecteur</li>'}</ul>
            <h6 class="mb-2">Apps <span class="text-muted">(${data.apps_succeeded || 0}/${data.apps_count || 0})</span></h6>
            <ul class="list-group">
                ${(preview.apps || []).map(a => `
                    <li class="list-group-item d-flex justify-content-between align-items-center">
                        <span>${escapeHtml(a.slug)}</span>
                        <span class="badge ${a.status === 'success' ? 'bg-success' : a.status === 'failed' ? 'bg-danger' : 'bg-secondary'}">${escapeHtml(a.status)}</span>
                    </li>
                `).join('') || '<li class="list-group-item text-muted">Aucune app</li>'}
            </ul>`;

        document.getElementById(`${modalId}PaneRestore`).innerHTML = `
            <div class="alert alert-warning">
                <i class="bi bi-exclamation-triangle me-2"></i>
                Cette action va ecraser <strong>toutes les donnees actuelles</strong> du Hub (base de donnees, secrets, fichiers).
            </div>
            <p>Contenu du snapshot :</p>
            <ul class="list-group mb-3">${contentRows || '<li class="list-group-item text-muted">Aucun collecteur</li>'}</ul>
            ${data.status === 'success' ? `
                <div class="d-flex gap-2">
                    <button class="btn btn-outline-warning" onclick="confirmRestoreSnapshot('${escapeHtml(data.snapshot_id)}')">
                        <i class="bi bi-arrow-counterclockwise me-1"></i>Restaurer ce snapshot
                    </button>
                </div>
            ` : `<p class="text-muted">Seuls les snapshots reussis peuvent etre restaures.</p>`}`;

        const modal = new bootstrap.Modal(modalEl);
        modal.show();

        if (openTab === 'restore') {
            const restoreTab = document.getElementById(`${modalId}TabRestore`);
            if (restoreTab) {
                setTimeout(() => restoreTab.click(), 100);
            }
        }
    } catch (e) {
        console.error('Erreur detail snapshot', e);
        showToast('Erreur', 'Impossible de charger les details du snapshot', 'danger');
    }
}

async function createSnapshot() {
    try {
        const data = await api.request('/admin/snapshots/', { method: 'POST' });
        showToast('Snapshot', 'Snapshot declenche !', 'success');
        loadSnapshots();
        startProgressPolling();
    } catch (e) {
        showToast('Erreur', e.message || 'Echec creation snapshot', 'danger');
    }
}

async function deleteSnapshot(snapshotId) {
    if (!confirm('Supprimer ce snapshot definitivement ?')) return;
    try {
        await api.request(`/admin/snapshots/${snapshotId}`, { method: 'DELETE' });
        showToast('Supprime', 'Snapshot supprime', 'success');
        loadSnapshots();
    } catch (e) {
        showToast('Erreur', e.message || 'Echec suppression', 'danger');
    }
}

function confirmRestoreSnapshot(snapshotId) {
    const existingModal = document.getElementById('snapshotRestoreConfirmModal');
    if (existingModal) existingModal.remove();

    const modalEl = document.createElement('div');
    modalEl.id = 'snapshotRestoreConfirmModal';
    modalEl.className = 'modal fade';
    modalEl.setAttribute('tabindex', '-1');
    modalEl.innerHTML = `<div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header bg-warning text-dark">
                <h5 class="modal-title"><i class="bi bi-exclamation-triangle me-2"></i>Confirmation restauration</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <p class="fw-bold">ATTENTION : Ceci va ecraser TOUTES les donnees actuelles du Hub !</p>
                <p>La restauration est irreversible. Toutes les donnees seront remplacees par le contenu de ce snapshot.</p>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
                <button type="button" class="btn btn-warning" id="snapshotRestoreConfirmBtn">
                    <i class="bi bi-arrow-counterclockwise me-1"></i>Restaurer
                </button>
            </div>
        </div>
    </div>`;
    document.body.appendChild(modalEl);

    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    document.getElementById('snapshotRestoreConfirmBtn').addEventListener('click', async () => {
        modal.hide();
        try {
            await api.request(`/admin/snapshots/${snapshotId}/restore`, {
                method: 'POST',
                body: JSON.stringify({ confirm_full_restore: true }),
            });
            showToast('Restauration', 'Restauration en cours...', 'info');
        } catch (e) {
            showToast('Erreur', e.message || 'Echec restauration', 'danger');
        }
    });

    modalEl.addEventListener('hidden.bs.modal', () => modalEl.remove());
}

async function downloadSnapshot(snapshotId) {
    showToast('Telechargement', 'Telechargement en cours...', 'info');
    try {
        const response = await fetch(`/api/admin/snapshots/${snapshotId}/download`, {
            headers: {
                'Authorization': `Bearer ${api.token}`
            }
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: 'Erreur serveur' }));
            throw new Error(err.detail || 'Erreur telechargement');
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `snapshot_${snapshotId}.tar.zst.enc`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('Telechargement', 'Telechargement termine', 'success');
    } catch (e) {
        showToast('Erreur', e.message || 'Echec du telechargement', 'danger');
    }
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
            if (config.s3_configured) {
                s3Status.innerHTML = '<i class="bi bi-check-circle-fill me-1"></i>Configure';
                s3Status.className = 'badge bg-success';
            } else {
                s3Status.innerHTML = '<i class="bi bi-x-circle me-1"></i>Non configure';
                s3Status.className = 'badge bg-warning text-dark';
            }
        }

        const cronHelp = document.getElementById('snapshotCronHelp');
        if (cronHelp) {
            if (config.snapshot_cron) {
                cronHelp.textContent = `Planification : ${config.snapshot_cron}`;
            } else {
                cronHelp.textContent = 'Laisser vide pour desactiver la planification';
            }
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
        showToast('Config', 'Configuration enregistree', 'success');
        loadSnapshotConfig();
    } catch (e) {
        showToast('Erreur', e.message || 'Echec enregistrement', 'danger');
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


// Ne pas utiliser DOMContentLoaded — le chargement est gere par showPage()
// Les fonctions loadSnapshots(), loadSnapshotConfig(), startProgressPolling()
// sont globales et appelees par auth-navigation.js au moment du showPage('snapshots')
