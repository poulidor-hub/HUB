// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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
    const previousWorkshopFilter = adminAppsWorkshopFilter;
    adminAppsSearchQuery = document.getElementById('adminAppsSearch')?.value || '';
    adminAppsStatusFilter = document.getElementById('adminAppsStatusFilter')?.value || 'all';
    adminAppsCategoryFilter = document.getElementById('adminAppsCategoryFilter')?.value || 'all';
    adminAppsWorkshopFilter = document.getElementById('adminAppsWorkshopFilter')?.value || 'false';
    adminAppsUnmanagedFilter = document.getElementById('adminAppsUnmanagedFilter')?.value || 'all';

    // Le filtre Mode est appliqué côté serveur (api.getAllApps(filter)) :
    // changer le sélecteur doit relancer loadAdminData(), sinon on ne
    // refiltre qu'une liste déjà amputée par l'ancien filtre (« Atelier »
    // afficherait 0 après un fetch initial en Production). La recherche
    // texte et les autres filtres restent des refiltrages client.
    if (adminAppsWorkshopFilter !== previousWorkshopFilter) {
        loadAdminData().catch(error => {
            console.error('filterAdminApps: re-fetch après changement de Mode:', error);
            showToast('Erreur', error.message, 'danger');
        });
        return;
    }

    syncAdminAppsFilterControls();
    renderAppsTable(getFilteredAdminApps());
}

/**
 * Clic sur le badge « apps en attente » de l'onglet : filtre le tableau sur
 * les pending AVEC les ateliers visibles (masqués par défaut). Sans cela, un
 * atelier pending (ex. fantôme après suppression hors API) est compté par le
 * badge sans jamais être visible ni actionnable — faux « à approuver » permanent.
 * L'activation de l'onglet Applications reste assurée par le bouton parent
 * (data-bs-toggle="tab"), le clic du badge bouillonnant vers lui.
 */
function focusPendingApps() {
    const statusSelect = document.getElementById('adminAppsStatusFilter');
    const workshopSelect = document.getElementById('adminAppsWorkshopFilter');
    if (statusSelect) statusSelect.value = 'pending';
    if (workshopSelect) workshopSelect.value = 'all';
    filterAdminApps();
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
    // Le reset peut changer le Mode (cf. filterAdminApps) : re-fetch serveur
    // nécessaire, sinon la liste affichée reste celle de l'ancien filtre.
    const workshopFilterChanged = adminAppsWorkshopFilter !== 'false';
    adminAppsSearchQuery = '';
    adminAppsStatusFilter = 'all';
    adminAppsCategoryFilter = 'all';
    adminAppsWorkshopFilter = 'false';
    adminAppsUnmanagedFilter = 'all';
    syncAdminAppsFilterControls();
    if (workshopFilterChanged) {
        loadAdminData().catch(error => {
            console.error('resetAdminAppsFilters: re-fetch après reset du Mode:', error);
            showToast('Erreur', error.message, 'danger');
        });
        return;
    }
    renderAppsTable(getFilteredAdminApps());
}

// ===== Status Monitoring =====

/**
 * État d'exécution par slug : {health, docker, latency_ms, error}.
 * Alimenté par le test manuel de connectivité (refreshAppsStatus, bouton
 * « Tester connectivité » + appel initial à l'ouverture) et par les SSE
 * docker_status_changed / app_health_changed. La pastille composite de la
 * colonne État est re-rendue depuis cet état combiné (santé prioritaire
 * visuellement, Docker dans le title).
 */
const runtimeStates = {};

/**
 * Pastille composite de la colonne État : « En ligne » (vert) /
 * « Hors ligne » (rouge) / « Vérification » (gris, état inconnu).
 * Le title combine connectivité (+ latence), Docker et erreur de sonde.
 */
