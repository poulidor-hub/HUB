/**
 * Hub Application — Vue admin « Accès & routage » (chantier B)
 *
 * Maquette B-v2 validée :
 * - Bandeau d'état : zone (éditable, confirmation renforcée), résolveur,
 *   wildcard PKI, IP serveur (éditable), dernière erreur de publication.
 * - Onglet Applications : liste compacte + panneau de détail (URL canonique,
 *   nom de routage éditable, statuts, fallback ports lecture seule, actions).
 * - Onglet Services tiers : liste + ajout/édition/suppression.
 * - Onglet Ports : lecture seule + libération d'orphelins (transition).
 * - Publication AUTOMATIQUE : aucun bouton « propager » — toute modification
 *   est publiée immédiatement dans la zone. Polling léger (5 s, onglet actif).
 *
 * Charte : pas d'animations (perf/stabilité) ; tokens HUBCSS/Bootstrap existants.
 */

let routingState = null;
let routingSelectedSlug = null;
let routingSubTab = 'apps';
let routingPollTimer = null;

const ROUTING_POLL_MS = 5000;

// ===== Chargement à l'activation de l'onglet + polling =====

document.addEventListener('DOMContentLoaded', () => {
    const tab = document.getElementById('routing-tab');
    if (tab) {
        tab.addEventListener('shown.bs.tab', () => {
            loadRoutingAdmin();
            startRoutingPolling();
        });
        tab.addEventListener('hidden.bs.tab', stopRoutingPolling);
    }
});

function startRoutingPolling() {
    if (routingPollTimer) return;
    routingPollTimer = setInterval(() => loadRoutingState(true), ROUTING_POLL_MS);
}

function stopRoutingPolling() {
    if (routingPollTimer) { clearInterval(routingPollTimer); routingPollTimer = null; }
}

// ===== Chargement =====

async function loadRoutingAdmin() {
    await loadRoutingState();
}

async function loadRoutingState(silent = false) {
    try {
        routingState = await api.getRoutingState();
        renderRoutingAdmin();
    } catch (error) {
        if (!silent) {
            const container = document.getElementById('routingContent');
            if (container) {
                container.innerHTML =
                    `<div class="alert alert-danger m-3">Chargement du routage impossible : ${escapeHtmlRouting(error.message)}</div>`;
            }
        }
    }
}

