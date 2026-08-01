/**
 * Admin VPN WireGuard Module — Mode Serveur
 *
 * Gère l'onglet VPN dans l'interface d'administration :
 * - Init du serveur (keypair + endpoint)
 * - Toggle ON/OFF
 * - CRUD des peers (créer, supprimer, activer/désactiver)
 * - Téléchargement .conf + QR code
 * - Polling du statut
 */

const VPN_POLL_INTERVAL = 10000;
let vpnPollTimer = null;

// ── Status ──────────────────────────────────────────────

async function loadVpnStatus() {
    if (!api.token) return;
    try {
        const data = await api.request('/vpn/status');
        const toggle = document.getElementById('toggleVpn');
        if (toggle) toggle.checked = data.enabled === true;

        const badge = document.getElementById('vpnStatusBadge');
        if (badge) {
            badge.classList.remove('d-none', 'bg-success', 'bg-secondary', 'bg-warning');
            if (data.enabled && data.initialized) {
                badge.classList.add('bg-success');
                badge.textContent = `${data.connected_count}/${data.peer_count} connecté(s)`;
            } else if (data.enabled) {
                badge.classList.add('bg-warning');
                badge.textContent = 'En attente';
            } else {
                badge.classList.add('bg-secondary');
                badge.textContent = 'Arrêté';
            }
        }

        const countEl = document.getElementById('vpnPeerCount');
        if (countEl && data.initialized) {
            countEl.textContent = `${data.peer_count} peer(s) · ${data.connected_count} connecté(s)`;
        }
    } catch (e) {
        console.error('loadVpnStatus:', e);
    }
}

// ── Server config / init ────────────────────────────────

async function loadVpnServerConfig() {
    if (!api.token) return;
    try {
        const data = await api.request('/vpn/server-config');
        const initPanel = document.getElementById('vpnInitPanel');
        const mainPanel = document.getElementById('vpnMainPanel');

        if (!data.initialized) {
            if (initPanel) initPanel.classList.remove('d-none');
            if (mainPanel) mainPanel.classList.add('d-none');
            return;
        }

        if (initPanel) initPanel.classList.add('d-none');
        if (mainPanel) mainPanel.classList.remove('d-none');

        const ep = document.getElementById('vpnSettingsEndpoint');
        const port = document.getElementById('vpnSettingsPort');
        const pubkey = document.getElementById('vpnServerPubkey');
        if (ep) ep.value = data.endpoint || '';
        if (port) port.value = data.listen_port || 51820;
        if (pubkey) pubkey.value = data.server_public_key || '';
    } catch (e) {
        console.error('loadVpnServerConfig:', e);
    }
}

async function handleVpnInit() {
    const endpoint = document.getElementById('vpnInitEndpoint')?.value?.trim();
    const port = parseInt(document.getElementById('vpnInitPort')?.value || '51820');

    if (!endpoint) {
        showToast('VPN', 'Endpoint requis', 'warning');
        return;
    }

    try {
        await api.request('/vpn/init', {
            method: 'POST',
            body: JSON.stringify({ endpoint, listen_port: port })
        });
        showToast('VPN', 'Serveur initialisé', 'success');
        await loadVpnServerConfig();
        await loadVpnStatus();
        await loadVpnPeers();
        startVpnPolling();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur d\'initialisation', 'danger');
    }
}

