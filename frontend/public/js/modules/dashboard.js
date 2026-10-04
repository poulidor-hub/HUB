/**
 * Hub Application - dashboard applications
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Applications Dashboard =====

// Filtre atelier : 'false' (prod), 'true' (ateliers), 'all' (tout)
let workshopFilter = 'false';

async function loadApps() {
    console.log('📱 loadApps() appelé, currentPage:', currentPage, 'workshopFilter:', workshopFilter);
    try {
        const apps = await api.getApps(workshopFilter);
        console.log('📱 loadApps: réponse reçue, nombre d\'apps:', Array.isArray(apps) ? apps.length : 'pas un tableau');
        // S'assurer que apps est un tableau
        if (Array.isArray(apps)) {
            dashboardApps = apps;
            filterDashboardApps();
            updateModeIndicator();
        } else {
            console.error('loadApps: réponse inattendue (pas un tableau):', apps);
            dashboardApps = [];
            renderAppsGrid([]);
        }
    } catch (error) {
        console.error('Failed to load apps:', error);
        // En cas d'erreur, afficher une grille vide plutôt que de laisser l'ancien contenu
        const grid = document.getElementById('appsGrid');
        if (grid) {
            grid.innerHTML = `
                <div class="col-12 empty-state">
                    <i class="bi bi-exclamation-triangle text-warning"></i>
                    <h4>Erreur de chargement</h4>
                    <p>Impossible de charger les applications. <a href="#" data-action="loadApps">Réessayer</a></p>
                </div>
            `;
        }
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
    }
}

function setWorkshopFilter(filter) {
    workshopFilter = filter;
    document.querySelectorAll('.workshop-filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-wfilter') === filter);
    });
    loadApps();
}

function setDashboardFilter(filter) {
    dashboardFilter = filter;
    document.querySelectorAll('.dash-filter-btn[data-filter]').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-filter') === filter);
    });
    filterDashboardApps();
}

function clearAppSearch() {
    const input = document.getElementById('appSearchInput');
    if (input) { input.value = ''; }
    filterDashboardApps();
}

function filterDashboardApps() {
    const searchInput = document.getElementById('appSearchInput');
    const clearBtn = document.getElementById('appSearchClear');
    const query = (searchInput ? searchInput.value : '').toLowerCase().trim();

    if (clearBtn) clearBtn.classList.toggle('d-none', !query);

    let filtered = dashboardApps;

    // Filtre par texte
    if (query) {
        filtered = filtered.filter(app =>
            app.name.toLowerCase().includes(query) ||
            (app.description || '').toLowerCase().includes(query) ||
            app.slug.toLowerCase().includes(query) ||
            (app.category || '').toLowerCase().includes(query) ||
            (app.group?.name || '').toLowerCase().includes(query)
        );
    }

    // Filtre actif ('all' | 'favorites' — les catégories se cherchent via la recherche)
    if (dashboardFilter === 'favorites') {
        filtered = filtered.filter(app => app.is_favorite);
    }

    renderAppsGrid(filtered);
}

async function toggleFavorite(event, appId) {
    event.stopPropagation();
    appId = parseInt(appId, 10);
    const app = dashboardApps.find(a => a.id === appId);
    if (!app) return;
    try {
        if (app.is_favorite) {
            await api.removeFavorite(appId);
            app.is_favorite = false;
        } else {
            await api.addFavorite(appId);
            app.is_favorite = true;
        }
        filterDashboardApps();
    } catch (e) {
        showToast('Erreur', 'Impossible de modifier le favori', 'danger');
    }
}

/**
 * Rend le markup d'une tuile d'application.
 * @param {object} app - L'application (payload GET /api/apps/)
 * @param {object} opts - { inModal: tuile rendue dans la modale de groupe,
 *                          showGroupBadge: afficher le badge du groupe (grille plate filtrée) }
 */
