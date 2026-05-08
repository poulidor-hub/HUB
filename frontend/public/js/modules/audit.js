/**
 * Hub Application - audit modal et sessions
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Audit & Sessions =====

// Variables globales pour l'audit
let auditCurrentAppSlug = null;
let auditLogsPage = 1;
let auditAppLogsPage = 1;

async function loadAuditData() {
    // Charger la vue d'ensemble par défaut
    await loadAuditOverview();
}

async function loadAuditOverview() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        // Charger toutes les données en parallèle
        const [stats, activeSessions, history, appSummary] = await Promise.all([
            api.getConnectionStats(days),
            api.getActiveSessions(),
            api.getSessionHistory(days),
            api.getAppAuditSummary(days).catch(() => ({ apps: [] }))
        ]);
        
        renderAuditStats(stats);
        renderActiveSessions(activeSessions);
        renderTopUsers(stats.top_users || []);
        renderTopApps(appSummary.apps || []);
    } catch (error) {
        console.error('Erreur chargement audit:', error);
        showToast('Erreur', 'Impossible de charger les données d\'audit', 'danger');
    }
}

async function loadAuditApps() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const summary = await api.getAppAuditSummary(days);
        renderAuditAppsGrid(summary.apps || []);
    } catch (error) {
        console.error('Erreur chargement apps audit:', error);
        showToast('Erreur', 'Impossible de charger les données', 'danger');
    }
}

async function loadAuditLogsTab() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    const action = document.getElementById('logsFilterAction')?.value || null;
    const target = document.getElementById('logsFilterApp')?.value || null;
    const username = document.getElementById('logsFilterUser')?.value?.trim() || null;
    
    try {
        // Charger les filtres si pas encore fait
        await loadLogsFilters();
        
        const data = await api.getAuditLogs({
            days,
            action: action || null,
            target: target || null,
            username: username || null,
            page: auditLogsPage,
            pageSize: 50
        });
        
        renderAuditLogs(data);
    } catch (error) {
        console.error('Erreur chargement logs:', error);
        showToast('Erreur', 'Impossible de charger les logs', 'danger');
    }
}

async function loadLogsFilters() {
    const actionSelect = document.getElementById('logsFilterAction');
    const appSelect = document.getElementById('logsFilterApp');
    
    // Ne recharger que si vide
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
            // Utiliser allApps si disponible, sinon charger
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
}

function resetLogsFilters() {
    document.getElementById('logsFilterAction').value = '';
    document.getElementById('logsFilterApp').value = '';
    document.getElementById('logsFilterUser').value = '';
    auditLogsPage = 1;
    loadAuditLogsTab();
}

function getActionLabel(action) {
    const labels = {
        'access_app': 'Accès application',
        'login': 'Connexion',
        'logout': 'Déconnexion',
        'service_account_login': 'Connexion M2M',
        'create_user': 'Création utilisateur',
        'delete_user': 'Suppression utilisateur',
        'create_app': 'Création app',
        'delete_app': 'Suppression app'
    };
    return labels[action] || action;
}

function renderAuditStats(stats) {
    document.getElementById('statActiveNow').textContent = stats.active_now || 0;
    document.getElementById('statTotalSessions').textContent = stats.total_sessions || 0;
    document.getElementById('statUniqueUsers').textContent = stats.unique_users || 0;
    document.getElementById('statAvgDuration').textContent = stats.avg_session_duration_display || 'N/A';
}

function renderActiveSessions(data) {
    const container = document.getElementById('activeSessionsTable');
    const countBadge = document.getElementById('activeSessionCount');
    
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
        <table class="table table-sm table-hover mb-0">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Application active</th>
                    <th>Connecté depuis</th>
                    <th>Inactivité</th>
                    <th>IP</th>
                </tr>
            </thead>
            <tbody>
                ${data.sessions.map(s => `
                    <tr>
                        <td>
                            <span class="badge bg-success me-2"><i class="bi bi-circle-fill" style="font-size: 0.5rem;"></i></span>
                            <strong>${escapeHtml(s.username || 'Unknown')}</strong>
                        </td>
                        <td>
                            ${s.current_app_name 
                                ? `<span class="badge bg-primary"><i class="bi bi-app me-1"></i>${escapeHtml(s.current_app_name)}</span>` 
                                : '<span class="text-muted small">Hub</span>'}
                        </td>
                        <td><small>${formatDateTime(s.login_at)}</small> <span class="badge bg-light text-dark">${s.duration_minutes} min</span></td>
                        <td>
                            ${s.idle_minutes > 5 
                                ? `<span class="badge bg-warning text-dark" title="Inactif depuis ${s.idle_minutes} min"><i class="bi bi-hourglass-split me-1"></i>${s.idle_minutes} min</span>`
                                : '<span class="badge bg-success"><i class="bi bi-activity me-1"></i>Actif</span>'}
                        </td>
                        <td><code class="small">${s.ip_address || '-'}</code></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderTopUsers(topUsers) {
    const container = document.getElementById('topUsersTable');
    
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
        <table class="table table-sm table-hover mb-0">
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
                                ? `<span class="badge ${index === 0 ? 'bg-warning' : index === 1 ? 'bg-secondary' : 'bg-danger'}">${index + 1}</span>`
                                : `<span class="text-muted">${index + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td><span class="badge bg-primary">${u.sessions}</span></td>
                        <td>${u.total_time_display}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function formatDateTime(isoString) {
    if (!isoString) return '-';
    const date = parseUTCDate(isoString);
    return date.toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function getLogoutReasonBadge(reason) {
    if (!reason) return '<span class="badge bg-secondary">Actif</span>';
    
    const badges = {
        'explicit': '<span class="badge bg-light text-dark"><i class="bi bi-box-arrow-right me-1"></i>Déconnexion</span>',
        'timeout': '<span class="badge bg-warning text-dark"><i class="bi bi-hourglass-bottom me-1"></i>Timeout</span>',
        'token_revoked': '<span class="badge bg-danger"><i class="bi bi-x-circle me-1"></i>Révoqué</span>',
        'forced': '<span class="badge bg-danger"><i class="bi bi-exclamation-triangle me-1"></i>Forcé</span>',
        'browser_closed': '<span class="badge bg-info text-dark"><i class="bi bi-window-x me-1"></i>Navigateur fermé</span>',
        'sse_disconnect': '<span class="badge bg-secondary"><i class="bi bi-wifi-off me-1"></i>Connexion perdue</span>'
    };
    
    return badges[reason] || `<span class="badge bg-secondary">${reason}</span>`;
}

// ===== Rendus supplémentaires pour Audit étendu =====

function renderTopApps(apps) {
    const container = document.getElementById('topAppsTable');
    
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
        <table class="table table-sm table-hover mb-0">
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
                                ? `<span class="badge ${index === 0 ? 'bg-warning' : index === 1 ? 'bg-secondary' : 'bg-danger'}">${index + 1}</span>`
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
                        <td><span class="badge bg-primary">${app.access_count}</span></td>
                        <td><span class="badge bg-info">${app.unique_users}</span></td>
                        <td>
                            <button class="btn btn-sm btn-outline-primary" onclick="showAppAuditDetail('${app.slug}')">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderAuditAppsGrid(apps) {
    const container = document.getElementById('auditAppsGrid');
    document.getElementById('auditAppDetail').classList.add('d-none');
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
            <div class="card h-100 cursor-pointer" onclick="showAppAuditDetail('${app.slug}')" style="cursor: pointer;">
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
                            <div class="h5 mb-0 text-primary">${app.access_count}</div>
                            <small class="text-muted">Accès</small>
                        </div>
                        <div class="text-center">
                            <div class="h5 mb-0 text-info">${app.unique_users}</div>
                            <small class="text-muted">Users</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `).join('');
}

async function showAppAuditDetail(appSlug) {
    auditCurrentAppSlug = appSlug;
    auditAppLogsPage = 1;
    
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(appSlug, { days, page: 1, pageSize: 50 });
        
        // Masquer la grille, afficher le détail
        document.getElementById('auditAppsGrid').classList.add('d-none');
        document.getElementById('auditAppDetail').classList.remove('d-none');
        
        // Remplir les infos de l'app
        document.getElementById('appDetailIcon').innerHTML = `<i class="${data.app.icon}" style="color: ${data.app.color};"></i>`;
        document.getElementById('appDetailIcon').style.backgroundColor = `${data.app.color}20`;
        document.getElementById('appDetailName').textContent = data.app.name;
        document.getElementById('appDetailSlug').textContent = data.app.slug;
        
        // Stats
        document.getElementById('appDetailAccess').textContent = data.stats.total_access;
        document.getElementById('appDetailUsers').textContent = data.stats.unique_users;
        document.getElementById('appDetailPeriod').textContent = `${days} jours`;
        
        // Top users
        renderAppDetailTopUsers(data.top_users);
        
        // Logs
        renderAppDetailLogs(data);
        
    } catch (error) {
        console.error('Erreur détail app:', error);
        showToast('Erreur', 'Impossible de charger les détails', 'danger');
    }
}

function hideAppDetail() {
    document.getElementById('auditAppDetail').classList.add('d-none');
    document.getElementById('auditAppsGrid').classList.remove('d-none');
    auditCurrentAppSlug = null;
}

function renderAppDetailTopUsers(topUsers) {
    const container = document.getElementById('appDetailTopUsers');
    
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
                                ? `<span class="badge ${i === 0 ? 'bg-warning' : i === 1 ? 'bg-secondary' : 'bg-danger'}">${i + 1}</span>`
                                : `<span class="text-muted">${i + 1}</span>`
                            }
                        </td>
                        <td><strong>${escapeHtml(u.username)}</strong></td>
                        <td class="text-end"><span class="badge bg-primary">${u.access_count}</span></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderAppDetailLogs(data) {
    const container = document.getElementById('appDetailLogs');
    const pagination = document.getElementById('appDetailPagination');
    
    // Pagination
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'loadAppDetailPage');
    
    if (!data.logs || data.logs.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun accès enregistré</p>';
        return;
    }
    
    container.innerHTML = `
        <table class="table table-sm table-hover mb-0">
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

async function loadAppDetailPage(page) {
    if (!auditCurrentAppSlug) return;
    auditAppLogsPage = page;
    
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        const data = await api.getAppAuditDetail(auditCurrentAppSlug, { days, page, pageSize: 50 });
        renderAppDetailLogs(data);
    } catch (error) {
        console.error('Erreur pagination:', error);
    }
}

function renderAuditLogs(data) {
    const container = document.getElementById('auditLogsTable');
    const countSpan = document.getElementById('logsCount');
    const pagination = document.getElementById('logsPagination');
    
    countSpan.textContent = `${data.total_count} résultats`;
    pagination.innerHTML = renderPagination(data.page, data.total_pages, 'changeLogsPage');
    
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
        <table class="table table-sm table-hover mb-0">
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

function getActionBadge(action) {
    const badges = {
        'access_app': '<span class="badge bg-secondary">Accès App</span>',
        'login': '<span class="badge bg-secondary">Connexion</span>',
        'logout': '<span class="badge bg-light text-dark">Déconnexion</span>',
        'service_account_login': '<span class="badge bg-dark">M2M</span>'
    };
    return badges[action] || `<span class="badge bg-light text-dark">${action}</span>`;
}

async function changeLogsPage(page) {
    auditLogsPage = page;
    await loadAuditLogsTab();
}

function renderPagination(currentPage, totalPages, functionName) {
    if (totalPages <= 1) return '';
    
    let html = '<nav><ul class="pagination pagination-sm mb-0">';
    
    // Précédent
    html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="${functionName}(${currentPage - 1}); return false;">&laquo;</a>
    </li>`;
    
    // Pages
    const maxPages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxPages / 2));
    let endPage = Math.min(totalPages, startPage + maxPages - 1);
    
    if (endPage - startPage < maxPages - 1) {
        startPage = Math.max(1, endPage - maxPages + 1);
    }
    
    if (startPage > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" onclick="${functionName}(1); return false;">1</a></li>`;
        if (startPage > 2) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="${functionName}(${i}); return false;">${i}</a>
        </li>`;
    }
    
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        html += `<li class="page-item"><a class="page-link" href="#" onclick="${functionName}(${totalPages}); return false;">${totalPages}</a></li>`;
    }
    
    // Suivant
    html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="${functionName}(${currentPage + 1}); return false;">&raquo;</a>
    </li>`;
    
    html += '</ul></nav>';
    return html;
}

// ===== Export CSV =====

async function exportAuditLogs() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    const action = document.getElementById('logsFilterAction')?.value || null;
    const target = document.getElementById('logsFilterApp')?.value || null;
    
    try {
        showToast('Export', 'Génération du fichier CSV...', 'info');
        await api.exportAuditLogs({ days, action, target });
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showToast('Erreur', 'Impossible d\'exporter les données', 'danger');
    }
}

async function exportSessions() {
    const days = parseInt(document.getElementById('auditPeriod')?.value || 30);
    
    try {
        showToast('Export', 'Génération du fichier CSV...', 'info');
        await api.exportSessions(days);
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showToast('Erreur', 'Impossible d\'exporter les données', 'danger');
    }
}
