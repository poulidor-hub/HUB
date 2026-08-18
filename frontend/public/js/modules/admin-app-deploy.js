/**
 * Hub Application — Déploiement automatique des apps
 * Modal de suivi temps réel (polling 2s) + bouton « Déployer » des apps non déployées.
 * Spéc : docs/superpowers/specs/2026-08-18-auto-deploy-apps-design.md
 */

const DEPLOY_STEPS = [
    { key: 'clone',  label: 'Récupération du code (git)' },
    { key: 'env',    label: 'Configuration .env' },
    { key: 'build',  label: 'Construction des images' },
    { key: 'up',     label: 'Démarrage des conteneurs' },
    { key: 'health', label: 'Vérification de santé' }
];
const DEPLOY_POLL_MS = 2000;
const DEPLOY_MAX_WAIT_MS = 20 * 60 * 1000; // 20 min (builds longs)

let _deployPollTimer = null;
let _deploySlug = null;

function _deployModalEl(slug) {
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
                        <h5 class="modal-title"><i class="bi bi-rocket-takeoff me-2"></i>Déploiement</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="deployModalBody"></div>
                    <div class="modal-footer" id="deployModalFooter">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modalEl);
        modalEl.addEventListener('hidden.bs.modal', () => {
            if (_deployPollTimer) { clearInterval(_deployPollTimer); _deployPollTimer = null; }
        });
    }
    document.querySelector('#deployModal .modal-title').innerHTML =
        '<i class="bi bi-rocket-takeoff me-2"></i>Déploiement — ' + escapeHtml(slug);
    return modalEl;
}

function _renderDeploySteps(current, status) {
    const terminalFail = (status === 'failed');
    const done = (status === 'success');
    const currentIdx = DEPLOY_STEPS.findIndex(s => s.key === current);
    return '<div class="mb-3">' + DEPLOY_STEPS.map((s, i) => {
        let icon = '<i class="bi bi-circle text-muted"></i>';
        if (done || (i < currentIdx && currentIdx >= 0)) icon = '<i class="bi bi-check-circle-fill text-success"></i>';
        else if (terminalFail && i === currentIdx) icon = '<i class="bi bi-x-circle-fill text-danger"></i>';
        else if (i === currentIdx) icon = '<div class="spinner-border spinner-border-sm text-primary" role="status"></div>';
        return '<div class="d-flex align-items-center gap-2 py-1">' + icon + '<span>' + escapeHtml(s.label) + '</span></div>';
    }).join('') + '</div>';
}

function _renderDeployLogs(logs) {
    const text = logs || '(pas encore de logs)';
    return '<pre class="mb-0" style="background:#1e293b;color:#e2e8f0;padding:10px;border-radius:6px;font-size:11px;max-height:40vh;overflow:auto;">' + escapeHtml(text) + '</pre>';
}

function _renderDeployModalBody(payload) {
    const current = payload.current;
    if (!current) {
        document.getElementById('deployModalBody').innerHTML =
            '<div class="text-muted">Aucun déploiement enregistré.</div>';
        return;
    }
    let html = _renderDeploySteps(current.current_step, current.status);
    if (current.status === 'failed') {
        html += '<div class="alert alert-danger py-2"><i class="bi bi-exclamation-triangle-fill me-1"></i>Déploiement échoué — consultez les logs puis réessayez.</div>';
    }
    if (current.status === 'success') {
        html += '<div class="alert alert-success py-2"><i class="bi bi-check-circle-fill me-1"></i>Application en ligne. Elle s\'enregistrera automatiquement auprès du HUB (M2M).</div>';
    }
    html += '<h6 class="text-muted small">Logs</h6>' + _renderDeployLogs(current.logs);
    document.getElementById('deployModalBody').innerHTML = html;

    const footer = document.getElementById('deployModalFooter');
    const existingRetry = document.getElementById('deployRetryBtn');
    if (existingRetry) existingRetry.remove();
    if (current.status === 'failed') {
        const retry = document.createElement('button');
        retry.id = 'deployRetryBtn';
        retry.className = 'btn btn-warning';
        retry.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Réessayer';
        retry.onclick = () => triggerDeploy(_deploySlug || current.slug);
        footer.insertBefore(retry, footer.firstChild);
    }
}

function openDeployModal(slug) {
    _deploySlug = slug;
    const modalEl = _deployModalEl(slug);
    document.getElementById('deployModalBody').innerHTML =
        '<div class="text-center py-4"><div class="spinner-border text-primary" role="status"></div><p class="mt-2 text-muted">Initialisation du déploiement…</p></div>';
    bootstrap.Modal.getOrCreateInstance(modalEl).show();

    if (_deployPollTimer) clearInterval(_deployPollTimer);
    const startedAt = Date.now();
    let stop = false;
    const stopPolling = () => { if (_deployPollTimer) { clearInterval(_deployPollTimer); _deployPollTimer = null; } };

    _deployPollTimer = setInterval(async () => {
        if (stop) return;
        try {
            const payload = await api.getAppDeployStatus(slug);
            if (payload.current) payload.current.slug = slug;
            _renderDeployModalBody(payload);
            if (payload.current && (payload.current.status === 'success' || payload.current.status === 'failed')) {
                stop = true; stopPolling();
                if (payload.current.status === 'success') {
                    if (typeof loadAdminData === 'function') await loadAdminData();
                    if (typeof loadApps === 'function') loadApps();
                }
            }
        } catch (e) { /* erreur transitoire : on retente au tick suivant */ }
        if (Date.now() - startedAt > DEPLOY_MAX_WAIT_MS) {
            stop = true; stopPolling();
            showToast('Attention', 'Suivi arrêté : délai dépassé (20 min). Le déploiement continue en arrière-plan.', 'warning');
        }
    }, DEPLOY_POLL_MS);
}

async function triggerDeploy(slug) {
    try {
        const resp = await api.deployApp(slug);
        openDeployModal(slug);
        return true;
    } catch (e) {
        showToast('Erreur de déploiement', e.message, 'danger');
        return false;
    }
}
