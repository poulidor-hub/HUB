/**
 * Hub Application — Ateliers (workshops), administration
 *
 * Onglet « Ateliers » de la page admin :
 * - Tableau des ateliers détectés (marqueurs .workshop.env, cf.
 *   GET /api/admin/atelier) : app, slug -ws, ports, état des conteneurs.
 * - « Fermer tous les ateliers » : modale de confirmation (destruction des
 *   volumes -ws = données des clones) puis POST /api/admin/atelier/close-all.
 *   Résumé closed/failed affiché en passthrough (rc/output montrés tels quels
 *   si présents) ; service hôte injoignable (503) verbalisé.
 *
 * Les fonctions restent globales pour conserver la compatibilité avec
 * les handlers HTML inline (cf. autres modules admin-*).
 */

let ateliersList = [];
let ateliersLoaded = false;
let confirmCloseAteliersModal = null;

// Badge Bootstrap par état conteneurs (backend : running/partiel/stopped/
// absent/inconnu — état inattendu ⇒ badge neutre, texte affiché tel quel).
const ATELIER_STATE_BADGES = {
    running: 'success',
    partiel: 'warning text-dark',
    stopped: 'secondary',
    absent: 'dark',
    inconnu: 'secondary'
};

// ===== Chargement à l'activation de l'onglet (parité admin-network.js) =====

document.addEventListener('DOMContentLoaded', () => {
    const atelierTab = document.getElementById('atelier-tab');
    if (atelierTab) {
        atelierTab.addEventListener('shown.bs.tab', () => {
            refreshAteliers();
        });
    }
});

// ===== Chargement / rendu de la liste =====

async function refreshAteliers() {
    const loading = document.getElementById('ateliersLoading');
    const errorBox = document.getElementById('ateliersError');
    if (loading) loading.classList.remove('d-none');
    if (errorBox) errorBox.classList.add('d-none');
    try {
        const data = await api.request('/admin/atelier');
        ateliersList = Array.isArray(data && data.ateliers) ? data.ateliers : [];
        ateliersLoaded = true;
        renderAteliers(ateliersList);
    } catch (error) {
        console.error('Erreur chargement ateliers:', error);
        showAteliersError(error.message);
    } finally {
        if (loading) loading.classList.add('d-none');
    }
}

/**
 * Affiche le tableau des ateliers (ou l'état vide « Aucun atelier actif »).
 */
