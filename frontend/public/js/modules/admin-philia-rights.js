/**
 * Admin — Droits Philia (phase 1 : lecture + suppression).
 *
 * Source de vérité : PhiliaNG, lu en direct par l'app philia-proxy
 * (endpoints Hub /api/philia-rights/*). Une suppression est définitive
 * dans le SI source ; confirmation forte avant exécution.
 *
 * Note : le profil « capturé » par exemple-app (page Droits & habilitations
 * de l'app) n'est mis à jour qu'à la prochaine connexion de l'utilisateur.
 */
/* global api, showToast, bootstrap */

var philiaRightsState = {
    users: [],
    current: null,        // { philia_user_id, hub_username, philia_name, philia_email }
    mo: [],
    agencies: [],
    pendingRemove: null   // { philia_user_id, agencyId, groupValue, groupLabel }
};

function _philiaEsc(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Chargement des utilisateurs connus ─────────────────────────

async function reloadPhiliaRights() {
    var select = document.getElementById('philiaRightsUserSelect');
    try {
        var data = await api.getPhiliaRightsUsers();
        philiaRightsState.users = (data && data.users) || [];
        select.innerHTML = '<option value="">— sélectionner un utilisateur —</option>' +
            philiaRightsState.users.map(function (u) {
                var label = (u.hub_username || u.philia_name || 'session #' + u.session_id) +
                    ' (id NG ' + _philiaEsc(u.philia_user_id) + ')';
                return '<option value="' + _philiaEsc(u.philia_user_id) + '">' + label + '</option>';
            }).join('');
    } catch (error) {
        select.innerHTML = '<option value="">— erreur de chargement —</option>';
        showToast('Erreur', error.message, 'danger');
    }
}

// Appelé par le délégué change sur le select utilisateur
async function philiaUserSelected() {
    var id = document.getElementById('philiaRightsUserSelect').value;
    if (!id) return;
    var user = philiaRightsState.users.find(function (u) {
        return String(u.philia_user_id) === id;
    });
    await loadPhiliaRightsFor(user
        ? { philia_user_id: id, hub_username: user.hub_username,
            philia_name: user.philia_name, philia_email: user.philia_email }
        : { philia_user_id: id });
}

// Appelé par le bouton « Charger » du champ id manuel
async function loadPhiliaManualId() {
    var input = document.getElementById('philiaRightsManualId');
    var id = (input.value || '').trim();
    if (!/^\d+$/.test(id)) {
        showToast('Id invalide', 'Saisir un id numérique PhiliaNG (ex : 232157).', 'warning');
        return;
    }
    await loadPhiliaRightsFor({ philia_user_id: id });
}

// ── Chargement des droits (2 lectures en parallèle) ────────────

async function loadPhiliaRightsFor(user) {
    philiaRightsState.current = user;
    philiaRightsState.mo = [];
    philiaRightsState.agencies = [];
    renderPhiliaIdentity(null, null);
    try {
        var results = await Promise.all([
            api.getPhiliaPerimeters(user.philia_user_id, 'mo'),
            api.getPhiliaPerimeters(user.philia_user_id, 'agencies')
        ]);
        philiaRightsState.mo = results[0].entries || [];
        philiaRightsState.agencies = results[1].entries || [];
        renderPhiliaIdentity(results[0], results[1]);
        renderPhiliaMo();
        renderPhiliaAgencies();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ── Rendus ─────────────────────────────────────────────────────

function renderPhiliaIdentity(moData, agenciesData) {
    var box = document.getElementById('philiaRightsIdentity');
    var u = philiaRightsState.current;
    if (!u) { box.classList.add('d-none'); return; }
    box.classList.remove('d-none');
    var loading = (moData === null);
    box.innerHTML = '<strong>' + _philiaEsc(u.hub_username || 'user Hub inconnu') + '</strong>' +
        (u.philia_name ? ' — ' + _philiaEsc(u.philia_name) : '') +
        (u.philia_email ? ' · ' + _philiaEsc(u.philia_email) : '') +
        ' · id PhiliaNG <code>' + _philiaEsc(u.philia_user_id) + '</code>' +
        (loading
            ? ' <span class="spinner-border spinner-border-sm ms-2"></span>'
            : ' · ' + (moData ? moData.total : '…') + ' habilitation(s) mo, ' +
              (agenciesData ? agenciesData.total : '…') + ' agence(s)');
}

function _philiaRightRow(kind, entry) {
    var gid = _philiaEsc(entry.adm_group_id || '');
    var glabel = _philiaEsc(entry.group_label || 'Groupe ' + (entry.adm_group_id || '?'));
    var label = (kind === 'mo' ? 'Habilitation ' : 'Agence ') + _philiaEsc(entry.id);
    return '<tr>' +
        '<td><code>' + _philiaEsc(entry.id) + '</code></td>' +
        '<td>' + label + '</td>' +
        '<td><span class="badge bg-info text-dark">' + glabel + '</span></td>' +
        '<td class="text-end">' +
        '<button class="btn btn-outline-danger btn-sm" data-action="openPhiliaRightRemoveModal"' +
        ' data-arg1="' + _philiaEsc(entry.id) + '" data-arg2="' + gid + '"' +
        ' data-arg3="' + glabel + '" data-arg4="' + _philiaEsc(entry.id) + '"' +
        ' title="Retirer ce droit"><i class="bi bi-trash"></i></button></td></tr>';
}

function renderPhiliaMo() {
    var block = document.getElementById('philiaRightsMoBlock');
    var body = document.getElementById('philiaRightsMoBody');
    if (!philiaRightsState.mo.length) { block.classList.add('d-none'); return; }
    block.classList.remove('d-none');
    body.innerHTML = philiaRightsState.mo.map(function (e) {
        return _philiaRightRow('mo', e);
    }).join('');
}

function renderPhiliaAgencies() {
    var block = document.getElementById('philiaRightsAgenciesBlock');
    var body = document.getElementById('philiaRightsAgenciesBody');
    var count = document.getElementById('philiaRightsAgenciesCount');
    var filter = document.getElementById('philiaRightsAgenciesFilter');
    if (!philiaRightsState.agencies.length && !philiaRightsState.mo.length) {
        block.classList.add('d-none');
        return;
    }
    block.classList.remove('d-none');
    var needle = ((filter && filter.value) || '').trim().toLowerCase();
    var rows = philiaRightsState.agencies.filter(function (e) {
        return !needle ||
            String(e.id).indexOf(needle) !== -1 ||
            (e.group_label || '').toLowerCase().indexOf(needle) !== -1;
    });
    count.textContent = rows.length + '/' + philiaRightsState.agencies.length;
    body.innerHTML = rows.length
        ? rows.map(function (e) { return _philiaRightRow('agency', e); }).join('')
        : '<tr><td colspan="4" class="text-center text-muted py-3">Aucune agence ne correspond au filtre.</td></tr>';
}

// Filtre agences (délégué input app.js appelle bien les INPUT data-action ;
// par cohérence on branche aussi un listener direct si l'élément existe).
(function () {
    var filter = document.getElementById('philiaRightsAgenciesFilter');
    if (filter) filter.addEventListener('input', renderPhiliaAgencies);
})();

// ── Suppression (confirmation forte, action définitive) ────────

// Appelé par les boutons « Retirer » (data-arg1=id, arg2=groupValue,
// arg3=groupLabel, arg4=agencyLabel)
function openPhiliaRightRemoveModal(agencyId, groupValue, groupLabel, agencyLabel) {
    if (!philiaRightsState.current) return;
    philiaRightsState.pendingRemove = {
        philia_user_id: philiaRightsState.current.philia_user_id,
        agencyId: agencyId,
        groupValue: groupValue,
        groupLabel: groupLabel
    };
    document.getElementById('philiaRightRemoveDetails').innerHTML =
        '<strong>Utilisateur :</strong> ' +
        _philiaEsc(philiaRightsState.current.hub_username || 'id NG ' + philiaRightsState.current.philia_user_id) +
        ' (id NG ' + _philiaEsc(philiaRightsState.current.philia_user_id) + ')<br>' +
        '<strong>Agence :</strong> ' + _philiaEsc(agencyLabel) + '<br>' +
        '<strong>Groupe retiré :</strong> ' + _philiaEsc(groupLabel) +
        ' <span class="badge bg-info text-dark">adm_group_id ' + _philiaEsc(groupValue) + '</span>';
    bootstrap.Modal.getOrCreateInstance(
        document.getElementById('philiaRightRemoveModal')).show();
}

// Appelé par le bouton danger de la modale
async function confirmRemovePhiliaRight() {
    var pending = philiaRightsState.pendingRemove;
    if (!pending) return;
    var modal = bootstrap.Modal.getOrCreateInstance(
        document.getElementById('philiaRightRemoveModal'));
    try {
        var result = await api.removePhiliaRight(
            pending.philia_user_id, pending.agencyId, pending.groupValue, pending.groupLabel);
        modal.hide();
        philiaRightsState.pendingRemove = null;
        showToast('Droit retiré',
            'Suppression effectuée dans PhiliaNG (' + (result.removed_count || 0) +
            ' ligne(s)). Rechargement…', 'success');
        // Re-fetch du seul bloc concerné (agences) — lecture source pour vérifier
        var data = await api.getPhiliaPerimeters(pending.philia_user_id, 'agencies');
        philiaRightsState.agencies = data.entries || [];
        renderPhiliaIdentity({ total: philiaRightsState.mo.length }, data);
        renderPhiliaAgencies();
    } catch (error) {
        showToast('Échec de la suppression', error.message, 'danger');
    }
}
