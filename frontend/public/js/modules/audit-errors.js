/**
 * Hub Application - erreurs HTTP audit
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Vue Erreurs HTTP (Audit) =====

let errorsPage = 1;

/**
 * Convertit la periode d'audit globale (jours) en heures pour le endpoint erreurs.
 * Max 168h (7 jours) car c'est la limite de l'API /audit/errors.
 */
function getErrorHours() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 7);
    return Math.min(days * 24, 168);
}

/**
 * Met à jour le badge de notification d'erreurs dans la navigation principale.
 */
function updateNavErrorBadge(count) {
    const navBadge = document.getElementById('navAuditErrorBadge');
    if (navBadge) {
        if (count > 0) {
            navBadge.textContent = count > 99 ? '99+' : count;
            navBadge.style.display = 'inline';
        } else {
            navBadge.style.display = 'none';
        }
    }
}

/**
 * Interroge l'API pour connaître le nombre de nouvelles erreurs
 * depuis le dernier acquittement, puis met à jour le badge nav.
 */
async function updateErrorNotificationBadge() {
    try {
        const result = await api.getNewErrorsCount(errorsLastAcknowledgedAt);
        updateNavErrorBadge(result.count || 0);
    } catch (e) {
        // Silencieux — ne pas bloquer le reste de l'UI
    }
}

/**
 * Purge les erreurs (avec confirmation).
 */
async function purgeErrors() {
    const hours = getErrorHours();
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    
    const desc = errorCode ? `les erreurs ${errorCode}` : 'toutes les erreurs';
    const period = `des ${hours} dernières heures`;
    
    if (!confirm(`Purger ${desc} ${period} ?\n\nCette action est irréversible.`)) return;
    
    try {
        const result = await api.purgeErrors(hours, errorCode ? parseInt(errorCode) : null);
        showToast('Succès', result.message, 'success');
        await loadAuditPageErrors();
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible de purger les erreurs', 'danger');
    }
}

/**
 * Purge toutes les erreurs (sans filtre de période).
 */
async function purgeAllErrors() {
    if (!confirm('Purger TOUTES les erreurs ?\n\nCette action est irréversible.')) return;
    
    try {
        const result = await api.purgeErrors();
        showToast('Succès', result.message, 'success');
        await loadAuditPageErrors();
    } catch (error) {
        showToast('Erreur', error.message || 'Impossible de purger les erreurs', 'danger');
    }
}

/**
 * Met à jour le badge de l'onglet Erreurs (proactivement, sans attendre le clic).
 */
async function updateErrorTabBadge() {
    try {
        const stats = await api.getErrorStats(getErrorHours());
        const badge = document.getElementById('errorCountBadge');
        if (badge) {
            if (stats.total > 0) {
                badge.textContent = stats.total > 99 ? '99+' : stats.total;
                badge.style.display = 'inline';
            } else {
                badge.style.display = 'none';
            }
        }
    } catch (e) {
        // Silencieux
    }
}

