/**
 * Hub Application - administration des applications
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Admin Panel =====

async function loadAdminData() {
    console.log('loadAdminData: Chargement des données admin...');
    try {
        const [apps, serviceAccounts] = await Promise.all([
            api.getAllApps(adminAppsWorkshopFilter),
            api.getServiceAccounts()
        ]);

        console.log('loadAdminData: Apps chargées:', apps?.length || 0);
        console.log('loadAdminData: Service accounts chargés:', serviceAccounts?.length || 0);

        allApps = Array.isArray(apps) ? apps : [];
        allServiceAccounts = Array.isArray(serviceAccounts) ? serviceAccounts : [];

        syncAdminAppsFilterControls();
        renderAppsTable(getFilteredAdminApps());
        renderServiceAccountsTable(allServiceAccounts);

        // Groupes d'applications (module admin-app-groups.js)
        loadAppGroups().catch(e => console.error('loadAdminData: groupes non chargés:', e));
    } catch (error) {
        console.error('loadAdminData: Erreur:', error);
        throw error;
    }
}

function compareAdminAppsByName(leftApp, rightApp) {
    const leftName = (leftApp?.name || leftApp?.slug || '').trim();
    const rightName = (rightApp?.name || rightApp?.slug || '').trim();
    return leftName.localeCompare(rightName, 'fr', { sensitivity: 'base', numeric: true });
}

function getAdminAppCategories() {
    return [...new Set(
        allApps
            .map(app => (app.category || '').trim())
            .filter(Boolean)
    )].sort((left, right) => left.localeCompare(right, 'fr', { sensitivity: 'base', numeric: true }));
}

function syncAdminAppsFilterControls() {
    const searchInput = document.getElementById('adminAppsSearch');
    const statusSelect = document.getElementById('adminAppsStatusFilter');
    const categorySelect = document.getElementById('adminAppsCategoryFilter');
    const clearBtn = document.getElementById('adminAppsSearchClear');

    if (searchInput && searchInput.value !== adminAppsSearchQuery) {
        searchInput.value = adminAppsSearchQuery;
    }

    if (statusSelect) {
        statusSelect.value = adminAppsStatusFilter;
    }

    if (categorySelect) {
        const categories = getAdminAppCategories();
        categorySelect.innerHTML = [
            '<option value="all">Toutes</option>',
            ...categories.map(category => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`)
        ].join('');

        if (!categories.includes(adminAppsCategoryFilter)) {
            adminAppsCategoryFilter = 'all';
        }
        categorySelect.value = adminAppsCategoryFilter;
    }

    if (clearBtn) {
        clearBtn.classList.toggle('d-none', !adminAppsSearchQuery.trim());
    }

    const unmanagedSelect = document.getElementById('adminAppsUnmanagedFilter');
    if (unmanagedSelect) unmanagedSelect.value = adminAppsUnmanagedFilter;
}

function getFilteredAdminApps() {
    const normalizedQuery = adminAppsSearchQuery.trim().toLowerCase();

    return allApps.filter(app => {
        const isPending = app.approval_status === 'pending';
        const isRejected = app.approval_status === 'rejected';

        if (normalizedQuery) {
            const matchesQuery = [
                app.name,
                app.slug,
                app.frontend_url,
                app.description,
                app.category,
            ].some(value => (value || '').toLowerCase().includes(normalizedQuery));

            if (!matchesQuery) {
                return false;
            }
        }

        if (adminAppsStatusFilter === 'active' && (isPending || isRejected || !app.is_active)) {
            return false;
        }

        if (adminAppsStatusFilter === 'inactive' && (isPending || isRejected || app.is_active)) {
            return false;
        }

        if (adminAppsStatusFilter === 'pending' && !isPending) {
            return false;
        }

        if (adminAppsStatusFilter === 'rejected' && !isRejected) {
            return false;
        }

        if (adminAppsCategoryFilter !== 'all' && (app.category || '').trim() !== adminAppsCategoryFilter) {
            return false;
        }

        if (adminAppsWorkshopFilter === 'true' && !app.is_workshop) {
            return false;
        }

        if (adminAppsWorkshopFilter === 'false' && app.is_workshop) {
            return false;
        }

        if (adminAppsUnmanagedFilter === 'unmanaged' && !app.is_unmanaged) {
            return false;
        }
        if (adminAppsUnmanagedFilter === 'managed' && app.is_unmanaged) {
            return false;
        }

        return true;
    });
}

function filterAdminApps() {
    adminAppsSearchQuery = document.getElementById('adminAppsSearch')?.value || '';
    adminAppsStatusFilter = document.getElementById('adminAppsStatusFilter')?.value || 'all';
    adminAppsCategoryFilter = document.getElementById('adminAppsCategoryFilter')?.value || 'all';
    adminAppsWorkshopFilter = document.getElementById('adminAppsWorkshopFilter')?.value || 'false';
    adminAppsUnmanagedFilter = document.getElementById('adminAppsUnmanagedFilter')?.value || 'all';

    syncAdminAppsFilterControls();
    renderAppsTable(getFilteredAdminApps());
}

function clearAdminAppsSearch() {
    adminAppsSearchQuery = '';
    const searchInput = document.getElementById('adminAppsSearch');
    if (searchInput) {
        searchInput.value = '';
        searchInput.focus();
    }
    syncAdminAppsFilterControls();
    renderAppsTable(getFilteredAdminApps());
}

function resetAdminAppsFilters() {
    adminAppsSearchQuery = '';
    adminAppsStatusFilter = 'all';
    adminAppsCategoryFilter = 'all';
    adminAppsWorkshopFilter = 'false';
    adminAppsUnmanagedFilter = 'all';
    syncAdminAppsFilterControls();
    renderAppsTable(getFilteredAdminApps());
}

// ===== Status Monitoring =====

/**
 * Rafraîchit le statut de connectivité des applications
 */
