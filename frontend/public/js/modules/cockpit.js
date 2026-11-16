// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — cockpit système de la page Administration (passe 2).
 *
 * Remplit #cockpitBody (créé par renderAdminHub, admin-hub.js) avec les
 * métriques serveur de l'hôte : GET /api/admin/system/stats (proxy TOLÉRANT
 * vers l'agent hôte hubctl update --serve, cf. backend system_update.py).
 *
 * Contrat du proxy :
 * - 200 {service: "ok"|"degraded", timestamp, uptime_seconds,
 *   cpu:{percent, cores, load1}, memory:{total_mb, used_mb, available_mb,
 *   percent, swap_percent}, disk:{path, total_gb, used_gb, free_gb, percent}}
 *   (+ errors:{...} si degraded) ;
 * - agent injoignable ⇒ 200 {service: "unreachable", hint}.
 *
 * Polling 10 s démarré par showAdminHub() et stoppé par leaveAdminHubView()
 * (donc à l'ouverture de chaque vue outil) ; le tick ne fetch rien quand le
 * hub n'est pas la vue affichée. Alertes sans fetch dupliqué : le statut
 * « mises à jour » passe par le cache partagé de admin-hub.js, le
 * certificat CA (public, quasi statique) est lu une fois par session de
 * polling. Aucune animation ni transition — redessin SVG sec à chaque tick
 * (charte perf du parc).
 */

// ===== État module =====

const COCKPIT_POLL_MS = 10000;      // polling métriques (maquette : 10 s)
const COCKPIT_CPU_MAX_SAMPLES = 60; // sparkline : 60 échantillons (~10 min)
const COCKPIT_CA_WARN_DAYS = 60;    // CA : orange si expiration ≤ 60 j

let cockpitPollTimer = null;
let cockpitInFlight = false;        // anti-chevauchement des ticks
const cockpitCpuSamples = [];       // buffer circulaire CPU % (60 max)
let cockpitLastStats = null;        // dernière réponse /admin/system/stats
let cockpitStatsError = null;       // dernier échec réseau (message)
let cockpitCaInfo = null;           // /api/pki/ca/info (lue une fois)
let cockpitCaPending = true;        // CA pas encore obtenue (session en cours)
let cockpitUpdatesData = null;      // cache partagé admin-hub.js (update-status)

// ===== Visibilité & cycle de polling =====

/** Vrai seulement si la page admin ET la vue hub sont affichées. */
function cockpitHubVisible() {
    const section = document.getElementById('adminSection');
    const pane = document.getElementById('adminHubPane');
    return Boolean(section && !section.classList.contains('d-none')
        && pane && !pane.classList.contains('d-none'));
}

/**
 * Démarre le polling cockpit (premier chargement immédiat, puis 10 s).
 * Appelé par showAdminHub() — idempotent (relance = reset du timer).
 */
function startCockpitPolling() {
    if (cockpitPollTimer) clearInterval(cockpitPollTimer);
    cockpitCaPending = true; // nouvelle session : relit la CA une seule fois
    loadCockpit();
    cockpitPollTimer = setInterval(loadCockpit, COCKPIT_POLL_MS);
}

/** Stoppe le polling (leaveAdminHubView → ouvertures de vues outil). */
function stopCockpitPolling() {
    if (cockpitPollTimer) {
        clearInterval(cockpitPollTimer);
        cockpitPollTimer = null;
    }
}

// ===== Chargement =====

async function loadCockpit() {
    if (!cockpitHubVisible() || cockpitInFlight) return;
    cockpitInFlight = true;
    try {
        cockpitLastStats = await api.getSystemStats();
        cockpitStatsError = null;
        // Buffer sparkline : un échantillon par relevé exploitable seulement.
        if (cockpitLastStats && cockpitLastStats.cpu
            && (cockpitLastStats.service === 'ok' || cockpitLastStats.service === 'degraded')) {
            pushCockpitCpuSample(cockpitLastStats.cpu.percent);
        }
    } catch (error) {
        console.warn('Cockpit : /api/admin/system/stats injoignable.', error);
        cockpitStatsError = (error && error.message) ? error.message : 'Erreur réseau';
        // cockpitLastStats garde la dernière valeur exploitable (éventuelle).
    }
    setCockpitStatusBadge();
    renderCockpitBody(); // métriques d'abord : la CA/updates ne bloquent pas
    await refreshCockpitSideData();
    renderCockpitBody(); // re-rendu sec avec les alertes fraîches
    cockpitInFlight = false;
}

/**
 * Données d'alertes, tolérantes aux pannes : CA lue une fois par session,
 * mises à jour via le cache partagé de admin-hub.js (fetchHubUpdatesStatus
 * ne lève jamais, un seul fetch par affichage du hub).
 */
async function refreshCockpitSideData() {
    if (cockpitCaPending) {
        cockpitCaPending = false;
        try {
            cockpitCaInfo = await api.getCaInfo();
        } catch (error) {
            console.warn('Cockpit : /api/pki/ca/info injoignable.', error);
            cockpitCaInfo = null;
        }
    }
    cockpitUpdatesData = await fetchHubUpdatesStatus();
}

/** Badge d'état de l'en-tête du cockpit (via hubctl / dégradé / …). */
function setCockpitStatusBadge() {
    const badge = document.getElementById('cockpitStatusBadge');
    if (!badge) return;
    badge.classList.remove('ok', 'warn');
    // Dernier fetch en échec : les valeurs affichées sont périmées → état
    // réseau prioritaire sur le dernier service connu.
    if (cockpitStatsError) {
        badge.textContent = 'erreur réseau';
        return;
    }
    const service = cockpitLastStats ? cockpitLastStats.service : null;
    if (service === 'ok') {
        badge.textContent = 'via hubctl';
        badge.classList.add('ok');
    } else if (service === 'degraded') {
        badge.textContent = 'dégradé';
        badge.classList.add('warn');
    } else if (service === 'unreachable') {
        badge.textContent = 'indisponible';
        badge.classList.add('warn');
    } else {
        badge.textContent = 'erreur réseau';
    }
}

// ===== Rendu =====

function renderCockpitBody() {
    const body = document.getElementById('cockpitBody');
    if (!body) return;
    const stats = cockpitLastStats;
    const service = stats ? stats.service : null;

    if (service === 'unreachable') {
        body.innerHTML = cockpitUnreachableHtml(stats.hint) + cockpitAlertsHtml();
        return;
    }

    if (service === 'ok' || service === 'degraded') {
        const notes = [];
        if (service === 'degraded') notes.push(cockpitDegradedHtml(stats.errors));
        if (cockpitStatsError) notes.push(cockpitInlineErrorHtml());
        body.innerHTML = notes.join('')
            + cockpitCpuHtml(stats)
            + cockpitMemoryHtml(stats)
            + cockpitKpisHtml(stats)
            + cockpitAlertsHtml();
        return;
    }

    // Pas encore de relevé : placeholder de chargement, ou message discret
    // inline si le premier fetch a échoué (jamais de popup/toast bloquant).
    if (cockpitStatsError) {
        body.innerHTML = cockpitInlineErrorHtml() + cockpitAlertsHtml();
    } else {
        body.innerHTML = '<div class="text-muted small text-center py-4">Chargement des métriques…</div>';
    }
}

function cockpitUnreachableHtml(hint) {
    return '<div class="cockpit-unreachable">'
        + '<div class="cockpit-unreachable-title">' + hubIcon('triangle-alert')
        + 'Cockpit indisponible — agent hubctl injoignable</div>'
        + (hint ? '<div class="cockpit-unreachable-hint">' + escapeHtml(String(hint)) + '</div>' : '')
        + '</div>';
}

function cockpitDegradedHtml(errors) {
    const keys = (errors && typeof errors === 'object') ? Object.keys(errors) : [];
    const detail = keys.length > 0 ? ' : ' + escapeHtml(keys.join(', ')) : '';
    return '<div class="cockpit-note warn">' + hubIcon('triangle-alert')
        + '<span>Métriques partielles — capteurs en échec' + detail + '</span></div>';
}

function cockpitInlineErrorHtml() {
    return '<div class="cockpit-note muted">' + hubIcon('triangle-alert')
        + '<span>Relevé indisponible (' + escapeHtml(cockpitStatsError || 'erreur réseau')
        + ') — nouvel essai au prochain tick (10 s)</span></div>';
}

function cockpitCpuHtml(stats) {
    const cpu = stats.cpu || {};
    const pct = Number.isFinite(Number(cpu.percent)) ? Math.round(Number(cpu.percent)) : null;
    const points = cockpitSparkPoints();
    const shapes = points
        ? '<path d="M' + points.replace(/ /g, ' L') + ' L260,44 L0,44 Z"/>'
            + '<polyline points="' + points + '"/>'
        : '';
    const legend = '60 derniers échantillons (polling 10 s)'
        + (Number.isFinite(Number(cpu.cores)) ? ' — ' + Number(cpu.cores) + ' cœurs' : '');
    return '<div class="cockpit-section">'
        + '<div class="cockpit-metric-head">' + hubIcon('cpu')
        + '<span class="cockpit-metric-label">CPU</span>'
        + '<span class="cockpit-value-lg">' + (pct === null ? '—' : pct + '&nbsp;%') + '</span>'
        + '</div>'
        + '<svg class="cockpit-spark" viewBox="0 0 260 44" preserveAspectRatio="none" aria-hidden="true">'
        + shapes + '</svg>'
        + '<div class="cockpit-legend">' + legend + '</div>'
        + '</div>';
}

function cockpitMemoryHtml(stats) {
    const mem = stats.memory || {};
    const pct = Number.isFinite(Number(mem.percent)) ? Math.round(Number(mem.percent)) : null;
    // « X,X / N Go » (maquette) : 1 décimale, sans ,0 superflu sur les valeurs rondes.
    const fmtGo = (mb) => cockpitFrNumber(mb / 1024, 1).replace(/,0$/, '');
    const detail = Number.isFinite(Number(mem.used_mb)) && Number.isFinite(Number(mem.total_mb))
        ? fmtGo(mem.used_mb) + ' / ' + fmtGo(mem.total_mb) + ' Go'
        : '';
    const width = pct === null ? 0 : Math.min(100, Math.max(0, pct));
    return '<div class="cockpit-section">'
        + '<div class="cockpit-metric-head">' + hubIcon('gauge')
        + '<span class="cockpit-metric-label">Mémoire</span>'
        + '<span class="cockpit-value">' + (pct === null ? '—' : pct + '&nbsp;%') + '</span>'
        + (detail ? '<span class="cockpit-value-detail">' + detail + '</span>' : '')
        + '</div>'
        + '<div class="cockpit-bar"><div class="cockpit-bar-fill" style="width:' + width + '%"></div></div>'
        + '</div>';
}

function cockpitKpisHtml(stats) {
    const disk = stats.disk || {};
    const diskPct = Number.isFinite(Number(disk.percent)) ? Math.round(Number(disk.percent)) : null;
    const diskWidth = diskPct === null ? 0 : Math.min(100, Math.max(0, diskPct));
    // Titre du KPI disque = basename du chemin suivi par hubctl (ex. POULIDOR).
    const diskName = (typeof disk.path === 'string' && disk.path)
        ? (disk.path.split('/').filter(Boolean).pop() || disk.path) : '';
    const load1 = Number.isFinite(Number(stats.cpu && stats.cpu.load1))
        ? cockpitFrNumber(stats.cpu.load1, 2) : '—';
    const uptime = Number.isFinite(Number(stats.uptime_seconds))
        ? cockpitFormatUptime(stats.uptime_seconds) : '—';
    const apps = cockpitActiveAppsCount();

    return '<div class="cockpit-kpis">'
        + '<div class="cockpit-kpi"' + (diskName ? ' title="' + escapeAttr(diskName) + '"' : '') + '>'
        + '<div class="cockpit-kpi-label">' + hubIcon('hard-drive') + 'Disque parc</div>'
        + '<div class="cockpit-kpi-value">' + (diskPct === null ? '—' : diskPct + '&nbsp;%') + '</div>'
        + '<div class="cockpit-bar"><div class="cockpit-bar-fill" style="width:' + diskWidth + '%"></div></div>'
        + '</div>'
        + '<div class="cockpit-kpi">'
        + '<div class="cockpit-kpi-label">' + hubIcon('activity') + 'Charge (1 min)</div>'
        + '<div class="cockpit-kpi-value">' + load1 + '</div>'
        + '</div>'
        + '<div class="cockpit-kpi">'
        + '<div class="cockpit-kpi-label">' + hubIcon('clock') + 'Uptime</div>'
        + '<div class="cockpit-kpi-value">' + uptime + '</div>'
        + '</div>'
        + '<div class="cockpit-kpi">'
        + '<div class="cockpit-kpi-label">' + hubIcon('zap') + 'Apps actives</div>'
        + '<div class="cockpit-kpi-value">' + (apps === null ? '—' : apps) + '</div>'
        + '</div>'
        + '</div>';
}

function cockpitAlertsHtml() {
    const rows = [];
    // Mises à jour du socle — cache partagé admin-hub.js, aucun fetch propre.
    const count = cockpitUpdatesData ? countHubUpdatesAvailable(cockpitUpdatesData) : null;
    if (count === null) {
        rows.push(cockpitAlertRow('muted', 'refresh-cw', 'Mises à jour du socle : état inconnu'));
    } else if (count > 0) {
        rows.push(cockpitAlertRow('warn', 'triangle-alert',
            count + ' mise' + (count > 1 ? 's' : '') + ' à jour du socle disponible'
            + (count > 1 ? 's' : '')));
    } else {
        rows.push(cockpitAlertRow('ok', 'check', 'Socle à jour'));
    }
    rows.push(cockpitCaAlertHtml());
    return '<div class="cockpit-alerts">' + rows.join('') + '</div>';
}

function cockpitCaAlertHtml() {
    const expires = cockpitCaInfo ? new Date(cockpitCaInfo.not_after) : null;
    if (!expires || isNaN(expires.getTime())) {
        return cockpitAlertRow('muted', 'shield-check', 'Certificat CA : état inconnu');
    }
    const days = Math.ceil((expires.getTime() - Date.now()) / 86400000);
    const label = 'Certificat CA : expire le ' + expires.toLocaleDateString('fr-FR');
    if (days < 0) return cockpitAlertRow('err', 'shield-check', label + ' — expiré');
    if (days <= COCKPIT_CA_WARN_DAYS) return cockpitAlertRow('warn', 'shield-check', label);
    return cockpitAlertRow('ok', 'shield-check', label);
}

function cockpitAlertRow(tone, icon, text) {
    return '<div class="cockpit-alert ' + tone + '">' + hubIcon(icon)
        + '<span>' + text + '</span></div>';
}

// ===== Helpers =====

/**
 * Apps actives depuis le cache `allApps` (chargé par loadAdminData à
 * l'ouverture admin — AUCUN fetch propre, même filtre que le badge de la
 * ligne Applications). Cache vide/inexistant ⇒ null (« — » à l'affichage).
 */
function cockpitActiveAppsCount() {
    if (typeof allApps === 'undefined' || !Array.isArray(allApps) || allApps.length === 0) return null;
    return allApps.filter(a =>
        a && a.is_active && a.approval_status !== 'pending' && a.approval_status !== 'rejected'
    ).length;
}

/** Buffer circulaire CPU : pousse un échantillon (%) borné à [0, 100]. */
function pushCockpitCpuSample(percent) {
    const value = Number(percent);
    if (!Number.isFinite(value)) return;
    cockpitCpuSamples.push(Math.min(100, Math.max(0, value)));
    if (cockpitCpuSamples.length > COCKPIT_CPU_MAX_SAMPLES) cockpitCpuSamples.shift();
}

/**
 * Points SVG de la sparkline (viewBox 260×44) : x réparti sur les 60
 * emplacements (la courbe s'étend vers la droite tant que le buffer n'est
 * pas plein), y de 42 (0 %) à 2 (100 %). Redessin sec, aucune animation.
 */
function cockpitSparkPoints() {
    const n = cockpitCpuSamples.length;
    if (n === 0) return '';
    const stepX = 260 / (COCKPIT_CPU_MAX_SAMPLES - 1);
    return cockpitCpuSamples.map((pct, i) => {
        const x = (i * stepX).toFixed(1);
        const y = (42 - (pct / 100) * 40).toFixed(1);
        return x + ',' + y;
    }).join(' ');
}

/**
 * Uptime humain (j/h/min) : réutilise le formateur global formatUptime
 * (apps-health.js, même format) avec repli local identique si le module
 * n'est pas chargé.
 */
function cockpitFormatUptime(seconds) {
    if (typeof formatUptime === 'function') return formatUptime(seconds);
    const total = Number(seconds);
    if (!Number.isFinite(total) || total < 0) return '—';
    const s = Math.floor(total);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'min';
    if (s < 86400) {
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        return m > 0 ? h + 'h ' + m + 'min' : h + 'h';
    }
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    return h > 0 ? d + 'j ' + h + 'h' : d + 'j';
}

/** Nombre décimal à la française (ex. 4,1) — '—' si non numérique. */
function cockpitFrNumber(value, digits) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toFixed(digits).replace('.', ',');
}
