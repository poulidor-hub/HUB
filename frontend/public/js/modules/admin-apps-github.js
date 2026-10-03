/**
 * Hub Application — Vue « Apps GitHub »
 * Découverte des repos GitHub (cache) + ajout au HUB.
 *
 * Design system Hub : en-tête standard, toolbar admin-apps-toolbar, badges
 * Bootstrap, icônes bi, empty-states et modal token (pattern modals global).
 * Toggle cartes/table mémorisé en localStorage (défaut : cartes).
 */

const GH_VIEW_STORAGE_KEY = 'github_view_mode';
const GH_ICON_TILES = {
    available: 'gh-icon-tile-available',
    integrated: 'gh-icon-tile-integrated',
    archived: 'gh-icon-tile-archived',
};
const GH_TILE_ICONS = {
    available: 'bi bi-shield-check',
    integrated: 'bi bi-hdd-network',
    archived: 'bi bi-archive',
};

let _ghState = {
    repos: [],
    fetched_at: null,
    token_configured: false,
    token_valid: null,
    token_last_checked: null,
    total: 0,
    search: '',
    owner: '',
    view: 'cards',
};

function _ghGetView() {
    if (_ghState.view !== 'cards' && _ghState.view !== 'table') {
        try { _ghState.view = localStorage.getItem(GH_VIEW_STORAGE_KEY) === 'table' ? 'table' : 'cards'; }
        catch (e) { _ghState.view = 'cards'; }
    }
    return _ghState.view;
}

function loadGithubApps() {
    const grid = document.getElementById('githubAppsGrid');
    if (!grid) return;
    grid.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement…</div>';
    return api.listGithubRepos({ search: _ghState.search, owner: _ghState.owner }).then(function(data) {
        _ghState.repos = data.repos || [];
        _ghState.fetched_at = data.fetched_at;
        _ghState.total = typeof data.total === 'number' ? data.total : _ghState.repos.length;
        _ghState.token_configured = data.token_configured;
        _ghState.token_valid = data.token_valid;
        _ghState.token_last_checked = data.token_last_checked;
        renderGithubApps();
    }).catch(function(e) {
        grid.innerHTML = '<div class="alert alert-danger">Erreur: ' + escapeHtml(e.message) + '</div>';
    });
}

function renderGithubApps() {
    renderGithubTokenBadge();
    renderGithubHeaderMeta();

    const grid = document.getElementById('githubAppsGrid');
    if (!grid) return;

    if (!_ghState.token_configured) {
        grid.innerHTML = _ghEmptyStateHTML(
            'bi-key',
            'Aucun token GitHub configuré',
            'Configurez un token pour découvrir les dépôts du parc et déployer les apps.',
            '<button class="btn btn-primary btn-sm" data-action="openGithubTokenModal"><i class="bi bi-key me-1"></i>Configurer le token</button>'
        );
        _ghSyncViewToggle();
        return;
    }

    const filtered = _ghState.search !== '' || _ghState.owner !== '';
    if (!_ghState.repos.length) {
        grid.innerHTML = filtered
            ? _ghEmptyStateHTML(
                'bi-search',
                'Aucun repo ne correspond aux filtres actuels.',
                'Essayez un autre nom ou changez d\'owner.',
                '<button class="btn btn-outline-secondary btn-sm" data-action="clearGithubFilters"><i class="bi bi-arrow-counterclockwise me-1"></i>Réinitialiser</button>'
            )
            : _ghEmptyStateHTML(
                'bi-github',
                'Aucun dépôt en cache',
                'Lancez une synchronisation pour importer le catalogue depuis GitHub.',
                '<button class="btn btn-primary btn-sm" data-action="syncGithubRepos"><i class="bi bi-arrow-clockwise me-1"></i>Synchroniser maintenant</button>'
            );
        _ghSyncViewToggle();
        return;
    }

    grid.innerHTML = _ghGetView() === 'table' ? _ghRenderTable() : _ghRenderCards();
    _ghSyncViewToggle();
    _ghSyncOwnerSelect();
}

