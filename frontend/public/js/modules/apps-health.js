// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Apps Health Dashboard
 *
 * Remplace l'ancien module discovery.js.
 * Fonctions globales pour compatibilite avec les handlers HTML inline.
 */

// Garde cote client : ne pas refresh plus d'une fois / 30s
var _lastGithubSync = 0;

async function refreshGithubCache() {
    var now = Date.now();
    if (now - _lastGithubSync < 30000) return;
    _lastGithubSync = now;
    try {
        var r = await api.refreshGithubRepos();
        if (!r.token_configured) {
            // silencieux : l'admin est informe via la vue Apps GitHub
        } else if (r.error) {
            console.warn('GitHub cache sync:', r.error);
        }
    } catch (e) {
        // silencieux, non-bloquant
    }
}

// ─── Chargement principal ─────────────────────────────────────────────

var _healthRenderSeq = 0;      // anti-course : le dernier loadAppsHealth gagne

async function loadAppsHealth() {
    const btn = document.getElementById('appsHealthRefreshBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Sondage…';
    const seq = ++_healthRenderSeq;

    // 1) Rendu IMMÉDIAT : liste des apps (lecture DB, rapide) en état
    //    « vérification… » — plus d'écran vide pendant le sondage du parc.
    try {
        const listing = await api.getAllApps('false');
        const raw = Array.isArray(listing) ? listing : (listing.apps || []);
        const apps = raw.map(function (a) {
            return { name: a.name, slug: a.slug, icon: a.icon, color: a.color,
                     is_system_app: a.is_system_app, version: a.version || a.app_version || '' };
        });
        // La liste rapide exclut les apps système — on rend la carte HUB aussi.
        if (!apps.some(function (a) { return a.slug === 'hub'; })) {
            apps.unshift({ name: 'Hub', slug: 'hub', icon: 'bi-grid-3x3-gap-fill',
                           color: '#495057', is_system_app: true, version: '' });
        }
        if (seq === _healthRenderSeq) renderAppsHealthPending(apps);
    } catch (e) {
        // Non bloquant : le sondage complet rendra les cartes de toute façon.
    }

    // 2) Sondage santé complet (long — timeouts des apps offline) → re-render.
    try {
        const result = await api.getAppsHealth();
        if (seq === _healthRenderSeq) await renderAppsHealth(result);
    } catch (error) {
        console.error('Erreur chargement health:', error);
        if (seq === _healthRenderSeq) {
            showToast('Erreur', 'Impossible de sonder les applications', 'danger');
        }
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Rafraichir';
    }
}

// ─── Rendu intermédiaire (liste rapide, avant le sondage) ─────────────

function renderAppsHealthPending(apps) {
    const sumEl = document.getElementById('appsHealthSummary');
    sumEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1" style="width:11px;height:11px;"></span> Vérification de '
        + apps.length + ' apps en cours…';

    const grid = document.getElementById('appsHealthGrid');
    grid.innerHTML = apps.map(function (a) {
        var item = {
            name: a.name, slug: a.slug, icon: a.icon, color: a.color,
            is_system_app: a.is_system_app, status: 'checking',
        };
        _healthBySlug[a.slug] = item;
        return renderAppCard(item);
    }).join('');
    _applyHealthFilters();
}

// Cache des dernières données santé par slug — alimente les mises à jour
// ciblées SSE (updateAppsHealthCard) sans re-sonder l'API.
var _healthBySlug = {};

async function renderAppsHealth(data) {
    const summary = data.summary;
    const sumEl = document.getElementById('appsHealthSummary');
    sumEl.innerHTML =
        '<span style="color:#16a34a;">●</span> ' + summary.online + ' en ligne · ' +
        '<span style="color:#dc2626;">●</span> ' + summary.offline + ' hors ligne' +
        (summary.degraded > 0 ? ' · <span style="color:#f59e0b;">●</span> ' + summary.degraded + ' degrade' : '') +
        (summary.stopped > 0 ? ' · <span style="color:#94a3b8;">●</span> ' + summary.stopped + ' stoppée(s)' : '') +
        ' · ' + summary.total + ' apps' +
        '<span style="float:right;display:inline-block;font-size:9.5px;background:#eef2ff;color:#4338ca;'
        + 'border-radius:5px;padding:2px 6px;font-weight:600;">Live SSE · maj au changement</span>';

    // Alimente le cache par slug AVANT le rendu (les cartes ciblées SSE s'y réfèrent)
    _healthBySlug = {};
    (data.apps || []).forEach(function (a) { _healthBySlug[a.slug] = a; });

    const grid = document.getElementById('appsHealthGrid');
    grid.innerHTML = data.apps.map(app => renderAppCard(app)).join('');

    // Lance la verification des mises a jour en arriere-plan
    if (data.apps && data.apps.length) {
        await refreshGithubCache();
        checkAllUpdates(data.apps);
    }

    // États atelier (contrat .workshop.env) — chargés après le rendu, non bloquant
    var wsSlugs = (data.apps || []).filter(function (a) { return !(a.is_system_app === true || a.slug === 'hub'); })
        .map(function (a) { return a.slug; });
    if (wsSlugs.length && typeof loadWorkshopStates === 'function') {
        loadWorkshopStates(wsSlugs);
    }
}

function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return '';
    var s = Math.floor(seconds);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'min';
    if (s < 86400) {
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        return m > 0 ? h + 'h ' + m + 'min' : h + 'h';
    }
    return Math.floor(s / 86400) + 'j';
}

// ─── Styles de la carte compacte (injectés une seule fois) ─────────────
// Le fichier reste en style inline ; seules les règles impossibles en
// inline (états hover/disabled, menu display, spinner keyframes) vivent ici.
// Zéro transition/animation hormis la rotation du spinner fonctionnel
// (indicateur d'action en cours, pas une déco — cf. PERFORMANCE).
function _injectHealthCardStyles() {
    if (document.getElementById('ahc2-styles')) return;
    var st = document.createElement('style');
    st.id = 'ahc2-styles';
    st.textContent =
        // Menu ⋯ : instantané (display none/block, aucune transition)
        '.ahc2-menu{position:absolute;top:30px;right:7px;z-index:60;min-width:158px;background:#fff;'
        + 'border:1px solid #e2e8f0;border-radius:9px;box-shadow:0 8px 24px rgba(15,23,42,.14);padding:4px;display:none;}'
        + '.ahc2-menu.open{display:block;}'
        + '.ahc2-menu button{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:none;border:0;'
        + 'border-radius:6px;padding:6px 8px;font-size:12px;color:#1e293b;cursor:pointer;}'
        + '.ahc2-menu button:hover{background:#f1f5f9;}'
        + '.ahc2-menu button.danger{color:#dc2626;}'
        + '.ahc2-menu button:disabled{color:#cbd5e1;cursor:not-allowed;}'
        + '.ahc2-menu button:disabled:hover{background:none;}'
        + '.ahc2-menu .sep{height:1px;background:#eef2f6;margin:4px 2px;}'
        + '.ahc2-menu .mi{width:14px;text-align:center;font-size:11px;flex:none;}'
        // Bouton ⋯ + état occupé
        + '.ahc2-more:hover{background:#f8fafc;color:#0f172a;}'
        + '.ahc2-more.loading{pointer-events:none;border-color:transparent;}'
        // Spinner fonctionnel (rotation uniquement)
        + '.ahc2-spinner{width:12px;height:12px;border:2px solid #cbd5e1;border-top-color:#4f46e5;border-radius:50%;'
        + 'animation:ahc-spin .9s linear infinite;}'
        + '@keyframes ahc-spin{to{transform:rotate(360deg);}}';
    document.head.appendChild(st);
}