function renderAteliers(ateliers) {
    const container = document.getElementById('ateliersTable');
    if (!container) return;

    if (!ateliers || ateliers.length === 0) {
        container.innerHTML = `
            <div class="text-center text-muted py-4">
                <i class="bi bi-inbox display-4"></i>
                <p class="mt-2 mb-0">Aucun atelier actif</p>
            </div>
        `;
        return;
    }

    const rows = ateliers.map(a => {
        const state = (a.containers_state === null || a.containers_state === undefined)
            ? 'inconnu' : String(a.containers_state);
        const badgeClass = ATELIER_STATE_BADGES[state] || 'secondary';
        const apiPort = (a.api_port === null || a.api_port === undefined) ? '—' : `:${escapeHtml(String(a.api_port))}`;
        const frontPort = (a.frontend_port === null || a.frontend_port === undefined) ? '—' : `:${escapeHtml(String(a.frontend_port))}`;
        return `
            <tr>
                <td><strong>${escapeHtml(String(a.app ?? ''))}</strong></td>
                <td><code>${escapeHtml(String(a.ws_slug ?? ''))}</code></td>
                <td><span class="badge bg-primary">API ${apiPort}</span></td>
                <td><span class="badge bg-success">Front ${frontPort}</span></td>
                <td><span class="badge bg-${badgeClass}">${escapeHtml(state)}</span></td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="card border-0 shadow-sm">
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead>
                        <tr>
                            <th>Application</th>
                            <th>Atelier</th>
                            <th>Port API</th>
                            <th>Port Frontend</th>
                            <th>Conteneurs</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>
    `;
}

function showAteliersError(message) {
    const errorBox = document.getElementById('ateliersError');
    if (!errorBox) return;
    errorBox.textContent = message || 'Erreur inconnue';
    errorBox.classList.remove('d-none');
}

// ===== Fermeture globale (confirmée) =====

/**
 * Ouvre la modale de confirmation « Fermer tous les ateliers ».
 */
function closeAllAteliers() {
    const countEl = document.getElementById('confirmCloseAteliersCount');
    if (countEl) {
        countEl.textContent = ateliersLoaded ? String(ateliersList.length) : '?';
    }
    if (!confirmCloseAteliersModal) {
        confirmCloseAteliersModal = new bootstrap.Modal(
            document.getElementById('confirmCloseAteliersModal'));
    }
    confirmCloseAteliersModal.show();
}

/**
 * Confirmation saisie : POST close-all puis résumé closed/failed (passthrough).
 */
async function confirmCloseAllAteliers() {
    const btn = document.getElementById('confirmCloseAteliersBtn');
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Fermeture...';
    }
    try {
        // Un `down -v` d'atelier peut être long : timeout client > timeout
        // backend (settings.atelier_close_timeout, 600 s par défaut).
        const result = await api.request('/admin/atelier/close-all', {
            method: 'POST',
            timeout: 660000
        });
        if (confirmCloseAteliersModal) confirmCloseAteliersModal.hide();
        renderAteliersCloseResult(result);
        await refreshAteliers();
    } catch (error) {
        console.error('Erreur fermeture ateliers:', error);
        if (confirmCloseAteliersModal) confirmCloseAteliersModal.hide();
        showAteliersError(error.message);
        showToast('Erreur', error.message, 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }
}

/**
 * Résumé du close-all — passthrough d'affichage strict : la shape (closed,
 * failed, rc, output…) est décidée par hubctl, on n'en déduit rien.
 */
function renderAteliersCloseResult(result) {
    const box = document.getElementById('ateliersActionResult');
    if (!box) return;

    const closed = Array.isArray(result && result.closed) ? result.closed : [];
    const failed = Array.isArray(result && result.failed) ? result.failed : [];
    const allOk = failed.length === 0;

    const closedList = closed.map(_atelierItemLabel).filter(Boolean);
    const failedList = failed.map(f => {
        if (f && typeof f === 'object' && (f.slug !== undefined || f.erreur !== undefined)) {
            const err = (f.erreur === null || f.erreur === undefined) ? ''
                : (typeof f.erreur === 'string' ? f.erreur : JSON.stringify(f.erreur));
            return `${String(f.slug ?? '?')}${err ? ` — ${err}` : ''}`;
        }
        return _atelierItemLabel(f);
    }).filter(Boolean);

    const rcHtml = (result && typeof result === 'object' && result.rc !== undefined)
        ? `<div class="small text-muted mt-1">Code retour : ${escapeHtml(JSON.stringify(result.rc))}</div>` : '';
    const outputHtml = (result && typeof result === 'object'
            && typeof result.output === 'string' && result.output.trim())
        ? `<pre class="small bg-light border rounded p-2 mt-2 mb-0" style="max-height:12rem;overflow-y:auto;">${escapeHtml(result.output)}</pre>` : '';

    box.innerHTML = `
        <div class="alert ${allOk ? 'alert-success' : 'alert-warning'}">
            <h6 class="alert-heading mb-1">
                <i class="bi ${allOk ? 'bi-check-circle' : 'bi-exclamation-triangle'} me-1"></i>
                Fermeture des ateliers ${allOk ? 'terminée' : 'terminée avec des échecs'}
            </h6>
            <div><strong>${closed.length}</strong> atelier(s) fermé(s)${closedList.length ? ` : ${escapeHtml(closedList.join(', '))}` : ''}</div>
            ${failedList.length ? `<div><strong>${failedList.length}</strong> échec(s) :</div>
            <ul class="mb-0 small">${failedList.map(f => `<li>${escapeHtml(f)}</li>`).join('')}</ul>` : ''}
            ${rcHtml}
            ${outputHtml}
        </div>
    `;
    box.classList.remove('d-none');
}

/**
 * Libellé d'affichage d'un élément de closed/failed — chaîne telle quelle,
 * objet réduit à un champ évident sinon sérialisé (pas d'interprétation).
 */
function _atelierItemLabel(item) {
    if (item === null || item === undefined) return '';
    if (typeof item === 'string') return item;
    if (typeof item === 'object') {
        if (item.slug !== undefined) return String(item.slug);
        if (item.app !== undefined) return String(item.app);
        return JSON.stringify(item);
    }
    return String(item);
}
