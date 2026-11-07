// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Ateliers (workshops), administration
 *
 * Onglet « Ateliers » de la page admin :
 * - Tableau des ateliers détectés (marqueurs .workshop.env, cf.
 *   GET /api/admin/atelier) : app, slug -ws, ports, état des conteneurs.
 * - « Fiches fantômes » (ghosts) : entrées DB is_workshop sans marqueur
 *   disque — sélection + purge via POST /api/admin/atelier/reconcile.
 * - « Projets Docker orphelins » (orphans) : projets compose -ws sans
 *   marqueur, strictement informatif (non pilotables depuis cette vue).
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
        renderAteliers(ateliersList, data && data.ghosts, data && data.orphans);
    } catch (error) {
        console.error('Erreur chargement ateliers:', error);
        showAteliersError(error.message);
    } finally {
        if (loading) loading.classList.add('d-none');
    }
}

/**
 * Rendu de l'onglet : tableau des ateliers (ou état vide « Aucun atelier
 * actif »), puis — uniquement si le backend en signale — les sections
 * « Fiches fantômes » et « Projets Docker orphelins ». Rien à signaler
 * ⇒ aucune section supplémentaire.
 */
function renderAteliers(ateliers, ghosts, orphans) {
    const container = document.getElementById('ateliersTable');
    if (!container) return;

    const ghostsList = Array.isArray(ghosts) ? ghosts : [];
    const orphansList = Array.isArray(orphans) ? orphans : [];

    let tableHtml;
    if (!ateliers || ateliers.length === 0) {
        tableHtml = `
            <div class="text-center text-muted py-4">
                <i class="bi bi-inbox display-4"></i>
                <p class="mt-2 mb-0">Aucun atelier actif</p>
            </div>
        `;
    } else {
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

        tableHtml = `
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

    container.innerHTML = [
        tableHtml,
        ghostsList.length ? _renderAtelierGhostsSection(ghostsList) : '',
        orphansList.length ? _renderAtelierOrphansSection(orphansList) : ''
    ].join('');
}

/**
 * Section « Fiches fantômes » : fiches DB is_workshop sans marqueur disque.
 * Cases pré-cochées (purge complète par défaut) + bouton « Purger la
 * sélection » — cf. purgeAtelierGhosts() → POST /reconcile.
 */
function _renderAtelierGhostsSection(ghosts) {
    const items = ghosts.map(g => {
        const slug = String((g && g.slug) ?? '?');
        const name = String((g && g.name) ?? '');
        const created = _atelierGhostDate(g && g.created_at);
        const inputId = `atelierGhost-${slug}`;
        return `
            <li class="list-group-item">
                <div class="form-check mb-0">
                    <input class="form-check-input atelier-ghost-check" type="checkbox"
                        id="${escapeHtml(inputId)}" value="${escapeHtml(slug)}" checked>
                    <label class="form-check-label" for="${escapeHtml(inputId)}">
                        <code>${escapeHtml(slug)}</code>${name ? ` — ${escapeHtml(name)}` : ''}${created ? ` <small class="text-muted">(créée le ${escapeHtml(created)})</small>` : ''}
                    </label>
                </div>
            </li>
        `;
    }).join('');

    return `
        <div class="card border-warning shadow-sm mb-3">
            <div class="card-header bg-warning bg-opacity-25 d-flex justify-content-between align-items-center flex-wrap gap-2">
                <h6 class="mb-0">
                    <i class="bi bi-exclamation-triangle me-1"></i>
                    Fiches fantômes <span class="badge bg-warning text-dark ms-1">${ghosts.length}</span>
                </h6>
                <button class="btn btn-sm btn-outline-danger" data-action="purgeAtelierGhosts"
                    title="Supprimer définitivement les fiches DB cochées (aucun marqueur disque)">
                    <i class="bi bi-trash me-1"></i>Purger la sélection
                </button>
            </div>
            <ul class="list-group list-group-flush">${items}</ul>
            <div class="card-footer small text-muted">
                Fiches marquées atelier en base mais sans marqueur <code>.workshop.env</code> sur disque — la purge supprime l'entrée DB, définitivement.
            </div>
        </div>
    `;
}

/**
 * Date de création d'une fiche fantôme — ISO backend affiché en local,
 * valeur brute telle quelle si non parsable (pas d'interprétation).
 */
function _atelierGhostDate(created) {
    if (created === null || created === undefined || created === '') return '';
    const parsed = new Date(created);
    return Number.isNaN(parsed.getTime()) ? String(created) : parsed.toLocaleString('fr-FR');
}

/**
 * Section « Projets Docker orphelins » : projets compose -ws qui tournent
 * sans marqueur. Strictement informatif — aucune action depuis cette vue.
 */
function _renderAtelierOrphansSection(orphans) {
    const items = orphans.map(o => {
        const project = String((o && o.project) ?? '?');
        const containers = Array.isArray(o && o.containers) ? o.containers : [];
        const running = (o && typeof o.running === 'number') ? o.running : 0;
        return `
            <li class="list-group-item d-flex justify-content-between align-items-center flex-wrap gap-2">
                <div>
                    <code>${escapeHtml(project)}</code>${containers.length ? ` <small class="text-muted ms-1">(${escapeHtml(containers.join(', '))})</small>` : ''}
                </div>
                <span class="badge bg-secondary">${running} conteneur(s) actif(s)</span>
            </li>
        `;
    }).join('');

    return `
        <div class="card border-0 shadow-sm mb-3">
            <div class="card-header">
                <h6 class="mb-0">
                    <i class="bi bi-box-seam me-1"></i>
                    Projets Docker orphelins <span class="badge bg-secondary ms-1">${orphans.length}</span>
                </h6>
            </div>
            <ul class="list-group list-group-flush">${items}</ul>
            <div class="card-footer small text-muted">
                <i class="bi bi-info-circle me-1"></i>
                Ces projets compose tournent sans marqueur <code>.workshop.env</code> : ils ne sont pas pilotables depuis cette vue.
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

// ===== Purge des fiches fantômes (confirmée) =====

/**
 * Purge les fiches fantômes cochées : confirm() explicite (définitif) puis
 * POST /api/admin/atelier/reconcile {slugs}. Toast de synthèse purged/
 * skipped/errors, bandeau de détail si skipped/errors, puis
 * refreshAteliers() pour rafraîchir tableau + sections.
 */
async function purgeAtelierGhosts() {
    const container = document.getElementById('ateliersTable');
    const checked = container
        ? Array.from(container.querySelectorAll('input.atelier-ghost-check:checked'))
            .map(cb => cb.value)
            .filter(Boolean)
        : [];

    if (checked.length === 0) {
        showToast('Fiches fantômes', 'Aucune fiche cochée — rien à purger.', 'info');
        return;
    }

    const countLabel = checked.length === 1 ? '1 fiche fantôme' : `${checked.length} fiches fantômes`;
    // Purge d'entrées DB définitive : confirmation explicite obligatoire.
    if (!confirm(`Purger ${countLabel} — définitif ? Cette opération est irréversible.`)) {
        return;
    }

    const btn = container.querySelector('button[data-action="purgeAtelierGhosts"]');
    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Purge...';
    }
    try {
        const result = await api.request('/admin/atelier/reconcile', {
            method: 'POST',
            body: JSON.stringify({ slugs: checked })
        });
        const purged = Array.isArray(result && result.purged) ? result.purged : [];
        const skipped = Array.isArray(result && result.skipped) ? result.skipped : [];
        const errors = Array.isArray(result && result.errors) ? result.errors : [];
        const allOk = skipped.length === 0 && errors.length === 0;

        const parts = [`${purged.length} purgée(s)`];
        if (skipped.length) parts.push(`${skipped.length} ignorée(s)`);
        if (errors.length) parts.push(`${errors.length} erreur(s)`);
        showToast('Purge des fiches fantômes', parts.join(' — '), allOk ? 'success' : 'warning');
        if (!allOk) {
            _renderAtelierReconcileResult(skipped, errors);
        }
        await refreshAteliers();
    } catch (error) {
        console.error('Erreur purge fiches fantômes:', error);
        showToast('Erreur', error.message, 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    }
}

/**
 * Détail skipped/errors du reconcile — même idiome d'affichage que le
 * bandeau de résultat du close-all (listes brutes, pas d'interprétation).
 */
function _renderAtelierReconcileResult(skipped, errors) {
    const box = document.getElementById('ateliersActionResult');
    if (!box) return;

    const skipList = skipped.map(s => {
        const slug = String((s && s.slug) ?? '?');
        const reason = String((s && s.reason) ?? '');
        return reason ? `${slug} — ${reason}` : slug;
    });
    const errList = errors.map(e => {
        const slug = String((e && e.slug) ?? '?');
        const error = String((e && e.error) ?? '');
        return error ? `${slug} — ${error}` : slug;
    });

    box.innerHTML = `
        <div class="alert alert-warning">
            <h6 class="alert-heading mb-1">
                <i class="bi bi-exclamation-triangle me-1"></i>
                Purge terminée avec des exclusions
            </h6>
            ${skipList.length ? `<div><strong>${skipList.length}</strong> fiche(s) ignorée(s) :</div>
            <ul class="mb-0 small">${skipList.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul>` : ''}
            ${errList.length ? `<div><strong>${errList.length}</strong> erreur(s) :</div>
            <ul class="mb-2 small">${errList.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        </div>
    `;
    box.classList.remove('d-none');
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