async function handleSaveServerConfig() {
    const endpoint = document.getElementById('vpnSettingsEndpoint')?.value?.trim();
    const port = parseInt(document.getElementById('vpnSettingsPort')?.value || '51820');

    if (!endpoint) {
        showToast('VPN', 'Endpoint requis', 'warning');
        return;
    }

    try {
        await api.request('/vpn/server-config', {
            method: 'PUT',
            body: JSON.stringify({ endpoint, listen_port: port })
        });
        showToast('VPN', 'Réglages enregistrés', 'success');
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

// ── Toggle ──────────────────────────────────────────────

async function handleVpnToggle() {
    const toggle = document.getElementById('toggleVpn');
    const checked = toggle ? toggle.checked : false;

    try {
        await api.request('/vpn/toggle', {
            method: 'PUT',
            body: JSON.stringify({ enabled: checked })
        });
        showToast('VPN', checked ? 'VPN activé' : 'VPN désactivé', 'success');
        if (checked) startVpnPolling(); else stopVpnPolling();
        await loadVpnStatus();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
        if (toggle) toggle.checked = !checked;
    }
}

// ── Peers ───────────────────────────────────────────────

async function loadVpnPeers() {
    if (!api.token) return;
    try {
        const data = await api.request('/vpn/peers');
        renderPeers(data.peers || []);
    } catch (e) {
        console.error('loadVpnPeers:', e);
    }
}

function renderPeers(peers) {
    const container = document.getElementById('vpnPeersList');
    if (!container) return;

    if (peers.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun peer. Ajoutez-en un pour commencer.</p>';
        return;
    }

    container.innerHTML = peers.map(p => `
        <div class="vpn-peer-card ${p.enabled ? '' : 'disabled'}">
            <div class="vpn-peer-avatar">${(p.username || '?')[0].toUpperCase()}</div>
            <div class="vpn-peer-info">
                <div class="vpn-peer-name">${escapeHtml(p.username)} — ${escapeHtml(p.name)}</div>
                <div class="vpn-peer-meta">
                    ${escapeHtml(p.vpn_ip)}
                    ${p.last_handshake ? ' · ' + escapeHtml(p.last_handshake) : ' · jamais connecté'}
                </div>
            </div>
            <div class="vpn-peer-tx">
                ${p.transfer_rx ? '↓' + escapeHtml(p.transfer_rx) : ''}
                ${p.transfer_tx ? ' ↑' + escapeHtml(p.transfer_tx) : ''}
            </div>
            <div class="vpn-peer-status ${p.connected ? 'connected' : ''}"
                 title="${p.connected ? 'Connecté' : 'Hors ligne'}"></div>
            <div class="vpn-peer-actions">
                <button class="btn btn-outline-secondary btn-sm" onclick="downloadPeerConfig(${p.id})" title="Télécharger .conf">
                    <i class="bi bi-download"></i>
                </button>
                <button class="btn btn-outline-secondary btn-sm" onclick="showPeerQrCode(${p.id}, '${escapeHtml(p.username)}', '${escapeHtml(p.name)}')" title="QR code">
                    <i class="bi bi-qr-code"></i>
                </button>
                <button class="btn btn-outline-secondary btn-sm" onclick="handleTogglePeer(${p.id})" title="${p.enabled ? 'Désactiver' : 'Activer'}">
                    <i class="bi bi-${p.enabled ? 'toggle-on' : 'toggle-off'}"></i>
                </button>
                <button class="btn btn-outline-danger btn-sm" onclick="handleDeletePeer(${p.id}, '${escapeHtml(p.username)}', '${escapeHtml(p.name)}')" title="Supprimer">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
    `).join('');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

async function loadVpnUsers() {
    try {
        const data = await api.request('/users?per_page=100');
        const select = document.getElementById('vpnPeerUser');
        if (!select) return;
        const users = data.users || data.items || data || [];
        select.innerHTML = users.map(u =>
            `<option value="${u.id}">${escapeHtml(u.username)}</option>`
        ).join('');
    } catch (e) {
        console.error('loadVpnUsers:', e);
    }
}

async function handleCreatePeer() {
    const userId = parseInt(document.getElementById('vpnPeerUser')?.value);
    const name = document.getElementById('vpnPeerName')?.value?.trim();

    if (!userId || !name) {
        showToast('VPN', 'Utilisateur et nom requis', 'warning');
        return;
    }

    try {
        const result = await api.request('/vpn/peers', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId, name })
        });
        showToast('VPN', `Peer créé (${result.vpn_ip})`, 'success');

        // Reset form + close modal
        const nameInput = document.getElementById('vpnPeerName');
        if (nameInput) nameInput.value = '';
        const modalEl = document.getElementById('vpnAddPeerModal');
        if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();

        await loadVpnPeers();
        await loadVpnStatus();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

async function handleDeletePeer(peerId, username, name) {
    if (!confirm(`Supprimer le peer "${username} — ${name}" ?`)) return;
    try {
        await api.request(`/vpn/peers/${peerId}`, { method: 'DELETE' });
        showToast('VPN', 'Peer supprimé', 'success');
        await loadVpnPeers();
        await loadVpnStatus();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

async function handleTogglePeer(peerId) {
    try {
        const result = await api.request(`/vpn/peers/${peerId}/toggle`, { method: 'PUT' });
        showToast('VPN', result.enabled ? 'Peer activé' : 'Peer désactivé', 'success');
        await loadVpnPeers();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

async function downloadPeerConfig(peerId) {
    try {
        const resp = await fetch(`/api/vpn/peers/${peerId}/config`, {
            headers: { 'Authorization': `Bearer ${api.token}` }
        });
        if (!resp.ok) throw new Error('Erreur de téléchargement');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'wireguard.conf';
        a.click();
        URL.revokeObjectURL(url);
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

async function showPeerQrCode(peerId, username, name) {
    try {
        const resp = await fetch(`/api/vpn/peers/${peerId}/qrcode`, {
            headers: { 'Authorization': `Bearer ${api.token}` }
        });
        if (!resp.ok) throw new Error('Erreur QR code');
        const svgText = await resp.text();

        const titleEl = document.getElementById('vpnQrTitle');
        const imgEl = document.getElementById('vpnQrImage');
        if (titleEl) titleEl.textContent = `QR Code — ${username} / ${name}`;
        if (imgEl) imgEl.innerHTML = svgText;

        const modalEl = document.getElementById('vpnQrModal');
        if (modalEl) new bootstrap.Modal(modalEl).show();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
    }
}

// ── Polling ─────────────────────────────────────────────

function startVpnPolling() {
    stopVpnPolling();
    vpnPollTimer = setInterval(async () => {
        await loadVpnStatus();
        await loadVpnPeers();
    }, VPN_POLL_INTERVAL);
}

function stopVpnPolling() {
    if (vpnPollTimer) {
        clearInterval(vpnPollTimer);
        vpnPollTimer = null;
    }
}

// ── Init ────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    const vpnTab = document.getElementById('vpn-tab');
    if (vpnTab) {
        vpnTab.addEventListener('shown.bs.tab', async () => {
            await loadVpnServerConfig();
            await loadVpnStatus();
            await loadVpnPeers();
            await loadVpnUsers();
        });
    }

    // Pré-charger les users quand on ouvre le modal
    const addPeerModal = document.getElementById('vpnAddPeerModal');
    if (addPeerModal) {
        addPeerModal.addEventListener('show.bs.modal', () => loadVpnUsers());
    }
});
