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
        + '<span class="hub-status-badge ok ms-auto">via hubctl</span>'
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

// Sortie propre de la vue courante (stop des pollings hérités des onglets).
// NB : le polling VPN n'est volontairement pas stoppé ici — parité avec
// l'ancien comportement (démarré par init/toggle, jamais par l'ouverture).
function leaveAdminHubView() {
    if (typeof stopConnectivityRefresh === 'function') stopConnectivityRefresh();
    if (typeof stopRoutingPolling === 'function') stopRoutingPolling();
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
            if (typeof refreshAppsStatus === 'function') refreshAppsStatus();
            if (typeof startConnectivityRefresh === 'function') startConnectivityRefresh();
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

function openHubTool(key) {
    const panelId = ADMIN_HUB_PANELS[key];
    if (!panelId) {
        showAdminHub();
        return;
    }
    leaveAdminHubView();
    renderAdminHub(); // no-op si déjà rendu
    showAdminPane(panelId.replace('#', ''));
    _adminHubCurrent = key;
    try { window.location.hash = 'admin/' + key; } catch (e) { /* hash refusé */ }
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
}

// ===== Routing par hash =====

/** Clé d'outil portée par le hash courant, ou null (#admin ou hash étranger). */
function adminHubKeyFromHash() {
    const match = /^#admin\/([a-z-]+)$/.exec(window.location.hash || '');
    if (!match) return null;
    return ADMIN_HUB_PANELS[match[1]] ? match[1] : null;
}

/**
 * Route la vue admin selon le hash courant. Appelé par showPage('admin')
 * (auth-navigation.js) à chaque ouverture de la page : #admin → hub,
 * #admin/<clé> → outil ( compatibilité system-update.js + liens directs).
 */
function routeAdminFromHash() {
    const key = adminHubKeyFromHash();
    if (key) openHubTool(key);
    else showAdminHub();
}

function onAdminHashChange() {
    const hash = window.location.hash || '';
    if (!/^#admin(\/|$)/.test(hash)) return; // hash hors page admin : ignorer
    // Page admin masquée (navigation ailleurs) : l'ouverture se fera via
    // showPage('admin') → routeAdminFromHash.
    if (document.getElementById('adminSection')?.classList.contains('d-none')) return;
    const key = adminHubKeyFromHash();
    if (key) {
        if (_adminHubCurrent !== key) openHubTool(key);
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