function renderAppTile(app, opts = {}) {
    const isAdmin = currentUser?.is_admin;
    // Déterminer si l'app est inactive (désactivée par l'admin) ou sans URL de lancement
    // Les statuts Docker/health arrivent en temps réel via SSE
    const hasLaunchUrl = Boolean(app.callback_url || app.frontend_url);
    const isUnavailable = !app.is_active || !hasLaunchUrl;
    const unavailableMessage = !app.is_active
        ? 'Cette application est désactivée.'
        : 'Cette application n’a pas d’URL de lancement configurée.';
    const appVersionLabel = formatAppVersionLabel(app.app_version);
    const favIcon = app.is_favorite ? 'bi-star-fill text-warning' : 'bi-star';
    // Badge d'état du dernier déploiement (bug 2026-10-03 : une app dont le
    // déploiement a échoué s'affichait comme les autres — carte active, rien
    // de fonctionnel derrière). last_deployment n'est exposé que pour
    // success/failed (cf. _load_last_deployments).
    const deployBadge = (() => {
        const ld = app.last_deployment;
        if (!ld || ld.status !== 'failed') return '';
        const when = ld.finished_at
            ? new Date(ld.finished_at).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
            : '';
        const title = 'Dernier déploiement en échec' + (when ? ' le ' + when : '') +
            ' — l\'application peut ne pas fonctionner. Redéploiement possible depuis l\'administration.';
        return `<span class="badge bg-danger ms-2" style="font-size:0.65em" title="${escapeHtml(title)}"><i class="bi bi-exclamation-triangle me-1"></i>Échec déploiement</span>`;
    })();
    // data-action de la carte : wrappers globaux (openAppFromCard / appCardUnavailable)
    const cardAction = !isUnavailable ? 'openAppFromCard' : 'appCardUnavailable';
    const cardArgs = !isUnavailable
        ? `data-arg1="${escapeHtml(app.slug)}" data-arg2="${opts.inModal ? '1' : ''}"`
        : `data-arg1="${escapeHtml(unavailableMessage || '')}"`;

    return `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card shadow-sm h-100 ${isUnavailable ? 'app-inactive' : ''}"
                 data-app-slug="${app.slug}"
                 data-action="${cardAction}" ${cardArgs}>
                <div class="card-body position-relative">
                    <button class="btn btn-sm btn-link favorite-btn" data-action="toggleFavorite" data-arg1="event" data-arg2="${app.id}" title="${app.is_favorite ? 'Retirer des favoris' : 'Ajouter aux favoris'}">
                        <i class="bi ${favIcon}"></i>
                    </button>
                    <div class="app-card-header">
                        <div class="app-icon" style="background-color: ${app.color}">
                            <i class="${app.icon}"></i>
                        </div>
                        <div class="app-card-info">
                            <h6>${escapeHtml(app.name)}${app.is_workshop ? '<span class="badge bg-warning text-dark ms-2" style="font-size:0.65em">ATELIER</span>' : ''}${deployBadge}</h6>
                            <p>${escapeHtml(app.description || 'Aucune description')}</p>
                        </div>
                    </div>
                    <div class="app-card-footer">
                        ${app.category ? `<span class="badge app-category-badge">${escapeHtml(app.category)}</span>` : (opts.showGroupBadge && app.group ? `<span class="badge app-group-badge"><i class="bi bi-folder2 me-1"></i>${escapeHtml(app.group.name)}</span>` : '<span></span>')}
                        ${appVersionLabel
                            ? `<span class="app-version-badge">${escapeHtml(appVersionLabel)}</span>`
                            : `<span class="app-version-badge app-version-unknown" title="Affichage de version non supporté par cette application">n/d</span>`}
                    </div>
                    ${isAdmin ? `
                        <div class="app-role-selector" data-action="noop">
                            <select class="form-select form-select-sm"
                                    id="appRole_${app.slug}"
                                    data-action="updateMyAppRole" data-arg1="${escapeHtml(app.slug)}" data-arg2="$el"
                                    title="Mon rôle sur cette application">
                                <option value="" ${!app.my_role ? 'selected' : ''}>-- Mon rôle --</option>
                                <option value="admin" ${app.my_role === 'admin' ? 'selected' : ''}>Admin</option>
                                <option value="manager" ${app.my_role === 'manager' ? 'selected' : ''}>Manager</option>
                                <option value="user" ${app.my_role === 'user' ? 'selected' : ''}>User</option>
                                <option value="viewer" ${app.my_role === 'viewer' ? 'selected' : ''}>Viewer</option>
                            </select>
                        </div>
                    ` : ''}
                </div>
            </div>
        </div>
    `;
}