async function loadAuditPageErrors() {
    const hours = getErrorHours();
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    const errorType = document.getElementById('errorsFilterType')?.value || null;
    const sourceApp = document.getElementById('errorsFilterApp')?.value || null;
    const path = document.getElementById('errorsFilterPath')?.value || null;
    
    try {
        // Charger les apps pour le filtre si pas encore fait
        await loadErrorsAppFilter();
        
        // Charger stats et logs en parallèle
        const [stats, errors] = await Promise.all([
            api.getErrorStats(hours),
            api.getErrorLogs({
                page: errorsPage,
                perPage: 50,
                errorCode: errorCode ? parseInt(errorCode) : null,
                errorType,
                sourceApp,
                path,
                hours
            })
        ]);
        
        const count4xx = stats.by_code?.filter(c => c.code >= 400 && c.code < 500)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        const count5xx = stats.by_code?.filter(c => c.code >= 500)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        const countAuth = stats.by_code?.filter(c => c.code === 401 || c.code === 403)
            .reduce((sum, c) => sum + c.count, 0) || 0;
        
        // Mettre à jour les KPIs avec le style dashboard
        const kpiRow = document.getElementById('auditErrorKpiRow');
        if (kpiRow) {
            kpiRow.innerHTML =
                auditKpiCard('#fde8e8', '#dc3545', 'bi-exclamation-triangle-fill', stats.total, 'Erreurs totales',
                    '<span class="audit-trend flat">' + (parseInt(document.getElementById('auditPeriodPage')?.value) || 7) + 'j</span>') +
                auditKpiCard('#fff1e6', '#fd7e14', 'bi-exclamation-diamond', count4xx, 'Erreurs client (4xx)',
                    '<span class="audit-trend flat">Client</span>') +
                auditKpiCard('#fde8e8', '#dc3545', 'bi-exclamation-triangle', count5xx, 'Erreurs serveur (5xx)',
                    '<span class="audit-trend flat">Serveur</span>') +
                auditKpiCard('#e6f4ea', '#198754', 'bi-lock', countAuth, 'Auth (401/403)',
                    '<span class="audit-trend flat">Accès</span>');
        }
        
        // Mettre à jour le badge dans l'onglet
        const badge = document.getElementById('errorCountBadge');
        if (stats.total > 0) {
            badge.textContent = stats.total > 99 ? '99+' : stats.total;
            badge.style.display = 'inline';
        } else {
            badge.style.display = 'none';
        }
        
        // Afficher la table
        renderErrorsTable(errors);
        
        // Marquer les erreurs comme consultées (acquittement automatique)
        // L'admin a vu la page, on considère les erreurs comme prises en compte
        try {
            const ack = await api.acknowledgeErrors();
            errorsLastAcknowledgedAt = ack.acknowledged_at;
            localStorage.setItem('errorsLastAcknowledgedAt', errorsLastAcknowledgedAt);
            updateNavErrorBadge(0);
        } catch (e) {
            // Non bloquant
        }
        
    } catch (error) {
        console.error('Erreur chargement erreurs:', error);
        showToast('Erreur', 'Impossible de charger les logs d\'erreurs', 'danger');
    }
}

async function loadErrorsAppFilter() {
    const appSelect = document.getElementById('errorsFilterApp');
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
}

function resetErrorsFilters() {
    document.getElementById('errorsFilterCode').value = '';
    document.getElementById('errorsFilterType').value = '';
    document.getElementById('errorsFilterApp').value = '';
    document.getElementById('errorsFilterPath').value = '';
    errorsPage = 1;
    loadAuditPageErrors();
}

