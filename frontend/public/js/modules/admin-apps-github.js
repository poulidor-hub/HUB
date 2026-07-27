/**
 * Hub Application — Vue « Apps GitHub »
 * Découverte des repos GitHub (cache) + ajout au HUB.
 */

let _ghState = { repos: [], fetched_at: null, token_configured: false, search: '', owner: '' };

async function loadGithubApps() {
    const grid = document.getElementById('githubAppsGrid');
    if (!grid) return;
    grid.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement…</div>';
    try {
        const data = await api.listGithubRepos({ search: _ghState.search, owner: _ghState.owner });
        _ghState.repos = data.repos || [];
        _ghState.fetched_at = data.fetched_at;
        _ghState.token_configured = data.token_configured;
        renderGithubApps(data);
    } catch (e) {
        grid.innerHTML = '<div class="alert alert-danger">Erreur: ' + escapeHtml(e.message) + '</div>';
    }
}

function renderGithubApps(data) {
    const summary = document.getElementById('githubAppsSummary');
    const fetchedStr = data.fetched_at ? ' · sync ' + formatRelative(data.fetched_at) : ' · jamais synchronisé';
    summary.innerHTML = '<strong>' + data.total + '</strong> repo(s)' + fetchedStr;

    const grid = document.getElementById('githubAppsGrid');
    if (!data.token_configured) {
        grid.innerHTML =
            '<div class="text-center py-5">' +
                '<div style="font-size:32px;">⚠️</div>' +
                '<h5>Aucun token GitHub configuré</h5>' +
                '<p class="text-muted">La sélection visuelle nécessite un Personal Access Token GitHub.</p>' +
                '<button class="btn btn-primary" onclick="document.getElementById(\'navAppsHealth\').click()">⚙️ Configurer le token</button>' +
            '</div>';
        return;
    }
    if (!data.repos || !data.repos.length) {
        grid.innerHTML =
            '<div class="text-center py-5">' +
                '<div style="font-size:32px;">📦</div>' +
                '<h5>Aucun repo en cache</h5>' +
                '<p class="text-muted">Synchronisez depuis GitHub pour voir vos dépôts.</p>' +
                '<button class="btn btn-primary" onclick="syncGithubRepos()">🔄 Synchroniser maintenant</button>' +
            '</div>';
        return;
    }
    grid.innerHTML = data.repos.map(renderGithubRepoCard).join('');

    const ownerSelect = document.getElementById('githubOwnerSelect');
    if (ownerSelect) {
        const owners = Array.from(new Set((data.repos || []).map(function(r) { return r.owner; }))).sort();
        const current = ownerSelect.value;
        ownerSelect.innerHTML = '<option value="">Tous les owners</option>' +
            owners.map(function(o) { return '<option value="' + escapeHtml(o) + '"' + (o === current ? ' selected' : '') + '>' + escapeHtml(o) + '</option>'; }).join('');
    }
}

function renderGithubRepoCard(repo) {
    var integrated = repo.already_integrated;
    var archived = repo.archived;
    var badge = integrated
        ? '<span class="badge bg-primary">🔵 Intégré</span>'
        : archived
            ? '<span class="badge bg-secondary">📦 Archivé</span>'
            : '<span class="badge bg-success">🟢 Dispo</span>';
    var action = integrated
        ? '<button class="btn btn-sm btn-outline-secondary w-100" onclick="viewAppForRepo(\'' + escapeHtml(repo.full_name) + '\')">✓ Voir l\'app →</button>'
        : archived
            ? '<button class="btn btn-sm btn-outline-secondary w-100" disabled>+ Ajouter au HUB</button>'
            : '<button class="btn btn-sm btn-success w-100" onclick="addRepoToHub(\'' + escapeHtml(repo.full_name) + '\')">+ Ajouter au HUB</button>';
    var desc = repo.description ? '<div class="small text-muted">' + escapeHtml(repo.description) + '</div>' : '';
    return '<div class="card h-100 ' + (integrated || archived ? 'opacity-75' : '') + '">' +
        '<div class="card-body d-flex flex-column">' +
            '<div class="d-flex justify-content-between align-items-start">' +
                '<strong>' + escapeHtml(repo.name) + '</strong>' + badge +
            '</div>' +
            '<div class="small text-muted">' + escapeHtml(repo.full_name) + '</div>' +
            desc +
            '<div class="mt-auto pt-2">' + action + '</div>' +
        '</div>' +
    '</div>';
}

async function syncGithubRepos() {
    var btn = document.getElementById('githubSyncBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>…'; }
    try {
        var r = await api.refreshGithubRepos();
        if (!r.token_configured) {
            showToast('GitHub', 'Aucun token GitHub configuré', 'warning');
        } else if (r.error) {
            showToast('GitHub', r.error, 'warning');
        } else if (r.skipped) {
            showToast('GitHub', 'Sync récente, ignorée', 'info');
        } else {
            showToast('GitHub', r.synced + ' repo(s) synchronisé(s)', 'success');
        }
        await loadGithubApps();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '🔄 Synchroniser'; }
    }
}

async function addRepoToHub(fullName) {
    try {
        var prepared = await api.prepareAppFromRepo(fullName);
        openAppFormPrefilled(prepared);
    } catch (e) {
        var msg = e.message || 'Échec de la préparation';
        showToast('Erreur', msg, 'danger');
    }
}

function viewAppForRepo(fullName) {
    showToast('Info', 'Recherche de l\'app liée à ' + fullName, 'info');
}

function onGithubFilterChange() {
    _ghState.search = document.getElementById('githubSearchInput').value.trim();
    _ghState.owner = document.getElementById('githubOwnerSelect').value;
    loadGithubApps();
}

function formatRelative(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    var secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return 'à l\'instant';
    if (secs < 3600) return 'il y a ' + Math.floor(secs / 60) + ' min';
    if (secs < 86400) return 'il y a ' + Math.floor(secs / 3600) + ' h';
    return 'il y a ' + Math.floor(secs / 86400) + ' j';
}

// (empty after stub removal in Task 9)

