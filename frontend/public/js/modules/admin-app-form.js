/**
 * Hub Application - formulaire application, Docker details et icônes
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== App CRUD =====

async function showAddAppForm() {
    // Charger le host du Hub si pas encore fait
    if (!hubHost) {
        try {
            const config = await api.getHubHost();
            hubHost = config.host;
        } catch (e) {
            console.error('Impossible de récupérer le host du Hub:', e);
            hubHost = window.location.hostname;
        }
    }
    
    document.getElementById('appFormTitle').textContent = 'Nouvelle Application';
    document.getElementById('appForm').reset();
    document.getElementById('appId').value = '';
    document.getElementById('appMode').value = 'create';
    document.getElementById('appName').required = true;
    document.getElementById('appIcon').value = 'bi-app';
    document.getElementById('appColor').value = '#0d6efd';
    document.getElementById('appActive').checked = true;
    document.getElementById('appRequiresAuth').checked = true;
    document.getElementById('appCustomHost').checked = false;
    document.getElementById('customHostGroup').classList.add('d-none');
    document.getElementById('appSlug').readOnly = true;
    document.getElementById('slugHint').textContent = 'Généré automatiquement';
    document.getElementById('appFrontendHttps').value = 'false';
    
    // Afficher les éléments de création
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.remove('d-none');
    
    // Mode simplifié (ports)
    document.getElementById('simplePortsSection').classList.remove('d-none');
    document.getElementById('advancedUrlsSection').classList.add('d-none');
    document.getElementById('appApiPort').required = true;
    document.getElementById('appApiUrl').required = false;
    
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    updateAppFormPreview();
    updateUrlPreview();
    appFormModal.show();
}

// ===== Docker Details Modal =====

/**
 * Construit la carte HTML des stats CPU/RAM/Uptime d'un conteneur
 */
function buildContainerStatsCard(stats) {
    const cpuPercent = (stats.cpu_percent || 0).toFixed(1);
    const memUsage = (stats.memory_usage_mb || 0).toFixed(1);
    const memLimit = (stats.memory_limit_mb || 0).toFixed(0);
    const memPercent = memLimit > 0 ? ((stats.memory_usage_mb / stats.memory_limit_mb) * 100).toFixed(1) : 0;
    const uptimeSec = stats.uptime_seconds || 0;

    // Couleur selon le pourcentage
    function barColor(pct) {
        if (pct >= 80) return 'bg-danger';
        if (pct >= 50) return 'bg-warning';
        return 'bg-success';
    }

    // Formatage uptime
    let uptimeStr = '';
    if (uptimeSec >= 86400) {
        const days = Math.floor(uptimeSec / 86400);
        const hours = Math.floor((uptimeSec % 86400) / 3600);
        uptimeStr = days + 'j ' + hours + 'h';
    } else if (uptimeSec >= 3600) {
        const hours = Math.floor(uptimeSec / 3600);
        const mins = Math.floor((uptimeSec % 3600) / 60);
        uptimeStr = hours + 'h ' + mins + 'min';
    } else {
        uptimeStr = Math.floor(uptimeSec / 60) + 'min';
    }

    return '<div class="card mb-3">'
        + '<div class="card-header py-2"><i class="bi bi-speedometer2 me-2"></i>Ressources</div>'
        + '<div class="card-body py-2">'
        + '<div class="mb-2">'
        + '<div class="d-flex justify-content-between"><small>CPU</small><small>' + cpuPercent + '%</small></div>'
        + '<div class="progress" style="height: 8px;">'
        + '<div class="progress-bar ' + barColor(parseFloat(cpuPercent)) + '" style="width: ' + cpuPercent + '%;"></div>'
        + '</div>'
        + '</div>'
        + '<div class="mb-2">'
        + '<div class="d-flex justify-content-between"><small>RAM</small><small>' + memUsage + ' / ' + memLimit + ' Mo (' + memPercent + '%)</small></div>'
        + '<div class="progress" style="height: 8px;">'
        + '<div class="progress-bar ' + barColor(parseFloat(memPercent)) + '" style="width: ' + memPercent + '%;"></div>'
        + '</div>'
        + '</div>'
        + '<div class="d-flex justify-content-between">'
        + '<small class="text-muted">Uptime</small>'
        + '<small><i class="bi bi-clock me-1"></i>' + uptimeStr + '</small>'
        + '</div>'
        + '</div>'
        + '</div>';
}

/**
 * Affiche les détails Docker d'une application
 */