function renderErrorsTable(data) {
    const container = document.getElementById('pageErrorsTable');
    const countSpan = document.getElementById('errorsCount');
    const pagination = document.getElementById('errorsPagination');
    
    countSpan.textContent = `${data.total} erreurs`;
    pagination.innerHTML = renderPagination(data.page, data.pages, 'changeErrorsPage');
    
    if (!data.errors || data.errors.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-check-circle display-4 text-success"></i>
                <p class="mt-2">Aucune erreur dans cette période 🎉</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table class="table audit-tbl table-hover mb-0">
            <thead class="sticky-top bg-white">
                <tr>
                    <th>Date/Heure</th>
                    <th>Code</th>
                    <th>Type</th>
                    <th>Méthode</th>
                    <th>Chemin</th>
                    <th>App Source</th>
                    <th>Utilisateur</th>
                    <th>Message</th>
                    <th>IP</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${data.errors.map(e => `
                    <tr class="${e.error_code >= 500 ? 'table-danger' : ''}">
                        <td><small>${formatDateTime(e.created_at)}</small></td>
                        <td>${getErrorCodeBadge(e.error_code)}</td>
                        <td><small class="text-muted">${e.error_type || '-'}</small></td>
                        <td><code class="small">${e.request_method || '-'}</code></td>
                        <td><code class="small" title="${escapeHtml(e.request_path || '')}">${truncateText(e.request_path, 30)}</code></td>
                        <td>${e.source_app_slug ? `<span class="badge bg-info">${escapeHtml(e.source_app_slug)}</span>` : '-'}</td>
                        <td><small>${errorActorHtml(e)}</small></td>
                        <td><small class="text-muted" title="${escapeHtml(e.error_message || '')}">${truncateText(e.error_message, 40)}</small></td>
                        <td><code class="small">${e.ip_address || '-'}</code></td>
                        <td>
                            ${e.has_stack_trace ? `<button class="btn btn-outline-secondary btn-sm" data-action="showErrorDetail" data-arg1="${e.id}" title="Voir le détail"><i class="bi bi-eye"></i></button>` : ''}
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

/**
 * Cellule « Utilisateur » : affiche l'appelant de l'erreur —
 * username (user), client_id tronqué avec badge M2M (service account), ou fallback id.
 */
function errorActorHtml(e) {
    if (e.username) return `<i class="bi bi-person me-1"></i>${escapeHtml(e.username)}`;
    const cid = e.service_account_client_id;
    if (cid) {
        const short = cid.length > 18 ? cid.slice(0, 15) + '…' : cid;
        return `<span class="badge bg-dark me-1">M2M</span><code title="${escapeHtml(cid)}">${escapeHtml(short)}</code>`;
    }
    if (e.user_id) return `ID: ${e.user_id}`;
    if (e.service_account_id) return `SA #${e.service_account_id}`;
    return '-';
}

function getErrorCodeBadge(code) {
    let bgClass = 'bg-secondary';
    if (code === 401) bgClass = 'bg-warning text-dark';    else if (code === 403) bgClass = 'bg-danger';
    else if (code === 404) bgClass = 'bg-info';
    else if (code === 422) bgClass = 'bg-warning text-dark';
    else if (code >= 500) bgClass = 'bg-danger';
    
    return `<span class="badge ${bgClass}">${code}</span>`;
}

function truncateText(text, maxLength) {
    if (!text) return '-';
    if (text.length <= maxLength) return escapeHtml(text);
    return escapeHtml(text.substring(0, maxLength)) + '...';
}

async function changeErrorsPage(page) {
    errorsPage = page;
    await loadAuditPageErrors();
}

async function showErrorDetail(errorId) {
    try {
        const error = await api.getErrorDetail(errorId);
        
        const modal = `
            <div class="modal fade" id="errorDetailModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="bi bi-exclamation-triangle me-2"></i>
                                Erreur ${error.error_code} - ${error.error_type || 'Inconnu'}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row mb-3">
                                <div class="col-md-6">
                                    <small class="text-muted">Date/Heure</small>
                                    <div>${formatDateTime(error.created_at)}</div>
                                </div>
                                <div class="col-md-6">
                                    <small class="text-muted">Request ID</small>
                                    <div><code>${error.request_id || '-'}</code></div>
                                </div>
                            </div>
                            <div class="row mb-3">
                                <div class="col-md-4">
                                    <small class="text-muted">Méthode</small>
                                    <div><code>${error.request_method || '-'}</code></div>
                                </div>
                                <div class="col-md-8">
                                    <small class="text-muted">Chemin</small>
                                    <div><code>${escapeHtml(error.request_path || '-')}</code></div>
                                </div>
                            </div>
                            <div class="row mb-3">
                                <div class="col-md-4">
                                    <small class="text-muted">App Source</small>
                                    <div>${error.source_app_slug ? `<span class="badge bg-info">${escapeHtml(error.source_app_slug)}</span>` : '-'}</div>
                                </div>
                                <div class="col-md-4">
                                    <small class="text-muted">Utilisateur</small>
                                    <div>${errorActorHtml(error)}</div>
                                </div>
                                <div class="col-md-4">
                                    <small class="text-muted">IP</small>
                                    <div><code>${error.ip_address || '-'}</code></div>
                                </div>
                            </div>
                            <div class="mb-3">
                                <small class="text-muted">Message d'erreur</small>
                                <div class="alert alert-danger mb-0">${escapeHtml(error.error_message || 'Aucun message')}</div>
                            </div>
                            ${error.stack_trace ? `
                                <div class="mb-3">
                                    <small class="text-muted">Stack Trace</small>
                                    <pre class="bg-dark text-light p-3 rounded small" style="max-height: 300px; overflow-y: auto;">${escapeHtml(error.stack_trace)}</pre>
                                </div>
                            ` : ''}
                            <div class="mb-0">
                                <small class="text-muted">User Agent</small>
                                <div class="small text-muted">${escapeHtml(error.user_agent || '-')}</div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Supprimer l'ancien modal s'il existe
        const existing = document.getElementById('errorDetailModal');
        if (existing) existing.remove();
        
        // Ajouter et afficher le modal
        document.body.insertAdjacentHTML('beforeend', modal);
        const modalEl = new bootstrap.Modal(document.getElementById('errorDetailModal'));
        modalEl.show();
        
    } catch (error) {
        console.error('Erreur chargement détail:', error);
        showToast('Erreur', 'Impossible de charger le détail de l\'erreur', 'danger');
    }
}

async function exportErrors() {
    const hours = getErrorHours();
    const errorCode = document.getElementById('errorsFilterCode')?.value || null;
    
    try {
        await api.exportErrors(hours, errorCode ? parseInt(errorCode) : null);
        showToast('Succès', 'Export CSV téléchargé', 'success');
    } catch (error) {
        showToast('Erreur', 'Impossible d\'exporter les erreurs', 'danger');
    }
}