function renderGithubTokenBadge() {
    var el = document.getElementById('githubTokenBadge');
    if (!el) return;
    if (!_ghState.token_configured) {
        el.innerHTML = '<span class="badge bg-danger" title="Configurez un token pour synchroniser le catalogue"><i class="bi bi-key me-1"></i>aucun token</span>';
        return;
    }
    if (_ghState.token_valid === false) {
        var when = _ghState.token_last_checked ? 'Dernier contrôle : ' + _ghState.token_last_checked : 'GitHub a refusé le token';
        el.innerHTML = '<span class="badge bg-danger" title="' + escapeHtml(when) + '"><i class="bi bi-exclamation-triangle me-1"></i>token invalide</span>';
        return;
    }
    el.innerHTML = '<span class="badge bg-success" title="Token configuré et accepté par GitHub"><i class="bi bi-key me-1"></i>token valide</span>';
}

function renderGithubHeaderMeta() {
    var countEl = document.getElementById('githubAppsCount');
    var metaEl = document.getElementById('githubSyncMeta');
    if (countEl) {
        countEl.innerHTML = '<strong>' + _ghState.total + '</strong> repo(s)';
    }
    if (metaEl) {
        metaEl.textContent = _ghState.fetched_at ? 'sync ' + formatRelative(_ghState.fetched_at) : 'jamais synchronisé';
    }
}

function _ghEmptyStateHTML(icon, title, text, actionHTML) {
    return '<div class="card border-0 shadow-sm">' +
        '<div class="card-body">' +
            '<div class="text-center py-4 text-muted">' +
                '<i class="bi ' + icon + ' fs-1"></i>' +
                '<p class="mt-2 mb-1 fw-semibold text-dark">' + escapeHtml(title) + '</p>' +
                '<p class="small mb-3">' + escapeHtml(text) + '</p>' +
                actionHTML +
            '</div>' +
        '</div>' +
    '</div>';
}

function _ghRepoState(repo) {
    if (repo.already_integrated) return 'integrated';
    if (repo.archived) return 'archived';
    return 'available';
}

function _ghRepoBadge(state) {
    if (state === 'integrated') return '<span class="badge bg-primary" title="Une app du HUB est liée à ce dépôt"><i class="bi bi-link-45deg me-1"></i>Intégré</span>';
    if (state === 'archived') return '<span class="badge bg-secondary" title="Dépôt archivé sur GitHub"><i class="bi bi-archive me-1"></i>Archivé</span>';
    return '<span class="badge bg-success" title="Dépôt prêt à être ajouté au HUB"><i class="bi bi-check-circle me-1"></i>Dispo</span>';
}

function _ghRepoAction(state, repo) {
    var fullNameAttr = ' data-arg1="' + escapeHtml(repo.full_name) + '"';
    if (state === 'integrated') {
        return '<button class="btn btn-sm btn-outline-secondary" data-action="viewAppForRepo"' + fullNameAttr + ' title="Ouvrir l\'onglet Applications filtré sur ce dépôt"><i class="bi bi-box-arrow-up-right me-1"></i>Voir l\'app</button>';
    }
    if (state === 'archived') {
        return '<button class="btn btn-sm btn-outline-secondary" disabled title="Dépôt archivé : ajout impossible">Ajouter</button>';
    }
    return '<button class="btn btn-sm btn-primary" data-action="addRepoToHub"' + fullNameAttr + '><i class="bi bi-plus-lg me-1"></i>Ajouter au HUB</button>';
}

function _ghRepoTile(state) {
    return '<span class="gh-icon-tile ' + GH_ICON_TILES[state] + '"><i class="' + GH_TILE_ICONS[state] + '"></i></span>';
}