// Ligne métrique compacte du corps de carte
function _cardLine(k, v, tone) {
    var color = tone === 'err' ? '#dc2626' : (tone === 'warn' ? '#d97706' : '#0f172a');
    return '<div style="display:flex;justify-content:space-between;gap:6px;font-size:10.5px;color:#475569;padding:1.5px 0;">'
        + '<span style="color:#94a3b8;flex:none;">' + escapeHtml(k) + '</span>'
        + '<span title="' + escapeHtml(v) + '" style="font-weight:600;color:' + color
        + ';font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        + escapeHtml(v) + '</span></div>';
}

// Carte : état du corps selon les données DISPONIBLES — plus aucun slot « — » fixe.
function _cardBodyLines(app) {
    var latency = app.latency_ms ? app.latency_ms + ' ms' : '';
    var uptime = formatUptime(app.uptime_seconds);
    var lines = [];
    if (app.status === 'checking') {
        lines.push(_cardLine('Sondage', 'en cours…'));
        return lines.join('');
    }
    // Un arrêt délibéré n'est pas une erreur : motif neutre gris (pas de rouge).
    if (app.status === 'stopped') {
        lines.push(_cardLine('État', 'Conteneurs arrêtés'));
        return lines.join('');
    }
    if (latency) lines.push(_cardLine('Latence', latency));
    if (uptime) lines.push(_cardLine('Uptime', uptime));
    if (app.error) {
        var short = String(app.error).split('\n')[0].substring(0, 60);
        lines.push(_cardLine('Erreur', short, 'err'));
    }
    // App joignable mais sans aucune donnée santé exploitable (pas d'endpoint
    // /health standard) — ne s'applique pas aux apps système (HUB auto-sondé).
    if (!lines.length) {
        lines.push(_cardLine('Santé', 'aucune donnée'));
    }
    return lines.join('');
}

// Carte : pastille dot colorée par statut avec halo (maquette .ahc2-dot)
function _cardDot(app) {
    var dot = {
        checking: { bg: '#cbd5e1', halo: '#f1f5f9', text: 'Vérification…' },
        online:   { bg: '#16a34a', halo: '#dcfce7', text: 'ONLINE' },
        offline:  { bg: '#dc2626', halo: '#fee2e2', text: 'OFFLINE' },
        degraded: { bg: '#d97706', halo: '#fef3c7', text: 'DEGRADED' },
        // Arrêt volontaire : gris neutre, jamais une couleur d'alerte.
        stopped:  { bg: '#94a3b8', halo: '#e2e8f0', text: 'STOPPÉE' },
    }[app.status] || { bg: '#cbd5e1', halo: '#f1f5f9', text: app.status || 'unknown' };
    var title = dot.text + (app.error ? ' — ' + String(app.error).split('\n')[0] : '');
    return '<span title="' + escapeHtml(title) + '" style="margin-left:auto;flex:none;width:10px;height:10px;border-radius:50%;'
        + 'background:' + dot.bg + ';box-shadow:0 0 0 3px ' + dot.halo + ';"></span>';
}

