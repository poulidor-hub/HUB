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
        if (block && pair[1]) block.innerHTML = renderWorkshopBlockInner(pair[0], pair[1]);
    });
}

function renderWorkshopBlockInner(slug, state) {
    if (!state.exists) {
        if (!state.git_ok) return '';
        return '<button data-action="createWorkshopUI" data-arg1="' + escapeHtml(slug) + '"'
            + ' class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:11px;">'
            + 'Créer l\'atelier</button>';
    }
    var ref = state.ws_ref || '?';
    var commit = (state.ws_commit || '').slice(0, 8) || '?';
    var version = state.version ? ' · ' + escapeHtml(state.version) : '';
    var busy = state.active_operation ? ' disabled' : '';
    return '<span class="badge text-bg-secondary me-1" title="Atelier actif (contrat .workshop.env)">'
        + 'Atelier : ' + escapeHtml(ref) + ' @ ' + escapeHtml(commit) + version + '</span>'
        + '<div style="display:flex;gap:4px;margin-top:4px;">'
        + '<button data-action="updateWorkshopUI" data-arg1="' + escapeHtml(slug) + '"' + busy
        + ' class="btn btn-sm btn-outline-primary py-0 px-2" style="font-size:11px;"'
        + ' title="Récupérer origin/main et rebuild l\'atelier (clone de DB)">Mettre à jour</button>'
        + '<button data-action="shipWorkshopUI" data-arg1="' + escapeHtml(slug) + '"' + busy
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

async function _triggerWorkshopOp(slug, operation) {
    try {
        var result;
        if (operation === 'create') result = await api.createWorkshop(slug);
        else if (operation === 'update') result = await api.updateWorkshop(slug);
        else if (operation === 'ship') result = await api.shipWorkshop(slug);
        else result = await api.removeWorkshop(slug);
        openWorkshopModal(slug, operation, result.operation_id);
    } catch (error) {
        showToast('Atelier', error.message, 'danger');
    }
}
