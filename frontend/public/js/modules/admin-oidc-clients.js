/**
 * Hub Application - OIDC Clients admin
 * Pattern vanilla JS identique à admin-service-accounts.js.
 * Fonctions globales pour compatibilité avec handlers HTML inline.
 */

async function loadOidcClients() {
    try {
        allOidcClients = await api.getOidcClients();
        renderOidcClientsTable(allOidcClients);
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function renderOidcClientsTable(clients) {
    const container = document.getElementById('oidcClientsTable');
    if (!clients || clients.length === 0) {
        container.innerHTML = `
            <div class="text-center py-4 text-muted">
                <i class="bi bi-shield-lock fs-1"></i>
                <p class="mt-2">Aucun client OIDC configuré.</p>
            </div>`;
        return;
    }
    container.innerHTML = `
        <table class="table table-hover align-middle">
            <thead>
                <tr>
                    <th>Nom</th>
                    <th>Client ID</th>
                    <th>Redirect URIs</th>
                    <th>Actif</th>
                    <th class="text-end">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${clients.map(c => `
                    <tr data-oidc-id="${c.id}">
                        <td><strong>${escapeHtml(c.name)}</strong></td>
                        <td><code>${escapeHtml(c.client_id)}</code></td>
                        <td><small class="text-muted">${(c.redirect_uris || []).length} URI(s)</small></td>
                        <td>
                            <span class="badge ${c.is_active ? 'bg-success' : 'bg-secondary'}">
                                ${c.is_active ? 'Actif' : 'Désactivé'}
                            </span>
                        </td>
                        <td class="text-end">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-warning"
                                        onclick="regenerateOidcClientSecret(${c.id}, '${escapeHtml(c.name)}')"
                                        title="Régénérer le secret">
                                    <i class="bi bi-key"></i>
                                </button>
                                <button class="btn btn-outline-secondary"
                                        onclick="toggleOidcClientActive(${c.id})"
                                        title="${c.is_active ? 'Désactiver' : 'Activer'}">
                                    <i class="bi bi-power"></i>
                                </button>
                                <button class="btn btn-outline-primary"
                                        onclick="editOidcClient(${c.id})"
                                        title="Modifier">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-outline-danger"
                                        onclick="deleteOidcClient(${c.id}, '${escapeHtml(c.name)}')"
                                        title="Supprimer">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>`).join('')}
            </tbody>
        </table>`;
}

function showAddOidcClientForm() {
    document.getElementById('oidcFormTitle').textContent = 'Nouveau Client OIDC';
    document.getElementById('oidcClientForm').reset();
    document.getElementById('oidcClientIdPk').value = '';
    document.getElementById('oidcScopes').value = 'openid,profile,email';
    document.getElementById('oidcAutoApprove').checked = true;
    oidcClientFormModal.show();
}

function editOidcClient(id) {
    const c = allOidcClients.find(x => x.id === id);
    if (!c) return;
    document.getElementById('oidcFormTitle').textContent = `Modifier ${c.name}`;
    document.getElementById('oidcClientIdPk').value = c.id;
    document.getElementById('oidcName').value = c.name;
    document.getElementById('oidcRedirectUris').value = (c.redirect_uris || []).join('\n');
    document.getElementById('oidcScopes').value = (c.scopes || []).join(',');
    document.getElementById('oidcBackchannelLogoutUri').value = c.backchannel_logout_uri || '';
    document.getElementById('oidcAutoApprove').checked = !!c.auto_approve;
    oidcClientFormModal.show();
}

async function handleSaveOidcClient(event) {
    event.preventDefault();
    const id = document.getElementById('oidcClientIdPk').value;
    const data = {
        name: document.getElementById('oidcName').value.trim(),
        redirect_uris: document.getElementById('oidcRedirectUris').value
            .split('\n').map(s => s.trim()).filter(Boolean),
        scopes: document.getElementById('oidcScopes').value
            .split(',').map(s => s.trim()).filter(Boolean),
        backchannel_logout_uri: document.getElementById('oidcBackchannelLogoutUri').value.trim() || null,
        auto_approve: document.getElementById('oidcAutoApprove').checked,
    };
    try {
        if (id) {
            await api.updateOidcClient(id, data);
            showToast('Succès', 'Client OIDC modifié', 'success');
        } else {
            const result = await api.createOidcClient(data);
            showOidcClientCredentials(result.client_id, result.client_secret);
            showToast('Succès', 'Client OIDC créé', 'success');
        }
        oidcClientFormModal.hide();
        await loadOidcClients();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function showOidcClientCredentials(clientId, clientSecret) {
    document.getElementById('oidcCredClientId').value = clientId;
    document.getElementById('oidcCredClientSecret').value = clientSecret;
    oidcClientCredentialsModal.show();
}

async function regenerateOidcClientSecret(id, name) {
    if (!confirm(`Régénérer le secret pour "${name}" ?\n\nL'ancien secret sera immédiatement invalidé.`)) return;
    try {
        const result = await api.regenerateOidcClientSecret(id);
        showOidcClientCredentials(result.client_id, result.client_secret);
        showToast('Succès', 'Secret régénéré', 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function toggleOidcClientActive(id) {
    try {
        await api.toggleOidcClientActive(id);
        showToast('Succès', 'Statut modifié', 'success');
        await loadOidcClients();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function deleteOidcClient(id, name) {
    if (!confirm(`Supprimer le client OIDC "${name}" ?\n\nCette action est irréversible.`)) return;
    try {
        await api.deleteOidcClient(id);
        showToast('Succès', 'Client supprimé', 'success');
        await loadOidcClients();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function copyOidcValue(elementId) {
    const input = document.getElementById(elementId);
    if (input && input.value) {
        navigator.clipboard.writeText(input.value).then(() => {
            showToast('Copié', 'Valeur copiée dans le presse-papier', 'success');
        }).catch(() => {
            input.select();
            document.execCommand('copy');
            showToast('Copié', 'Valeur copiée dans le presse-papier', 'success');
        });
    }
}

// Brancher le formulaire au chargement
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('oidcClientForm');
    if (form) form.addEventListener('submit', handleSaveOidcClient);
});
