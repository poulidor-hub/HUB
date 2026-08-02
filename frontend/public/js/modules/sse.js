/**
 * Hub Application - SSE et mises à jour temps réel
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Server-Sent Events (SSE) pour mises à jour temps réel =====

// Polling de sécurité : resynchronise les badges toutes les 30s
// pour couvrir les cas où le SSE est silencieux (déconnexion, onglet arrière-plan, cooldown)
let _badgeRefreshInterval = null;

function startBadgePolling() {
    if (_badgeRefreshInterval) return;
    _badgeRefreshInterval = setInterval(refreshAllBadges, 30000);
}

function stopBadgePolling() {
    if (_badgeRefreshInterval) {
        clearInterval(_badgeRefreshInterval);
        _badgeRefreshInterval = null;
    }
}

/**
 * Initialise la connexion SSE pour recevoir les mises à jour en temps réel
 * Passe l'user_id en query param pour le tracking de session côté serveur
 * @param {boolean} isReconnect - true si c'est une reconnexion après une déconnexion
 */
function initEventSource(isReconnect = false) {
    // Guard contre les initialisations concurrentes
    if (_sseInitializing) return;
    
    // Fermer toute connexion existante
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    
    // Ne pas connecter si pas authentifié
    if (!api.token) {
        return;
    }
    
    _sseInitializing = true;
    try {
        // Passer l'user_id et beacon_token pour le tracking de session sécurisé
        const userId = currentUser?.id || '';
        let sseUrl = '/api/events/stream';
        if (userId && beaconToken) {
            sseUrl = `/api/events/stream?user_id=${userId}&beacon_token=${beaconToken}`;
        }
        eventSource = new EventSource(sseUrl);
        
        eventSource.onopen = () => {
            console.log('🔌 SSE connecté - mises à jour temps réel actives');
            
            // Si c'est une reconnexion après une déconnexion (ex: redémarrage serveur),
            // rafraîchir les données pour synchroniser l'état
            if (isReconnect && sseWasConnected) {
                console.log('🔄 Reconnexion SSE détectée - rafraîchissement des données...');
                refreshAfterReconnect();
            }
            sseWasConnected = true;
            startBadgePolling();
        };
        
        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleSSEEvent(data);
            } catch (e) {
                console.warn('SSE: erreur parsing message', e);
            }
        };
        
        eventSource.onerror = (error) => {
            console.warn('SSE: erreur de connexion, reconnexion dans 5s...', error);
            eventSource.close();
            // Reconnexion automatique après 5 secondes
            // Renouveler le beacon_token AVANT de reconnecter le SSE
            // (le secret serveur est régénéré après un redémarrage/reload)
            setTimeout(async () => {
                if (api.token && currentUser) {
                    try {
                        const userValid = await api.getCurrentUser().catch(() => null);
                        if (userValid) {
                            currentUser = userValid;
                            if (userValid.beacon_token) {
                                beaconToken = userValid.beacon_token;
                                console.log('🔑 Beacon token renouvelé avant reconnexion SSE');
                            }
                            initEventSource(true); // Reconnecter avec le nouveau token
                        } else {
                            // Session expirée, on tente quand même la reconnexion basique
                            initEventSource(true);
                        }
                    } catch (e) {
                        // API pas encore dispo, réessayer la reconnexion simple
                        initEventSource(true);
                    }
                }
            }, 5000);
        };
    } catch (e) {
        console.warn('SSE: impossible de créer la connexion', e);
    } finally {
        _sseInitializing = false;
    }
}

/**
 * Rafraîchit les données après une reconnexion SSE (ex: après redémarrage serveur)
 * Cela garantit que l'interface est synchronisée avec l'état du serveur
 */
