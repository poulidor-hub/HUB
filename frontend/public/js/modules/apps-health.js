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

async function loadAppsHealth() {
    const btn = document.getElementById('appsHealthRefreshBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>…';

    try {
        const result = await api.getAppsHealth();
        await renderAppsHealth(result);
    } catch (error) {
        console.error('Erreur chargement health:', error);
        showToast('Erreur', 'Impossible de sonder les applications', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Rafraichir';
    }
}

// ─── Rendu ────────────────────────────────────────────────────────────

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
    var statusStyle = {
        online:   { bg: '#16a34a', text: 'Online', cardBg: '#fff', border: '#e2e8f0' },
        offline:  { bg: '#dc2626', text: 'Offline', cardBg: '#fef2f2', border: '#fecaca' },
        degraded: { bg: '#f59e0b', text: 'Degraded', cardBg: '#fffbeb', border: '#fcd34d' },
    }[app.status] || { bg: '#9ca3af', text: app.status, cardBg: '#fff', border: '#e2e8f0' };

    var icon = escapeHtml(app.icon || 'bi-app');
    var color = escapeHtml(app.color || '#6c757d');
    var name = escapeHtml(app.name);
    var slug = escapeHtml(app.slug);
    var latency = app.latency_ms ? app.latency_ms + 'ms' : '';
    var version = app.version || '';
    var uptime = formatUptime(app.uptime_seconds);
    var error = app.error
        ? '<div style="font-size:11px;color:#dc2626;margin-top:4px;">' + escapeHtml(app.error.substring(0, 100)) + '</div>'
        : '';

    // Ligne metriques : latence, version, uptime
    var metrics = [];
    if (latency) metrics.push('📡 ' + latency);
    if (version) metrics.push('🏷 ' + escapeHtml(version));
    if (uptime) metrics.push('⏱ ' + uptime);
    var metricsRow = metrics.length
        ? '<div style="display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:#64748b;border-top:1px solid #f1f5f9;padding-top:10px;">' +
          metrics.map(function (m) { return '<span>' + m + '</span>'; }).join('') + '</div>'
        : '';

    // Ligne integration : statut connexion Hub + services decouverts
    var integrationBadges = [];
    if (app.hub_connected === true) {
        integrationBadges.push('<span style="background:#dcfce7;color:#16a34a;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:500;">✓ Hub connecté</span>');
    } else if (app.hub_connected === false) {
        integrationBadges.push('<span style="background:#fee2e2;color:#dc2626;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:500;">✗ Hub déconnecté</span>');
    }
    if (app.services_count != null) {
        integrationBadges.push('<span style="background:#eff6ff;color:#2563eb;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:500;">🔌 ' + app.services_count + ' service' + (app.services_count > 1 ? 's' : '') + '</span>');
    }
    var integrationRow = integrationBadges.length
        ? '<div style="display:flex;gap:6px;flex-wrap:wrap;">' + integrationBadges.join('') + '</div>'
        : '';

    // L'app est joignable mais sans aucune donnee sante (ni version, ni uptime,
    // ni integration Hub, ni erreur) → vraiment aucun endpoint /health exploitable.
    var noHealth = (app.status === 'online' && !version && !uptime && !integrationBadges.length && !error);
    var healthNote = noHealth
        ? '<div style="font-size:11px;color:#94a3b8;margin-top:4px;"><i class="bi bi-info-circle me-1"></i>Serveur joignable — pas d\'endpoint /health standard</div>'
        : '';

    var degradedInfo = '';
    if (app.status === 'degraded' && app.error) {
        degradedInfo = ' <i class="bi bi-info-circle" title="' + escapeHtml(app.error) + '" style="cursor:help;font-size:11px;"></i>';
    }

    return (
        '<div class="col-12 col-md-6 col-lg-4">' +
        '<div style="background:' + statusStyle.cardBg + ';border:1px solid ' + statusStyle.border + ';border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:10px;height:100%;">' +
            '<div style="display:flex;align-items:center;gap:10px;">' +
                '<div style="width:40px;height:40px;border-radius:8px;background:' + color + ';display:flex;align-items:center;justify-content:center;color:white;font-size:18px;flex-shrink:0;">' +
                    '<i class="bi ' + icon + '"></i>' +
                '</div>' +
                '<div style="flex:1;min-width:0;">' +
                    '<div style="font-weight:600;font-size:14px;color:#1e293b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + name + '</div>' +
                    '<div style="font-size:12px;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + slug + '</div>' +
                '</div>' +
                '<span style="background:' + statusStyle.bg + ';color:white;font-size:11px;padding:3px 8px;border-radius:12px;font-weight:500;flex-shrink:0;">' + statusStyle.text + degradedInfo + '</span>' +
            '</div>' +
            metricsRow +
            integrationRow +
            '<div id="update-badge-' + slug + '" style="display:none;"></div>' +
            healthNote +
            error +
            '<div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid ' + statusStyle.border + ';padding-top:8px;margin-top:auto;">' +
                '<span style="font-size:11px;color:#94a3b8;">' +
                    'Verifie il y a <span class="apps-health-timestamp" data-ts="' + (app.checked_at || '') + '">…</span>' +
                '</span>' +
                '<div style="display:flex;gap:4px;">' +
                    '<button onclick="startApp(\'' + slug + '\')" class="btn btn-sm btn-outline-success py-0 px-2" style="font-size:11px;" title="Demarrer">▶</button>' +
                    '<button onclick="stopApp(\'' + slug + '\')" class="btn btn-sm btn-outline-danger py-0 px-2" style="font-size:11px;" title="Arreter">■</button>' +
                    '<button onclick="restartApp(\'' + slug + '\')" class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:11px;" id="restartBtn_' + slug + '" title="Redemarrer">⟳</button>' +
                    '<button onclick="showLogs(\'' + slug + '\')" class="btn btn-sm btn-outline-secondary py-0 px-2" style="font-size:11px;" title="Logs">📋</button>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '</div>'
    );
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
        '<button onclick="showChangelog(\'' + slug + '\', \'' + escapeHtml(update.current_version || '') + '\')" ' +
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
            '<select onchange="_changelogSelectVersion(this.value)" class="form-select form-select-sm" style="width:auto;">' +
                options +
            '</select>' +
            (release.html_url ? '<a href="' + escapeHtml(release.html_url) + '" target="_blank" style="font-size:12px;color:#6366f1;text-decoration:none;">GitHub ↗</a>' : '') +
            '<div style="margin-left:auto;">' +
                '<button onclick="_changelogTab(\'notes\')" class="btn btn-sm ' + notesBtn + '">📖 Notes</button> ' +
                '<button onclick="_changelogTab(\'code\')" class="btn btn-sm ' + codeBtn + '"' +
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
            ? '<button onclick="_toggleDiff(\'diff-' + i + '\', this)" class="btn btn-sm btn-outline-secondary py-0 px-1" style="font-size:11px;">▸</button>'
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

function _changelogSelectVersion(index) {
    _changelogState.selectedIndex = parseInt(index, 10);
    _changelogState.tab = 'notes';
    var modal = document.getElementById('appsHealthModal');
    if (modal) _renderChangelogBody(modal);
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
        '<button type="button" class="btn-close" onclick="document.getElementById(\'appsHealthModal\').remove()"></button>' +
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
