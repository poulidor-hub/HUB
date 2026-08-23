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

// Timeline 00h→24h : une rangée par app (segments parallèles), nom de l'app
// en tête de rangée + bordures pointillées si plusieurs apps partagent la couleur
function renderUserDayTimeline(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const dayStart = new Date(data.date + 'T00:00:00Z').getTime();
    const dayEnd = dayStart + 86400000;
    const variants = usageColorVariants(data.totals);

    // Une rangée par app (36px) — les usages parallèles ne se chevauchent plus
    const el = document.getElementById(containerId);
    const neededHeight = Math.max(140, 40 + data.totals.length * 36 + 30);
    if (el && el.clientHeight < neededHeight) el.style.height = `${neededHeight}px`;

    const rowY = (i) => 10 + i * 36;

    const series = data.totals.map((app, appIndex) => {
        const v = variants.get(app.app_slug) || {};
        return {
            name: app.app_name,
            type: 'custom',
            renderItem: (params, api) => {
                const x1 = api.coord([api.value(0), 0])[0];
                const x2 = api.coord([api.value(1), 0])[0];
                return {
                    type: 'rect',
                    shape: { x: x1, y: params.coordSys.y + rowY(appIndex), width: Math.max(x2 - x1, 2), height: 28 },
                    style: {
                        fill: app.color,
                        // Doublon de couleur : bordure contrastée en pointillés distinctifs
                        stroke: v.dup ? v.stroke : null,
                        lineWidth: v.dup ? 2 : 0,
                        lineDash: v.dup ? (v.dash || [8, 4]) : null,
                    }
                };
            },
            encode: { x: [0, 1], y: 0 },
            data: data.segments
                .filter(s => s.app_slug === app.app_slug)
                .map(s => [new Date(s.started_at).getTime(), new Date(s.ended_at).getTime()]),
            tooltip: {
                formatter: (p) => {
                    const seg = data.segments.filter(s => s.app_slug === app.app_slug)[p.dataIndex];
                    const mins = Math.round((seg ? seg.duration_seconds : 0) / 60);
                    return `${escapeHtml(app.app_name)} — ${mins} min`;
                }
            }
        };
    });

    // Étiquettes de rangée : nom de l'app à gauche de sa ligne (identification
    // immédiate même quand plusieurs apps partagent la même couleur)
    series.push({
        type: 'custom',
        silent: true,
        renderItem: (params, api) => {
            const x = api.coord([api.value(0), 0])[0];
            return {
                type: 'text',
                style: {
                    text: api.value(1) != null ? String(data.totals[api.value(1)].app_name) : '',
                    x: x + 2,
                    y: params.coordSys.y + rowY(api.value(1)) + 15,
                    fill: axis.labelColor,
                    font: '11px ' + (csFont() || 'sans-serif'),
                    backgroundColor: 'rgba(127,127,127,0.18)',
                    padding: [1, 4],
                }
            };
        },
        encode: { x: 0, y: 1 },
        data: data.totals.map((app, i) => [dayStart + 30 * 60000, i]),
    });

    chart.setOption({
        tooltip: { trigger: 'item' },
        legend: { top: 0, textStyle: { color: axis.labelColor } },
        grid: { left: 10, right: 10, top: 30, bottom: 24 },
        xAxis: {
            type: 'time', min: dayStart, max: dayEnd,
            axisLabel: { color: axis.labelColor, formatter: '{HH}:{mm}' },
            axisLine: { lineStyle: { color: axis.lineColor } },
            splitLine: { show: false }
        },
        yAxis: { show: false, data: [''] },
        animation: false,
        series
    }, true);
    chart.resize();
}

// Police du design system pour les étiquettes custom
function csFont() {
    const cs = getComputedStyle(document.documentElement);
    return cs.getPropertyValue('--font-family-base').trim() || null;
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

    // Une série par app (le decal ECharts est porté par la série, pas l'item) :
    // chaque série ne porte qu'une valeur, à sa position sur l'axe catégorie.
    const series = data.totals.map((app, i) => {
        const v = variants.get(app.app_slug) || {};
        return {
            name: app.app_name,
            type: 'bar',
            barWidth: 18,
            itemStyle: { color: app.color },
            decal: v.decal || undefined,
            label: {
                show: true, position: 'right', color: axis.labelColor,
                formatter: (p) => fmtH(p.value)
            },
            data: data.totals.map((_, j) => (j === i ? app.total_seconds : null)),
            tooltip: {
                formatter: (p) => {
                    const total = data.day_total_seconds || 0;
                    const pct = total > 0 ? Math.round(app.total_seconds / total * 100) : 0;
                    const pctDay = Math.round(app.total_seconds / 86400 * 100);
                    return `${escapeHtml(app.app_name)} — ${fmtH(app.total_seconds)}`
                        + `<br/>${pct}% du temps total`
                        + `<br/>${pctDay}% de la journée`;
                }
            }
        };
    });

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
