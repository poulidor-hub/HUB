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
let pageAuditDays = 7;

function setAuditPeriod(days) {
    pageAuditDays = days;
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
    await loadAuditPageOverview();
}

async function loadAuditPageOverview() {
    const days = pageAuditDays;
    try {
        const [kpis, alerts, active] = await Promise.all([
            api.getAuditKpis(days),
            api.getAuditAlerts(days),
            api.getActiveSessions()
        ]);
        renderAuditKpis(kpis);
        renderAuditAlerts(alerts.alerts || []);
        renderAuditLiveSessions(active);
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
                <a href="#" class="small text-danger fw-semibold text-decoration-none" onclick="auditForceLogout(${s.id}); return false;">Forcer déco.</a>
            </div>
        </div>`).join('');
}

async function auditForceLogout(sessionId) {
    if (!confirm('Forcer la déconnexion de cette session ?')) return;
    try {
        await api.revokeSession(sessionId);
        showToast('Succès', 'Session révoquée', 'success');
        loadAuditPageOverview();
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
                            <button class="btn btn-sm btn-outline-secondary" onclick="showPageAppAuditDetail('${app.slug}')">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function loadAuditPageApps() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    
    try {
        const summary = await api.getAppAuditSummary(days);
        renderPageAuditAppsGrid(summary.apps || []);
    } catch (error) {
        console.error('Erreur chargement apps audit:', error);
        showToast('Erreur', 'Impossible de charger les données', 'danger');
    }
}

function renderPageAuditAppsGrid(apps) {
    const container = document.getElementById('auditPageAppsGrid');
    document.getElementById('auditPageAppDetail').classList.add('d-none');
    container.classList.remove('d-none');
    
    if (!apps || apps.length === 0) {
        container.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="bi bi-grid display-1"></i>
                <h4 class="mt-3">Aucun accès enregistré</h4>
                <p>Les accès aux applications apparaîtront ici</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = apps.map(app => `
        <div class="col-sm-6 col-md-4 col-lg-3 mb-3">
            <div class="card h-100 cursor-pointer" onclick="showPageAppAuditDetail('${app.slug}')" style="cursor: pointer;">
                <div class="card-body text-center">
                    <div class="mb-2">
                        <span class="rounded-circle d-inline-flex align-items-center justify-content-center" 
                              style="width: 60px; height: 60px; background-color: ${app.color}; color: white; font-size: 1.5rem;">
                            <i class="${app.icon}"></i>
                        </span>
                    </div>
                    <h6 class="card-title mb-1">${escapeHtml(app.name)}</h6>
                    <p class="card-text small text-muted mb-2">${app.slug}</p>
                    <div class="d-flex justify-content-center gap-3">
                        <div class="text-center">
                            <div class="h5 mb-0">${app.access_count}</div>
                            <small class="text-muted">Accès</small>
                        </div>
                        <div class="text-center">
                            <div class="h5 mb-0">${app.unique_users}</div>
                            <small class="text-muted">Users</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
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
        const data = await api.getAppAuditDetail(appSlug, { days, page: 1, pageSize: 50 });
        
        // Masquer la grille, afficher le détail
        document.getElementById('auditPageAppsGrid').classList.add('d-none');
        document.getElementById('auditPageAppDetail').classList.remove('d-none');
        
        // Remplir les infos de l'app
        document.getElementById('pageAppDetailIcon').innerHTML = `<i class="${data.app.icon}" style="color: ${data.app.color};"></i>`;
        document.getElementById('pageAppDetailIcon').style.backgroundColor = `${data.app.color}20`;
        document.getElementById('pageAppDetailName').textContent = data.app.name;
        document.getElementById('pageAppDetailSlug').textContent = data.app.slug;
        
        // Stats
        document.getElementById('pageAppDetailAccess').textContent = data.stats.total_access;
        document.getElementById('pageAppDetailUsers').textContent = data.stats.unique_users;
        document.getElementById('pageAppDetailPeriod').textContent = `${days} jours`;
        
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
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-list-ul display-4"></i>
                <p class="mt-2">Aucun log trouvé</p>
            </div>
        `;
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

function renderPageSessionHistory(data) {
    const container = document.getElementById('pageSessionHistoryTable');
    
    if (!data.sessions || data.sessions.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-clock-history display-4 mb-3"></i>
                <p class="mb-0">Aucune session dans cette période</p>
                <small class="text-muted">Les sessions apparaîtront ici une fois que des utilisateurs se connecteront</small>
            </div>
        `;
        return;
    }
    
    // Séparer sessions actives et terminées
    const activeSessions = data.sessions.filter(s => !s.logout_at);
    const closedSessions = data.sessions.filter(s => s.logout_at);
    
    container.innerHTML = `
        <!-- Sessions actives -->
        ${activeSessions.length > 0 ? `
        <div class="mb-4 px-3 pt-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-success me-2">${activeSessions.length}</span>
                <h6 class="mb-0 text-success"><i class="bi bi-broadcast me-1"></i>Sessions actives</h6>
            </div>
            <table class="table table-sm table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        <th style="min-width: 150px">Utilisateur</th>
                        <th style="min-width: 120px">Application</th>
                        <th style="min-width: 150px">Connecté depuis</th>
                        <th style="min-width: 100px">Durée</th>
                        <th style="min-width: 80px">Activité</th>
                        <th style="min-width: 120px">IP</th>
                    </tr>
                </thead>
                <tbody>
                    ${activeSessions.map(s => `
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
                            <td>
                                <small class="text-muted">${formatDateTime(s.login_at)}</small>
                            </td>
                            <td>
                                <span class="badge bg-light text-dark border">
                                    <i class="bi bi-clock me-1"></i>${s.duration_display || 'en cours'}
                                </span>
                            </td>
                            <td>
                                ${s.idle_minutes !== undefined && s.idle_minutes > 5 
                                    ? `<span class="badge bg-warning text-dark" title="Inactif depuis ${s.idle_minutes} min">
                                        <i class="bi bi-hourglass-split me-1"></i>${s.idle_minutes}m
                                       </span>`
                                    : '<span class="badge bg-success"><i class="bi bi-activity"></i></span>'}
                            </td>
                            <td><code class="small bg-light px-2 py-1 rounded">${s.ip_address || '-'}</code></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
        ` : ''}
        
        <!-- Sessions terminées -->
        ${closedSessions.length > 0 ? `
        <div class="px-3 pb-3">
            <div class="d-flex align-items-center mb-2">
                <span class="badge bg-secondary me-2">${closedSessions.length}</span>
                <h6 class="mb-0 text-muted"><i class="bi bi-clock-history me-1"></i>Sessions terminées</h6>
            </div>
            <table class="table table-sm table-hover align-middle mb-0 border rounded">
                <thead class="table-light">
                    <tr>
                        <th style="min-width: 140px">Utilisateur</th>
                        <th style="min-width: 120px">Application</th>
                        <th style="min-width: 140px">Connexion</th>
                        <th style="min-width: 140px">Déconnexion</th>
                        <th style="min-width: 80px">Durée</th>
                        <th style="min-width: 100px">Raison</th>
                        <th style="min-width: 100px">IP</th>
                    </tr>
                </thead>
                <tbody>
                    ${closedSessions.map(s => `
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
    
    // Résumé dans le footer (hors zone scrollable)
    const summaryContainer = document.getElementById('pageSessionsSummary');
    if (summaryContainer) {
        summaryContainer.innerHTML = `
            <div class="row text-center">
                <div class="col">
                    <div class="text-muted small">Total sessions</div>
                    <strong class="fs-5">${data.total_sessions}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Actives</div>
                    <strong class="fs-5 text-success">${activeSessions.length}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Terminées</div>
                    <strong class="fs-5 text-secondary">${closedSessions.length}</strong>
                </div>
                <div class="col">
                    <div class="text-muted small">Période</div>
                    <strong class="fs-5">${data.period_days}j</strong>
                </div>
            </div>
        `;
    }
}