async function refreshAppsStatus() {
    const connectivityIcons = document.querySelectorAll('.app-connectivity-icon');
    const isFirstLoad = connectivityIcons.length > 0 && 
        connectivityIcons[0].querySelector('i') === null;

    if (!isFirstLoad) {
        // Rafraîchissement : indicateur subtil sans détruire l'affichage
        connectivityIcons.forEach(el => {
            el.style.opacity = '0.6';
        });
    }
    
    try {
        const status = await api.getAppsStatus();
        console.log('📡 Statut des applications:', status);
        
        // Mettre à jour chaque icône in-place
        status.apps.forEach(appStatus => {
            const cell = document.querySelector(`.app-status-cell[data-app-slug="${appStatus.slug}"]`);
            if (!cell) return;
            const iconEl = cell.querySelector('.app-connectivity-icon');
            if (!iconEl) return;
            
            iconEl.style.opacity = '1';
            let icon = '';
            switch (appStatus.status) {
                case 'online':
                    icon = `<i class="bi bi-wifi text-success" title="En ligne (${appStatus.response_time_ms || '?'}ms)"></i>`;
                    break;
                case 'offline':
                    icon = `<i class="bi bi-wifi-off text-danger" title="Hors ligne: ${appStatus.error || 'Non joignable'}"></i>`;
                    break;
                case 'error':
                    icon = `<i class="bi bi-exclamation-triangle text-warning" title="Erreur: ${appStatus.error || ''}"></i>`;
                    break;
                default:
                    icon = `<i class="bi bi-wifi text-muted" title="Inconnu"></i>`;
            }
            iconEl.innerHTML = icon;
        });

        // Restaurer l'opacité pour les éléments non mis à jour
        connectivityIcons.forEach(el => {
            el.style.opacity = '1';
        });
        
        // Afficher un résumé dans un toast discret
        const summary = status.summary;
        if (summary.offline > 0 || summary.error > 0) {
            showToast('Connectivité', 
                `${summary.online}/${summary.total} applications en ligne`, 
                summary.offline > 0 ? 'warning' : 'info');
        }
    } catch (error) {
        console.error('Erreur lors du test de connectivité:', error);
        // En cas d'erreur réseau, conserver le dernier état connu
        connectivityIcons.forEach(el => {
            el.style.opacity = '1';
        });
    }
}

/**
 * Met à jour le statut Docker d'une app dans le tableau admin (colonne État fusionnée)
 */
