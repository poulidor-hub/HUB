// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub - graphiques ECharts de la page Audit (usage par application).
 * Registre d'instances + helpers de rendu. Les fonctions sont globales
 * (convention des modules du HUB, handlers data-action).
 */

// containerId -> instance echarts
const _usageCharts = new Map();

// Récupère ou crée l'instance ECharts d'un conteneur
function usageChart(containerId) {
    let chart = _usageCharts.get(containerId);
    const el = document.getElementById(containerId);
    if (!el) return null;
    if (!chart) {
        // Purger un éventuel état vide (.usage-empty) superposé au re-rendu
        el.innerHTML = '';
        chart = echarts.init(el);
        _usageCharts.set(containerId, chart);
    }
    return chart;
}

// Détruit proprement l'instance d'un conteneur (anti-fuite)
function usageChartDispose(containerId) {
    const chart = _usageCharts.get(containerId);
    if (chart) {
        chart.dispose();
        _usageCharts.delete(containerId);
    }
}

// Détruit toutes les instances d'une liste de conteneurs
function usageChartsDispose(containerIds) {
    (containerIds || []).forEach(usageChartDispose);
}

// Tokens CSS -> thème commun des axes/tooltips
function usageChartAxisStyle() {
    const cs = getComputedStyle(document.documentElement);
    return {
        lineColor: cs.getPropertyValue('--border-color').trim() || '#dee2e6',
        labelColor: cs.getPropertyValue('--text-secondary', '#6c757d').trim() || '#6c757d',
    };
}

// --- Différenciation des apps partageant la même couleur -------------------
// Les couleurs d'apps sont libres : des doublons existent. On assigne à chaque
// occurrence d'un groupe de couleur un "variant" (motif decal pour les barres,
// bordure en pointillés pour les rectangles custom) + un contraste adapté à la
// luminance (motif clair sur fond sombre, sombre sur fond clair).

// #rrggbb -> couleur de motif contrastée
function usageContrastOn(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
    if (!m) return 'rgba(0,0,0,0.4)';
    const v = parseInt(m[1], 16);
    const lum = 0.299 * ((v >> 16) & 255) + 0.587 * ((v >> 8) & 255) + 0.114 * (v & 255);
    return lum > 150 ? 'rgba(0,0,0,0.38)' : 'rgba(255,255,255,0.6)';
}

// Variantes decal (barres) — la 1re occurrence d'un groupe reste pleine
const USAGE_DECAL_VARIANTS = [
    null,
    (c) => ({ symbol: 'circle', symbolSize: 0.8, color: c, dashArrayX: [1, 0], dashArrayY: [2, 2] }),
    (c) => ({ dashArrayX: [5, 3], dashArrayY: [1, 0], rotation: 0, color: c }),
    (c) => ({ dashArrayX: [5, 3], dashArrayY: [1, 0], rotation: Math.PI / 4, color: c }),
    (c) => ({ dashArrayX: [2, 2], dashArrayY: [2, 2], color: c }),
];

// Variantes de bordure (rectangles custom de la timeline)
const USAGE_DASH_VARIANTS = [null, [8, 4], [2, 3], [10, 3, 3, 3]];

// totals -> { perSlug: Map<app_slug, {variant, contrast}> }
function usageColorVariants(totals) {
    const seen = new Map(); // color -> occurrence count
    const perSlug = new Map();
    (totals || []).forEach(app => {
        const n = (seen.get(app.color) || 0);
        seen.set(app.color, n + 1);
        const variant = Math.min(n, USAGE_DECAL_VARIANTS.length - 1);
        perSlug.set(app.app_slug, {
            variant,
            decal: USAGE_DECAL_VARIANTS[variant] ? USAGE_DECAL_VARIANTS[variant](usageContrastOn(app.color)) : null,
            dash: USAGE_DASH_VARIANTS[Math.min(variant, USAGE_DASH_VARIANTS.length - 1)],
            stroke: usageContrastOn(app.color),
            dup: n > 0, // true dès qu'une autre app partage cette couleur
        });
    });
    // Les variants ne sont utiles que si le groupe compte 2+ apps :
    // un groupe singleton reste sans décoration.
    (totals || []).forEach(app => {
        const g = perSlug.get(app.app_slug);
        g.dup = (seen.get(app.color) || 0) > 1;
        if (!g.dup) { g.variant = 0; g.decal = null; g.dash = null; }
    });
    return perSlug;
}

