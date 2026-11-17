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
 * sections (maquette « apps-fiche-v2 ») : en-tête (tuile, nom, badges,
 * Ouvrir/Éditer), rangée de badges d'état (Docker, connectivité, version,
 * utilisateurs), mises à jour (dernier déploiement + historique <details>,
 * poll 2 s tant qu'un déploiement est « running » ET la fiche est visible),
 * changelog (markdown minimal + 3 dernières releases), logs (service/tail,
 * zéro polling), Docker (collapse replié + modale « Détails complets »).
 * Cas pending/rejected : fiche réduite (workflow d'approbation uniquement).
 *
 * Aucune animation — charte perf du parc. Boutons SOLID Bootstrap.
 * Tout contenu dynamique est échappé (escapeHtml/escapeAttr) ; les logs sont
 * injectés via textContent (aucune passe HTML).
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
// Séquence des fetchs de logs : la dernière demande gagne (les réponses
// périmées — service/tail changés entre-temps — sont jetées).
let _appDetailLogFetchSeq = 0;

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

/**
 * Rendu markdown MINIMAL (maquette apps-fiche-v2, sans lib) : le texte est
 * échappé AVANT parsing ; titres #/##/### en <strong> (div fw-bold),
 * lignes « - »/« * » en puces (groupe <ul>), reste en paragraphes.
 * Entrée/sortie : string → string, aucune dépendance DOM.
 */
function renderAppDetailMarkdown(markdown) {
    if (!markdown || typeof markdown !== 'string') return '';
    const lines = escapeHtml(markdown).split(/\r?\n/);
    const out = [];
    let inList = false;
    const closeList = () => {
        if (inList) { out.push('</ul>'); inList = false; }
    };
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) { closeList(); continue; }
        const heading = /^#{1,3}\s+(.*)$/.exec(line);
        if (heading) {
            closeList();
            out.push('<div class="fw-bold">' + heading[1] + '</div>');
            continue;
        }
        const bullet = /^[-*]\s+(.*)$/.exec(line);
        if (bullet) {
            if (!inList) { out.push('<ul class="mb-2">'); inList = true; }
            out.push('<li>' + bullet[1] + '</li>');
            continue;
        }
        closeList();
        out.push('<p class="mb-1">' + line + '</p>');
    }
    closeList();
    return out.join('');
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
            : ''
                + renderAppDetailStatusRow()
                + renderAppDetailUpdatesSection()
                + renderAppDetailChangelogSection()
                + renderAppDetailLogsSection()
                + renderAppDetailDockerSection());

    if (!needsApproval) {
        loadAppDetailSections();
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
        + '<span class="rounded d-inline-flex align-items-center justify-content-center flex-shrink-0" style="width:44px;height:44px;background-color:' + escapeAttr(app.color || '#0d6efd') + ';color:#fff">'
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

// ===== Chargement des sections + badges =====

/**
 * Lance les chargements de la fiche active (badges d'état, update-check,
 * déploiements, changelog, logs, docker). Chaque consommateur re-vérifie
 * _appDetailSlug : changer de fiche/outil en cours de route ne rend rien.
 */
function loadAppDetailSections() {
    const slug = _appDetailSlug;
    const app = _appDetailApp;
    if (!slug || !app) return;

    ensureAppDetailStatusFetched(slug);
    updateAppDetailStatusBadges();
    refreshAppDetailDeployments();
    loadAppDetailChangelog(slug);
    loadAppDetailLogs();
    loadAppDetailDocker(slug);

    // Badge version via update-check (comparaison semver enregistré vs live).
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

// ===== Section Mises à jour =====

function renderAppDetailUpdatesSection() {
    return '<div class="card border-0 shadow-sm mb-3">'
        + '<div class="card-header py-2 d-flex align-items-center">'
        + '<strong class="d-flex align-items-center gap-2">' + hubIcon('rocket') + 'Mises à jour</strong>'
        + '<button class="btn btn-sm btn-outline-secondary ms-auto" data-action="refreshAppDetailDeployments"'
        + ' title="Rafraîchir le statut et l\'historique de déploiement">'
        + hubIcon('refresh-cw') + '<span class="ms-1">Actualiser</span></button>'
        + '</div>'
        + '<div class="card-body py-3 small">'
        + '<div id="appDetailDeployments"><span class="text-muted">Chargement…</span></div>'
        + '</div>'
        + '</div>';
}

/** Charge (ou recharge via le bouton Actualiser) le statut de déploiement. */
async function refreshAppDetailDeployments() {
    const slug = _appDetailSlug;
    if (!slug) return;
    const container = document.getElementById('appDetailDeployments');
    if (container) container.innerHTML = '<span class="text-muted">Chargement…</span>';
    try {
        const data = await api.getAppDeployStatus(slug);
        renderAppDetailDeployments(slug, data);
    } catch (error) {
        if (_appDetailSlug !== slug) return;
        const box = document.getElementById('appDetailDeployments');
        if (box) {
            box.innerHTML = '<span class="text-muted">Historique de déploiement indisponible : '
                + escapeHtml(error.message || 'erreur réseau') + '</span>';
        }
        stopAppDetailPolling();
    }
}

/**
 * Rend le dernier déploiement (badge, durée = finished-started,
 * triggered_by, date fr) et l'historique (≤ 10 entrées, dates fr), chaque
 * entrée dans un <details> avec les logs du déploiement dans un <pre>.
 * current.status === 'running' → poll 2 s tant que la fiche est visible.
 */
function renderAppDetailDeployments(slug, data) {
    if (_appDetailSlug !== slug) return;
    const container = document.getElementById('appDetailDeployments');
    if (!container) return;

    const current = data && data.current ? data.current : null;
    const history = Array.isArray(data && data.history) ? data.history : [];

    if (!current && history.length === 0) {
        container.innerHTML = '<span class="text-muted">Aucun déploiement enregistré pour cette application.</span>';
        stopAppDetailPolling();
        return;
    }

    const parts = [];
    if (current) {
        const duration = formatAppDetailDuration(current.started_at, current.finished_at);
        parts.push('<div class="mb-2"><strong>Dernier déploiement :</strong> '
            + appDetailDeployBadgeHtml(current.status)
            + '<span class="text-muted">'
            + (duration ? ' · ' + escapeHtml(duration) : '')
            + (current.triggered_by ? ' · par ' + escapeHtml(current.triggered_by) : '')
            + ' · ' + formatAppDetailDateTime(current.started_at)
            + '</span></div>');
    }

    if (history.length > 0) {
        const items = history.slice(0, 10).map(dep => {
            const duration = formatAppDetailDuration(dep.started_at, dep.finished_at);
            const logs = (typeof dep.logs === 'string' && dep.logs.trim()) ? dep.logs : '(aucun log)';
            return '<details class="border-bottom pb-1 mb-1">'
                + '<summary style="cursor:pointer">' + appDetailDeployBadgeHtml(dep.status)
                + ' <span class="text-muted">' + formatAppDetailDateTime(dep.started_at)
                + (duration ? ' · ' + escapeHtml(duration) : '')
                + (dep.triggered_by ? ' · par ' + escapeHtml(dep.triggered_by) : '')
                + '</span></summary>'
                + '<pre class="bg-light border rounded p-2 font-monospace" style="max-height:220px;overflow:auto;font-size:.75rem">'
                + escapeHtml(logs)
                + '</pre>'
                + '</details>';
        }).join('');
        parts.push('<div class="text-muted mb-1">Historique :</div>' + items);
    }

    container.innerHTML = parts.join('');

    if (current && current.status === 'running') startAppDetailDeployPoll(slug);
    else stopAppDetailPolling();
}

/**
 * Poll 2 s du statut de déploiement TANT QUE la fiche est visible (garde à
 * chaque tick : fiche masquée, autre outil, page admin fermée ou slug
 * changé → stop immédiat). Aucun poll dans les autres cas.
 */
function startAppDetailDeployPoll(slug) {
    if (_appDetailPollTimer && _appDetailPollSlug === slug) return; // déjà en cours
    stopAppDetailPolling();
    _appDetailPollSlug = slug;
    _appDetailPollTimer = setInterval(async () => {
        const pane = document.getElementById('appDetailPane');
        const adminSection = document.getElementById('adminSection');
        if (!pane || pane.classList.contains('d-none')
            || (adminSection && adminSection.classList.contains('d-none'))
            || _appDetailSlug !== slug) {
            stopAppDetailPolling();
            return;
        }
        try {
            const data = await api.getAppDeployStatus(slug);
            renderAppDetailDeployments(slug, data); // redémarre/stoppe selon le statut
        } catch (error) {
            // Erreur transitoire : l'affichage est conservé, retentée au tick suivant.
            console.warn('[admin-app-detail] Poll déploiement échoué :', error);
        }
    }, 2000);
}

// ===== Section Changelog =====

function renderAppDetailChangelogSection() {
    const repoUrl = appDetailGithubUrl(_appDetailApp ? _appDetailApp.git_repo_url : null);
    const link = repoUrl
        ? '<a class="small" href="' + escapeAttr(repoUrl + '/blob/HEAD/CHANGELOG.md') + '" target="_blank" rel="noopener"'
            + ' title="CHANGELOG.md sur GitHub">CHANGELOG complet ↗</a>'
        : '';
    return '<div class="card border-0 shadow-sm mb-3">'
        + '<div class="card-header py-2 d-flex align-items-center">'
        + '<strong class="d-flex align-items-center gap-2">' + hubIcon('file-text') + 'Changelog</strong>'
        + (link ? '<span class="ms-auto">' + link + '</span>' : '')
        + '</div>'
        + '<div class="card-body py-3 small">'
        + '<div id="appDetailChangelogBody"><span class="text-muted">Chargement…</span></div>'
        + '<div id="appDetailReleasesBody"></div>'
        + '</div>'
        + '</div>';
}

/** Changelog (markdown minimal) + 3 dernières releases (tag + date). */
async function loadAppDetailChangelog(slug) {
    const results = await Promise.allSettled([
        api.getChangelog(slug),
        api.getReleases(slug),
    ]);
    if (_appDetailSlug !== slug) return; // fiche changée entre-temps

    const body = document.getElementById('appDetailChangelogBody');
    if (body) {
        if (results[0].status === 'fulfilled') {
            const changelog = results[0].value;
            if (changelog && changelog.ok && changelog.markdown) {
                body.innerHTML = renderAppDetailMarkdown(changelog.markdown);
            } else if (changelog && changelog.repo_configured === false) {
                body.innerHTML = '<span class="text-muted">Aucun dépôt GitHub configuré pour cette application.</span>';
            } else {
                body.innerHTML = '<span class="text-muted">Changelog indisponible pour cette application.</span>';
            }
        } else {
            const reason = results[0].reason && results[0].reason.message ? results[0].reason.message : 'erreur réseau';
            body.innerHTML = '<span class="text-muted">Changelog indisponible : ' + escapeHtml(reason) + '</span>';
        }
    }

    const releasesBox = document.getElementById('appDetailReleasesBody');
    if (releasesBox) {
        const releases = (results[1].status === 'fulfilled' && results[1].value && Array.isArray(results[1].value.releases))
            ? results[1].value.releases.slice(0, 3)
            : [];
        if (releases.length > 0) {
            releasesBox.innerHTML = '<hr class="my-2"><div class="text-muted mb-1">Dernières releases :</div>'
                + releases.map(r => '<div class="d-flex align-items-center gap-2">'
                    + '<code>' + escapeHtml(r.tag || '—') + '</code>'
                    + '<span class="text-muted">' + formatAppDetailDateTime(r.published_at) + '</span>'
                    + '</div>').join('');
        } else {
            releasesBox.innerHTML = '';
        }
    }
}

// ===== Section Logs =====

function renderAppDetailLogsSection() {
    return '<div class="card border-0 shadow-sm mb-3">'
        + '<div class="card-header py-2 d-flex align-items-center flex-wrap gap-2">'
        + '<strong class="d-flex align-items-center gap-2">' + hubIcon('terminal') + 'Logs</strong>'
        + '<div class="ms-auto d-flex align-items-center gap-2">'
        + '<select class="form-select form-select-sm w-auto" id="appDetailLogService" data-action="loadAppDetailLogs" aria-label="Service des logs">'
        + '<option value="api">API</option>'
        + '<option value="frontend">Frontend</option>'
        + '<option value="postgres">PostgreSQL</option>'
        + '</select>'
        + '<select class="form-select form-select-sm w-auto" id="appDetailLogTail" data-action="loadAppDetailLogs" aria-label="Nombre de lignes">'
        + '<option value="50">50 lignes</option>'
        + '<option value="150" selected>150 lignes</option>'
        + '<option value="500">500 lignes</option>'
        + '</select>'
        + '<button class="btn btn-sm btn-secondary" data-action="loadAppDetailLogs" title="Recharger les logs">'
        + hubIcon('refresh-cw') + '<span class="ms-1">Actualiser</span></button>'
        + '</div>'
        + '</div>'
        + '<div class="card-body py-3">'
        + '<pre id="appDetailLogsPre" class="bg-light border rounded p-2 font-monospace" style="max-height:320px;overflow:auto;font-size:.8rem;margin-bottom:0">Chargement…</pre>'
        + '</div>'
        + '</div>';
}

/**
 * Charge les logs (service/tail lus dans les <select>). Réponses ordonnées
 * par séquence : changer service/tail en rafale n'affiche que la dernière
 * demande. Erreur → ligne muted inline (textContent, jamais de popup).
 * AUCUN polling.
 */
async function loadAppDetailLogs() {
    const slug = _appDetailSlug;
    const pre = document.getElementById('appDetailLogsPre');
    if (!slug || !pre) return;
    const service = document.getElementById('appDetailLogService')?.value || 'api';
    const tail = parseInt(document.getElementById('appDetailLogTail')?.value || '150', 10) || 150;
    const seq = ++_appDetailLogFetchSeq;
    pre.textContent = 'Chargement des logs (' + service + ', ' + tail + ' dernières lignes)…';
    try {
        const data = await api.getAppLogs(slug, tail, service);
        if (_appDetailSlug !== slug || seq !== _appDetailLogFetchSeq) return; // réponse périmée
        const lines = Array.isArray(data && data.lines) ? data.lines : [];
        pre.textContent = lines.length > 0 ? lines.join('\n') : '(aucune ligne de log)';
    } catch (error) {
        if (_appDetailSlug !== slug || seq !== _appDetailLogFetchSeq) return;
        pre.textContent = 'Erreur : ' + (error.message || 'récupération impossible') + ' — réessayez avec « Actualiser ».';
    }
}

// ===== Section Docker =====

/**
 * Collapse Bootstrap replié par défaut : header = titre + résumé muted
 * (CPU / RAM / uptime, alimenté après fetch) + chevron ; corps = résumé
 * conteneur (nom, image, ports) + bouton « Détails complets » (modale
 * existante showDockerDetails, admin-app-form.js — aucune duplication).
 */
function renderAppDetailDockerSection() {
    return '<div class="card border-0 shadow-sm mb-3">'
        + '<div class="card-header py-2">'
        + '<div class="d-flex align-items-center gap-2" role="button" tabindex="0"'
        + ' data-bs-toggle="collapse" data-bs-target="#appDetailDockerCollapse"'
        + ' aria-expanded="false" aria-controls="appDetailDockerCollapse" style="cursor:pointer">'
        + '<strong class="d-flex align-items-center gap-2">' + hubIcon('box') + 'Docker</strong>'
        + '<span class="text-muted small" id="appDetailDockerSummary">chargement…</span>'
        + '<i class="bi bi-chevron-down ms-auto" aria-hidden="true"></i>'
        + '</div>'
        + '</div>'
        + '<div class="collapse" id="appDetailDockerCollapse">'
        + '<div class="card-body py-3 small" id="appDetailDockerBody"><span class="text-muted">Chargement…</span></div>'
        + '</div>'
        + '</div>';
}

async function loadAppDetailDocker(slug) {
    let info = null;
    let fetchError = null;
    try {
        info = await api.getAppDockerInfo(slug);
    } catch (error) {
        fetchError = error;
    }
    if (_appDetailSlug !== slug) return; // fiche changée entre-temps

    const summary = document.getElementById('appDetailDockerSummary');
    const body = document.getElementById('appDetailDockerBody');

    if (fetchError) {
        if (summary) summary.textContent = '';
        if (body) {
            body.innerHTML = '<span class="text-muted">Informations Docker indisponibles : '
                + escapeHtml(fetchError.message || 'erreur réseau') + '</span>';
        }
        return;
    }

    const stats = info && info.container_stats ? info.container_stats : null;
    const containerFound = Boolean(info && info.container_found);
    if (summary) {
        if (!containerFound) {
            summary.textContent = (info && info.docker_available === false) ? 'scan désactivé' : 'conteneur non trouvé';
        } else if (stats) {
            summary.textContent = 'CPU ' + (Number(stats.cpu_percent) || 0).toFixed(1)
                + ' % · RAM ' + (Number(stats.memory_usage_mb) || 0).toFixed(1)
                + ' Mo · uptime ' + formatAppDetailUptime(stats.uptime_seconds);
        } else {
            summary.textContent = '';
        }
    }
    if (!body) return;

    if (!containerFound) {
        body.innerHTML = '<span class="text-muted">'
            + (info && info.docker_available === false
                ? 'Scan Docker désactivé (socket non monté dans le Hub).'
                : 'Conteneur non trouvé pour cette application.')
            + '</span>';
        return;
    }

    const ports = Array.isArray(info.ports) ? info.ports : [];
    const portsHtml = ports.length > 0
        ? ports.map(p => '<div>' + escapeHtml(String(p.container_port ?? '—')) + ' → '
            + (p.host_port ? '<strong>' + escapeHtml(String(p.host_port)) + '</strong>' : '<span class="text-muted">—</span>')
            + ' <span class="badge text-bg-light border">' + escapeHtml(String(p.protocol || 'tcp')) + '</span>'
            + '</div>').join('')
        : '<span class="text-muted">Aucun port exposé</span>';

    body.innerHTML = ''
        + '<div class="row g-2">'
        + '<div class="col-md-6">'
        + '<div class="text-muted">Conteneur</div><div><code>' + escapeHtml(info.container_name || '—') + '</code></div>'
        + '<div class="text-muted mt-1">Image</div><div class="text-break"><code>' + escapeHtml(info.container_image || '—') + '</code></div>'
        + '</div>'
        + '<div class="col-md-6"><div class="text-muted">Ports</div>' + portsHtml + '</div>'
        + '</div>'
        + '<div class="mt-2">'
        + '<button class="btn btn-sm btn-outline-secondary" data-action="showDockerDetails" data-arg1="' + escapeAttr(slug) + '"'
        + ' title="Variables APP_*, labels, réseaux, ressources (modale)">Détails complets</button>'
        + '</div>';
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
