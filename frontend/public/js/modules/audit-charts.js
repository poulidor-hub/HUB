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

// Message d'état vide standard (tracking récent)
const USAGE_EMPTY_MSG = 'Données disponibles depuis le déploiement du tracking d\'usage.';

// Redimensionnement automatique de toutes les instances
window.addEventListener('resize', () => {
    _usageCharts.forEach(c => c.resize());
});

// Timeline 00h→24h : segments colorés par app (custom series sur axe temps)
function renderUserDayTimeline(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const dayStart = new Date(data.date + 'T00:00:00Z').getTime();
    const dayEnd = dayStart + 86400000;

    const series = data.totals.map(app => ({
        name: app.app_name,
        type: 'custom',
        renderItem: (params, api) => {
            const x1 = api.coord([api.value(0), 0])[0];
            const x2 = api.coord([api.value(1), 0])[0];
            const height = 28;
            return {
                type: 'rect',
                shape: { x: x1, y: params.coordSys.y + 10, width: Math.max(x2 - x1, 2), height },
                style: { fill: app.color }
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
    }));

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
        series
    }, true);
}

// Répartition du temps par app : barre empilée horizontale (1 barre = journée)
function renderUserDayBreakdown(containerId, data) {
    const chart = usageChart(containerId);
    if (!chart) return;
    const axis = usageChartAxisStyle();
    const fmtH = (s) => {
        const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
        return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
    };
    chart.setOption({
        tooltip: {
            trigger: 'item',
            formatter: (p) => `${escapeHtml(p.name)} — ${fmtH(p.value)} (${p.percent}%)`
        },
        legend: { top: 0, textStyle: { color: axis.labelColor } },
        grid: { left: 10, right: 10, top: 30, bottom: 8 },
        xAxis: { show: false },
        yAxis: { show: false, data: [''] },
        series: data.totals.map(app => ({
            name: app.app_name,
            type: 'bar',
            stack: 'total',
            barWidth: 36,
            itemStyle: { color: app.color },
            data: [app.total_seconds]
        }))
    }, true);
}