function renderAppCard(app) {
    _injectHealthCardStyles();
    var isSystemApp = app.is_system_app === true || app.slug === 'hub';

    // Carte compacte (maquette validée « après ») : 6 par ligne ≥1200px,
    // en-tête resserré + pastille dot, corps à données présentes seulement.
    // App volontairement arrêtée : tête en tons neutres gris — pas une erreur.
    var headGrad = {
        checking: 'linear-gradient(135deg,#f8fafc,#ffffff)',
        online:   'linear-gradient(135deg,#e9f9f0,#d8f3e5)',
        offline:  'linear-gradient(135deg,#fee9e7,#fde3e3)',
        degraded: 'linear-gradient(135deg,#fef4d6,#fdeebc)',
        stopped:  'linear-gradient(135deg,#f1f5f9,#e9eef3)',
    }[app.status] || 'linear-gradient(135deg,#f8fafc,#ffffff)';

    var icon = escapeHtml(app.icon || 'bi-app');
    var color = escapeHtml(app.color || '#6c757d');
    var name = escapeHtml(app.name);
    var slug = escapeHtml(app.slug);
    var version = app.version || '';

    var hubLabel = app.hub_connected === true ? 'Hub ✓'
        : (app.hub_connected === false ? 'Hub ✗' : 'Hub —');

    // Badge « Système » (chip indigo) — dans le footer, comme la maquette.
    var systemChip = isSystemApp
        ? '<span title="Application système (noyau HUB)" style="display:inline-block;font-size:8.5px;font-weight:700;'
          + 'background:#eef2ff;color:#4338ca;border-radius:5px;padding:2px 6px;">SYSTÈME</span><span style="color:#cbd5e1;">·</span>'
        : '';

    // Version courante (span masqué si un chip MàJ compact le remplace)
    var versionSpan = version
        ? '<span id="appver-' + slug + '" title="Version déployée">v' + escapeHtml(version) + '</span><span style="color:#cbd5e1;">·</span>'
        : '<span id="appver-' + slug + '"></span>';

    // Chip « MàJ » compact — conteneur rempli par renderUpdateBadge (check async)
    var updateBadge = '<span id="update-badge-' + slug + '" style="display:none;"></span>';

    // Compteur de conteneurs — affiché SEULEMENT si le backend le fournit
    var contSpan = (app.containers_count != null)
        ? '<span style="color:#cbd5e1;">·</span><span title="Conteneurs de l\'application">' + escapeHtml(String(app.containers_count)) + ' cont.</span>'
        : '';

    // Chip atelier — conteneur vide au rendu ; loadWorkshopStates (workshop-ui.js)
    // y insère le chip « Atelier » si un atelier est actif. Le panneau est FERMÉ.
    var wsChip = isSystemApp ? ''
        : '<span id="ws-chip-' + slug + '" style="display:none;"></span>';
    var wsPanel = isSystemApp ? ''
        : '<div id="workshop-panel-' + slug + '" class="ahc2-panel" style="display:none;"></div>';

    // Panneau branches — chargé à la demande par toggleBranches (jamais au rendu)
    var branchPanel = isSystemApp ? ''
        : '<div id="branch-panel-' + slug + '" class="ahc2-panel" style="display:none;"></div>';

    // Menu ⋯ unique (dropdown vanilla, instantané) — absent pour les apps système.
    // Items activés selon le statut : Démarrer si stopped|offline ; Arrêter +
    // Redémarrer + Socle si online|degraded ; Logs / MàJ / Branches toujours.
    var moreMenu = '';
    if (!isSystemApp) {
        var st = app.status || 'checking';
        var canStart = (st === 'stopped' || st === 'offline');
        var canStopRestart = (st === 'online' || st === 'degraded');
        var dis = function (ok) { return ok ? '' : ' disabled'; };
        moreMenu =
            '<button type="button" class="ahc2-more" id="moreBtn_' + slug + '" data-action="toggleHealthMenu" data-args="$el"'
            + ' title="Actions" aria-label="Actions" aria-haspopup="true" aria-expanded="false"'
            + ' style="margin-left:auto;width:22px;height:20px;border:1px solid #e2e8f0;background:#fff;border-radius:6px;'
            + 'font-size:12px;line-height:1;color:#64748b;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;flex:none;">⋯</button>'
            + '<div class="ahc2-menu" id="health-menu-' + slug + '" role="menu">'
            + '<button role="menuitem"' + dis(canStart) + ' data-action="runAppAction" data-arg1="' + slug + '" data-arg2="start"><span class="mi">▶</span> Démarrer</button>'
            + '<button role="menuitem" class="danger"' + dis(canStopRestart) + ' data-action="runAppAction" data-arg1="' + slug + '" data-arg2="stop"><span class="mi">■</span> Arrêter</button>'
            + '<button role="menuitem"' + dis(canStopRestart) + ' data-action="runAppAction" data-arg1="' + slug + '" data-arg2="restart"><span class="mi">⟳</span> Redémarrer</button>'
            + '<div class="sep"></div>'
            + '<button role="menuitem" data-action="showLogs" data-arg1="' + slug + '"><span class="mi">📋</span> Logs</button>'
            + '<button role="menuitem"' + dis(canStopRestart) + ' data-action="socleCheck" data-arg1="' + slug + '"><span class="mi">🩺</span> Socle</button>'
            + '<button role="menuitem" data-action="updateApp" data-arg1="' + slug + '"><span class="mi">⬆</span> Mettre à jour</button>'
            + '<button role="menuitem" data-action="toggleBranches" data-arg1="' + slug + '"><span class="mi">🌿</span> Branches</button>'
            + '</div>';
    }

    return (
        '<div class="col-6 col-md-4 col-lg-3 col-xl-2" data-slug="' + slug + '"' +
        ' data-status="' + escapeHtml(app.status || 'checking') + '"' +
        ' data-ws="0"' +
        ' data-search="' + escapeHtml((app.name + ' ' + app.slug).toLowerCase()) + '">' +
        '<div style="position:relative;background:#fff;border:1px solid #e2e8f0;border-radius:10px;box-shadow:0 1px 2px rgba(15,23,42,.05);display:flex;flex-direction:column;height:100%;overflow:visible;">' +
            '<div style="background:' + headGrad + ';border-radius:10px 10px 0 0;padding:8px 9px 7px;display:flex;gap:7px;align-items:center;">' +
                '<div style="width:26px;height:26px;border-radius:7px;background:' + color + ';display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;flex:none;">' +
                    '<i class="bi ' + icon + '"></i>' +
                '</div>' +
                '<div style="min-width:0;">' +
                    '<div style="font-size:12px;font-weight:700;color:#0f172a;line-height:1.2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + name + '</div>' +
                    '<div style="display:block;font-size:9.5px;font-weight:500;color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + slug + '</div>' +
                '</div>' +
                _cardDot(app) +
            '</div>' +
            '<div class="ahc2-body" id="appbody-' + slug + '" style="padding:6px 9px 7px;">' +
                _cardBodyLines(app) +
            '</div>' +
            wsPanel +
            branchPanel +
            '<div style="margin-top:auto;padding:5px 9px 7px;display:flex;align-items:center;flex-wrap:wrap;font-size:9.5px;color:#94a3b8;gap:4px;">' +
                systemChip +
                versionSpan +
                updateBadge +
                '<span title="Intégration Hub">' + hubLabel + '</span>' +
                contSpan +
                wsChip +
                moreMenu +
            '</div>' +
        '</div>' +
        '</div>'
    );
}

// ─── Branches (lookup à la demande → atelier) ─────────────────────────

var _branchesCache = {};      // slug -> { ts, data } (cache client 30 s)
var _BRANCHES_TTL_MS = 30000;

async function toggleBranches(slug) {
    var panel = document.getElementById('branch-panel-' + slug);
    if (!panel) return;
    if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
    panel.style.display = '';
    var cached = _branchesCache[slug];
    if (cached && Date.now() - cached.ts < _BRANCHES_TTL_MS) {
        _renderBranchPanel(slug, cached.data, true);
        return;
    }
    await refreshBranches(slug);
}

async function refreshBranches(slug) {
    var panel = document.getElementById('branch-panel-' + slug);
    if (!panel) return;
    panel.innerHTML = '<div style="padding:8px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:6px;font-size:11.5px;color:#64748b;display:flex;align-items:center;gap:8px;">'
        + '<span class="spinner-border spinner-border-sm" style="width:12px;height:12px;color:#7c3aed;"></span>'
        + 'Chargement des branches…</div>';
    try {
        var data = await api.getAppBranches(slug);
        _branchesCache[slug] = { ts: Date.now(), data: data };
        _renderBranchPanel(slug, data, false);
    } catch (e) {
        panel.innerHTML = '<div style="font-size:11.5px;color:#b91c1c;background:#fef2f2;border-radius:6px;padding:6px 8px;">'
            + escapeHtml(e.message || 'Erreur inconnue') + '</div>';
    }
}

