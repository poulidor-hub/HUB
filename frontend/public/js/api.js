/**
 * API Client for Hub Authentication
 * Supporte les refresh tokens pour maintenir les sessions
 * Inclut un refresh proactif avant expiration du token
 */

// L'API est maintenant proxifiée via Nginx sur le même hôte/port
const API_BASE = '/api';

class HubAPI {
    constructor() {
        this.token = localStorage.getItem('hub_token');
        this.refreshToken = localStorage.getItem('hub_refresh_token');
        this._refreshPromise = null; // Pour éviter les appels concurrents
        this._lastRefreshTime = 0; // Timestamp du dernier refresh réussi
        this._proactiveRefreshTimer = null; // Timer pour refresh proactif
        this._tokenExpiry = null; // Date d'expiration du token
        
        // Si on a un token, parser son expiration et planifier le refresh
        if (this.token) {
            this._scheduleProactiveRefresh();
        }
    }

    /**
     * Parse le payload JWT pour extraire l'expiration
     */
    _parseTokenExpiry(token) {
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            return payload.exp ? new Date(payload.exp * 1000) : null;
        } catch (e) {
            console.warn('Impossible de parser le token JWT:', e);
            return null;
        }
    }

    /**
     * Planifie un refresh proactif 2 minutes avant l'expiration du token
     */
    _scheduleProactiveRefresh() {
        // Annuler tout timer existant
        if (this._proactiveRefreshTimer) {
            clearTimeout(this._proactiveRefreshTimer);
            this._proactiveRefreshTimer = null;
        }

        if (!this.token || !this.refreshToken) return;

        const expiry = this._parseTokenExpiry(this.token);
        if (!expiry) return;

        this._tokenExpiry = expiry;
        const now = Date.now();
        const expiryTime = expiry.getTime();
        
        // Rafraîchir 2 minutes avant l'expiration (ou immédiatement si < 2 min)
        const refreshIn = Math.max(0, expiryTime - now - 2 * 60 * 1000);
        
        console.log(`🔑 Token expire à ${expiry.toLocaleTimeString()}, refresh proactif dans ${Math.round(refreshIn / 1000)}s`);
        
        this._proactiveRefreshTimer = setTimeout(async () => {
            console.log('🔄 Refresh proactif du token...');
            const success = await this.tryRefreshToken();
            if (success) {
                console.log('✅ Refresh proactif réussi');
            } else {
                console.warn('⚠️ Refresh proactif échoué');
            }
        }, refreshIn);
    }

    setToken(token) {
        this.token = token;
        localStorage.setItem('hub_token', token);
        // Replanifier le refresh proactif avec le nouveau token
        this._scheduleProactiveRefresh();
    }

    setRefreshToken(token) {
        this.refreshToken = token;
        if (token) {
            localStorage.setItem('hub_refresh_token', token);
        } else {
            localStorage.removeItem('hub_refresh_token');
        }
    }

    clearToken() {
        this.token = null;
        this.refreshToken = null;
        this._tokenExpiry = null;
        localStorage.removeItem('hub_token');
        localStorage.removeItem('hub_refresh_token');
        // Annuler le timer de refresh proactif
        if (this._proactiveRefreshTimer) {
            clearTimeout(this._proactiveRefreshTimer);
            this._proactiveRefreshTimer = null;
        }
    }

    /**
     * Vérifie que le token est valide et le rafraîchit si nécessaire.
     * À appeler avant toute opération nécessitant un token garanti valide
     * (ex: ouverture d'une application dans un nouvel onglet).
     * @returns {Promise<void>}
     * @throws {Error} si le token ne peut pas être obtenu/rafraîchi
     */
    async ensureFreshToken() {
        if (!this.token) throw new Error('Non authentifié');

        const expiry = this._parseTokenExpiry(this.token);
        if (!expiry) return; // Impossible de parser, tenter quand même

        // Si le token expire dans moins de 30 secondes, forcer un refresh
        const margin = 30 * 1000;
        if (expiry.getTime() - Date.now() < margin) {
            console.log('🔄 Token proche de l\'expiration, refresh avant ouverture app...');
            const refreshed = await this.tryRefreshToken();
            if (!refreshed) {
                throw new Error('Impossible de rafraîchir le token');
            }
        }
    }

    getHeaders() {
        const headers = {
            'Content-Type': 'application/json'
        };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        return headers;
    }

    /**
     * Tente de rafraîchir le token d'accès
     * @returns {Promise<boolean>} true si le refresh a réussi
     */
    async tryRefreshToken() {
        // Éviter les appels concurrents - retourner la promesse existante
        if (this._refreshPromise) {
            return this._refreshPromise;
        }

        if (!this.refreshToken) {
            return false;
        }

        // Éviter les refreshs trop rapprochés (minimum 2 secondes entre chaque)
        const now = Date.now();
        if (now - this._lastRefreshTime < 2000) {
            console.log('Refresh ignoré - trop récent');
            return true; // Considérer comme succès si refresh récent
        }

        // Capturer le token actuel pour éviter les races conditions
        const tokenToRefresh = this.refreshToken;

        this._refreshPromise = (async () => {
            try {
                // Vérifier que le token n'a pas changé entre-temps
                // (si un autre refresh a déjà mis à jour le token)
                if (this.refreshToken !== tokenToRefresh) {
                    console.log('Refresh token déjà mis à jour par un autre appel');
                    return true; // Le token a déjà été rafraîchi
                }

                const response = await fetch(`${API_BASE}/auth/token/refresh`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ refresh_token: tokenToRefresh })
                });

                if (!response.ok) {
                    // Vérifier si le token a été mis à jour pendant la requête
                    if (this.refreshToken !== tokenToRefresh) {
                        console.log('Token rafraîchi par un autre appel pendant la requête');
                        return true;
                    }
                    // Refresh token invalide - supprimer UNIQUEMENT le refresh token
                    // On garde le token d'accès qui peut encore être valide
                    console.warn('Refresh token invalide, suppression du refresh token uniquement');
                    this.setRefreshToken(null);
                    // Retourner false pour indiquer l'échec du refresh
                    // Mais ne pas effacer le token d'accès - il sera invalidé naturellement
                    return false;
                }

                const data = await response.json();
                this.setToken(data.access_token);
                if (data.refresh_token) {
                    this.setRefreshToken(data.refresh_token);
                }
                this._lastRefreshTime = Date.now(); // Marquer le succès
                return true;
            } catch (error) {
                console.error('Refresh token failed:', error);
                // En cas d'erreur réseau, garder les tokens existants
                return false;
            } finally {
                this._refreshPromise = null;
            }
        })();

        return this._refreshPromise;
    }

    async request(endpoint, options = {}) {
        const url = `${API_BASE}${endpoint}`;
        const config = {
            ...options,
            headers: {
                ...this.getHeaders(),
                ...options.headers
            }
        };

        try {
            let response = await fetch(url, config);
            
            // Si 401, tenter un refresh puis réessayer
            if (response.status === 401 && this.refreshToken) {
                const refreshed = await this.tryRefreshToken();
                if (refreshed) {
                    // Réessayer avec le nouveau token
                    config.headers['Authorization'] = `Bearer ${this.token}`;
                    response = await fetch(url, config);
                } else if (this.token) {
                    // Refresh échoué mais on a encore un token d'accès - réessayer avec
                    // (le token pourrait encore être valide quelques secondes)
                    config.headers['Authorization'] = `Bearer ${this.token}`;
                    response = await fetch(url, config);
                }
            }

            if (response.status === 401) {
                // Token d'accès invalide ET pas de refresh possible
                // Maintenant on peut vraiment déconnecter
                this.clearToken();
                this._dispatchSessionExpired();
                throw new Error('Session expirée');
            }

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: 'Erreur serveur' }));
                // Gérer les erreurs de validation Pydantic (detail peut être un tableau)
                let errorMessage = 'Erreur serveur';
                if (error.detail) {
                    if (Array.isArray(error.detail)) {
                        // Erreur de validation Pydantic
                        errorMessage = error.detail.map(e => {
                            const field = e.loc ? e.loc.slice(1).join('.') : '';
                            return field ? `${field}: ${e.msg}` : e.msg;
                        }).join(', ');
                    } else {
                        errorMessage = error.detail;
                    }
                }
                throw new Error(errorMessage);
            }

            if (response.status === 204) {
                return null;
            }

            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    }

    /**
     * Déclenche un événement global quand la session expire
     * Permet à app.js de réagir immédiatement pour déconnecter l'utilisateur
     */
    _dispatchSessionExpired() {
        window.dispatchEvent(new CustomEvent('hub:sessionExpired', {
            detail: { message: 'Votre session a expiré. Veuillez vous reconnecter.' }
        }));
    }

    // Auth
    async login(username, password) {
        const formData = new URLSearchParams();
        formData.append('username', username);
        formData.append('password', password);

        let response;
        try {
            response = await fetch(`${API_BASE}/auth/token`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: formData
            });
        } catch (e) {
            throw new Error(`Impossible de contacter le serveur: ${API_BASE}`);
        }

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Identifiants incorrects' }));
            throw new Error(error.detail);
        }

        const data = await response.json();
        this.setToken(data.access_token);
        if (data.refresh_token) {
            this.setRefreshToken(data.refresh_token);
        }
        return data;
    }

    async getCurrentUser() {
        return this.request('/auth/me');
    }

    async changePassword(currentPassword, newPassword) {
        return this.request('/auth/change-password', {
            method: 'PUT',
            body: JSON.stringify({
                current_password: currentPassword,
                new_password: newPassword
            })
        });
    }

    async logout() {
        // Sauvegarder les tokens avant de les effacer
        const accessToken = this.token;
        const refreshTokenValue = this.refreshToken;
        
        // Effacer les tokens locaux IMMÉDIATEMENT pour éviter toute reconnexion automatique
        this.clearToken();
        
        // Tenter de révoquer côté serveur (best effort)
        try {
            // Envoyer le refresh token au serveur pour le révoquer
            const response = await fetch(`${API_BASE}/auth/logout?revoke_all=true`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${accessToken}`
                },
                body: JSON.stringify({ refresh_token: refreshTokenValue })
            });
            // Ignorer les erreurs - les tokens locaux sont déjà effacés
        } catch (e) {
            console.log('Logout server notification failed (tokens already cleared locally)');
        }
    }

    // Config
    async getHubHost() {
        return this.request('/config/hub-host');
    }

    // Apps
    async getApps() {
        return this.request('/apps/');
    }

    async getAllApps() {
        return this.request('/apps/all');
    }

    async createApp(appData) {
        return this.request('/apps/', {
            method: 'POST',
            body: JSON.stringify(appData)
        });
    }

    async updateApp(id, appData) {
        return this.request(`/apps/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(appData)
        });
    }

    async updateMyAppRole(appSlug, role) {
        // role peut être null pour supprimer le rôle
        const url = `/apps/me/role/${encodeURIComponent(appSlug)}${role ? `?role=${encodeURIComponent(role)}` : ''}`;
        return this.request(url, {
            method: 'PUT'
        });
    }

    async deleteApp(id) {
        return this.request(`/apps/${id}`, {
            method: 'DELETE'
        });
    }

    async getAppDockerInfo(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/docker-info`);
    }

    // Users
    async getUsers() {
        return this.request('/users/');
    }

    async createUser(userData) {
        return this.request('/users/', {
            method: 'POST',
            body: JSON.stringify(userData)
        });
    }

    async updateUser(id, userData) {
        return this.request(`/users/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(userData)
        });
    }

    async deleteUser(id) {
        return this.request(`/users/${id}`, {
            method: 'DELETE'
        });
    }

    async revokeUserSessions(userId) {
        return this.request(`/users/${userId}/revoke-sessions`, {
            method: 'POST'
        });
    }

    async setUserApps(userId, assignments) {
        // assignments = [{ app_id: 1, role: "admin" }, ...]
        return this.request(`/users/${userId}/apps`, {
            method: 'POST',
            body: JSON.stringify({ assignments: assignments })
        });
    }

    // Service Accounts (M2M)
    async getServiceAccounts() {
        return this.request('/service-accounts/');
    }

    async createServiceAccount(saData) {
        return this.request('/service-accounts/', {
            method: 'POST',
            body: JSON.stringify(saData)
        });
    }

    async updateServiceAccount(id, saData) {
        return this.request(`/service-accounts/${id}`, {
            method: 'PUT',
            body: JSON.stringify(saData)
        });
    }

    async deleteServiceAccount(id) {
        return this.request(`/service-accounts/${id}`, {
            method: 'DELETE'
        });
    }

    async regenerateServiceAccountSecret(id) {
        return this.request(`/service-accounts/${id}/regenerate-secret`, {
            method: 'POST'
        });
    }

    async setServiceAccountApps(saId, appIds) {
        return this.request(`/service-accounts/${saId}/apps`, {
            method: 'PUT',
            body: JSON.stringify(appIds)
        });
    }

    // Audit
    async getActiveSessions() {
        return this.request('/audit/sessions/active');
    }

    async getSessionHistory(days = 7, userId = null) {
        let url = `/audit/sessions/history?days=${days}`;
        if (userId) url += `&user_id=${userId}`;
        return this.request(url);
    }

    async getConnectionStats(days = 7) {
        return this.request(`/audit/stats?days=${days}`);
    }

    async getUserStats(userId, days = 30) {
        return this.request(`/audit/stats/user/${userId}?days=${days}`);
    }

    // Audit Logs détaillés
    async getAuditLogs(params = {}) {
        const { days = 30, action = null, target = null, userId = null, page = 1, pageSize = 50 } = params;
        let url = `/audit/logs?days=${days}&page=${page}&page_size=${pageSize}`;
        if (action) url += `&action=${encodeURIComponent(action)}`;
        if (target) url += `&target=${encodeURIComponent(target)}`;
        if (userId) url += `&user_id=${userId}`;
        return this.request(url);
    }

    async getAppAuditSummary(days = 30) {
        return this.request(`/audit/logs/apps?days=${days}`);
    }

    async getAppAuditDetail(appSlug, params = {}) {
        const { days = 30, page = 1, pageSize = 50 } = params;
        return this.request(`/audit/logs/apps/${encodeURIComponent(appSlug)}?days=${days}&page=${page}&page_size=${pageSize}`);
    }

    async getAvailableActions() {
        return this.request('/audit/logs/actions');
    }

    // Export CSV
    async exportAuditLogs(params = {}) {
        const { days = 30, action = null, target = null } = params;
        let url = `/audit/export/logs?days=${days}`;
        if (action) url += `&action=${encodeURIComponent(action)}`;
        if (target) url += `&target=${encodeURIComponent(target)}`;
        
        // Téléchargement direct du fichier CSV
        const response = await fetch(`${API_BASE}${url}`, {
            headers: this.getHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Erreur lors de l\'export');
        }
        
        const blob = await response.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `audit_logs_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
    }

    async exportSessions(days = 30) {
        const url = `/audit/export/sessions?days=${days}`;
        
        const response = await fetch(`${API_BASE}${url}`, {
            headers: this.getHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Erreur lors de l\'export');
        }
        
        const blob = await response.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `sessions_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
    }

    // Log accès application
    async logAppAccess(appSlug) {
        return this.request(`/apps/access/${encodeURIComponent(appSlug)}`, {
            method: 'POST'
        });
    }

    // Docker Scanner
    async scanDockerContainers(includeStopped = false, showAll = false) {
        return this.request(`/apps/docker/scan?include_stopped=${includeStopped}&show_all=${showAll}`);
    }

    // Auto-register d'une application (crée app + service account automatiquement)
    async autoRegisterApp(slug, name, apiPort, frontendPort = null, isShared = false, description = null) {
        return this.request('/apps/auto-register', {
            method: 'POST',
            body: JSON.stringify({
                slug: slug,
                name: name,
                description: description,
                api_port: apiPort,
                frontend_port: frontendPort,
                is_shared: isShared
            })
        });
    }

    // ===== Approbation des Applications =====
    
    // Liste les applications en attente d'approbation
    async getPendingApps() {
        return this.request('/apps/pending');
    }
    
    // Approuver une application
    async approveApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/approve`, {
            method: 'POST',
            body: JSON.stringify({ approve: true })
        });
    }
    
    // Rejeter une application
    async rejectApp(slug, reason) {
        return this.request(`/apps/${encodeURIComponent(slug)}/approve`, {
            method: 'POST',
            body: JSON.stringify({ approve: false, reason: reason })
        });
    }
    
    // Supprimer une application en attente/rejetée
    async deletePendingApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/pending`, {
            method: 'DELETE'
        });
    }

    // ===== Status Monitoring =====
    
    // Vérifie le statut de toutes les applications (connectivité)
    async getAppsStatus() {
        return this.request('/apps/status');
    }

    // Récupère les stats Docker (CPU/RAM) des conteneurs
    async getDockerStats() {
        return this.request('/apps/docker-stats');
    }

    // Vérifie le statut de tous les Service Accounts
    async getServiceAccountsStatus() {
        return this.request('/service-accounts/status');
    }

    // ===== Error Logs =====
    
    // Liste les logs d'erreurs HTTP
    async getErrorLogs(params = {}) {
        const { page = 1, perPage = 50, errorCode, errorType, sourceApp, path, hours = 24 } = params;
        let url = `/audit/errors?page=${page}&per_page=${perPage}&hours=${hours}`;
        if (errorCode) url += `&error_code=${errorCode}`;
        if (errorType) url += `&error_type=${encodeURIComponent(errorType)}`;
        if (sourceApp) url += `&source_app=${encodeURIComponent(sourceApp)}`;
        if (path) url += `&path=${encodeURIComponent(path)}`;
        return this.request(url);
    }
    
    // Statistiques des erreurs
    async getErrorStats(hours = 24) {
        return this.request(`/audit/errors/stats?hours=${hours}`);
    }
    
    // Détail d'une erreur
    async getErrorDetail(errorId) {
        return this.request(`/audit/errors/${errorId}`);
    }
    
    // Export CSV des erreurs
    async exportErrors(hours = 24, errorCode = null) {
        let url = `${API_BASE}/audit/export/errors?hours=${hours}`;
        if (errorCode) url += `&error_code=${errorCode}`;
        
        const response = await fetch(url, {
            headers: this.getHeaders()
        });
        
        if (!response.ok) {
            throw new Error('Erreur lors de l\'export');
        }
        
        const blob = await response.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `errors_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
    }

    // ===== M2M / Discovery =====
    
    /**
     * Obtient un token M2M via client_credentials grant
     * @param {string} clientId - Le client_id du Service Account
     * @param {string} clientSecret - Le client_secret du Service Account
     * @returns {Promise<{access_token: string, token_type: string, expires_in: number}>}
     */
    async getM2MToken(clientId, clientSecret) {
        const formData = new URLSearchParams();
        formData.append('grant_type', 'client_credentials');
        formData.append('client_id', clientId);
        formData.append('client_secret', clientSecret);
        
        const response = await fetch(`${API_BASE}/auth/token`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Erreur d\'authentification M2M' }));
            throw new Error(error.detail);
        }
        
        return response.json();
    }
    
    /**
     * Découvre les services partagés via l'endpoint /api/apps/discover
     * @param {string} m2mToken - Token M2M obtenu via getM2MToken
     * @returns {Promise<{shared_services: Array, total_shared: number}>}
     */
    async discoverServices(m2mToken) {
        const response = await fetch(`${API_BASE}/apps/discover`, {
            headers: {
                'Authorization': `Bearer ${m2mToken}`,
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Erreur de découverte' }));
            throw new Error(error.detail);
        }
        
        return response.json();
    }
    
    // ===== Backup/Restore API =====
    
    /**
     * Exporte l'environnement complet (backup)
     * @param {boolean} includeJwtKeys - Inclure les clés JWT
     * @param {boolean} includeSaSecrets - Inclure les secrets des service accounts
     * @param {boolean} includeAppBackups - Inclure les backups d'applications (retourne ZIP)
     * @returns {Promise<Object|Blob>} Données du backup (JSON) ou Blob (ZIP)
     */
    async exportBackup(includeJwtKeys = true, includeSaSecrets = true, includeAppBackups = false) {
        const params = new URLSearchParams();
        params.append('include_jwt_keys', includeJwtKeys);
        params.append('include_sa_secrets', includeSaSecrets);
        params.append('include_app_backups', includeAppBackups);
        
        if (includeAppBackups) {
            // Retourne un Blob ZIP via l'endpoint download
            const response = await fetch(`${API_BASE}/admin/backup/export/download?${params}`, {
                headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {}
            });
            if (!response.ok) {
                const err = await response.json().catch(() => ({ detail: response.statusText }));
                throw new Error(err.detail || 'Erreur export');
            }
            return await response.blob();
        }
        
        return this.request(`/admin/backup/export?${params}`);
    }
    
    /**
     * Importe un environnement depuis un backup JSON
     * @param {Object} backupData - Données du backup
     * @param {string} importMode - Mode d'import: skip, update, error
     * @param {boolean} restoreJwtKeys - Restaurer les clés JWT
     * @returns {Promise<Object>} Résultat de l'import
     */
    async importBackup(backupData, importMode = 'skip', restoreJwtKeys = false) {
        const payload = {
            import_mode: importMode,
            restore_jwt_keys: restoreJwtKeys,
            users: backupData.users || [],
            applications: backupData.applications || [],
            user_app_roles: backupData.user_app_roles || [],
            service_accounts: backupData.service_accounts || [],
            app_backups: backupData.app_backups || [],
            jwt_private_key_b64: backupData.jwt_private_key_b64,
            jwt_public_key_b64: backupData.jwt_public_key_b64
        };
        
        return this.request('/admin/backup/import', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    }

    /**
     * Importe un environnement depuis un fichier uploadé (JSON ou ZIP)
     * @param {File} file - Fichier JSON ou ZIP
     * @param {string} importMode - Mode d'import: skip, update, error
     * @param {boolean} restoreJwtKeys - Restaurer les clés JWT
     * @returns {Promise<Object>} Résultat de l'import
     */
    async importBackupFile(file, importMode = 'skip', restoreJwtKeys = false) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('import_mode', importMode);
        formData.append('restore_jwt_keys', restoreJwtKeys);
        
        const response = await fetch(`${API_BASE}/admin/backup/import/upload`, {
            method: 'POST',
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
            body: formData
        });
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || 'Erreur import');
        }
        
        return await response.json();
    }

    /**
     * Prévisualise un fichier d'import (JSON ou ZIP) sans l'importer
     * @param {File} file - Fichier JSON ou ZIP
     * @returns {Promise<Object>} Aperçu du contenu du fichier
     */
    async previewBackupUpload(file) {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch(`${API_BASE}/admin/backup/import/preview`, {
            method: 'POST',
            headers: this.token ? { 'Authorization': `Bearer ${this.token}` } : {},
            body: formData
        });
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || 'Erreur preview');
        }
        
        return await response.json();
    }
    
    // ===== Bulk Operations API =====
    
    /**
     * Crée plusieurs utilisateurs en masse
     * @param {Array} users - Liste des utilisateurs à créer
     * @returns {Promise<Object>} Résultat de la création
     */
    async bulkCreateUsers(users) {
        return this.request('/users/bulk/create', {
            method: 'POST',
            body: JSON.stringify({ users })
        });
    }
    
    /**
     * Met à jour les rôles de plusieurs utilisateurs en masse
     * @param {Array} updates - Liste des mises à jour de rôles
     * @returns {Promise<Object>} Résultat de la mise à jour
     */
    async bulkUpdateUserRoles(updates) {
        return this.request('/users/bulk/roles', {
            method: 'POST',
            body: JSON.stringify({ updates })
        });
    }
    
    /**
     * Supprime plusieurs utilisateurs en masse
     * @param {Array} userIds - Liste des IDs à supprimer
     * @param {boolean} confirm - Confirmation de suppression
     * @returns {Promise<Object>} Résultat de la suppression
     */
    async bulkDeleteUsers(userIds, confirm = true) {
        return this.request('/users/bulk/delete', {
            method: 'POST',
            body: JSON.stringify({ user_ids: userIds, confirm })
        });
    }
    
    /**
     * Assigne des rôles sur des applications à plusieurs utilisateurs
     * @param {Array} assignments - Liste des assignations
     * @returns {Promise<Object>} Résultat de l'assignation
     */
    async bulkAssignAppRoles(assignments) {
        return this.request('/users/bulk/assign-app-roles', {
            method: 'POST',
            body: JSON.stringify({ assignments })
        });
    }
    
    /**
     * Supprime l'accès à des applications pour plusieurs utilisateurs
     * @param {Array} removals - Liste des suppressions d'accès
     * @returns {Promise<Object>} Résultat de la suppression
     */
    async bulkRemoveAppAccess(removals) {
        return this.request('/users/bulk/remove-app-access', {
            method: 'POST',
            body: JSON.stringify({ removals })
        });
    }
}

const api = new HubAPI();
