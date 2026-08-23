/**
 * Hub Application - page audit dédiée
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Page Audit Dédiée =====

let pageAuditLogsPage = 1;
let pageAuditCurrentAppSlug = null;
let pageAuditAppLogsPage = 1;
let allUsersPage = [];

let uptimeSelectedUserId = null;
let uptimeSelectedDate = new Date().toISOString().split('T')[0]; // UTC
let uptimeUsersLoaded = false;

function uptimeFmtDuration(seconds) {
    const h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
    return h > 0 ? `${h} h ${String(m).padStart(2, '0')}` : `${m} min`;
}

function setAuditPeriod(days) {
    days = parseInt(days);
    const hidden = document.getElementById('auditPeriodPage');
    if (hidden) hidden.value = days;
    document.querySelectorAll('#auditPageSeg button').forEach(b => {
        b.classList.toggle('active', parseInt(b.dataset.days) === days);
    });
    loadAuditPageData();
}

function openAuditTab(tab) {
    const map = { overview: '#auditPageOverview', apps: '#auditPageApps', logs: '#auditPageLogs', sessions: '#auditPageSessions', errors: '#auditPageErrors' };
    const btn = document.querySelector(`#auditPageTabs [data-bs-target="${map[tab]}"]`);
    if (btn) btn.click();
}

async function loadAuditPageData() {
    // Toujours recharger la vue d'ensemble (les KPIs et les alertes en haut)
    await loadAuditPageOverview();

    // Recharger aussi l'onglet actuellement visible
    const activeTab = document.querySelector('#auditPageTabs .nav-link.active');
    if (!activeTab) return;

    const target = activeTab.getAttribute('data-bs-target');
    const loaders = {
        '#auditPageOverview': loadAuditPageOverview,
        '#auditPageUptime': loadAuditPageUptime,
        '#auditPageApps': loadAuditPageApps,
        '#auditPageLogs': loadAuditPageLogsTab,
        '#auditPageSessions': loadAuditPageSessions,
        '#auditPageErrors': loadAuditPageErrors
    };

    const loader = loaders[target];
    if (loader && loader !== loadAuditPageOverview) {
        await loader();
    }
}

async function loadAuditPageOverview() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 7);
    try {
        const [kpis, alerts, active, heatmap] = await Promise.all([
            api.getAuditKpis(days),
            api.getAuditAlerts(days),
            api.getActiveSessions(),
            api.getUsageHeatmap(days).catch(() => null) // non bloquant
        ]);
        renderAuditKpis(kpis);
        renderAuditAlerts(alerts.alerts || []);
        renderAuditLiveSessions(active);
        if (heatmap && (heatmap.days || []).length) {
            renderUsageHeatmap('auditHeatmapChart', heatmap);
        }
        updateErrorTabBadge();
    } catch (error) {
        console.error('Erreur chargement audit:', error);
        showToast('Erreur', "Impossible de charger les données d'audit", 'danger');
    }
}

function auditTrend(cur, prev, goodDirection) {
    const d = cur - prev;
    if (d === 0) return '<span class="audit-trend flat">0</span>';
    const sign = d > 0 ? '+' : '';
    const improving = goodDirection === 'down' ? d < 0 : d > 0;
    return `<span class="audit-trend ${improving ? 'down' : 'up'}">${sign}${d}</span>`;
}

function auditKpiCard(tile, color, icon, value, label, trendHtml) {
    return `
        <div class="col-6 col-lg-3">
            <div class="card h-100"><div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-3">
                    <div class="audit-icon-tile" style="background:${tile};color:${color};"><i class="bi ${icon}"></i></div>
                    ${trendHtml}
                </div>
                <div class="audit-kpi-value">${value}</div>
                <div class="small text-muted mt-1">${label}</div>
            </div></div>
        </div>`;
}

function renderAuditKpis(k) {
    const live = '<span class="audit-trend flat"><span class="audit-pulse"></span></span>';
    document.getElementById('auditKpiRow').innerHTML =
        auditKpiCard('#e7f1ff', '#0d6efd', 'bi-people', k.active_now, 'Sessions actives', live) +
        auditKpiCard('#fde8e8', '#dc3545', 'bi-exclamation-triangle', k.errors_critical.current, 'Erreurs critiques',
            auditTrend(k.errors_critical.current, k.errors_critical.previous, 'down')) +
        auditKpiCard('#e6f4ea', '#28a745', 'bi-hdd-stack', k.apps_active.current, 'Apps actives',
            auditTrend(k.apps_active.current, k.apps_active.previous, 'up')) +
        auditKpiCard('#fff1e6', '#fd7e14', 'bi-shield-exclamation', k.auth_failures.current, "Échecs d'auth.",
            auditTrend(k.auth_failures.current, k.auth_failures.previous, 'down'));
}

function renderAuditAlerts(alerts) {
    const c = document.getElementById('auditAlertsList');
    if (!alerts.length) {
        c.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-check-circle text-success me-1"></i>Aucune alerte à traiter</div>';
        return;
    }
    c.innerHTML = alerts.map(a => {
        const isErr = a.type === 'http_error';
        const tile = isErr ? '#fde8e8' : '#fff1e6';
        const col = isErr ? '#dc3545' : '#fd7e14';
        const icon = isErr ? 'bi-exclamation-triangle' : 'bi-shield-exclamation';
        const title = isErr
            ? `Erreur ${a.request_path}`
            : `Multiples échecs de connexion (IP : ${a.ip_address})`;
        const src = a.source_app_slug ? `<span class="badge text-bg-light border">${escapeHtml(a.source_app_slug)}</span>` : '';
        return `
        <div class="audit-alert-row">
            <div class="audit-alert-ic" style="background:${tile};color:${col};"><i class="bi ${icon}"></i></div>
            <div class="flex-grow-1">
                <div class="fw-semibold small">${escapeHtml(title)}</div>
                <div class="d-flex align-items-center gap-2 mt-2" style="font-size:.75rem;">
                    ${src}<span class="text-danger fw-semibold">${a.occurrences} occurrences</span>
                    <span class="text-muted">${formatDateTime(a.last_seen)}</span>
                </div>
            </div>
            <button class="btn btn-sm btn-outline-secondary" onclick='auditAlertDetails(${JSON.stringify(a.deeplink)})'>Détails</button>
        </div>`;
    }).join('');
}

function auditAlertDetails(deeplink) {
    if (deeplink.tab === 'errors') {
        const el = document.getElementById('errorsFilterPath');
        if (el) el.value = deeplink.filters.path || '';
        openAuditTab('errors');
        loadAuditPageErrors();
    } else {
        const el = document.getElementById('pageLogsFilterIp');
        if (el) el.value = deeplink.filters.ip || '';
        openAuditTab('logs');
        pageAuditLogsPage = 1;
        loadAuditPageLogsTab();
    }
}

function renderAuditLiveSessions(data) {
    document.getElementById('auditLiveCount').textContent = data.active_count || 0;
    const c = document.getElementById('auditLiveSessions');
    if (!data.sessions || !data.sessions.length) {
        c.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-person-x me-1"></i>Aucun utilisateur connecté</div>';
        return;
    }
    c.innerHTML = data.sessions.map(s => `
        <div class="p-3 border-bottom">
            <div class="d-flex justify-content-between">
                <span class="fw-semibold small">${escapeHtml(s.username || 'Unknown')}</span>
                <small class="text-muted"><i class="bi bi-clock me-1"></i>${s.duration_minutes} min</small>
            </div>
            <div class="d-flex justify-content-between align-items-end mt-2">
                <div class="d-flex flex-column gap-1">
                    <span class="badge text-bg-light border">${escapeHtml(s.current_app_name || 'Hub')}</span>
                    <code class="small text-muted">${s.ip_address || '-'}</code>
                </div>
                <a href="#" class="small text-danger fw-semibold text-decoration-none" data-action="auditForceLogout" data-arg1="${s.id}">Forcer déco.</a>
            </div>
        </div>`).join('');
}

async function auditForceLogout(sessionId) {
    if (!confirm('Forcer la déconnexion de cette session ?')) return;
    try {
        await api.revokeSession(sessionId);
        showToast('Succès', 'Session révoquée', 'success');
        loadAuditPageOverview();
        if (document.getElementById('auditPageSessions')?.classList.contains('active')) {
            loadAuditPageSessions();
        }
    } catch (e) {
        showToast('Erreur', e.message || 'Révocation impossible', 'danger');
    }
}

function auditQuickSearch() {
    const q = document.getElementById('auditQuickSearchInput')?.value?.trim();
    if (!q) { openAuditTab('logs'); return; }
    const looksIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(q);
    if (looksIp) {
        const el = document.getElementById('pageLogsFilterIp'); if (el) el.value = q;
    } else {
        const el = document.getElementById('pageLogsFilterUser'); if (el) el.value = q;
    }
    openAuditTab('logs');
    pageAuditLogsPage = 1;
    loadAuditPageLogsTab();
}

function auditChip(kind, value) {
    if (kind === 'status') {
        const el = document.getElementById('errorsFilterCode'); if (el) el.value = value;
        openAuditTab('errors'); loadAuditPageErrors();
    } else if (kind === 'action') {
        const el = document.getElementById('pageLogsFilterAction'); if (el) el.value = value;
        openAuditTab('logs'); pageAuditLogsPage = 1; loadAuditPageLogsTab();
    } else if (kind === 'app') {
        const el = document.getElementById('pageLogsFilterApp'); if (el) el.value = value;
        openAuditTab('logs'); pageAuditLogsPage = 1; loadAuditPageLogsTab();
    }
}

function renderPageActiveSessions(data) {
    const container = document.getElementById('pageActiveSessionsTable');
    const countBadge = document.getElementById('pageActiveSessionCount');
    
    countBadge.textContent = data.active_count || 0;
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-person-x display-6"></i>
                <p class="mt-2">Aucun utilisateur connecté</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Connecté depuis</th>
                    <th>Durée</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td>
                            <span class="badge bg-secondary me-2"><i class="bi bi-circle-fill text-success" style="font-size: 0.5rem;"></i></span>
                            <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                        </td>
                        <td><small>${formatDateTime(s.login_at)}</small></td>
                        <td><span class="badge bg-light text-dark">${s.duration_minutes} min</span></td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageTopUsers(topUsers) {
    const container = document.getElementById('pageTopUsersTable');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-trophy display-6"></i>
                <p class="mt-2">Aucune donnée disponible</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Utilisateur</th>
                    <th>Sessions</th>
                    <th>Temps total</th>
                </tr>
            </thead>
            <tbody>
                ${topUsers.map((u, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td><span class="badge bg-secondary">${u.sessions}</span></td>
                        <td>${u.total_time_display}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageTopApps(apps) {
    const container = document.getElementById('pageTopAppsTable');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-grid display-6"></i>
                <p class="mt-2">Aucun accès enregistré</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Application</th>
                    <th>Accès</th>
                    <th>Utilisateurs uniques</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${apps.map((app, index) => `
                    <tr>
                        <td>
                            ${index < 3 
                                ? `<span class="badge ${index === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td>
                            <span class="me-2 rounded px-2 py-1" style="background-color: ${app.color}; color: white;">
                                <i class="${app.icon}"></i>
                            </span>
                            <strong>${escapeHtml(app.name)}</strong>
                            <small class="text-muted ms-1">(${app.slug})</small>
                        </td>
                        <td><span class="badge bg-secondary">${app.access_count}</span></td>
                        <td><span class="badge bg-light text-dark">${app.unique_users}</span></td>
                        <td>
                            <button class="btn btn-sm btn-outline-secondary" data-action="showPageAppAuditDetail" data-arg1="${app.slug}">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

let appGridSortKey = 'name';
let appGridSortDir = 'asc';
let _lastConnectedBySlug = {};
let _lastBackupBySlug = {};

async function loadAuditPageApps() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const [summary, active, backups] = await Promise.all([
            api.getAppAuditSummary(days),
            api.getActiveSessions(),
            api.getAllBackups().catch(() => ({ apps: [] }))
        ]);
        
        const connectedBySlug = {};
        if (active.sessions) {
            active.sessions.forEach(s => {
                const slug = s.current_app_slug;
                if (slug) connectedBySlug[slug] = (connectedBySlug[slug] || 0) + 1;
            });
        }
        _lastConnectedBySlug = connectedBySlug;
        
        const backupBySlug = {};
        if (backups.apps) {
            backups.apps.forEach(b => {
                backupBySlug[b.app_slug] = {
                    size: b.total_size_human || '0 B',
                    count: b.backups_count || 0,
                    lastAt: b.last_backup_at || null
                };
            });
        }
        _lastBackupBySlug = backupBySlug;
        
        const enriched = (summary.apps || []).map(app => ({
            ...app,
            connected: connectedBySlug[app.slug] || 0,
            backupSize: backupBySlug[app.slug]?.size || '-',
            backupCount: backupBySlug[app.slug]?.count || 0,
            lastBackup: backupBySlug[app.slug]?.lastAt || null
        }));
        
        renderPageAuditAppsGrid(enriched);
    } catch (error) {
        console.error('Erreur chargement apps audit:', error);
        showToast('Erreur', 'Impossible de charger les données', 'danger');
    }
}

function sortApps(apps, key, dir) {
    if (!dir) return apps;
    const sorted = [...apps].sort((a, b) => {
        let va, vb;
        switch (key) {
            case 'name': va = (a.name || '').toLowerCase(); vb = (b.name || '').toLowerCase(); break;
            case 'access': va = a.access_count || 0; vb = b.access_count || 0; break;
            case 'connected': va = a.connected || 0; vb = b.connected || 0; break;
            case 'users': va = a.unique_users || 0; vb = b.unique_users || 0; break;
            case 'backup': va = a.backupCount || 0; vb = b.backupCount || 0; break;
            default: return 0;
        }
        if (typeof va === 'string') return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return dir === 'asc' ? va - vb : vb - va;
    });
    return sorted;
}

function renderAppGridSortBar() {
    const keys = [
        { key: 'name', label: 'Nom', icon: 'bi-sort-alpha-down' },
        { key: 'access', label: 'Accès', icon: 'bi-graph-up' },
        { key: 'connected', label: 'Connectés', icon: 'bi-broadcast' },
        { key: 'users', label: 'Users', icon: 'bi-people' },
        { key: 'backup', label: 'Taille', icon: 'bi-hdd' }
    ];
    return `
        <div class="d-flex align-items-center gap-1 mb-3 flex-wrap">
            <small class="text-muted me-2"><i class="bi bi-arrow-down-up me-1"></i>Trier :</small>
            ${keys.map(k => {
                const active = appGridSortKey === k.key;
                const dirIcon = active ? (appGridSortDir === 'asc' ? 'bi-sort-up' : 'bi-sort-down') : k.icon;
                const cls = active ? 'btn-primary' : 'btn-outline-secondary';
                return `<button class="btn btn-sm ${cls} app-sort-btn" data-action="setAppGridSort" data-arg1="${k.key}">
                    <i class="bi ${dirIcon} me-1"></i>${k.label}
                </button>`;
            }).join('')}
        </div>`;
}

function setAppGridSort(key) {
    if (appGridSortKey === key) {
        appGridSortDir = appGridSortDir === 'asc' ? 'desc' : (appGridSortDir === 'desc' ? '' : 'asc');
    } else {
        appGridSortKey = key;
        appGridSortDir = 'asc';
    }
    loadAuditPageApps();
}

function renderPageAuditAppsGrid(apps) {
    const container = document.getElementById('auditPageAppsGrid');
    document.getElementById('auditPageAppDetail').classList.add('d-none');
    container.classList.remove('d-none');
    
    const kpiRow = document.getElementById('auditAppsKpiRow');
    
    if (!apps || apps.length === 0) {
        if (kpiRow) kpiRow.innerHTML = '';
        container.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="bi bi-grid display-1"></i>
                <h4 class="mt-3">Aucun accès enregistré</h4>
                <p>Les accès aux applications apparaîtront ici</p>
            </div>`;
        return;
    }
    
    if (kpiRow) {
        const totalAccess = apps.reduce((s, a) => s + (a.access_count || 0), 0);
        const totalConnected = apps.reduce((s, a) => s + (a.connected || 0), 0);
        const totalSize = apps.reduce((s, a) => s + (a.backupCount > 0 ? 1 : 0), 0);
        kpiRow.innerHTML =
            auditKpiCard('#e7f1ff', '#0d6efd', 'bi-grid', apps.length, 'Applications', '<span class="audit-trend flat">suivies</span>') +
            auditKpiCard('#e6f4ea', '#28a745', 'bi-broadcast', totalConnected, 'Connectés now', '<span class="audit-trend flat"><span class="audit-pulse"></span></span>') +
            auditKpiCard('#fff1e6', '#fd7e14', 'bi-hdd-stack', totalSize, 'Avec backup', '<span class="audit-trend flat">actif</span>') +
            auditKpiCard('#f1f3f5', '#6c757d', 'bi-calendar-range', '', 'Période', '<span class="audit-trend flat">' + (parseInt(document.getElementById('auditPeriodPage')?.value) || 30) + 'j</span>');
    }
    
    const sorted = sortApps(apps, appGridSortKey, appGridSortDir);
    
    container.innerHTML = renderAppGridSortBar() + sorted.map(app => {
        const connBadge = app.connected > 0
            ? `<span class="badge bg-success ms-1" title="${app.connected} connecté(s)"><span class="audit-pulse me-1" style="vertical-align:middle;"></span>${app.connected}</span>`
            : '';
        const backupInfo = app.backupSize !== '-' && app.backupSize !== '0 B'
            ? `<small class="text-muted" title="${app.backupCount} backup(s)">${app.backupSize}</small>`
            : '<small class="text-muted">-</small>';
        const backupColor = app.backupSize !== '-' && app.backupSize !== '0 B' ? '' : 'text-muted';
        return `
        <div class="col-sm-6 col-md-4 col-lg-3 mb-3">
            <div class="card h-100 cursor-pointer" data-action="showPageAppAuditDetail" data-arg1="${app.slug}" style="cursor:pointer;transition:transform .15s,box-shadow .15s;">
                <div class="card-body text-center p-3">
                    <div class="mb-2">
                        <span class="rounded-circle d-inline-flex align-items-center justify-content-center"
                              style="width:48px;height:48px;background-color:${app.color};color:white;font-size:1.3rem;">
                            <i class="${app.icon}"></i>
                        </span>
                    </div>
                    <h6 class="card-title mb-1 fw-semibold small">${escapeHtml(app.name)}${connBadge}</h6>
                    <p class="card-text small text-muted mb-2" style="font-size:.7rem;">${app.slug}</p>
                    <div class="row g-1 text-center" style="font-size:.75rem;">
                        <div class="col-6">
                            <div class="fw-bold" style="font-size:.95rem;">${app.access_count}</div>
                            <small class="text-muted">Accès</small>
                        </div>
                        <div class="col-6">
                            <div class="fw-bold" style="font-size:.95rem;color:${app.connected > 0 ? '#28a745' : '#6c757d'}">${app.connected}</div>
                            <small class="text-muted">Actifs</small>
                        </div>
                        <div class="col-6 mt-2">
                            <div class="fw-bold" style="font-size:.95rem;">${app.unique_users}</div>
                            <small class="text-muted">Users</small>
                        </div>
                        <div class="col-6 mt-2 ${backupColor}">
                            <div class="fw-bold" style="font-size:.95rem;">${app.backupSize}</div>
                            <small class="text-muted">Backup</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
}

async function showPageAppAuditDetail(appSlug) {
    pageAuditCurrentAppSlug = appSlug;
    pageAuditAppLogsPage = 1;
    
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    // Basculer vers l'onglet "Par Application" si pas déjà dessus
    const appsTab = document.querySelector('[data-bs-target="#auditPageApps"]');
    if (appsTab && !appsTab.classList.contains('active')) {
        appsTab.click();
    }
    
    try {
        const [data, usageStats] = await Promise.all([
            api.getAppAuditDetail(appSlug, { days, page: 1, pageSize: 50 }),
            api.getAppsUsageStats(days, appSlug).catch(() => null) // non bloquant
        ]);
        
        // Masquer la grille, afficher le détail
        document.getElementById('auditPageAppsGrid').classList.add('d-none');
        document.getElementById('auditPageAppDetail').classList.remove('d-none');
        
        // Remplir les infos de l'app
        document.getElementById('pageAppDetailIcon').innerHTML = `<i class="${data.app.icon}" style="color: ${data.app.color};"></i>`;
        document.getElementById('pageAppDetailIcon').style.backgroundColor = `${data.app.color}20`;
        document.getElementById('pageAppDetailName').textContent = data.app.name;
        document.getElementById('pageAppDetailSlug').textContent = data.app.slug;
        
        // Stats
        const connected = _lastConnectedBySlug[appSlug] || 0;
        const backup = _lastBackupBySlug[appSlug];
        document.getElementById('pageAppDetailAccess').textContent = data.stats.total_access;
        document.getElementById('pageAppDetailUsers').textContent = data.stats.unique_users;
        document.getElementById('pageAppDetailConnected').textContent = connected;
        document.getElementById('pageAppDetailBackup').textContent = backup?.size || '-';
        document.getElementById('pageAppDetailPeriod').textContent = `${days} jours`;

        // Graphiques d'usage + records
        const usageApp = usageStats && (usageStats.apps || []).find(a => a.slug === appSlug);
        if (usageApp) {
            renderAppUsageChart('pageAppUsageChart', usageApp);
            const r = usageApp.records || {};
            const recFmtDate = (iso) => iso ? formatDateTime(iso) : '-';
            document.getElementById('pageAppRecordsRow').innerHTML =
                auditKpiCard('#e7f1ff', '#0d6efd', 'bi-trophy', r.max ?? 0,
                    `Pic simultanés — ${recFmtDate(r.max_at)}`,
                    `<span class="audit-trend flat">${escapeHtml((r.max_users || []).join(', ') || '-')}</span>`) +
                auditKpiCard('#e6f4ea', '#28a745', 'bi-graph-up', r.avg ?? 0, 'Moyenne simultanés', '') +
                auditKpiCard('#fff1e6', '#fd7e14', 'bi-percent', r.p95 ?? 0, 'P95 simultanés', '') +
                auditKpiCard('#f1f3f5', '#6c757d', 'bi-arrows-collapse', `${r.min ?? 0} / σ ${r.stddev ?? 0}`, 'Min / Écart-type', '');
        } else {
            document.getElementById('pageAppRecordsRow').innerHTML = '';
            usageChartDispose('pageAppUsageChart');
            document.getElementById('pageAppUsageChart').innerHTML =
                `<div class="usage-empty"><i class="bi bi-activity"></i>${USAGE_EMPTY_MSG}</div>`;
        }

        // Top users
        renderPageAppDetailTopUsers(data.top_users);
        
        // Logs
        renderPageAppDetailLogs(data);
        
    } catch (error) {
        console.error('Erreur détail app:', error);
        showToast('Erreur', 'Impossible de charger les détails', 'danger');
    }
}

function hidePageAppDetail() {
    document.getElementById('auditPageAppDetail').classList.add('d-none');
    document.getElementById('auditPageAppsGrid').classList.remove('d-none');
    pageAuditCurrentAppSlug = null;
}

function renderPageAppDetailTopUsers(topUsers) {
    const container = document.getElementById('pageAppDetailTopUsers');
    
    if (!topUsers || topUsers.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-3">Aucune donnée</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm mb-0">
            <tbody>
                ${topUsers.map((u, i) => `
                    <tr>
                        <td class="text-center" style="width: 30px;">
                            ${i < 3 
                                ? `<span class="badge ${i === 0 ? 'bg-warning text-dark' : 'bg-secondary'}">${i + 1}</span>`
                                : `<span class="text-muted">${i + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td class="text-end"><span class="badge bg-secondary">${u.access_count}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPageAppDetailLogs(data) {
    const container = document.getElementById('pageAppDetailLogs');
    const pagination = document.getElementById('pageAppDetailPagination');
    
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'loadPageAppDetailPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun accès enregistré</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function loadPageAppDetailPage(page) {
    if (!pageAuditCurrentAppSlug) return;
    pageAuditAppLogsPage = page;
    
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(pageAuditCurrentAppSlug, { days, page, pageSize: 50 });
        renderPageAppDetailLogs(data);
    } catch (error) {
        console.error('Erreur pagination:', error);
    }
}

async function loadAuditPageLogsTab() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    const action = document.getElementById('pageLogsFilterAction')?.value || null;
    const target = document.getElementById('pageLogsFilterApp')?.value || null;
    const username = document.getElementById('pageLogsFilterUser')?.value?.trim() || null;
    const ip = document.getElementById('pageLogsFilterIp')?.value?.trim() || null;
    
    try {
        await loadPageLogsFilters();
        
        const data = await api.getAuditLogs({
            days,
            action: action || null,
            target: target || null,
            username: username || null,
            ip: ip || null,
            page: pageAuditLogsPage,
            pageSize: 50
        });
        
        renderPageAuditLogs(data);
    } catch (error) {
        console.error('Erreur chargement logs:', error);
        showToast('Erreur', 'Impossible de charger les logs', 'danger');
    }
}

async function loadPageLogsFilters() {
    const actionSelect = document.getElementById('pageLogsFilterAction');
    const appSelect = document.getElementById('pageLogsFilterApp');
    
    if (actionSelect.options.length <= 1) {
        try {
            const actions = await api.getAvailableActions();
            actions.actions.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.action;
                opt.textContent = `${getActionLabel(a.action)} (${a.count})`;
                actionSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les actions:', e);
        }
    }
    
    if (appSelect.options.length <= 1) {
        try {
            const apps = allApps.length > 0 ? allApps : await api.getAllApps();
            apps.forEach(app => {
                const opt = document.createElement('option');
                opt.value = app.slug;
                opt.textContent = app.name;
                appSelect.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger les apps:', e);
        }
    }

    // Peupler la datalist des usernames pour l'autocomplete
    const usersDatalist = document.getElementById('auditUsersDatalist');
    if (usersDatalist && usersDatalist.options.length === 0) {
        try {
            const usersResp = await api.getUsers({ page: 0 });
            const usersList = Array.isArray(usersResp) ? usersResp : (usersResp.users || usersResp.items || []);
            usersList.forEach(u => {
                const opt = document.createElement('option');
                opt.value = u.username;
                usersDatalist.appendChild(opt);
            });
        } catch (e) {
            console.warn('Impossible de charger la liste des utilisateurs pour autocomplete:', e);
        }
    }
}

function resetPageLogsFilters() {
    document.getElementById('pageLogsFilterAction').value = '';
    document.getElementById('pageLogsFilterApp').value = '';
    document.getElementById('pageLogsFilterUser').value = '';
    const ipEl = document.getElementById('pageLogsFilterIp');
    if (ipEl) ipEl.value = '';
    pageAuditLogsPage = 1;
    loadAuditPageLogsTab();
}

function renderPageAuditLogs(data) {
    const container = document.getElementById('pageAuditLogsTable');
    const countSpan = document.getElementById('pageLogsCount');
    const pagination = document.getElementById('pageLogsPagination');
    
    countSpan.textContent = `${data.total_count} résultats`;
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'changePageLogsPage');
    
    // KPI mini cards
    const kpiRow = document.getElementById('auditLogsKpiRow');
    if (kpiRow) {
        const uniqueUsers = data.logs ? new Set(data.logs.map(l => l.username).filter(Boolean)).size : 0;
        const uniqueIps = data.logs ? new Set(data.logs.map(l => l.ip_address).filter(Boolean)).size : 0;
        kpiRow.innerHTML =
            auditKpiCard('#e7f1ff', '#0d6efd', 'bi-list-ul', data.total_count, 'Logs trouvés', `<span class="audit-trend flat">page ${data.page || 1}</span>`) +
            auditKpiCard('#e6f4ea', '#198754', 'bi-people', uniqueUsers, 'Utilisateurs', '<span class="audit-trend flat">uniques</span>') +
            auditKpiCard('#f1f3f5', '#6c757d', 'bi-hdd-network', uniqueIps, 'IPs uniques', '<span class="audit-trend flat">adresses</span>') +
            auditKpiCard('#f1f3f5', '#6c757d', 'bi-files', `${data.logs?.length || 0}`, 'Sur cette page', '<span class="audit-trend flat">' + (parseInt(document.getElementById('auditPeriodPage')?.value) || 7) + 'j</span>');
    }
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-list-ul display-4"></i>
                <p class="mt-2">Aucun log trouvé</p>
            </div>`;
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Utilisateur</th>
                    <th>Action</th>
                    <th>Cible</th>
                    <th>Détails</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.logs.map(l => `
                    <tr>
                        <td><small>${formatDateTime(l.created_at)}</small></td>
                        <td><strong>${escapeHtml(l.username)}</strong></td>
                        <td>${getActionBadge(l.action)}</td>
                        <td><code class="small">${l.target || '-'}</code></td>
                        <td><small class="text-muted">${escapeHtml(l.details || '-')}</small></td>
                        <td><code class="small">${l.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function changePageLogsPage(page) {
    pageAuditLogsPage = page;
    await loadAuditPageLogsTab();
}

let sessionSortKey = '';
let sessionSortDir = 'asc';

async function loadAuditPageSessions() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const history = await api.getSessionHistory(days);
        renderPageSessionHistory(history);
        document.getElementById('pageSessionsCount').textContent = `${history.total_sessions || 0} sessions`;
    } catch (error) {
        console.error('Erreur chargement sessions:', error);
        showToast('Erreur', 'Impossible de charger les sessions', 'danger');
    }
}

function sessionSortIcon(key) {
    if (sessionSortKey !== key) return '';
    return sessionSortDir === 'asc' ? ' <i class="bi bi-sort-up small"></i>' : ' <i class="bi bi-sort-down small"></i>';
}

function setSessionSort(key) {
    if (sessionSortKey === key) {
        sessionSortDir = sessionSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        sessionSortKey = key;
        sessionSortDir = 'asc';
    }
    loadAuditPageSessions();
}

function sortSessions(sessions, key, dir) {
    if (!sessions || !key || !dir) return sessions;
    return [...sessions].sort((a, b) => {
        let va, vb;
        switch (key) {
            case 'user': va = (a.username || '').toLowerCase(); vb = (b.username || '').toLowerCase(); break;
            case 'app': va = (a.current_app_name || 'Hub').toLowerCase(); vb = (b.current_app_name || 'Hub').toLowerCase(); break;
            case 'login': va = a.login_at || ''; vb = b.login_at || ''; break;
            case 'logout': va = a.logout_at || ''; vb = b.logout_at || ''; break;
            case 'duration': va = a.logout_at ? (a.duration_seconds || 0) : 999999999; vb = b.logout_at ? (b.duration_seconds || 0) : 999999999; break;
            case 'idle': va = a.idle_minutes || 0; vb = b.idle_minutes || 0; break;
            case 'ip': va = (a.ip_address || '').toLowerCase(); vb = (b.ip_address || '').toLowerCase(); break;
            case 'reason': va = (a.logout_reason || '').toLowerCase(); vb = (b.logout_reason || '').toLowerCase(); break;
            default: return 0;
        }
        if (typeof va === 'string') return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return dir === 'asc' ? va - vb : vb - va;
    });
}

function makeSortTh(key, label) {
    return `<th class="cursor-pointer" data-action="setSessionSort" data-arg1="${key}" style="cursor:pointer;user-select:none;">${label}${sessionSortIcon(key)}</th>`;
}

function renderPageSessionHistory(data) {
    const container = document.getElementById('pageSessionHistoryTable');
    
    const activeSessions = data.sessions?.filter(s => !s.logout_at) || [];
    const closedSessions = data.sessions?.filter(s => s.logout_at) || [];
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-clock-history display-4 mb-3"></i>
                <p class="mb-0">Aucune session dans cette période</p>
                <small class="text-muted">Les sessions apparaîtront ici une fois que des utilisateurs se connecteront</small>
            </div>`;
        document.getElementById('auditSessionKpiRow').innerHTML = '';
        return;
    }
    
    
    const avgSec = closedSessions.length > 0
        ? closedSessions.reduce((s, x) => s + (x.duration_seconds || 0), 0) / closedSessions.length
        : 0;
    const avgMin = Math.round(avgSec / 60);
    document.getElementById('auditSessionKpiRow').innerHTML =
        auditKpiCard('#e7f1ff', '#0d6efd', 'bi-people-fill', data.total_sessions, 'Total sessions', `<span class="audit-trend flat">${data.period_days}j</span>`) +
        auditKpiCard('#e6f4ea', '#28a745', 'bi-broadcast', activeSessions.length, 'Actives', '<span class="audit-trend flat"><span class="audit-pulse"></span></span>') +
        auditKpiCard('#f1f3f5', '#6c757d', 'bi-stop-circle', closedSessions.length, 'Terminées', '') +
        auditKpiCard('#fff1e6', '#fd7e14', 'bi-hourglass-split', `${avgMin} min`, 'Durée moy.', '<span class="audit-trend flat">ferm.</span>');
    
    const sortedActive = sortSessions(activeSessions, sessionSortKey, sessionSortDir);
    const sortedClosed = sortSessions(closedSessions, sessionSortKey, sessionSortDir);
    
    container.innerHTML = `
        <!-- Sessions actives -->
        ${sortedActive.length > 0 ? `
        <div class="mb-4 px-3 pt-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-success me-2">${sortedActive.length}</span>
                <h6 class="mb-0 text-success"><i class="bi bi-broadcast me-1"></i>Sessions actives</h6>
                ${sessionSortKey ? `<small class="text-muted ms-2"><i class="bi bi-arrow-down-up me-1"></i>Tri actif — re-clic pour inverser</small>` : ''}
            </div>
            <table class="table audit-tbl table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        ${makeSortTh('user', 'Utilisateur')}
                        ${makeSortTh('app', 'Application')}
                        ${makeSortTh('login', 'Connecté depuis')}
                        ${makeSortTh('duration', 'Durée')}
                        ${makeSortTh('idle', 'Activité')}
                        ${makeSortTh('ip', 'IP')}
                        <th class="text-end">Action</th>
                    </tr>
                </thead>
                <tbody>
                    ${sortedActive.map(s => `
                        <tr class="table-success table-active">
                            <td>
                                <div class="d-flex align-items-center">
                                    <span class="position-relative me-2">
                                        <i class="bi bi-person-circle fs-5 text-success"></i>
                                        <span class="position-absolute bottom-0 end-0 translate-middle-x">
                                            <i class="bi bi-circle-fill text-success" style="font-size: 0.5rem;"></i>
                                        </span>
                                    </span>
                                    <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                                </div>
                            </td>
                            <td>
                                ${s.current_app_name 
                                    ? `<span class="badge bg-primary bg-gradient"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                    : '<span class="badge bg-secondary bg-gradient"><i class="bi bi-house me-1"></i>Hub</span>'}
                            </td>
                            <td><small class="text-muted">${formatDateTime(s.login_at)}</small></td>
                            <td><span class="badge bg-light text-dark border"><i class="bi bi-clock me-1"></i>${s.duration_display || 'en cours'}</span></td>
                            <td>
                                ${s.idle_minutes !== undefined && s.idle_minutes > 5 
                                    ? `<span class="badge bg-warning text-dark" title="Inactif depuis ${s.idle_minutes} min"><i class="bi bi-hourglass-split me-1"></i>${s.idle_minutes}m</span>`
                                    : '<span class="badge bg-success"><i class="bi bi-activity"></i></span>'}
                            </td>
                            <td><code class="small bg-light px-2 py-1 rounded">${s.ip_address || '-'}</code></td>
                            <td class="text-end">
                                <a href="#" class="small text-danger fw-semibold text-decoration-none" data-action="auditForceLogout" data-arg1="${s.id}"><i class="bi bi-box-arrow-right me-1"></i>Forcer déco.</a>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
        ` : ''}
        
        <!-- Sessions terminées -->
        ${sortedClosed.length > 0 ? `
        <div class="px-3 pb-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-secondary me-2">${sortedClosed.length}</span>
                <h6 class="mb-0 text-muted"><i class="bi bi-clock-history me-1"></i>Sessions terminées</h6>
            </div>
            <table class="table audit-tbl table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        ${makeSortTh('user', 'Utilisateur')}
                        ${makeSortTh('app', 'Application')}
                        ${makeSortTh('login', 'Connexion')}
                        ${makeSortTh('logout', 'Déconnexion')}
                        ${makeSortTh('duration', 'Durée')}
                        ${makeSortTh('reason', 'Raison')}
                        ${makeSortTh('ip', 'IP')}
                    </tr>
                </thead>
                <tbody>
                    ${sortedClosed.map(s => `
                        <tr>
                            <td>
                                <div class="d-flex align-items-center">
                                    <i class="bi bi-person-circle fs-5 text-muted me-2"></i>
                                    <span class="text-muted">${escapeHtml(s.username || 'Unknown')}</span>
                                </div>
                            </td>
                            <td>
                                ${s.current_app_name 
                                    ? `<span class="badge bg-primary bg-opacity-50"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                    : '<span class="text-muted small"><i class="bi bi-house me-1"></i>Hub</span>'}
                            </td>
                            <td><small class="text-muted">${formatDateTime(s.login_at)}</small></td>
                            <td><small class="text-muted">${formatDateTime(s.logout_at)}</small></td>
                            <td>
                                <span class="badge bg-light text-dark border">
                                    ${s.duration_display || '-'}
                                </span>
                            </td>
                            <td>${getLogoutReasonBadge(s.logout_reason)}</td>
                            <td><code class="small text-muted">${s.ip_address || '-'}</code></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
        ` : ''}
    `;
}

// ===== Onglet Uptime utilisateurs =====

let uptimePollTimer = null;

async function loadUptimeUserOptions() {
    const select = document.getElementById('uptimeUserSelect');
    if (!select) return;
    if (!uptimeUsersLoaded) {
        try {
            const res = await api.getUsers({ page: 0 });  // page=0 = tous
            const users = res.users || res || [];
            select.innerHTML = users.map(u =>
                `<option value="${u.id}">${escapeHtml(u.username)}</option>`).join('');
            uptimeUsersLoaded = true;
        } catch (e) {
            console.error('Erreur chargement utilisateurs:', e);
            showToast('Erreur', 'Impossible de charger la liste des utilisateurs', 'danger');
            return;
        }
    }
    if (uptimeSelectedUserId) select.value = String(uptimeSelectedUserId);
    else uptimeSelectedUserId = parseInt(select.value);
}

// Convention handlers change (cf. changeUsersPerPage) : sans argument, lit le DOM
function changeUptimeUser() {
    const select = document.getElementById('uptimeUserSelect');
    if (!select || !select.value) return;
    uptimeSelectedUserId = parseInt(select.value);
    loadAuditPageUptime();
}

function shiftUptimeDay(delta) {
    const d = new Date(uptimeSelectedDate + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + parseInt(delta));
    // Pas de navigation vers le futur
    const today = new Date().toISOString().split('T')[0];
    const target = d.toISOString().split('T')[0];
    if (target <= today) {
        uptimeSelectedDate = target;
        loadAuditPageUptime();
    }
}

function resetUptimeToday() {
    uptimeSelectedDate = new Date().toISOString().split('T')[0];
    loadAuditPageUptime();
}

async function loadAuditPageUptime() {
    await loadUptimeUserOptions();
    const dateEl = document.getElementById('uptimeCurrentDate');
    if (dateEl) dateEl.textContent = uptimeSelectedDate;
    if (!uptimeSelectedUserId) return;

    try {
        const data = await api.getUserUsage(uptimeSelectedUserId, uptimeSelectedDate);

        const kpiRow = document.getElementById('uptimeKpiRow');
        const totalMin = Math.round((data.day_total_seconds || 0) / 60);
        kpiRow.innerHTML =
            auditKpiCard('#e7f1ff', '#0d6efd', 'bi-person', escapeHtml(data.username), 'Utilisateur', '<span class="audit-trend flat">UTC</span>') +
            auditKpiCard('#e6f4ea', '#28a745', 'bi-stopwatch', uptimeFmtDuration(data.day_total_seconds || 0), 'Temps total du jour', '') +
            auditKpiCard('#f1f3f5', '#6c757d', 'bi-grid', data.totals.length, 'Applications utilisées', '') +
            auditKpiCard('#fff1e6', '#fd7e14', 'bi-arrow-repeat', data.segments.length, 'Bascules', '');

        const hasData = data.segments.length > 0;
        document.getElementById('uptimeTimelineChart').classList.toggle('d-none', !hasData);
        document.getElementById('uptimeTimelineEmpty').classList.toggle('d-none', hasData);
        if (hasData) {
            renderUserDayTimeline('uptimeTimelineChart', data);
            renderUserDayBreakdown('uptimeBreakdownChart', data);
        } else {
            usageChartsDispose(['uptimeTimelineChart', 'uptimeBreakdownChart']);
        }
    } catch (error) {
        console.error('Erreur uptime utilisateur:', error);
        showToast('Erreur', 'Impossible de charger les données d\'usage', 'danger');
    }
}

// Dispose des graphiques quand leur onglet est quitté (anti-fuite)
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#auditPageTabs [data-bs-toggle="tab"]').forEach(btn => {
        btn.addEventListener('hidden.bs.tab', (e) => {
            const target = e.target.getAttribute('data-bs-target');
            if (target === '#auditPageUptime') {
                usageChartsDispose(['uptimeTimelineChart', 'uptimeBreakdownChart']);
                if (uptimePollTimer) { clearInterval(uptimePollTimer); uptimePollTimer = null; }
            }
            if (target === '#auditPageApps') usageChartsDispose(['pageAppUsageChart']);
            if (target === '#auditPageOverview') usageChartsDispose(['auditHeatmapChart']);
        });
        btn.addEventListener('shown.bs.tab', (e) => {
            if (e.target.getAttribute('data-bs-target') === '#auditPageUptime') {
                if (uptimePollTimer) clearInterval(uptimePollTimer);
                // Secours si le SSE audit_updated est down : rafraîchissement minute
                uptimePollTimer = setInterval(loadAuditPageUptime, 60000);
            }
        });
    });
});
