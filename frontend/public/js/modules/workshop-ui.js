// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Atelier (workshop) — bloc d'état + actions sur les cartes de la vue santé.
 * Contrat .workshop.env (hubctl v0.6.1) : WS_REF @ WS_COMMIT + version.
 * La modal de progression (openWorkshopModal) est définie dans ce module
 * et partagée par les 4 opérations (pattern admin-app-deploy.js).
 */
window._workshopStates = window._workshopStates || {};

async function loadWorkshopStates(slugs) {
    // Charge en parallèle l'état atelier de chaque slug, puis met à jour les blocs.
    var results = await Promise.all(slugs.map(function(slug) {
        return api.getWorkshopState(slug).then(function(st) { return [slug, st]; })
            .catch(function() { return [slug, null]; });
    }));
    results.forEach(function(pair) {
        window._workshopStates[pair[0]] = pair[1];
        var block = document.getElementById('workshop-block-' + pair[0]);
        if (!block) return;
        block.innerHTML = pair[1] ? renderWorkshopBlockInner(pair[0], pair[1]) : '';
        // Pas d'atelier → bloc masqué (sinon ~16px d'espace mort sur la carte)
        block.style.display = block.innerHTML ? '' : 'none';
        // Filtre « atelier actif » : tenir l'attribut data-ws de la carte à jour
        var col = document.querySelector('#appsHealthGrid > [data-slug="' + pair[0] + '"]');
        if (col) {
            col.setAttribute('data-ws', (pair[1] && pair[1].exists) ? '1' : '0');
            if (typeof _applyHealthFilters === 'function' && _healthFilterWs) _applyHealthFilters();
        }
    });
}

function _renderGitConfigButton(slug) {
    return '<button data-action="showGitConfigModal" data-arg1="' + escapeHtml(slug) + '"'
        + ' class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:11px;"'
        + ' title="Renseigner l\'URL du dépôt git (HTTPS) pour activer l\'atelier">'
        + 'Configurer le dépôt git</button>';
}

function renderWorkshopBlockInner(slug, state) {
    if (!state.exists) {
        if (!state.git_ok) return _renderGitConfigButton(slug);
        return '<button data-action="createWorkshopUI" data-arg1="' + escapeHtml(slug) + '"'
            + ' class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:11px;">'
            + 'Créer l\'atelier</button>';
    }
    var ref = state.ws_ref || '?';
    var commit = (state.ws_commit || '').slice(0, 8) || '?';
    var version = state.version ? ' · ' + escapeHtml(state.version) : '';
    var busy = state.active_operation ? ' disabled' : '';
    // Sans remote git (cas dégénéré, ex. canari) : maj/ship impossibles —
    // boutons désactivés avec explication, seule la suppression reste active.
    var noGit = state.git_ok ? '' : ' disabled title="Impossible : cette app n\'a pas de repo git avec remote origin"';
    var noGitNote = state.git_ok ? '' :
        '<div class="text-muted" style="font-size:10px;">Sans remote git : mise à jour et déploiement indisponibles.</div>'
        + _renderGitConfigButton(slug);
    return '<span class="badge text-bg-secondary me-1" title="Atelier actif (contrat .workshop.env)">'
        + 'Atelier : ' + escapeHtml(ref) + ' @ ' + escapeHtml(commit) + version + '</span>'
        + noGitNote
        + '<div style="display:flex;gap:4px;margin-top:4px;">'
        + '<button data-action="updateWorkshopUI" data-arg1="' + escapeHtml(slug) + '"' + busy + noGit
        + ' class="btn btn-sm btn-outline-primary py-0 px-2" style="font-size:11px;"'
        + ' title="Récupérer origin/main et rebuild l\'atelier (clone de DB)">Mettre à jour</button>'
        + '<button data-action="shipWorkshopUI" data-arg1="' + escapeHtml(slug) + '"' + busy + noGit
        + ' class="btn btn-sm btn-outline-success py-0 px-2" style="font-size:11px;"'
        + ' title="Déployer origin/main validé en prod, puis supprimer l\'atelier">Déployer en prod</button>'
        + '<button data-action="removeWorkshopUI" data-arg1="' + escapeHtml(slug) + '"' + busy
        + ' class="btn btn-sm btn-outline-danger py-0 px-2" style="font-size:11px;"'
        + ' title="Supprimer l\'atelier (down -v : conteneurs + volume cloné)">Supprimer</button>'
        + '</div>';
}

function renderWorkshopBlock(slug) {
    // Rendu initial (placeholder) — rempli par loadWorkshopStates.
    return '<div id="workshop-block-' + escapeHtml(slug) + '" style="margin-top:6px;"></div>';
}

async function createWorkshopUI(slug) {
    if (!confirm('Créer l\'atelier de ' + slug + ' ?\n(clone de la DB de prod + 2e instance sur ports dédiés)')) return;
    await _triggerWorkshopOp(slug, 'create');
}

