/**
 * Hub Application - sauvegardes applications
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// =============================================================================
// APP BACKUPS - SAUVEGARDES CENTRALISÉES DES APPLICATIONS
// =============================================================================

let appBackupConfigModal = null;
let appBackupsListModal = null;
let backupClusterModal = null;
let appBackupsById = {};
let selectedRestoreBackup = null;
let backupOverviewApps = [];
let backupClusters = [];



// Map<app_slug, {backup_id, app_name, status, progress_step, progress_percent, started_at, avg_duration_seconds}>
const runningBackups = new Map();
let _runningBackupsTimer = null;

// Map<backup_id, finished_at_ms> : backups dont l'événement SSE terminal
// (backup_completed / backup_failed) a déjà été traité. Sert à neutraliser la
// race où l'insertion optimiste de doTriggerBackup s'exécute APRÈS l'arrivée du
// SSE terminal (cas du backup Hub sur lui-même, quasi instantané), ce qui
// ré-injectait un backup déjà terminé et bloquait l'indicateur "En cours…".
const recentlyFinishedBackups = new Map();
const RECENTLY_FINISHED_TTL_MS = 60 * 1000;

function markBackupFinished(backupId) {
    if (!backupId) return;
    const now = Date.now();
    recentlyFinishedBackups.set(backupId, now);
    // Purge des entrées trop anciennes pour éviter une croissance illimitée.
    for (const [id, ts] of recentlyFinishedBackups.entries()) {
        if (now - ts > RECENTLY_FINISHED_TTL_MS) {
            recentlyFinishedBackups.delete(id);
        }
    }
}

function wasBackupRecentlyFinished(backupId) {
    if (!backupId || !recentlyFinishedBackups.has(backupId)) return false;
    const ts = recentlyFinishedBackups.get(backupId);
    if (Date.now() - ts > RECENTLY_FINISHED_TTL_MS) {
        recentlyFinishedBackups.delete(backupId);
        return false;
    }
    return true;
}

const BACKUP_STEP_LABELS = {
    queued: 'En attente…',
    create: 'Création sur l\'app…',
    downloading: 'Téléchargement…',
    download_complete: 'Téléchargement terminé',
    storing: 'Enregistrement…',
    done: 'Terminé'
};

function switchBackupWorkspace(tab) {
    const tabs = ['apps', 'snapshots', 'env', 'vault'];
    tabs.forEach(name => {
        document.getElementById(`backupWorkspace${capitalizeBackupTab(name)}Tab`)?.classList.toggle('btn-primary', name === tab);
        document.getElementById(`backupWorkspace${capitalizeBackupTab(name)}Tab`)?.classList.toggle('btn-outline-primary', name !== tab);
        document.getElementById(`backupWorkspace${capitalizeBackupTab(name)}`)?.classList.toggle('d-none', name !== tab);
    });

    if (tab === 'apps') {
        loadAppBackupsOverview();
        hydrateRunningBackups();
    } else if (tab === 'snapshots') {
        if (typeof loadSnapshots === 'function') loadSnapshots();
        if (typeof loadSnapshotConfig === 'function') loadSnapshotConfig();
        if (typeof startProgressPolling === 'function') startProgressPolling();
    } else if (tab === 'env') {
        // Export/import d'environnement (module backup-import.js)
        if (typeof loadBackupInfo === 'function') loadBackupInfo();
    } else if (tab === 'vault') {
        // Coffre hubctl (module admin-vault.js)
        if (typeof loadVaultStatus === 'function') loadVaultStatus();
    }
}

function capitalizeBackupTab(tab) {
    return tab.charAt(0).toUpperCase() + tab.slice(1);
}

function parseCronToBuilder(cron) {
    const raw = (cron || '').trim();
    const parts = raw.split(/\s+/);
    if (parts.length !== 5) {
        return { preset: 'daily', time: '02:00', weekday: '1', monthday: '1', cron: raw || '0 2 * * *' };
    }
    const [minute, hour, day, month, weekday] = parts;
    const time = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
    if (day === '*' && month === '*' && weekday === '*') {
        return { preset: 'daily', time, weekday: '1', monthday: '1', cron: raw };
    }
    if (day === '*' && month === '*' && /^\d$/.test(weekday)) {
        return { preset: 'weekly', time, weekday, monthday: '1', cron: raw };
    }
    if (/^\d{1,2}$/.test(day) && month === '*' && weekday === '*') {
        return { preset: 'monthly', time, weekday: '1', monthday: day, cron: raw };
    }
    return { preset: 'custom', time, weekday: '1', monthday: '1', cron: raw };
}

/**
 * data-action du select #backupCronPreset (remplace l'ancien escape-hatch onChange)
 */
function onBackupCronPresetChange() {
    updateCronBuilderVisibility();
    updateCronFromBuilder();
}

function updateCronBuilderVisibility() {
    const preset = document.getElementById('backupCronPreset')?.value || 'daily';
    document.getElementById('backupCronWeekdayGroup')?.classList.toggle('d-none', preset !== 'weekly');
    document.getElementById('backupCronMonthdayGroup')?.classList.toggle('d-none', preset !== 'monthly');
    document.getElementById('backupCronAdvancedGroup')?.classList.toggle('d-none', preset !== 'custom');
}

function updateCronFromBuilder() {
    const preset = document.getElementById('backupCronPreset')?.value || 'daily';
    if (preset === 'custom') {
        updateCronPreview();
        return;
    }
    const [hour = '2', minute = '0'] = (document.getElementById('backupCronTime')?.value || '02:00').split(':');
    const weekday = document.getElementById('backupCronWeekday')?.value || '1';
    const monthday = Math.max(1, Math.min(28, parseInt(document.getElementById('backupCronMonthday')?.value || '1', 10)));
    let cron = `${parseInt(minute, 10)} ${parseInt(hour, 10)} * * *`;
    if (preset === 'weekly') {
        cron = `${parseInt(minute, 10)} ${parseInt(hour, 10)} * * ${weekday}`;
    } else if (preset === 'monthly') {
        cron = `${parseInt(minute, 10)} ${parseInt(hour, 10)} ${monthday} * *`;
    }
    const input = document.getElementById('backupConfigCron');
    if (input) input.value = cron;
    updateCronPreview();
}

function updateCronPreview() {
    const value = document.getElementById('backupConfigCron')?.value || '0 2 * * *';
    const preview = document.getElementById('backupCronPreview');
    if (preview) preview.textContent = value;
}

function initializeCronBuilder(cron) {
    const parsed = parseCronToBuilder(cron);
    document.getElementById('backupCronPreset').value = parsed.preset;
    document.getElementById('backupCronTime').value = parsed.time;
    document.getElementById('backupCronWeekday').value = parsed.weekday;
    document.getElementById('backupCronMonthday').value = parsed.monthday;
    document.getElementById('backupConfigCron').value = parsed.cron;
    updateCronBuilderVisibility();
    updateCronFromBuilder();
}

