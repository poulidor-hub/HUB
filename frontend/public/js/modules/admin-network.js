/**
 * Hub Application — Administration réseau (admin)
 *
 * Onglet « Réseau » :
 * - Carte IP du Hub : état (source db/env/auto), changement d'IP validé
 *   par l'admin (PUT /api/config/network/hub-host), retour à l'auto-détection.
 * - Plan de propagation : apps v2 enregistrées avec l'IP de leur .env vs
 *   l'IP effective ; propagation de la sélection (POST apply) puis
 *   recréation des conteneurs par app (POST /{slug}/recreate, opération
 *   tracée, polling du statut).
 *
 * Les fonctions restent globales pour conserver la compatibilité avec
 * les handlers HTML inline (cf. autres modules admin-*).
 */

let networkState = null;
let networkPlan = null;
const _networkSelected = new Set();

// ===== Chargement à l'activation de l'onglet =====

document.addEventListener('DOMContentLoaded', () => {
    const networkTab = document.getElementById('network-tab');
    if (networkTab) {
        networkTab.addEventListener('shown.bs.tab', () => {
            loadNetworkAdmin();
        });
    }
    const checkAll = document.getElementById('networkCheckAll');
    if (checkAll) {
        checkAll.addEventListener('change', () => {
            document.querySelectorAll('#networkPlanBody input[type="checkbox"]')
                .forEach(cb => {
                    cb.checked = checkAll.checked;
                    _updateNetworkSelection(cb.dataset.slug, cb.checked);
                });
        });
    }
});

// ===== Chargement =====

async function loadNetworkAdmin() {
    await Promise.all([loadNetworkState(), loadNetworkPlan()]);
}

async function loadNetworkState() {
    try {
        networkState = await api.getNetworkState();
        renderNetworkState(networkState);
    } catch (error) {
        console.error('Erreur chargement état réseau:', error);
        showToast('Erreur', `Chargement de l'état réseau : ${error.message}`, 'error');
    }
}

function _sourceBadge(source) {
    if (source === 'db') return '<span class="badge bg-success">admin (DB)</span>';
    if (source === 'env') return '<span class="badge bg-info text-dark">env HUB_HOST</span>';
    return '<span class="badge bg-secondary">auto-détectée</span>';
}

function renderNetworkState(state) {
    document.getElementById('networkHubHost').textContent = state.hub_host || '—';
    document.getElementById('networkSourceBadge').innerHTML = _sourceBadge(state.source);
    document.getElementById('networkDetected').textContent = state.detected_ip || '—';
    document.getElementById('networkEnvValue').textContent = state.env_value || '(non définie)';
    document.getElementById('networkInstanceId').textContent =
        (state.instance_id || '—').slice(0, 8) + '…';
    document.getElementById('networkPorts').textContent =
        `API :${state.api_port} · Frontend :${state.frontend_port}`;
}

async function loadNetworkPlan() {
    const loadingEl = document.getElementById('networkPlanLoading');
    const errorEl = document.getElementById('networkPlanError');
    const contentEl = document.getElementById('networkPlanContent');

    loadingEl.classList.remove('d-none');
    errorEl.classList.add('d-none');
    contentEl.classList.add('d-none');
    _networkSelected.clear();
    _updateApplyButton();

    try {
        networkPlan = await api.getPropagationPlan();
        loadingEl.classList.add('d-none');
        renderNetworkPlan(networkPlan);
        contentEl.classList.remove('d-none');
    } catch (error) {
        console.error('Erreur chargement plan de propagation:', error);
        loadingEl.classList.add('d-none');
        errorEl.classList.remove('d-none');
        errorEl.textContent = `Chargement du plan : ${error.message}`;
    }
}

function _statusBadge(status) {
    if (status === 'up_to_date') return '<span class="badge bg-success">à jour</span>';
    if (status === 'pending') return '<span class="badge bg-warning text-dark">à propager</span>';
    return '<span class="badge bg-secondary">sans .env</span>';
}