function _renderBranchPanel(slug, data, fromCache) {
    var panel = document.getElementById('branch-panel-' + slug);
    if (!panel) return;
    if (!data.ok) {
        var hint = data.repo_configured ? ''
            : ' <button data-action="showGitConfigModal" data-arg1="' + escapeHtml(slug) + '"'
              + ' class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:10px;">Configurer le dépôt git</button>';
        panel.innerHTML = '<div style="font-size:11.5px;color:#92400e;background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;padding:6px 8px;">'
            + escapeHtml(data.error || 'Branches indisponibles.') + hint + '</div>';
        return;
    }
    var options = data.branches.map(function (b) {
        var label = b.name + (b.name === data.default_branch ? '  (défaut)' : '');
        return '<option value="' + escapeHtml(b.name) + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var cachedNote = fromCache ? ' · cache' : '';
    panel.innerHTML =
        '<div style="padding:9px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;">'
        + '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">'
            + '<span style="font-size:10px;font-weight:700;color:#7c3aed;text-transform:uppercase;letter-spacing:.6px;">🌿 Branche → atelier</span>'
            + '<span data-action="refreshBranches" data-arg1="' + escapeHtml(slug) + '" role="button" title="Rafraîchir les branches" style="font-size:11px;color:#94a3b8;cursor:pointer;">↻' + cachedNote + '</span>'
        + '</div>'
        + '<div style="display:flex;gap:6px;">'
            + '<select id="branch-select-' + escapeHtml(slug) + '" class="form-select form-select-sm" style="font-size:12px;">' + options + '</select>'
            + '<button data-action="deployBranch" data-arg1="' + escapeHtml(slug) + '"'
            + ' class="btn btn-sm" style="background:#16a34a;border-color:#16a34a;color:#fff;font-weight:600;padding:3px 10px;font-size:11px;white-space:nowrap;"'
            + ' title="Envoyer cette branche dans l\'atelier (clone de DB, la prod n\'est pas touchée)">Déployer ▸</button>'
        + '</div>'
        + '<div style="font-size:10px;color:#94a3b8;margin-top:5px;">La branche part en atelier pour validation — la prod ne reçoit que du main (ship).</div>'
        + '</div>';
}

async function deployBranch(slug) {
    var sel = document.getElementById('branch-select-' + slug);
    var ref = sel ? sel.value : '';
    if (!ref) return;
    var st = window._workshopStates[slug];
    if (!st) {
        try { st = await api.getWorkshopState(slug); window._workshopStates[slug] = st; }
        catch (e) { st = { exists: false }; }
    }
    var operation = st.exists ? 'update' : 'create';
    try {
        var result = (operation === 'update')
            ? await api.updateWorkshop(slug, ref)
            : await api.createWorkshop(slug, ref);
        var panel = document.getElementById('branch-panel-' + slug);
        if (panel) panel.style.display = 'none';
        openWorkshopModal(slug, operation, result.operation_id);
    } catch (e) {
        showToast('Atelier', e.message, 'danger');
    }
}

// ─── Mise à jour (pipeline deploy : pull --ff-only → .env → build → up → health) ───

var POLL_MS = 2000;
var POLL_TIMEOUT_MS = 600000;
var STEP_LABELS = {
    clone: 'Récupération du code',
    env: 'Mise à jour de la configuration',
    build: 'Build des images',
    up: 'Démarrage',
    health: 'Vérification santé',
    done: 'Terminé'
};

function pollDeployStatus(slug, deploymentId) {
    return new Promise(function (resolve) {
        var lastStep = null;
        var started = Date.now();
        var timer = setInterval(async function () {
            // Plafond temporel évalué EN TÊTE de tick : aucune sortie anticipée
            // (erreur réseau, id étranger) ne peut contourner le timeout.
            if (Date.now() - started > POLL_TIMEOUT_MS) {
                clearInterval(timer);
                showToast('Mise à jour', 'Délai dépassé pour ' + slug + ' — vérifiez le statut du déploiement', 'danger');
                resolve(false);
                return;
            }
            var st;
            try {
                st = await api.getAppDeployStatus(slug);
            } catch (e) {
                return; // erreur transitoire : on retente au prochain tick
            }
            var cur = st && st.current;
            // Ne conclure que sur LE déploiement qu'on a lancé (un autre peut passer devant)
            if (!cur || cur.id !== deploymentId) return;
            if (cur.current_step && cur.current_step !== lastStep) {
                lastStep = cur.current_step;
                showToast('Mise à jour', slug + ' — ' + (STEP_LABELS[cur.current_step] || cur.current_step), 'info');
            }
            if (cur.status === 'success') {
                clearInterval(timer);
                showToast('Mise à jour', slug + ' est à jour', 'success');
                loadAppsHealth();
                resolve(true);
            } else if (cur.status === 'failed') {
                clearInterval(timer);
                var logs = (cur.logs || '').slice(-300);
                showToast('Mise à jour', slug + ' : déploiement échoué (' + (STEP_LABELS[cur.current_step] || cur.current_step || '?') + ')' + (logs ? '\n' + logs : ''), 'danger');
                resolve(false);
            }
        }, POLL_MS);
    });
}

async function updateApp(slug) {
    var btn = document.getElementById('updateBtn_' + slug);
    if (btn && btn.disabled) return; // anti double-clic
    if (!confirm('Mettre à jour ' + slug + " ? L'application va être mise à jour, reconstruite puis redémarrée.")) return;
    if (btn) btn.disabled = true;
    try {
        var res = await api.deployApp(slug); // 201 ; 400/409/422 → throw (message = detail)
        showToast('Mise à jour', 'Déploiement #' + res.deployment_id + ' lancé pour ' + slug, 'info');
        await pollDeployStatus(slug, res.deployment_id);
    } catch (e) {
        // api.request jette sur tout non-2xx sans exposer le statut ni le body.
        // 'Erreur serveur' = body sans champ detail = le format 422 du backend
        // (message + checks) → re-precheck pour lister les blocages. Les 400/409
        // ont un detail lisible : affichés tels quels, sans precheck (latence).
        var msg = (e && e.message) || 'Erreur inconnue';
        if (msg === 'Erreur serveur') {
            try {
                var pre = await api.getAppDeployPrecheck(slug);
                if (pre && Array.isArray(pre.checks)) {
                    var ko = pre.checks.filter(function (c) { return c.status === 'fail'; })
                        .map(function (c) { return c.label + ' : ' + (c.detail || 'KO'); })
                        .join('\n');
                    msg = 'Prérequis de déploiement non satisfaits pour ' + slug + (ko ? '\n' + ko : '\nCause possiblement transitoire — réessayez.');
                }
            } catch (_) { /* precheck indisponible : message d'origine */ }
        }
        showToast('Mise à jour', msg, 'danger');
    } finally {
        // En succès la vue est re-rendue (boutons neufs) ; en échec on réactive l'existant.
        var b = document.getElementById('updateBtn_' + slug);
        if (b) b.disabled = false;
    }
}

// ─── Socle-check (rapport d'intégration hub↔app) ──────────────────────

// Statuts des contrôles — mêmes pastilles/couleurs que la vue (#16a34a/#f59e0b/#dc2626)
var _SOCLE_STATUS = {
    pass: { color: '#16a34a' },
    warn: { color: '#f59e0b' },
    fail: { color: '#dc2626' },
};

function _socleScoreColor(passed) {
    return passed >= 7 ? '#16a34a' : (passed >= 4 ? '#f59e0b' : '#dc2626');
}

function _renderSocleReport(report) {
    var rows = (report.controls || []).map(function (c) {
        var st = _SOCLE_STATUS[c.status] || { color: '#9ca3af' };
        return '<div style="display:flex;align-items:baseline;gap:8px;padding:8px 0;border-bottom:1px solid #f1f5f9;">' +
            '<span style="color:' + st.color + ';font-size:13px;flex:none;">●</span>' +
            '<div style="flex:1;min-width:0;">' +
                '<div style="font-size:13px;font-weight:600;color:#0f172a;">' + escapeHtml(c.label || c.id || '') + '</div>' +
                '<div style="font-size:11.5px;color:#64748b;word-break:break-word;">' + escapeHtml(c.detail || '') + '</div>' +
            '</div>' +
            '<span style="flex:none;font-size:10.5px;color:#94a3b8;white-space:nowrap;">' +
                (c.duration_ms != null ? c.duration_ms + ' ms' : '') + '</span>' +
        '</div>';
    }).join('');

    return '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">' +
            '<span style="background:' + _socleScoreColor(report.passed) + ';color:#fff;font-size:13px;font-weight:700;padding:4px 10px;border-radius:6px;">' +
                escapeHtml(report.score || (report.passed + '/' + report.total)) + '</span>' +
            '<span style="font-size:12px;color:#334155;">app <strong>' + escapeHtml(report.app_version || '—') + '</strong>' +
                ' · hub <strong>' + escapeHtml(report.hub_version || '—') + '</strong></span>' +
            '<span style="margin-left:auto;font-size:11px;color:#94a3b8;">' +
                (report.total_ms != null ? report.total_ms + ' ms' : '') + '</span>' +
        '</div>' +
        '<div>' + rows + '</div>';
}

async function socleCheck(slug) {
    var btn = document.getElementById('socleCheckBtn_' + slug);
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    }
    var modal = _showModal('Contrôle du socle — ' + slug,
        '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Contrôles en cours (jusqu\'à ~35 s)…</div>');
    try {
        var report = await api.socleCheck(slug);
        modal.querySelector('.modal-body').innerHTML = _renderSocleReport(report);
    } catch (error) {
        modal.querySelector('.modal-body').innerHTML =
            '<div class="alert alert-danger mb-0">Erreur : ' + escapeHtml(error.message) + '</div>';
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '🩺';
        }
    }
}