/**
 * Rend le markup d'une tuile de groupe d'applications (conteneur purement visuel).
 * Le clic ouvre la modale listant les apps du groupe.
 */
function renderGroupTile(group, groupApps) {
    return `
        <div class="col-sm-6 col-md-4 col-lg-3">
            <div class="card app-card app-group-card shadow-sm h-100"
                 data-group-id="${group.id}"
                 data-action="openAppGroupModal" data-arg1="${group.id}">
                <div class="card-body position-relative">
                    <div class="app-card-header">
                        <div class="app-icon" style="background-color: ${group.color}">
                            <i class="${group.icon || 'bi-collection'}"></i>
                        </div>
                        <div class="app-card-info">
                            <h6>${escapeHtml(group.name)}</h6>
                            <p>${escapeHtml(group.description || 'Groupe d\'applications')}</p>
                        </div>
                    </div>
                    <div class="app-card-footer">
                        <span class="badge app-group-badge"><i class="bi bi-grid me-1"></i>${groupApps.length} application${groupApps.length > 1 ? 's' : ''}</span>
                        <span></span>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Ouvre la modale listant les applications d'un groupe.
 * Filtrage dynamique : seules les apps accessibles à l'utilisateur sont listées
 * (elles viennent de dashboardApps, déjà filtrées par le backend).
 */
function openAppGroupModal(groupId) {
    groupId = parseInt(groupId, 10);
    const groupApps = dashboardApps.filter(a => a.group && a.group.id === groupId);
    if (groupApps.length === 0) return;
    const group = groupApps[0].group;

    const modalEl = document.getElementById('appGroupModal');
    if (!modalEl) { console.warn('openAppGroupModal: modale #appGroupModal introuvable'); return; }

    document.getElementById('appGroupModalTitle').innerHTML = `
        <span class="app-group-modal-icon" style="background-color: ${group.color}"><i class="${group.icon || 'bi-collection'}"></i></span>
        ${escapeHtml(group.name)}
    `;
    document.getElementById('appGroupModalGrid').innerHTML =
        groupApps.map(app => renderAppTile(app, { inModal: true })).join('');
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
}

/** Ferme la modale de groupe (avant lancement d'une app) */
function closeAppGroupModal() {
    const modalEl = document.getElementById('appGroupModal');
    if (modalEl) {
        const inst = bootstrap.Modal.getInstance(modalEl);
        if (inst) inst.hide();
    }
}

function renderAppsGrid(apps) {
    const grid = document.getElementById('appsGrid');

    // Protection contre grid null (page pas encore chargée)
    if (!grid) {
        console.warn('renderAppsGrid: élément appsGrid non trouvé');
        return;
    }

    // Protection contre apps invalide
    if (!Array.isArray(apps)) {
        console.warn('renderAppsGrid: apps n\'est pas un tableau:', apps);
        apps = [];
    }

    const searchInput = document.getElementById('appSearchInput');
    const query = (searchInput ? searchInput.value : '').toLowerCase().trim();

    if (apps.length === 0) {
        const msg = dashboardFilter === 'favorites'
            ? `<i class="bi bi-star"></i><h4>Aucun favori</h4><p>Cliquez sur l'étoile d'une application pour l'ajouter à vos favoris.</p>`
            : `<i class="bi bi-inbox"></i><h4>Aucune application</h4><p>${query ? 'Aucun résultat pour cette recherche.' : 'Contactez un administrateur pour obtenir l\'accès aux applications.'}</p>`;
        grid.innerHTML = `<div class="col-12 empty-state">${msg}</div>`;
        return;
    }

    // Un filtre actif (recherche / favoris) "débloque" la grille :
    // on affiche toutes les apps à plat, y compris celles qui sont groupées.
    const filterActive = query !== '' || (dashboardFilter && dashboardFilter !== 'all');

    if (!filterActive) {
        // Vue par défaut : tuiles de groupes (alphabétique) puis apps hors groupe
        const groupsMap = new Map();
        const ungroupedApps = [];
        for (const app of apps) {
            if (app.group) {
                if (!groupsMap.has(app.group.id)) groupsMap.set(app.group.id, { group: app.group, apps: [] });
                groupsMap.get(app.group.id).apps.push(app);
            } else {
                ungroupedApps.push(app);
            }
        }
        const sortedGroups = [...groupsMap.values()]
            .sort((a, b) => a.group.name.localeCompare(b.group.name, 'fr', { sensitivity: 'base' }));
        const sortedUngrouped = [...ungroupedApps]
            .sort((a, b) => a.name.localeCompare(b.name, 'fr', { sensitivity: 'base' }));

        grid.innerHTML =
            sortedGroups.map(({ group, apps: ga }) => renderGroupTile(group, ga)).join('') +
            sortedUngrouped.map(app => renderAppTile(app)).join('');
        return;
    }

    // Grille plate (filtre actif) : badge du groupe sur les tuiles groupées
    grid.innerHTML = apps.map(app => renderAppTile(app, { showGroupBadge: true })).join('');
}