async function refreshAfterReconnect() {
    try {
        // Vérifier d'abord que la session est toujours valide
        const userValid = await api.getCurrentUser().catch(() => null);
        if (!userValid) {
            console.log('⚠️ Session invalide après reconnexion - redirection vers login');
            handleSessionExpired('Votre session a expiré suite au redémarrage du serveur.');
            return;
        }
        
        // Mettre à jour currentUser
        currentUser = userValid;
        
        // Renouveler le beacon_token (régénéré côté serveur après redémarrage)
        if (userValid.beacon_token) {
            beaconToken = userValid.beacon_token;
            console.log('🔑 Beacon token renouvelé après reconnexion');
            
            // Reconnecter le SSE avec le nouveau beacon_token
            initEventSource(false);
        }
        
        // Rafraîchir les applications (page principale)
        if (currentPage === 'dashboard' || currentPage === 'apps') {
            await loadApps();
        }
        
        // Rafraîchir selon la page courante
        switch (currentPage) {
            case 'users':
                loadUsersPageActiveTab();
                break;
            case 'service-accounts':
                loadAdminData();
                break;
            case 'audit':
                loadAuditPageData();
                break;
            case 'apps-admin':
                loadAdminData();
                break;
        }
        
        // Resynchroniser tous les badges de notification (cloche, messages, audit,
        // apps en attente, demandes de compte) — des événements ont pu être ratés
        // pendant la déconnexion.
        refreshAllBadges();
        
        console.log('✅ Données rafraîchies après reconnexion SSE');
    } catch (e) {
        console.error('Erreur lors du rafraîchissement après reconnexion:', e);
    }
}

/**
 * Resynchronise tous les badges de notification depuis le serveur.
 * Appelé à la connexion, après une reconnexion SSE et au retour sur l'onglet,
 * afin que les notifications soient à jour sans avoir à ouvrir le menu concerné.
 */
function refreshAllBadges() {
    // Cloche de notifications utilisateur (tous les utilisateurs)
    loadUnreadMessageCount();
    
    // Badges réservés aux administrateurs
    if (currentUser?.is_admin) {
        updateNavMessagesBadge();
        updateErrorNotificationBadge();
        loadPendingAppsCount();
        loadPendingRequestsCount();
    }
}

/**
 * Ferme la connexion SSE
 */
function closeEventSource() {
    stopBadgePolling();
    if (eventSource) {
        eventSource.close();
        eventSource = null;
        console.log('🔌 SSE déconnecté');
    }
}

/**
 * Gère les événements SSE reçus
 */