function updateAppDockerStatusInTable(slug, dockerStatus) {
    if (!slug) return;
    
    const cell = document.querySelector(`.app-status-cell[data-app-slug="${slug}"]`);
    if (!cell) return;
    
    // Trouver la 2ème icône dans .status-icons (Docker)
    const icons = cell.querySelectorAll('.status-icons > i, .status-icons > span > i');
    const statusIcons = cell.querySelector('.status-icons');
    if (!statusIcons) return;
    
    // L'icône Docker est le 2ème enfant direct de .status-icons
    const children = Array.from(statusIcons.children);
    if (children.length < 2) return;
    const dockerEl = children[1]; // 2ème élément = Docker
    
    let dockerIcon;
    if (!dockerStatus) {
        dockerIcon = '<i class="bi bi-question-circle text-muted" title="Docker: Inconnu"></i>';
    } else if (dockerStatus === 'running') {
        dockerIcon = '<i class="bi bi-play-circle-fill text-success" title="Docker: Running"></i>';
    } else {
        dockerIcon = '<i class="bi bi-pause-circle-fill text-warning" title="Docker: Maintenance"></i>';
    }
    dockerEl.outerHTML = dockerIcon;
    
    console.log(`🔄 Docker status mis à jour pour ${slug}: ${dockerStatus}`);
}

/**
 * Met à jour le statut de santé (health check) d'une app dans le tableau admin
 */
/**
 * Met à jour le statut de santé (health check) d'une app dans le tableau admin (colonne État fusionnée)
 */
function updateAppHealthStatusInTable(slug, healthStatus) {
    if (!slug) return;
    
    const cell = document.querySelector(`.app-status-cell[data-app-slug="${slug}"]`);
    if (!cell) return;
    
    const iconEl = cell.querySelector('.app-connectivity-icon');
    if (!iconEl) return;
    
    let icon;
    if (healthStatus === 'healthy') {
        icon = '<i class="bi bi-wifi text-success" title="Connectivité: En ligne"></i>';
    } else if (healthStatus === 'unhealthy') {
        icon = '<i class="bi bi-wifi-off text-danger" title="Connectivité: Hors ligne"></i>';
    } else {
        icon = '<i class="bi bi-wifi text-muted" title="Connectivité: Inconnu"></i>';
    }
    iconEl.innerHTML = icon;
    
    console.log(`🏥 Health status mis à jour pour ${slug}: ${healthStatus}`);
}

/**
 * Met à jour le statut d'une carte d'app sur le dashboard
 * @param {string} slug - Slug de l'application
 * @param {string} dockerStatus - Statut Docker ('running', 'exited', etc.)
 * @param {string} healthStatus - Statut santé ('healthy', 'unhealthy')
 */
function updateAppCardStatus(slug, dockerStatus, healthStatus) {
    if (!slug) return;
    
    const card = document.querySelector(`.app-card[data-app-slug="${slug}"]`);
    if (!card) return;
    
    const isDockerStopped = dockerStatus && dockerStatus !== 'running';
    const isHealthUnhealthy = healthStatus === 'unhealthy';
    const isInMaintenance = isDockerStopped || isHealthUnhealthy;
    
    if (isInMaintenance) {
        card.classList.add('app-inactive');
        // Ajouter un badge maintenance si pas déjà présent
        if (!card.querySelector('.maintenance-badge')) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark maintenance-badge position-absolute top-0 end-0 m-2';
            badge.innerHTML = '<i class="bi bi-tools me-1"></i>Maintenance';
            card.style.position = 'relative';
            card.appendChild(badge);
        }
    } else {
        card.classList.remove('app-inactive');
        // Retirer le badge maintenance
        const badge = card.querySelector('.maintenance-badge');
        if (badge) badge.remove();
    }
}

/**
 * Intervalle de rafraîchissement automatique de la connectivité
 */
let connectivityRefreshInterval = null;
const CONNECTIVITY_REFRESH_DELAY = 30000; // 30 secondes

/**
 * Démarre le rafraîchissement périodique de la connectivité
 */
function startConnectivityRefresh() {
    if (connectivityRefreshInterval) return; // Déjà démarré
    
    connectivityRefreshInterval = setInterval(() => {
        // Rafraîchir seulement si on est sur la page apps-admin
        if (currentPage === 'apps-admin') {
            console.log('🔄 Rafraîchissement automatique de la connectivité...');
            refreshAppsStatus();
        }
    }, CONNECTIVITY_REFRESH_DELAY);
    
    console.log(`⏱️ Rafraîchissement connectivité activé (${CONNECTIVITY_REFRESH_DELAY/1000}s)`);
}