// ─── Actions runtime unifiées (start / stop / restart) ─────────────────
// Un seul point d'entrée runAppAction(slug, action) : confirmations, état
// « occupé » (spinner sur ⋯ + carte atténuée + ligne « Action… »), toast
// résultat PAR CONTENEUR (contrat containers: [{name, ok, state}]), puis
// re-sonde immédiate + confirmation à +5 s. L'anti-course _healthRenderSeq
// de loadAppsHealth fait foi sur les re-rendus concurrents.

var _RUN_ACTIONS = {
    start: {
        api: 'startApp', label: 'Démarrage', past: 'démarrés',
        confirm: function (slug) { return confirm('Démarrer ' + slug + ' ?'); },
    },
    stop: {
        api: 'stopApp', label: 'Arrêt', past: 'arrêtés',
        confirm: function (slug) { return confirm('Arrêter ' + slug + ' ? Cette action rendra l\'application indisponible.'); },
    },
    restart: {
        api: 'restartApp', label: 'Redémarrage', past: 'redémarrés',
        confirm: null,
    },
};

function _cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

// Ligne « Action… » temporaire dans le corps de la carte (tant que le statut
// n'a pas basculé — le re-rendu du sondage la fait disparaître).
function _setCardActionLine(slug, text) {
    var body = document.getElementById('appbody-' + slug);
    if (!body) return null;
    var line = document.createElement('div');
    line.id = 'action-line-' + slug;
    line.style.cssText = 'display:flex;justify-content:space-between;gap:6px;font-size:10.5px;color:#475569;padding:1.5px 0;';
    line.innerHTML = '<span style="color:#94a3b8;flex:none;">Action</span>'
        + '<span style="font-weight:600;color:#d97706;font-variant-numeric:tabular-nums;">' + escapeHtml(text) + '</span>';
    body.appendChild(line);
    return line;
}

// Toast de résultat : PAR CONTENEUR si la réponse porte `containers`,
// sinon repli sur les contrats legacy ({message, ok} / {container_name, status}).
function _toastActionByContainer(action, slug, result) {
    var spec = _RUN_ACTIONS[action] || {};
    var title = spec.label || 'Action';
    var containers = result && Array.isArray(result.containers) ? result.containers : null;
    if (containers && containers.length) {
        var ok = containers.filter(function (c) { return c && c.ok; });
        if (ok.length === containers.length) {
            showToast(title, ok.length + '/' + containers.length + ' conteneurs ' + (spec.past || 'traités') + ' — ' + slug, 'success');
        } else {
            var fails = containers.filter(function (c) { return !c || !c.ok; })
                .map(function (c) { return (c && c.name ? c.name : '?') + (c && c.state ? ' (' + c.state + ')' : ''); })
                .join(', ');
            showToast(title, ok.length + '/' + containers.length + ' conteneurs ' + (spec.past || 'traités') + ' — échec : ' + fails, 'danger');
        }
        return;
    }
    if (!result) { showToast(title, slug, 'info'); return; }
    if (result.message) showToast(title, result.message, result.ok === false ? 'danger' : 'success');
    else if (result.container_name) showToast(title, result.container_name + ': ' + result.status, 'success');
    else showToast(title, slug, 'info');
}

async function runAppAction(slug, action) {
    var spec = _RUN_ACTIONS[action];
    if (!spec) return;
    var btn = document.getElementById('moreBtn_' + slug);
    if (btn && btn.classList.contains('loading')) return; // anti double-clic
    if (spec.confirm && !spec.confirm(slug)) return;
    _closeAllHealthMenus();

    // État « occupé » : spinner sur le bouton ⋯ (rotation seule — indicateur
    // fonctionnel), carte légèrement atténuée, ligne « Action… » dans le corps.
    var col = document.querySelector('#appsHealthGrid > [data-slug="' + _cssEscape(slug) + '"]');
    if (btn) { btn.classList.add('loading'); btn.innerHTML = '<span class="ahc2-spinner"></span>'; btn.title = 'Action en cours'; }
    if (col) col.style.opacity = '.75';
    var line = _setCardActionLine(slug, spec.label + '…');

    try {
        var result = await api[spec.api](slug);
        _toastActionByContainer(action, slug, result);
        if (!result || result.ok !== false) {
            // Re-sonde IMMÉDIATE + confirmation à +5 s (l'état docker peut
            // encore basculer) — remplace l'ancien setTimeout 3 s à l'aveugle.
            loadAppsHealth();
            setTimeout(function () { loadAppsHealth(); }, 5000);
        }
    } catch (error) {
        // Erreur réseau / refus API : toast danger avec le message renvoyé.
        showToast('Erreur', (error && error.message) || 'Erreur inconnue', 'danger');
    } finally {
        // En succès le re-sonde re-rend la grille (carte neuve) ; en échec on
        // restaure le bouton pour ne pas laisser la carte bloquée « occupée ».
        if (btn) { btn.classList.remove('loading'); btn.innerHTML = '⋯'; btn.title = 'Actions'; }
        if (col) col.style.opacity = '';
        if (line) line.remove();
    }
}

async function showLogs(slug) {
    var modal = _showModal('Logs — ' + slug, '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement…</div>');
    try {
        var result = await api.getAppLogs(slug, 150, 'api');
        var lines = result.lines || '(aucun log)';
        modal.querySelector('.modal-body').innerHTML =
            '<pre style="font-size:11px;line-height:1.4;max-height:60vh;overflow:auto;background:#1e293b;color:#e2e8f0;padding:12px;border-radius:6px;">' + escapeHtml(lines) + '</pre>';
    } catch (error) {
        modal.querySelector('.modal-body').innerHTML =
            '<div class="alert alert-danger">Erreur: ' + escapeHtml(error.message) + '</div>';
    }
}

// ─── Update check + Changelog ─────────────────────────────────────────

async function checkAllUpdates(apps) {
    for (var i = 0; i < apps.length; i++) {
        try {
            var update = await api.checkUpdate(apps[i].slug);
            if (update.update_available && update.repo_configured) {
                renderUpdateBadge(apps[i].slug, update);
            }
        } catch (e) {
            // Silencieux — le check de mise a jour est non-bloquant
        }
    }
}