async function updateWorkshopUI(slug) {
    if (!confirm('Mettre à jour l\'atelier de ' + slug + ' sur origin/main ?\n(git fetch + rebuild — les migrations rejouent sur le clone de DB, la prod n\'est pas touchée)')) return;
    await _triggerWorkshopOp(slug, 'update');
}

async function shipWorkshopUI(slug) {
    var st = window._workshopStates[slug] || {};
    var commit = (st.ws_commit || '').slice(0, 8) || '?';
    if (!confirm('Déployer en production ?\n\nCommit testé en atelier : ' + commit
        + '\nLa prod passera sur origin/main @ ' + commit
        + ' et l\'atelier sera SUPPRIMÉ après un déploiement réussi.')) return;
    await _triggerWorkshopOp(slug, 'ship');
}

async function removeWorkshopUI(slug) {
    if (!confirm('Supprimer l\'atelier de ' + slug + ' ?\n\nATTENTION : down -v — conteneurs ET volume de DB cloné supprimés (la prod n\'est pas touchée).')) return;
    await _triggerWorkshopOp(slug, 'remove');
}

async function _triggerWorkshopOp(slug, operation, ref) {
    try {
        var result;
        if (operation === 'create') result = await api.createWorkshop(slug, ref);
        else if (operation === 'update') result = await api.updateWorkshop(slug, ref);
        else if (operation === 'ship') result = await api.shipWorkshop(slug);
        else result = await api.removeWorkshop(slug);
        openWorkshopModal(slug, operation, result.operation_id);
    } catch (error) {
        showToast('Atelier', error.message, 'danger');
    }
}

// ─── Modal de progression (pattern admin-app-deploy.js) ───────────────

var WORKSHOP_STEPS = {
    create: [
        { key: 'allocate', label: 'Ports + subnet atelier' },
        { key: 'clone-db', label: 'Clone de la DB de prod' },
        { key: 'build', label: 'Build de l\'atelier' },
        { key: 'up', label: 'Démarrage de l\'atelier' },
        { key: 'health', label: 'Santé de l\'atelier' }
    ],
    update: [
        { key: 'fetch', label: 'git fetch origin' },
        { key: 'checkout', label: 'Checkout du commit' },
        { key: 'build', label: 'Rebuild de l\'atelier' },
        { key: 'up', label: 'Redémarrage de l\'atelier' },
        { key: 'health', label: 'Santé de l\'atelier' }
    ],
    ship: [
        { key: 'guards', label: 'Garde-fous (atelier sain, commit testé)' },
        { key: 'build', label: 'Build prod' },
        { key: 'up', label: 'Démarrage prod' },
        { key: 'health', label: 'Santé prod' },
        { key: 'cleanup', label: 'Suppression de l\'atelier' }
    ],
    remove: [
        { key: 'down', label: 'down -v (conteneurs + volume cloné)' },
        { key: 'cleanup', label: 'Suppression de la config' }
    ]
};
var WORKSHOP_POLL_MS = 2000;
var WORKSHOP_MAX_WAIT_MS = 20 * 60 * 1000;
var WORKSHOP_POLL_MAX_ERRORS = 5;
var WORKSHOP_LABELS = {
    create: 'Création de l\'atelier', update: 'Mise à jour de l\'atelier',
    ship: 'Déploiement en production', remove: 'Suppression de l\'atelier'
};
var _workshopPollTimer = null;
var _workshopPollErrors = 0;

function _renderWorkshopSteps(steps, current, status) {
    var idx = -1;
    for (var i = 0; i < steps.length; i++) {
        if (steps[i].key === current) { idx = i; break; }
    }
    if (status === 'success') idx = steps.length;
    var html = '<div class="mb-2">';
    steps.forEach(function(step, i) {
        var icon = '<span>○</span>';
        if (status === 'failed' && i === idx) icon = '<span class="text-danger">✗</span>';
        else if (i < idx) icon = '<span class="text-success">✓</span>';
        else if (i === idx) icon = '<span class="spinner-border spinner-border-sm"></span>';
        html += '<div>' + icon + ' ' + escapeHtml(step.label) + '</div>';
    });
    return html + '</div>';
}

function _renderWorkshopBody(slug, operation, payload) {
    var steps = WORKSHOP_STEPS[operation] || [];
    var cur = payload && payload.current;
    var html = '<h6>' + escapeHtml(slug) + ' — ' + WORKSHOP_LABELS[operation] + '</h6>';
    if (!cur) return html + '<div class="text-muted">Aucune opération en cours.</div>';
    html += _renderWorkshopSteps(steps, cur.current_step, cur.status);
    if (cur.status === 'failed') {
        html += '<div class="alert alert-danger py-1">Opération échouée — détails dans les logs ci-dessous.</div>';
    } else if (cur.status === 'success') {
        html += '<div class="alert alert-success py-1">Opération réussie.</div>';
    }
    if (cur.logs) {
        html += '<pre class="bg-dark text-light p-2" style="max-height:260px;overflow:auto;font-size:11px;">'
            + escapeHtml(cur.logs) + '</pre>';
    }
    return html;
}