/**
 * Rafraîchit le token après une création/modification d'application
 * Affiche un spinner discret pendant l'opération
 * @param {string|null} actionType - Type d'action ('création', 'modification') ou null si silencieux
 * @param {boolean} silent - Si true, pas de toast (pour les événements SSE)
 */
async function refreshTokenAfterAppChange(actionType = null, silent = false) {
    // Créer et afficher un spinner discret dans la navbar
    const navbar = document.querySelector('.navbar-nav') || document.querySelector('.navbar');
    let spinner = null;
    
    if (navbar && !silent) {
        spinner = document.createElement('span');
        spinner.id = 'token-refresh-spinner';
        spinner.className = 'ms-2 d-inline-flex align-items-center';
        spinner.innerHTML = `
            <span class="spinner-border spinner-border-sm text-light me-1" role="status"></span>
            <small class="text-light opacity-75">Sync...</small>
        `;
        navbar.appendChild(spinner);
    }
    
    try {
        const tokenRefreshed = await api.tryRefreshToken();
        
        // Recharger aussi les données utilisateur pour synchroniser l'état local
        if (tokenRefreshed) {
            try {
                currentUser = await api.getCurrentUser();
                if (currentUser?.beacon_token) {
                    beaconToken = currentUser.beacon_token;
                }
            } catch (e) {
                console.log('Impossible de recharger currentUser:', e);
            }
        }
        
        if (!silent && actionType) {
            if (tokenRefreshed) {
                showToast('Session synchronisée', `Vos accès ont été mis à jour après la ${actionType}.`, 'info');
            } else {
                showToast('Info', `La ${actionType} est enregistrée. Reconnectez-vous pour accéder à l'app.`, 'warning');
            }
        }
        
        return tokenRefreshed;
    } catch (e) {
        console.log('Refresh token non disponible:', e);
        if (!silent && actionType) {
            showToast('Info', `La ${actionType} est enregistrée. Le nouvel accès sera actif à la prochaine connexion.`, 'info');
        }
        return false;
    } finally {
        // Supprimer le spinner
        if (spinner) {
            spinner.remove();
        }
    }
}

