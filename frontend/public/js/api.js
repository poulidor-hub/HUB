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
     * Parse le payload JWT.
     */
    _parseTokenPayload(token) {
        if (!token) {
            return null;
        }

        try {
            return JSON.parse(atob(token.split('.')[1]));
        } catch (e) {
            console.warn('Impossible de parser le token JWT:', e);
            return null;
        }
    }

    /**
     * Parse le payload JWT pour extraire l'expiration
     */
    _parseTokenExpiry(token) {
        const payload = this._parseTokenPayload(token);
        return payload?.exp ? new Date(payload.exp * 1000) : null;
    }

    /**
     * Vérifie si le token courant contient déjà l'accès à l'application demandée.
     */
    _tokenHasAppAccess(token, appSlug) {
        if (!appSlug) {
            return true;
        }

        const payload = this._parseTokenPayload(token);
        return Boolean(payload?.apps?.[appSlug]);
    }

    /**
     * Retourne la durée de validité restante du token en millisecondes.
     */
    _getTokenRemainingMs(token) {
        const expiry = this._parseTokenExpiry(token);
        if (!expiry) {
            return Number.POSITIVE_INFINITY;
        }

        return expiry.getTime() - Date.now();
    }

    /**
     * Vérifie si le token d'accès courant est encore exploitable.
     */
    _hasUsableAccessToken(appSlug = null, minimumValidityMs = 0) {
        if (!this.token) {
            return false;
        }

        if (!this._tokenHasAppAccess(this.token, appSlug)) {
            return false;
        }

        return this._getTokenRemainingMs(this.token) >= minimumValidityMs;
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
     * @param {string|null} appSlug slug de l'application à ouvrir pour forcer une resync des droits si nécessaire
     * @returns {Promise<void>}
     * @throws {Error} si le token ne peut pas être obtenu/rafraîchi
     */
    async ensureFreshToken(appSlug = null) {
        if (!this.token) throw new Error('Non authentifié');

        if (!this.refreshToken) {
            if (!this._hasUsableAccessToken(appSlug, 30 * 1000)) {
                throw new Error('Impossible de rafraîchir le token');
            }
            return;
        }

        // Toujours tenter un refresh avant d'ouvrir une app externe.
        // Cela resynchronise les droits embarqués dans le JWT.
        console.log('🔄 Refresh token avant ouverture app (sync droits)...');
        let refreshed = await this.tryRefreshToken();
        if (!refreshed) {
            console.warn('⚠️ Premier refresh échoué, nouvelle tentative immédiate...');
            refreshed = await this.tryRefreshToken();
        }

        if (!refreshed) {
            if (!this._hasUsableAccessToken(appSlug, 30 * 1000)) {
                throw new Error('Impossible de rafraîchir le token');
            }
            console.warn('⚠️ Refresh échoué, utilisation du token existant');
        }

        if (!this._hasUsableAccessToken(appSlug, 30 * 1000)) {
            throw new Error(appSlug
                ? 'Le token synchronisé ne contient pas l’accès à cette application'
                : 'Le token synchronisé n’est pas assez récent');
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
                    // Refresh token invalide
                    // Ne supprimer que si c'est une vraie erreur 401 (token révoqué/expiré)
                    // Pas sur les erreurs transitoires (500, réseau, etc.)
                    if (response.status === 401) {
                        console.warn('Refresh token invalide (401), suppression');
                        this.setRefreshToken(null);
                    } else {
                        console.warn('Refresh token échoué (status ' + response.status + '), token conservé');
                    }
                    return false;
                }

                const data = await response.json();
                this.setToken(data.access_token);
                if (data.refresh_token) {
                    this.setRefreshToken(data.refresh_token);
                }
                if (data.beacon_token) {
                    window.dispatchEvent(new CustomEvent('hub:beaconTokenUpdated', {
                        detail: { beacon_token: data.beacon_token }
                    }));
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

        // Timeout via AbortController (30s par défaut, configurable via options.timeout)
        const timeoutMs = options.timeout || 30000;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        config.signal = config.signal || controller.signal;

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
                        // Erreur de validation Pydantic (format simplifié : field + message)
                        errorMessage = error.detail.map(e => {
                            // Format serveur : { field, message }
                            // Fallback sur l'ancien format Pydantic : { loc, msg }
                            const field = e.field || (e.loc ? e.loc.slice(1).join('.') : '');
                            const msg = e.message || e.msg || '';
                            return field ? `${field}: ${msg}` : msg;
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
            if (error.name === 'AbortError') {
                throw new Error('Requête expirée (timeout)');
            }
            console.error('API Error:', error);
            throw error;
        } finally {
            clearTimeout(timeoutId);
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

    async importVpnConfig(config) {
        return this.request('/vpn/import', {
            method: 'POST',
            body: JSON.stringify({ config })
        });
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

    async totpSetup() {
        return this.request('/auth/totp/setup', { method: 'POST' });
    }

    async totpConfirm(code) {
        return this.request('/auth/totp/confirm', {
            method: 'POST',
            body: JSON.stringify({ code })
        });
    }

    async totpDisable(currentPassword) {
        return this.request('/auth/totp/disable', {
            method: 'POST',
            body: JSON.stringify({ current_password: currentPassword })
        });
    }

    async recoverVerify(username, totpCode) {
        const resp = await fetch(`${API_BASE}/auth/recover/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, totp_code: totpCode })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.detail || 'Code de récupération invalide');
        }
        return data;
    }

    async recoverReset(resetToken, newPassword) {
        const resp = await fetch(`${API_BASE}/auth/recover/reset`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reset_token: resetToken, new_password: newPassword })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.detail || 'Échec de la réinitialisation');
        }
        return data;
    }

    async adminDisableTotp(userId) {
        return this.request(`/users/${userId}/totp/disable`, { method: 'POST' });
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

    async getSystemTime() {
        return this.request('/system/time');
    }

    async getMode() {
        return this.request('/config/mode');
    }

    // Apps
    async getApps(workshopFilter = 'false') {
        return this.request(`/apps/?workshop_filter=${encodeURIComponent(workshopFilter)}`);
    }

    async getAllApps(workshopFilter = 'false') {
        return this.request(`/apps/all?workshop_filter=${encodeURIComponent(workshopFilter)}`);
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

    async createAppLaunch(appSlug) {
        return this.request(`/auth/apps/${encodeURIComponent(appSlug)}/launch`, {
            method: 'POST'
        });
    }

    async addFavorite(appId) {
        return this.request(`/apps/${appId}/favorite`, { method: 'POST' });
    }

    async removeFavorite(appId) {
        return this.request(`/apps/${appId}/favorite`, { method: 'DELETE' });
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

    // App Groups (groupes visuels du dashboard)
    async getAppGroups() {
        return this.request('/app-groups/');
    }

    async createAppGroup(groupData) {
        return this.request('/app-groups/', {
            method: 'POST',
            body: JSON.stringify(groupData)
        });
    }

    async updateAppGroup(groupId, groupData) {
        return this.request(`/app-groups/${groupId}`, {
            method: 'PATCH',
            body: JSON.stringify(groupData)
        });
    }

    async deleteAppGroup(groupId) {
        return this.request(`/app-groups/${groupId}`, {
            method: 'DELETE'
        });
    }

    async getAppDockerInfo(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/docker-info`);
    }

    // Users
    async getUsers(params = {}) {
        const { page = 0, perPage = 50, search, role, appSlug, profileId, isActive, appRoleOverride } = params;
        let url = `/users/?page=${page}&per_page=${perPage}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (role) url += `&role=${encodeURIComponent(role)}`;
        if (appSlug) url += `&app_slug=${encodeURIComponent(appSlug)}`;
        if (profileId !== undefined && profileId !== null && profileId !== '') url += `&profile_id=${profileId}`;
        if (isActive !== undefined && isActive !== null && isActive !== '') url += `&is_active=${isActive}`;
        if (appRoleOverride !== undefined && appRoleOverride !== null && appRoleOverride !== '') url += `&app_role_override=${appRoleOverride}`;
        return this.request(url);
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

    // ─── Tags API ────────────────────────────────────────────────

    async getTagCategories() {
        return this.request('/tags/categories');
    }

    async getActiveTagCategories() {
        return this.request('/tags/categories/active');
    }

    async createTagCategory(data) {
        return this.request('/tags/categories', {
            method: 'POST', body: JSON.stringify(data)
        });
    }

    async updateTagCategory(slug, data) {
        return this.request(`/tags/categories/${encodeURIComponent(slug)}`, {
            method: 'PATCH', body: JSON.stringify(data)
        });
    }

    async deleteTagCategory(slug) {
        return this.request(`/tags/categories/${encodeURIComponent(slug)}`, {
            method: 'DELETE'
        });
    }

    async importTagCsv(slug, file, mode = 'replace') {
        const url = `/tags/categories/${encodeURIComponent(slug)}/import-csv?mode=${mode}`;
        const formData = new FormData();
        formData.append('file', file);
        const headers = {};
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const response = await fetch(`${API_BASE}${url}`, {
            method: 'POST', headers: headers, body: formData
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: 'Erreur inconnue' }));
            throw new Error(err.detail || `Erreur ${response.status}`);
        }
        if (response.status === 204) return null;
        return await response.json();
    }

    async importTagM2m(slug) {
        return this.request(`/tags/categories/${encodeURIComponent(slug)}/import-m2m`, {
            method: 'POST'
        });
    }

    async getMyTags() {
        return this.request('/tags/my');
    }

    async updateMyTags(data) {
        return this.request('/tags/my', {
            method: 'PUT', body: JSON.stringify(data)
        });
    }

    async acknowledgeMyTags(categories) {
        return this.request('/tags/my/acknowledge', {
            method: 'POST', body: JSON.stringify({ categories })
        });
    }

    async getUsersWithTags() {
        return this.request('/tags/users');
    }

    async getUserTags(username) {
        return this.request(`/tags/user/${encodeURIComponent(username)}`);
    }

    async setUserTags(username, data) {
        return this.request(`/tags/user/${encodeURIComponent(username)}`, {
            method: 'PUT', body: JSON.stringify(data)
        });
    }

    async discoverSourceTables(appId) {
        return this.request(`/tags/discover/tables?app_id=${appId}`);
    }

    async discoverSourceColumns(appId, table) {
        return this.request(`/tags/discover/columns?app_id=${appId}&table=${encodeURIComponent(table)}`);
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

    // ===== OIDC Clients =====
    async getOidcClients()               { return this.request('/oidc/clients'); }
    async createOidcClient(data)         { return this.request('/oidc/clients', { method: 'POST', body: JSON.stringify(data) }); }
    async updateOidcClient(id, data)     { return this.request(`/oidc/clients/${id}`, { method: 'PUT', body: JSON.stringify(data) }); }
    async toggleOidcClientActive(id)     { return this.request(`/oidc/clients/${id}/toggle-active`, { method: 'PATCH' }); }
    async regenerateOidcClientSecret(id) { return this.request(`/oidc/clients/${id}/regenerate-secret`, { method: 'POST' }); }
    async deleteOidcClient(id)           { return this.request(`/oidc/clients/${id}`, { method: 'DELETE' }); }

    // Audit
    async getActiveSessions() {
        return this.request('/audit/sessions/active');
    }

    async getSessionHistory(days = 7, userId = null, username = null) {
        let url = `/audit/sessions/history?days=${days}`;
        if (userId) url += `&user_id=${userId}`;
        if (username) url += `&username=${encodeURIComponent(username)}`;
        return this.request(url);
    }

    async getConnectionStats(days = 7) {
        return this.request(`/audit/stats?days=${days}`);
    }

    async getAuditKpis(days = 7) {
        return this.request(`/audit/overview/kpis?days=${days}`);
    }

    async getAuditAlerts(days = 7) {
        return this.request(`/audit/overview/alerts?days=${days}`);
    }

    // Audit - visualisations d'usage (segments par application)
    async getUserUsage(userId, date) {
        return this.request(`/audit/usage/user/${userId}?date=${encodeURIComponent(date)}`);
    }

    async getAppsUsageStats(days = 7, appSlug = null) {
        let url = `/audit/usage/apps?days=${days}`;
        if (appSlug) url += `&app=${encodeURIComponent(appSlug)}`;
        return this.request(url);
    }

    async getUsageHeatmap(days = 7) {
        return this.request(`/audit/usage/heatmap?days=${days}`);
    }

    async revokeSession(sessionId) {
        return this.request(`/audit/sessions/${sessionId}/revoke`, { method: 'POST' });
    }

    async getUserStats(userId, days = 30) {
        return this.request(`/audit/stats/user/${userId}?days=${days}`);
    }

    // Audit Logs détaillés
    async getAuditLogs(params = {}) {
        const { days = 30, action = null, target = null, userId = null, username = null, ip = null, page = 1, pageSize = 50 } = params;
        let url = `/audit/logs?days=${days}&page=${page}&page_size=${pageSize}`;
        if (action) url += `&action=${encodeURIComponent(action)}`;
        if (target) url += `&target=${encodeURIComponent(target)}`;
        if (userId) url += `&user_id=${userId}`;
        if (username) url += `&username=${encodeURIComponent(username)}`;
        if (ip) url += `&ip=${encodeURIComponent(ip)}`;
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
        const { days = 30, action = null, target = null, userId = null, username = null } = params;
        let url = `/audit/export/logs?days=${days}`;
        if (action) url += `&action=${encodeURIComponent(action)}`;
        if (target) url += `&target=${encodeURIComponent(target)}`;
        if (userId) url += `&user_id=${userId}`;
        if (username) url += `&username=${encodeURIComponent(username)}`;
        
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

    // Pool d'allocation des ports (admin)
    async getPortsStatus() {
        return this.request('/apps/admin/ports/status');
    }

    async deletePortAllocation(allocId) {
        return this.request(`/apps/admin/ports/allocations/${encodeURIComponent(allocId)}`, {
            method: 'DELETE'
        });
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

    // Import lot d'applications (crée plusieurs apps + SA en une seule opération)
    async batchImportApps(apps) {
        return this.request('/apps/batch-import', {
            method: 'POST',
            body: JSON.stringify({ apps })
        });
    }

    // Bascule l'état actif/inactif d'une application
    async toggleAppActive(appId) {
        return this.request(`/apps/${appId}/toggle-active`, {
            method: 'PATCH'
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

    // Purger les error logs
    async purgeErrors(hours = null, errorCode = null) {
        let url = '/audit/errors';
        const params = [];
        if (hours) params.push(`hours=${hours}`);
        if (errorCode) params.push(`error_code=${errorCode}`);
        if (params.length > 0) url += '?' + params.join('&');
        return this.request(url, { method: 'DELETE' });
    }

    // Marquer les erreurs comme consultées
    async acknowledgeErrors() {
        return this.request('/audit/errors/acknowledge', { method: 'POST' });
    }

    // Compter les nouvelles erreurs depuis un timestamp
    async getNewErrorsCount(since = null) {
        let url = '/audit/errors/new-count';
        if (since) url += `?since=${encodeURIComponent(since)}`;
        return this.request(url);
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

    // ===== Apps Health Dashboard (admin) =====

    /**
     * Sonde toutes les apps actives pour le dashboard de sante
     * @returns {Promise<{apps: Array, summary: {online, offline, degraded, total}}>}
     */
    async getAppsHealth() {
        return this.request('/apps/health');
    }

    /**
     * Redemarre le conteneur Docker d'une application
     * @param {string} slug - Slug de l'application
     * @returns {Promise<{ok: boolean, container_name: string, status: string}>}
     */
    async restartApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/restart`, {
            method: 'POST'
        });
    }

    /**
     * Demarre une application via hubctl
     * @param {string} slug
     */
    async startApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/start`, {
            method: 'POST'
        });
    }

    /**
     * Arrete une application via hubctl
     * @param {string} slug
     */
    async stopApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/stop`, {
            method: 'POST'
        });
    }

    /**
     * Recupere les logs recents d'une app
     * @param {string} slug
     * @param {number} [tail=100]
     * @param {string} [service='api']
     */
    async getAppLogs(slug, tail, service) {
        var params = new URLSearchParams();
        if (tail) params.set('tail', tail);
        if (service) params.set('service', service);
        return this.request(`/apps/${encodeURIComponent(slug)}/logs?${params.toString()}`);
    }

    /**
     * Verifie si une mise a jour est disponible (GitHub)
     * @param {string} slug
     * @returns {Promise<{update_available, current_version, latest_version, release_url, published_at, repo_configured}>}
     */
    async checkUpdate(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/update-check`);
    }

    /**
     * Recupere le CHANGELOG.md depuis GitHub
     * @param {string} slug
     * @returns {Promise<{ok, markdown, repo_configured}>}
     */
    async getChangelog(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/changelog`);
    }

    /**
     * Liste l'historique des releases GitHub
     * @param {string} slug
     * @returns {Promise<{ok, releases: Array<{tag, name, published_at, body, html_url}>, repo_configured}>}
     */
    async getReleases(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/releases`);
    }

    /**
     * Compare deux tags via GitHub (fichiers modifies + patches)
     * @param {string} slug
     * @param {string} fromTag
     * @param {string} toTag
     * @returns {Promise<{ok, from_tag, to_tag, total_commits, files: Array<{filename, status, additions, deletions, patch}>, html_url}>}
     */
    async getDiff(slug, fromTag, toTag) {
        var params = new URLSearchParams({ from: fromTag, to: toTag });
        return this.request(`/apps/${encodeURIComponent(slug)}/diff?${params.toString()}`);
    }
    
    // ===== Backup/Restore API =====

    /**
     * Liste les backups d'applications en cours d'exécution (pending/running)
     * @returns {Promise<{items: Array, total: number}>}
     */
    async listInProgressBackups() {
        return this.request('/admin/backups/in-progress');
    }

    async getAllBackups() {
        return this.request('/admin/backups');
    }

    /**
     * Annule (kill-switch manuel) un backup bloqué en pending/running.
     * @param {string} backupId - identifiant du backup à annuler
     */
    async cancelInProgressBackup(backupId) {
        return this.request(`/admin/backups/${encodeURIComponent(backupId)}/cancel`, {
            method: 'POST'
        });
    }

    async listBackupClusters() {
        return this.request('/admin/backup-clusters/');
    }

    async createBackupCluster(payload) {
        return this.request('/admin/backup-clusters/', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    }

    async updateBackupCluster(clusterId, payload) {
        return this.request(`/admin/backup-clusters/${encodeURIComponent(clusterId)}`, {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
    }

    async deleteBackupCluster(clusterId) {
        return this.request(`/admin/backup-clusters/${encodeURIComponent(clusterId)}`, {
            method: 'DELETE'
        });
    }

    async updateBackupClusterConfig(clusterId, config) {
        return this.request(`/admin/backup-clusters/${encodeURIComponent(clusterId)}/backup/config`, {
            method: 'PUT',
            body: JSON.stringify(config)
        });
    }

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
    async importBackup(backupData, importMode = 'skip', restoreJwtKeys = false, includeOptions = {}) {
        const payload = {
            import_mode: importMode,
            restore_jwt_keys: restoreJwtKeys,
            include_users: includeOptions.include_users !== false,
            include_applications: includeOptions.include_applications !== false,
            include_user_app_roles: includeOptions.include_user_app_roles !== false,
            include_service_accounts: includeOptions.include_service_accounts !== false,
            include_app_backups: includeOptions.include_app_backups !== false,
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
    async importBackupFile(file, importMode = 'skip', restoreJwtKeys = false, includeOptions = {}) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('import_mode', importMode);
        formData.append('restore_jwt_keys', restoreJwtKeys);
        formData.append('include_users', includeOptions.include_users !== false);
        formData.append('include_applications', includeOptions.include_applications !== false);
        formData.append('include_user_app_roles', includeOptions.include_user_app_roles !== false);
        formData.append('include_service_accounts', includeOptions.include_service_accounts !== false);
        formData.append('include_app_backups', includeOptions.include_app_backups !== false);
        
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

    // ===== Profiles API =====
    
    async getProfiles() {
        return this.request('/profiles/');
    }
    
    async createProfile(profileData) {
        return this.request('/profiles/', {
            method: 'POST',
            body: JSON.stringify(profileData)
        });
    }
    
    async updateProfile(id, profileData) {
        return this.request(`/profiles/${id}`, {
            method: 'PUT',
            body: JSON.stringify(profileData)
        });
    }
    
    async deleteProfile(id) {
        return this.request(`/profiles/${id}`, {
            method: 'DELETE'
        });
    }
    
    async applyProfile(profileId, userIds, syncRoles = true) {
        return this.request(`/profiles/${profileId}/apply`, {
            method: 'POST',
            body: JSON.stringify({ user_ids: userIds, sync_roles: syncRoles })
        });
    }
    
    async syncProfile(profileId) {
        return this.request(`/profiles/${profileId}/sync`, {
            method: 'POST'
        });
    }
    
    async detachProfile(profileId, userIds) {
        return this.request(`/profiles/${profileId}/detach`, {
            method: 'POST',
            body: JSON.stringify({ user_ids: userIds, sync_roles: true })
        });
    }
    
    // Rôles disponibles
    async getAvailableRoles() {
        return this.request('/users/roles/available');
    }

    // ===== CSV Import =====

    async downloadCsvTemplate() {
        const response = await fetch(`${API_BASE}/users/csv/template`, {
            headers: this.getHeaders()
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Erreur téléchargement' }));
            throw new Error(error.detail || 'Erreur téléchargement template');
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'import_users_template.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    async previewCsvUpload(file) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`${API_BASE}/users/csv/preview`, {
            method: 'POST',
            headers: {
                'Authorization': this.getHeaders()['Authorization']
            },
            body: formData
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Erreur analyse CSV' }));
            throw new Error(error.detail || 'Erreur analyse CSV');
        }
        return response.json();
    }

    // =========================================================================
    // MESSAGES
    // =========================================================================

    async sendMessage(data) {
        return this.request('/messages/send', { method: 'POST', body: JSON.stringify(data) });
    }

    async getMessages(params = {}) {
        const { page = 1, perPage = 50, priority, senderType, senderApp, targetUserId, targetType, search, hours, status, expiringWithinHours } = params;
        let url = `/messages/?page=${page}&per_page=${perPage}`;
        if (priority) url += `&priority=${encodeURIComponent(priority)}`;
        if (senderType) url += `&sender_type=${encodeURIComponent(senderType)}`;
        if (senderApp) url += `&sender_app=${encodeURIComponent(senderApp)}`;
        if (targetUserId) url += `&target_user_id=${targetUserId}`;
        if (targetType) url += `&target_type=${encodeURIComponent(targetType)}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (hours) url += `&hours=${hours}`;
        if (status) url += `&status=${encodeURIComponent(status)}`;
        if (expiringWithinHours) url += `&expiring_within_hours=${expiringWithinHours}`;
        return this.request(url);
    }

    async getMessageStats(hours = null, targetType = null) {
        let url = '/messages/stats';
        const p = [];
        if (hours) p.push(`hours=${hours}`);
        if (targetType) p.push(`target_type=${encodeURIComponent(targetType)}`);
        if (p.length > 0) url += '?' + p.join('&');
        return this.request(url);
    }

    async getMessageDetail(id) {
        return this.request(`/messages/${id}`);
    }

    async getMyMessages() {
        return this.request('/messages/me');
    }

    async getUnreadCount() {
        return this.request('/messages/me/unread-count');
    }

    async markMessageRead(id) {
        return this.request(`/messages/${id}/read`, { method: 'POST' });
    }

    async markAllMessagesRead() {
        return this.request('/messages/read-all', { method: 'POST' });
    }

    // Lifecycle (admin)
    async archiveMessage(id) {
        return this.request(`/messages/${id}/archive`, { method: 'POST' });
    }

    async restoreMessage(id) {
        return this.request(`/messages/${id}/restore`, { method: 'POST' });
    }

    async hardDeleteMessage(id) {
        return this.request(`/messages/${id}/hard`, { method: 'DELETE' });
    }

    // Engagement (user)
    async markMessageDelivered(id) {
        return this.request(`/messages/${id}/delivered`, { method: 'POST' });
    }

    async markMessageClicked(id) {
        return this.request(`/messages/${id}/click`, { method: 'POST' });
    }

    async purgeMessages(params = {}) {
        const { hours, priority, senderApp, targetType, hard } = params;
        let url = '/messages/';
        const p = [];
        if (hours) p.push(`hours=${hours}`);
        if (priority) p.push(`priority=${encodeURIComponent(priority)}`);
        if (senderApp) p.push(`sender_app=${encodeURIComponent(senderApp)}`);
        if (targetType) p.push(`target_type=${encodeURIComponent(targetType)}`);
        if (hard) p.push('hard=true');
        if (p.length > 0) url += '?' + p.join('&');
        return this.request(url, { method: 'DELETE' });
    }

    async deleteMessagesByIds(ids, hard = false) {
        const url = '/messages/batch' + (hard ? '?hard=true' : '');
        return this.request(url, {
            method: 'DELETE',
            body: JSON.stringify({ ids }),
        });
    }

    async exportMessages(params = {}) {
        const { hours, priority, senderApp, targetType } = params;
        let url = `${API_BASE}/messages/export`;
        const p = [];
        if (hours) p.push(`hours=${hours}`);
        if (priority) p.push(`priority=${encodeURIComponent(priority)}`);
        if (senderApp) p.push(`sender_app=${encodeURIComponent(senderApp)}`);
        if (targetType) p.push(`target_type=${encodeURIComponent(targetType)}`);
        if (p.length > 0) url += '?' + p.join('&');
        const response = await fetch(url, { headers: this.getHeaders() });
        if (!response.ok) throw new Error('Export échoué');
        const blob = await response.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'messages_export.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
    }

    // =========================================================================
    // MESSAGE TEMPLATES
    // =========================================================================

    async getTemplates() {
        return this.request('/messages/templates');
    }

    async createTemplate(data) {
        return this.request('/messages/templates', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    }

    async updateTemplate(id, data) {
        return this.request(`/messages/templates/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    }

    async deleteTemplate(id) {
        return this.request(`/messages/templates/${id}`, {
            method: 'DELETE',
        });
    }

    // Account Requests (admin)
    async getAccountRequests(params = {}) {
        let url = '/account-requests/?';
        const p = [];
        if (params.status) p.push(`status=${encodeURIComponent(params.status)}`);
        if (params.page) p.push(`page=${params.page}`);
        if (params.perPage) p.push(`per_page=${params.perPage}`);
        url += p.join('&');
        return this.request(url);
    }

    async getAccountRequestsCount() {
        return this.request('/account-requests/count');
    }

    async getAccountRequestNotificationAdmins() {
        return this.request('/account-requests/notification-admins');
    }

    async updateAccountRequestNotificationAdmins(adminIds = []) {
        return this.request('/account-requests/notification-admins', {
            method: 'PUT',
            body: JSON.stringify({ admin_ids: adminIds })
        });
    }

    async getAccountRequestFormConfig() {
        const resp = await fetch(`${API_BASE}/account-requests/form-config`);
        if (!resp.ok) throw new Error('Impossible de charger la configuration du formulaire');
        return resp.json();
    }

    async updateAccountRequestFormConfig(config) {
        return this.request('/account-requests/form-config', {
            method: 'PUT',
            body: JSON.stringify(config)
        });
    }

    async approveAccountRequest(id, data = {}) {
        return this.request(`/account-requests/${id}/approve`, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async rejectAccountRequest(id, data = {}) {
        return this.request(`/account-requests/${id}/reject`, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    // ===== GitHub Apps =====

    async listGithubRepos(params = {}) {
        let qs = new URLSearchParams();
        if (params.search) qs.set('search', params.search);
        if (params.owner) qs.set('owner', params.owner);
        if (params.include_forks) qs.set('include_forks', 'true');
        if (params.include_archived) qs.set('include_archived', 'true');
        return this.request(`/apps/github/repos${qs.toString() ? '?' + qs.toString() : ''}`);
    }

    async refreshGithubRepos() {
        return this.request('/apps/github/repos/refresh', { method: 'POST' });
    }

    async prepareAppFromRepo(repoFullName) {
        return this.request('/apps/github/prepare', {
            method: 'POST',
            body: JSON.stringify({ repo_full_name: repoFullName }),
            headers: { 'Content-Type': 'application/json' },
        });
    }

    // ===== Déploiement automatique =====
    async deployApp(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/deploy`, { method: 'POST' });
    }

    async getAppDeployStatus(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/deploy/status`);
    }

    // ===== Atelier (vue santé) =====
    async getWorkshopState(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop`);
    }

    async createWorkshop(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop`, { method: 'POST' });
    }

    async updateWorkshop(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop/update`, { method: 'POST' });
    }

    async shipWorkshop(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop/ship`, { method: 'POST' });
    }

    async removeWorkshop(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop`, { method: 'DELETE' });
    }

    async getWorkshopStatus(slug) {
        return this.request(`/apps/${encodeURIComponent(slug)}/workshop/status`);
    }

    async configureGit(slug, gitRepoUrl) {
        return this.request(`/apps/${encodeURIComponent(slug)}/git-config`, {
            method: 'POST', body: JSON.stringify({ git_repo_url: gitRepoUrl })
        });
    }

    // Settings
    async getSetting(key) {
        const resp = await fetch(`${API_BASE}/settings/${encodeURIComponent(key)}`);
        if (!resp.ok) throw new Error(`Setting ${key}: ${resp.status}`);
        return resp.json();
    }

    async updateSetting(key, value) {
        return this.request(`/settings/${encodeURIComponent(key)}`, {
            method: 'PUT',
            body: JSON.stringify({ value: String(value) })
        });
    }
}

const api = new HubAPI();