function _ghRenderCards() {
    var cards = _ghState.repos.map(function(repo) {
        var state = _ghRepoState(repo);
        var dimmed = state !== 'available' ? ' opacity-75' : '';
        var desc = repo.description
            ? '<div class="gh-repo-desc small text-muted">' + escapeHtml(repo.description) + '</div>'
            : '<div class="gh-repo-desc small text-muted fst-italic">Pas de description</div>';
        return '<div class="col">' +
            '<div class="card h-100 shadow-sm gh-repo-card' + dimmed + '">' +
                '<div class="card-body d-flex flex-column">' +
                    '<div class="d-flex justify-content-between align-items-start gap-2">' +
                        '<div class="d-flex align-items-center gap-2">' +
                            _ghRepoTile(state) +
                            '<div>' +
                                '<div class="gh-repo-name">' + escapeHtml(repo.name) + '</div>' +
                                '<div class="gh-repo-full">' + escapeHtml(repo.full_name) + ' · ' + escapeHtml(repo.default_branch || 'main') + '</div>' +
                            '</div>' +
                        '</div>' +
                        _ghRepoBadge(state) +
                    '</div>' +
                    desc +
                    '<div class="d-flex justify-content-between align-items-center mt-auto pt-2">' +
                        '<span class="gh-meta"><i class="bi bi-clock me-1"></i>maj ' + _ghFormatDay(repo.updated_at) + '</span>' +
                        _ghRepoAction(state, repo) +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }).join('');
    return '<div class="row row-cols-1 row-cols-md-2 row-cols-xl-3 g-3">' + cards + '</div>';
}

function _ghRenderTable() {
    var rows = _ghState.repos.map(function(repo) {
        var state = _ghRepoState(repo);
        var desc = repo.description ? escapeHtml(repo.description) : '<span class="fst-italic">Pas de description</span>';
        var dimmed = state !== 'available' ? ' opacity-75' : '';
        return '<tr class="align-middle' + dimmed + '">' +
            '<td><div class="d-flex align-items-center gap-2">' +
                _ghRepoTile(state) +
                '<div>' +
                    '<div class="gh-repo-name">' + escapeHtml(repo.name) + '</div>' +
                    '<code class="small text-muted">' + escapeHtml(repo.full_name) + '</code>' +
                '</div>' +
            '</div></td>' +
            '<td class="small text-muted gh-repo-desc" style="max-width:320px;">' + desc + '</td>' +
            '<td class="small">' + escapeHtml(repo.owner) + '</td>' +
            '<td class="gh-meta">' + _ghFormatDay(repo.updated_at) + '</td>' +
            '<td>' + _ghRepoBadge(state) + '</td>' +
            '<td class="text-end text-nowrap">' + _ghRepoAction(state, repo) + '</td>' +
        '</tr>';
    }).join('');
    return '<div class="card border-0 shadow-sm">' +
        '<div class="table-responsive">' +
            '<table class="table table-hover align-middle mb-0">' +
                '<thead><tr>' +
                    '<th>Dépôt</th><th>Description</th><th>Owner</th><th>Maj</th><th>État</th><th class="text-end">Action</th>' +
                '</tr></thead>' +
                '<tbody>' + rows + '</tbody>' +
            '</table>' +
        '</div>' +
    '</div>';
}