/**
 * Arrête le rafraîchissement périodique
 */
function stopConnectivityRefresh() {
    if (connectivityRefreshInterval) {
        clearInterval(connectivityRefreshInterval);
        connectivityRefreshInterval = null;
        console.log('⏱️ Rafraîchissement connectivité désactivé');
    }
}

/**
 * Rafraîchit le statut des Service Accounts
 */
async function refreshServiceAccountsStatus() {
    const credentialsCells = document.querySelectorAll('.sa-credentials-status');
    
    // Indicateur subtil de rafraîchissement
    credentialsCells.forEach(cell => {
        cell.style.opacity = '0.6';
    });
    
    try {
        const status = await api.getServiceAccountsStatus();
        console.log('📡 Statut des Service Accounts:', status);
        
        // Mettre à jour chaque cellule avec une icône
        status.service_accounts.forEach(saStatus => {
            const cell = document.querySelector(`.sa-credentials-status[data-sa-id="${saStatus.id}"]`);
            if (cell) {
                cell.style.opacity = '1';
                let icon = '';
                switch (saStatus.credentials_status) {
                    case 'file_exists':
                        icon = `<i class="bi bi-file-earmark-check-fill text-success" title="Credentials OK: ${saStatus.credentials_file || ''}"></i>`;
                        break;
                    case 'file_missing':
                        icon = `<i class="bi bi-file-earmark-x-fill text-danger" title="Fichier de credentials manquant"></i>`;
                        break;
                    case 'no_app':
                        icon = `<i class="bi bi-dash-circle text-muted" title="Pas d'application associée"></i>`;
                        break;
                    default:
                        icon = `<i class="bi bi-question-circle text-muted" title="Credentials: Inconnu"></i>`;
                }
                cell.innerHTML = icon;
            }
        });

        // Restaurer l'opacité
        credentialsCells.forEach(cell => {
            cell.style.opacity = '1';
        });
        
        // Afficher un résumé
        const summary = status.summary;
        if (summary.missing_credentials > 0) {
            showToast('Service Accounts', 
                `${summary.missing_credentials} SA sans fichier de credentials`, 
                'warning');
        }
    } catch (error) {
        console.error('Erreur lors de la vérification des SA:', error);
        credentialsCells.forEach(cell => {
            cell.style.opacity = '1';
        });
    }
}