function renderNetworkPlan(plan) {
    const bodyEl = document.getElementById('networkPlanBody');
    const excludedEl = document.getElementById('networkExcluded');
    const pending = plan.apps.filter(a => a.status === 'pending').length;

    if (plan.apps.length === 0) {
        bodyEl.innerHTML = '<tr><td colspan="5" class="text-muted">Aucune application v2 enregistrée.</td></tr>';
    } else {
        bodyEl.innerHTML = plan.apps.map(a => `
            <tr>
                <td>${a.status === 'no_env' ? '' :
                    `<input type="checkbox" class="form-check-input network-app-check"
                            data-slug="${escapeHtml(a.slug)}" ${a.status === 'pending' ? '' : ''}
                            onchange="_updateNetworkSelection('${escapeHtml(a.slug)}', this.checked)">`}</td>
                <td>${escapeHtml(a.slug)}${a.is_active ? '' : ' <span class="badge bg-light text-dark">inactive</span>'}</td>
                <td>${_statusBadge(a.status)}</td>
                <td>${a.current_hub_host ? escapeHtml(a.current_hub_host) : '<span class="text-muted">—</span>'}</td>
                <td class="text-end">
                    ${a.status !== 'no_env' ? `
                    <button class="btn btn-outline-secondary btn-sm" data-action="recreateNetworkApp" data-arg1="${escapeHtml(a.slug)}"
                            title="Recrée les conteneurs pour prendre en charge le .env">
                        <i class="bi bi-arrow-repeat me-1"></i>Recréer
                    </button>` : ''}
                </td>
            </tr>`).join('');
    }

    excludedEl.innerHTML = plan.excluded.length
        ? 'Non couvertes : ' + plan.excluded
            .map(e => `<strong>${escapeHtml(e.slug)}</strong> (${escapeHtml(e.reason)})`)
            .join(', ')
        : '';

    // Badge d'onglet : nombre d'apps à propager
    const badge = document.getElementById('networkPendingBadge');
    if (pending > 0) {
        badge.textContent = pending;
        badge.classList.remove('d-none');
    } else {
        badge.classList.add('d-none');
    }
    _updateApplyButton();
}

function _updateNetworkSelection(slug, checked) {
    if (checked) _networkSelected.add(slug);
    else _networkSelected.delete(slug);
    _updateApplyButton();
}

function _updateApplyButton() {
    const btn = document.getElementById('networkApplyBtn');
    if (!btn) return;
    const count = _networkSelected.size;
    document.getElementById('networkApplyCount').textContent = count;
    btn.disabled = count === 0;
}

// ===== Changement d'IP =====

async function showChangeHubHostModal() {
    const state = networkState || await api.getNetworkState();
    const modal = _showModal('Modifier l\'IP du Hub',
        `<div class="mb-3">
            <label class="form-label">Nouvelle IP / hostname (IP ou hostname du Hub vu par les applications)</label>
            <div class="input-group">
                <input type="text" class="form-control" id="networkNewHostInput"
                       value="${escapeHtml(state.db_value || state.hub_host || '')}"
                       placeholder="ex. 192.168.1.99">
                <button class="btn btn-outline-secondary" type="button" data-action="detectHubHost">
                    <i class="bi bi-magic me-1"></i>Détecter
                </button>
            </div>
            <div class="form-text">
                IP auto-détectée du serveur : <strong>${escapeHtml(state.detected_ip || '—')}</strong>.
                ⚠️ Hub conteneurisé : l'auto-détection peut retourner l'IP Docker interne —
                préférez la saisie manuelle de l'IP LAN vue par les applications.
                La valeur est appliquée immédiatement et persistée ; la propagation
                aux applications reste un second temps, validé par vous.
            </div>
        </div>
        <div class="text-end">
            <button class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
            <button class="btn btn-primary" data-action="submitChangeHubHost">
                <i class="bi bi-check-lg me-1"></i>Appliquer
            </button>
        </div>`);
    return modal;
}

function detectHubHost() {
    api.getNetworkState().then(state => {
        const input = document.getElementById('networkNewHostInput');
        if (input && state.detected_ip) input.value = state.detected_ip;
    });
}

