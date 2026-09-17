/**
 * Hub Application — Suivi multi-déploiements (spéc 2026-09-17-ajout-app-fiable)
 *
 * Un seul polling global pour tous les déploiements suivis (_deployWatch).
 * La modal est purement visuelle : la fermer n'interrompt rien, un toast
 * annonce chaque fin (succès/échec). Remplace l'ancienne modal captrice
 * mono-slug (polling 2s jusqu'à 20 min).
 *
 * Pipeline inchangé côté serveur : git clone → .env → make build && make up → health.
 */

const DEPLOY_POLL_MS = 2000;
const DEPLOY_MAX_WAIT_MS = 30 * 60 * 1000; // 30 min (file d'attente + builds longs)

let _deployPollTimer = null;
const _deployWatch = new Map(); // slug -> { startedAt, lastStatus }

function _deployModalEl() {
    let modalEl = document.getElementById('deployModal');
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.className = 'modal fade';
        modalEl.id = 'deployModal';
        modalEl.tabIndex = -1;
        modalEl.innerHTML = `
            <div class="modal-dialog modal-lg modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title"><i class="bi bi-rocket-takeoff me-2"></i>Déploiements</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="deployModalBody"></div>
                    <div class="modal-footer">
                        <span class="small text-muted me-auto">Le suivi continue si vous fermez — un toast vous préviendra.</span>
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modalEl);
        // PAS de stop du polling à la fermeture : le suivi continue en fond.
    }
    return modalEl;
}

/** Enregistre un slug au suivi (idempotent) et démarre la boucle si besoin. */
async function watchDeploy(slug) {
    if (!_deployWatch.has(slug)) {
        _deployWatch.set(slug, { startedAt: Date.now(), lastStatus: null });
    }
    _startPollLoop();
}

function _stopPollLoopIfEmpty() {
    if (_deployWatch.size === 0 && _deployPollTimer) {
        clearInterval(_deployPollTimer);
        _deployPollTimer = null;
    }
}

function _startPollLoop() {
    if (_deployPollTimer) return;
    _deployPollTimer = setInterval(_pollAll, DEPLOY_POLL_MS);
    _pollAll();
}

/** Un tick = un poll de chaque slug suivi. Toast à l'état terminal. */
async function _pollAll() {
    for (const [slug, state] of Array.from(_deployWatch.entries())) {
        try {
            const payload = await api.getAppDeployStatus(slug);
            const current = payload && payload.current;
            const status = current ? current.status : null;
            const changed = status !== state.lastStatus;
            state.lastStatus = status;

            // Rafraîchir la modal si elle est ouverte (elle est optionnelle)
            const modalEl = document.getElementById('deployModal');
            if (modalEl && modalEl.classList.contains('show')) _renderDeployList();

            if ((status === 'success' || status === 'failed') && changed) {
                if (status === 'success') {
                    showToast('Déploiement terminé', slug + ' est en ligne.', 'success');
                    if (typeof loadAdminData === 'function') loadAdminData().catch(() => {});
                    if (typeof loadApps === 'function') loadApps();
                } else {
                    const step = current && current.current_step ? ' (étape : ' + current.current_step + ')' : '';
                    showToast('Déploiement en échec', slug + ' : échec' + step + ". Relancez depuis l'admin.", 'danger');
                }
                _deployWatch.delete(slug);
                _stopPollLoopIfEmpty();
                continue;
            }
            if (Date.now() - state.startedAt > DEPLOY_MAX_WAIT_MS) {
                showToast('Suivi arrêté', slug + ' : délai de suivi dépassé. Le déploiement continue en arrière-plan.', 'warning');
                _deployWatch.delete(slug);
                _stopPollLoopIfEmpty();
            }
        } catch (e) { /* erreur transitoire : retentée au tick suivant */ }
    }
}

/** Liste simple des déploiements suivis (en cours ou en file d'attente). */
function _renderDeployList() {
    const body = document.getElementById('deployModalBody');
    if (!body) return;
    if (_deployWatch.size === 0) {
        body.innerHTML = '<p class="text-muted small mb-0">Aucun déploiement en cours.</p>';
        return;
    }
    body.innerHTML = Array.from(_deployWatch.keys()).map(slug =>
        '<div class="border rounded p-2 mb-2" data-deploy-slug="' + escapeHtml(slug) + '">' +
            '<div class="d-flex justify-content-between align-items-center">' +
                '<strong class="small">' + escapeHtml(slug) + '</strong>' +
                '<span class="badge bg-primary"><span class="spinner-border spinner-border-sm me-1"></span>Déploiement</span>' +
            '</div>' +
            '<div class="progress mt-2" style="height:4px;">' +
                '<div class="progress-bar progress-bar-striped progress-bar-animated w-100"></div>' +
            '</div>' +
            '<div class="text-muted" style="font-size:12px;">En cours ou en file d\'attente (pool de déploiements)…</div>' +
        '</div>').join('');
}

/** Ouvre la modal et suit le slug. Fermer la modal n'arrête pas le suivi. */
async function openDeployModal(slug) {
    await watchDeploy(slug);
    _renderDeployList();
    bootstrap.Modal.getOrCreateInstance(_deployModalEl()).show();
}

async function triggerDeploy(slug) {
    try {
        const resp = await api.deployApp(slug);
        await openDeployModal(slug);
        return true;
    } catch (e) {
        showToast('Déploiement', e.message, 'warning');
        return false;
    }
}