// Message d'état vide standard (tracking récent)
const USAGE_EMPTY_MSG = 'Données disponibles depuis le déploiement du tracking d\'usage.';

// Redimensionnement automatique de toutes les instances
window.addEventListener('resize', () => {
    _usageCharts.forEach(c => c.resize());
});

// Timestamp UTC explicite : le backend renvoie de l'ISO naïf (sans Z) —
// `new Date(iso)` l'interpréterait en heure LOCALE du navigateur. La page
// uptime est étiquetée UTC (badge + sélecteur de date UTC) : on force l'UTC.
function parseUtc(iso) {
    const s = String(iso || '');
    return new Date(/Z$|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z');
}

// #rrggbb -> rgba(r,g,b,a)
function usageHexToRgba(hex, alpha) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
    if (!m) return `rgba(13,110,253,${alpha})`;
    const v = parseInt(m[1], 16);
    return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${alpha})`;
}

// Heatmap « Activité de la journée » : lignes = apps, colonnes = 8 tranches de 3h.
// Intensité d'une cellule = proportion de présence dans la tranche (opacité de la
// couleur de l'app) ; tooltip = durée exacte. Design validé (option C, 2026-08-23).
function renderUserDayTimeline(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const dayStart = new Date(data.date + 'T00:00:00Z').getTime();
    const BUCKET_S = 3 * 3600;           // tranche de 3h en secondes
    const N_BUCKETS = 8;
    const bucketLabels = Array.from({ length: N_BUCKETS }, (_, i) => `${String(i * 3).padStart(2, '0')}h`);
    const bucketRange = (i) => `${bucketLabels[i]}–${String((i + 1) * 3).padStart(2, '0')}h`;

    const fmtH = (s) => {
        const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
        return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
    };

    // Cumul par (app, tranche) des recouvrements des segments, clampé au jour
    const totals = data.totals || [];
    const buckets = totals.map(() => new Array(N_BUCKETS).fill(0));
    (data.segments || []).forEach(seg => {
        const appIndex = totals.findIndex(a => a.app_slug === seg.app_slug);
        if (appIndex < 0) return;
        const s = Math.max(parseUtc(seg.started_at).getTime(), dayStart);
        const e = Math.min(parseUtc(seg.ended_at).getTime(), dayStart + 86400000);
        if (e <= s) return;
        for (let i = 0; i < N_BUCKETS; i++) {
            const bs = dayStart + i * BUCKET_S * 1000;
            const be = bs + BUCKET_S * 1000;
            const ov = Math.min(e, be) - Math.max(s, bs);
            if (ov > 0) buckets[appIndex][i] += ov / 1000;
        }
    });

    // Cellules : [x, y, secondes, appIndex] — couleur par item = couleur d'app × alpha
    const cells = [];
    buckets.forEach((row, yi) => {
        row.forEach((seconds, xi) => {
            cells.push([xi, yi, Math.min(seconds, BUCKET_S), yi]);
        });
    });

    // Hauteur dynamique : ~30px par app, ne rétrécit jamais
    const el = document.getElementById(containerId);
    const neededHeight = Math.max(150, 30 + totals.length * 30 + 34);
    if (el && el.clientHeight < neededHeight) el.style.height = `${neededHeight}px`;

    const names = totals.map(a => a.app_name.length > 16 ? a.app_name.slice(0, 15) + '…' : a.app_name);

    chart.setOption({
        tooltip: {
            trigger: 'item',
            formatter: (p) => {
                const [xi, yi, seconds] = [p.value[0], p.value[1], p.value[2]];
                const app = totals[yi];
                const pct = Math.round(seconds / BUCKET_S * 100);
                return `${escapeHtml(app ? app.app_name : '')} — ${bucketRange(xi)}`
                    + `<br/>${fmtH(seconds)} (${pct}% de la tranche)`;
            }
        },
        grid: { left: 130, right: 10, top: 10, bottom: 26, containLabel: true },
        xAxis: {
            type: 'category', data: bucketLabels, boundaryGap: true,
            axisLabel: { color: axis.labelColor },
            axisLine: { lineStyle: { color: axis.lineColor } },
            axisTick: { show: false }, splitLine: { show: false }
        },
        yAxis: {
            type: 'category', data: names, inverse: true,
            axisLabel: { color: axis.labelColor, width: 120, overflow: 'truncate' },
            axisLine: { lineStyle: { color: axis.lineColor } },
            axisTick: { show: false }, splitLine: { show: false }
        },
        animation: false,
        series: [{
            type: 'heatmap',
            data: cells,
            itemStyle: {
                borderColor: '#fff', borderWidth: 1, borderRadius: 2,
                color: (p) => {
                    const app = totals[p.value[3]];
                    const seconds = p.value[2];
                    if (!app || seconds <= 0) return '#f1f3f5';
                    const ratio = Math.min(seconds / BUCKET_S, 1);
                    return usageHexToRgba(app.color, 0.18 + 0.82 * ratio);
                }
            },
            emphasis: { disabled: true }
        }]
    }, true);
    chart.resize();
}

// Répartition du temps par app : une barre horizontale PAR application.
// (L'ancienne barre unique empilée ne représentait pas l'usage parallèle :
// la somme des apps peut dépasser le temps réel de la journée.)
// Apps partageant une couleur : motif decal distinctif (points, tirets,
// hachures, grille) calculé selon le contraste de la couleur.
function renderUserDayBreakdown(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const variants = usageColorVariants(data.totals);
    const fmtH = (s) => {
        const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
        return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
    };
    const names = data.totals.map(a => a.app_name.length > 16 ? a.app_name.slice(0, 15) + '…' : a.app_name);

    // Hauteur dynamique : une rangée par app (30px), ne rétrécit jamais
    const el = document.getElementById(containerId);
    const neededHeight = Math.max(110, 24 + data.totals.length * 30 + 16);
    if (el && el.clientHeight < neededHeight) el.style.height = `${neededHeight}px`;

    // UNE SEULE série (ex-« une série par app ») : avec N séries bar sur un
    // axe catégorie, ECharts place les N barres CÔTE À CÔTE dans un cluster
    // par bande — dès 2-3 apps, les barres débordent de leur bande de 30px
    // et atterrissent visuellement à côté du mauvais label (tooltip ≠ nom
    // à gauche). En série unique, chaque barre est centrée sur sa bande ;
    // le decal (motif différenciant les couleurs dupliquées) est porté par
    // itemStyle du data item (ECharts ≥ 5), pas par la série.
    const series = [{
        name: 'Temps par application',
        type: 'bar',
        barWidth: 18,
        data: data.totals.map((app) => {
            const v = variants.get(app.app_slug) || {};
            return {
                value: app.total_seconds,
                itemStyle: { color: app.color, decal: v.decal || undefined },
            };
        }),
        label: {
            show: true, position: 'right', color: axis.labelColor,
            formatter: (p) => fmtH(p.value)
        },
        tooltip: {
            formatter: (p) => {
                const app = data.totals[p.dataIndex] || {};
                const total = data.day_total_seconds || 0;
                const pct = total > 0 ? Math.round(app.total_seconds / total * 100) : 0;
                const pctDay = Math.round(app.total_seconds / 86400 * 100);
                return `${escapeHtml(app.app_name)} — ${fmtH(app.total_seconds)}`
                    + `<br/>${pct}% du temps total`
                    + `<br/>${pctDay}% de la journée`;
            }
        }
    }];

    chart.setOption({
        tooltip: { trigger: 'item' },
        grid: { left: 130, right: 60, top: 10, bottom: 10, containLabel: true },
        xAxis: { show: false, type: 'value', max: (v) => (v.max > 0 ? v.max * 1.15 : 3600) },
        yAxis: {
            type: 'category', inverse: true, data: names,
            axisLabel: { color: axis.labelColor, width: 120, overflow: 'truncate' },
            axisLine: { lineStyle: { color: axis.lineColor } }, axisTick: { show: false }
        },
        animation: false,
        series
    }, true);
    chart.resize();
}

// Heatmap parc : lignes = jours, colonnes = heures 0-23
function renderUsageHeatmap(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const days = data.days || [];
    const flat = [];
    let maxV = 0;
    days.forEach((d, yi) => d.hours.forEach((v, xi) => {
        flat.push([xi, yi, v]);
        if (v > maxV) maxV = v;
    }));
    chart.setOption({
        tooltip: {
            position: 'top',
            formatter: (p) => {
                const d = days[days.length - 1 - p.value[1]];
                return `${d ? d.date : ''} ${String(p.value[0]).padStart(2, '0')}:00 — ${p.value[2]} session(s)`;
            }
        },
        grid: { left: 90, right: 20, top: 10, bottom: 60 },
        xAxis: {
            type: 'category', data: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0')),
            axisLabel: { color: axis.labelColor }, axisLine: { lineStyle: { color: axis.lineColor } },
            splitLine: { show: false }
        },
        yAxis: {
            type: 'category', data: days.map(d => d.date).reverse(),
            axisLabel: { color: axis.labelColor }, axisLine: { lineStyle: { color: axis.lineColor } }
        },
        visualMap: {
            min: 0, max: Math.max(maxV, 1), calculable: false,
            orient: 'horizontal', left: 'center', bottom: 0,
            inRange: { color: ['#e9ecef', '#0d6efd'] }
        },
        series: [{
            type: 'heatmap',
            data: flat.map(([x, y, v]) => [x, days.length - 1 - y, v])
        }]
    }, true);
    // Re-mesure le conteneur : si une instance a été créée pendant que l'onglet
    // était caché (SSE audit_updated → renderUsageHeatmap sur panneau display:none,
    // canvas mesuré à 0px), setOption seul réutilise cette taille fausse et la
    // vue d'ensemble reste « écrasée » jusqu'au F5. resize() aligne le canvas sur
    // le conteneur désormais visible — même contractuelle que les renderers
    // renderUserDayTimeline / renderUserDayBreakdown ci-dessus.
    chart.resize();
}

// Courbe simultanés + barres uniques/jour (mixte, double axe Y)
function renderAppUsageChart(containerId, app) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    chart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { top: 0, textStyle: { color: axis.labelColor } },
        grid: { left: 40, right: 40, top: 30, bottom: 40 },
        xAxis: [
            {
                type: 'time',
                axisLabel: { color: axis.labelColor },
                axisLine: { lineStyle: { color: axis.lineColor } }
            }
        ],
        yAxis: [
            { type: 'value', name: 'Simultanés', minInterval: 1, axisLabel: { color: axis.labelColor }, splitLine: { lineStyle: { color: axis.lineColor } } },
            { type: 'value', name: 'Uniques/jour', minInterval: 1, axisLabel: { color: axis.labelColor }, splitLine: { show: false } }
        ],
        series: [
            {
                name: 'Utilisateurs simultanés', type: 'line', showSymbol: false,
                data: (app.simultaneous || []).map(p => [p.t, p.count]),
                itemStyle: { color: app.color }, lineStyle: { width: 2 }
            },
            {
                name: 'Uniques par jour', type: 'bar', yAxisIndex: 1,
                data: (app.daily_uniques || []).map(d => [d.date + 'T12:00:00Z', d.count]),
                itemStyle: { color: app.color, opacity: 0.35 }, barWidth: '60%'
            }
        ]
    }, true);
}