function renderAppsTable(apps) {
    const container = document.getElementById('appsTable');
    const sortedApps = [...apps].sort(compareAdminAppsByName);
    const totalApps = allApps.length;
    const hasActiveFilters = Boolean(adminAppsSearchQuery.trim()) || adminAppsStatusFilter !== 'all' || adminAppsCategoryFilter !== 'all' || adminAppsWorkshopFilter !== 'false' || adminAppsUnmanagedFilter !== 'all';

    syncAdminAppsFilterControls();

    // Compter les apps pending pour le badge
    const pendingCount = allApps.filter(a => a.approval_status === 'pending' || a.approval_status === 'rejected').length;
    const badge = document.getElementById('pendingAppsBadge');
    if (badge) {
        if (pendingCount > 0) {
            badge.textContent = pendingCount;
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
    }

    const countLabel = hasActiveFilters
        ? `${sortedApps.length} application(s) sur ${totalApps}`
        : `${totalApps} application(s)`;

    if (totalApps === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4 mb-0">Aucune application configurée</p>';
        return;
    }

    if (sortedApps.length === 0) {
        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <small class="text-muted">${countLabel} • triées de A à Z</small>
                <button class="btn btn-sm btn-outline-secondary" data-action="refreshAppsStatus" title="Vérifier la connectivité">
                    <i class="bi bi-arrow-clockwise me-1"></i>Tester connectivité
                </button>
            </div>
            <div class="border rounded-3 bg-light text-center text-muted py-4 px-3">
                Aucune application ne correspond aux filtres actuels.
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <small class="text-muted">${countLabel} • triées de A à Z</small>
            <button class="btn btn-sm btn-outline-secondary" data-action="refreshAppsStatus" title="Vérifier la connectivité">
                <i class="bi bi-arrow-clockwise me-1"></i>Tester connectivité
            </button>
        </div>
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Application</th>
                    <th>Slug</th>
                    <th>URL Frontend</th>
                    <th>Utilisateurs</th>
                    <th class="text-center">Actif</th>
                    <th>État</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody id="appsTableBody">
                ${sortedApps.map(app => {
                    // Les ic\u00f4nes Docker/Health sont d'abord en \u00e9tat "inconnu"
                    // puis mises \u00e0 jour via SSE (docker_status_changed / app_health_changed)
                    let dockerIcon = '<i class="bi bi-question-circle text-muted" title="Docker: Chargement..."></i>';
                    let healthIcon = '<i class="bi bi-wifi text-muted" title="Connectivit\u00e9: V\u00e9rification..."></i>';
                    
                    // Badge approbation
                    let approvalBadge = '';
                    let approvalActions = '';
                    const approvalStatus = app.approval_status;
                    if (approvalStatus === 'pending') {
                        approvalBadge = '<span class="badge bg-warning text-dark ms-1">En attente</span>';
                        approvalActions = `
                            <button class="btn btn-sm btn-success me-1" data-action="approveApp" data-arg1="${escapeHtml(app.slug)}" title="Approuver">
                                <i class="bi bi-check-lg"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger me-1" data-app-name="${escapeHtml(app.name)}" data-action="showRejectAppModal" data-arg1="${escapeHtml(app.slug)}" data-arg2="$el" title="Rejeter">
                                <i class="bi bi-x-lg"></i>
                            </button>
                        `;
                    } else if (approvalStatus === 'rejected') {
                        approvalBadge = '<span class="badge bg-danger ms-1">Rejeté</span>';
                        approvalActions = `
                            <button class="btn btn-sm btn-success me-1" data-action="approveApp" data-arg1="${escapeHtml(app.slug)}" title="Approuver">
                                <i class="bi bi-check-lg"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-secondary me-1" data-action="deletePendingApp" data-arg1="${escapeHtml(app.slug)}" title="Supprimer">
                                <i class="bi bi-trash"></i>
                            </button>
                        `;
                    }
                    
                    // Toggle switch pour activation
                    const isPending = approvalStatus === 'pending' || approvalStatus === 'rejected';
                    const toggleSwitch = isPending ? '' : `
                        <div class="form-check form-switch d-flex justify-content-center mb-0">
                            <input class="form-check-input" type="checkbox" role="switch" 
                                id="toggleApp-${app.id}" 
                                ${app.is_active ? 'checked' : ''} 
                                data-action="toggleAppActive" data-arg1="${app.id}"
                                title="${app.is_active ? 'Désactiver' : 'Activer'}">
                        </div>
                    `;
                    
                    const rowClass = isPending ? 'table-warning' : (approvalStatus === 'rejected' ? 'table-danger' : '');
                    
                    return `
                    <tr data-app-id="${app.id}" class="${rowClass}">
                        <td>
                            <div class="d-flex align-items-center">
                                <span class="me-2 rounded p-1" style="background-color: ${app.color}; color: white;">
                                    <i class="${app.icon}"></i>
                                </span>
                                <div>
                                    <strong>${escapeHtml(app.name)}</strong>
                                    ${app.is_shared_service ? '<span class="badge bg-info ms-1" title="API partagée accessible par tous les SA"><i class="bi bi-share"></i></span>' : ''}
                                    ${app.is_workshop ? '<span class="badge bg-warning text-dark ms-1" title="Application atelier (APP_Workshop/)"><i class="bi bi-tools"></i> ATELIER</span>' : ''}
                                    ${app.is_unmanaged ? '<span class="badge bg-secondary ms-1" title="Application non gérée par le HUB (launcher-only)"><i class="bi bi-exclamation-octagon"></i> Non gérée</span>' : ''}
                                    ${approvalBadge}
                                </div>
                            </div>
                        </td>
                        <td><code>${app.slug}</code></td>
                        <td><small class="text-muted">${escapeHtml(app.frontend_url || '-')}</small></td>
                        <td><span class="badge bg-secondary">${app.users?.length || 0}</span></td>
                        <td class="text-center">${toggleSwitch}</td>
                        <td class="app-status-cell" data-app-slug="${app.slug}">
                            <div class="status-icons">
                                ${dockerIcon}
                                <span class="app-connectivity-icon">${healthIcon}</span>
                            </div>
                        </td>
                        <td class="text-end text-nowrap">
                            ${approvalActions}
                            ${!isPending ? `
                                ${app.git_repo_url && (!app.last_deployment || app.last_deployment.status !== 'success') ? `
                                    <button class="btn btn-sm btn-outline-success me-1" data-action="triggerDeploy" data-arg1="${app.slug}" title="Déployer automatiquement (clone, .env, build, démarrage)">
                                        <i class="bi bi-rocket-takeoff"></i>
                                    </button>
                                ` : ''}
                                <button class="btn btn-sm btn-outline-info me-1" data-action="showDockerDetails" data-arg1="${app.slug}" title="Détails Docker">
                                    <i class="bi bi-eye"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-primary me-1" data-action="editApp" data-arg1="${app.id}">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" data-app-name="${escapeHtml(app.name)}" data-action="deleteApp" data-arg1="${app.id}" data-arg2="$el">
                                    <i class="bi bi-trash"></i>
                                </button>
                            ` : ''}
                        </td>
                    </tr>
                `}).join('')}
            </tbody>
        </table>
    `;
    
    // Note: Les rafraîchissements de connectivité et stats Docker sont gérés
    // par startConnectivityRefresh() déclenché par l'événement shown.bs.modal
    // Ne pas les relancer ici pour éviter des appels API doublonnés
}

/**
 * Bascule l'état actif/inactif d'une application (toggle switch)
 */
async function toggleAppActive(appId) {
    const toggle = document.getElementById(`toggleApp-${appId}`);
    const originalState = !toggle.checked; // L'état vient de changer, on garde l'ancien
    
    try {
        const result = await api.toggleAppActive(appId);
        showToast('Mise à jour', result.message, 'success');
        
        // Rafraîchir le dashboard si la page est visible
        loadApps();
    } catch (error) {
        console.error('Erreur toggle app:', error);
        // Rollback le switch
        toggle.checked = originalState;
        showToast('Erreur', error.message, 'danger');
    }
}

function renderUsersTable(users) {
    const container = document.getElementById('usersTable');
    
    container.innerHTML = `
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Utilisateur</th>
                    <th>Email</th>
                    <th>Rôle Hub</th>
                    <th>Applications (Rôle)</th>
                    <th>Statut</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${users.map(user => `
                    <tr>
                        <td><strong>${escapeHtml(user.username)}</strong></td>
                        <td><small class="text-muted">${escapeHtml(user.email || '-')}</small></td>
                        <td>
                            ${user.is_admin 
                                ? '<span class="badge bg-dark">Admin</span>' 
                                : '<span class="badge bg-light text-dark">User</span>'}
                        </td>
                        <td>
                            ${user.app_roles?.map(ar => `
                                <span class="badge me-1" style="background-color: ${getRoleBadgeColor(ar.role)}">
                                    ${(ar.source || '').startsWith('profile') ? '<i class="bi bi-person-badge"></i> ' : ''}
                                    ${escapeHtml(ar.app_name)} 
                                    <small>(${ar.role})</small>
                                </span>
                            `).join('') || '<span class="text-muted">-</span>'}
                        </td>
                        <td>
                            ${user.is_active 
                                ? '<span class="status-active"><i class="bi bi-check-circle-fill"></i></span>' 
                                : '<span class="status-inactive"><i class="bi bi-x-circle-fill"></i></span>'}
                        </td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-primary me-1" data-action="editUser" data-arg1="${user.id}">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger" data-action="deleteUser" data-arg1="${user.id}" data-arg2="${escapeHtml(user.username)}" 
                                ${user.id === currentUser.id ? 'disabled' : ''}>
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// Couleurs des badges selon le rôle
function getRoleBadgeColor(role) {
    const colors = {
        'admin': '#dc3545',     // Rouge
        'manager': '#fd7e14',   // Orange
        'user': '#0d6efd',      // Bleu
        'viewer': '#6c757d',    // Gris
        'service': '#6f42c1'    // Violet
    };
    return colors[role] || '#0d6efd';
}