function closeWorkshopModal() {
    if (_workshopPollTimer) { clearInterval(_workshopPollTimer); _workshopPollTimer = null; }
    var m = document.getElementById('appsHealthModal');
    if (m) m.remove();
}

function openWorkshopModal(slug, operation, operationId) {
    var title = WORKSHOP_LABELS[operation] || 'Atelier';
    var modal = _showModal(title, '<div class="text-center"><span class="spinner-border"></span></div>');
    var startedAt = Date.now();
    if (_workshopPollTimer) clearInterval(_workshopPollTimer);
    _workshopPollErrors = 0;
    _workshopPollTimer = setInterval(async function() {
        if (!document.getElementById('appsHealthModal')) {
            clearInterval(_workshopPollTimer);
            _workshopPollTimer = null;
            return;
        }
        try {
            var payload = await api.getWorkshopStatus(slug);
            _workshopPollErrors = 0;
            var cur = payload && payload.current;
            var body = _renderWorkshopBody(slug, operation, payload);
            var bodyEl = modal.querySelector('.modal-body');
            if (bodyEl) bodyEl.innerHTML = body + '<button class="btn btn-sm btn-secondary" data-action="closeWorkshopModal">Fermer</button>';
            if (cur && (cur.status === 'success' || cur.status === 'failed')) {
                clearInterval(_workshopPollTimer); _workshopPollTimer = null;
                if (typeof loadAppsHealth === 'function') loadAppsHealth();
                if (cur.status === 'success') {
                    showToast('Atelier', WORKSHOP_LABELS[operation] + ' : succès', 'success');
                } else {
                    showToast('Atelier', WORKSHOP_LABELS[operation] + ' : échec', 'danger');
                }
            }
            if (Date.now() - startedAt > WORKSHOP_MAX_WAIT_MS) {
                clearInterval(_workshopPollTimer); _workshopPollTimer = null;
                showToast('Atelier', 'Suivi interrompu après 20 min — vérifiez le statut manuellement.', 'warning');
            }
        } catch (e) {
            _workshopPollErrors++;
            if (_workshopPollErrors >= WORKSHOP_POLL_MAX_ERRORS) {
                clearInterval(_workshopPollTimer);
                _workshopPollTimer = null;
                showToast('Atelier', 'Erreur de suivi : ' + e.message, 'danger');
            }
        }
    }, WORKSHOP_POLL_MS);
}

// ─── Configuration du dépôt git (modale) ──────────────────────────────

async function showGitConfigModal(slug) {
    var st = window._workshopStates[slug] || {};
    var current = st.git_repo_url || '';
    var modal = _showModal('Configurer le dépôt git — ' + slug,
        '<div class="mb-2 text-muted" style="font-size:12px;">L\'URL est enregistrée dans l\'app et configurée comme remote <code>origin</code> du dépôt local (APPS/' + escapeHtml(slug) + '). SSH non supporté — utilisez HTTPS.</div>'
        + '<input type="text" class="form-control" id="gitConfigUrl" placeholder="https://github.com/org/repo.git" value="' + escapeHtml(current) + '">'
        + '<div id="gitConfigResult" class="mt-2" style="font-size:12px;"></div>'
        + '<div class="mt-3 d-flex gap-2">'
        + '<button class="btn btn-sm btn-primary" data-action="saveGitConfig" data-arg1="' + escapeHtml(slug) + '">Enregistrer et tester</button>'
        + '<button class="btn btn-sm btn-secondary" data-action="closeWorkshopModal">Fermer</button>'
        + '</div>');
    modal.querySelector('#gitConfigUrl').focus();
}

async function saveGitConfig(slug) {
    var input = document.getElementById('gitConfigUrl');
    var result = document.getElementById('gitConfigResult');
    if (!input || !result) return;
    var url = input.value.trim();
    if (!url) { result.innerHTML = '<span class="text-danger">URL requise.</span>'; return; }
    result.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Test en cours…';
    try {
        var r = await api.configureGit(slug, url);
        if (r.ok) {
            result.innerHTML = '<span class="text-success">✓ ' + escapeHtml(r.message) + '</span>';
            setTimeout(function() {
                closeWorkshopModal();
                showToast('Dépôt git', 'Configuré : ' + slug, 'success');
                if (typeof loadAppsHealth === 'function') loadAppsHealth();
            }, 1200);
        } else {
            result.innerHTML = '<div class="alert alert-danger py-1 mb-0">' + escapeHtml(r.message)
                + '</div><div class="text-muted mt-1">L\'URL est enregistrée — vérifiez le token GitHub (PAT) ou l\'URL, puis réessayez.</div>';
        }
    } catch (error) {
        result.innerHTML = '<div class="alert alert-danger py-1 mb-0">' + escapeHtml(error.message) + '</div>';
    }
}
