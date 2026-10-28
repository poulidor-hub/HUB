/**
 * Hub Application - Coffre hubctl
 *
 * Workspace Admin -> Sauvegardes : état du coffre chiffré (hubctl) et
 * sauvegarde manuelle. Modèle : admin-snapshots.js (loader -> render ->
 * action, modales dynamiques, toasts).
 *
 * Ne pas utiliser DOMContentLoaded — le chargement est géré par
 * switchBackupWorkspace() (app-backups.js) via loadVaultStatus().
 * Payloads défensifs : service "unreachable" => seulement {service, hint}
 * garantis ; status "locked" => ni entries ni divergences ; last_backup
 * peut être null ; pra peut être {configured: false, location: null}.
 */

function vaultFormatBytes(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '—';
    const units = ['o', 'Ko', 'Mo', 'Go', 'To'];
    let size = n;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) {
        size /= 1024;
        i++;
    }
    return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function vaultFormatDate(dateStr) {
    if (!dateStr) return '—';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return '—';
        return `${d.toLocaleDateString('fr-FR')} ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
    } catch (e) {
        return '—';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Charge l'état du coffre puis rend le workspace.
 * catch (réseau/API) => alerte service + état vide + bouton désactivé.
 */
async function loadVaultStatus() {
    try {
        const data = await api.getVaultStatus();
        renderVaultStatus(data);
    } catch (e) {
        console.error('Erreur chargement statut coffre:', e);
        renderVaultStatus({ service: 'unreachable', hint: e?.message || 'vérifier hubctl-atelier.service (systemctl status hubctl-atelier)' });
    }
}

/**
 * Rendu pur du workspace Coffre — garde défensive sur chaque clé.
 * Trois états : unreachable (service) / locked (coffre) / ok (payload complet).
 */
function renderVaultStatus(data) {
    const serviceAlert = document.getElementById('vaultServiceAlert');
    const serviceHint = document.getElementById('vaultServiceHint');
    const lockedAlert = document.getElementById('vaultLockedAlert');
    const statusLine = document.getElementById('vaultStatusLine');
    const entriesEl = document.getElementById('vaultEntries');
    const divEl = document.getElementById('vaultDivergences');
    const btn = document.getElementById('vaultBackupBtn');
    if (!statusLine) return;

    // Réinitialisation des alertes
    if (serviceAlert) serviceAlert.classList.add('d-none');
    if (lockedAlert) lockedAlert.classList.add('d-none');

    // 1. Service hubctl injoignable (pas d'autres clés garanties)
    if (data?.service === 'unreachable') {
        if (serviceHint) serviceHint.textContent = data.hint || 'vérifier hubctl-atelier.service (systemctl status hubctl-atelier)';
        if (serviceAlert) serviceAlert.classList.remove('d-none');
        statusLine.innerHTML = '<span class="badge bg-secondary"><i class="bi bi-unplugged me-1"></i>Service injoignable</span>';
        if (entriesEl) entriesEl.innerHTML = '<span class="text-muted small">—</span>';
        if (divEl) divEl.innerHTML = '<span class="text-muted small">—</span>';
        if (btn) btn.disabled = true;
        return;
    }

    // 2. Coffre verrouillé (ni entries ni divergences dans le payload)
    if (data?.status === 'locked') {
        if (lockedAlert) lockedAlert.classList.remove('d-none');
        statusLine.innerHTML = '<span class="badge bg-warning text-dark"><i class="bi bi-lock-fill me-1"></i>Verrouillé</span>';
        if (entriesEl) entriesEl.innerHTML = '<span class="text-muted small">Indisponible (coffre verrouillé)</span>';
        if (divEl) divEl.innerHTML = '<span class="text-muted small">—</span>';
        if (btn) btn.disabled = true;
        return;
    }

    // 3. Coffre déverrouillé — payload hubctl complet (accès défensif)
    if (btn) btn.disabled = false;

    const lockBadge = data?.locked
        ? '<span class="badge bg-warning text-dark"><i class="bi bi-lock-fill me-1"></i>Verrouillé</span>'
        : '<span class="badge bg-success"><i class="bi bi-unlock-fill me-1"></i>Déverrouillé</span>';
    const lastBackupBadge = `<span class="badge bg-light text-dark border"><i class="bi bi-clock-history me-1"></i>Dernière sauvegarde : ${escapeHtml(vaultFormatDate(data?.last_backup))}</span>`;
    const sizeBadge = `<span class="badge bg-light text-dark border"><i class="bi bi-hdd me-1"></i>Taille : ${escapeHtml(vaultFormatBytes(data?.vault_size))}</span>`;
    const praBadge = data?.pra?.configured
        ? `<span class="badge bg-info text-dark"><i class="bi bi-cloud-arrow-up me-1"></i>PRA : ${escapeHtml(data.pra.location || 'configuré')}</span>`
        : '<span class="badge bg-secondary"><i class="bi bi-cloud-slash me-1"></i>PRA non configuré</span>';
    statusLine.innerHTML = [lockBadge, lastBackupBadge, sizeBadge, praBadge].join('');

    const entries = data?.entries || {};
    const entryDefs = [
        { key: 'env', label: '.env' },
        { key: 'files', label: 'Fichiers secrets' },
        { key: 'apps', label: 'Apps' },
        { key: 'db_dump', label: 'Dump DB' }
    ];
    if (entriesEl) {
        entriesEl.innerHTML = entryDefs.map(def => {
            const count = Number(entries?.[def.key]) || 0;
            const icon = count > 0
                ? '<i class="bi bi-check-circle-fill text-success me-1"></i>'
                : '<i class="bi bi-dash text-muted me-1"></i>';
            const countLabel = count > 0 ? ` <span class="fw-bold">${count}</span>` : '';
            return `<span class="badge bg-light text-dark border me-1 mb-1">${icon}${def.label}${countLabel}</span>`;
        }).join('');
    }

    const divergences = Array.isArray(data?.divergences) ? data.divergences : [];
    if (divEl) {
        if (!divergences.length) {
            divEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle-fill me-1"></i>✓ Coffre à jour</span>';
        } else {
            divEl.innerHTML = divergences.map(slug => {
                const clean = String(slug || '').replace(/^app:/, '');
                return `<span class="badge bg-warning text-dark me-1 mb-1" role="button" title="Voir la santé de l'app" data-action="openVaultApp" data-arg1="${escapeHtml(clean)}">${escapeHtml(clean)}</span>`;
            }).join('');
        }
    }
}

/**
 * Confirmation avant sauvegarde coffre : copie disque -> coffre + réplication
 * PRA ; le disque n'est jamais modifié. Modèle : confirmRestoreSnapshot.
 */
function confirmVaultBackup() {
    const existingModal = document.getElementById('vaultBackupConfirmModal');
    if (existingModal) existingModal.remove();

    const modalEl = document.createElement('div');
    modalEl.id = 'vaultBackupConfirmModal';
    modalEl.className = 'modal fade';
    modalEl.setAttribute('tabindex', '-1');
    modalEl.innerHTML = `<div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title"><i class="bi bi-safe me-2"></i>Sauvegarder vers le coffre</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <p>La sauvegarde copie <code>.env</code>, secrets et dump DB vers le coffre chiffré, puis lance la réplication PRA.</p>
                <p class="text-muted small mb-0"><i class="bi bi-shield-check me-1"></i>Le disque n'est jamais modifié.</p>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
                <button type="button" class="btn btn-primary" id="vaultBackupConfirmBtn">
                    <i class="bi bi-shield-lock me-1"></i>Sauvegarder
                </button>
            </div>
        </div>
    </div>`;
    document.body.appendChild(modalEl);

    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    document.getElementById('vaultBackupConfirmBtn').addEventListener('click', async () => {
        modal.hide();
        await backupVaultNow();
    });

    modalEl.addEventListener('hidden.bs.modal', () => modalEl.remove());
}

/**
 * Exécute la sauvegarde coffre sous spinner statique.
 * Succès => toast success + refresh ; 409 "déjà en cours" => toast warning ;
 * autre erreur => toast danger. Spinner retiré dans finally.
 */
async function backupVaultNow() {
    const progressEl = document.createElement('div');
    progressEl.id = 'vaultBackupProgressModal';
    progressEl.className = 'modal fade';
    progressEl.setAttribute('tabindex', '-1');
    progressEl.setAttribute('data-bs-backdrop', 'static');
    progressEl.setAttribute('data-bs-keyboard', 'false');
    progressEl.innerHTML = `<div class="modal-dialog modal-dialog-centered modal-sm">
        <div class="modal-content text-center">
            <div class="modal-body py-4">
                <div class="spinner-border text-primary mb-3" role="status" style="width:3rem;height:3rem;">
                    <span class="visually-hidden">Sauvegarde...</span>
                </div>
                <h6>Sauvegarde du coffre en cours...</h6>
                <p class="text-muted small mb-0">Copie vers le coffre chiffré — le disque n'est pas modifié.</p>
            </div>
        </div>
    </div>`;
    document.body.appendChild(progressEl);
    const progressModal = new bootstrap.Modal(progressEl);
    progressModal.show();

    try {
        const result = await api.vaultBackup();
        if (result?.status === 'locked') {
            showToast('Coffre verrouillé', 'Déverrouillez le coffre (clé parc.vault.key ou CLI hubctl) avant de sauvegarder.', 'warning');
        } else if (result?.pra?.error) {
            showToast('Coffre', 'Sauvegarde réussie mais réplication PRA en échec', 'warning');
        } else {
            const envCount = Number(result?.env) || 0;
            const filesCount = Number(result?.files) || 0;
            showToast('Coffre sauvegardé', `Coffre sauvegardé (${envCount} .env, ${filesCount} fichiers)`, 'success');
        }
        loadVaultStatus();
    } catch (e) {
        if (e?.message && e.message.includes('déjà en cours')) {
            showToast('Sauvegarde en cours', 'Une sauvegarde du coffre est déjà en cours.', 'warning');
        } else {
            showToast('Erreur', e?.message || 'Échec de la sauvegarde du coffre', 'danger');
        }
        loadVaultStatus();
    } finally {
        progressModal.hide();
        progressEl.addEventListener('hidden.bs.modal', () => progressEl.remove(), { once: true });
    }
}

/**
 * Clic sur un badge de divergence : ignore le slug, la vue Santé des apps
 * centralise l'état applicatif.
 */
function openVaultApp() {
    if (typeof showPage === 'function') showPage('appsHealth');
}
