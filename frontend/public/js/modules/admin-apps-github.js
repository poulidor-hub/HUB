// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
    hidden_uncertified_count: 0,
    hidden_breakdown: {},
    hidden_unchecked_count: 0,
    search: '',
    owner: '',
    view: '',  // vide au boot : _ghGetView() lit alors localStorage (défaut cartes)
};

function _ghGetView() {
    if (_ghState.view !== 'cards' && _ghState.view !== 'table') {
        try { _ghState.view = localStorage.getItem(GH_VIEW_STORAGE_KEY) === 'table' ? 'table' : 'cards'; }
        catch (e) { _ghState.view = 'cards'; }
    }
    return _ghState.view;
}

/**
 * Parse un timestamp ISO en le traitant comme UTC quand il est naïf.
 * fetched_at est stocké naïf (sans suffixe) côté serveur : sans ce correctif,
 * Date le lirait en heure locale — « sync il y a 2 h » juste après une sync
 * réussie sur un poste en UTC+2 (bug campagne de tests 2026-10-03).
 */
function _ghParseUtc(iso) {
    if (!iso) return null;
    var s = String(iso);
    if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(s)) return new Date(s);
    return new Date(s + 'Z');
}

function loadGithubApps() {
    const grid = document.getElementById('githubAppsGrid');
    if (!grid) return;
    grid.innerHTML = '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement…</div>';
    return api.listGithubRepos({ search: _ghState.search, owner: _ghState.owner }).then(function(data) {
        _ghState.repos = data.repos || [];
        _ghState.fetched_at = data.fetched_at;
        _ghState.total = typeof data.total === 'number' ? data.total : _ghState.repos.length;
        _ghState.hidden_uncertified_count = typeof data.hidden_uncertified_count === 'number' ? data.hidden_uncertified_count : 0;
        _ghState.hidden_breakdown = data.hidden_breakdown || {};
        _ghState.hidden_unchecked_count = typeof data.hidden_unchecked_count === 'number' ? data.hidden_unchecked_count : 0;
        _ghState.token_configured = data.token_configured;
        _ghState.token_valid = data.token_valid;
        _ghState.token_last_checked = data.token_last_checked;
        _ghEnsureScanPolling();
        renderGithubApps();
    }).catch(function(e) {
        grid.innerHTML = '<div class="alert alert-danger">Erreur: ' + escapeHtml(e.message) + '</div>';
    });
}

function renderGithubApps() {
    renderGithubTokenBadge();
    renderGithubHeaderMeta();
    _ghRenderHiddenNotice();

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

/**
 * Compteur des dépôts sans certificat masqués par le filtrage serveur
 * (certification hub-app.yaml). Ventilation au survol (title). Nœud dédié
 * inséré sous la barre d'outils — hors #githubAppsGrid pour survivre aux
 * re-rendus du catalogue ; masqué quand aucun dépôt n'est masqué.
 * NB : compteurs post-filtres search/owner, affichés tels quels (ruling R3).
 */
function _ghRenderHiddenNotice() {
    var grid = document.getElementById('githubAppsGrid');
    if (!grid || !grid.parentNode) return;
    var el = document.getElementById('githubHiddenCount');
    var total = (_ghState.hidden_uncertified_count || 0) + (_ghState.hidden_unchecked_count || 0);
    if (!total) {
        if (el) el.remove();
        return;
    }
    var bd = _ghState.hidden_breakdown || {};
    var parts = [];
    if (bd.manifest_missing) parts.push(bd.manifest_missing + ' sans manifeste');
    if (bd.manifest_invalid_yaml) parts.push(bd.manifest_invalid_yaml + ' YAML invalide');
    if (bd.manifest_schema_invalid) parts.push(bd.manifest_schema_invalid + ' schéma invalide');
    if (bd.manifest_repo_mismatch) parts.push(bd.manifest_repo_mismatch + ' dépôt différent');
    if (_ghState.hidden_unchecked_count) parts.push(_ghState.hidden_unchecked_count + ' non vérifiés');
    var detail = parts.length ? 'Masqués : ' + parts.join(' · ') : 'Masqués par le filtrage certification';
    if (!el) {
        el = document.createElement('div');
        el.id = 'githubHiddenCount';
        grid.parentNode.insertBefore(el, grid);
    }
    el.className = 'text-muted small mb-2';
    el.innerHTML = '<i class="bi bi-eye-slash me-1"></i>' + escapeHtml(String(total)) + ' dépôt(s) sans certificat masqués';
    el.setAttribute('title', escapeHtml(detail));
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

function _ghRepoBadge(state, repo) {
    var badges = '';
    if (state === 'integrated') badges = '<span class="badge bg-primary" title="Une app du HUB est liée à ce dépôt"><i class="bi bi-link-45deg me-1"></i>Intégré</span>';
    else if (state === 'archived') badges = '<span class="badge bg-secondary" title="Dépôt archivé sur GitHub"><i class="bi bi-archive me-1"></i>Archivé</span>';
    else badges = '<span class="badge bg-success" title="Dépôt prêt à être ajouté au HUB"><i class="bi bi-check-circle me-1"></i>Dispo</span>';
    // Certification hub-app.yaml (scan de fond) : badge distinct après le badge d'état
    if (repo && repo.manifest_valid === true) {
        badges += ' <span class="badge text-bg-success" title="Manifeste hub-app.yaml vérifié par la dernière certification">Certifié</span>';
    }
    return badges;
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
                        _ghRepoBadge(state, repo) +
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
            '<td>' + _ghRepoBadge(state, repo) + '</td>' +
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
    var d = _ghParseUtc(iso);
    if (!d || isNaN(d.getTime())) return '—';
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
        _ghEnsureScanPolling(); // la sync déclenche un scan de fond : pilule immédiate
        return loadGithubApps();
    }).catch(function(e) {
        showToast('Erreur', e.message, 'danger');
    }).finally(done);
}