function renderUpdateBadge(slug, update) {
    var badge = document.getElementById('update-badge-' + slug);
    if (!badge) return;
    // Chip compact cliquable (changelog) — remplace la version courante dans le
    // footer (l'info « d'où l'on part » est dans le title et le modal changelog).
    var ver = document.getElementById('appver-' + slug);
    if (ver) ver.style.display = 'none';
    badge.style.display = '';
    badge.innerHTML =
        '<button data-action="showChangelog" data-arg1="' + slug + '" data-arg2="' + escapeHtml(update.current_version || '') + '" '
        + 'title="Mise à jour disponible : ' + escapeHtml(update.current_version || '?') + ' → ' + escapeHtml(update.latest_version || '?') + ' — voir le changelog" '
        + 'style="display:inline-block;font-size:8.5px;font-weight:700;color:#92400e;background:#fef3c7;border:0;'
        + 'border-radius:4px;padding:1px 5px;cursor:pointer;">'
        + 'MàJ ' + escapeHtml(update.current_version || '?') + '→' + escapeHtml(update.latest_version || '?')
        + '</button>';
}

async function showChangelog(slug, currentVersion) {
    _changelogState = { slug: slug, releases: [], selectedIndex: 0, tab: 'notes', currentVersion: currentVersion || null };
    var modal = _showModal('Changelog — ' + slug,
        '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement des releases…</div>');
    try {
        var result = await api.getReleases(slug);
        if (!result.ok || !result.releases || !result.releases.length) {
            return await _showRawChangelog(modal, slug);
        }
        _changelogState.releases = result.releases;
        _renderChangelogBody(modal);
    } catch (error) {
        modal.querySelector('.modal-body').innerHTML =
            '<div class="alert alert-danger">Erreur: ' + escapeHtml(error.message) + '</div>';
    }
}

var _changelogState = { slug: null, releases: [], selectedIndex: 0, tab: 'notes' };

async function _showRawChangelog(modal, slug) {
    try {
        var result = await api.getChangelog(slug);
        if (!result.ok || !result.markdown) {
            modal.querySelector('.modal-body').innerHTML =
                '<div class="alert alert-warning">Aucun changelog disponible (repo non configuré ou inaccessible).</div>';
            return;
        }
        modal.querySelector('.modal-body').innerHTML =
            '<pre style="font-size:12px;line-height:1.5;max-height:60vh;overflow:auto;background:#f8fafc;color:#334155;padding:16px;border-radius:6px;white-space:pre-wrap;font-family:system-ui,-apple-system,sans-serif;">' +
            escapeHtml(result.markdown) + '</pre>';
    } catch (error) {
        modal.querySelector('.modal-body').innerHTML =
            '<div class="alert alert-danger">Erreur: ' + escapeHtml(error.message) + '</div>';
    }
}

function _renderChangelogBody(modal) {
    var s = _changelogState;
    var options = s.releases.map(function (r, i) {
        return '<option value="' + i + '"' + (i === s.selectedIndex ? ' selected' : '') + '>' +
            escapeHtml(r.tag) + (r.published_at ? ' — ' + _formatDate(r.published_at) : '') +
            '</option>';
    }).join('');

    var release = s.releases[s.selectedIndex];
    var prevTag = (s.selectedIndex + 1 < s.releases.length) ? s.releases[s.selectedIndex + 1].tag : null;
    var notesBtn = s.tab === 'notes' ? 'btn-primary' : 'btn-outline-secondary';
    var codeBtn = s.tab === 'code' ? 'btn-primary' : 'btn-outline-secondary';

    modal.querySelector('.modal-body').innerHTML =
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap;">' +
            '<select data-action="_changelogSelectVersion" data-arg1="$el" class="form-select form-select-sm" style="width:auto;">' +
                options +
            '</select>' +
            (release.html_url ? '<a href="' + escapeHtml(release.html_url) + '" target="_blank" style="font-size:12px;color:#6366f1;text-decoration:none;">GitHub ↗</a>' : '') +
            '<div style="margin-left:auto;">' +
                '<button data-action="_changelogTab" data-args="notes" class="btn btn-sm ' + notesBtn + '">📖 Notes</button> ' +
                '<button data-action="_changelogTab" data-args="code" class="btn btn-sm ' + codeBtn + '"' +
                    (prevTag ? '' : ' disabled title="Première version : pas de comparaison"') +
                '> Code</button>' +
            '</div>' +
        '</div>' +
        '<div id="changelogTabContent"></div>';

    if (s.tab === 'notes') {
        _renderNotesTab(release);
    } else {
        _loadDiffTab(s.slug, prevTag, release.tag);
    }
}

function _renderNotesTab(release) {
    var el = document.getElementById('changelogTabContent');
    if (!el) return;

    // Mode cumulatif : afficher les notes de TOUTES les versions
    // entre la version actuelle (depuis le badge) et la derniere release.
    var s = _changelogState;
    // Mode cumulatif : uniquement depuis le badge (currentVersion fourni)
    // et quand on regarde la derniere version (selectedIndex === 0).
    if (s.currentVersion && s.selectedIndex === 0) {
        var newerReleases = s.releases.filter(function (r) {
            return _versionNewer(r.tag, s.currentVersion);
        });
        // Si au moins 2 releases plus recentes (ou 1 mais differente de la selection),
        // on affiche le cumul. Sinon on tombe sur le mode normal.
        if (newerReleases.length >= 1) {
            _renderCumulativeNotes(newerReleases, s.currentVersion);
            return;
        }
    }

    // Mode normal : notes d'une seule version
    _renderSingleNotes(release);
}

function _renderSingleNotes(release) {
    var el = document.getElementById('changelogTabContent');
    if (!el) return;
    var body = release.body ? escapeHtml(release.body) : '(pas de notes pour cette version)';
    el.innerHTML =
        '<pre style="font-size:12px;line-height:1.5;max-height:55vh;overflow:auto;background:#f8fafc;color:#334155;padding:16px;border-radius:6px;white-space:pre-wrap;font-family:system-ui,-apple-system,sans-serif;">' +
        body + '</pre>';
}

function _renderCumulativeNotes(newerReleases, fromVersion) {
    var el = document.getElementById('changelogTabContent');
    if (!el) return;

    var toVersion = newerReleases[0].tag;
    var header = '<div style="font-size:13px;color:#6366f1;margin-bottom:10px;font-weight:600;">' +
        '📋 Notes cumulatives : <strong>' + escapeHtml(fromVersion) + '</strong> → <strong>' + escapeHtml(toVersion) + '</strong> ' +
        '(' + newerReleases.length + ' version' + (newerReleases.length > 1 ? 's' : '') + ')' +
        '</div>';

    var sections = newerReleases.map(function (r) {
        var body = r.body ? escapeHtml(r.body) : '(pas de notes)';
        return '<div style="border:1px solid #e2e8f0;border-radius:6px;margin-bottom:10px;padding:12px;background:#fff;">' +
            '<div style="font-weight:600;font-size:13px;color:#0f172a;margin-bottom:6px;">' +
            escapeHtml(r.tag) + (r.published_at ? ' — <span style="color:#94a3b8;font-weight:400;">' + _formatDate(r.published_at) + '</span>' : '') +
            '</div>' +
            '<pre style="font-size:12px;line-height:1.5;background:#f8fafc;color:#334155;padding:10px;border-radius:4px;white-space:pre-wrap;font-family:system-ui,-apple-system,sans-serif;margin:0;">' +
            body + '</pre>' +
            '</div>';
    }).join('');

    el.innerHTML =
        '<div style="max-height:55vh;overflow:auto;">' +
        header +
        sections +
        '<div style="font-size:11px;color:#94a3b8;margin-top:8px;">Utilisez le selecteur de version ci-dessus pour voir les details d\'une version specifique.</div>' +
        '</div>';
}