function _ghFormatDay(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function _ghSyncViewToggle() {
    var cardsBtn = document.getElementById('ghViewCardsBtn');
    var tableBtn = document.getElementById('ghViewTableBtn');
    var view = _ghGetView();
    if (cardsBtn) cardsBtn.classList.toggle('active', view === 'cards');
    if (tableBtn) tableBtn.classList.toggle('active', view === 'table');
}

function _ghSyncOwnerSelect() {
    const ownerSelect = document.getElementById('githubOwnerSelect');
    if (!ownerSelect) return;
    const owners = Array.from(new Set(_ghState.repos.map(function(r) { return r.owner; }))).sort();
    const current = ownerSelect.value;
    ownerSelect.innerHTML = '<option value="">Tous les owners</option>' +
        owners.map(function(o) { return '<option value="' + escapeHtml(o) + '"' + (o === current ? ' selected' : '') + '>' + escapeHtml(o) + '</option>'; }).join('');
}

function _ghSyncSearchClear() {
    var clearBtn = document.getElementById('githubSearchClear');
    if (clearBtn) clearBtn.classList.toggle('d-none', !_ghState.search);
}

function setGithubView(mode) {
    if (mode !== 'cards' && mode !== 'table') return;
    _ghState.view = mode;
    try { localStorage.setItem(GH_VIEW_STORAGE_KEY, mode); } catch (e) { /* stockage indisponible */ }
    // Re-render local (aucun appel API) si des repos sont déjà affichés
    if (_ghState.repos.length) renderGithubApps();
}

function syncGithubRepos() {
    var btn = document.getElementById('githubSyncBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Synchronisation…';
    }
    var done = function() {
        if (btn) {
            btn.disabled = false;
            // Markup identique au shell index.html (l'icône bi ne doit jamais
            // être remplacée par un emoji — régression 2026-10-03)
            btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Synchroniser';
        }
    };
    return api.refreshGithubRepos().then(function(r) {
        if (!r.token_configured) {
            showToast('GitHub', 'Aucun token GitHub configuré', 'warning');
        } else if (r.error) {
            showToast('GitHub', r.error, 'warning');
        } else if (r.skipped) {
            showToast('GitHub', 'Sync récente, ignorée', 'info');
        } else {
            showToast('GitHub', r.synced + ' repo(s) synchronisé(s)', 'success');
        }
        return loadGithubApps();
    }).catch(function(e) {
        showToast('Erreur', e.message, 'danger');
    }).finally(done);
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
    // Bascule vers l'onglet Applications, recherche préremplie sur le nom du dépôt
    var tabBtn = document.querySelector('[data-bs-target="#tabApps"]');
    if (tabBtn && typeof bootstrap !== 'undefined' && bootstrap.Tab) {
        bootstrap.Tab.getOrCreateInstance(tabBtn).show();
    }
    var slug = String(fullName || '').split('/').pop().toLowerCase();
    var search = document.getElementById('adminAppsSearch');
    if (search) search.value = slug;
    if (typeof filterAdminApps === 'function') filterAdminApps();
}

function onGithubFilterChange() {
    _ghState.search = document.getElementById('githubSearchInput').value.trim();
    _ghState.owner = document.getElementById('githubOwnerSelect').value;
    _ghSyncSearchClear();
    loadGithubApps();
}

function clearGithubFilters() {
    _ghState.search = '';
    _ghState.owner = '';
    var searchInput = document.getElementById('githubSearchInput');
    var ownerSelect = document.getElementById('githubOwnerSelect');
    if (searchInput) searchInput.value = '';
    if (ownerSelect) ownerSelect.value = '';
    _ghSyncSearchClear();
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

// ─── Gestion du token GitHub (modal #githubTokenModal) ───────────────

function openGithubTokenModal() {
    var modalEl = document.getElementById('githubTokenModal');
    if (!modalEl) return;
    var input = document.getElementById('githubTokenInput');
    if (input) { input.value = ''; input.type = 'password'; }
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    if (input) input.focus();
}

function toggleGithubTokenVisibility() {
    var input = document.getElementById('githubTokenInput');
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
    var icon = input.parentElement ? input.parentElement.querySelector('button i') : null;
    if (icon) icon.className = input.type === 'password' ? 'bi bi-eye' : 'bi bi-eye-slash';
}

async function saveGithubToken() {
    var input = document.getElementById('githubTokenInput');
    if (!input) return;
    var token = input.value.trim();
    if (!token) {
        showToast('Token GitHub', 'Veuillez saisir un token', 'warning');
        return;
    }
    // Validation basique : un PAT GitHub commence par ghp_ (classic) ou github_pat_ (fine-grained)
    if (!token.startsWith('ghp_') && !token.startsWith('github_pat_')) {
        showToast('Token GitHub', 'Le token ne ressemble pas à un PAT GitHub (ghp_... ou github_pat_...)', 'warning');
        return;
    }

    var btn = event && event.target ? event.target : null;
    if (btn) { btn.disabled = true; btn.textContent = 'Enregistrement…'; }

    try {
        await api.updateSetting('github_token', token);
        showToast('Token GitHub', 'Token enregistré avec succès', 'success');
        var modalEl = document.getElementById('githubTokenModal');
        if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        await loadGithubApps();
    } catch (e) {
        showToast('Token GitHub', 'Erreur: ' + (e.message || 'échec'), 'danger');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
        }
    }
}
