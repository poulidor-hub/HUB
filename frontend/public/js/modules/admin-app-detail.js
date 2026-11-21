// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - fiche application v6 (maquette « fiche-grid-v6 »)
 *
 * Fiche détaillée d'une application, ouverte par clic sur une ligne du
 * tableau ou bouton « Fiche » (openAppDetail — câblé par admin-apps.js,
 * passe 1). La fiche vit DANS la vue outil Applications : bascule
 * d'affichage entre la liste (#appsListWrap) et la fiche (#appDetailPane) —
 * pas une nouvelle page admin. Le hash porte un 3ᵉ segment
 * (#admin/apps/<slug>) géré par admin-hub.js : refresh / lien direct /
 * back-forward rouvrent la fiche ; #admin/apps remet la liste.
 *
 * v6 (grille 2 colonnes) : le SQUELETTE de la fiche est statique dans
 * index.html (#appDetailPane) — en-tête (tuile, nom + badges, slug/URL,
 * mini-cockpit, Ouvrir), rangée de badges d'état, grille flex 2 colonnes
 * indépendantes (.fgrid/.fcol) dont les zones identifiées sont remplies par
 * ce module. Aucun innerHTML global : les listeners posés au DOMContentLoaded
 * (init.js) survivent aux ouvertures/fermetures de la fiche.
 *
 * Mini-cockpit de l'en-tête : 4 chips (CPU %, RAM Mo, Réseau ↓/↑, Latence
 * ms) avec micro-sparklines SVG — buffer de 30 points max par métrique,
 * redessinées à chaque tick, STATIQUES (aucune animation — charte perf).
 * Source unique : GET /apps/{slug}/docker-info (container_stats : cpu_percent,
 * memory_*, uptime_seconds + compteurs réseau CUMULÉS net_rx_bytes /
 * net_tx_bytes — le débit ↓/↑ est calculé ici par delta entre deux ticks
 * rapporté aux secondes écoulées ; garde division par zéro au 1ᵉʳ tick).
 * Polling 10 s UNIQUEMENT tant que la fiche est visible (start/stop aux
 * ouvertures/retours, même mécanique que le poll « déploiement en cours »).
 * Conteneur arrêté/injoignable → chips en état « — » muted (jamais de
 * popup). Latence : runtimeStates (sonde /apps/status + SSE), dernier état
 * connu sinon « — ».
 *
 * Cas pending/rejected : fiche réduite (workflow d'approbation uniquement),
 * rendue dans #appDetailReducedPane — le squelette n'est jamais détruit.
 *
 * Boutons SOLID Bootstrap. Tout contenu dynamique est échappé
 * (escapeHtml/escapeAttr) ; les logs sont injectés via textContent.
 */

// ===== État de la fiche =====

// Slug affiché, '__new__' (mode création) ou null (fiche fermée / liste).
let _appDetailSlug = null;
// Objet app affiché (cache allApps en priorité, sinon fetch /apps/all).
let _appDetailApp = null;
// Timer du poll « déploiement en cours » (2 s), null si aucun — piloté
// par la section Mises à jour ; stop général via stopAppDetailPolling().
let _appDetailPollTimer = null;
let _appDetailPollSlug = null;
// Timer du poll mini-cockpit (10 s), null si aucun — démarré à l'ouverture
// d'une fiche active, stoppé à chaque fermeture/retour liste.
let _appDetailCockpitTimer = null;
let _appDetailCockpitSlug = null;
// Garde : le test de connectivité de secours ne part qu'une fois par fiche.
let _appDetailStatusFetchStarted = false;
// Séquence des fetchs de logs : la dernière demande gagne (les réponses
// périmées — service/tail changés entre-temps — sont jetées).
let _appDetailLogFetchSeq = 0;

// ===== État du mini-cockpit =====

// Buffer 30 points max par métrique (même principe que le cockpit système) :
// l'axe X est réparti uniformément, l'axe Y normalisé min/max du buffer.
const APP_DETAIL_COCKPIT_MAX_POINTS = 30;
const APP_DETAIL_COCKPIT_POLL_MS = 10000;
let _cockpitPoints = { cpu: [], ram: [], net: [], latency: [] };
// Dernier relevé exploitable (valeurs affichées des chips CPU/RAM) : null si
// le conteneur est arrêté/injoignable ou avant le 1ᵉʳ tick utile.
let _cockpitLast = null;
// Dernier échantillon réseau brut { slug, rx, tx, at } : sert de base au
// delta du tick suivant. Null au 1ᵉʳ tick → pas de débit affiché (« — »).
let _cockpitPrevNet = null;

// ===== Helpers purs (formats, badges, sparklines) =====

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

/** Nombre → format fr (« 2,1 », « 86 »), « — » si non fini. Décimales bornées. */
function formatAppDetailFrNumber(value, maxDigits = 1) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString('fr-FR', { minimumFractionDigits: 0, maximumFractionDigits: maxDigits });
}