function _parseVersion(v) {
    return (v || '0').split('.').map(function (p) { return parseInt(p, 10) || 0; });
}

function _versionNewer(a, b) {
    var pa = _parseVersion(a), pb = _parseVersion(b);
    for (var i = 0; i < 3; i++) {
        if (pa[i] > pb[i]) return true;
        if (pa[i] < pb[i]) return false;
    }
    return false;
}

async function _loadDiffTab(slug, fromTag, toTag) {
    var el = document.getElementById('changelogTabContent');
    if (!el) return;
    el.innerHTML = '<div class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Chargement du diff…</div>';
    try {
        var result = await api.getDiff(slug, fromTag, toTag);
        if (!result.ok || !result.files || !result.files.length) {
            el.innerHTML = '<div class="alert alert-warning">Aucun fichier modifié trouvé (comparaison ' +
                escapeHtml(fromTag) + ' → ' + escapeHtml(toTag) + ').</div>';
            return;
        }
        _renderDiffTab(result);
    } catch (error) {
        el.innerHTML = '<div class="alert alert-danger">Erreur: ' + escapeHtml(error.message) + '</div>';
    }
}

function _renderDiffTab(diff) {
    var el = document.getElementById('changelogTabContent');
    if (!el) return;

    var totalAdd = 0, totalDel = 0;
    diff.files.forEach(function (f) { totalAdd += f.additions; totalDel += f.deletions; });

    var summary = diff.total_commits + ' commit' + (diff.total_commits !== 1 ? 's' : '') +
        ' · ' + diff.files.length + ' fichier' + (diff.files.length !== 1 ? 's' : '') +
        ' · <span style="color:#16a34a;">+' + totalAdd + '</span>' +
        ' <span style="color:#dc2626;">-' + totalDel + '</span>';

    var fileList = diff.files.map(function (f, i) {
        var icon = f.status === 'added' ? '🟢' : f.status === 'removed' ? '🔴' : f.status === 'renamed' ? '🔄' : '🟡';
        var toggleBtn = f.patch
            ? '<button data-action="_toggleDiff" data-arg1="diff-' + i + '" data-arg2="$el" class="btn btn-sm btn-outline-secondary py-0 px-1" style="font-size:11px;">▸</button>'
            : '<span style="font-size:11px;color:#94a3b8;">(diff trop grand)</span>';
        var patchHtml = f.patch
            ? '<div id="diff-' + i + '" style="display:none;margin-top:6px;">' + _colorizeDiff(f.patch) + '</div>'
            : '';
        return '<div style="padding:5px 0;border-bottom:1px solid #f1f5f9;">' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:12px;">' +
                '<span>' + icon + '</span>' +
                '<span style="flex:1;font-family:monospace;color:#334155;word-break:break-all;">' + escapeHtml(f.filename) + '</span>' +
                '<span style="color:#16a34a;font-size:11px;white-space:nowrap;">+' + f.additions + '</span>' +
                '<span style="color:#dc2626;font-size:11px;white-space:nowrap;">-' + f.deletions + '</span>' +
                toggleBtn +
            '</div>' +
            patchHtml +
        '</div>';
    }).join('');

    el.innerHTML =
        '<div style="font-size:12px;color:#64748b;margin-bottom:8px;">' + summary + '</div>' +
        '<div style="max-height:55vh;overflow:auto;">' + fileList + '</div>' +
        (diff.html_url ? '<div style="margin-top:8px;"><a href="' + escapeHtml(diff.html_url) + '" target="_blank" style="font-size:12px;color:#6366f1;">Voir la comparaison complète sur GitHub ↗</a></div>' : '');
}

function _colorizeDiff(patch) {
    var lines = escapeHtml(patch).split('\n');
    return '<pre style="font-size:11px;line-height:1.4;background:#1e293b;color:#cbd5e1;padding:10px;border-radius:4px;overflow-x:auto;white-space:pre-wrap;">' +
        lines.map(function (line) {
            if (line.charAt(0) === '+') return '<span style="color:#4ade80;">' + line + '</span>';
            if (line.charAt(0) === '-') return '<span style="color:#f87171;">' + line + '</span>';
            if (line.substring(0, 2) === '@@') return '<span style="color:#818cf8;">' + line + '</span>';
            return line;
        }).join('\n') + '</pre>';
}

function _toggleDiff(id, btn) {
    var el = document.getElementById(id);
    if (!el) return;
    if (el.style.display === 'none') {
        el.style.display = 'block';
        btn.textContent = '▾';
    } else {
        el.style.display = 'none';
        btn.textContent = '▸';
    }
}

function _changelogSelectVersion(el) {
    _changelogState.selectedIndex = parseInt(el.value, 10);
    _changelogState.tab = 'notes';
    var modal = document.getElementById('appsHealthModal');
    if (modal) _renderChangelogBody(modal);
}

/**
 * Ferme le modal de santé des apps (data-action du btn-close du modal généré)
 */
function closeAppsHealthModal() {
    var modal = document.getElementById('appsHealthModal');
    if (modal) modal.remove();
}

function _changelogTab(tab) {
    _changelogState.tab = tab;
    var modal = document.getElementById('appsHealthModal');
    if (modal) _renderChangelogBody(modal);
}

function _formatDate(iso) {
    if (!iso) return '';
    try {
        var d = new Date(iso);
        return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
    } catch (e) {
        return iso.substring(0, 10);
    }
}

// ─── Modal utilitaire ─────────────────────────────────────────────────

function _showModal(title, bodyHtml) {
    // Supprime un eventuel modal precedent
    var existing = document.getElementById('appsHealthModal');
    if (existing) existing.remove();

    var html =
        '<div class="modal fade show" id="appsHealthModal" tabindex="-1" style="display:block;background:rgba(0,0,0,0.5);">' +
        '<div class="modal-dialog modal-lg modal-dialog-scrollable">' +
        '<div class="modal-content">' +
        '<div class="modal-header">' +
        '<h5 class="modal-title">' + escapeHtml(title) + '</h5>' +
        '<button type="button" class="btn-close" data-action="closeAppsHealthModal"></button>' +
        '</div>' +
        '<div class="modal-body">' + bodyHtml + '</div>' +
        '</div></div></div>';

    var div = document.createElement('div');
    div.innerHTML = html;
    document.body.appendChild(div.firstElementChild);
    return document.getElementById('appsHealthModal');
}