async function showDockerDetails(slug) {
    // Créer la modal si elle n'existe pas
    let modalEl = document.getElementById('dockerInfoModal');
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.className = 'modal fade';
        modalEl.id = 'dockerInfoModal';
        modalEl.tabIndex = -1;
        modalEl.innerHTML = `
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            <i class="bi bi-box me-2"></i>Informations Docker
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body" id="dockerInfoContent">
                        <div class="text-center py-4">
                            <div class="spinner-border text-primary" role="status"></div>
                            <p class="mt-2 text-muted">Chargement...</p>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modalEl);
    }
    
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    // Réinitialiser le contenu avec le spinner de chargement
    document.getElementById('dockerInfoContent').innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status"></div>
            <p class="mt-2 text-muted">Chargement...</p>
        </div>
    `;
    modal.show();
    
    const content = document.getElementById('dockerInfoContent');
    
    try {
        const info = await api.getAppDockerInfo(slug);
        console.log('🐳 Docker info:', info);
        
        if (!info.container_found) {
            const dockerOff = info.docker_available === false;
            const alertClass = dockerOff ? 'alert-info' : 'alert-warning';
            const alertIcon = dockerOff ? 'bi-shield-lock' : 'bi-exclamation-triangle';
            const alertTitle = dockerOff ? 'Scan Docker désactivé' : 'Conteneur non trouvé';
            const alertMessage = dockerOff
                ? (info.developer_info?.warning || 'Le socket Docker n\'est pas monté dans le Hub.')
                : (info.developer_info?.warning || 'Le conteneur Docker n\'a pas été trouvé.');
            content.innerHTML = `
                <div class="alert ${alertClass}">
                    <i class="bi ${alertIcon} me-2"></i>
                    <strong>${alertTitle}</strong>
                    <p class="mb-0 mt-2">${alertMessage}</p>
                    ${info.developer_info?.tip ? `<small class="text-muted">${info.developer_info.tip}</small>` : ''}
                </div>
                <div class="card">
                    <div class="card-header"><i class="bi bi-info-circle me-2"></i>Informations Application</div>
                    <div class="card-body">
                        <table class="table table-sm mb-0">
                            <tr><th style="width: 150px;">Slug</th><td><code>${escapeHtml(info.app_slug)}</code></td></tr>
                            <tr><th>Nom</th><td>${escapeHtml(info.app_name)}</td></tr>
                            <tr><th>URL API</th><td><a href="${escapeHtml(info.app_api_url)}" target="_blank">${escapeHtml(info.app_api_url)}</a></td></tr>
                            ${info.app_frontend_url ? `<tr><th>URL Frontend</th><td><a href="${escapeHtml(info.app_frontend_url)}" target="_blank">${escapeHtml(info.app_frontend_url)}</a></td></tr>` : ''}
                        </table>
                    </div>
                </div>
            `;
            return;
        }
        
        // Conteneur trouvé - afficher toutes les infos
        const statusBadge = info.container_status === 'running' 
            ? '<span class="badge bg-success"><i class="bi bi-play-circle me-1"></i>Running</span>'
            : '<span class="badge bg-warning text-dark"><i class="bi bi-pause-circle me-1"></i>' + escapeHtml(info.container_status) + '</span>';
        
        content.innerHTML = `
            <!-- Statut global -->
            <div class="d-flex align-items-center mb-3">
                <span class="me-2 fs-4"><i class="bi bi-box text-primary"></i></span>
                <div>
                    <h5 class="mb-0">${escapeHtml(info.container_name)}</h5>
                    <small class="text-muted">ID: ${escapeHtml(info.container_id)}</small>
                </div>
                <span class="ms-auto">${statusBadge}</span>
            </div>
            
            <div class="row g-3">
                <!-- Colonne gauche -->
                <div class="col-md-6">
                    <!-- Info Conteneur -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-box me-2"></i>Conteneur</div>
                        <div class="card-body py-2">
                            <table class="table table-sm mb-0">
                                <tr><th style="width: 100px;">Image</th><td><code>${escapeHtml(info.container_image || 'N/A')}</code></td></tr>
                                <tr><th>Créé</th><td>${info.container_created ? formatDateTime(info.container_created) : 'N/A'}</td></tr>
                                <tr><th>Démarré</th><td>${info.container_started_at ? formatDateTime(info.container_started_at) : 'N/A'}</td></tr>
                            </table>
                        </div>
                    </div>
                    
                    <!-- Ports -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-plug me-2"></i>Ports exposés</div>
                        <div class="card-body py-2">
                            ${info.ports && info.ports.length > 0 ? `
                                <table class="table table-sm mb-0">
                                    <thead><tr><th>Conteneur</th><th>→</th><th>Hôte</th><th>Proto</th></tr></thead>
                                    <tbody>
                                        ${info.ports.map(p => `
                                            <tr>
                                                <td>${p.container_port}</td>
                                                <td>→</td>
                                                <td><strong>${p.host_port || '-'}</strong></td>
                                                <td><span class="badge bg-light text-dark">${p.protocol}</span></td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun port exposé</p>'}
                        </div>
                    </div>
                    
                    <!-- Réseaux -->
                    <div class="card">
                        <div class="card-header py-2"><i class="bi bi-diagram-3 me-2"></i>Réseaux Docker</div>
                        <div class="card-body py-2">
                            ${info.networks && info.networks.length > 0 ? `
                                <table class="table table-sm mb-0">
                                    <thead><tr><th>Réseau</th><th>IP</th></tr></thead>
                                    <tbody>
                                        ${info.networks.map(n => `
                                            <tr>
                                                <td><code>${escapeHtml(n)}</code></td>
                                                <td><code>${info.ip_addresses[n] || '-'}</code></td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun réseau</p>'}
                        </div>
                    </div>
                </div>
                
                <!-- Colonne droite -->
                <div class="col-md-6">
                    <!-- Variables APP_* -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-gear me-2"></i>Variables APP_*</div>
                        <div class="card-body py-2">
                            ${info.app_env_vars && Object.keys(info.app_env_vars).length > 0 ? `
                                <table class="table table-sm mb-0">
                                    ${Object.entries(info.app_env_vars).map(([k, v]) => `
                                        <tr><th style="width: 120px;">${escapeHtml(k)}</th><td><code>${escapeHtml(v)}</code></td></tr>
                                    `).join('')}
                                </table>
                            ` : '<p class="text-muted mb-0">Aucune variable APP_*</p>'}
                        </div>
                    </div>
                    
                    <!-- Labels Hub -->
                    <div class="card mb-3">
                        <div class="card-header py-2"><i class="bi bi-tag me-2"></i>Labels hub.*</div>
                        <div class="card-body py-2">
                            ${info.hub_labels && Object.keys(info.hub_labels).length > 0 ? `
                                <table class="table table-sm mb-0">
                                    ${Object.entries(info.hub_labels).map(([k, v]) => `
                                        <tr><th style="width: 120px;">${escapeHtml(k)}</th><td><code>${escapeHtml(v)}</code></td></tr>
                                    `).join('')}
                                </table>
                            ` : '<p class="text-muted mb-0">Aucun label hub.*</p>'}
                        </div>
                    </div>
                    
                    <!-- Ressources CPU/RAM -->
                    ${info.container_stats ? buildContainerStatsCard(info.container_stats) : ''}
                    
                    <!-- Info développeur -->
                    <div class="card border-info mb-3">
                        <div class="card-header py-2 bg-info text-white"><i class="bi bi-code-slash me-2"></i>Pour développeurs</div>
                        <div class="card-body py-2">
                            <table class="table table-sm mb-0">
                                <tr>
                                    <th style="width: 100px;">URL API</th>
                                    <td>
                                        <a href="${escapeHtml(info.app_api_url)}" target="_blank" class="text-break">
                                            ${escapeHtml(info.app_api_url)}
                                        </a>
                                    </td>
                                </tr>
                                ${info.app_frontend_url ? `
                                <tr>
                                    <th>Frontend</th>
                                    <td>
                                        <a href="${escapeHtml(info.app_frontend_url)}" target="_blank" class="text-break">
                                            ${escapeHtml(info.app_frontend_url)}
                                        </a>
                                    </td>
                                </tr>
                                ` : ''}
                                <tr>
                                    <th>Header</th>
                                    <td><code>X-Slug: ${escapeHtml(info.app_slug)}</code></td>
                                </tr>
                            </table>
                            
                            <div class="mt-2">
                                <small class="text-muted">Commandes utiles :</small>
                                <div class="bg-dark text-light p-2 rounded mt-1" style="font-size: 0.8rem;">
                                    <code class="text-info">docker logs ${escapeHtml(info.container_name)} --tail 100 -f</code><br>
                                    <code class="text-warning">docker exec -it ${escapeHtml(info.container_name)} /bin/bash</code>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    } catch (error) {
        console.error('Erreur récupération Docker info:', error);
        content.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-x-circle me-2"></i>
                <strong>Erreur</strong>
                <p class="mb-0">${escapeHtml(error.message || 'Impossible de récupérer les informations Docker')}</p>
            </div>
        `;
    }
}

/**
 * Formate une date ISO pour l'affichage
 */
function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    try {
        const date = parseUTCDate(isoString);
        return date.toLocaleString('fr-FR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        return isoString;
    }
}

async function editApp(id) {
    const app = allApps.find(a => a.id === id);
    if (!app) {
        showToast('Erreur', 'Application non trouvée. Veuillez rafraîchir la page.', 'danger');
        try { await loadAdminData(); } catch (e) { console.error('Impossible de recharger les apps:', e); }
        return;
    }

    document.getElementById('appForm').reset();
    
    document.getElementById('appFormTitle').textContent = 'Modifier l\'Application';
    document.getElementById('appId').value = app.id;
    document.getElementById('appMode').value = 'edit';
    
    document.getElementById('advancedOptionsSection').classList.remove('d-none');
    document.getElementById('m2mInfoSection').classList.add('d-none');
    
    document.getElementById('appName').value = app.name;
    document.getElementById('appName').required = true;
    document.getElementById('appSlug').value = app.slug;
    document.getElementById('appSlug').readOnly = true;
    document.getElementById('slugHint').textContent = 'Non modifiable';
    document.getElementById('appDescription').value = app.description || '';
    document.getElementById('appIcon').value = app.icon;
    document.getElementById('appColor').value = app.color;
    document.getElementById('appApiUrl').value = app.api_url;
    document.getElementById('appFrontendUrl').value = app.frontend_url || '';
    document.getElementById('appCallbackUrl').value = app.callback_url || '';
    document.getElementById('appActive').checked = app.is_active;
    document.getElementById('appRequiresAuth').checked = app.requires_auth;
    document.getElementById('appIsSharedService').checked = app.is_shared_service || false;
    
    // Mode édition : URLs directes
    document.getElementById('simplePortsSection').classList.add('d-none');
    document.getElementById('advancedUrlsSection').classList.remove('d-none');
    document.getElementById('appApiPort').required = false;
    document.getElementById('appApiUrl').required = true;
    
    document.getElementById('appSubmitBtn').innerHTML = '<i class="bi bi-check-lg me-1"></i>Enregistrer';
    
    hideIconPicker();
    document.getElementById('iconSearchInput').value = '';
    
    updateIconPreview();
    updateAppFormPreview();
    
    appFormModal.show();
}

function toggleCustomHost() {
    const checked = document.getElementById('appCustomHost').checked;
    document.getElementById('customHostGroup').classList.toggle('d-none', !checked);
    updateUrlPreview();
}

function updateUrlPreview() {
    const apiPort = document.getElementById('appApiPort').value;
    const frontendPort = document.getElementById('appFrontendPort').value;
    const customHost = document.getElementById('appCustomHost').checked;
    const customHostValue = document.getElementById('appCustomHostValue').value;
    
    const host = customHost && customHostValue ? customHostValue : (hubHost || 'chargement...');
    
    const previewApi = document.getElementById('previewApiUrl');
    const previewFrontend = document.getElementById('previewFrontendUrl');
    
    if (apiPort) {
        previewApi.innerHTML = `<i class="bi bi-hdd-rack me-1"></i>API: <code>http://${host}:${apiPort}</code>`;
    } else {
        previewApi.innerHTML = '<i class="bi bi-hdd-rack me-1"></i>API: <span class="text-danger">Port requis</span>';
    }
    
    if (frontendPort) {
        previewFrontend.innerHTML = `<i class="bi bi-window me-1"></i>Front: <code>http://${host}:${frontendPort}</code>`;
    } else {
        previewFrontend.innerHTML = '';
    }
}

function updateAppFormPreview() {
    const icon = document.getElementById('appIcon').value;
    const color = document.getElementById('appColor').value;
    const preview = document.getElementById('appFormPreview');
    if (!preview) return;
    preview.style.background = color;
    preview.querySelector('i').className = `bi ${icon}`;
}

async function handleSaveApp(event) {
    event.preventDefault();
    
    const id = document.getElementById('appId').value;
    const mode = document.getElementById('appMode').value;
    
    try {
        if (id && mode === 'edit') {
            // Mode édition - utiliser les URLs directes
            // Ne pas envoyer les champs vides pour éviter d'écraser les valeurs existantes
            const data = {
                name: document.getElementById('appName').value,
                description: document.getElementById('appDescription').value || null,
                icon: document.getElementById('appIcon').value,
                color: document.getElementById('appColor').value,
                is_active: document.getElementById('appActive').checked,
                requires_auth: document.getElementById('appRequiresAuth').checked,
                is_shared_service: document.getElementById('appIsSharedService').checked
            };
            
            // Ajouter les URLs seulement si elles sont renseignées
            const apiUrl = document.getElementById('appApiUrl').value;
            const frontendUrl = document.getElementById('appFrontendUrl').value;
            const callbackUrl = document.getElementById('appCallbackUrl').value;
            
            if (apiUrl) data.api_url = apiUrl;
            if (frontendUrl) data.frontend_url = frontendUrl;
            else if (frontendUrl === '') data.frontend_url = null;  // Permettre de vider explicitement
            if (callbackUrl) data.callback_url = callbackUrl;
            else if (callbackUrl === '') data.callback_url = null;  // Permettre de vider explicitement
            
            await api.updateApp(id, data);
            showToast('Succès', 'Application modifiée', 'success');
            
            appFormModal.hide();
            await loadAdminData();
        } else {
            // Mode création - utiliser les ports (schéma simplifié)
            // Le M2M sera créé automatiquement quand l'app s'auto-enregistre
            const customHost = document.getElementById('appCustomHost').checked;
            const isSharedService = document.getElementById('appIsSharedService')?.checked || false;
            const gitRepoUrl = document.getElementById('appGitRepoUrl')?.value?.trim() || null;
            const data = {
                name: document.getElementById('appName').value,
                slug: document.getElementById('appSlug').value,
                description: document.getElementById('appDescription').value || null,
                icon: document.getElementById('appIcon').value,
                color: document.getElementById('appColor').value,
                api_port: parseInt(document.getElementById('appApiPort').value),
                frontend_port: document.getElementById('appFrontendPort').value ? 
                    parseInt(document.getElementById('appFrontendPort').value) : null,
                custom_host: customHost ? document.getElementById('appCustomHostValue').value : null,
                requires_auth: document.getElementById('appRequiresAuth').checked,
                is_shared_service: isSharedService,
                create_service_account: false  // L'app créera son M2M en s'auto-enregistrant
            };
            if (gitRepoUrl) data.git_repo_url = gitRepoUrl;
            
            await api.createApp(data);
            appFormModal.hide();
            const sharedMsg = isSharedService ? ' (API partagée M2M)' : '';
            showToast('Succès', `Application créée${sharedMsg} - le M2M sera créé au démarrage de l'app`, 'success');
            
            // Rafraîchir le token pour avoir accès à la nouvelle app immédiatement
            await refreshTokenAfterAppChange('création');
            
            await loadAdminData();
        }
        loadApps(); // Refresh dashboard
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteApp(id, name) {
    if (!confirm(`Supprimer l'application "${name}" ?`)) return;
    
    try {
        await api.deleteApp(id);
        showToast('Succès', 'Application supprimée', 'success');
        await loadAdminData();
        loadApps();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function updateIconPreview() {
    const icon = document.getElementById('appIcon').value;
    document.getElementById('iconPreview').className = `bi ${icon}`;
    updateIconPickerSelection();
    updateAppFormPreview();
}

// ===== Icon Picker =====

// Liste des icônes Bootstrap populaires organisées par catégorie
const BOOTSTRAP_ICONS = [
    // Applications & UI
    'bi-app', 'bi-app-indicator', 'bi-grid', 'bi-grid-3x3', 'bi-grid-3x3-gap', 'bi-grid-3x3-gap-fill',
    'bi-layout-text-window', 'bi-layout-text-sidebar', 'bi-window', 'bi-window-stack', 'bi-window-sidebar',
    'bi-columns', 'bi-columns-gap', 'bi-view-list', 'bi-view-stacked', 'bi-kanban',
    // Documents
    'bi-file-earmark', 'bi-file-earmark-text', 'bi-file-earmark-code', 'bi-file-earmark-pdf', 'bi-file-earmark-spreadsheet',
    'bi-file-earmark-richtext', 'bi-file-earmark-zip', 'bi-file-earmark-image', 'bi-file-earmark-music', 'bi-file-earmark-play',
    'bi-files', 'bi-folder', 'bi-folder-fill', 'bi-folder2', 'bi-folder2-open',
    // Personnes
    'bi-person', 'bi-person-fill', 'bi-person-circle', 'bi-person-badge', 'bi-person-badge-fill',
    'bi-people', 'bi-people-fill', 'bi-person-plus', 'bi-person-plus-fill', 'bi-person-check', 'bi-person-check-fill',
    'bi-person-gear', 'bi-person-workspace', 'bi-person-video', 'bi-person-lines-fill', 'bi-person-vcard',
    // Business
    'bi-briefcase', 'bi-briefcase-fill', 'bi-building', 'bi-buildings', 'bi-buildings-fill',
    'bi-shop', 'bi-shop-window', 'bi-cart', 'bi-cart-fill', 'bi-cart3', 'bi-cart4',
    'bi-basket', 'bi-basket-fill', 'bi-bag', 'bi-bag-fill', 'bi-handbag', 'bi-handbag-fill',
    'bi-wallet', 'bi-wallet-fill', 'bi-wallet2', 'bi-credit-card', 'bi-credit-card-fill',
    'bi-bank', 'bi-bank2', 'bi-piggy-bank', 'bi-piggy-bank-fill', 'bi-cash', 'bi-cash-stack', 'bi-cash-coin',
    'bi-currency-euro', 'bi-currency-dollar', 'bi-receipt', 'bi-receipt-cutoff',
    // Tech & Infra
    'bi-hdd', 'bi-hdd-fill', 'bi-hdd-network', 'bi-hdd-network-fill', 'bi-hdd-rack', 'bi-hdd-rack-fill',
    'bi-hdd-stack', 'bi-hdd-stack-fill', 'bi-server', 'bi-database', 'bi-database-fill',
    'bi-cpu', 'bi-cpu-fill', 'bi-memory', 'bi-gpu-card', 'bi-motherboard', 'bi-motherboard-fill',
    'bi-pc', 'bi-pc-display', 'bi-pc-display-horizontal', 'bi-laptop', 'bi-laptop-fill',
    'bi-phone', 'bi-phone-fill', 'bi-tablet', 'bi-tablet-fill', 'bi-smartwatch',
    'bi-router', 'bi-router-fill', 'bi-modem', 'bi-modem-fill', 'bi-wifi', 'bi-broadcast',
    // Cloud & Réseau
    'bi-cloud', 'bi-cloud-fill', 'bi-cloud-upload', 'bi-cloud-download', 'bi-cloud-check', 'bi-cloud-check-fill',
    'bi-cloud-arrow-up', 'bi-cloud-arrow-down', 'bi-cloud-slash', 'bi-clouds', 'bi-clouds-fill',
    'bi-globe', 'bi-globe2', 'bi-globe-americas', 'bi-globe-europe-africa', 'bi-globe-asia-australia',
    'bi-link', 'bi-link-45deg', 'bi-share', 'bi-share-fill', 'bi-diagram-2', 'bi-diagram-3',
    // Charts & Data
    'bi-graph-up', 'bi-graph-up-arrow', 'bi-graph-down', 'bi-bar-chart', 'bi-bar-chart-fill',
    'bi-bar-chart-line', 'bi-bar-chart-steps', 'bi-pie-chart', 'bi-pie-chart-fill',
    'bi-activity', 'bi-speedometer', 'bi-speedometer2', 'bi-clipboard-data', 'bi-clipboard-data-fill',
    // Sécurité
    'bi-shield', 'bi-shield-fill', 'bi-shield-check', 'bi-shield-lock', 'bi-shield-lock-fill',
    'bi-lock', 'bi-lock-fill', 'bi-unlock', 'bi-unlock-fill', 'bi-key', 'bi-key-fill',
    'bi-fingerprint', 'bi-eye', 'bi-eye-fill', 'bi-eye-slash', 'bi-eye-slash-fill',
    // Outils & Settings
    'bi-gear', 'bi-gear-fill', 'bi-gear-wide', 'bi-gear-wide-connected', 'bi-gears',
    'bi-tools', 'bi-wrench', 'bi-wrench-adjustable', 'bi-hammer', 'bi-screwdriver',
    'bi-sliders', 'bi-sliders2', 'bi-toggles', 'bi-toggles2',
    // Communication
    'bi-chat', 'bi-chat-fill', 'bi-chat-dots', 'bi-chat-dots-fill', 'bi-chat-left-text', 'bi-chat-left-text-fill',
    'bi-chat-square', 'bi-chat-square-fill', 'bi-chat-square-text', 'bi-chat-quote', 'bi-chat-quote-fill',
    'bi-envelope', 'bi-envelope-fill', 'bi-envelope-open', 'bi-envelope-at', 'bi-mailbox', 'bi-mailbox2',
    'bi-telephone', 'bi-telephone-fill', 'bi-telephone-inbound', 'bi-telephone-outbound', 'bi-headset',
    'bi-megaphone', 'bi-megaphone-fill', 'bi-bell', 'bi-bell-fill', 'bi-bell-slash',
    // Médias
    'bi-camera', 'bi-camera-fill', 'bi-camera-video', 'bi-camera-video-fill', 'bi-film',
    'bi-image', 'bi-image-fill', 'bi-images', 'bi-card-image', 'bi-file-image',
    'bi-music-note', 'bi-music-note-beamed', 'bi-music-note-list', 'bi-volume-up', 'bi-volume-up-fill',
    'bi-play', 'bi-play-fill', 'bi-play-circle', 'bi-play-circle-fill', 'bi-play-btn', 'bi-play-btn-fill',
    'bi-mic', 'bi-mic-fill', 'bi-mic-mute', 'bi-mic-mute-fill', 'bi-record', 'bi-record-fill',
    // Maps & Lieux
    'bi-geo', 'bi-geo-fill', 'bi-geo-alt', 'bi-geo-alt-fill', 'bi-pin', 'bi-pin-fill',
    'bi-pin-map', 'bi-pin-map-fill', 'bi-map', 'bi-map-fill', 'bi-compass', 'bi-compass-fill',
    'bi-signpost', 'bi-signpost-fill', 'bi-signpost-2', 'bi-signpost-2-fill', 'bi-signpost-split',
    'bi-house', 'bi-house-fill', 'bi-house-door', 'bi-house-door-fill', 'bi-house-gear', 'bi-house-gear-fill',
    // Transport
    'bi-car-front', 'bi-car-front-fill', 'bi-truck', 'bi-truck-front', 'bi-truck-front-fill',
    'bi-bicycle', 'bi-scooter', 'bi-bus-front', 'bi-bus-front-fill', 'bi-train-front', 'bi-train-front-fill',
    'bi-airplane', 'bi-airplane-fill', 'bi-rocket', 'bi-rocket-fill', 'bi-rocket-takeoff', 'bi-rocket-takeoff-fill',
    // Nature & Météo
    'bi-sun', 'bi-sun-fill', 'bi-moon', 'bi-moon-fill', 'bi-stars', 'bi-star', 'bi-star-fill',
    'bi-cloud-sun', 'bi-cloud-sun-fill', 'bi-cloud-moon', 'bi-cloud-rain', 'bi-cloud-rain-fill',
    'bi-lightning', 'bi-lightning-fill', 'bi-snow', 'bi-snow2', 'bi-tornado',
    'bi-tree', 'bi-tree-fill', 'bi-flower1', 'bi-flower2', 'bi-flower3',
    // Médical & Santé
    'bi-heart', 'bi-heart-fill', 'bi-heart-pulse', 'bi-heart-pulse-fill', 'bi-activity',
    'bi-hospital', 'bi-hospital-fill', 'bi-bandaid', 'bi-bandaid-fill', 'bi-capsule', 'bi-capsule-pill',
    'bi-thermometer', 'bi-thermometer-half', 'bi-clipboard2-pulse', 'bi-clipboard2-pulse-fill',
    // Education
    'bi-book', 'bi-book-fill', 'bi-book-half', 'bi-journals', 'bi-journal', 'bi-journal-text',
    'bi-bookmark', 'bi-bookmark-fill', 'bi-bookmarks', 'bi-bookmarks-fill', 'bi-bookshelf',
    'bi-mortarboard', 'bi-mortarboard-fill', 'bi-backpack', 'bi-backpack-fill', 'bi-pen', 'bi-pen-fill',
    'bi-pencil', 'bi-pencil-fill', 'bi-pencil-square', 'bi-brush', 'bi-brush-fill', 'bi-palette', 'bi-palette-fill',
    // Actions
    'bi-check', 'bi-check2', 'bi-check-circle', 'bi-check-circle-fill', 'bi-check-lg', 'bi-check-all',
    'bi-x', 'bi-x-circle', 'bi-x-circle-fill', 'bi-x-lg', 'bi-x-octagon', 'bi-x-octagon-fill',
    'bi-plus', 'bi-plus-circle', 'bi-plus-circle-fill', 'bi-plus-lg', 'bi-plus-square', 'bi-plus-square-fill',
    'bi-dash', 'bi-dash-circle', 'bi-dash-circle-fill', 'bi-dash-lg', 'bi-dash-square', 'bi-dash-square-fill',
    'bi-arrow-up', 'bi-arrow-down', 'bi-arrow-left', 'bi-arrow-right', 'bi-arrow-clockwise', 'bi-arrow-counterclockwise',
    'bi-arrow-repeat', 'bi-arrows-expand', 'bi-arrows-fullscreen', 'bi-arrows-move',
    // Divers utiles
    'bi-box', 'bi-box-fill', 'bi-box-seam', 'bi-box-seam-fill', 'bi-boxes', 'bi-archive', 'bi-archive-fill',
    'bi-inbox', 'bi-inbox-fill', 'bi-send', 'bi-send-fill', 'bi-reply', 'bi-reply-fill',
    'bi-clipboard', 'bi-clipboard-fill', 'bi-clipboard-check', 'bi-clipboard-check-fill',
    'bi-calendar', 'bi-calendar-fill', 'bi-calendar-event', 'bi-calendar-event-fill', 'bi-calendar-check', 'bi-calendar-check-fill',
    'bi-clock', 'bi-clock-fill', 'bi-clock-history', 'bi-stopwatch', 'bi-stopwatch-fill', 'bi-hourglass', 'bi-hourglass-split',
    'bi-alarm', 'bi-alarm-fill', 'bi-watch',
    'bi-calculator', 'bi-calculator-fill', 'bi-percent', 'bi-hash', 'bi-at',
    'bi-qr-code', 'bi-qr-code-scan', 'bi-upc', 'bi-upc-scan', 'bi-barcode',
    'bi-tag', 'bi-tag-fill', 'bi-tags', 'bi-tags-fill', 'bi-bookmark-star', 'bi-bookmark-star-fill',
    'bi-trophy', 'bi-trophy-fill', 'bi-award', 'bi-award-fill', 'bi-patch-check', 'bi-patch-check-fill',
    'bi-flag', 'bi-flag-fill', 'bi-puzzle', 'bi-puzzle-fill', 'bi-lightbulb', 'bi-lightbulb-fill',
    'bi-magic', 'bi-gem', 'bi-gift', 'bi-gift-fill', 'bi-ticket', 'bi-ticket-fill',
    'bi-cup', 'bi-cup-fill', 'bi-cup-hot', 'bi-cup-hot-fill',
    // Robots & Tech avancée
    'bi-robot', 'bi-cpu', 'bi-bricks', 'bi-plugin', 'bi-terminal', 'bi-terminal-fill',
    'bi-code', 'bi-code-slash', 'bi-code-square', 'bi-braces', 'bi-braces-asterisk',
    'bi-bug', 'bi-bug-fill', 'bi-vector-pen', 'bi-bezier', 'bi-bezier2',
    // Social & Partage
    'bi-hand-thumbs-up', 'bi-hand-thumbs-up-fill', 'bi-hand-thumbs-down', 'bi-hand-thumbs-down-fill',
    'bi-emoji-smile', 'bi-emoji-smile-fill', 'bi-emoji-heart-eyes', 'bi-emoji-heart-eyes-fill',
    'bi-emoji-laughing', 'bi-emoji-laughing-fill', 'bi-emoji-neutral', 'bi-emoji-neutral-fill',
    // Formes
    'bi-circle', 'bi-circle-fill', 'bi-square', 'bi-square-fill', 'bi-triangle', 'bi-triangle-fill',
    'bi-diamond', 'bi-diamond-fill', 'bi-hexagon', 'bi-hexagon-fill', 'bi-pentagon', 'bi-pentagon-fill',
    'bi-octagon', 'bi-octagon-fill', 'bi-heart', 'bi-heart-fill', 'bi-suit-heart', 'bi-suit-heart-fill'
];

let iconPickerVisible = false;

function toggleIconPicker() {
    const container = document.getElementById('iconPickerContainer');
    iconPickerVisible = !iconPickerVisible;
    
    if (iconPickerVisible) {
        container.classList.remove('d-none');
        renderIconPicker();
        document.getElementById('iconSearchInput').focus();
    } else {
        container.classList.add('d-none');
    }
}

function hideIconPicker() {
    iconPickerVisible = false;
    const container = document.getElementById('iconPickerContainer');
    if (container) {
        container.classList.add('d-none');
    }
}

function renderIconPicker() {
    const grid = document.getElementById('iconPickerGrid');
    const currentIcon = document.getElementById('appIcon').value;
    
    grid.innerHTML = BOOTSTRAP_ICONS.map(icon => `
        <div class="icon-picker-item ${icon === currentIcon ? 'selected' : ''}" 
             data-icon="${icon}" 
             onclick="selectIcon('${icon}')"
             title="${icon}">
            <i class="bi ${icon}"></i>
        </div>
    `).join('');
}

function selectIcon(iconName) {
    document.getElementById('appIcon').value = iconName;
    updateIconPreview();
    
    // Fermer le picker après sélection
    toggleIconPicker();
}

function updateIconPickerSelection() {
    const currentIcon = document.getElementById('appIcon').value;
    const items = document.querySelectorAll('.icon-picker-item');
    
    items.forEach(item => {
        if (item.dataset.icon === currentIcon) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
}

function filterIcons() {
    const searchTerm = document.getElementById('iconSearchInput').value.toLowerCase();
    const items = document.querySelectorAll('.icon-picker-item');
    
    items.forEach(item => {
        const iconName = item.dataset.icon.toLowerCase();
        if (iconName.includes(searchTerm)) {
            item.classList.remove('hidden');
        } else {
            item.classList.add('hidden');
        }
    });
}

function openAppFormPrefilled(prepared) {
    showAddAppForm();

    setTimeout(() => {
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
        set('appName', prepared.name);
        set('appSlug', prepared.slug);
        set('appApiPort', prepared.api_port);
        if (prepared.frontend_port) set('appFrontendPort', prepared.frontend_port);
        set('appGitRepoUrl', prepared.git_repo_url || '');

        if (typeof updateUrlPreview === 'function') updateUrlPreview();

        const modalBody = document.querySelector('#appFormModal .modal-body');
        if (modalBody && !document.getElementById('prefilledEnvBanner')) {
            const envBlock = [
                'APP_SLUG=' + prepared.slug,
                'API_PORT=' + prepared.api_port
            ];
            if (prepared.frontend_port) envBlock.push('FRONTEND_PORT=' + prepared.frontend_port);
            if (prepared.frontend_https_port) envBlock.push('FRONTEND_HTTPS_PORT=' + prepared.frontend_https_port);
            const envStr = envBlock.join('\n');

            const banner = document.createElement('div');
            banner.id = 'prefilledEnvBanner';
            banner.className = 'alert alert-success';
            banner.style.cssText = 'border-left:4px solid #16a34a;';
            banner.innerHTML = ''
                + '<div class="d-flex justify-content-between align-items-center mb-2">'
                + '<strong>📋 Valeurs à reporter dans l\'app (le HUB décide, l\'app suit)</strong>'
                + '<button class="btn btn-sm btn-success" onclick="navigator.clipboard.writeText(atob(\'' + btoa(envStr) + '\')).then(function(){showToast(\'Copié\',\'Bloc .env copié\',\'success\')})">📋 Copier le .env</button>'
                + '</div>'
                + '<pre class="mb-0" style="background:#1e293b;color:#e2e8f0;padding:10px;border-radius:6px;font-size:11px;">' + escapeHtml(envStr) + '</pre>'
                + '<small class="text-muted">↑ Copiez ce bloc dans le .env de l\'app déployée avant de la (re)démarrer.</small>';
            modalBody.insertBefore(banner, modalBody.firstChild);
        }

        showToast('Pré-rempli', 'Slug "' + prepared.slug + '" + ports alloués', 'info');
    }, 150);
}

// Fermer le picker si on clique en dehors
document.addEventListener('click', (e) => {
    const picker = document.getElementById('iconPickerContainer');
    const toggleBtn = e.target.closest('[onclick="toggleIconPicker()"]');
    const pickerContainer = e.target.closest('#iconPickerContainer');
    
    if (iconPickerVisible && !toggleBtn && !pickerContainer) {
        iconPickerVisible = false;
        picker?.classList.add('d-none');
    }
});