// ─── Pilule de certification des manifestes (#ghManifestScanPill) ─────

var _scanPollTimer = null;   // garde : un seul timer global de polling
var _scanWasRunning = false; // un scan était « running » au tick précédent
var _scanLastErrors = 0;     // erreurs du dernier statut « running »
var _scanNotice = false;     // true pendant les 4 s d'annonce « terminée »

function _ghEnsureScanPill() {
    var pill = document.getElementById('ghManifestScanPill');
    if (pill) return pill;
    pill = document.createElement('div');
    pill.id = 'ghManifestScanPill';
    pill.className = 'badge text-bg-secondary';
    pill.style.position = 'fixed';
    pill.style.bottom = '1rem';
    pill.style.right = '1rem';
    pill.style.zIndex = '1060';
    document.body.appendChild(pill);
    return pill;
}

function _ghStopScanPolling() {
    if (_scanPollTimer) {
        clearInterval(_scanPollTimer);
        _scanPollTimer = null;
    }
}

function _ghHideScanPill() {
    var pill = document.getElementById('ghManifestScanPill');
    if (pill) pill.remove();
}

/**
 * Polling du statut de certification (2 s, GET /repos/manifest-scan).
 * Aucun scan jamais lancé (running:false, total:0) → la pilule reste masquée ;
 * en cours → « Certification des apps… done/total » ; à la fin → « terminée »
 * 4 s puis suppression du nœud et UN SEUL rafraîchissement du catalogue.
 * Zéro animation CSS (PERFORMANCE.md).
 */
function _ghEnsureScanPolling() {
    if (_scanPollTimer) return;
    _ghPollManifestScan(); // tick immédiat (évite 2 s d'angle mort après sync)
    _scanPollTimer = setInterval(_ghPollManifestScan, 2000);
}

function _ghPollManifestScan() {
    api.getManifestScanStatus().then(function(st) {
        st = st || {};
        var total = typeof st.total === 'number' ? st.total : 0;
        if (st.running) {
            _scanWasRunning = true;
            _scanNotice = false;
            _scanLastErrors = typeof st.errors === 'number' ? st.errors : 0;
            _ghEnsureScanPill().textContent = 'Certification des apps… ' + (typeof st.done === 'number' ? st.done : 0) + '/' + total;
            return;
        }
        if (_scanNotice) {
            // Annonce « terminée » en cours (timer volontairement maintenu pour
            // détecter un éventuel nouveau scan sans doubler les annonces)
            return;
        }
        if (!_scanWasRunning) {
            // Aucun scan n'a jamais tourné : rien à afficher
            _ghHideScanPill();
            return;
        }
        // Un scan venait de finir : annonce 4 s (erreurs = items non vérifiables,
        // ex. default_branch inconnu), puis nœud + timer supprimés et un seul
        // rechargement du catalogue pour rafraîchir badges + compteurs masqués.
        _scanNotice = true;
        _scanWasRunning = false;
        _ghEnsureScanPill().textContent = _scanLastErrors > 0
            ? 'Certification terminée (' + _scanLastErrors + ' inconnues)'
            : 'Certification terminée';
        setTimeout(function() {
            _scanNotice = false;
            _ghStopScanPolling();
            _ghHideScanPill();
            loadGithubApps();
        }, 4000);
    }).catch(function() {
        // Statut momentanément indisponible : le prochain tick réessaiera
    });
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
    // Bascule vers la vue Applications du hub cockpit, recherche préremplie
    // sur le slug de l'APP liée (integrated_app_slug renvoyé par /repos — bug
    // 2026-10-03 : chercher le slug dérivé du repo donnait 0 résultat quand
    // les deux diffèrent, ex: repo POULIDOR → app `hub`, repo exemple-app-2 → app
    // `exemple-app`).
    if (typeof openHubTool === 'function') openHubTool('apps');
    var searchTerm = '';
    var repo = (_ghState.repos || []).find(function(r) { return r.full_name === fullName; });
    if (repo && repo.integrated_app_slug) {
        searchTerm = repo.integrated_app_slug;
    } else {
        // Fallback : slug dérivé du nom du repo (même normalisation que
        // slugify_repo côté serveur : tout non [a-z0-9] devient '-')
        searchTerm = String(fullName || '').split('/').pop().toLowerCase()
            .replace(/[^a-z0-9]+/g, '-');
    }
    var search = document.getElementById('adminAppsSearch');
    if (search) search.value = searchTerm;
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
    var d = _ghParseUtc(iso);
    if (!d || isNaN(d.getTime())) return '';
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