// ─── Mise à jour ciblée d'une carte (SSE app_health_changed) ──────────
// Payload émis par le checker (hub/app/health_checker.py:295-301) :
//   { slug, name, health_status: 'healthy'|'unhealthy'|...,
//     previous_status, error }
// Tolère aussi les valeurs display (online/offline/degraded/stopped) et un
// healthItem complet portant status/latency_ms/uptime_seconds/containers_count.
// Ne touche QUE la carte du slug : pas de re-render de la grille, pas d'API.
var _SSE_STATUS_MAP = {
    healthy: 'online', online: 'online',
    unhealthy: 'offline', offline: 'offline',
    degraded: 'degraded', stopped: 'stopped',
};

function updateAppsHealthCard(slug, healthItem) {
    if (!slug) return;
    var section = document.getElementById('appsHealthSection');
    if (!section || section.classList.contains('d-none')) return; // vue non visible
    var col = document.querySelector('#appsHealthGrid > [data-slug="' + _cssEscape(slug) + '"]');
    if (!col) return; // app filtrée/absente de la vue : silence
    var btn = document.getElementById('moreBtn_' + slug);
    if (btn && btn.classList.contains('loading')) return; // action en cours : le re-sonde rafraîchira

    var data = _healthBySlug[slug];
    if (!data) return;
    var raw = (healthItem && (healthItem.health_status || healthItem.status)) || '';
    var newStatus = _SSE_STATUS_MAP[raw];
    if (!newStatus) return; // valeur inconnue : ne rien casser

    data.status = newStatus;
    if (healthItem && healthItem.error !== undefined) data.error = healthItem.error;
    if (healthItem && healthItem.containers_count !== undefined) data.containers_count = healthItem.containers_count;
    if (healthItem && healthItem.latency_ms !== undefined) data.latency_ms = healthItem.latency_ms;
    if (healthItem && healthItem.uptime_seconds !== undefined) data.uptime_seconds = healthItem.uptime_seconds;
    // Offline/arrêtée : les métriques précédentes ne sont plus valables et le
    // payload SSE ne les porte pas — on les retire (la carte affiche l'erreur).
    if (newStatus === 'offline' || newStatus === 'stopped') {
        data.latency_ms = null;
        data.uptime_seconds = null;
    }

    // États à préserver (le re-render d'une carte les recrée fermés/vides) :
    // panneaux branch/atelier, chip MàJ, version masquée par le chip, data-ws.
    function _capture(id) {
        var el = document.getElementById(id);
        return (el && col.contains(el)) ? { display: el.style.display, html: el.innerHTML } : null;
    }
    var branchState = _capture('branch-panel-' + slug);
    var wsState = _capture('workshop-panel-' + slug);
    var badgeState = _capture('update-badge-' + slug);
    var wsChipState = _capture('ws-chip-' + slug);
    var wsAttr = col.getAttribute('data-ws');

    var tmp = document.createElement('div');
    tmp.innerHTML = renderAppCard(data);
    var newCol = tmp.firstElementChild;
    col.replaceWith(newCol);

    function _restore(id, state) {
        var el = document.getElementById(id);
        if (!el || state === null) return;
        el.innerHTML = state.html;
        el.style.display = state.display;
    }
    _restore('branch-panel-' + slug, branchState);
    _restore('workshop-panel-' + slug, wsState);
    if (badgeState && badgeState.html) {
        _restore('update-badge-' + slug, badgeState);
        var ver = document.getElementById('appver-' + slug);
        if (ver) ver.style.display = 'none';
    }
    _restore('ws-chip-' + slug, wsChipState);
    newCol.setAttribute('data-ws', wsAttr || '0');

    // Le filtre statut actif peut désormais cacher/montrer cette carte.
    if (typeof _applyHealthFilters === 'function') _applyHealthFilters();
}

// ─── Filtres (client-side, instantanés) ───────────────────────────────

var _healthFilterText = '';
var _healthFilterStatus = 'all';
var _healthFilterWs = false;

function filterAppsHealth(input) {
    _healthFilterText = (input.value || '').trim().toLowerCase();
    _applyHealthFilters();
}

function filterAppsHealthStatus(status, el) {
    _healthFilterStatus = status;
    var group = document.getElementById('appsHealthStatusGroup');
    if (group) {
        Array.prototype.forEach.call(group.querySelectorAll('button'), function (b) {
            b.classList.toggle('active', b === el);
        });
    }
    _applyHealthFilters();
}

function filterAppsHealthWs(el) {
    _healthFilterWs = !!el.checked;
    _applyHealthFilters();
}

function _applyHealthFilters() {
    var cols = document.querySelectorAll('#appsHealthGrid > [data-slug]');
    Array.prototype.forEach.call(cols, function (col) {
        var okStatus = (_healthFilterStatus === 'all' || col.getAttribute('data-status') === _healthFilterStatus);
        var okWs = (!_healthFilterWs || col.getAttribute('data-ws') === '1');
        var okText = (!_healthFilterText || (col.getAttribute('data-search') || '').indexOf(_healthFilterText) !== -1);
        col.style.display = (okStatus && okWs && okText) ? '' : 'none';
    });
}

// ─── Menu ⋯ (dropdown vanilla, SANS dépendance Bootstrap JS) ──────────
// Instantané : display none/block via la classe .open — zéro transition.
// Refermé par le clic extérieur (listener document ci-dessous) ou au
// déclenchement d'une action.

function _closeAllHealthMenus() {
    var open = document.querySelectorAll('.ahc2-menu.open');
    Array.prototype.forEach.call(open, function (m) {
        m.classList.remove('open');
        var foot = m.parentElement;
        var btn = foot ? foot.querySelector('.ahc2-more') : null;
        if (btn) btn.setAttribute('aria-expanded', 'false');
    });
}

// Le menu 22px doit rester dans le viewport : mesure à l'ouverture, flip
// simple vertical (menu ancré en bas de carte si déborderait en bas) et
// horizontal (ancré au bord gauche de la carte si déborderait à gauche).
function _flipMenuInViewport(menu) {
    menu.style.left = ''; menu.style.right = ''; menu.style.top = ''; menu.style.bottom = '';
    var r = menu.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    if (r.bottom > vh - 8) { menu.style.top = 'auto'; menu.style.bottom = '30px'; }
    if (r.left < 8 && r.right <= vw - 8) { menu.style.left = '7px'; menu.style.right = 'auto'; }
}

function toggleHealthMenu(btn) {
    if (!btn || btn.classList.contains('loading')) return;
    var foot = btn.parentElement;
    var menu = foot ? foot.querySelector('.ahc2-menu') : null;
    if (!menu) return;
    var wasOpen = menu.classList.contains('open');
    _closeAllHealthMenus();
    if (wasOpen) return;
    menu.classList.add('open');
    btn.setAttribute('aria-expanded', 'true');
    _flipMenuInViewport(menu);
}

// Fermeture : clic sur un item (l'action est dispatchée par le délégué
// data-action d'app.js — ordre des listeners indifférent ici, la fermeture
// ne retire aucun élément du DOM) ou clic n'importe où ailleurs. Les clics
// sur le bouton ⋯ lui-même sont ignorés : toggleHealthMenu bascule.
(function () {
    document.addEventListener('click', function (e) {
        if (e.target.closest('.ahc2-more')) return;
        if (!document.querySelector('.ahc2-menu.open')) return;
        _closeAllHealthMenus();
    });
})();