function getBackupConfigCronValue() {
    const mode = document.getElementById('backupConfigMode').value;
    if (mode !== 'scheduled') return null;
    updateCronFromBuilder();
    return document.getElementById('backupConfigCron').value || null;
}

/**
 * Démarre/arrête le timer 1s qui rafraîchit les durées écoulées des backups en cours.
 */
function ensureRunningBackupsTimer() {
    if (runningBackups.size === 0) {
        if (_runningBackupsTimer) {
            clearInterval(_runningBackupsTimer);
            _runningBackupsTimer = null;
        }
        return;
    }
    if (_runningBackupsTimer) return;
    _runningBackupsTimer = setInterval(() => {
        // Refresh elapsed seconds inline
        runningBackups.forEach((entry, slug) => {
            const el = document.querySelector(`[data-running-elapsed="${slug}"]`);
            if (el && entry.started_at) {
                const startMs = parseUTCDate(entry.started_at).getTime();
                const elapsed = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
                el.textContent = formatBackupDuration(elapsed);
            }
        });
    }, 1000);
}

/**
 * Restitue la section "Sauvegardes en cours" à partir de la Map runningBackups.
 */
function renderRunningBackups() {
    const section = document.getElementById('appBackupsRunningSection');
    const body = document.getElementById('appBackupsRunningBody');
    const countEl = document.getElementById('appBackupsRunningCount');
    if (!section || !body) return;

    if (runningBackups.size === 0) {
        section.classList.add('d-none');
        body.innerHTML = '';
        if (countEl) countEl.textContent = '0';
        ensureRunningBackupsTimer();
        return;
    }

    section.classList.remove('d-none');
    if (countEl) countEl.textContent = String(runningBackups.size);

    const items = Array.from(runningBackups.entries()).map(([slug, entry]) => {
        const percent = Math.max(0, Math.min(100, Number(entry.progress_percent) || 0));
        const stepLabel = BACKUP_STEP_LABELS[entry.progress_step] || entry.progress_step || '…';
        const safeSlug = escapeHtml(slug);
        const safeName = escapeHtml(entry.app_name || slug);
        let elapsed = '0s';
        if (entry.started_at) {
            const elapsedSec = Math.max(0, Math.floor((Date.now() - parseUTCDate(entry.started_at).getTime()) / 1000));
            elapsed = formatBackupDuration(elapsedSec);
        }
        const eta = entry.avg_duration_seconds
            ? `<small class="text-muted ms-2">Moy. ${formatBackupDuration(entry.avg_duration_seconds)}</small>`
            : '';
        return `
            <div class="px-2 py-2 border-bottom" data-running-row="${safeSlug}">
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <div>
                        <strong>${safeName}</strong>
                        <small class="text-muted ms-2">${escapeHtml(stepLabel)}</small>
                        ${eta}
                    </div>
                    <div class="d-flex align-items-center gap-2">
                        <small class="text-muted">
                            Écoulé : <span data-running-elapsed="${safeSlug}">${elapsed}</span>
                        </small>
                        <button type="button"
                                class="btn btn-sm btn-outline-danger"
                                title="Annuler / Kill-switch"
                                data-action="cancelRunningBackup" data-arg1="${escapeHtml(entry.backup_id)}" data-arg2="${safeName}">
                            <i class="bi bi-x-circle"></i> Annuler
                        </button>
                    </div>
                </div>
                <div class="progress" style="height: 10px;">
                    <div class="progress-bar progress-bar-striped progress-bar-animated bg-primary"
                         role="progressbar"
                         style="width: ${percent}%;"
                         aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
                        ${percent}%
                    </div>
                </div>
            </div>
        `;
    }).join('');

    body.innerHTML = items;
    ensureRunningBackupsTimer();
}

/**
 * Hydrate la Map runningBackups à partir de l'API (utilisé à l'entrée d'onglet).
 */
async function hydrateRunningBackups() {
    try {
        const data = await api.listInProgressBackups();
        runningBackups.clear();
        (data.items || []).forEach(item => {
            runningBackups.set(item.app_slug, {
                backup_id: item.backup_id,
                app_name: item.app_name,
                status: item.status,
                progress_step: item.progress_step,
                progress_percent: item.progress_percent,
                started_at: item.started_at,
                avg_duration_seconds: item.avg_duration_seconds
            });
        });
        renderRunningBackups();
    } catch (e) {
        console.warn('Hydratation backups en cours échouée:', e);
    }
}

/**
 * Kill-switch manuel : annule un backup bloqué en pending/running.
 */
async function cancelRunningBackup(backupId, appName) {
    if (!backupId) return;
    if (!confirm(`Annuler la sauvegarde en cours pour ${appName} ?\n\nLa ligne sera marquée comme échouée et le verrou libéré pour permettre un nouveau déclenchement.`)) {
        return;
    }
    try {
        const resp = await api.cancelInProgressBackup(backupId);
        showToast('Sauvegarde annulée', resp?.message || `Sauvegarde de ${appName} annulée`, 'warning');
        // Optimistic UI : retirer immédiatement de la Map (le SSE backup_failed
        // va également arriver et confirmer).
        for (const [slug, entry] of runningBackups.entries()) {
            if (entry.backup_id === backupId) {
                runningBackups.delete(slug);
                break;
            }
        }
        renderRunningBackups();
        loadAppBackupsOverview();
    } catch (error) {
        console.error('Erreur annulation backup:', error);
        showToast('Erreur', error?.message || 'Impossible d\'annuler la sauvegarde', 'danger');
        // Re-hydrater au cas où le backend l'a déjà nettoyé
        hydrateRunningBackups();
    }
}

/**
 * Charge la vue d'ensemble des sauvegardes de toutes les applications
 */