/**
 * Met à jour le rôle de l'admin courant sur une application
 */
async function updateMyAppRole(appSlug, el) {
    const role = el?.value || null;
    try {
        const result = await api.updateMyAppRole(appSlug, role || null);
        const roleText = role ? `défini à "${role}"` : 'supprimé';
        
        // Rafraîchir le token pour que le nouveau rôle soit pris en compte immédiatement
        // Le refresh token recharge les rôles depuis la base de données
        let tokenRefreshed = false;
        try {
            tokenRefreshed = await api.tryRefreshToken();
        } catch (e) {
            console.log('Refresh token non disponible, le nouveau rôle sera actif après re-login');
        }
        
        // IMPORTANT: Recharger la liste des applications pour refléter le nouveau rôle
        // Cela vide le cache implicite de l'UI et recharge depuis l'API
        await loadApps();
        
        // Message adapté selon que le token a été rafraîchi ou non
        if (tokenRefreshed) {
            showToast('Rôle mis à jour', `Votre rôle sur ${result.app_name} a été ${roleText}. Actif immédiatement.`, 'success');
        } else {
            showToast('Rôle mis à jour', `Votre rôle sur ${result.app_name} a été ${roleText}. Le nouveau rôle sera actif à la prochaine connexion à l'app.`, 'success');
        }
    } catch (error) {
        console.error('Erreur mise à jour rôle:', error);
        showToast('Erreur', error.message || 'Impossible de mettre à jour le rôle', 'danger');
        // Recharger pour remettre la bonne valeur
        await loadApps();
    }
}

/**
 * data-action des cartes app du dashboard : ouvre l'app
 * (et ferme le modal groupe si la carte est rendue dans un modal)
 */
function openAppFromCard(slug, inModal) {
    if (inModal) closeAppGroupModal();
    openApp(slug);
}

/**
 * data-action des cartes app indisponibles : toast explicite
 */
function appCardUnavailable(message) {
    showToast('Application indisponible', message || 'Cette application est indisponible', 'warning');
}