/**
 * Débit o/s → chaîne fr auto Ko/s–Mo/s (« 340 o/s », « 2,1 Ko/s », « 1,2 Mo/s »).
 * « — » si non fini/négatif (inclut le 1ᵉʳ tick sans échantillon précédent).
 */
function formatAppDetailRate(bytesPerSec) {
    if (bytesPerSec === null || bytesPerSec === undefined || bytesPerSec === '') return '—';
    const n = Number(bytesPerSec);
    if (!Number.isFinite(n) || n < 0) return '—';
    if (n >= 1048576) return formatAppDetailFrNumber(n / 1048576, 1) + ' Mo/s';
    if (n >= 1024) return formatAppDetailFrNumber(n / 1024, n >= 10240 ? 0 : 1) + ' Ko/s';
    return Math.round(n) + ' o/s';
}

/**
 * Poussé dans un buffer sparkline : valeurs non finies ignorées, taille
 * plafonnée (FIFO) à `max` points (30 par défaut — mini-cockpit fiche).
 */
function appDetailPushPoint(buffer, value, max = APP_DETAIL_COCKPIT_MAX_POINTS) {
    if (!Array.isArray(buffer)) return buffer;
    const v = Number(value);
    if (!Number.isFinite(v)) return buffer;
    buffer.push(v);
    while (buffer.length > max) buffer.shift();
    return buffer;
}

/**
 * Points SVG d'une sparkline (viewBox 100×22, marge 2) : x réparti sur les
 * n-1 intervalles, y normalisé min/max du buffer inversé (haut = valeur
 * haute). < 2 points → '' (polyline vide) ; palier constant → ligne médiane
 * (min/max écartés artificiellement pour éviter une division par 0).
 */