async function loadAppBackupsOverview() {
    try {
        const data = await api.request('/admin/backups');
        backupOverviewApps = data.apps || [];
        
        // Mettre à jour les stats globales
        document.getElementById('statsAppsCount').textContent = data.total_apps || 0;
        document.getElementById('statsTotalBackups').textContent = data.total_backups || 0;
        document.getElementById('statsTotalSize').textContent = data.total_size_human || '0 B';
        
        // Compter les apps planifiées
        const scheduledCount = (data.apps || []).filter(a => a.backup_mode === 'scheduled').length;
        document.getElementById('statsScheduledApps').textContent = scheduledCount;
        
        // Générer le tableau
        const tbody = document.getElementById('appBackupsTableBody');
        
        if (!backupOverviewApps || backupOverviewApps.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted py-4">
                        <i class="bi bi-inbox me-2"></i>Aucune application trouvée
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = backupOverviewApps.map(app => {
            // Badge du mode
            let modeBadge;
            switch (app.backup_mode) {
                case 'disabled':
                    modeBadge = '<span class="badge bg-secondary">Désactivé</span>';
                    break;
                case 'manual':
                    modeBadge = '<span class="badge bg-info">Manuel</span>';
                    break;
                case 'scheduled':
                    modeBadge = `<span class="badge bg-success">Planifié</span>`;
                    break;
                default:
                    modeBadge = '<span class="badge bg-secondary">Manuel</span>';
            }
            
            // Dernier backup
            let lastBackupInfo = '-';
            if (app.last_backup_at) {
                const lastDate = parseUTCDate(app.last_backup_at);
                const statusIcon = app.last_backup_status === 'success' ? '✅' : 
                                   app.last_backup_status === 'failed' ? '❌' : '⏳';
                lastBackupInfo = `${statusIcon} ${lastDate.toLocaleDateString('fr-FR')} ${lastDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
                if (app.last_backup_duration_human) {
                    lastBackupInfo += ` <span class="text-muted">(${escapeHtml(app.last_backup_duration_human)})</span>`;
                }
            }
            
            // Prochain backup
            let nextBackupInfo = '-';
            if (app.backup_mode === 'scheduled' && app.next_backup_at) {
                const nextDate = parseUTCDate(app.next_backup_at);
                nextBackupInfo = `${nextDate.toLocaleDateString('fr-FR')} ${nextDate.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
            } else if (app.backup_mode === 'scheduled' && app.backup_cron) {
                nextBackupInfo = `<code class="small">${app.backup_cron}</code>`;
            }

            const isRunning = runningBackups.has(app.app_slug);
            const isUnmanaged = !!app.is_unmanaged;
            const systemBadge = app.is_system_app
                ? '<span class="badge bg-dark ms-2"><i class="bi bi-grid-3x3-gap-fill me-2"></i>Hub</span>'
                : '';
            const unmanagedBadge = isUnmanaged
                ? '<span class="badge bg-secondary ms-2" title="Application lien only — non gérée par le HUB">Lien only</span>'
                : '';
            const triggerCell = isUnmanaged
                ? `<button class="btn btn-outline-secondary" disabled title="Application non gérée (lien only) — sauvegarde indisponible">
                       <i class="bi bi-slash-circle"></i>
                   </button>`
                : isRunning
                ? `<button class="btn btn-outline-secondary" disabled title="Sauvegarde en cours">
                       <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
                       <span class="ms-1">En cours…</span>
                   </button>`
                : `<button class="btn btn-outline-success" data-action="showTriggerBackupConfirm" data-arg1="${app.app_slug}" data-arg2="${escapeHtml(app.app_name)}"
                     title="Déclencher sauvegarde" ${app.backup_mode === 'disabled' ? 'style="display: none;"' : ''}>
                        <i class="bi bi-play-circle"></i>
                    </button>`;

            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(app.app_name)}</strong>${systemBadge}${unmanagedBadge}
                        <br><small class="text-muted">${escapeHtml(app.app_slug)}</small>
                    </td>
                    <td>${modeBadge}</td>
                    <td><small>${lastBackupInfo}</small></td>
                    <td><small>${nextBackupInfo}</small></td>
                    <td>
                        <span class="badge bg-light text-dark">${app.backups_count}</span>
                    </td>
                    <td>${app.total_size_human}</td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">
                            ${triggerCell}
                            <button class="btn btn-outline-primary" data-action="showAppBackupsList" data-arg1="${app.app_slug}" title="Voir les sauvegardes">
                                <i class="bi bi-hdd-stack"></i>
                            </button>
                            ${isUnmanaged ? '' : `<button class="btn btn-outline-secondary" data-action="showAppBackupConfig" data-arg1="${app.app_slug}" data-arg2="${escapeHtml(app.app_name)}"
                             title="Configurer">
                                <i class="bi bi-gear"></i>
                            </button>`}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        
    } catch (error) {
        console.error('Erreur chargement backups apps:', error);
        showToast('Erreur', 'Impossible de charger les sauvegardes', 'danger');
    }
}

/**
 * Affiche la modale de configuration de backup pour une application
 */
async function showAppBackupConfig(slug, name) {
    // Initialiser la modale si nécessaire
    if (!appBackupConfigModal) {
        appBackupConfigModal = new bootstrap.Modal(document.getElementById('appBackupConfigModal'));
    }
    
    document.getElementById('backupConfigAppSlug').value = slug;
    document.getElementById('backupConfigAppName').textContent = name;
    
    try {
        // Charger la config actuelle
        const config = await api.request(`/apps/${slug}/backup/config`);
        
        document.getElementById('backupConfigMode').value = config.backup_mode || 'manual';
        initializeCronBuilder(config.backup_cron || '0 2 * * *');
        document.getElementById('backupConfigRetention').value = config.backup_retention ?? 5;
        document.getElementById('backupConfigQuiesce').checked = Boolean(config.backup_quiesce);
        
        toggleCronInput();
        appBackupConfigModal.show();

        // Afficher l'heure UTC du Hub pour aider l'utilisateur à composer le cron
        const hubTimeEl = document.getElementById('backupConfigHubTime');
        if (hubTimeEl) {
            hubTimeEl.textContent = '…';
            try {
                const t = await api.getSystemTime();
                hubTimeEl.textContent = t.utc_now_human || t.utc_now || '?';
            } catch (e) {
                hubTimeEl.textContent = 'indisponible';
            }
        }
        
    } catch (error) {
        console.error('Erreur chargement config backup:', error);
        showToast('Erreur', 'Impossible de charger la configuration', 'danger');
    }
}

/**
 * Affiche/masque le champ cron selon le mode
 */
function toggleCronInput() {
    const mode = document.getElementById('backupConfigMode').value;
    const cronGroup = document.getElementById('cronInputGroup');
    cronGroup.style.display = mode === 'scheduled' ? 'block' : 'none';
    if (mode === 'scheduled') {
        updateCronBuilderVisibility();
        updateCronFromBuilder();
    }
}

/**
 * Enregistre la configuration de backup
 */
async function saveAppBackupConfig(event) {
    event.preventDefault();
    
    const slug = document.getElementById('backupConfigAppSlug').value;
    const config = {
        backup_mode: document.getElementById('backupConfigMode').value,
        backup_cron: getBackupConfigCronValue(),
        backup_retention: parseInt(document.getElementById('backupConfigRetention').value) || 5,
        backup_quiesce: document.getElementById('backupConfigQuiesce').checked
    };
    
    try {
        await api.request(`/apps/${slug}/backup/config`, {
            method: 'PUT',
            body: JSON.stringify(config)
        });
        
        showToast('Succès', 'Configuration de sauvegarde mise à jour', 'success');
        appBackupConfigModal.hide();
        loadAppBackupsOverview();
        
    } catch (error) {
        console.error('Erreur sauvegarde config:', error);
        showToast('Erreur', error.message || 'Impossible de sauvegarder', 'danger');
    }
}

async function loadBackupClusters() {
    const tbody = document.getElementById('backupClustersTableBody');
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">Clusters remplaces par les <a href="#" data-action="showPage" data-args="snapshots">Snapshots</a></td></tr>';
    }
}

function renderBackupClustersTable() {
    const tbody = document.getElementById('backupClustersTableBody');
    if (!tbody) return;

    if (!backupClusters.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center text-muted py-4">
                    <i class="bi bi-diagram-3 me-2"></i>Aucun cluster configuré
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = backupClusters.map(cluster => {
        const apps = cluster.apps || [];
        const appBadges = apps.length
            ? apps.map(app => `<span class="badge bg-light text-dark border me-1 mb-1">${escapeHtml(app.app_name)}${app.is_system_app ? ' · Hub' : ''}</span>`).join('')
            : '<span class="text-muted small">Aucune application</span>';
        return `
            <tr>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <span class="rounded-circle d-inline-block" style="width: 14px; height: 14px; background: ${escapeHtml(cluster.color || '#0d6efd')};"></span>
                        <div>
                            <strong>${escapeHtml(cluster.name)}</strong>
                            <br><small class="text-muted">${escapeHtml(cluster.slug)}</small>
                        </div>
                    </div>
                </td>
                <td>${appBadges}</td>
                <td><span class="badge bg-success">${cluster.scheduled_apps || 0}</span></td>
                <td><span class="badge bg-light text-dark">${cluster.total_backups || 0}</span></td>
                <td>${escapeHtml(cluster.total_size_human || '0 B')}</td>
                <td class="text-end">
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-success" data-action="triggerBackupCluster" data-arg1="${cluster.id}" title="Déclencher les sauvegardes du cluster">
                            <i class="bi bi-play-circle"></i>
                        </button>
                        <button class="btn btn-outline-primary" data-action="showBackupClusterModal" data-arg1="${cluster.id}" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-danger" data-action="deleteBackupCluster" data-arg1="${cluster.id}" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function ensureBackupAppsForClusterModal() {
    if (backupOverviewApps.length) return;
    const data = await api.request('/admin/backups');
    backupOverviewApps = data.apps || [];
}

async function showBackupClusterModal(clusterId = null) {
    if (clusterId != null) clusterId = parseInt(clusterId, 10);
    if (!backupClusterModal) {
        backupClusterModal = new bootstrap.Modal(document.getElementById('backupClusterModal'));
    }

    try {
        await ensureBackupAppsForClusterModal();
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
        return;
    }

    const cluster = clusterId ? backupClusters.find(item => item.id === clusterId) : null;
    document.getElementById('backupClusterModalTitle').textContent = cluster ? 'Modifier le cluster' : 'Nouveau cluster';
    document.getElementById('backupClusterId').value = cluster?.id || '';
    document.getElementById('backupClusterName').value = cluster?.name || '';
    document.getElementById('backupClusterSlug').value = cluster?.slug || '';
    document.getElementById('backupClusterDescription').value = cluster?.description || '';
    document.getElementById('backupClusterColor').value = cluster?.color || '#0d6efd';
    document.getElementById('backupClusterConfigMode').value = '';
    document.getElementById('backupClusterConfigCron').value = '';
    document.getElementById('backupClusterConfigRetention').value = '';
    document.getElementById('backupClusterConfigQuiesce').checked = false;

    const selectedIds = new Set((cluster?.apps || []).map(app => Number(app.app_id)));
    renderBackupClusterAppChoices(selectedIds);
    backupClusterModal.show();
}

function renderBackupClusterAppChoices(selectedIds = new Set()) {
    const list = document.getElementById('backupClusterAppsList');
    if (!list) return;
    if (!backupOverviewApps.length) {
        list.innerHTML = '<div class="text-center text-muted py-3">Aucune application disponible</div>';
        updateBackupClusterSelectedCount();
        return;
    }
    list.innerHTML = backupOverviewApps.map(app => {
        const appId = Number(app.app_id);
        const checked = selectedIds.has(appId) ? 'checked' : '';
        const systemBadge = app.is_system_app ? '<span class="badge bg-dark ms-2">Hub</span>' : '';
        return `
            <label class="d-flex align-items-center justify-content-between border-bottom py-2 mb-0">
                <span>
                    <input class="form-check-input me-2 backup-cluster-app-choice" type="checkbox" value="${appId}" ${checked} data-action="updateBackupClusterSelectedCount">
                    <strong>${escapeHtml(app.app_name)}</strong>${systemBadge}
                    <small class="text-muted ms-2">${escapeHtml(app.app_slug)}</small>
                </span>
                <small class="text-muted">${escapeHtml(app.backup_mode || 'manual')}</small>
            </label>
        `;
    }).join('');
    updateBackupClusterSelectedCount();
}

function updateBackupClusterSelectedCount() {
    const count = document.querySelectorAll('.backup-cluster-app-choice:checked').length;
    const el = document.getElementById('backupClusterSelectedCount');
    if (el) el.textContent = String(count);
}

function suggestBackupClusterSlug() {
    const id = document.getElementById('backupClusterId')?.value;
    const slugInput = document.getElementById('backupClusterSlug');
    if (id || !slugInput || slugInput.value.trim()) return;
    const name = document.getElementById('backupClusterName')?.value || '';
    slugInput.value = name
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 50);
}

async function saveBackupCluster(event) {
    event.preventDefault();
    const id = document.getElementById('backupClusterId').value;
    const payload = {
        name: document.getElementById('backupClusterName').value.trim(),
        slug: document.getElementById('backupClusterSlug').value.trim(),
        description: document.getElementById('backupClusterDescription').value.trim() || null,
        color: document.getElementById('backupClusterColor').value || '#0d6efd',
        app_ids: Array.from(document.querySelectorAll('.backup-cluster-app-choice:checked')).map(input => Number(input.value))
    };

    const config = {};
    const mode = document.getElementById('backupClusterConfigMode').value;
    const cron = document.getElementById('backupClusterConfigCron').value.trim();
    const retention = document.getElementById('backupClusterConfigRetention').value;
    if (mode) config.backup_mode = mode;
    if (cron) config.backup_cron = cron;
    if (retention !== '') config.backup_retention = parseInt(retention, 10);
    if (document.getElementById('backupClusterConfigQuiesce').checked) config.backup_quiesce = true;

    try {
        let saved = id
            ? await api.updateBackupCluster(id, payload)
            : await api.createBackupCluster(payload);
        if (Object.keys(config).length) {
            saved = await api.updateBackupClusterConfig(saved.id, config);
        }
        showToast('Succès', `Cluster ${saved.name} enregistré`, 'success');
        backupClusterModal.hide();
        await loadBackupClusters();
        loadAppBackupsOverview();
    } catch (error) {
        console.error('Erreur sauvegarde cluster:', error);
        showToast('Erreur', error.message || 'Impossible d\'enregistrer le cluster', 'danger');
    }
}

async function deleteBackupCluster(clusterId) {
    clusterId = parseInt(clusterId, 10);
    const cluster = backupClusters.find(item => item.id === clusterId);
    if (!cluster) return;
    if (!confirm(`Supprimer le cluster ${cluster.name} ?\n\nLes sauvegardes existantes ne seront pas supprimées.`)) {
        return;
    }
    try {
        await api.deleteBackupCluster(clusterId);
        showToast('Succès', 'Cluster supprimé', 'success');
        loadBackupClusters();
    } catch (error) {
        console.error('Erreur suppression cluster:', error);
        showToast('Erreur', error.message || 'Impossible de supprimer le cluster', 'danger');
    }
}

async function triggerBackupCluster(clusterId) {
    clusterId = parseInt(clusterId, 10);
    const cluster = backupClusters.find(item => item.id === clusterId);
    if (!cluster || !cluster.apps?.length) return;
    const candidates = cluster.apps.filter(app => app.backup_mode !== 'disabled' && !runningBackups.has(app.app_slug));
    if (!candidates.length) {
        showToast('Cluster', 'Aucune application disponible à sauvegarder', 'warning');
        return;
    }
    if (!confirm(`Déclencher ${candidates.length} sauvegarde(s) pour le cluster ${cluster.name} ?`)) {
        return;
    }
    for (const app of candidates) {
        await doTriggerBackup(app.app_slug, app.app_name);
    }
}

/**
 * Affiche la liste des backups d'une application
 */
async function showAppBackupsList(slug) {
    // Initialiser la modale si nécessaire
    if (!appBackupsListModal) {
        appBackupsListModal = new bootstrap.Modal(document.getElementById('appBackupsListModal'));
    }
    
    document.getElementById('backupsListAppSlug').value = slug;
    
    try {
        const data = await api.request(`/apps/${slug}/backup`);
        
        document.getElementById('backupsListAppName').textContent = data.app_name;
        
        // Config résumé
        const modeLabels = {
            'disabled': 'Désactivé',
            'manual': 'Manuel',
            'scheduled': 'Planifié'
        };
        document.getElementById('backupsListMode').textContent = modeLabels[data.backup_config?.backup_mode] || 'Manuel';
        document.getElementById('backupsListCron').textContent = data.backup_config?.backup_cron || '-';
        document.getElementById('backupsListRetention').textContent = data.backup_config?.backup_retention ?? 5;
        
        // Afficher/masquer le bouton de déclenchement selon le mode
        const triggerBtn = document.getElementById('triggerBackupBtn');
        if (triggerBtn) {
            const unmanaged = !!data.is_unmanaged;
            triggerBtn.style.display = (!unmanaged && data.backup_config?.backup_mode !== 'disabled') ? '' : 'none';
        }
        
        // Liste des backups
        const tbody = document.getElementById('backupsListBody');
        appBackupsById = {};
        
        if (!data.backups || data.backups.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-muted py-3">
                        <i class="bi bi-inbox me-1"></i>Aucune sauvegarde disponible
                    </td>
                </tr>
            `;
        } else {
            tbody.innerHTML = data.backups.map(backup => {
                appBackupsById[`${slug}:${backup.backup_id}`] = backup;
                const date = parseUTCDate(backup.created_at);
                const dateStr = `${date.toLocaleDateString('fr-FR')} ${date.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'})}`;
                const escapedAppName = escapeHtml(data.app_name).replace(/'/g, "\\'");
                const typeBadges = [];
                if (backup.includes_vaultwarden_snapshot) {
                    typeBadges.push('<span class="badge bg-danger-subtle text-danger border border-danger-subtle me-1"><i class="bi bi-database-lock me-1"></i>Vaultwarden complet</span>');
                }
                if (backup.includes_secret_bundle) {
                    typeBadges.push('<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle"><i class="bi bi-key me-1"></i>Secrets chiffrés</span>');
                }
                
                // Afficher le contenu: description si disponible, sinon compteurs
                let contentDisplay = '';
                if (backup.description && backup.description.trim()) {
                    contentDisplay = backup.description;
                } else if (backup.total_items > 0 || backup.total_files > 0 || backup.total_preferences > 0) {
                    const parts = [];
                    if (backup.total_items > 0) parts.push(`${backup.total_items} items`);
                    if (backup.total_preferences > 0) parts.push(`${backup.total_preferences} prefs`);
                    if (backup.total_files > 0) parts.push(`${backup.total_files} fichiers`);
                    contentDisplay = parts.join(', ');
                } else {
                    contentDisplay = 'Backup vide';
                }
                
                return `
                    <tr>
                        <td>
                            <small>${dateStr}</small>
                        </td>
                        <td><small>${backup.app_version || '-'}</small></td>
                        <td>
                            <small class="text-muted" title="${escapeHtml(backup.description || '')}">
                                ${escapeHtml(contentDisplay)}
                            </small>
                            ${typeBadges.length ? `<div class="mt-1">${typeBadges.join('')}</div>` : ''}
                        </td>
                        <td><small>${backup.total_size_human}</small></td>
                        <td>
                            <span class="badge bg-light text-dark">${backup.created_by_type}</span>
                            ${backup.duration_human ? `<br><small class="text-muted">Durée: ${escapeHtml(backup.duration_human)}</small>` : ''}
                        </td>
                        <td class="text-end">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-warning" data-action="showRestoreBackupModal" data-arg1="${slug}" data-arg2="${backup.backup_id}" data-arg3="${escapedAppName}" title="Restaurer">
                                    <i class="bi bi-arrow-counterclockwise"></i>
                                </button>
                                <button class="btn btn-outline-success" data-action="exportEncryptedBackup" data-arg1="${slug}" data-arg2="${backup.backup_id}" title="Exporter chiffré">
                                    <i class="bi bi-shield-lock"></i>
                                </button>
                                <button class="btn btn-outline-danger" data-action="deleteAppBackup" data-arg1="${slug}" data-arg2="${backup.backup_id}" data-arg3="${escapedAppName}" data-arg4="${dateStr}" title="Supprimer">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }
        
        appBackupsListModal.show();
        
    } catch (error) {
        console.error('Erreur chargement backups:', error);
        showToast('Erreur', 'Impossible de charger les sauvegardes', 'danger');
    }
}

// =============================================================================
// RESTAURATION DE BACKUPS
// =============================================================================

let restoreBackupModal = null;
let importRestoreModal = null;

/**
 * Affiche le modal de restauration pour un backup existant
 */
function showRestoreBackupModal(slug, backupId, appName) {
    if (!restoreBackupModal) {
        restoreBackupModal = new bootstrap.Modal(document.getElementById('restoreBackupModal'));
    }
    selectedRestoreBackup = appBackupsById[`${slug}:${backupId}`] || null;
    
    document.getElementById('restoreBackupSlug').value = slug;
    document.getElementById('restoreBackupId').value = backupId;
    document.getElementById('restoreAppName').textContent = appName;
    
    // Réinitialiser les options
    document.getElementById('restoreClearExisting').checked = false;
    document.getElementById('restoreItems').checked = true;
    document.getElementById('restorePreferences').checked = true;
    document.getElementById('restoreFiles').checked = true;
    document.getElementById('restoreTestData').checked = true;
    document.getElementById('restoreVaultwarden').checked = false;
    document.getElementById('restoreSecretBundle').checked = false;
    document.getElementById('requireVaultwardenOffline').checked = true;
    document.getElementById('confirmFullRestore').checked = false;

    const hasVaultwarden = Boolean(selectedRestoreBackup?.includes_vaultwarden_snapshot);
    const hasSecrets = Boolean(selectedRestoreBackup?.includes_secret_bundle);
    const vaultwardenOptions = document.getElementById('vaultwardenRestoreOptions');
    vaultwardenOptions.classList.toggle('d-none', !hasVaultwarden && !hasSecrets);
    document.getElementById('restoreVaultwarden').disabled = !hasVaultwarden;
    document.getElementById('restoreSecretBundle').disabled = !hasSecrets;
    updateRestoreVaultwardenConfirmation();
    
    restoreBackupModal.show();
}

function updateRestoreVaultwardenConfirmation() {
    const restoreVaultwarden = document.getElementById('restoreVaultwarden');
    const confirmFullRestore = document.getElementById('confirmFullRestore');
    const confirmGroup = document.getElementById('restoreFullConfirmGroup');
    const executeBtn = document.getElementById('executeRestoreBtn');
    const needsConfirmation = restoreVaultwarden && restoreVaultwarden.checked;

    if (confirmGroup) {
        confirmGroup.classList.toggle('d-none', !needsConfirmation);
    }
    if (!needsConfirmation && confirmFullRestore) {
        confirmFullRestore.checked = false;
    }
    if (executeBtn) {
        executeBtn.disabled = needsConfirmation && !confirmFullRestore.checked;
    }
}

/**
 * Exécute la restauration d'un backup existant vers l'application
 */
async function executeRestore() {
    const slug = document.getElementById('restoreBackupSlug').value;
    const backupId = document.getElementById('restoreBackupId').value;
    
    if (!slug || !backupId) return;
    
    const options = {
        clear_existing: document.getElementById('restoreClearExisting').checked,
        restore_items: document.getElementById('restoreItems').checked,
        restore_preferences: document.getElementById('restorePreferences').checked,
        restore_files: document.getElementById('restoreFiles').checked,
        restore_test_data: document.getElementById('restoreTestData').checked,
        restore_vaultwarden: document.getElementById('restoreVaultwarden').checked,
        restore_secret_bundle: document.getElementById('restoreSecretBundle').checked,
        require_vaultwarden_offline: document.getElementById('requireVaultwardenOffline').checked,
        confirm_full_restore: document.getElementById('restoreVaultwarden').checked && document.getElementById('confirmFullRestore').checked
    };
    
    // Confirmation si clear_existing
    if (options.clear_existing) {
        if (!confirm('⚠️ ATTENTION : Toutes les données existantes de l\'application seront SUPPRIMÉES avant la restauration.\n\nContinuer ?')) {
            return;
        }
    }
    
    restoreBackupModal.hide();
    showToast('Restauration', 'Restauration en cours...', 'info');
    
    try {
        const result = await api.request(`/apps/${slug}/backup/${backupId}/restore`, {
            method: 'POST',
            body: JSON.stringify(options)
        });
        
        if (result.success) {
            let message = `Restauration terminée : ${result.items_restored} items, ${result.preferences_restored} prefs, ${result.files_restored} fichiers`;
            if (result.vaultwarden_snapshot_restored) {
                message += ', snapshot Vaultwarden restauré';
            }
            if (result.secret_bundle_restored) {
                message += ', secrets chiffrés restaurés';
            }
            if (result.accounts_reencrypted) {
                message += `, ${result.accounts_reencrypted} compte(s) ré-encrypté(s)`;
            }
            if (result.requires_vaultwarden_restart) {
                message += '\nRedémarrage Vaultwarden requis.';
            }
            if (result.warnings && result.warnings.length > 0) {
                message += `\n⚠️ ${result.warnings.length} avertissement(s)`;
            }
            showToast('Succès', message, 'success');
        } else {
            showToast('Attention', result.message || 'Restauration avec erreurs', 'warning');
        }
        
        // Afficher les erreurs éventuelles
        if (result.errors && result.errors.length > 0) {
            console.warn('Erreurs de restauration:', result.errors);
        }
        
    } catch (error) {
        console.error('Erreur restauration:', error);
        showToast('Erreur', error.message || 'Impossible de restaurer le backup', 'danger');
    }
}

/**
 * Affiche le modal d'import et restauration depuis fichier
 */
function showImportRestoreModal() {
    if (!importRestoreModal) {
        importRestoreModal = new bootstrap.Modal(document.getElementById('importRestoreModal'));
    }
    
    const slug = document.getElementById('backupsListAppSlug').value;
    document.getElementById('importRestoreSlug').value = slug;
    
    // Réinitialiser
    document.getElementById('importRestoreFile').value = '';
    document.getElementById('importRestorePreview').classList.add('d-none');
    document.getElementById('executeImportRestoreBtn').disabled = true;
    document.getElementById('importClearExisting').checked = false;
    document.getElementById('importRestoreItems').checked = true;
    document.getElementById('importRestorePreferences').checked = true;
    document.getElementById('importRestoreFiles').checked = true;
    document.getElementById('importRestoreTestData').checked = true;
    document.getElementById('importRestoreVaultwarden').checked = false;
    document.getElementById('importRestoreSecretBundle').checked = false;
    document.getElementById('importRequireVaultwardenOffline').checked = true;
    document.getElementById('importConfirmFullRestore').checked = false;
    document.getElementById('importRestorePassword').value = '';
    document.getElementById('importStoreBackup').checked = true;
    document.getElementById('importRestoreNow').checked = false;
    updateImportVaultwardenConfirmation();
    
    importRestoreModal.show();
}

function updateImportVaultwardenConfirmation() {
    const restoreVaultwarden = document.getElementById('importRestoreVaultwarden');
    const confirmFullRestore = document.getElementById('importConfirmFullRestore');
    const confirmGroup = document.getElementById('importFullConfirmGroup');
    const executeBtn = document.getElementById('executeImportRestoreBtn');
    const fileSelected = Boolean(document.getElementById('importRestoreFile')?.files?.[0]);
    const needsConfirmation = restoreVaultwarden && restoreVaultwarden.checked;

    if (confirmGroup) {
        confirmGroup.classList.toggle('d-none', !needsConfirmation);
    }
    if (!needsConfirmation && confirmFullRestore) {
        confirmFullRestore.checked = false;
    }
    if (executeBtn) {
        executeBtn.disabled = !fileSelected || (needsConfirmation && !confirmFullRestore.checked);
    }
}

/**
 * Prévisualise le fichier d'import sélectionné
 */
function previewImportRestoreFile() {
    const fileInput = document.getElementById('importRestoreFile');
    const file = fileInput.files[0];
    
    if (!file) {
        document.getElementById('importRestorePreview').classList.add('d-none');
        document.getElementById('executeImportRestoreBtn').disabled = true;
        return;
    }
    
    // Vérifier l'extension
    if (!file.name.endsWith('.zip') && !file.name.endsWith('.encrypted')) {
        showToast('Erreur', 'Le fichier doit être un ZIP ou un fichier chiffré (.encrypted)', 'warning');
        fileInput.value = '';
        document.getElementById('importRestorePreview').classList.add('d-none');
        document.getElementById('executeImportRestoreBtn').disabled = true;
        return;
    }
    
    // Afficher l'aperçu
    document.getElementById('importRestoreFilename').textContent = file.name;
    document.getElementById('importRestoreFilesize').textContent = formatFileSize(file.size);
    document.getElementById('importRestorePreview').classList.remove('d-none');
    updateImportVaultwardenConfirmation();
}

/**
 * Exécute l'import et restauration depuis fichier
 */
async function executeImportRestore() {
    const slug = document.getElementById('importRestoreSlug').value;
    const fileInput = document.getElementById('importRestoreFile');
    const file = fileInput.files[0];
    
    if (!slug || !file) return;
    
    const clearExisting = document.getElementById('importClearExisting').checked;
    
    // Confirmation si clear_existing
    if (clearExisting) {
        if (!confirm('⚠️ ATTENTION : Toutes les données existantes de l\'application seront SUPPRIMÉES avant la restauration.\n\nContinuer ?')) {
            return;
        }
    }
    
    importRestoreModal.hide();
    showToast('Import', 'Envoi et restauration en cours...', 'info');
    
    try {
        // Préparer le FormData
        const formData = new FormData();
        formData.append('file', file);
        formData.append('clear_existing', clearExisting);
        formData.append('restore_items', document.getElementById('importRestoreItems').checked);
        formData.append('restore_preferences', document.getElementById('importRestorePreferences').checked);
        formData.append('restore_files', document.getElementById('importRestoreFiles').checked);
        formData.append('restore_test_data', document.getElementById('importRestoreTestData').checked);
        formData.append('restore_vaultwarden', document.getElementById('importRestoreVaultwarden').checked);
        formData.append('restore_secret_bundle', document.getElementById('importRestoreSecretBundle').checked);
        formData.append('require_vaultwarden_offline', document.getElementById('importRequireVaultwardenOffline').checked);
        formData.append('confirm_full_restore', document.getElementById('importRestoreVaultwarden').checked && document.getElementById('importConfirmFullRestore').checked);
        formData.append('password', document.getElementById('importRestorePassword').value || '');
        formData.append('store_backup', document.getElementById('importStoreBackup').checked);
        formData.append('restore_now', document.getElementById('importRestoreNow').checked);

        // Envoyer la requête
        const response = await fetch(`/api/apps/${slug}/backup/import`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${api.token}`
            },
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Erreur lors de l\'import');
        }
        
        const result = await response.json();
        
        if (result.success) {
            let message = result.message || 'Backup importé';
            if (result.restore) {
                message += ` — ${result.restore.items_restored} items, ${result.restore.preferences_restored} prefs, ${result.restore.files_restored} fichiers`;
                if (result.restore.vaultwarden_snapshot_restored) {
                    message += ', snapshot Vaultwarden restauré';
                }
                if (result.restore.secret_bundle_restored) {
                    message += ', secrets chiffrés restaurés';
                }
                if (result.restore.requires_vaultwarden_restart) {
                    message += '\nRedémarrage Vaultwarden requis.';
                }
            }
            showToast('Succès', message, 'success');
        } else {
            showToast('Attention', result.message || 'Import avec erreurs', 'warning');
        }
        
        // Rafraîchir la liste des backups
        showAppBackupsList(slug);
        
    } catch (error) {
        console.error('Erreur import restauration:', error);
        showToast('Erreur', error.message || 'Impossible d\'importer le backup', 'danger');
    }
}

/**
 * Ouvre le modal de mot de passe pour l'export chiffré
 */
function exportEncryptedBackup(slug, backupId) {
    if (!window._exportPasswordModal) {
        window._exportPasswordModal = new bootstrap.Modal(document.getElementById('exportPasswordModal'));
    }
    document.getElementById('exportPwdSlug').value = slug;
    document.getElementById('exportPwdBackupId').value = backupId;
    document.getElementById('exportPassword').value = '';
    document.getElementById('exportPasswordConfirm').value = '';
    window._exportPasswordModal.show();
}

/**
 * Lance l'export chiffré avec le mot de passe saisi
 */
async function confirmExportPassword() {
    const slug = document.getElementById('exportPwdSlug').value;
    const backupId = document.getElementById('exportPwdBackupId').value;
    const password = document.getElementById('exportPassword').value;
    const passwordConfirm = document.getElementById('exportPasswordConfirm').value;

    if (!password || password.length < 8) {
        showToast('Erreur', 'Le mot de passe doit contenir au moins 8 caractères', 'warning');
        return;
    }
    if (password !== passwordConfirm) {
        showToast('Erreur', 'Les deux mots de passe ne correspondent pas', 'warning');
        return;
    }

    window._exportPasswordModal.hide();
    showToast('Chiffrement', 'Chiffrement du backup en cours...', 'info');

    try {
        const formData = new FormData();
        formData.append('password', password);
        formData.append('password_confirm', passwordConfirm);

        const exportResult = await fetch(`/api/apps/${slug}/backup/${backupId}/export`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${api.token}` },
            body: formData
        });
        if (!exportResult.ok) {
            const err = await exportResult.json().catch(() => ({}));
            throw new Error(err.detail || 'Erreur de chiffrement');
        }
        const exportData = await exportResult.json();

        // Télécharger le fichier chiffré (résilient : tranches Range + reprise,
        // survit à une coupure réseau en plein transfert ; cf. resilient-download.js)
        const url = `/api/apps/${slug}/backup/${backupId}/export/download`;
        const blob = await resilientDownload(url, { 'Authorization': `Bearer ${api.token}` }, {
            label: exportData.encrypted_filename || `backup_${slug}_${backupId}.encrypted`
        });
        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = exportData.encrypted_filename || `backup_${slug}_${backupId}.encrypted`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);

        showToast('Succès', `Backup chiffré exporté (${exportData.encrypted_size_human}). Conservez le mot de passe : il sera demandé à l'import.`, 'success');
        showAppBackupsList(slug);

    } catch (error) {
        console.error('Erreur export chiffré:', error);
        showToast('Erreur', error.message || 'Impossible d\'exporter le backup', 'danger');
    }
}

/**
 * Variables pour la suppression de backup
 */
let confirmDeleteBackupModal = null;

/**
 * Affiche le modal de confirmation de suppression d'un backup
 */
function deleteAppBackup(slug, backupId, appName, backupDate) {
    if (!confirmDeleteBackupModal) {
        confirmDeleteBackupModal = new bootstrap.Modal(document.getElementById('confirmDeleteBackupModal'));
    }
    
    // Stocker les infos du backup à supprimer
    document.getElementById('deleteBackupSlug').value = slug;
    document.getElementById('deleteBackupId').value = backupId;
    document.getElementById('deleteBackupAppName').textContent = appName || slug;
    document.getElementById('deleteBackupDate').textContent = backupDate || '-';
    
    // Réinitialiser le champ de confirmation
    document.getElementById('confirmDeleteInput').value = '';
    document.getElementById('confirmDeleteBackupBtn').disabled = true;
    
    confirmDeleteBackupModal.show();
}

/**
 * Vérifie si la confirmation de suppression est valide
 */
function checkDeleteConfirmation() {
    const input = document.getElementById('confirmDeleteInput').value;
    const btn = document.getElementById('confirmDeleteBackupBtn');
    btn.disabled = input !== 'SUPPRIMER';
}

/**
 * Exécute la suppression après confirmation
 */
async function confirmDeleteBackup() {
    const slug = document.getElementById('deleteBackupSlug').value;
    const backupId = document.getElementById('deleteBackupId').value;
    
    if (!slug || !backupId) return;
    
    // Fermer le modal
    confirmDeleteBackupModal.hide();
    
    try {
        await api.request(`/apps/${slug}/backup/${backupId}`, {
            method: 'DELETE'
        });
        
        showToast('Succès', 'Backup supprimé définitivement', 'success');
        showAppBackupsList(slug);
        loadAppBackupsOverview();
        
    } catch (error) {
        console.error('Erreur suppression:', error);
        showToast('Erreur', error.message || 'Impossible de supprimer le backup', 'danger');
    }
}


// Variables pour le déclenchement de backup
let confirmBackupTriggerModal = null;
let pendingBackupSlug = null;
let pendingBackupAppName = null;

/**
 * Affiche le modal de confirmation pour déclencher un backup
 */
function showTriggerBackupConfirm(slug, appName) {
    if (!confirmBackupTriggerModal) {
        confirmBackupTriggerModal = new bootstrap.Modal(document.getElementById('confirmBackupTriggerModal'));
    }
    
    pendingBackupSlug = slug;
    pendingBackupAppName = appName;
    
    document.getElementById('confirmBackupAppName').textContent = appName;
    confirmBackupTriggerModal.show();
}

/**
 * Déclenche le backup après confirmation (depuis le modal de confirmation)
 */
async function confirmTriggerBackup() {
    if (!pendingBackupSlug) return;
    
    // Fermer le modal de confirmation
    confirmBackupTriggerModal.hide();
    
    await doTriggerBackup(pendingBackupSlug, pendingBackupAppName);
    
    pendingBackupSlug = null;
    pendingBackupAppName = null;
}

/**
 * Déclenche le backup depuis le modal de liste des backups
 */
async function triggerAppBackup() {
    const slug = document.getElementById('backupsListAppSlug').value;
    const appName = document.getElementById('backupsListAppName').textContent;
    
    if (!slug) return;
    
    // Afficher le modal de confirmation
    showTriggerBackupConfirm(slug, appName);
}

/**
 * Effectue le déclenchement du backup
 */
async function doTriggerBackup(slug, appName) {
    // Garde-fou : si une sauvegarde est déjà en cours pour cette app, ne rien faire.
    if (runningBackups.has(slug)) {
        showToast('Sauvegarde en cours', `Une sauvegarde est déjà en cours pour ${appName}.`, 'warning');
        return;
    }

    showToast('Sauvegarde', `Déclenchement de la sauvegarde pour ${appName}...`, 'info');

    try {
        const response = await api.request(`/apps/${slug}/backup/trigger`, {
            method: 'POST'
        });

        // Nouveau flux asynchrone (202 Accepted)
        if (response && response.status === 'pending') {
            // Garde anti-race : si le SSE terminal (backup_completed/failed) est
            // déjà arrivé avant la résolution de ce 202 (cas du backup Hub quasi
            // instantané), ne pas ré-injecter un backup déjà terminé sinon
            // l'indicateur "En cours…" resterait bloqué jusqu'à un refresh.
            if (wasBackupRecentlyFinished(response.backup_id)) {
                runningBackups.delete(slug);
                renderRunningBackups();
                loadAppBackupsOverview();
                return;
            }
            // Insérer immédiatement dans la Map en attendant le 1er événement SSE
            runningBackups.set(slug, {
                backup_id: response.backup_id,
                app_name: appName,
                status: 'pending',
                progress_step: 'queued',
                progress_percent: 0,
                started_at: response.started_at || new Date().toISOString(),
                avg_duration_seconds: null
            });
            renderRunningBackups();
            loadAppBackupsOverview();
            showToast('Sauvegarde lancée', `Sauvegarde de ${appName} en cours…`, 'info');
            return;
        }

        // Compatibilité descendante (réponse synchrone)
        if (response && response.success) {
            showToast('Succès', response.message || `Sauvegarde créée pour ${appName}`, 'success');
            loadAppBackupsOverview();
            if (appBackupsListModal && document.getElementById('backupsListAppSlug').value === slug) {
                showAppBackupsList(slug);
            }
        } else {
            showToast('Information', (response && response.message) || 'Backup non créé', 'warning');
        }

    } catch (error) {
        console.error('Erreur déclenchement backup:', error);
        // 409 Conflict (déjà en cours) → message dédié
        const detail = error?.message || error?.detail || 'Impossible de déclencher la sauvegarde';
        if (error?.status === 409 || /en cours|conflict/i.test(detail)) {
            showToast('Sauvegarde déjà en cours', detail, 'warning');
            // Forcer une hydratation pour afficher l'état réel
            hydrateRunningBackups();
        } else {
            showToast('Erreur', detail, 'danger');
        }
    }
}
