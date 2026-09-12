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
        return renderAppCard({
            name: a.name, slug: a.slug, icon: a.icon, color: a.color,
            is_system_app: a.is_system_app, status: 'checking',
        });
    }).join('');
    updateAppsHealthTimestamps();
    _applyHealthFilters();
}

async function renderAppsHealth(data) {
    const summary = data.summary;
    const sumEl = document.getElementById('appsHealthSummary');
    sumEl.innerHTML =
        '<span style="color:#16a34a;">●</span> ' + summary.online + ' en ligne · ' +
        '<span style="color:#dc2626;">●</span> ' + summary.offline + ' hors ligne' +
        (summary.degraded > 0 ? ' · <span style="color:#f59e0b;">●</span> ' + summary.degraded + ' degrade' : '') +
        ' · ' + summary.total + ' apps';

    const grid = document.getElementById('appsHealthGrid');
    grid.innerHTML = data.apps.map(app => renderAppCard(app)).join('');

    updateAppsHealthTimestamps();

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

function renderAppCard(app) {
    var isSystemApp = app.is_system_app === true || app.slug === 'hub';

    // Direction « Ops console » : bandeau teinté selon le statut + badge plein.
    // Slots FIXES : les emplacements (statut, 3 métriques, hub) existent toujours
    // avec des placeholders (« … », « — ») — le layout ne bouge jamais.
    var statusStyle = {
        checking: { grad: 'linear-gradient(90deg,#f8fafc,#ffffff)', badge: '#9ca3af', text: '…' },
        online:   { grad: 'linear-gradient(90deg,#f0fdf4,#ffffff)', badge: '#16a34a', text: 'Online' },
        offline:  { grad: 'linear-gradient(90deg,#fef2f2,#ffffff)', badge: '#dc2626', text: 'Offline' },
        degraded: { grad: 'linear-gradient(90deg,#fffbeb,#ffffff)', badge: '#f59e0b', text: 'Degraded' },
    }[app.status] || { grad: 'linear-gradient(90deg,#f8fafc,#ffffff)', badge: '#9ca3af', text: (app.status || 'Unknown') };

    var icon = escapeHtml(app.icon || 'bi-app');
    var color = escapeHtml(app.color || '#6c757d');
    var name = escapeHtml(app.name);
    var slug = escapeHtml(app.slug);
    var latency = app.latency_ms ? app.latency_ms + ' ms' : '';
    var version = app.version || '';
    var uptime = formatUptime(app.uptime_seconds);
    var error = app.error
        ? '<div style="margin:0 14px 10px;font-size:11.5px;color:#b91c1c;background:#fef2f2;border-radius:6px;padding:6px 8px;">' + escapeHtml(app.error.substring(0, 100)) + '</div>'
        : '';

    // Badge « Système » pour les apps système (HUB)
    var systemBadge = isSystemApp
        ? ' <span style="background:#e0e7ff;color:#4338ca;font-size:10px;padding:1px 6px;border-radius:8px;font-weight:600;vertical-align:1px;">SYSTÈME</span>'
        : '';

    // Métriques en valeurs fortes — grille FIXE 3 cellules, placeholder « — »
    var stats = [
        [latency || '—', 'Latence'],
        [uptime || '—', 'Uptime'],
        [app.services_count != null ? String(app.services_count) : '—', 'Services'],
    ];
    var statsRow =
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid #e5e7eb;">'
        + stats.map(function (s) {
            return '<div style="padding:9px 6px;text-align:center;">'
                + '<div style="font-size:17px;font-weight:700;color:#0f172a;">' + escapeHtml(s[0]) + '</div>'
                + '<div style="font-size:9.5px;color:#94a3b8;text-transform:uppercase;letter-spacing:.6px;">' + s[1] + '</div>'
                + '</div>';
        }).join('<div style="border-left:1px solid #f1f5f9;"></div>')
        + '</div>';

    var hubLabel = app.hub_connected === true ? 'Hub ✓'
        : (app.hub_connected === false ? 'Hub ✗' : 'Hub —');

    // L'app est joignable mais sans aucune donnée santé (ni version, ni uptime,
    // ni intégration Hub, ni erreur) → vraiment aucun endpoint /health exploitable.
    // Ne s'applique PAS aux apps système (le HUB se sonde lui-même).
    var noHealth = (!isSystemApp && app.status === 'online' && !version && !uptime && !latency
        && app.hub_connected !== true && app.hub_connected !== false && !error);
    var healthNote = noHealth
        ? '<div style="padding:0 14px 8px;font-size:11px;color:#94a3b8;"><i class="bi bi-info-circle me-1"></i>Serveur joignable — pas d\'endpoint /health standard</div>'
        : '';

    var degradedInfo = '';
    if (app.status === 'degraded' && app.error) {
        degradedInfo = ' <i class="bi bi-info-circle" title="' + escapeHtml(app.error) + '" style="cursor:help;font-size:11px;color:#92400e;"></i>';
    }

    // Barre d'actions unifiée (boutons collés) — masquée pour les apps système
    var actionButtons = '';
    if (!isSystemApp) {
        var btnCss = 'border:none;border-right:1px solid #e2e8f0;border-radius:0;color:#334155;background:#fff;padding:3px 9px;font-size:12px;cursor:pointer;';
        actionButtons =
            '<div style="display:flex;border:1px solid #cbd5e1;border-radius:6px;overflow:hidden;">' +
                '<button data-action="startApp" data-arg1="' + slug + '" style="' + btnCss + 'color:#16a34a;" title="Demarrer">▶</button>' +
                '<button data-action="stopApp" data-arg1="' + slug + '" style="' + btnCss + 'color:#dc2626;" title="Arreter">■</button>' +
                '<button data-action="restartApp" data-arg1="' + slug + '" id="restartBtn_' + slug + '" style="' + btnCss + '" title="Redemarrer">⟳</button>' +
                '<button data-action="showLogs" data-arg1="' + slug + '" style="' + btnCss + '" title="Logs">📋</button>' +
                '<button data-action="socleCheck" data-arg1="' + slug + '" id="socleCheckBtn_' + slug + '" style="' + btnCss + '" title="Contrôler le socle">🩺</button>' +
                '<button data-action="toggleBranches" data-arg1="' + slug + '" id="branchBtn_' + slug + '" style="' + btnCss.replace('border-right:1px solid #e2e8f0;', '') + 'color:#7c3aed;background:#f5f3ff;" title="Déployer une branche (via l\'atelier)">🌿</button>' +
            '</div>';
    }

    // Panneau branches — chargé à la demande par toggleBranches (jamais au rendu)
    var branchPanel = isSystemApp ? ''
        : '<div id="branch-panel-' + slug + '" style="display:none;"></div>';

    // Bloc atelier (workshop) — placeholder rempli par loadWorkshopStates (workshop-ui.js)
    var workshopBlock = (!isSystemApp && typeof renderWorkshopBlock === 'function')
        ? renderWorkshopBlock(slug) : '';

    return (
        '<div class="col-12 col-md-6 col-lg-4" data-slug="' + slug + '"' +
        ' data-status="' + escapeHtml(app.status || 'checking') + '"' +
        ' data-ws="0"' +
        ' data-search="' + escapeHtml((app.name + ' ' + app.slug).toLowerCase()) + '">' +
        '<div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.07);display:flex;flex-direction:column;height:100%;min-height:150px;overflow:hidden;">' +
            '<div style="background:' + statusStyle.grad + ';border-bottom:1px solid #e5e7eb;padding:11px 14px;display:flex;align-items:center;gap:10px;">' +
                '<div style="width:36px;height:36px;border-radius:8px;background:' + color + ';display:flex;align-items:center;justify-content:center;color:white;font-size:16px;flex-shrink:0;">' +
                    '<i class="bi ' + icon + '"></i>' +
                '</div>' +
                '<div style="flex:1;min-width:0;">' +
                    '<div style="font-weight:700;font-size:14px;color:#0f172a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + name +
                    (systemBadge ? systemBadge : '') +
                    '</div>' +
                    '<div style="font-size:11px;color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + slug + ' · v' + (version ? escapeHtml(version) : '—') + '</div>' +
                '</div>' +
                '<span style="background:' + statusStyle.badge + ';color:#fff;font-size:10px;padding:3px 8px;border-radius:5px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;flex-shrink:0;">' + statusStyle.text + degradedInfo + '</span>' +
            '</div>' +
            statsRow +
            '<div id="update-badge-' + slug + '" style="display:none;"></div>' +
            healthNote +
            error +
            workshopBlock +
            branchPanel +
            '<div style="margin-top:auto;padding:8px 14px;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #f1f5f9;">' +
                '<span style="font-size:10.5px;color:#94a3b8;">' +
                    'Vérifié il y a <span class="apps-health-timestamp" data-ts="' + (app.checked_at || '') + '">…</span>' +
                    ' · ' + hubLabel +
                '</span>' +
                actionButtons +
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
    panel.innerHTML = '<div style="margin:10px 14px 0;padding:8px 10px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:6px;font-size:11.5px;color:#64748b;display:flex;align-items:center;gap:8px;">'
        + '<span class="spinner-border spinner-border-sm" style="width:12px;height:12px;color:#7c3aed;"></span>'
        + 'Chargement des branches…</div>';
    try {
        var data = await api.getAppBranches(slug);
        _branchesCache[slug] = { ts: Date.now(), data: data };
        _renderBranchPanel(slug, data, false);
    } catch (e) {
        panel.innerHTML = '<div style="margin:10px 14px 0;font-size:11.5px;color:#b91c1c;background:#fef2f2;border-radius:6px;padding:6px 8px;">'
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
        panel.innerHTML = '<div style="margin:10px 14px 0;font-size:11.5px;color:#92400e;background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;padding:6px 8px;">'
            + escapeHtml(data.error || 'Branches indisponibles.') + hint + '</div>';
        return;
    }
    var options = data.branches.map(function (b) {
        var label = b.name + (b.name === data.default_branch ? '  (défaut)' : '');
        return '<option value="' + escapeHtml(b.name) + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var cachedNote = fromCache ? ' · cache' : '';
    panel.innerHTML =
        '<div style="margin:10px 14px 0;padding:9px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;">'
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

// ─── Restart ──────────────────────────────────────────────────────────

async function restartApp(slug) {
    var btn = document.getElementById('restartBtn_' + slug);
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    try {
        var result = await api.restartApp(slug);
        showToast('Redemarrage', result.container_name + ': ' + result.status, 'success');
        setTimeout(function() { loadAppsHealth(); }, 2000);
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
        btn.disabled = false;
        btn.innerHTML = '⟳';
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

// ─── Start / Stop / Logs ──────────────────────────────────────────────

async function startApp(slug) {
    if (!confirm('Demarrer ' + slug + ' ?')) return;
    showToast('Demarrage', 'Demarrage de ' + slug + ' en cours…', 'info');
    try {
        var result = await api.startApp(slug);
        showToast('Demarrage', result.message, result.ok ? 'success' : 'danger');
        if (result.ok) setTimeout(function() { loadAppsHealth(); }, 3000);
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function stopApp(slug) {
    if (!confirm('Arreter ' + slug + ' ? Cette action rendra l\'application indisponible.')) return;
    showToast('Arret', 'Arret de ' + slug + ' en cours…', 'info');
    try {
        var result = await api.stopApp(slug);
        showToast('Arret', result.message, result.ok ? 'success' : 'danger');
        if (result.ok) setTimeout(function() { loadAppsHealth(); }, 3000);
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
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
    badge.style.display = 'block';
    badge.style.marginTop = '6px';
    badge.innerHTML =
        '<button data-action="showChangelog" data-arg1="' + slug + '" data-arg2="' + escapeHtml(update.current_version || '') + '" ' +
        'style="background:#fef3c7;color:#92400e;border:1px solid #fcd34d;font-size:11px;padding:3px 10px;border-radius:6px;cursor:pointer;display:inline-block;width:100%;text-align:left;">' +
        '🔄 Mise à jour disponible : <strong>' + escapeHtml(update.current_version) + '</strong> → <strong>' + escapeHtml(update.latest_version) + '</strong>' +
        ' <span style="float:right;color:#b45309;">📋 Voir le changelog</span>' +
        '</button>';
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

// ─── Timestamps relatifs ──────────────────────────────────────────────

function updateAppsHealthTimestamps() {
    var els = document.querySelectorAll('.apps-health-timestamp');
    var now = Date.now();
    for (var i = 0; i < els.length; i++) {
        var ts = els[i].dataset.ts;
        if (!ts) continue;
        var then = new Date(ts).getTime();
        var diff = Math.max(0, Math.floor((now - then) / 1000));
        if (diff < 60) els[i].textContent = diff + 's';
        else if (diff < 3600) els[i].textContent = Math.floor(diff / 60) + 'min';
        else els[i].textContent = Math.floor(diff / 3600) + 'h';
    }
}

setInterval(updateAppsHealthTimestamps, 30000);

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