function handleSSEEvent(data) {
    if (data.type === 'ping') {
        // Keep-alive, ignorer silencieusement
        return;
    }
    
    // L'événement 'connected' est maintenant géré par onopen + refreshAfterReconnect
    if (data.type === 'connected') {
        return;
    }
    
    console.log('📡 SSE event:', data.type, data.data);
    
    switch (data.type) {
        case 'apps_updated': {
            // Notification adaptée selon l'action
            let appAction = 'modifiée';
            let toastType = 'info';
            switch (data.data?.action) {
                case 'created':
                    appAction = 'ajoutée';
                    if (currentUser?.is_admin) loadPendingAppsCount();  // Nouvelle app en attente d'approbation
                    break;
                case 'deleted':
                    appAction = 'supprimée';
                    break;
                case 'approved':
                    appAction = 'approuvée';
                    toastType = 'success';
                    loadPendingAppsCount();  // Mettre à jour le badge
                    break;
                case 'rejected':
                    appAction = 'rejetée';
                    toastType = 'warning';
                    loadPendingAppsCount();  // Mettre à jour le badge
                    break;
                case 'docker_status_changed':
                    appAction = `Docker: ${data.data?.new_status || 'mis à jour'}`;
                    toastType = data.data?.new_status === 'running' ? 'success' : 'warning';
                    break;
                case 'toggled':
                    appAction = data.data?.is_active ? 'activée' : 'désactivée';
                    toastType = data.data?.is_active ? 'success' : 'warning';
                    break;
                case 'batch_import':
                    appAction = `lot importé (${data.data?.created || 0} créée(s))`;
                    toastType = 'success';
                    break;
            }
            const appName = data.data?.app_name || 'Application';
            showToast('Mise à jour', `${appName} ${appAction}`, toastType);
            
            // Debounce : regrouper les mises à jour SSE rapprochées (ex: import batch)
            // pour éviter des dizaines de requêtes API simultanées
            if (_sseAppsUpdateTimer) clearTimeout(_sseAppsUpdateTimer);
            _sseAppsUpdateTimer = setTimeout(() => {
                _sseAppsUpdateTimer = null;
                
                const needsTokenRefresh = ['created', 'updated', 'approved', 'toggled', 'batch_import'].includes(data.data?.action);
                
                const refreshUI = () => {
                    if (currentPage === 'apps-admin') {
                        loadAdminData();
                    } else {
                        loadApps();
                    }
                };
                
                if (needsTokenRefresh && !api._refreshPromise) {
                    refreshTokenAfterAppChange(null, true).then(refreshUI).catch(refreshUI);
                } else {
                    refreshUI();
                }
            }, SSE_DEBOUNCE_DELAY);
            break;
        }
            
        case 'docker_status_changed':
            // Événement dédié pour les changements de statut Docker
            if (currentPage === 'apps-admin') {
                updateAppDockerStatusInTable(data.data?.slug, data.data?.status);
            }
            // Aussi mettre à jour sur le dashboard
            updateAppCardStatus(data.data?.slug, data.data?.status, null);
            break;
        
        case 'app_health_changed':
            // Événement pour les changements de santé (health check)
            const healthAppSlug = data.data?.slug;
            const healthAppName = data.data?.name;
            const newHealthStatus = data.data?.health_status;
            const previousStatus = data.data?.previous_status;
            
            console.log(`🏥 Health status changed: ${healthAppSlug} -> ${newHealthStatus}`);
            
            // Mettre à jour le badge dans le tableau admin
            if (currentPage === 'apps-admin') {
                updateAppHealthStatusInTable(healthAppSlug, newHealthStatus);
            }
            
            // Mettre à jour la carte sur le dashboard
            updateAppCardStatus(healthAppSlug, null, newHealthStatus);
            
            // Notification
            if (newHealthStatus === 'unhealthy') {
                showToast('⚠️ Application hors ligne', `${healthAppName || healthAppSlug} ne répond plus`, 'warning');
            } else if (previousStatus === 'unhealthy' && newHealthStatus === 'healthy') {
                showToast('✅ Application en ligne', `${healthAppName || healthAppSlug} est de nouveau accessible`, 'success');
            }
            break;
            
        case 'users_updated':
            // Rafraîchir la liste des utilisateurs si on est sur cette page
            if (currentPage === 'users') {
                loadUsersPageData();
                allProfiles = [];
                loadProfilesTab(); // Mettre à jour les compteurs de profils
            }
            break;
            
        case 'service_accounts_updated':
            // Rafraîchir la liste des service accounts si on est sur cette page
            if (currentPage === 'service-accounts') {
                loadServiceAccounts();
            }
            break;
            
        case 'stats_updated':
            // Mettre à jour les statistiques affichées en temps réel
            updateStatsDisplay(data.data);
            break;
            
        case 'audit_updated':
            // Rafraîchir les données d'audit si on est sur cette page
            if (currentPage === 'audit') {
                // Rafraîchir les données d'audit sans toast (pour éviter le spam)
                loadAuditPageData();
            }
            // Mettre à jour aussi les stats si disponibles
            if (data.data?.stats) {
                updateStatsDisplay(data.data.stats);
            }
            // Mettre à jour le badge d'erreurs dans la nav
            if (currentUser?.is_admin) {
                updateErrorNotificationBadge();
            }
            break;

        case 'user_permissions_changed': {
            // Droits modifiés pour un utilisateur — rafraîchir token + apps si c'est nous
            if (currentUser && data.data?.user_id === currentUser.id) {
                console.log('🔑 Permissions modifiées pour l\'utilisateur courant, synchronisation...');
                // Debounce : les modifications en lot génèrent plusieurs événements
                if (_ssePermissionsUpdateTimer) clearTimeout(_ssePermissionsUpdateTimer);
                _ssePermissionsUpdateTimer = setTimeout(async () => {
                    _ssePermissionsUpdateTimer = null;
                    try {
                        await refreshTokenAfterAppChange('modification de droits', false);
                        await loadApps();
                    } catch (e) {
                        console.error('Erreur lors de la synchronisation des permissions:', e);
                    }
                }, SSE_DEBOUNCE_DELAY);
            }
            // Rafraîchir l'onglet users actif si l'admin est sur cette page
            if (currentPage === 'users') {
                loadUsersPageActiveTab();
            }
            break;
        }

        case 'errors_acknowledged':
            // Réinitialiser le badge de notifications d'erreurs
            errorsLastAcknowledgedAt = data.data?.acknowledged_at || new Date().toISOString();
            localStorage.setItem('errorsLastAcknowledgedAt', errorsLastAcknowledgedAt);
            updateNavErrorBadge(0);
            break;

        case 'backup_started': {
            const slug = data.data?.app_slug;
            if (!slug) break;
            runningBackups.set(slug, {
                backup_id: data.data.backup_id,
                app_name: data.data.app_name || slug,
                status: data.data.status || 'pending',
                progress_step: data.data.progress_step || 'queued',
                progress_percent: data.data.progress_percent ?? 0,
                started_at: data.data.started_at || data.data.created_at || new Date().toISOString(),
                avg_duration_seconds: data.data.avg_duration_seconds ?? null
            });
            renderRunningBackups();
            if (currentPage === 'app-backups') {
                loadAppBackupsOverview();
            }
            break;
        }

        case 'backup_progress': {
            const slug = data.data?.app_slug;
            if (!slug) break;
            const existing = runningBackups.get(slug) || {
                app_name: data.data.app_name || slug,
                started_at: data.data.started_at || new Date().toISOString()
            };
            existing.backup_id = data.data.backup_id || existing.backup_id;
            existing.status = data.data.status || 'running';
            existing.progress_step = data.data.progress_step || existing.progress_step;
            existing.progress_percent = data.data.progress_percent ?? existing.progress_percent ?? 0;
            if (data.data.app_name) existing.app_name = data.data.app_name;
            if (data.data.avg_duration_seconds !== undefined) existing.avg_duration_seconds = data.data.avg_duration_seconds;
            runningBackups.set(slug, existing);
            renderRunningBackups();
            break;
        }

        case 'backup_completed': {
            const slug = data.data?.app_slug;
            markBackupFinished(data.data?.backup_id);
            if (slug && runningBackups.has(slug)) {
                runningBackups.delete(slug);
                renderRunningBackups();
            }
            const okName = data.data?.app_name || slug || 'Application';
            const dur = data.data?.duration_human ? ` (${data.data.duration_human})` : '';
            showToast('Sauvegarde terminée', `${okName}${dur}`, 'success');
            if (currentPage === 'app-backups') {
                loadAppBackupsOverview();
                if (appBackupsListModal && document.getElementById('backupsListAppSlug')?.value === slug) {
                    showAppBackupsList(slug);
                }
            }
            break;
        }

        case 'backup_failed': {
            const slug = data.data?.app_slug;
            markBackupFinished(data.data?.backup_id);
            if (slug && runningBackups.has(slug)) {
                runningBackups.delete(slug);
                renderRunningBackups();
            }
            const failName = data.data?.app_name || slug || 'Application';
            const reason = data.data?.error_message || 'Erreur inconnue';
            const kind = data.data?.status === 'timeout' ? 'Timeout sauvegarde' : 'Échec sauvegarde';
            showToast(kind, `${failName} : ${reason}`, 'danger');
            if (currentPage === 'app-backups') {
                loadAppBackupsOverview();
            }
            break;
        }

        case 'backup_clusters_updated':
            if (currentPage === 'app-backups') {
                loadBackupClusters();
            }
            break;

        case 'force_logout':
            // Vérifier si c'est l'utilisateur courant qui doit être déconnecté
            if (currentUser && data.data?.user_id === currentUser.id) {
                handleForceLogout(data.data.reason);
            }
            break;

        case 'user_message':
            handleIncomingMessage(data.data);
            break;

        case 'messages_updated':
            // Toujours resynchroniser la cloche utilisateur (un message peut avoir
            // été supprimé/archivé/expiré côté serveur).
            loadUnreadMessageCount();
            // Mettre à jour le badge nav admin même hors de la page Messages.
            if (currentUser?.is_admin) {
                updateNavMessagesBadge();
                // Rafraîchir la liste complète seulement si la page est ouverte.
                if (currentPage === 'messages') {
                    loadMessagesPage();
                }
            }
            break;

        case 'account_requests_updated':
            // Rafraîchir l'onglet demandes si l'admin est sur la page users
            if (currentPage === 'users' && currentUser?.is_admin) {
                loadPendingRequestsCount();
                // Si l'onglet demandes est actif, rafraîchir la liste
                const reqTab = document.querySelector('#tabAccountRequests');
                if (reqTab && reqTab.classList.contains('show')) {
                    loadAccountRequestsTab();
                }
            }
            break;

        case 'account_request_notification_admins_updated':
            if (currentPage === 'users' && currentUser?.is_admin) {
                loadPendingRequestsCount();
                const reqTab = document.querySelector('#tabAccountRequests');
                if (reqTab && reqTab.classList.contains('show')) {
                    loadAccountRequestsTab();
                }
            }
            break;

        case 'settings_updated':
            // Mettre à jour le toggle admin si l'onglet demandes est visible
            if (data.data?.key === 'account_requests_enabled') {
                const toggle = document.getElementById('toggleAccountRequests');
                if (toggle) toggle.checked = data.data.value === 'true';
            }
            break;
    }
}

