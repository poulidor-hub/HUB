// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - fiche application (vue outil « Applications », passe 2)
 *
 * Fiche détaillée d'une application, ouverte par clic sur une ligne du
 * tableau ou bouton « Fiche » (openAppDetail — câblé par admin-apps.js,
 * passe 1). La fiche vit DANS la vue outil Applications : bascule
 * d'affichage entre la liste (#appsListWrap) et la fiche (#appDetailPane) —
 * pas une nouvelle page admin. Le hash porte un 3ᵉ segment
 * (#admin/apps/<slug>) géré par admin-hub.js : refresh / lien direct /
 * back-forward rouvrent la fiche ; #admin/apps remet la liste.
 *
 * Ce premier jet pose la mécanique (ouverture/fermeture/routage, en-tête
 * tuile+badges, rangée d'état Docker/connectivité/version/utilisateurs,
 * fiche réduite pending/rejected) ; les sections techniques (mises à jour,
 * changelog, logs, Docker) suivent dans le même module.
 *
 * Aucune animation — charte perf du parc. Boutons SOLID Bootstrap.
 * Tout contenu dynamique est échappé (escapeHtml/escapeAttr).
 */

// ===== État de la fiche =====

// Slug affiché, ou null (fiche fermée / liste affichée).
let _appDetailSlug = null;
// Objet app affiché (cache allApps en priorité, sinon fetch /apps/all).
let _appDetailApp = null;
// Timer du poll « déploiement en cours » (2 s), null si aucun — piloté
// par la section Mises à jour ; stop général via stopAppDetailPolling().
let _appDetailPollTimer = null;
let _appDetailPollSlug = null;
// Garde : le test de connectivité de secours ne part qu'une fois par fiche.
let _appDetailStatusFetchStarted = false;

// ===== Helpers purs (formats, badges) =====

/**
 * Convertit une URL de dépôt git en URL GitHub canonique (sans .git),
 * ou null si le dépôt n'est pas hébergé sur github.com. Supporte les
 * formes https://github.com/o/r(.git) et git@github.com:o/r(.git).
 */
function appDetailGithubUrl(gitRepoUrl) {
    if (!gitRepoUrl || typeof gitRepoUrl !== 'string') return null;
    const raw = gitRepoUrl.trim();
    let match = /^https?:\/\/[^/]*github\.com\/([^/]+)\/([^/#?]+?)(?:\.git)?(?:[/?#].*)?$/i.exec(raw);
    if (match) return 'https://github.com/' + match[1] + '/' + match[2];
    match = /^git@[^:]*:?github\.com[:/]([^/]+)\/([^/#?]+?)(?:\.git)?$/i.exec(raw);
    if (match) return 'https://github.com/' + match[1] + '/' + match[2];
    return null;
}

/** Date ISO → « jj/mm/aaaa hh:mm » (fr), ou « — » si non parsable. */
function formatAppDetailDateTime(iso) {
    const date = parseUTCDate(iso);
    if (!date || isNaN(date)) return '—';
    return date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
        + ' ' + date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

/** Durée entre deux dates ISO (« 42 s », « 3 min 05 s », « 2 h 00 »), null si incomplète. */
function formatAppDetailDuration(startIso, endIso) {
    const start = parseUTCDate(startIso);
    const end = parseUTCDate(endIso);
    if (!start || isNaN(start) || !end || isNaN(end)) return null;
    const seconds = Math.max(0, Math.round((end - start) / 1000));
    if (seconds >= 3600) {
        return Math.floor(seconds / 3600) + ' h ' + String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    }
    if (seconds >= 60) {
        return Math.floor(seconds / 60) + ' min ' + String(seconds % 60).padStart(2, '0') + ' s';
    }
    return seconds + ' s';
}

/** Uptime en secondes → « 3 j 2 h » / « 5 h 12 min » / « 42 min » (même format que buildContainerStatsCard). */
function formatAppDetailUptime(seconds) {
    const sec = Number(seconds) || 0;
    if (sec >= 86400) {
        return Math.floor(sec / 86400) + ' j ' + Math.floor((sec % 86400) / 3600) + ' h';
    }
    if (sec >= 3600) {
        return Math.floor(sec / 3600) + ' h ' + Math.floor((sec % 3600) / 60) + ' min';
    }
    return Math.floor(sec / 60) + ' min';
}

/** Badge Docker de la rangée d'état : en marche (success) / arrêté (warning) / inconnu (light). */
function appDetailDockerBadgeHtml(state) {
    const docker = state ? state.docker : null;
    if (!docker) return '<span class="badge text-bg-light border">Docker · inconnu</span>';
    if (docker === 'running') return '<span class="badge text-bg-success">Docker · en marche</span>';
    return '<span class="badge text-bg-warning">Docker · arrêté</span>';
}

/** Badge connectivité : « En ligne · X ms » (success) / « Hors ligne » (danger) / « Vérification » (light). */
function appDetailConnectivityBadgeHtml(state) {
    const health = state ? state.health : null;
    const latency = state ? state.latency_ms : null;
    if (health === 'healthy') {
        return '<span class="badge text-bg-success">En ligne' + (latency ? ' · ' + latency + ' ms' : '') + '</span>';
    }
    if (health === 'unhealthy') return '<span class="badge text-bg-danger">Hors ligne</span>';
    return '<span class="badge text-bg-light border">Vérification</span>';
}

/**
 * Badge version : « v1.4.2 — à jour » (vert clair), « vX — nouvelle version
 * dispo (vY) » (warning via update-check), « version inconnue » (light) si
 * aucune version. `updateInfo` = réponse de /apps/<slug>/update-check ou
 * null (erreur / pas de dépôt) → badge neutre sur la version enregistrée.
 */
function appDetailVersionBadgeHtml(version, updateInfo) {
    const current = (updateInfo && updateInfo.current_version) || version || null;
    if (!current) return '<span class="badge text-bg-light border">version inconnue</span>';
    const label = 'v' + String(current).replace(/^v/i, '');
    if (updateInfo && updateInfo.update_available && updateInfo.latest_version) {
        const latest = 'v' + String(updateInfo.latest_version).replace(/^v/i, '');
        return '<span class="badge text-bg-warning" title="Version installée : '
            + escapeAttr(label) + ' · disponible : ' + escapeAttr(latest) + '">'
            + label + ' — nouvelle version dispo (' + latest + ')</span>';
    }
    return '<span class="badge bg-success-subtle text-success-emphasis border">' + escapeHtml(label) + ' — à jour</span>';
}

/** Badge « N utilisateurs » (longueur du tableau users de l'app). */
function appDetailUsersBadgeHtml(users) {
    const count = Array.isArray(users) ? users.length : 0;
    return '<span class="badge text-bg-light border">' + count
        + (count === 1 ? ' utilisateur' : ' utilisateurs') + '</span>';
}

/** Badge de statut d'un déploiement : Réussi / Échoué / En cours… / brut. */
function appDetailDeployBadgeHtml(status) {
    switch (status) {
        case 'success': return '<span class="badge text-bg-success">Réussi</span>';
        case 'failed': return '<span class="badge text-bg-danger">Échoué</span>';
        case 'running': return '<span class="badge text-bg-warning">En cours…</span>';
        default: return '<span class="badge text-bg-light border">' + escapeHtml(status || 'inconnu') + '</span>';
    }
}

// ===== Ouverture / fermeture / routage =====

/**
 * Ouvre la fiche d'une application (global — câblé sur le tableau par
 * data-action="openAppDetail" data-arg1="<slug>" et le clic-ligne).
 * Cache allApps en priorité, sinon fetch /apps/all ; masque la liste,
 * affiche la fiche et pose le hash #admin/apps/<slug>.
 */
async function openAppDetail(slugArg) {
    const slug = String(slugArg || '').trim();
    if (!slug) return;
    const pane = document.getElementById('appDetailPane');
    if (!pane) { console.warn('[admin-app-detail] #appDetailPane introuvable'); return; }

    // Garde no-op : déjà ouverte sur ce slug (double évènement clic + hash).
    if (_appDetailSlug === slug && !pane.classList.contains('d-none')) return;

    let app = (typeof allApps !== 'undefined' && Array.isArray(allApps))
        ? allApps.find(a => a && a.slug === slug) : null;
    if (!app) {
        // App absente du cache (lien direct #admin/apps/<slug> alors que le
        // filtre Mode côté serveur l'exclut) : un fetch /apps/all la trouve.
        try {
            const fetched = await api.getAllApps('all');
            app = (Array.isArray(fetched) ? fetched : []).find(a => a && a.slug === slug);
        } catch (error) {
            console.error('[admin-app-detail] Rechargement des apps impossible :', error);
        }
    }
    if (!app) {
        showToast('Erreur', 'Application « ' + slug + ' » introuvable.', 'danger');
        // Fiche restée ouverte sur une app supprimée depuis une autre vue
        // (refreshAppDetail) : retour propre à la liste.
        if (!pane.classList.contains('d-none')) closeAppDetail();
        return;
    }

    // Rend l'app visible par editApp (recherche par id dans allApps) quand
    // elle ne vient pas du tableau : ajout local, le tableau se re-rend de
    // lui-même au prochain loadAdminData (cache remplacé en bloc).
    if (typeof allApps !== 'undefined' && Array.isArray(allApps) && !allApps.some(a => a && a.id === app.id)) {
        allApps.push(app);
    }

    stopAppDetailPolling();
    _appDetailSlug = slug;
    _appDetailApp = app;
    _appDetailStatusFetchStarted = false;

    const list = document.getElementById('appsListWrap');
    if (list) list.classList.add('d-none');
    pane.classList.remove('d-none');
    pane.innerHTML = '<div class="text-muted text-center py-4">Chargement de la fiche…</div>';
    try { window.location.hash = 'admin/apps/' + slug; } catch (e) { /* hash refusé */ }

    renderAppDetailPane();
}

/** Retour à la liste (bouton « ← Applications ») : masque la fiche + hash liste. */
function closeAppDetail() {
    hideAppDetailPane();
    try { window.location.hash = 'admin/apps'; } catch (e) { /* hash refusé */ }
}

/** Masque la fiche si elle est ouverte (routage hash #admin/apps, back/forward). */
function hideAppDetailIfOpen() {
    const pane = document.getElementById('appDetailPane');
    if (!pane || pane.classList.contains('d-none')) return;
    hideAppDetailPane();
}

/** Masque la fiche et restaure la liste (aucune écriture du hash). */
function hideAppDetailPane() {
    stopAppDetailPolling();
    _appDetailSlug = null;
    _appDetailApp = null;
    const pane = document.getElementById('appDetailPane');
    if (pane) { pane.classList.add('d-none'); pane.innerHTML = ''; }
    const list = document.getElementById('appsListWrap');
    if (list) list.classList.remove('d-none');
}

/**
 * Sortie de la vue outil Applications (appelé par leaveAdminHubView dans
 * admin-hub.js à chaque ouverture d'un autre outil ou du hub) : la fiche
 * est fermée sans toucher au hash (openHubTool/showAdminHub posent le leur).
 */
function leaveAppDetail() {
    hideAppDetailPane();
}

/** Stoppe le poll « déploiement en cours » s'il tourne (idempotent). */
function stopAppDetailPolling() {
    if (_appDetailPollTimer) {
        clearInterval(_appDetailPollTimer);
        _appDetailPollTimer = null;
    }
    _appDetailPollSlug = null;
}

/**
 * Re-rend la fiche courante avec des données fraîches (appelé après une
 * modification : sauvegarde du formulaire d'édition, approbation, rejet,
 * suppression). Si l'app n'existe plus, retour propre à la liste.
 */
function refreshAppDetail() {
    const slug = _appDetailSlug;
    if (!slug) return;
    const pane = document.getElementById('appDetailPane');
    if (!pane || pane.classList.contains('d-none')) return;
    // Réinitialise la garde no-op d'openAppDetail pour forcer le re-render.
    _appDetailSlug = null;
    _appDetailApp = null;
    openAppDetail(slug);
}

// ===== Rendu de la fiche =====

function renderAppDetailPane() {
    const slug = _appDetailSlug;
    const app = _appDetailApp;
    const pane = document.getElementById('appDetailPane');
    if (!pane || !app || slug !== app.slug) return;

    const isPending = app.approval_status === 'pending';
    const isRejected = app.approval_status === 'rejected';
    const needsApproval = isPending || isRejected;

    pane.innerHTML = ''
        + '<div class="mb-3">'
        + '<button class="btn btn-sm btn-outline-secondary" data-action="closeAppDetail" title="Retour à la liste des applications">'
        + hubIcon('arrow-left') + '<span class="ms-1">Applications</span>'
        + '</button>'
        + '</div>'
        + renderAppDetailHeader(app, needsApproval)
        + (needsApproval
            ? renderAppDetailApprovalBlock(app, isPending, isRejected)
            : renderAppDetailStatusRow());

    if (!needsApproval) {
        ensureAppDetailStatusFetched(slug);
        updateAppDetailStatusBadges();
        loadAppDetailVersionBadge();
    }
}

/**
 * En-tête : tuile icône (bg = app.color), nom + badges (partagé / Atelier /
 * Non gérée, workflow éventuel), slug + frontend_url en muted ; à droite
 * « Ouvrir » (primary, frontend_url) et « Éditer » (secondary) — absents
 * pour une app pending/rejected (workflow d'approbation uniquement).
 */
function renderAppDetailHeader(app, needsApproval) {
    const badges = [];
    if (app.is_shared_service) badges.push('<span class="badge text-bg-info" title="API partagée accessible par tous les Service Accounts">partagé</span>');
    if (app.is_workshop) badges.push('<span class="badge text-bg-warning">Atelier</span>');
    if (app.is_unmanaged) badges.push('<span class="badge text-bg-secondary">Non gérée</span>');
    if (app.approval_status === 'pending') badges.push('<span class="badge text-bg-warning">En attente d\'approbation</span>');
    if (app.approval_status === 'rejected') badges.push('<span class="badge text-bg-danger">Rejetée</span>');

    let actionsHtml = '';
    if (!needsApproval) {
        const openBtn = app.frontend_url
            ? '<a class="btn btn-sm btn-primary" href="' + escapeAttr(app.frontend_url) + '" target="_blank" rel="noopener"'
                + ' title="Ouvrir l\'application dans un nouvel onglet">'
                + hubIcon('external-link') + '<span class="ms-1">Ouvrir</span></a>'
            : '';
        const editBtn = '<button class="btn btn-sm btn-secondary" data-action="editApp" data-arg1="' + escapeAttr(app.id) + '"'
            + ' title="Modifier les informations de l\'application">'
            + hubIcon('pencil') + '<span class="ms-1">Éditer</span></button>';
        actionsHtml = '<div class="d-flex gap-2 ms-auto">' + openBtn + editBtn + '</div>';
    }

    return '<div class="card border-0 shadow-sm mb-3">'
        + '<div class="card-body py-3">'
        + '<div class="d-flex align-items-center gap-3 flex-wrap">'
        + '<span class="rounded d-inline-flex align-items-center justify-content-center flex-none" style="width:44px;height:44px;background-color:' + escapeAttr(app.color || '#0d6efd') + ';color:#fff">'
        + '<i class="' + escapeAttr(app.icon || 'bi-app') + '" style="font-size:1.25rem"></i>'
        + '</span>'
        + '<div class="flex-grow-1">'
        + '<h5 class="mb-0 d-flex align-items-center gap-2 flex-wrap">' + escapeHtml(app.name || app.slug)
        + (badges.length ? '<span class="d-inline-flex gap-1">' + badges.join('') + '</span>' : '')
        + '</h5>'
        + '<div class="text-muted small"><code>' + escapeHtml(app.slug) + '</code>'
        + (app.frontend_url ? ' · <span class="text-break">' + escapeHtml(app.frontend_url) + '</span>' : '')
        + '</div>'
        + '</div>'
        + actionsHtml
        + '</div>'
        + '</div>'
        + '</div>';
}

/**
 * Rangée de badges d'état des apps actives : Docker, connectivité (source
 * runtimeStates — sinon 1 appel refreshAppsStatus via
 * ensureAppDetailStatusFetched), version (update-check, rempli après fetch)
 * et nombre d'utilisateurs. Conteneur #appDetailStatusBadges rempli par
 * updateAppDetailStatusBadges() — ré-appelé par les SSE via
 * renderAppRuntimeState (admin-apps.js).
 */
function renderAppDetailStatusRow() {
    return '<div id="appDetailStatusBadges" class="d-flex flex-wrap gap-1 align-items-center mb-3"></div>';
}

function updateAppDetailStatusBadges() {
    const container = document.getElementById('appDetailStatusBadges');
    if (!container || _appDetailSlug === null) return;
    const state = (typeof runtimeStates !== 'undefined') ? runtimeStates[_appDetailSlug] : null;
    container.innerHTML = ''
        + appDetailDockerBadgeHtml(state)
        + appDetailConnectivityBadgeHtml(state)
        + '<span id="appDetailVersionBadge"><span class="badge text-bg-light border">version…</span></span>'
        + appDetailUsersBadgeHtml(_appDetailApp ? _appDetailApp.users : null);
}

/**
 * Badge Docker/connectivité absent des runtimeStates (fiche ouverte avant
 * le test initial) : un appel refreshAppsStatus() une seule fois par
 * ouverture de fiche, puis re-render des badges. Jamais de polling ici.
 */
function ensureAppDetailStatusFetched(slug) {
    if (_appDetailStatusFetchStarted) return;
    const state = (typeof runtimeStates !== 'undefined') ? runtimeStates[slug] : null;
    _appDetailStatusFetchStarted = true;
    if (state && state.health) return; // déjà renseigné : rien à faire
    if (typeof refreshAppsStatus === 'function') {
        Promise.resolve(refreshAppsStatus()).finally(() => updateAppDetailStatusBadges());
    }
}

/** Badge version via update-check (comparaison semver enregistré vs live). */
function loadAppDetailVersionBadge() {
    const slug = _appDetailSlug;
    const app = _appDetailApp;
    if (!slug || !app) return;
    api.checkUpdate(slug).then(updateInfo => {
        if (_appDetailSlug !== slug) return;
        const box = document.getElementById('appDetailVersionBadge');
        if (box) box.innerHTML = appDetailVersionBadgeHtml(app.version, updateInfo);
    }).catch(() => {
        if (_appDetailSlug !== slug) return;
        const box = document.getElementById('appDetailVersionBadge');
        if (box) box.innerHTML = appDetailVersionBadgeHtml(app.version, null);
    });
}

// ===== Cas pending / rejected : fiche réduite =====

/**
 * En-tête + badges workflow + boutons d'approbation uniquement — mêmes
 * fonctions que le tableau (admin-app-approval.js). PAS de sections
 * techniques, PAS de Éditer/Ouvrir.
 */
function renderAppDetailApprovalBlock(app, isPending, isRejected) {
    const notes = [];
    if (isRejected && app.rejection_reason) {
        notes.push('<div class="alert alert-danger py-2 small mb-2"><strong>Motif du rejet :</strong> '
            + escapeHtml(app.rejection_reason) + '</div>');
    }
    notes.push('<div class="text-muted small mb-2">Application hors du parc actif : validez le workflow '
        + 'd\'approbation ou supprimez-la. Les sections techniques sont masquées tant que l\'application '
        + 'n\'est pas approuvée.</div>');

    const buttons = ['<button class="btn btn-sm btn-success me-1" data-action="approveApp"'
        + ' data-arg1="' + escapeAttr(app.slug) + '" title="Approuver l\'application">'
        + '<i class="bi bi-check-lg me-1"></i>Approuver</button>'];
    if (isPending) {
        buttons.push('<button class="btn btn-sm btn-outline-danger me-1" data-app-name="' + escapeAttr(app.name) + '"'
            + ' data-action="showRejectAppModal" data-arg1="' + escapeAttr(app.slug) + '" data-arg2="$el"'
            + ' title="Rejeter l\'application"><i class="bi bi-x-lg me-1"></i>Rejeter</button>');
    }
    buttons.push('<button class="btn btn-sm btn-outline-secondary" data-action="deletePendingApp"'
        + ' data-arg1="' + escapeAttr(app.slug) + '" title="Supprimer l\'application">'
        + '<i class="bi bi-trash me-1"></i>Supprimer</button>');

    return '<div class="card border-0 shadow-sm mb-3"><div class="card-body py-3">'
        + notes.join('')
        + '<div>' + buttons.join('') + '</div>'
        + '</div></div>';
}
