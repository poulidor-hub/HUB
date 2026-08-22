/**
 * Hub Application - helpers partagés + exports CSV audit
 *
 * Ce fichier ne contient que les fonctions encore utilisées par le reste du code :
 *   - getActionLabel, formatDateTime, getLogoutReasonBadge, getActionBadge, renderPagination
 *   - exportAuditLogs, exportSessions (wired via data-action dans index.html)
 *
 * L'ancienne version modale (loadAuditData, loadAuditOverview, renderAuditStats, etc.)
 * a été supprimée : les éléments DOM cibles (auditPeriod, auditAppsGrid, statActiveNow…)
 * n'existent plus dans index.html. Tout est géré par audit-page.js / audit-errors.js.
 */

// ===== Helpers partagés =====

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

function getActionBadge(action) {
    const badges = {
        'access_app': '<span class="badge bg-secondary">Accès App</span>',
        'login': '<span class="badge bg-secondary">Connexion</span>',
        'logout': '<span class="badge bg-light text-dark">Déconnexion</span>',
        'service_account_login': '<span class="badge bg-dark">M2M</span>'
    };
    return badges[action] || `<span class="badge bg-light text-dark">${action}</span>`;
}

function renderPagination(currentPage, totalPages, functionName) {
    if (totalPages <= 1) return '';

    let html = '<nav><ul class="pagination pagination-sm mb-0">';

    // Précédent
    html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" data-action="${functionName}" data-arg1="${currentPage - 1}">&laquo;</a>
    </li>`;

    // Pages
    const maxPages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxPages / 2));
    let endPage = Math.min(totalPages, startPage + maxPages - 1);

    if (endPage - startPage < maxPages - 1) {
        startPage = Math.max(1, endPage - maxPages + 1);
    }

    if (startPage > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" data-action="${functionName}" data-arg1="${1}">1</a></li>`;
        if (startPage > 2) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
    }

    for (let i = startPage; i <= endPage; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" data-action="${functionName}" data-arg1="${i}">${i}</a>
        </li>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        html += `<li class="page-item"><a class="page-link" href="#" data-action="${functionName}" data-arg1="${totalPages}">${totalPages}</a></li>`;
    }

    // Suivant
    html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" data-action="${functionName}" data-arg1="${currentPage + 1}">&raquo;</a>
    </li>`;

    html += '</ul></nav>';
    return html;
}

// ===== Export CSV =====

async function exportAuditLogs() {
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);
    const action = document.getElementById('pageLogsFilterAction')?.value || null;
    const target = document.getElementById('pageLogsFilterApp')?.value || null;

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
    const days = parseInt(document.getElementById('auditPeriodPage')?.value || 30);

    try {
        showToast('Export', 'Génération du fichier CSV...', 'info');
        await api.exportSessions(days);
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        console.error('Erreur export:', error);
        showToast('Erreur', 'Impossible d\'exporter les données', 'danger');
    }
}