/**
 * Met à jour l'affichage des statistiques en temps réel (sans recharger depuis l'API)
 */
function updateStatsDisplay(stats) {
    // Mettre à jour les compteurs si les éléments existent sur la page
    const statsElements = {
        'stat-total-apps': stats.total_apps,
        'stat-active-apps': stats.active_apps,
        'stat-total-users': stats.total_users,
        'stat-total-sa': stats.total_service_accounts,
        'stat-active-sessions': stats.active_sessions,
        'stat-sse-clients': stats.connected_sse_clients
    };
    
    for (const [elementId, value] of Object.entries(statsElements)) {
        const element = document.getElementById(elementId);
        if (element && value !== undefined) {
            // Animation de mise à jour
            element.classList.add('stats-updating');
            element.textContent = value;
            setTimeout(() => element.classList.remove('stats-updating'), 300);
        }
    }
    
    // Mettre à jour aussi les cartes de stats du dashboard si présentes
    updateDashboardStats(stats);
}

/**
 * Met à jour les cartes de statistiques du dashboard
 */
function updateDashboardStats(stats) {
    // Stats des applications sur le dashboard
    const appsCountEl = document.querySelector('[data-stat="apps-count"]');
    if (appsCountEl) {
        appsCountEl.textContent = stats.total_apps || 0;
    }
    
    const activeAppsEl = document.querySelector('[data-stat="active-apps"]');
    if (activeAppsEl) {
        activeAppsEl.textContent = stats.active_apps || 0;
    }
    
    const usersCountEl = document.querySelector('[data-stat="users-count"]');
    if (usersCountEl) {
        usersCountEl.textContent = stats.total_users || 0;
    }
    
    const sessionsEl = document.querySelector('[data-stat="active-sessions"]');
    if (sessionsEl) {
        sessionsEl.textContent = stats.active_sessions || 0;
    }
}

/**
 * Gestion du callback SSO - persisté en sessionStorage pour survivre aux reloads
 */