async function submitChangeHubHost() {
    const input = document.getElementById('networkNewHostInput');
    const host = (input?.value || '').trim();
    if (!host) {
        showToast('Erreur', 'Renseignez une IP ou un hostname', 'error');
        return;
    }
    try {
        const state = await api.setHubHost(host);
        bootstrap.Modal.getInstance(document.querySelector('.modal.show'))?.hide();
        networkState = state;
        renderNetworkState(state);
        showToast('IP du Hub mise à jour',
            `Nouvelle IP effective : ${state.hub_host} — pensez à propager aux applications.`,
            'success');
        await loadNetworkPlan();
    } catch (error) {
        showToast('Erreur', `Changement d'IP : ${error.message}`, 'error');
    }
}

async function unsetHubHostAction() {
    try {
        const state = await api.unsetHubHost();
        networkState = state;
        renderNetworkState(state);
        showToast('Auto-détection', `IP effective : ${state.hub_host} (source : ${state.source})`, 'success');
        await loadNetworkPlan();
    } catch (error) {
        showToast('Erreur', `Retour à l'auto-détection : ${error.message}`, 'error');
    }
}

// ===== Propagation =====

async function applyNetworkPropagation() {
    const slugs = [..._networkSelected];
    if (slugs.length === 0) return;
    const effectiveHost = networkPlan?.hub_host || '?';

    _showModal('Confirmer la propagation',
        `<p>Écrire <strong>HUB_HOST=${escapeHtml(effectiveHost)}</strong> dans le <code>.env</code> de
         <strong>${slugs.length} application(s)</strong> ?</p>
         <p class="text-muted small">${escapeHtml(slugs.join(', '))}</p>
         <p class="text-muted small">Les conteneurs continueront sur l'ancienne IP
         jusqu'à leur recréation (bouton « Recréer »).</p>
         <div class="text-end">
            <button class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
            <button class="btn btn-primary" data-action="confirmApplyPropagation">
                <i class="bi bi-cloud-arrow-up me-1"></i>Propager
            </button>
         </div>`);
}

async function confirmApplyPropagation() {
    bootstrap.Modal.getInstance(document.querySelector('.modal.show'))?.hide();
    try {
        const result = await api.applyPropagationPlan([..._networkSelected]);
        const written = result.applied.filter(a => a.status === 'written').length;
        const problems = result.applied.filter(a => a.status !== 'written');
        showToast('Propagation appliquée',
            `${written} app(s) mise(s) à jour vers ${result.hub_host}` +
            (problems.length ? ` — ${problems.length} problème(s), voir le plan.` : '.'),
            problems.length ? 'warning' : 'success');
        _networkSelected.clear();
        await loadNetworkPlan();
    } catch (error) {
        showToast('Erreur', `Propagation : ${error.message}`, 'error');
    }
}

// ===== Recréation des conteneurs =====

async function recreateNetworkApp(slug) {
    try {
        const r = await api.recreateApp(slug);
        showToast('Recréation lancée',
            `${slug} : opération ${r.operation_id} en cours (up --force-recreate + health).`,
            'info');
        await _pollRecreateStatus(slug, r.operation_id);
        await loadNetworkPlan();
    } catch (error) {
        showToast('Erreur', `Recréation de ${slug} : ${error.message}`, 'error');
    }
}

async function _pollRecreateStatus(slug, operationId) {
    // Statut via l'endpoint atelier (générique par app) — 2 s, max 3 min
    const maxPolls = 90;
    for (let i = 0; i < maxPolls; i++) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        try {
            const st = await api.getWorkshopStatus(slug);
            const current = st?.current;
            if (!current || current.id !== operationId) continue;
            if (current.status === 'success') {
                showToast('Recréation terminée', `${slug} : conteneurs recréés et healthy.`, 'success');
                return;
            }
            if (current.status === 'failed') {
                showToast('Recréation échouée', `${slug} : voir les logs de l'opération.`, 'error');
                return;
            }
        } catch (error) {
            console.warn('Polling recréation:', error);
        }
    }
    showToast('Recréation', `${slug} : statut non finalisé après 3 min.`, 'warning');
}
