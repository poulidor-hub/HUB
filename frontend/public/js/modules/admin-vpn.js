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

// ── État des filtres (refonte grille) ───────────────────
let vpnFilterQuery = '';
let vpnFilterStatus = 'all';
let vpnCurrentPage = 1;
const VPN_USERS_PER_PAGE = 30;
let vpnLastGrouped = [];
let vpnLastPeersJson = '';

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
        const peers = data.peers || [];
        const sig = JSON.stringify(peers.map(p => ({
            id: p.id, connected: p.connected, enabled: p.enabled,
            rx: p.transfer_rx, tx: p.transfer_tx, hs: p.last_handshake
        })));
        if (sig === vpnLastPeersJson) return;
        vpnLastPeersJson = sig;
        renderVpnKpi(peers);
        renderPeersGrouped(peers);
    } catch (e) {
        console.error('loadVpnPeers:', e);
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

/**
 * Échappe une valeur pour usage dans un attribut HTML onclick.
 * JSON.stringify() produit des guillemets doubles qui cassent l'attribut.
 */
function attrStr(x) {
    return JSON.stringify(x).replace(/"/g, '&quot;');
}

// ── Utilitaires refonte grille ─────────────────────────

/**
 * Regroupe les peers par utilisateur.
 * @param {Array} peers - Liste brute des peers depuis l'API
 * @returns {Array} Utilisateurs groupés, triés (connectés d'abord, puis alpha)
 */
function groupPeersByUser(peers) {
    const grouped = {};
    for (const p of peers) {
        const uid = p.user_id;
        if (!grouped[uid]) {
            grouped[uid] = {
                user_id: uid,
                username: p.username || 'Inconnu',
                devices: [],
                totalDevices: 0,
                connectedDevices: 0,
                lastActivity: null
            };
        }
        grouped[uid].devices.push(p);
        grouped[uid].totalDevices++;
        if (p.connected) {
            grouped[uid].connectedDevices++;
            // Priorité au handshake d'un device connecté
            grouped[uid].lastActivity = p.last_handshake || grouped[uid].lastActivity;
        } else if (!grouped[uid].lastActivity && p.last_handshake) {
            grouped[uid].lastActivity = p.last_handshake;
        }
    }
    // Tri : utilisateurs avec ≥1 connecté d'abord, puis par username
    return Object.values(grouped).sort((a, b) => {
        const aOn = a.connectedDevices > 0 ? 1 : 0;
        const bOn = b.connectedDevices > 0 ? 1 : 0;
        if (aOn !== bOn) return bOn - aOn;
        return a.username.localeCompare(b.username);
    });
}

function filterVpnUsers(grouped) {
    const query = vpnFilterQuery.toLowerCase().trim();
    const status = vpnFilterStatus;

    return grouped.filter(user => {
        if (query) {
            const matchUser = user.username.toLowerCase().includes(query);
            const matchDevice = user.devices.some(d =>
                (d.name || '').toLowerCase().includes(query)
            );
            if (!matchUser && !matchDevice) return false;
        }
        if (status === 'connected' && user.connectedDevices === 0) return false;
        if (status === 'disconnected' && user.connectedDevices > 0) return false;
        return true;
    });
}

function initVpnFilters() {
    const search = document.getElementById('vpnFilterSearch');
    if (search && !search.dataset.bound) {
        search.addEventListener('input', () => {
            vpnFilterQuery = search.value;
            vpnCurrentPage = 1;
            if (typeof renderPeersGrouped === 'function') {
                renderPeersGrouped(vpnLastGrouped, true);
            }
        });
        search.dataset.bound = '1';
    }

    document.querySelectorAll('.vpn-filter-chip').forEach(chip => {
        if (chip.dataset.bound) return;
        chip.addEventListener('click', () => {
            document.querySelectorAll('.vpn-filter-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            vpnFilterStatus = chip.dataset.filter || 'all';
            vpnCurrentPage = 1;
            if (typeof renderPeersGrouped === 'function') {
                renderPeersGrouped(vpnLastGrouped, true);
            }
        });
        chip.dataset.bound = '1';
    });

    const prevBtn = document.getElementById('vpnPagePrev');
    const nextBtn = document.getElementById('vpnPageNext');
    if (prevBtn && !prevBtn.dataset.bound) {
        prevBtn.addEventListener('click', () => {
            if (vpnCurrentPage > 1) { vpnCurrentPage--; renderPeersGrouped([], true); }
        });
        prevBtn.dataset.bound = '1';
    }
    if (nextBtn && !nextBtn.dataset.bound) {
        nextBtn.addEventListener('click', () => {
            vpnCurrentPage++; renderPeersGrouped([], true);
        });
        nextBtn.dataset.bound = '1';
    }
}

function renderPeersGrouped(peers, fromFilter = false) {
    const container = document.getElementById('vpnUsersGrid');
    if (!container) return;

    if (!fromFilter && peers.length >= 0) {
        vpnLastGrouped = groupPeersByUser(peers);
    }
    const grouped = vpnLastGrouped;

    const filtered = filterVpnUsers(grouped);

    if (filtered.length === 0) {
        container.innerHTML = '<p class="text-muted text-center py-4">Aucun peer. Ajoutez-en un pour commencer.</p>';
        const pag = document.getElementById('vpnPagination');
        if (pag) pag.style.display = 'none';
        return;
    }

    const totalPages = Math.ceil(filtered.length / VPN_USERS_PER_PAGE);
    if (vpnCurrentPage > totalPages) vpnCurrentPage = 1;
    const start = (vpnCurrentPage - 1) * VPN_USERS_PER_PAGE;
    const pageUsers = filtered.slice(start, start + VPN_USERS_PER_PAGE);

    container.innerHTML = pageUsers.map(user => renderUserCard(user)).join('');
    renderPagination(filtered.length, totalPages);
}

function renderUserCard(user) {
    const allOn = user.connectedDevices === user.totalDevices;
    const allOff = user.connectedDevices === 0;
    const badgeClass = allOn ? 'all-on' : (allOff ? 'all-off' : 'partial');
    const badgeText = `${user.connectedDevices}/${user.totalDevices} conn.`;
    const cardClass = allOff ? 'vpn-user-card all-off' : 'vpn-user-card';
    const initial = (user.username || '?')[0].toUpperCase();
    const lastSeen = user.lastActivity ? `Actif : ${escapeHtml(user.lastActivity)}` : 'Jamais connecté';

    const devicesHtml = user.devices.map(d => renderDeviceRow(d)).join('');

    return `
        <div class="${cardClass}">
            <div class="vpn-user-header">
                <div class="vpn-user-avatar">${initial}</div>
                <div class="vpn-user-meta">
                    <span class="vpn-user-name">${escapeHtml(user.username)}</span>
                    <span class="vpn-user-last-seen">${lastSeen}</span>
                </div>
                <span class="vpn-user-badge ${badgeClass}">${badgeText}</span>
            </div>
            <div class="vpn-device-list">${devicesHtml}</div>
            <div class="vpn-user-footer">
                <button class="vpn-add-device"
                        data-action="openAddPeerForUser" data-arg1="${user.user_id}" data-arg2="${attrStr(user.username)}">
                    <i class="bi bi-plus-lg"></i> Ajouter un device
                </button>
            </div>
        </div>
    `;
}

function renderDeviceRow(d) {
    const icon = getDeviceIcon(d.name);
    const rowClass = d.connected ? 'vpn-device-row' : 'vpn-device-row off';
    const statusClass = d.connected ? 'on' : '';
    const handshake = d.last_handshake ? escapeHtml(d.last_handshake) : 'jamais';
    const toggleIcon = d.enabled ? 'bi-toggle-on' : 'bi-toggle-off';
    const toggleTitle = d.enabled ? 'Désactiver' : 'Activer';

    // Méta : trafic si en ligne, sinon dernier handshake
    let metaHtml = '';
    if (d.connected) {
        const rx = d.transfer_rx ? escapeHtml(d.transfer_rx) : '';
        const tx = d.transfer_tx ? escapeHtml(d.transfer_tx) : '';
        metaHtml = (rx || tx) ? `<span class="rx">${rx ? '↓' + rx : ''}</span><span class="tx">${tx ? '↑' + tx : ''}</span>` : `<span class="hs">${handshake}</span>`;
    } else {
        metaHtml = `<span class="hs">${handshake}</span>`;
    }

    return `
        <div class="${rowClass}">
            <span class="vpn-device-icon"><i class="bi ${icon}"></i></span>
            <div class="vpn-device-info">
                <span class="vpn-device-name" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</span>
                <span class="vpn-device-ip">${escapeHtml(d.vpn_ip)}</span>
            </div>
            <div class="vpn-device-meta">${metaHtml}</div>
            <span class="vpn-device-status ${statusClass}"
                  title="${d.connected ? 'Connecté' : 'Hors ligne'}"></span>
            <div class="vpn-device-actions">
                <button class="vpn-device-btn" data-action="downloadPeerConfig" data-arg1="${d.id}" title="Télécharger .conf">
                    <i class="bi bi-download"></i>
                </button>
                <button class="vpn-device-btn"
                        data-action="showPeerQrCode" data-arg1="${d.id}" data-arg2="${attrStr(d.username)}" data-arg3="${attrStr(d.name)}" title="QR code">
                    <i class="bi bi-qr-code"></i>
                </button>
                <button class="vpn-device-btn toggle-on" data-action="handleTogglePeer" data-arg1="${d.id}" title="${toggleTitle}">
                    <i class="bi ${toggleIcon}"></i>
                </button>
                <button class="vpn-device-btn delete"
                        data-action="handleDeletePeer" data-arg1="${d.id}" data-arg2="${attrStr(d.username)}" data-arg3="${attrStr(d.name)}" title="Supprimer">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
    `;
}

function renderPagination(totalUsers, totalPages) {
    const pag = document.getElementById('vpnPagination');
    if (!pag) return;
    if (totalPages <= 1) {
        pag.style.display = 'none';
        return;
    }
    pag.style.display = 'flex';
    const prev = document.getElementById('vpnPagePrev');
    const next = document.getElementById('vpnPageNext');
    const info = document.getElementById('vpnPageInfo');
    if (prev) prev.disabled = vpnCurrentPage === 1;
    if (next) next.disabled = vpnCurrentPage === totalPages;
    if (info) info.textContent = `Page ${vpnCurrentPage} sur ${totalPages} · ${totalUsers} utilisateurs`;
}

function openAddPeerForUser(userId, username) {
    const modalEl = document.getElementById('vpnAddPeerModal');
    if (!modalEl) return;
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
    setTimeout(() => {
        const select = document.getElementById('vpnPeerUser');
        if (select) select.value = userId;
    }, 200);
}

/**
 * Détermine l'icône Bootstrap Icon selon le nom du device.
 * @param {string} name - Nom du device (ex: "iPhone 15", "Laptop Pro")
 * @returns {string} Classe Bootstrap Icon (ex: "bi-phone")
 */
function getDeviceIcon(name) {
    const n = (name || '').toLowerCase();
    if (/phone|mobile|iphone|android|samsung|pixel|smartphone|téléphone|galaxy/.test(n)) return 'bi-phone';
    if (/desktop|pc[\s-]|tour|bureau|workstation|serveur|server|imac|mac mini/.test(n)) return 'bi-pc-display';
    return 'bi-laptop';
}

/**
 * Parse une string de trafic formatée ("1.50 GiB", "800 MiB") en bytes.
 * @param {string} str - String depuis l'API WireGuard
 * @returns {number} Nombre de bytes
 */
function parseTrafficToBytes(str) {
    if (!str) return 0;
    const match = String(str).trim().match(/^([\d.]+)\s*(B|KiB|MiB|GiB|TiB)$/);
    if (!match) return 0;
    const value = parseFloat(match[1]);
    const mult = { B: 1, KiB: 1024, MiB: 1024 ** 2, GiB: 1024 ** 3, TiB: 1024 ** 4 };
    return value * (mult[match[2]] || 1);
}

/**
 * Formate un nombre de bytes en string lisible.
 * @param {number} bytes
 * @returns {string} ex: "1.50 GiB", "800 MiB"
 */
function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let i = 0;
    let val = bytes;
    while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
    return val.toFixed(val >= 100 ? 0 : (val >= 10 ? 1 : 2)) + ' ' + units[i];
}

/**
 * Met à jour les 4 cartes KPI du dashboard VPN.
 * @param {Array} peers - Liste brute des peers depuis l'API
 */
function renderVpnKpi(peers) {
    const users = new Set(peers.map(p => p.user_id));
    const connected = peers.filter(p => p.connected).length;

    let totalRx = 0, totalTx = 0;
    for (const p of peers) {
        totalRx += parseTrafficToBytes(p.transfer_rx);
        totalTx += parseTrafficToBytes(p.transfer_tx);
    }

    const elUsers = document.getElementById('vpnKpiUsers');
    const elPeers = document.getElementById('vpnKpiPeers');
    const elConn = document.getElementById('vpnKpiConnected');
    const elConnSub = document.getElementById('vpnKpiConnectedSub');
    const elTraffic = document.getElementById('vpnKpiTraffic');

    if (elUsers) elUsers.textContent = users.size;
    if (elPeers) elPeers.textContent = peers.length;
    if (elConn) elConn.textContent = connected;
    if (elConnSub && peers.length > 0) {
        elConnSub.textContent = `connectés · ${Math.round(connected / peers.length * 100)}%`;
    }
    if (elTraffic) {
        elTraffic.textContent = `↓${formatBytes(totalRx)} ↑${formatBytes(totalTx)}`;
    }
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
            initVpnFilters();
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