function appRuntimeStateHtml(state) {
    const health = state?.health || null;   // 'healthy' | 'unhealthy' | null
    const docker = state?.docker || null;   // 'running' | autre statut | null
    const latency = state?.latency_ms || null;
    const probeError = state?.error || null;

    let tone;
    let label;
    if (health === 'healthy') {
        tone = 'text-success';
        label = 'En ligne';
    } else if (health === 'unhealthy') {
        tone = 'text-danger';
        label = 'Hors ligne';
    } else {
        tone = 'text-muted';
        label = 'Vérification';
    }

    const titleParts = [];
    if (health === 'healthy') {
        titleParts.push('Connectivité : en ligne' + (latency ? ` (${latency} ms)` : ''));
    } else if (health === 'unhealthy') {
        titleParts.push('Connectivité : hors ligne' + (probeError ? ` — ${probeError}` : ''));
    } else {
        titleParts.push('Connectivité : non vérifiée');
    }
    titleParts.push(docker ? `Docker : ${docker}` : 'Docker : inconnu');

    return '<span class="d-inline-flex align-items-center gap-1 ' + tone + '"'
        + ' title="' + escapeHtml(titleParts.join(' · ')) + '">'
        + '<span aria-hidden="true" style="width:.5rem;height:.5rem;border-radius:50%;background-color:currentColor;display:inline-block;flex:0 0 auto;"></span>'
        + '<span>' + label + '</span>'
        + '</span>';
}

/**
 * Re-rend la pastille composite d'une app (cellule .app-status-cell —
 * même cible que les hooks SSE).
 */
function renderAppRuntimeState(slug) {
    if (!slug) return;
    const cell = document.querySelector(`.app-status-cell[data-app-slug="${slug}"]`);
    if (!cell) return;
    cell.innerHTML = appRuntimeStateHtml(runtimeStates[slug]);
}

/**
 * Rafraîchit le statut de connectivité des applications (test manuel ou
 * appel initial à l'ouverture) et alimente runtimeStates.
 */
