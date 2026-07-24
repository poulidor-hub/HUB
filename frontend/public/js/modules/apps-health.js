/**
 * Hub Application — Apps Health Dashboard
 *
 * Remplace l'ancien module discovery.js.
 * Fonctions globales pour compatibilite avec les handlers HTML inline.
 */

// ─── Chargement principal ─────────────────────────────────────────────

async function loadAppsHealth() {
    const btn = document.getElementById('appsHealthRefreshBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>…';

    try {
        const result = await api.getAppsHealth();
        renderAppsHealth(result);
    } catch (error) {
        console.error('Erreur chargement health:', error);
        showToast('Erreur', 'Impossible de sonder les applications', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Rafraichir';
    }
}

// ─── Rendu ────────────────────────────────────────────────────────────

function renderAppsHealth(data) {
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
        '<button onclick="showChangelog(\'' + slug + '\')" ' +
        'style="background:#fef3c7;color:#92400e;border:1px solid #fcd34d;font-size:11px;padding:3px 10px;border-radius:6px;cursor:pointer;display:inline-block;width:100%;text-align:left;">' +
        '🔄 Mise à jour disponible : <strong>' + escapeHtml(update.current_version) + '</strong> → <strong>' + escapeHtml(update.latest_version) + '</strong>' +
        ' <span style="float:right;color:#b45309;">📋 Voir le changelog</span>' +
        '</button>';
}

async function showChangelog(slug) {
    var modal = _showModal('Changelog — ' + slug,
        '<div class="text-center py-4"><div class="spinner-border spinner-border-sm"></div> Chargement du changelog…</div>');
    try {
        var result = await api.getChangelog(slug);
        if (!result.ok || !result.markdown) {
            modal.querySelector('.modal-body').innerHTML =
                '<div class="alert alert-warning">Aucun changelog disponible pour ' + escapeHtml(slug) + ' (repo non configure ou inaccessible).</div>';
            return;
        }
        modal.querySelector('.modal-body').innerHTML =
            '<pre style="font-size:12px;line-height:1.5;max-height:60vh;overflow:auto;background:#f8fafc;color:#334155;padding:16px;border-radius:6px;white-space:pre-wrap;font-family:system-ui,-apple-system,sans-serif;">' +
            escapeHtml(result.markdown) +
            '</pre>';
    } catch (error) {
        modal.querySelector('.modal-body').innerHTML =
            '<div class="alert alert-danger">Erreur: ' + escapeHtml(error.message) + '</div>';
    }
}

// ─── Modal utilitaire ─────────────────────────────────────────────────

function _showModal(title, bodyHtml) {
    // Supprime un eventuel modal precedent
    var existing = document.getElementById('appsHealthModal');
    if (existing) existing.remove();

    var html =
        '<div class="modal fade" id="appsHealthModal" tabindex="-1" style="display:block;background:rgba(0,0,0,0.5);">' +
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