async function openApp(slug) {
    // Nettoyer les références aux fenêtres déjà fermées
    cleanupClosedWindows();

    // 1. Ouvrir la fenêtre IMMÉDIATEMENT (dans le contexte synchrone du clic)
    //    pour éviter le blocage par les popup blockers du navigateur
    const windowName = `hub_app_${slug}_${Date.now()}`;
    const appWindow = window.open('about:blank', windowName);

    if (!appWindow) {
        showToast('Erreur', 'Impossible d\'ouvrir l\'application (popup bloqué ?)', 'warning');
        return;
    }

    // Afficher une page de chargement dans la fenêtre en attendant le token
    try {
        appWindow.document.write(`
            <!DOCTYPE html>
            <html><head><title>Connexion...</title></head>
            <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:system-ui,sans-serif;background:#f8f9fa;">
                <div style="text-align:center;">
                    <div style="border:4px solid #e9ecef;border-top:4px solid #0d6efd;border-radius:50%;width:40px;height:40px;animation:spin 1s linear infinite;margin:0 auto 1rem;"></div>
                    <p style="color:#6c757d;margin:0;">Connexion à l'application...</p>
                </div>
                <style>@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}</style>
            </body></html>
        `);
    } catch (e) {
        // Ignorer si on ne peut pas écrire (cross-origin)
    }

    // Stocker la référence pour fermeture automatique au logout
    openedAppWindows.set(windowName, appWindow);

    try {
        // 2. Garder la session Hub fraîche sans dépendre du rôle app dans le JWT global
        await api.ensureFreshToken();

        // 3. Préparer un lancement dédié côté Hub avec un token frais pour cette app
        const launch = await api.createAppLaunch(slug);
        if (!launch?.launch_url || !launch?.access_token) {
            throw new Error('Réponse de lancement invalide');
        }

        // 4. Naviguer vers l'URL d'entrée de l'app, callback_url en priorité côté API
        const separator = launch.launch_url.includes('?') ? '&' : '?';
        appWindow.location.href = `${launch.launch_url}${separator}access_token=${encodeURIComponent(launch.access_token)}`;
        console.log(`Application ${slug} ouverte dans l'onglet ${windowName}`);
    } catch (e) {
        // En cas d'échec du lancement, garder une page explicite au lieu de fermer brutalement l'onglet
        console.error(`Échec ouverture app ${slug}:`, e);
        openedAppWindows.delete(windowName);

        try {
            const loginUrl = `${window.location.origin}/`;
            appWindow.document.open();
            appWindow.document.write(`
                <!DOCTYPE html>
                <html><head><title>Connexion requise</title></head>
                <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:system-ui,sans-serif;background:#f8f9fa;color:#212529;">
                    <div style="max-width:460px;padding:2rem;border:1px solid #dee2e6;border-radius:12px;background:white;box-shadow:0 10px 30px rgba(0,0,0,0.08);text-align:center;">
                        <h2 style="margin-top:0;margin-bottom:1rem;">Connexion au Hub requise</h2>
                        <p style="margin-bottom:1.5rem;color:#6c757d;">L'application n'a pas pu être ouverte car le lancement Hub n'a pas pu être préparé. Reconnectez-vous puis relancez l'application.</p>
                        <div style="display:flex;gap:0.75rem;justify-content:center;flex-wrap:wrap;">
                            <a href="${loginUrl}" style="padding:0.75rem 1rem;border:none;border-radius:8px;background:#0d6efd;color:white;cursor:pointer;text-decoration:none;display:inline-block;">Ouvrir le Hub</a>
                            <button id="closeWindowBtn" style="padding:0.75rem 1rem;border:1px solid #ced4da;border-radius:8px;background:white;color:#212529;cursor:pointer;">Fermer</button>
                        </div>
                    </div>
                    <script src="/js/window-close.js"></script>
                </body></html>
            `);
            appWindow.document.close();
        } catch (_) {
            try { appWindow.close(); } catch (__ ) {}
        }

        showToast('Erreur', 'Impossible d\'ouvrir l\'application. Reconnectez-vous au Hub puis réessayez.', 'danger');
    }
}

/**
 * Nettoie les références aux fenêtres qui ont été fermées manuellement
 */
function cleanupClosedWindows() {
    for (const [name, win] of openedAppWindows.entries()) {
        if (win.closed) {
            openedAppWindows.delete(name);
        }
    }
}

/**
 * Ferme tous les onglets d'applications ouverts par le Hub
 */
function closeAllAppWindows() {
    let closedCount = 0;
    for (const [name, win] of openedAppWindows.entries()) {
        if (win && !win.closed) {
            try {
                win.close();
                closedCount++;
                console.log(`Onglet ${name} fermé`);
            } catch (e) {
                console.warn(`Impossible de fermer l'onglet ${name}:`, e);
            }
        }
    }
    openedAppWindows.clear();
    return closedCount;
}

async function updateModeIndicator() {
    const bar = document.getElementById('modeIndicatorBar');
    const indicator = document.getElementById('modeIndicator');
    if (!bar || !indicator) return;
    
    // Afficher uniquement quand les ateliers sont visibles (jamais en PROD)
    if (workshopFilter === 'true') {
        bar.classList.remove('d-none');
        indicator.style.backgroundColor = '#fd7e14';
        indicator.style.color = '#fff';
        indicator.innerHTML = '<i class="bi bi-tools me-1"></i>Mode Atelier';
    } else if (workshopFilter === 'all') {
        bar.classList.remove('d-none');
        indicator.style.backgroundColor = '#6f42c1';
        indicator.style.color = '#fff';
        indicator.innerHTML = '<i class="bi bi-funnel me-1"></i>Toutes les applications (Prod + Atelier)';
    } else {
        bar.classList.add('d-none');
    }
}
