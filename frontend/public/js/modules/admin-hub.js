// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - page Administration : hub « cockpit »
 *
 * Remplace la navigation à onglets de la page Administration par un hub à
 * deux colonnes : cockpit système à gauche (rempli en passe 2), menu par
 * thèmes à droite (cartes de lignes cliquables). Les outils deviennent des
 * vues plein-largeur avec bouton retour ; la vue courante est encodée dans le
 * hash (#admin → hub, #admin/<clé> → outil), ce qui rend le retour
 * post-rechargement après mise à jour et les liens directs fonctionnels.
 *
 * Aucune animation ni transition — charte perf du parc.
 */

// Vue courante : 'hub' ou clé d'outil. Sert de garde anti-rebouclage au
// hashchange (le hash est posé programmatiquement par openHubTool/showAdminHub).
let _adminHubCurrent = null;
// Garde du routage initial différé (#admin après rechargement post-update).
let _adminHubDeferredStarted = false;

// ===== Cartes du menu par thèmes (droite) =====

const ADMIN_HUB_THEMES = [
    {
        icon: 'layers',
        title: "Parc d'applications",
        tools: [
            { key: 'apps', icon: 'app-window', name: 'Applications', desc: 'Parc, scanner Docker, approbation, édition' },
            { key: 'groups', icon: 'users', name: 'Groupes', desc: 'Regroupement visuel du dashboard' },
            { key: 'dependencies', icon: 'share-2', name: 'Dépendances', desc: 'Graphe et matrice des depends.yaml' },
            { key: 'github', icon: 'code', name: 'Apps GitHub', desc: 'Sync des dépôts, déploiement en 1 clic' },
            { key: 'ateliers', icon: 'wrench', name: 'Ateliers', desc: 'Environnements dev, fermeture des ateliers' },
        ],
    },
    {
        icon: 'lock',
        title: 'Accès &amp; sécurité',
        tools: [
            { key: 'vpn', icon: 'wifi', name: 'VPN', desc: 'WireGuard, peers, trafic' },
            { key: 'oidc', icon: 'fingerprint', name: 'Clients OIDC', desc: 'SSO des apps tierces (Vikunja, Nextcloud…)' },
            { key: 'service-accounts', icon: 'key', name: 'Service Accounts', desc: 'Comptes M2M inter-apps' },
            { key: 'routing', icon: 'signpost', name: 'Accès &amp; routage', desc: 'Zone DNS, FQDN, publication de services' },
        ],
    },
    {
        icon: 'archive',
        title: 'Données',
        tools: [
            { key: 'backups', icon: 'database', name: 'Sauvegardes', desc: 'Apps, snapshots, coffre hubctl, environnement' },
        ],
    },
    {
        icon: 'refresh-cw',
        title: 'Système',
        tools: [
            { key: 'updates', icon: 'rocket', name: 'Mises à jour', desc: 'Socle et composants du parc' },
        ],
    },
];

// Mapping clé d'outil → panneau DOM (les panneaux vivent dans index.html).
const ADMIN_HUB_PANELS = {
    apps: '#tabApps',
    groups: '#tabAppGroups',
    dependencies: '#tabDependencies',
    github: '#tabGithubApps',
    ateliers: '#tabAteliers',
    vpn: '#vpnPanel',
    oidc: '#tabOidcClients',
    'service-accounts': '#tabServiceAccounts',
    routing: '#tabRouting',
    backups: '#tabAppBackups',
    updates: '#tabSystemUpdates',
};

// Valeurs currentPage par outil (mêmes valeurs que l'ancien shown.bs.tab —
// sse.js et d'autres modules s'en servent pour router leurs refresh).
const ADMIN_HUB_PAGES = {
    apps: 'apps-admin',
    dependencies: 'app-dependencies',
    'service-accounts': 'service-accounts',
    backups: 'app-backups',
    oidc: 'oidc-clients',
};

function hubIcon(name, extraClass) {
    return '<svg class="hub-icon' + (extraClass ? ' ' + extraClass : '') + '" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        + '<use href="/vendor/design-system/lucide.svg#icon-' + name + '"></use></svg>';
}

function renderAdminHub() {
    const pane = document.getElementById('adminHubPane');
    if (!pane || pane.childElementCount > 0) return;

    const themeCards = ADMIN_HUB_THEMES.map(theme => {
        const rows = theme.tools.map(tool => (
            '<button type="button" class="hub-row" data-hub-tool="' + tool.key + '"'
            + ' data-action="openHubTool" data-args="' + tool.key + '">'
            + hubIcon(tool.icon, 'hub-row-icon')
            + '<div class="flex-grow-1">'
            + '<div class="hub-name">' + tool.name + '</div>'
            + '<div class="hub-desc">' + tool.desc + '</div>'
            + '</div>'
            + '<span class="hub-status-badge" data-hub-status="' + tool.key + '">—</span>'
            + hubIcon('chevron-right', 'hub-chevron')
            + '</button>'
        )).join('');

        return '<div class="card border-0 shadow-sm">'
            + '<div class="hub-card-head">' + hubIcon(theme.icon) + theme.title + '</div>'
            + rows
            + '</div>';
    }).join('');

    // Colonne gauche : cockpit système — conteneur vide, rempli en passe 2.
    // #pendingAppsBadge reste la cible de updatePendingAppsBadge (SSE/login) :
    // il est masqué, le badge visible de la ligne Applications est alimenté
    // par refreshHubStatuses().
    pane.innerHTML = '<div class="admin-hub-layout">'
        + '<div class="card border-0 shadow-sm admin-hub-cockpit">'
        + '<div class="hub-card-head" style="padding:12px 14px">'
        + hubIcon('server') + 'Cockpit système'
        // Badge d'état piloté par cockpit.js (setCockpitStatusBadge) :
        // « via hubctl » / « dégradé » / « indisponible » / « erreur réseau ».
        + '<span id="cockpitStatusBadge" class="hub-status-badge ok ms-auto">via hubctl</span>'
        + '</div>'
        + '<div id="cockpitBody" class="card-body">'
        + '<div class="text-muted small text-center py-4">Chargement des métriques…</div>'
        + '</div>'
        + '</div>'
        + '<div class="admin-hub-menu">' + themeCards + '</div>'
        + '</div>';

    // Badge « apps en attente » de compatibilité (invisible) : l'ancien badge
    // de la barre d'onglets est référencé par updatePendingAppsBadge() —
    // display:none en dur car cette fonction ne manipule que la classe d-none.
    const appsBadge = pane.querySelector('[data-hub-status="apps"]');
    if (appsBadge) {
        const compat = document.createElement('span');
        compat.id = 'pendingAppsBadge';
        compat.className = 'd-none';
        compat.style.display = 'none';
        appsBadge.insertAdjacentElement('afterend', compat);

        // Clic sur le badge visible : vue Applications filtrée sur les apps en
        // attente (l'ancien badge d'onglet était son seul déclencheur).
        appsBadge.setAttribute('data-action', 'hubFocusPendingApps');
        appsBadge.setAttribute('role', 'button');
        appsBadge.setAttribute('title', "Voir les applications en attente d'approbation");
        appsBadge.style.cursor = 'pointer';
    }
}

// ===== Visibilité des panneaux =====

function showAdminPane(panelId) {
    document.querySelectorAll('#adminSection .admin-tool-pane').forEach(p => {
        p.classList.toggle('d-none', p.id !== panelId);
    });
    const target = document.getElementById(panelId);
    if (target) target.classList.remove('d-none');
}

// Sortie propre de la vue courante (stop des pollings hérités des onglets,
// + polling cockpit démarré par showAdminHub). NB : le polling VPN n'est
// volontairement pas stoppé ici — parité avec l'ancien comportement
// (démarré par init/toggle, jamais par l'ouverture).
function leaveAdminHubView() {
    if (typeof stopConnectivityRefresh === 'function') stopConnectivityRefresh();
    if (typeof stopRoutingPolling === 'function') stopRoutingPolling();
    if (typeof stopCockpitPolling === 'function') stopCockpitPolling();
    // Fiche application ouverte (vue apps) : fermée à la sortie de l'outil
    // (poll « déploiement en cours » compris) — admin-app-detail.js.
    if (typeof leaveAppDetail === 'function') leaveAppDetail();
}

// ===== Ouverture d'un outil (vue plein-largeur) =====

/**
 * Charge les données de l'outil à l'ouverture — reprend exactement les
 * déclencheurs shown.bs.tab des anciens onglets (init.js, admin-vpn.js,
 * admin-atelier.js, admin-routing.js, system-update.js) plus les
 * data-action des anciens boutons (loadGithubApps, switchBackupWorkspace).
 */
function loadAdminHubTool(key) {
    switch (key) {
        case 'apps':
            // Test de connectivité initial à l'ouverture ; ensuite SSE +
            // bouton manuel uniquement (plus de polling 30 s).
            if (typeof refreshAppsStatus === 'function') refreshAppsStatus();
            break;
        case 'dependencies':
            if (typeof loadDependenciesGraph === 'function') loadDependenciesGraph();
            break;
        case 'service-accounts':
            if (typeof refreshServiceAccountsStatus === 'function') refreshServiceAccountsStatus();
            break;
        case 'backups':
            // L'ancien bouton d'onglet portait data-action="switchBackupWorkspace"
            // data-args="apps" : on restaure le workspace par défaut.
            if (typeof switchBackupWorkspace === 'function') switchBackupWorkspace('apps');
            break;
        case 'oidc':
            if (typeof loadOidcClients === 'function') loadOidcClients();
            break;
        case 'github':
            if (typeof loadGithubApps === 'function') loadGithubApps();
            break;
        case 'vpn':
            // Parité avec l'ancien listener shown.bs.tab de admin-vpn.js
            (async () => {
                if (typeof initVpnFilters === 'function') initVpnFilters();
                if (typeof loadVpnServerConfig === 'function') await loadVpnServerConfig();
                if (typeof loadVpnStatus === 'function') await loadVpnStatus();
                if (typeof loadVpnPeers === 'function') await loadVpnPeers();
                if (typeof loadVpnUsers === 'function') await loadVpnUsers();
            })();
            break;
        case 'ateliers':
            if (typeof refreshAteliers === 'function') refreshAteliers();
            break;
        case 'routing':
            if (typeof loadRoutingAdmin === 'function') loadRoutingAdmin();
            if (typeof startRoutingPolling === 'function') startRoutingPolling();
            break;
        case 'updates':
            if (typeof loadSystemUpdates === 'function') loadSystemUpdates(true);
            break;
        case 'groups':
            // Parité : l'ancien onglet Groupes ne rechargeait rien
            // (loadAdminData → loadAppGroups déjà fait à l'ouverture admin).
            break;
    }
}

function openHubTool(key, options) {
    const panelId = ADMIN_HUB_PANELS[key];
    if (!panelId) {
        showAdminHub();
        return;
    }
    leaveAdminHubView();
    renderAdminHub(); // no-op si déjà rendu
    showAdminPane(panelId.replace('#', ''));
    _adminHubCurrent = key;
    // options.keepHash : routage initial depuis #admin/apps/<slug> (routeAdminFromHash)
    // — le hash final est posé par openAppDetail ; écrire 'admin/apps' ici
    // puis le 3ᵉ segment empilerait deux entrées d'historique navigateur.
    if (!(options && options.keepHash)) {
        try { window.location.hash = 'admin/' + key; } catch (e) { /* hash refusé */ }
    }
    const page = ADMIN_HUB_PAGES[key];
    if (page) currentPage = page;
    loadAdminHubTool(key);
}

/**
 * Badge « apps en attente » de la ligne Applications : ouvre la vue
 * Applications avec le filtre pending (reprend focusPendingApps, dont le
 * seul déclencheur était le badge de l'ancienne barre d'onglets).
 */
function hubFocusPendingApps() {
    openHubTool('apps');
    if (typeof focusPendingApps === 'function') focusPendingApps();
}

// ===== Retour au hub =====

function showAdminHub() {
    leaveAdminHubView();
    renderAdminHub();
    showAdminPane('adminHubPane');
    _adminHubCurrent = 'hub';
    try { window.location.hash = 'admin'; } catch (e) { /* hash refusé */ }
    currentPage = 'apps-admin';
    refreshHubStatuses();
    // Polling cockpit 10 s : uniquement quand le hub est affiché (stop à
    // l'ouverture d'une vue outil via leaveAdminHubView).
    if (typeof startCockpitPolling === 'function') startCockpitPolling();
}

// ===== Statuts des lignes du hub =====
//
// Un seul fetch par ligne et uniquement quand le hub est affiché (appel par
// showAdminHub, pas de polling). Réutilise exclusivement des endpoints déjà
// consommés par les modules des vues correspondantes — aucun fetch nouveau.
// Toute erreur ⇒ badge « — » (ton neutre), jamais de popup.
// Les lignes sans sonde en passe 1 (groupes, dépendances, GitHub, ateliers,
// OIDC, service accounts, routage) gardent leur badge statique « — ».

let _hubStatusesInFlight = false;

// Cache partagé du statut « mises à jour du socle » : consommé par le
// badge de la ligne Mises à jour ET par l'alerte du cockpit (cockpit.js) —
// un seul fetch par affichage du hub, jamais d'appel dupliqué. Invalidé à
// chaque refreshHubStatuses (chaque affichage du hub) pour garder la
// fraîcheur du badge ; le cockpit relit le cache entre-temps.

let _hubUpdatesStatusData = null;
let _hubUpdatesStatusPromise = null;

function setHubStatus(key, tone, text) {
    const badge = document.querySelector('#adminHubPane [data-hub-status="' + key + '"]');
    if (!badge) return;
    badge.textContent = text;
    badge.classList.remove('ok', 'warn');
    if (tone) badge.classList.add(tone);
}

function refreshHubStatuses() {
    if (_hubStatusesInFlight) return;
    const pane = document.getElementById('adminHubPane');
    if (!pane || pane.classList.contains('d-none')) return; // hub non affiché
    _hubStatusesInFlight = true;
    _hubUpdatesStatusData = null; // invalidation : 1 fetch frais par affichage
    Promise.allSettled([
        refreshHubAppsStatus(),
        refreshHubUpdatesStatus(),
        refreshHubBackupsStatus(),
        refreshHubVpnStatus(),
    ]).finally(() => { _hubStatusesInFlight = false; });
}

/**
 * Applications : nb d'apps actives + nb en attente. Endpoints existants :
 * api.getPendingApps() (badge #pendingAppsBadge, admin-app-approval.js) et
 * le cache allApps rempli par loadAdminData() (api.getAllApps en repli).
 */
async function refreshHubAppsStatus() {
    try {
        const pendingData = await api.getPendingApps();
        const pending = (pendingData && typeof pendingData.total === 'number') ? pendingData.total : 0;
        let list = (typeof allApps !== 'undefined' && Array.isArray(allApps) && allApps.length > 0) ? allApps : null;
        if (!list) list = await api.getAllApps('all');
        const active = (Array.isArray(list) ? list : []).filter(a =>
            a && a.is_active && a.approval_status !== 'pending' && a.approval_status !== 'rejected'
        ).length;
        setHubStatus('apps', 'ok', active + ' actives' + (pending > 0 ? ' · ' + pending + ' en attente' : ''));
    } catch (error) {
        setHubStatus('apps', null, '—');
    }
}

/**
 * Statut update-status avec cache + mémo de la promesse en vol (le tick
 * cockpit et refreshHubStatuses peuvent converger au même moment sans
 * doubler l'appel). Ne lève jamais : panne ⇒ null, les consommateurs
 * (badge, alerte cockpit) dégradent en « état inconnu ».
 */
function fetchHubUpdatesStatus() {
    if (_hubUpdatesStatusData) return Promise.resolve(_hubUpdatesStatusData);
    if (!_hubUpdatesStatusPromise) {
        _hubUpdatesStatusPromise = api.getSystemUpdateStatus()
            .then(data => { _hubUpdatesStatusData = data; return data; })
            .catch(() => null)
            .finally(() => { _hubUpdatesStatusPromise = null; });
    }
    return _hubUpdatesStatusPromise;
}

/**
 * Nombre de mises à jour disponibles (socle + composants), null si l'état
 * est inexploitable (service down/dégradé ou panne réseau).
 */
function countHubUpdatesAvailable(data) {
    if (!data || data.service !== 'ok') return null;
    let count = 0;
    if (data.meta && data.meta.update_available) count += 1;
    const components = data.components || {};
    Object.keys(components).forEach(relPath => {
        if (components[relPath] && components[relPath].update_available) count += 1;
    });
    return count;
}

/**
 * Mises à jour : composants concernés. Endpoint existant :
 * api.getSystemUpdateStatus() (system-update.js, badge navbar) via le
 * cache partagé fetchHubUpdatesStatus() — SANS ?refresh=1 pour éviter un
 * re-check git lourd à chaque affichage du hub.
 */
async function refreshHubUpdatesStatus() {
    const data = await fetchHubUpdatesStatus();
    const count = countHubUpdatesAvailable(data);
    if (count === null) { setHubStatus('updates', null, '—'); return; }
    if (count > 0) setHubStatus('updates', 'warn', count + ' dispo');
    else setHubStatus('updates', null, 'à jour');
}

/**
 * Sauvegardes : dernier backup toutes apps. Endpoint existant :
 * /admin/backups (loadAppBackupsOverview, app-backups.js) — champ
 * apps[].last_backup_at / last_backup_status (déjà consommé par sa colonne
 * « dernier backup »).
 */
async function refreshHubBackupsStatus() {
    try {
        const data = await api.request('/admin/backups');
        const apps = Array.isArray(data && data.apps) ? data.apps : [];
        let latest = null;
        apps.forEach(app => {
            if (app && app.last_backup_at && (!latest || app.last_backup_at > latest.last_backup_at)) latest = app;
        });
        if (!latest) { setHubStatus('backups', null, 'aucun'); return; }
        if (typeof parseUTCDate !== 'function') { setHubStatus('backups', 'ok', 'Dernier : récent'); return; }
        const date = parseUTCDate(latest.last_backup_at);
        const label = 'Dernier : ' + date.toLocaleDateString('fr-FR')
            + ' ' + date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
        if (latest.last_backup_status === 'success') setHubStatus('backups', 'ok', label);
        else if (latest.last_backup_status === 'failed') setHubStatus('backups', 'warn', label);
        else setHubStatus('backups', null, label);
    } catch (error) {
        setHubStatus('backups', null, '—');
    }
}

/**
 * VPN : état + peers connectés. Endpoint existant : /vpn/status
 * (loadVpnStatus, admin-vpn.js) — champs enabled/initialized/connected_count.
 */
async function refreshHubVpnStatus() {
    try {
        const data = await api.request('/vpn/status');
        if (!data || !data.enabled) { setHubStatus('vpn', null, 'désactivé'); return; }
        if (!data.initialized) { setHubStatus('vpn', 'warn', 'en attente'); return; }
        setHubStatus('vpn', 'ok', (data.connected_count || 0) + ' connectés');
    } catch (error) {
        setHubStatus('vpn', null, '—');
    }
}

// ===== Routing par hash =====

/** Clé d'outil portée par le hash courant, ou null (#admin ou hash étranger). */
function adminHubKeyFromHash() {
    const match = /^#admin\/([a-z-]+)$/.exec(window.location.hash || '');
    if (!match) return null;
    return ADMIN_HUB_PANELS[match[1]] ? match[1] : null;
}

/**
 * Route complète du hash : { key, slug? } ou null. Le 3ᵉ segment est
 * réservé à la fiche application (#admin/apps/<slug> — admin-app-detail.js) ;
 * les autres outils restent à 2 segments (adminHubKeyFromHash, regex stricte
 * → #admin/<outils>/<x> est inerté comme avant).
 */
function adminHubRouteFromHash() {
    const hash = window.location.hash || '';
    const deep = /^#admin\/(apps)\/([a-z0-9-]+)$/.exec(hash);
    if (deep && ADMIN_HUB_PANELS[deep[1]]) {
        return { key: 'apps', slug: deep[2] };
    }
    const key = adminHubKeyFromHash();
    return key ? { key } : null;
}

/**
 * Route la vue admin selon le hash courant. Appelé par showPage('admin')
 * (auth-navigation.js) à chaque ouverture de la page : #admin → hub,
 * #admin/<clé> → outil, #admin/apps/<slug> → outil + fiche (liens directs,
 * retour post-rechargement).
 */
function routeAdminFromHash() {
    const route = adminHubRouteFromHash();
    if (!route) {
        showAdminHub();
        return;
    }
    openHubTool(route.key, route.slug ? { keepHash: true } : undefined);
    if (route.slug && typeof openAppDetail === 'function') {
        // La fiche re-positionne le hash #admin/apps/<slug> (no-op si déjà posé).
        openAppDetail(route.slug);
    }
}

function onAdminHashChange() {
    const hash = window.location.hash || '';
    if (!/^#admin(\/|$)/.test(hash)) return; // hash hors page admin : ignorer
    // Page admin masquée (navigation ailleurs) : l'ouverture se fera via
    // showPage('admin') → routeAdminFromHash.
    if (document.getElementById('adminSection')?.classList.contains('d-none')) return;
    const route = adminHubRouteFromHash();
    if (route) {
        if (_adminHubCurrent !== route.key) openHubTool(route.key);
        if (route.key === 'apps') {
            if (route.slug && typeof openAppDetail === 'function') {
                // Ouvre/reprend la fiche (no-op si déjà affichée sur ce slug) ;
                // couvre back/forward et le retour post-rechargement.
                openAppDetail(route.slug);
            } else if (typeof hideAppDetailIfOpen === 'function') {
                // #admin/apps sans slug : liste (back depuis la fiche).
                hideAppDetailIfOpen();
            }
        }
    } else if (hash === '#admin' && _adminHubCurrent !== 'hub') {
        showAdminHub();
    }
}

/**
 * Routage initial différé : au chargement avec #admin (retour post-mise à
 * jour, armSystemUpdateReload) ou #admin/<clé>, showPage('admin') exige
 * currentUser — on attend la restauration de session (loadCurrentUser →
 * showDashboard) au lieu d'un appel immédiat qui partait en « Accès refusé »
 * (toast parasite au retour post-update). Idempotent.
 */
function routeAdminFromHashDeferred() {
    if (_adminHubDeferredStarted) return;
    _adminHubDeferredStarted = true;
    if (!/^#admin(\/|$)/.test(window.location.hash || '')) return;
    let tries = 0;
    const timer = setInterval(() => {
        tries += 1;
        const ready = typeof currentUser !== 'undefined' && currentUser;
        const stale = !/^#admin(\/|$)/.test(window.location.hash || '');
        if (ready || stale || tries >= 50) { // 50 × 200 ms = 10 s max
            clearInterval(timer);
            if (ready && !stale && currentUser?.is_admin && typeof showPage === 'function') {
                showPage('admin');
            }
        }
    }, 200);
}

document.addEventListener('DOMContentLoaded', () => {
    window.addEventListener('hashchange', onAdminHashChange);
    routeAdminFromHashDeferred();
});
