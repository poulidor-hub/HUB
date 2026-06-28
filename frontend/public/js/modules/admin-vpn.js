/**
 * Admin VPN WireGuard Module
 *
 * Gère l'onglet VPN dans l'interface d'administration :
 * - Toggle ON/OFF
 * - Formulaire de configuration
 * - Déploiement
 * - Polling du statut
 * - Génération de clés
 */

const VPN_POLL_INTERVAL = 10000;
let vpnPollTimer = null;

async function loadVpnState() {
    if (!api.token) return;
    try {
        const data = await api.request('/vpn/status');
        const toggle = document.getElementById('toggleVpn');
        const form = document.getElementById('vpnConfigForm');
        const badge = document.getElementById('vpnStatusBadge');
        const stats = document.getElementById('vpnStats');

        if (toggle) toggle.checked = data.enabled === true;

        if (form) form.classList.toggle('d-none', !data.enabled);

        if (badge) {
            badge.classList.remove('d-none', 'bg-success', 'bg-secondary', 'bg-warning');
            if (!data.enabled) {
                badge.classList.add('d-none');
            } else if (data.connected) {
                badge.classList.add('bg-success');
                badge.textContent = 'Connecté';
            } else {
                badge.classList.add('bg-secondary');
                badge.textContent = 'Déconnecté';
            }
        }

        if (stats) stats.classList.toggle('d-none', !data.connected);

        if (data.connected) {
            const ipEl = document.getElementById('vpnStatsIp');
            const epEl = document.getElementById('vpnStatsEndpoint');
            const rxEl = document.getElementById('vpnStatsRx');
            const txEl = document.getElementById('vpnStatsTx');
            if (ipEl) ipEl.textContent = data.vpn_ip || '-';
            if (epEl) epEl.textContent = data.endpoint || '-';
            if (rxEl) rxEl.textContent = data.transfer_rx || '-';
            if (txEl) txEl.textContent = data.transfer_tx || '-';
        }

        if (data.enabled) {
            await loadVpnConfig();
        }
    } catch (e) {
        console.error('loadVpnState:', e);
    }
}

async function loadVpnConfig() {
    try {
        const data = await api.request('/vpn/config');
        if (!data.configured) return;

        const addressEl = document.getElementById('vpnAddress');
        const dnsEl = document.getElementById('vpnDns');
        const pkEl = document.getElementById('vpnPublicKey');
        const epEl = document.getElementById('vpnEndpoint');
        const aipEl = document.getElementById('vpnAllowedIPs');

        if (addressEl && data.address) addressEl.value = data.address;
        if (dnsEl && data.dns) dnsEl.value = data.dns;
        if (pkEl && data.public_key) pkEl.value = data.public_key;
        if (epEl && data.endpoint) epEl.value = data.endpoint;
        if (aipEl && data.allowed_ips) aipEl.value = data.allowed_ips;
    } catch (e) {
        console.error('loadVpnConfig:', e);
    }
}

async function handleVpnToggle() {
    const toggle = document.getElementById('toggleVpn');
    const form = document.getElementById('vpnConfigForm');
    const checked = toggle ? toggle.checked : false;

    try {
        const data = await api.request('/vpn/toggle', {
            method: 'PUT',
            body: JSON.stringify({ enabled: checked })
        });

        if (form) form.classList.toggle('d-none', !checked);

        showToast(
            'VPN',
            checked ? 'VPN activé' : 'VPN désactivé',
            'success'
        );

        if (checked) {
            startVpnPolling();
        } else {
            stopVpnPolling();
        }

        await loadVpnState();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur', 'danger');
        if (toggle) toggle.checked = !checked;
    }
}

async function handleVpnKeyGen() {
    const genBtn = document.querySelector('[data-action="handleVpnKeyGen"]');
    if (genBtn) genBtn.disabled = true;

    try {
        const data = await api.request('/vpn/generate-key', {
            method: 'POST'
        });

        const pkInput = document.getElementById('vpnPrivateKey');
        if (pkInput) pkInput.value = data.private_key;

        showToast('VPN', 'Clés WireGuard générées', 'success');
    } catch (e) {
        showToast('VPN', e.message || 'Erreur de génération', 'danger');
    } finally {
        if (genBtn) genBtn.disabled = false;
    }
}

async function handleVpnDeploy() {
    const privateKey = document.getElementById('vpnPrivateKey')?.value?.trim();
    const address = document.getElementById('vpnAddress')?.value?.trim();
    const dns = document.getElementById('vpnDns')?.value?.trim() || null;
    const publicKey = document.getElementById('vpnPublicKey')?.value?.trim();
    const endpoint = document.getElementById('vpnEndpoint')?.value?.trim();
    const allowedIps = document.getElementById('vpnAllowedIPs')?.value?.trim() || '0.0.0.0/0';
    const presharedKey = document.getElementById('vpnPresharedKey')?.value?.trim() || null;

    if (!privateKey || !address || !publicKey || !endpoint) {
        showToast('VPN', 'Les champs Clé privée, Adresse, Clé publique et Endpoint sont requis', 'warning');
        return;
    }

    const deployBtn = document.querySelector('[data-action="handleVpnDeploy"]');
    if (deployBtn) deployBtn.disabled = true;

    try {
        await api.request('/vpn/deploy', {
            method: 'POST',
            body: JSON.stringify({
                enabled: true,
                private_key: privateKey,
                address: address,
                dns: dns,
                public_key: publicKey,
                endpoint: endpoint,
                allowed_ips: allowedIps,
                preshared_key: presharedKey
            })
        });

        showToast('VPN', 'Configuration déployée avec succès', 'success');
        await loadVpnState();
        startVpnPolling();
    } catch (e) {
        showToast('VPN', e.message || 'Erreur de déploiement', 'danger');
    } finally {
        if (deployBtn) deployBtn.disabled = false;
    }
}

function startVpnPolling() {
    stopVpnPolling();
    vpnPollTimer = setInterval(loadVpnState, VPN_POLL_INTERVAL);
}

function stopVpnPolling() {
    if (vpnPollTimer) {
        clearInterval(vpnPollTimer);
        vpnPollTimer = null;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Ne pas charger l'état VPN au démarrage : /vpn/status est protégé et un appel
    // non authentifié (page de login) déclenche un faux "session expirée".
    // Le chargement est géré à l'affichage de l'onglet VPN (shown.bs.tab).
    const vpnTab = document.getElementById('vpn-tab');
    if (vpnTab) {
        vpnTab.addEventListener('shown.bs.tab', () => {
            loadVpnState().catch(() => {});
        });
    }
});
