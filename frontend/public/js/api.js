/**
 * API Client for Hub Authentication
 * Supporte les refresh tokens pour maintenir les sessions
 */

// L'API est maintenant proxifiée via Nginx sur le même hôte/port
const API_BASE = '/api';

class HubAPI {
    constructor() {
        this.token = localStorage.getItem('hub_token');
        this.refreshToken = localStorage.getItem('hub_refresh_token');
        this._refreshPromise = null; // Pour éviter les appels concurrents
    }

    setToken(token) {
        this.token = token;
        localStorage.setItem('hub_token', token);
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
        localStorage.removeItem('hub_token');
        localStorage.removeItem('hub_refresh_token');
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
        // Éviter les appels concurrents
        if (this._refreshPromise) {
            return this._refreshPromise;
        }

        if (!this.refreshToken) {
            return false;
        }

        this._refreshPromise = (async () => {
            try {
                const response = await fetch(`${API_BASE}/auth/token/refresh`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ refresh_token: this.refreshToken })
                });

                if (!response.ok) {
                    // Refresh token expiré ou invalide
                    this.clearToken();
                    return false;
                }

                const data = await response.json();
                this.setToken(data.access_token);
                if (data.refresh_token) {
                    this.setRefreshToken(data.refresh_token);
                }
                return true;
            } catch (error) {
                console.error('Refresh token failed:', error);
                this.clearToken();
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
                } else {
                    // Refresh échoué - nettoyer et laisser l'appelant gérer
                    this.clearToken();
                    throw new Error('Session expirée');
                }
            }

            if (response.status === 401) {
                // Token invalide - nettoyer et laisser l'appelant gérer
                this.clearToken();
                throw new Error('Session expirée');
            }

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: 'Erreur serveur' }));
                throw new Error(error.detail || 'Erreur serveur');
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

    async deleteApp(id) {
        return this.request(`/apps/${id}`, {
            method: 'DELETE'
        });
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
}

const api = new HubAPI();