function escapeHtmlRouting(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

// ===== Rendu principal =====

function renderRoutingAdmin() {
    const container = document.getElementById('routingContent');
    if (!container || !routingState) return;

    const s = routingState;
    if (!s.configured) {
        container.innerHTML = renderRoutingNotConfigured();
        return;
    }

    container.innerHTML = `
        ${renderRoutingBanner()}
        <ul class="nav nav-pills mb-3" id="routingSubTabs">
            <li class="nav-item"><button class="nav-link ${routingSubTab === 'apps' ? 'active' : ''}" data-action="routingSubTab" data-subtab="apps">Applications</button></li>
            <li class="nav-item"><button class="nav-link ${routingSubTab === 'services' ? 'active' : ''}" data-action="routingSubTab" data-subtab="services">Services tiers (${s.services.length})</button></li>
            <li class="nav-item"><button class="nav-link ${routingSubTab === 'ports' ? 'active' : ''}" data-action="routingSubTab" data-subtab="ports">Ports — lecture seule</button></li>
        </ul>
        <div id="routingSubContent"></div>
    `;

    const sub = document.getElementById('routingSubContent');
    if (routingSubTab === 'services') {
        sub.innerHTML = renderRoutingServices();
    } else if (routingSubTab === 'ports') {
        sub.innerHTML = `
            <div class="alert alert-warning py-2 small">
                <i class="bi bi-hourglass-split me-1"></i>Fallback pendant la transition —
                les noms <code>&lt;slug&gt;.hub.&lt;zone&gt;</code> remplacent ces ports quand le LAN passe au résolveur du hub.
                Aucun port n'est éditable : l'allocation est automatique au déploiement.
            </div>
            <div id="routingPortsReadOnly"></div>
        `;
        loadRoutingPortsReadOnly();
    } else {
        sub.innerHTML = renderRoutingApps();
    }
}

function renderRoutingNotConfigured() {
    return `
        <div class="card">
            <div class="card-body">
                <h5><i class="bi bi-diagram-3 me-2"></i>Routage par noms — non configuré</h5>
                <p class="text-muted mb-3">
                    Choisissez la zone interne du hub : les applications deviennent
                    joignables en <code>https://&lt;slug&gt;.&lt;zone&gt;</code> via le VPN (le résolveur est
                    poussé dans les configs WireGuard). Les accès par IP:ports restent valides
                    pendant toute la transition.
                </p>
                <div class="row g-2 align-items-center" style="max-width:560px">
                    <div class="col-auto flex-grow-1">
                        <input class="form-control" id="routingZoneInput"
                               placeholder="ex. hub.example.lan" value="">
                    </div>
                    <div class="col-auto">
                        <button class="btn btn-primary" data-action="routingConfigureZone">Configurer</button>
                    </div>
                </div>
                ${routingState && routingState.last_error ? `<div class="alert alert-danger mt-3 mb-0 small">${escapeHtmlRouting(routingState.last_error)}</div>` : ''}
            </div>
        </div>
    `;
}

function renderRoutingBanner() {
    const s = routingState;
    const err = s.last_error
        ? `<div class="alert alert-danger py-2 mb-2 small"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtmlRouting(s.last_error)}</div>`
        : '';
    const wildcard = s.wildcard
        ? `<span class="badge bg-success">🔒 wildcard valide — ${escapeHtmlRouting(String(s.wildcard.expires_at).slice(0, 10))}</span>`
        : `<span class="badge bg-secondary">🔒 wildcard absent</span>`;
    const resolver = s.resolver.active
        ? `<span class="badge bg-success">● Résolveur actif · ${s.resolver.count} noms</span>`
        : `<span class="badge bg-warning text-dark">● Résolveur arrêté</span>`;
    return `
        ${err}
        <div class="d-flex flex-wrap gap-2 align-items-center mb-3 p-2 rounded border">
            <span class="badge bg-primary fs-6">hub : ${escapeHtmlRouting(s.zone)}
                <button class="btn btn-link btn-sm text-white p-0 ms-1" data-action="routingEditZone" title="Changer de zone">✏️</button>
            </span>
            ${resolver}
            ${wildcard}
            <span class="ms-auto text-muted small">
                IP serveur : <code>${escapeHtmlRouting(getSettingsRoutingIp())}</code>
                <button class="btn btn-link btn-sm p-0" data-action="routingEditIp" title="Modifier l'IP du hub">✏️</button>
                · publication automatique
            </span>
        </div>
    `;
}

function getSettingsRoutingIp() {
    // L'IP effective est servie par /api/config/network (état) — mémo locale simple.
    return routingState._hubHost || routingState.hub_host || 'auto';
}

function renderRoutingApps() {
    const s = routingState;
    const apps = [...s.apps].sort((a, b) => (a.is_workshop - b.is_workshop) || a.slug.localeCompare(b.slug));
    const selected = apps.find(a => a.slug === routingSelectedSlug) || apps[0] || null;
    return `
        <div class="row g-3">
            <div class="col-md-5">
                <div class="list-group routing-app-list">
                    ${apps.map(a => `
                        <button class="list-group-item list-group-item-action ${selected && selected.slug === a.slug ? 'active' : ''}"
                                data-action="routingSelectApp" data-slug="${escapeHtmlRouting(a.slug)}">
                            <span>${escapeHtmlRouting(a.name)}</span>
                            ${a.is_workshop ? '<span class="badge bg-warning text-dark ms-1">atelier</span>' : ''}
                            ${a.is_remote ? '<span class="badge bg-info text-dark ms-1">distante</span>' : ''}
                            <span class="float-end">${a.published ? '<span class="text-success">●</span>' : '<span class="text-muted">○</span>'}</span>
                            <br><code class="small ${selected && selected.slug === a.slug ? '' : 'text-muted'}">${escapeHtmlRouting(a.dns_name || a.slug)}</code>
                        </button>
                    `).join('')}
                </div>
            </div>
            <div class="col-md-7">
                ${selected ? renderRoutingAppDetail(selected) : '<div class="text-muted p-3">Sélectionnez une application.</div>'}
            </div>
        </div>
    `;
}

function renderRoutingAppDetail(app) {
    const zone = routingState.zone;
    const fqdn = app.dns_name ? `${app.dns_name}.${zone}` : '';
    const fb = app.fallback || {};
    const fbLabel = (fb.api || fb.front)
        ? `API <code>${fb.api ?? '—'}</code> · Front <code>${fb.front ?? '—'}/${fb.https ?? '—'}</code>`
        : '<span class="text-muted">aucune allocation</span>';
    const stateIcon = app.is_active ? '<span class="text-success">▶ conteneur actif</span>' : '<span class="text-muted">⏸ conteneur arrêté</span>';
    return `
        <div class="card">
            <div class="card-body">
                <div class="d-flex align-items-center gap-2 mb-3">
                    <b>${escapeHtmlRouting(app.name)}</b>
                    ${app.is_workshop ? '<span class="badge bg-warning text-dark">atelier</span>' : ''}
                    ${app.is_remote ? '<span class="badge bg-info text-dark">distante</span>' : ''}
                    ${stateIcon}
                </div>
                <div class="d-flex align-items-center gap-2 border rounded p-2 mb-3">
                    ${fqdn
                        ? `<code class="text-primary fw-bold">https://${escapeHtmlRouting(fqdn)}</code>
                           <span class="ms-auto">
                             <button class="btn btn-sm btn-outline-secondary" data-action="routingCopyUrl" data-url="https://${escapeHtmlRouting(fqdn)}">📋 copier</button>
                             <button class="btn btn-sm btn-primary" data-action="routingOpenUrl" data-url="https://${escapeHtmlRouting(fqdn)}">ouvrir ↗</button>
                           </span>`
                        : '<span class="text-muted">aucun nom de routage</span>'}
                </div>
                <div class="mb-2">
                    <span class="text-muted small">Nom de routage (publié à l'enregistrement)</span><br>
                    <code class="border rounded px-2 py-1">${escapeHtmlRouting(app.dns_name || app.slug)}</code><span class="text-muted">.${escapeHtmlRouting(zone)}</span>
                    <button class="btn btn-link btn-sm p-0" data-action="routingRename" data-slug="${escapeHtmlRouting(app.slug)}" data-current="${escapeHtmlRouting(app.dns_name || app.slug)}">✏️ renommer</button>
                </div>
                <div class="small mb-2">
                    Statuts : <span class="${app.published ? 'text-success' : 'text-muted'}">${app.published ? '● DNS publié' : '○ non publié'}</span>
                    · 🔒 cert wildcard partagé
                </div>
                <div class="small text-muted mb-3">
                    Fallback ports (transition) : ${fbLabel}
                </div>
                <button class="btn btn-sm btn-outline-secondary" data-action="routingOpenApp" data-slug="${escapeHtmlRouting(app.slug)}">⚙ Gérer l'app →</button>
            </div>
        </div>
    `;
}

function renderRoutingServices() {
    const s = routingState;
    return `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <span class="text-muted small">Flux externes routés par nom (ex. PhiliaNG) — hors parc, exposés via le hub.</span>
            <button class="btn btn-sm btn-outline-primary" data-action="routingAddService">
                <i class="bi bi-plus-lg me-1"></i>Publier un service tiers
            </button>
        </div>
        <div class="list-group">
            ${s.services.map(sv => `
                <div class="list-group-item d-flex justify-content-between align-items-center">
                    <div>
                        <b>${escapeHtmlRouting(sv.name)}</b>
                        ${sv.is_enabled ? '<span class="text-success ms-1">● routé</span>' : '<span class="text-muted ms-1">○ désactivé</span>'}
                        <br><code class="text-primary small">${escapeHtmlRouting(sv.dns_name)}</code>
                        <span class="text-muted small">→ ${escapeHtmlRouting(sv.target_url)}</span>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-sm btn-outline-secondary" data-action="routingEditService" data-id="${sv.id}">✏️</button>
                        <button class="btn btn-sm btn-outline-danger" data-action="routingDeleteService" data-id="${sv.id}" data-name="${escapeHtmlRouting(sv.name)}">🗑</button>
                    </div>
                </div>
            `).join('') || '<div class="text-muted p-3">Aucun service tiers routé.</div>'}
        </div>
    `;
}

// ===== Actions (handlers globaux — pattern inline des modules admin-*) =====

async function routingConfigureZone() {
    const input = document.getElementById('routingZoneInput');
    const zone = (input?.value || '').trim().toLowerCase();
    if (!zone) { showToast('Zone requise', 'Saisissez la zone interne (ex. hub.example.lan)', 'danger'); return; }
    if (!confirm(`Configurer la zone « ${zone} » ?\n\nUn certificat wildcard sera émis et les applications deviennent joignables en https://<slug>.${zone} (via VPN).`)) return;
    try {
        await api.putRoutingZone(zone);
        showToast('Zone configurée', `Routage par noms actif sur ${zone}`, 'success');
        await loadRoutingState();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function routingEditZone() {
    const current = routingState?.zone || '';
    const next = prompt(`Nouvelle zone interne (tous les noms changent, un nouveau wildcard sera émis) :`, current);
    if (next === null) return;
    const zone = next.trim().toLowerCase();
    if (!zone || zone === current) return;
    if (!confirm(`Changer la zone « ${current} » → « ${zone} » ?\n\nTous les noms d'applications changent : les bookmarks existants devront être mis à jour.`)) return;
    try {
        await api.putRoutingZone(zone);
        showToast('Zone mise à jour', `Routage actif sur ${zone}`, 'success');
        await loadRoutingState();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function routingEditIp() {
    const current = getSettingsRoutingIp();
    const next = prompt(`IP du serveur Hub (utilisée par les enregistrements DNS et les configs WireGuard) :`, current === 'auto' ? '' : current);
    if (next === null) return;
    const ip = next.trim();
    if (!ip) return;
    try {
        await api.setHubHost(ip);
        showToast('IP mise à jour', `hub_host = ${ip} — republished la zone`, 'success');
        await loadRoutingState();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function routingSelectApp(target) {
    routingSelectedSlug = target.dataset.slug;
    renderRoutingAdmin();
}

function routingCopyUrl(button) {
    navigator.clipboard?.writeText(button.dataset.url)
        .then(() => showToast('Copié', button.dataset.url, 'success'))
        .catch(() => {});
}

function routingOpenUrl(button) {
    window.open(button.dataset.url, '_blank', 'noopener');
}

async function routingRename(button) {
    const slug = button.dataset.slug;
    const current = button.dataset.current;
    const next = prompt(`Nouveau nom de routage pour « ${slug} » (les bookmarks utilisateurs pointent sur l'actuel) :`, current);
    if (next === null) return;
    const dnsName = next.trim();
    if (!dnsName || dnsName === current) return;
    if (!confirm(`Renommer « ${current} » → « ${dnsName} » ?\n\nLa publication est immédiate.`)) return;
    try {
        await api.putRoutingDnsName(slug, dnsName);
        showToast('Nom de routage mis à jour', `${slug} → ${dnsName}`, 'success');
        routingSelectedSlug = slug;
        await loadRoutingState();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function routingOpenApp(button) {
    const tab = document.getElementById('apps-tab');
    if (tab) bootstrap.Tab.getOrCreateInstance(tab).show();
}

function routingSubTab(button) {
    routingSubTab = button.dataset.subtab;
    renderRoutingAdmin();
    if (routingSubTab === 'ports') loadRoutingPortsReadOnly();
}

// ===== Services tiers =====

function routingAddService() {
    routingEditServiceModal(null);
}

function routingEditService(button) {
    const id = Number(button.dataset.id);
    const svc = (routingState?.services || []).find(s => s.id === id);
    if (svc) routingEditServiceModal(svc);
}

function routingEditServiceModal(svc) {
    const name = prompt(`Nom du service tiers :`, svc?.name || '');
    if (name === null) return;
    const dnsName = prompt(`FQDN de routage (ex. service.example.org ou compta.${routingState?.zone || 'hub.zone'}) :`, svc?.dns_name || '');
    if (dnsName === null) return;
    const target = prompt(`URL cible (proxy_pass) :`, svc?.target_url || 'https://');
    if (target === null) return;
    const payload = { name: name.trim(), dns_name: dnsName.trim(), target_url: target.trim(), is_enabled: svc ? svc.is_enabled : true };
    const request = svc
        ? api.updateRoutingService(svc.id, payload)
        : api.createRoutingService(payload);
    request
        .then(async () => {
            showToast(svc ? 'Service mis à jour' : 'Service publié', payload.dns_name, 'success');
            await loadRoutingState();
        })
        .catch(error => showToast('Erreur', error.message, 'danger'));
}

async function routingDeleteService(button) {
    if (!confirm(`Supprimer le service « ${button.dataset.name} » ? Le flux ne sera plus routé par le hub.`)) return;
    try {
        await api.deleteRoutingService(Number(button.dataset.id));
        showToast('Service supprimé', button.dataset.name, 'success');
        await loadRoutingState();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== Ports (lecture seule, transition) =====

async function loadRoutingPortsReadOnly() {
    const container = document.getElementById('routingPortsReadOnly');
    if (!container) return;
    try {
        const status = await api.getPortsStatus();
        const allocations = status.allocations || [];
        container.innerHTML = `
            <div class="small text-muted mb-2">
                Plages : API ${escapeHtmlRouting(String(status.api_range_start ?? 9000))}-${escapeHtmlRouting(String(status.api_range_end ?? 9499))}
                · Front ${escapeHtmlRouting(String(status.frontend_range_start ?? 4200))}-${escapeHtmlRouting(String(status.frontend_range_end ?? 4699))}
            </div>
            <table class="table table-sm table-striped">
                <thead><tr><th>App</th><th>Host</th><th>API</th><th>Front</th><th>Front HTTPS</th><th></th></tr></thead>
                <tbody>
                    ${allocations.map(a => `
                        <tr>
                            <td>${escapeHtmlRouting(a.app_slug)}</td>
                            <td><code>${escapeHtmlRouting(a.host)}</code></td>
                            <td>${a.api_port ?? '—'}</td>
                            <td>${a.frontend_port ?? '—'}</td>
                            <td>${a.frontend_https_port ?? '—'}</td>
                            <td><button class="btn btn-sm btn-outline-danger" data-action="routingReleasePorts" data-id="${a.id}" data-slug="${escapeHtmlRouting(a.app_slug)}">libérer</button></td>
                        </tr>
                    `).join('') || '<tr><td colspan="6" class="text-muted">Aucune allocation.</td></tr>'}
                </tbody>
            </table>
        `;
    } catch (error) {
        container.innerHTML = `<div class="alert alert-danger py-2 small">${escapeHtmlRouting(error.message)}</div>`;
    }
}

async function routingReleasePorts(button) {
    if (!confirm(`Libérer les ports de « ${button.dataset.slug} » ?`)) return;
    try {
        await api.deletePortAllocation(Number(button.dataset.id));
        showToast('Ports libérés', button.dataset.slug, 'success');
        await loadRoutingPortsReadOnly();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// Les fonctions restent globales pour les handlers data-action inline
Object.assign(window, {
    routingConfigureZone, routingEditZone, routingEditIp, routingSelectApp,
    routingCopyUrl, routingOpenUrl, routingRename, routingOpenApp,
    routingSubTab, routingAddService, routingEditService, routingDeleteService,
    routingReleasePorts,
});
