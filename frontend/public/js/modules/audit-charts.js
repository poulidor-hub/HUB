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
