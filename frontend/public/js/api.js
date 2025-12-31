/**
 * API Client for Hub Authentication
 */

// L'API est maintenant proxifiée via Nginx sur le même hôte/port
const API_BASE = '/api';

class HubAPI {
    constructor() {
        this.token = localStorage.getItem('hub_token');
    }

    setToken(token) {
        this.token = token;
        localStorage.setItem('hub_token', token);
    }

    clearToken() {
        this.token = null;
        localStorage.removeItem('hub_token');
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
            const response = await fetch(url, config);
            
            if (response.status === 401) {
                this.clearToken();
                window.location.reload();
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
        return data;
    }

    async getCurrentUser() {
        return this.request('/auth/me');
    }

    async logout() {
        try {
            await this.request('/auth/logout', { method: 'POST' });
        } finally {
            this.clearToken();
        }
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

    async setUserApps(userId, appIds) {
        return this.request(`/users/${userId}/apps`, {
            method: 'POST',
            body: JSON.stringify(appIds)
        });
    }
}

const api = new HubAPI();