async function refreshAppsStatus() {
    try {
        const status = await api.getAppsStatus();
        console.log('📡 Statut des applications:', status);

        (Array.isArray(status?.apps) ? status.apps : []).forEach(appStatus => {
            if (!appStatus?.slug) return;
            runtimeStates[appStatus.slug] = {
                ...(runtimeStates[appStatus.slug] || {}),
                health: appStatus.status === 'online' ? 'healthy' : 'unhealthy',
                latency_ms: appStatus.status === 'online' ? (appStatus.response_time_ms || null) : null,
                error: appStatus.status === 'online' ? null : (appStatus.error || null)
            };
            renderAppRuntimeState(appStatus.slug);
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
        // En cas d'erreur réseau, conserver le dernier état connu (pastilles inchangées)
    }
}

/**
 * Met à jour le statut Docker d'une app dans le tableau admin (hook SSE
 * docker_status_changed) : alimente runtimeStates puis re-rend la pastille
 * composite (Docker visible dans le title).
 */
function updateAppDockerStatusInTable(slug, dockerStatus) {
    if (!slug) return;

    runtimeStates[slug] = { ...(runtimeStates[slug] || {}), docker: dockerStatus || null };
    renderAppRuntimeState(slug);

    console.log(`🔄 Docker status mis à jour pour ${slug}: ${dockerStatus}`);
}

/**
 * Met à jour le statut de santé (health check) d'une app dans le tableau
 * admin (hook SSE app_health_changed) : alimente runtimeStates puis re-rend
 * la pastille composite (la santé est prioritaire visuellement).
 */
function updateAppHealthStatusInTable(slug, healthStatus) {
    if (!slug) return;

    runtimeStates[slug] = {
        ...(runtimeStates[slug] || {}),
        health: healthStatus === 'healthy' || healthStatus === 'unhealthy' ? healthStatus : null
    };
    renderAppRuntimeState(slug);

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

/**
 * Clic sur une ligne active : ouvre la fiche application (openAppDetail,
 * fournie par le module de fiche — passe 2). Les lignes pending/rejected ne
 * sont pas cliquables (flux d'approbation uniquement via leurs boutons) et
 * les clics interactifs (boutons, toggle switch, menu déroulant) sont
 * exclus pour ne pas déclencher la fiche. Liaison unique au conteneur
 * #appsTable — elle survit aux re-rendus innerHTML.
 */
function bindAppsTableRowClick(container) {
    if (!container || container.dataset.rowClickBound === '1') return;
    container.dataset.rowClickBound = '1';
    container.addEventListener('click', event => {
        if (event.target.closest('button, a, input, select, textarea, label, .form-check, .dropdown-menu, [data-action]')) return;
        const row = event.target.closest('tr[data-app-slug]');
        if (!row) return;
        const slug = row.getAttribute('data-app-slug');
        if (!slug) return;
        // Garde-fou : la fiche application (openAppDetail) est fournie par le
        // module dédié ; sans elle, le clic-ligne ne fait rien de cassé.
        if (typeof openAppDetail !== 'function') {
            console.warn('[admin-apps] Fiche application indisponible (openAppDetail non chargée).');
            return;
        }
        openAppDetail(slug);
    });
}

/**
 * Ligne du tableau (5 colonnes) : Application (pastille + nom + badges de
 * workflow), Version, État (pastille composite), Actif (toggle), Actions.
 * Les badges ATELIER et « Non gérée » ont quitté le tableau : l'info vit
 * désormais dans la fiche application (openAppDetail).
 */
function renderAppsTableRow(app) {
    const approvalStatus = app.approval_status;
    const isPending = approvalStatus === 'pending';
    const isRejected = approvalStatus === 'rejected';
    const needsApproval = isPending || isRejected;

    // Badge de workflow : pending/rejeté restent visibles dans le tableau.
    let approvalBadge = '';
    if (isPending) {
        approvalBadge = '<span class="badge bg-warning text-dark ms-1">En attente</span>';
    } else if (isRejected) {
        approvalBadge = '<span class="badge bg-danger ms-1">Rejeté</span>';
    }

    // Actions : flux d'approbation pour pending/rejected (pas de clic-ligne),
    // fiche + menu contextuel pour les autres.
    let actionsHtml;
    if (isPending) {
        actionsHtml = `
            <button class="btn btn-sm btn-success me-1" data-action="approveApp" data-arg1="${escapeHtml(app.slug)}" title="Approuver">
                <i class="bi bi-check-lg"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger me-1" data-app-name="${escapeHtml(app.name)}" data-action="showRejectAppModal" data-arg1="${escapeHtml(app.slug)}" data-arg2="$el" title="Rejeter">
                <i class="bi bi-x-lg"></i>
            </button>
            <button class="btn btn-sm btn-outline-secondary" data-action="deletePendingApp" data-arg1="${escapeHtml(app.slug)}" title="Supprimer">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else if (isRejected) {
        actionsHtml = `
            <button class="btn btn-sm btn-success me-1" data-action="approveApp" data-arg1="${escapeHtml(app.slug)}" title="Approuver">
                <i class="bi bi-check-lg"></i>
            </button>
            <button class="btn btn-sm btn-outline-secondary" data-action="deletePendingApp" data-arg1="${escapeHtml(app.slug)}" title="Supprimer">
                <i class="bi bi-trash"></i>
            </button>
        `;
    } else {
        // Déployer : même condition d'affichage que l'ancien bouton ; les
        // prechecks/bloqueurs restent gérés par triggerDeploy (admin-app-deploy.js).
        const canDeploy = Boolean(app.git_repo_url) && (!app.last_deployment || app.last_deployment.status !== 'success');
        actionsHtml = `
            <button class="btn btn-sm btn-primary me-1" data-action="openAppDetail" data-arg1="${escapeHtml(app.slug)}" title="Ouvrir la fiche de l'application">
                ${hubIcon('eye')}<span class="ms-1">Fiche</span>
            </button>
            <div class="dropdown d-inline-block">
                <button class="btn btn-sm btn-secondary" type="button" data-bs-toggle="dropdown" aria-expanded="false" title="Autres actions">
                    <i class="bi bi-three-dots"></i>
                </button>
                <ul class="dropdown-menu dropdown-menu-end">
                    ${canDeploy ? `
                    <li>
                        <button class="dropdown-item" type="button" data-action="triggerDeploy" data-arg1="${escapeHtml(app.slug)}" title="Déployer automatiquement (clone, .env, build, démarrage)">
                            ${hubIcon('rocket')}<span class="ms-1">Déployer</span>
                        </button>
                    </li>
                    ` : ''}
                    <li>
                        <button class="dropdown-item text-danger" type="button" data-app-name="${escapeHtml(app.name)}" data-action="deleteApp" data-arg1="${app.id}" data-arg2="$el" title="Supprimer l'application">
                            <i class="bi bi-trash me-1"></i>Supprimer
                        </button>
                    </li>
                </ul>
            </div>
        `;
    }

    // Toggle switch : masqué pour pending/rejected (comme avant)
    const toggleSwitch = needsApproval ? '' : `
        <div class="form-check form-switch d-flex justify-content-center mb-0">
            <input class="form-check-input" type="checkbox" role="switch"
                id="toggleApp-${app.id}"
                ${app.is_active ? 'checked' : ''}
                data-action="toggleAppActive" data-arg1="${app.id}"
                title="${app.is_active ? 'Désactiver' : 'Activer'}">
        </div>
    `;

    const rowClass = isPending ? 'table-warning' : (isRejected ? 'table-danger' : '');

    // Clic-ligne → fiche : uniquement les lignes hors workflow d'approbation
    // (voir bindAppsTableRowClick).
    const rowAttrs = needsApproval
        ? ''
        : ` data-app-slug="${escapeHtml(app.slug)}" title="Ouvrir la fiche" style="cursor:pointer"`;

    return `
        <tr data-app-id="${app.id}"${rowAttrs} class="${rowClass}">
            <td>
                <div class="d-flex align-items-center">
                    <span class="me-2 rounded p-1" style="background-color: ${app.color}; color: white;">
                        <i class="${app.icon}"></i>
                    </span>
                    <div>
                        <strong>${escapeHtml(app.name)}</strong>
                        ${app.is_shared_service ? '<span class="badge bg-info text-dark ms-1" title="API partagée accessible par tous les SA">partagé</span>' : ''}
                        ${approvalBadge}
                    </div>
                </div>
            </td>
            <td>${app.version ? `<code>${escapeHtml(app.version)}</code>` : '<span class="text-muted">—</span>'}</td>
            <td class="app-status-cell" data-app-slug="${escapeHtml(app.slug)}">${appRuntimeStateHtml(runtimeStates[app.slug])}</td>
            <td class="text-center">${toggleSwitch}</td>
            <td class="text-end text-nowrap">${actionsHtml}</td>
        </tr>
    `;
}

function renderAppsTable(apps) {
    const container = document.getElementById('appsTable');
    bindAppsTableRowClick(container);
    const sortedApps = [...apps].sort(compareAdminAppsByName);
    const totalApps = allApps.length;
    const hasActiveFilters = Boolean(adminAppsSearchQuery.trim()) || adminAppsStatusFilter !== 'all' || adminAppsCategoryFilter !== 'all' || adminAppsWorkshopFilter !== 'false' || adminAppsUnmanagedFilter !== 'all';

    syncAdminAppsFilterControls();

    // Badge « en attente » : pending uniquement, via l'updater unifié
    // (même sémantique que loadPendingAppsCount côté SSE/login).
    // Les apps rejetées ne gonflent pas le badge : filtre « Rejetées » dédié.
    updatePendingAppsBadge(allApps.filter(a => a.approval_status === 'pending').length);

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
                    <th>Version</th>
                    <th>État</th>
                    <th class="text-center">Actif</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody id="appsTableBody">
                ${sortedApps.map(app => renderAppsTableRow(app)).join('')}
            </tbody>
        </table>
    `;

    // État (connectivité/Docker) : les pastilles composites sont alimentées
    // par le test manuel « Tester connectivité » (refreshAppsStatus, aussi
    // appelé à l'ouverture) et par les SSE docker_status_changed /
    // app_health_changed — aucun polling automatique ici.
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