function appDetailSparkPoints(values, width = 100, height = 22, pad = 2) {
    const pts = (Array.isArray(values) ? values : [])
        .map(v => Number(v))
        .filter(v => Number.isFinite(v));
    const n = pts.length;
    if (n < 2) return '';
    let min = Math.min(...pts);
    let max = Math.max(...pts);
    if (max - min < 1e-9) { min -= 1; max += 1; }
    const stepX = width / (n - 1);
    const fmt = v => {
        const s = Number(v).toFixed(1);
        return s.endsWith('.0') ? s.slice(0, -2) : s; // « 20 » plutôt que « 20.0 »
    };
    return pts.map((v, i) => {
        const x = fmt(i * stepX);
        const y = fmt(height - pad - ((v - min) / (max - min)) * (height - 2 * pad));
        return x + ',' + y;
    }).join(' ');
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

/** Badge Docker de la rangée d'état v6 : « Docker : en marche » / « arrêté » / « inconnu ». */
function appDetailDockerBadgeHtml(state) {
    const docker = state ? state.docker : null;
    if (!docker) return '<span class="badge text-bg-light border">Docker : inconnu</span>';
    if (docker === 'running') return '<span class="badge text-bg-success">Docker : en marche</span>';
    return '<span class="badge text-bg-warning">Docker : arrêté</span>';
}

/**
 * Badge Health v6 : « Health : en ligne · N ms » (success) / « hors ligne »
 * (danger) / « stoppée » (secondary neutre — app volontairement arrêtée).
 * Sonde indisponible → '' (pas de badge — la rangée s'allège,
 * maquette v6 ; l'ancien badge « Vérification » bruitait la fiche).
 */
function appDetailHealthBadgeHtml(state) {
    const health = state ? state.health : null;
    const latency = state ? state.latency_ms : null;
    if (health === 'healthy') {
        return '<span class="badge text-bg-success">Health : en ligne'
            + (latency ? ' · ' + escapeHtml(String(latency)) + ' ms' : '') + '</span>';
    }
    if (health === 'stopped') return '<span class="badge text-bg-secondary">Health : stoppée</span>';
    if (health === 'unhealthy') return '<span class="badge text-bg-danger">Health : hors ligne</span>';
    return '';
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
    // Sentinelle de création (v6) : '__new__' (interne) et 'new' (hash
    // #admin/apps/new, toolbar « Ajouter ») → fiche en mode création.
    if (slug === 'new' || slug === '__new__') return openAppDetailCreate();
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

    // Rend l'app visible par les flux par id (allApps) quand elle ne vient
    // pas du tableau : ajout local, le tableau se re-rend de lui-même au
    // prochain loadAdminData (cache remplacé en bloc).
    if (typeof allApps !== 'undefined' && Array.isArray(allApps) && !allApps.some(a => a && a.id === app.id)) {
        allApps.push(app);
    }

    stopAppDetailPolling();
    resetAppDetailCockpit();
    _appDetailSlug = slug;
    _appDetailApp = app;
    _appDetailStatusFetchStarted = false;

    const list = document.getElementById('appsListWrap');
    if (list) list.classList.add('d-none');
    pane.classList.remove('d-none');
    try { window.location.hash = 'admin/apps/' + slug; } catch (e) { /* hash refusé */ }

    renderAppDetailPane();
}

/**
 * Ouvre la fiche en mode CRÉATION (v6 — « Ajouter », flux GitHub, hash
 * #admin/apps/new) : la même fiche avec Réglages vide (resetAppFormForCreate
 * — nom vide, slug auto-généré à la frappe, ports alloués automatiquement),
 * sections techniques masquées et badges remplacés par l'encart « Nouvelle
 * application ». L'enregistrement repasse par handleSaveApp (POST /apps/ +
 * auto-deploy si un dépôt Git est configuré), puis retour liste.
 */
function openAppDetailCreate() {
    const pane = document.getElementById('appDetailPane');
    if (!pane) { console.warn('[admin-app-detail] #appDetailPane introuvable'); return; }
    // Garde no-op : déjà en création (double évènement clic + hash).
    if (_appDetailSlug === '__new__' && !pane.classList.contains('d-none')) return;

    stopAppDetailPolling();
    resetAppDetailCockpit();
    _appDetailSlug = '__new__';
    _appDetailApp = null;
    _appDetailStatusFetchStarted = false;

    const list = document.getElementById('appsListWrap');
    if (list) list.classList.add('d-none');
    pane.classList.remove('d-none');
    try { window.location.hash = 'admin/apps/new'; } catch (e) { /* hash refusé */ }

    renderAppDetailPane();
    // Reset du formulaire inline APRÈS le rendu des zones (sync — aucun
    // fetch intermédiaire, contrairement à l'ancienne modale).
    if (typeof resetAppFormForCreate === 'function') resetAppFormForCreate();
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

/**
 * Masque la fiche et restaure la liste (aucune écriture du hash). Le
 * SQUELETTE STATIQUE n'est PAS vidé : les listeners DOMContentLoaded
 * (génération du slug, preview icône/couleur) doivent survivre ; chaque
 * ouverture re-remplit les zones avant affichage.
 */
function hideAppDetailPane() {
    stopAppDetailPolling();
    resetAppDetailCockpit();
    _appDetailSlug = null;
    _appDetailApp = null;
    const pane = document.getElementById('appDetailPane');
    if (pane) pane.classList.add('d-none');
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

/** Stoppe les polls de la fiche (déploiement 2 s + mini-cockpit 10 s) — idempotent. */
function stopAppDetailPolling() {
    if (_appDetailPollTimer) {
        clearInterval(_appDetailPollTimer);
        _appDetailPollTimer = null;
    }
    _appDetailPollSlug = null;
    stopAppDetailCockpitPoll();
}

/**
 * Re-rend la fiche courante avec des données fraîches (appelé après une
 * modification : sauvegarde des réglages inline, approbation, rejet,
 * suppression). Si l'app n'existe plus, retour propre à la liste.
 */
function refreshAppDetail() {
    const slug = _appDetailSlug;
    if (!slug || slug === '__new__') return;
    const pane = document.getElementById('appDetailPane');
    if (!pane || pane.classList.contains('d-none')) return;
    // Réinitialise la garde no-op d'openAppDetail pour forcer le re-render.
    _appDetailSlug = null;
    _appDetailApp = null;
    openAppDetail(slug);
}

// ===== Rendu de la fiche (squelette statique — zones identifiées) =====

function renderAppDetailPane() {
    const slug = _appDetailSlug;
    const app = _appDetailApp;
    const pane = document.getElementById('appDetailPane');
    if (!pane) return;

    // Mode création (slug sentinelle '__new__') : identité « Nouvelle
    // application », Réglages seul (reset effectué par openAppDetailCreate),
    // aucune section technique ni mini-cockpit.
    if (slug === '__new__') {
        fillAppDetailIdentity(null);
        setAppDetailMode('create');
        return;
    }
    if (!app || slug !== app.slug) return;

    const isPending = app.approval_status === 'pending';
    const isRejected = app.approval_status === 'rejected';
    const needsApproval = isPending || isRejected;

    fillAppDetailIdentity(app);

    if (needsApproval) {
        // Fiche réduite : pas de mini-cockpit, pas de Réglages, pas de
        // sections techniques — le workflow d'approbation uniquement.
        setAppDetailMode('approval');
        const reduced = document.getElementById('appDetailReducedPane');
        if (reduced) reduced.innerHTML = renderAppDetailApprovalBlock(app, isPending, isRejected);
        return;
    }

    setAppDetailMode('view');
    // Réglages inline : préfill du formulaire avec les données courantes à
    // chaque ouverture/re-render (fonction existante — admin-app-form.js).
    if (typeof prefillAppFormFromApp === 'function') prefillAppFormFromApp(app);
    loadAppDetailSections();
}

/**
 * Remplit la zone identité de l'en-tête (tuile icône/couleur, nom + badges
 * partagé/Atelier/Non gérée/workflow, slug + URL muted) et les actions
 * (« Ouvrir » — l'édition vit DANS la fiche v6, plus de bouton Éditer).
 */
function fillAppDetailIdentity(app) {
    const tile = document.getElementById('appDetailTile');
    const tileIcon = document.getElementById('appDetailTileIcon');
    const nameEl = document.getElementById('appDetailName');
    const badgesEl = document.getElementById('appDetailNameBadges');
    const subEl = document.getElementById('appDetailSub');
    const actionsEl = document.getElementById('appDetailHeaderActions');
    if (!tile || !tileIcon || !nameEl || !badgesEl || !subEl || !actionsEl) return;

    if (!app) {
        // Mode création : identité dédiée (pas encore d'app enregistrée).
        tile.style.backgroundColor = '#0d6efd';
        tileIcon.className = 'bi bi-app';
        nameEl.textContent = 'Nouvelle application';
        badgesEl.innerHTML = '';
        subEl.textContent = 'Renseignez les réglages puis enregistrez — le déploiement suit automatiquement si un dépôt Git est configuré.';
        actionsEl.innerHTML = '';
        return;
    }

    tile.style.backgroundColor = app.color || '#0d6efd';
    tileIcon.className = app.icon || 'bi-app';
    nameEl.textContent = app.name || app.slug;

    const badges = [];
    if (app.is_shared_service) badges.push('<span class="badge text-bg-info" title="API partagée accessible par tous les Service Accounts">partagé</span>');
    if (app.is_workshop) badges.push('<span class="badge text-bg-warning">Atelier</span>');
    if (app.is_unmanaged) badges.push('<span class="badge text-bg-secondary">Non gérée</span>');
    if (app.approval_status === 'pending') badges.push('<span class="badge text-bg-warning">En attente d\'approbation</span>');
    if (app.approval_status === 'rejected') badges.push('<span class="badge text-bg-danger">Rejetée</span>');
    badgesEl.innerHTML = badges.join('');

    subEl.innerHTML = '<code>' + escapeHtml(app.slug) + '</code>'
        + (app.frontend_url ? ' · <span class="text-break">' + escapeHtml(app.frontend_url) + '</span>' : '');

    // Actions : « Ouvrir » seul (v6 — plus de bouton Éditer dans l'en-tête).
    actionsEl.innerHTML = app.frontend_url
        ? '<a class="btn btn-sm btn-primary" href="' + escapeAttr(app.frontend_url) + '" target="_blank" rel="noopener"'
            + ' title="Ouvrir l\'application dans un nouvel onglet">'
            + hubIcon('external-link') + '<span class="ms-1">Ouvrir</span></a>'
        : '';
}

/**
 * Bascule les zones du squelette selon le mode :
 * - 'view'     : fiche complète (mini-cockpit, badges, grille 2 colonnes) ;
 * - 'create'   : création — Réglages seul (colonne droite + Logs masqués),
 *                encart « Nouvelle application » à la place des badges ;
 * - 'approval' : fiche réduite pending/rejected (en-tête + bloc workflow,
 *                grille et mini-cockpit masqués).
 */
function setAppDetailMode(mode) {
    const show = (id, visible) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('d-none', !visible);
    };
    const isView = mode === 'view';
    const isCreate = mode === 'create';
    show('appDetailMiniMonitor', isView);
    show('appDetailHeaderActions', isView);
    show('appDetailStatusBadges', isView);
    show('appDetailUptimeNote', isView);
    show('appDetailCreateNote', isCreate);
    show('appDetailGrid', mode !== 'approval');
    show('appDetailColRight', isView);
    show('appDetailLogsCard', isView);
    show('appDetailSettingsCard', mode !== 'approval');
    show('appDetailReducedPane', mode === 'approval');
    if (!isView) {
        const note = document.getElementById('appDetailUptimeNote');
        if (note) note.textContent = '';
    }
}

// ===== Mini-cockpit (en-tête) =====

/** Réinitialise buffers + dernier relevé + échantillon réseau du cockpit. */
function resetAppDetailCockpit() {
    _cockpitPoints = { cpu: [], ram: [], net: [], latency: [] };
    _cockpitLast = null;
    _cockpitPrevNet = null;
}

/**
 * Poll 10 s du mini-cockpit TANT QUE la fiche est visible (garde à chaque
 * tick : fiche masquée, autre outil, slug changé, mode création → stop).
 * Démarré à l'ouverture d'une fiche active (loadAppDetailSections), stoppé
 * par stopAppDetailPolling à chaque fermeture/retour.
 */
function startAppDetailCockpitPoll(slug) {
    if (_appDetailCockpitTimer && _appDetailCockpitSlug === slug) return;
    stopAppDetailCockpitPoll();
    _appDetailCockpitSlug = slug;
    loadAppDetailCockpit(); // 1ᵉʳ relevé immédiat (les suivants : 1×/10 s)
    _appDetailCockpitTimer = setInterval(() => {
        const pane = document.getElementById('appDetailPane');
        const adminSection = document.getElementById('adminSection');
        if (!pane || pane.classList.contains('d-none')
            || (adminSection && adminSection.classList.contains('d-none'))
            || _appDetailSlug !== slug || _appDetailSlug === '__new__') {
            stopAppDetailCockpitPoll();
            return;
        }
        loadAppDetailCockpit();
    }, APP_DETAIL_COCKPIT_POLL_MS);
}

/** Stoppe le poll mini-cockpit s'il tourne (idempotent). */
function stopAppDetailCockpitPoll() {
    if (_appDetailCockpitTimer) {
        clearInterval(_appDetailCockpitTimer);
        _appDetailCockpitTimer = null;
    }
    _appDetailCockpitSlug = null;
}

/**
 * Un tick du mini-cockpit : fetch docker-info, pousse CPU/RAM/débit réseau
 * dans les buffers 30 points et re-rend les chips. Erreur réseau /
 * conteneur arrêté → état « — » muted (jamais de popup). Met aussi à jour
 * le résumé Docker (section repliée) et la note uptime de la rangée badges.
 */
async function loadAppDetailCockpit() {
    const slug = _appDetailSlug;
    if (!slug || slug === '__new__') return;

    let info = null;
    try {
        info = await api.getAppDockerInfo(slug);
    } catch (error) {
        info = null; // injoignable : traité comme conteneur non trouvé
    }
    if (_appDetailSlug !== slug) return; // fiche changée/fermée entre-temps

    const stats = info && info.container_found && info.container_stats ? info.container_stats : null;
    const running = Boolean(info
        && info.container_found
        && (info.container_status || 'running') === 'running'
        && stats);

    if (!running) {
        // Conteneur arrêté / scan désactivé / stats absentes : chips « — »
        // muted, buffers techniques vidés (redémarrage → repart à zéro).
        // La latence garde le dernier état connu (runtimeStates).
        _cockpitLast = null;
        _cockpitPoints.cpu = [];
        _cockpitPoints.ram = [];
        _cockpitPoints.net = [];
        _cockpitPrevNet = null;
        renderAppDetailMiniCockpit({ unavailable: true });
        updateAppDetailDockerSummary(null);
        updateAppDetailUptimeNote(null);
        return;
    }

    const cpu = Number(stats.cpu_percent);
    const memUse = Number(stats.memory_usage_mb);
    const memLimit = Number(stats.memory_limit_mb);
    if (Number.isFinite(cpu)) appDetailPushPoint(_cockpitPoints.cpu, Math.max(0, cpu));
    if (Number.isFinite(memUse)) appDetailPushPoint(_cockpitPoints.ram, Math.max(0, memUse));
    _cockpitLast = {
        cpu: Number.isFinite(cpu) ? Math.max(0, cpu) : null,
        memUse: Number.isFinite(memUse) ? Math.max(0, memUse) : null,
        memLimit: Number.isFinite(memLimit) ? Math.max(0, memLimit) : null,
        uptime: stats.uptime_seconds,
    };

    // Débit réseau : delta des compteurs CUMULÉS entre deux ticks rapporté
    // aux secondes écoulées (horloge murale). Garde division par zéro au
    // 1ᵉʳ tick : sans échantillon précédent (ou après un changement de
    // fiche), pas de débit — on ne mémorise que les compteurs pour le tick
    // suivant. Compteurs qui reculent (redémarrage conteneur) → pas de
    // débit affiché ce tick (les compteurs repartent de zéro).
    const now = Date.now();
    const rx = Number(stats.net_rx_bytes);
    const tx = Number(stats.net_tx_bytes);
    let rxRate = null;
    let txRate = null;
    if (_cockpitPrevNet && _cockpitPrevNet.slug === slug) {
        const elapsedSec = (now - _cockpitPrevNet.at) / 1000;
        const countersOk = Number.isFinite(rx) && Number.isFinite(tx)
            && Number.isFinite(_cockpitPrevNet.rx) && Number.isFinite(_cockpitPrevNet.tx)
            && rx >= _cockpitPrevNet.rx && tx >= _cockpitPrevNet.tx;
        if (elapsedSec > 0 && countersOk) {
            rxRate = Math.max(0, (rx - _cockpitPrevNet.rx) / elapsedSec);
            txRate = Math.max(0, (tx - _cockpitPrevNet.tx) / elapsedSec);
        }
    }
    _cockpitPrevNet = { slug, rx, tx, at: now };
    if (rxRate !== null && txRate !== null) {
        appDetailPushPoint(_cockpitPoints.net, Math.max(0, rxRate + txRate));
    }

    renderAppDetailMiniCockpit({ rxRate, txRate });
    updateAppDetailDockerSummary(stats);
    updateAppDetailUptimeNote(stats.uptime_seconds);
}

/**
 * Écrit une chip du mini-cockpit (valeur, sous-titre, polyline sparkline,
 * état muted/up/down). Toutes les zones sont statiques (index.html) :
 * seule la polyline voit son attribut points redessiné par tick.
 */
function appDetailSetChip(chipId, valId, subId, sparkId, text, subText, points, muted, tone) {
    const chip = document.getElementById(chipId);
    if (!chip) return;
    chip.classList.toggle('muted', !!muted);
    chip.classList.toggle('up', tone === 'up');
    chip.classList.toggle('down', tone === 'down');
    const val = document.getElementById(valId);
    if (val) val.textContent = text;
    const sub = subId ? document.getElementById(subId) : null;
    if (sub) {
        sub.textContent = subText || '';
        sub.classList.toggle('d-none', !subText);
    }
    const poly = document.getElementById(sparkId);
    if (poly) poly.setAttribute('points', points || '');
}

/**
 * Re-rend les 4 chips du mini-cockpit depuis les buffers + dernier relevé.
 * opts.rxRate/txRate : débits calculés au tick courant (null → « — »).
 * opts.unavailable : conteneur arrêté/injoignable → CPU/RAM/Réseau « — »
 * muted ; la latence reste au dernier état connu (runtimeStates).
 */
function renderAppDetailMiniCockpit(opts = {}) {
    const unavailable = !!opts.unavailable;
    const state = (typeof runtimeStates !== 'undefined') ? runtimeStates[_appDetailSlug] : null;
    const last = unavailable ? null : _cockpitLast;

    // CPU %
    appDetailSetChip('appDetailChipCpu', 'appDetailCpuVal', null, 'appDetailCpuSpark',
        last && last.cpu !== null ? formatAppDetailFrNumber(last.cpu, 1) + ' %' : '—', '',
        unavailable ? '' : appDetailSparkPoints(_cockpitPoints.cpu),
        unavailable || !last, null);

    // RAM Mo + « sur X Mo · N % »
    const ramSub = (last && last.memUse !== null && last.memLimit > 0)
        ? 'sur ' + Math.round(last.memLimit) + ' Mo · '
            + Math.round((last.memUse / last.memLimit) * 100) + ' %'
        : '';
    appDetailSetChip('appDetailChipRam', 'appDetailRamVal', 'appDetailRamSub', 'appDetailRamSpark',
        last && last.memUse !== null ? Math.round(last.memUse) + ' Mo' : '—', ramSub,
        unavailable ? '' : appDetailSparkPoints(_cockpitPoints.ram),
        unavailable || !last, null);

    // Réseau ↓ / ↑ (débits calculés par delta entre 2 ticks)
    const rxRate = Number.isFinite(Number(opts.rxRate)) ? Number(opts.rxRate) : null;
    const txRate = Number.isFinite(Number(opts.txRate)) ? Number(opts.txRate) : null;
    const netText = (rxRate !== null && txRate !== null)
        ? '↓ ' + formatAppDetailRate(rxRate) + ' · ↑ ' + formatAppDetailRate(txRate)
        : '—';
    appDetailSetChip('appDetailChipNet', 'appDetailNetVal', null, 'appDetailNetSpark',
        netText, '',
        unavailable ? '' : appDetailSparkPoints(_cockpitPoints.net),
        unavailable || (rxRate === null && txRate === null), null);

    // Latence : runtimeStates (sonde + SSE) — dernier état connu sinon « — ».
    // App volontairement arrêtée (stopped) : sous-titre neutre, pas de tone
    // down (rouge) — un arrêt délibéré n'est pas une alerte.
    const latency = state && state.health === 'healthy' && Number.isFinite(Number(state.latency_ms))
        ? Number(state.latency_ms) : null;
    const latencySub = state
        ? (state.health === 'healthy' ? 'en ligne'
            : (state.health === 'stopped' ? 'stoppée' : 'hors ligne'))
        : '';
    appDetailSetChip('appDetailChipLatency', 'appDetailLatencyVal', 'appDetailLatencySub', 'appDetailLatencySpark',
        latency !== null ? Math.round(latency) + ' ms' : '—', latencySub,
        appDetailSparkPoints(_cockpitPoints.latency),
        latency === null && !latencySub,
        state ? (state.health === 'healthy' ? 'up' : (state.health === 'stopped' ? null : 'down')) : null);
}

/** Note uptime à droite de la rangée de badges (maquette v6). */
function updateAppDetailUptimeNote(uptimeSeconds) {
    const note = document.getElementById('appDetailUptimeNote');
    if (!note) return;
    note.textContent = uptimeSeconds === null || uptimeSeconds === undefined
        ? ''
        : 'Uptime conteneur : ' + formatAppDetailUptime(uptimeSeconds)
            + ' · rafraîchi 1×/10 s tant que la fiche est ouverte';
}

// ===== Rangée de badges d'état =====

/**
 * Rangée allégée v6 : Docker, Health (si sonde dispo), version, utilisateurs
 * (+ note uptime à droite, remplie par le cockpit). Conteneur
 * #appDetailStatusBadges — ré-appelé par les SSE via renderAppRuntimeState
 * (admin-apps.js) et à chaque tick de latence.
 */
function updateAppDetailStatusBadges() {
    const container = document.getElementById('appDetailStatusBadges');
    if (!container || _appDetailSlug === null || _appDetailSlug === '__new__') return;
    const state = (typeof runtimeStates !== 'undefined') ? runtimeStates[_appDetailSlug] : null;
    const healthHtml = appDetailHealthBadgeHtml(state);
    container.innerHTML = ''
        + appDetailDockerBadgeHtml(state)
        + healthHtml
        + '<span id="appDetailVersionBadge"><span class="badge text-bg-light border">version…</span></span>'
        + appDetailUsersBadgeHtml(_appDetailApp ? _appDetailApp.users : null);

    // Sparkline latence : chaque relevé de sonde alimente le buffer (la
    // sonde /apps/status et les SSE passent tous deux par ici).
    if (state && state.health === 'healthy' && Number.isFinite(Number(state.latency_ms))) {
        appDetailPushPoint(_cockpitPoints.latency, Number(state.latency_ms));
    }
    renderAppDetailMiniCockpit();
}

/**
 * Badge Docker/Health absent des runtimeStates (fiche ouverte avant le test
 * initial) : un appel refreshAppsStatus() une seule fois par ouverture de
 * fiche, puis re-render des badges. Jamais de polling ici.
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
 * déploiements, changelog, logs, docker, mini-cockpit). Chaque consommateur
 * re-vérifie _appDetailSlug : changer de fiche/outil en cours de route ne
 * rend rien.
 */
function loadAppDetailSections() {
    const slug = _appDetailSlug;
    const app = _appDetailApp;
    if (!slug || slug === '__new__' || !app) return;

    ensureAppDetailStatusFetched(slug);
    updateAppDetailStatusBadges();
    refreshAppDetailDeployments();
    loadAppDetailChangelog(slug);
    loadAppDetailLogs();
    startAppDetailCockpitPoll(slug);

    // Docker : état de chargement explicite puis fetch (le body n'est rendu
    // qu'à l'ouverture — les ticks cockpit rafraîchissent ensuite le résumé
    // replié + les chips sans re-rendre le corps).
    const dockerBody = document.getElementById('appDetailDockerBody');
    if (dockerBody) dockerBody.innerHTML = '<span class="text-muted">Chargement…</span>';
    const dockerSummary = document.getElementById('appDetailDockerSummary');
    if (dockerSummary) dockerSummary.textContent = 'chargement…';
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

/** Charge (ou recharge via le bouton Actualiser) le statut de déploiement. */
async function refreshAppDetailDeployments() {
    const slug = _appDetailSlug;
    if (!slug || slug === '__new__') return;
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
        parts.push('<div class="text-muted mb-1">Historique :</div><div class="appdetail-scroll">' + items + '</div>');
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

/** Changelog (markdown minimal) + 3 dernières releases (tag + date). */
async function loadAppDetailChangelog(slug) {
    // Lien « CHANGELOG complet ↗ » dans l'en-tête de carte (squelette statique).
    const linkBox = document.getElementById('appDetailChangelogLink');
    if (linkBox) {
        const repoUrl = appDetailGithubUrl(_appDetailApp ? _appDetailApp.git_repo_url : null);
        linkBox.innerHTML = repoUrl
            ? '<a class="small" href="' + escapeAttr(repoUrl + '/blob/HEAD/CHANGELOG.md') + '" target="_blank" rel="noopener"'
                + ' title="CHANGELOG.md sur GitHub">CHANGELOG complet ↗</a>'
            : '';
    }

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

/**
 * Charge les logs (service/tail lus dans les <select> statiques). Réponses
 * ordonnées par séquence : changer service/tail en rafale n'affiche que la
 * dernière demande. Erreur → ligne muted inline (textContent, jamais de
 * popup). AUCUN polling.
 */
async function loadAppDetailLogs() {
    const slug = _appDetailSlug;
    const pre = document.getElementById('appDetailLogsPre');
    if (!slug || slug === '__new__' || !pre) return;
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
 * Résumé replié de l'en-tête Docker : « CPU 2,1 % · RAM 86 Mo · uptime 3 j ».
 * Rempli au fetch d'ouverture ET à chaque tick du mini-cockpit (même source
 * docker-info). stats null → « conteneur non trouvé ».
 */
function updateAppDetailDockerSummary(stats) {
    const summary = document.getElementById('appDetailDockerSummary');
    if (!summary) return;
    if (!stats) {
        summary.textContent = 'conteneur non trouvé';
        return;
    }
    summary.textContent = 'CPU ' + formatAppDetailFrNumber(stats.cpu_percent, 1)
        + ' % · RAM ' + formatAppDetailFrNumber(stats.memory_usage_mb, 1)
        + ' Mo · uptime ' + formatAppDetailUptime(stats.uptime_seconds);
}

/**
 * Corps du collapse Docker (déplié via data-bs-toggle) : résumé conteneur
 * (nom, image, ports) + bouton « Détails complets » (modale existante
 * showDockerDetails, admin-app-form.js — aucune duplication). Un seul fetch
 * à l'ouverture ; les ticks cockpit ne re-rendent PAS ce corps.
 */
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
    if (containerFound) updateAppDetailDockerSummary(stats);
    else if (summary) summary.textContent = (info && info.docker_available === false) ? 'scan désactivé' : 'conteneur non trouvé';

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
 * techniques, PAS de mini-cockpit, PAS d'Ouvrir. Rendu dans
 * #appDetailReducedPane (le squelette reste en place, masqué).
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
