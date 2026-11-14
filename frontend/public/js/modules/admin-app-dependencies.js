// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — graphe des dépendances inter-apps (admin)
 *
 * Source : GET /api/apps/dependencies (admin) — déclarations depends.yaml
 * poussées au register par les apps (_push_deps_to_hub).
 * Rendu : SVG statique sans lib externe ni animation (perf/stabilité),
 * tooltip HTML, tableau compact en dessous. Mêmes conventions que
 * admin-apps.js : fonctions globales + data-action.
 *
 * Lisibilité (feat 3.89) :
 *  - ordre des nœuds par barycentre (minimise les croisements) au lieu du
 *    tri alphabétique ;
 *  - points d'ancrage des arêtes étalés le long de chaque nœud (les arcs
 *    ne partent plus tous du même point) ;
 *  - épaisseur/opacité des arêtes selon la fraîcheur (le frais ressort
 *    même quand l'essentiel est rouge) ;
 *  - bascule Graphe / Matrice (matrice d'adjacence : lignes sources ×
 *    colonnes cibles, cases colorées par fraîcheur, tooltip réutilisé).
 */

// ===== État local =====

let depsData = null;        // [{source, target, note, declared_at, last_sync_at}]
let depsAppsIndex = {};     // slug -> application (registre)
let depsSelectedNode = null;
let depsListenersBound = false;
let depsViewMode = 'graph'; // 'graph' | 'matrix'

const DEPS_FRESH_DAYS = 7;   // ≤ 7 j : fraîche (vert)
const DEPS_STALE_DAYS = 30;  // > 30 j ou jamais : rouge
const DEPS_NODE_W = 160;
const DEPS_NODE_H = 34;
const DEPS_ROW_H = 46;
const DEPS_COL_STEP = 260;
const DEPS_MARGIN = 24;

// ===== Chargement =====

async function loadDependenciesGraph(force = false) {
    const statusEl = document.getElementById('depsGraphStatus');
    if (!statusEl) return;
    if (depsData && !force) {
        renderDependenciesView();
        return;
    }
    statusEl.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Chargement des dépendances…';
    try {
        const [deps, apps] = await Promise.all([
            api.getAppDependencies(),
            api.getAllApps('false')
        ]);
        depsData = Array.isArray(deps?.dependencies) ? deps.dependencies : [];
        depsAppsIndex = {};
        (Array.isArray(apps) ? apps : (apps?.apps || [])).forEach(app => {
            depsAppsIndex[app.slug] = app;
        });
        depsSelectedNode = null;
        renderDependenciesView();
    } catch (error) {
        console.error('loadDependenciesGraph: Erreur:', error);
        statusEl.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle me-1"></i>Erreur de chargement : ${escapeHtml(error.message || 'inconnue')}</span>`;
    }
}

function depsRefresh() {
    loadDependenciesGraph(true);
}

// ===== Helpers =====

function depsNodeStatus(slug) {
    const app = depsAppsIndex[slug];
    if (!app) return 'unknown';     // pas dans le registre
    if (!app.is_active) return 'inactive';
    return 'active';
}

function depsEdgeFreshness(dep) {
    if (!dep.last_sync_at) return 'never';
    const days = (Date.now() - new Date(dep.last_sync_at).getTime()) / 86400000;
    if (days <= DEPS_FRESH_DAYS) return 'fresh';
    if (days <= DEPS_STALE_DAYS) return 'stale';
    return 'never';
}

const DEPS_EDGE_COLORS = {
    fresh: '#198754',
    stale: '#fd7e14',
    never: '#dc3545'
};

const DEPS_STATUS_LABELS = {
    active: '<span class="badge bg-success">Active</span>',
    inactive: '<span class="badge bg-secondary">Inactive</span>',
    unknown: '<span class="badge bg-danger">Hors registre</span>'
};

function depsFmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString('fr-FR');
}

function depsTruncate(text, max) {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
}

// ===== Lisibilité : ordre barycentre + ancrages étalés =====

/**
 * Ordre des slugs par colonne minimisant les croisements d'arêtes :
 * heuristique du barycentre, balayages alternés bas→haut / haut→bas,
 * tiebreak alphabétique → déterministe (même donnée ⇒ même rendu).
 * Remplace le tri alphabétique, qui maximisait les croisements.
 */
function depsComputeOrderedColumns(columns) {
    const inMap = {}, outMap = {};
    depsData.forEach(dep => {
        if (dep.source === dep.target) return;
        (outMap[dep.source] ??= []).push(dep.target);
        (inMap[dep.target] ??= []).push(dep.source);
    });
    const order = columns.map(col => col.slugs.slice());
    const posOf = {};
    const reindex = () => order.forEach((arr, ci) => arr.forEach((slug, ri) => { posOf[slug] = { col: ci, row: ri }; }));
    reindex();
    const mean = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : Infinity;
    for (let sweep = 0; sweep < 8; sweep++) {
        for (let ci = 1; ci < order.length; ci++) {
            order[ci] = order[ci]
                .map(slug => [slug, mean((inMap[slug] || []).map(t => posOf[t].row))])
                .sort((a, b) => (a[1] - b[1]) || a[0].localeCompare(b[0]))
                .map(x => x[0]);
            reindex();
        }
        for (let ci = order.length - 2; ci >= 0; ci--) {
            order[ci] = order[ci]
                .map(slug => [slug, mean((outMap[slug] || []).map(t => posOf[t].row))])
                .sort((a, b) => (a[1] - b[1]) || a[0].localeCompare(b[0]))
                .map(x => x[0]);
            reindex();
        }
    }
    return order;
}

/**
 * Points d'ancrage des arêtes le long d'un côté d'un nœud : les k arêtes
 * sont réparties uniformément (triées par position de leur extrémité
 * opposée) au lieu de partir toutes du point médian — les faisceaux se
 * séparent dès la sortie du nœud.
 */
function depsSpreadAnchors(edgeIndexes, p) {
    const anchors = new Map();
    const k = edgeIndexes.length;
    if (!k) return anchors;
    if (k === 1) {
        anchors.set(edgeIndexes[0], p.y + DEPS_NODE_H / 2);
        return anchors;
    }
    const y0 = p.y + 4, y1 = p.y + DEPS_NODE_H - 4;
    const step = (y1 - y0) / (k - 1);
    edgeIndexes.forEach((idx, i) => anchors.set(idx, y0 + i * step));
    return anchors;
}

function depsComputeAnchors(pos) {
    const bySource = {}, byTarget = {};
    depsData.forEach((dep, i) => {
        if (dep.source === dep.target) return;
        (bySource[dep.source] ??= []).push(i);
        (byTarget[dep.target] ??= []).push(i);
    });
    const anchorOut = {}, anchorIn = {};
    Object.keys(pos).forEach(slug => {
        const outs = (bySource[slug] || []).slice()
            .sort((a, b) => pos[depsData[a].target].row - pos[depsData[b].target].row);
        const ins = (byTarget[slug] || []).slice()
            .sort((a, b) => pos[depsData[a].source].row - pos[depsData[b].source].row);
        anchorOut[slug] = depsSpreadAnchors(outs, pos[slug]);
        anchorIn[slug] = depsSpreadAnchors(ins, pos[slug]);
    });
    return { anchorOut, anchorIn };
}

// Épaisseur/opacité par fraîcheur : hiérarchie visuelle même quand tout est rouge.
function depsEdgeWeight(fresh) {
    return fresh === 'fresh' ? 2.4 : fresh === 'stale' ? 1.7 : 1.1;
}

function depsEdgeOpacity(fresh, dimmed) {
    if (dimmed) return 0.15;
    return fresh === 'never' ? 0.72 : 0.9;
}

function depsEmptyAlertHtml() {
    return `
        <div class="alert alert-info mb-0">
            <i class="bi bi-info-circle me-2"></i>Aucune dépendance déclarée pour le moment.
            Les apps poussent leur <code>depends.yaml</code> au Hub à chaque register
            (le fichier doit être embarqué dans l'image — <code>COPY depends.yaml</code>).
        </div>`;
}

// ===== Bascule Graphe / Matrice =====

function depsSetViewMode(mode) {
    depsViewMode = mode === 'matrix' ? 'matrix' : 'graph';
    depsSyncViewToggle();
    depsRenderMainView();
}

function depsSyncViewToggle() {
    const graphBtn = document.getElementById('depsViewGraphBtn');
    const matrixBtn = document.getElementById('depsViewMatrixBtn');
    if (!graphBtn || !matrixBtn) return;
    const isGraph = depsViewMode === 'graph';
    graphBtn.classList.toggle('active', isGraph);
    matrixBtn.classList.toggle('active', !isGraph);
    graphBtn.setAttribute('aria-pressed', String(isGraph));
    matrixBtn.setAttribute('aria-pressed', String(!isGraph));
}

function depsBindViewToggle() {
    const graphBtn = document.getElementById('depsViewGraphBtn');
    const matrixBtn = document.getElementById('depsViewMatrixBtn');
    if (!graphBtn || !matrixBtn || graphBtn.dataset.depsBound) return;
    graphBtn.dataset.depsBound = '1';
    graphBtn.addEventListener('click', () => depsSetViewMode('graph'));
    matrixBtn.addEventListener('click', () => depsSetViewMode('matrix'));
}

function depsRenderMainView() {
    if (depsViewMode === 'matrix') renderDependenciesMatrix();
    else renderDependenciesGraph();
}

// ===== Rendu =====

function renderDependenciesView() {
    depsBindViewToggle();
    depsSyncViewToggle();
    depsRenderMainView();
    renderDependenciesTable();
    const statusEl = document.getElementById('depsGraphStatus');
    if (statusEl) {
        statusEl.innerHTML = `${depsData.length} dépendance${depsData.length > 1 ? 's' : ''} déclarée${depsData.length > 1 ? 's' : ''} — <span class="text-success">■</span> synchro ≤ ${DEPS_FRESH_DAYS} j · <span class="text-warning">■</span> ${DEPS_FRESH_DAYS}–${DEPS_STALE_DAYS} j · <span class="text-danger">■</span> > ${DEPS_STALE_DAYS} j ou jamais`;
    }
}

function renderDependenciesGraph() {
    const container = document.getElementById('depsGraphContainer');
    if (!container) return;

    if (!depsData.length) {
        container.innerHTML = depsEmptyAlertHtml();
        return;
    }

    // Nœuds et rôles
    const roles = {};   // slug -> {in:0, out:0}
    depsData.forEach(dep => {
        if (dep.source === dep.target) return;
        roles[dep.source] = roles[dep.source] || { in: 0, out: 0 };
        roles[dep.target] = roles[dep.target] || { in: 0, out: 0 };
        roles[dep.source].out++;
        roles[dep.target].in++;
    });

    const consumers = [], mixed = [], providers = [];
    Object.keys(roles).sort().forEach(slug => {
        const r = roles[slug];
        if (r.in > 0 && r.out > 0) mixed.push(slug);
        else if (r.out > 0) consumers.push(slug);
        else providers.push(slug);
    });

    const columns = [
        { title: 'Apps appelantes', slugs: consumers },
        { title: 'Apps mixtes', slugs: mixed },
        { title: 'Services appelés', slugs: providers }
    ].filter(col => col.slugs.length);

    // Réordonne chaque colonne (barycentre) pour minimiser les croisements
    depsComputeOrderedColumns(columns).forEach((slugs, ci) => { columns[ci].slugs = slugs; });

    const maxRows = Math.max(...columns.map(c => c.slugs.length), 1);
    const graphW = DEPS_MARGIN * 2 + columns.length * DEPS_NODE_W + (columns.length - 1) * (DEPS_COL_STEP - DEPS_NODE_W);
    const graphH = DEPS_MARGIN * 2 + maxRows * DEPS_ROW_H;

    // Position des nœuds
    const pos = {};
    columns.forEach((col, ci) => {
        col.slugs.forEach((slug, ri) => {
            pos[slug] = {
                x: DEPS_MARGIN + ci * DEPS_COL_STEP,
                y: DEPS_MARGIN + DEPS_ROW_H * ri,
                col: ci
            };
        });
    });

    // Position de départ des colonnes pour les flèches (entrée/sortie de nœud)
    const colLeft = ci => DEPS_MARGIN + ci * DEPS_COL_STEP;

    // Ancrages étalés : chaque arête a son propre point d'entrée/sortie
    const { anchorOut, anchorIn } = depsComputeAnchors(pos);

    let edgesSvg = '';
    depsData.forEach((dep, i) => {
        if (dep.source === dep.target) return;
        const from = pos[dep.source], to = pos[dep.target];
        if (!from || !to) return;
        const fresh = depsEdgeFreshness(dep);
        const color = DEPS_EDGE_COLORS[fresh];
        const unknown = depsNodeStatus(dep.target) === 'unknown';
        const dimmed = depsSelectedNode && dep.source !== depsSelectedNode && dep.target !== depsSelectedNode;
        const sx = colLeft(from.col) + DEPS_NODE_W, sy = anchorOut[dep.source].get(i);
        const tx = colLeft(to.col), ty = anchorIn[dep.target].get(i);
        const mx = (sx + tx) / 2;
        edgesSvg += `
            <path d="M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}"
                  fill="none" stroke="${color}" stroke-width="${dimmed ? 1 : depsEdgeWeight(fresh)}"
                  opacity="${depsEdgeOpacity(fresh, dimmed)}" ${unknown ? 'stroke-dasharray="6 4"' : ''}
                  marker-end="url(#depsArrow)" data-deps-edge="${i}" style="cursor:help"/>`;
    });

    let nodesSvg = '';
    Object.keys(pos).forEach(slug => {
        const p = pos[slug];
        const status = depsNodeStatus(slug);
        const app = depsAppsIndex[slug];
        const statusColor = status === 'active' ? '#198754' : status === 'inactive' ? '#6c757d' : '#dc3545';
        const selected = depsSelectedNode === slug;
        const dimmed = depsSelectedNode && !selected &&
            !depsData.some(d => (d.source === depsSelectedNode && d.target === slug) || (d.target === depsSelectedNode && d.source === slug));
        const title = app?.name ? escapeHtml(app.name) : 'Hors registre';
        nodesSvg += `
            <g data-deps-node="${escapeHtml(slug)}" style="cursor:pointer" opacity="${dimmed ? 0.3 : 1}">
                <rect x="${p.x}" y="${p.y}" width="${DEPS_NODE_W}" height="${DEPS_NODE_H}" rx="6"
                      fill="#ffffff" stroke="${selected ? '#0d6efd' : '#ced4da'}" stroke-width="${selected ? 2 : 1}"/>
                <rect x="${p.x}" y="${p.y}" width="5" height="${DEPS_NODE_H}" rx="2.5" fill="${statusColor}"/>
                <circle cx="${p.x + 16}" cy="${p.y + DEPS_NODE_H / 2}" r="4" fill="${statusColor}"/>
                <text x="${p.x + 26}" y="${p.y + DEPS_NODE_H / 2 + 4}"
                      font-size="12" font-family="system-ui, sans-serif" fill="#212529">${escapeHtml(depsTruncate(slug, 18))}<title>${escapeHtml(slug)} — ${title}</title></text>
            </g>`;
    });

    // En-têtes de colonnes
    let headersSvg = '';
    columns.forEach((col, ci) => {
        headersSvg += `
            <text x="${colLeft(ci) + DEPS_NODE_W / 2}" y="${DEPS_MARGIN - 8}" text-anchor="middle"
                  font-size="11" font-weight="600" fill="#6c757d" font-family="system-ui, sans-serif">${escapeHtml(col.title)}</text>`;
    });

    container.innerHTML = `
        <svg viewBox="0 0 ${graphW} ${graphH}" style="width:100%;height:auto;min-height:${Math.min(graphH, 420)}px;display:block">
            <defs>
                <marker id="depsArrow" viewBox="0 0 10 10" refX="9" refY="5"
                        markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#6c757d"/>
                </marker>
            </defs>
            ${headersSvg}
            ${edgesSvg}
            ${nodesSvg}
        </svg>`;

    depsBindGraphEvents(container);
}

/**
 * Vue matrice d'adjacence : lignes = apps sources, colonnes = cibles,
 * case colorée selon la fraîcheur. Réutilise depsTooltip via
 * data-deps-edge (le listener du wrapper fait le reste) — zéro croissement
 * par construction, lecture « santé globale ».
 */
function renderDependenciesMatrix() {
    const container = document.getElementById('depsGraphContainer');
    if (!container) return;

    if (!depsData.length) {
        container.innerHTML = depsEmptyAlertHtml();
        return;
    }

    const indexOfDep = new Map(depsData.map((dep, i) => [dep, i]));
    const map = new Map(depsData.map(dep => [dep.source + '|' + dep.target, dep]));
    const sources = [...new Set(depsData.filter(d => d.source !== d.target).map(d => d.source))].sort();
    const targets = [...new Set(depsData.filter(d => d.source !== d.target).map(d => d.target))].sort();

    const headCells = targets.map(t => `
        <th class="px-0" title="${escapeHtml(t)}">
            <div style="writing-mode:vertical-rl;transform:rotate(180deg);font-size:.6rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#495057;height:96px;white-space:nowrap;margin:0 auto">${escapeHtml(t)}</div>
        </th>`).join('');

    const bodyRows = sources.map(src => {
        const cells = targets.map(tgt => {
            const dep = map.get(src + '|' + tgt);
            if (!dep) {
                return `<td class="px-1 py-1"><div style="width:15px;height:15px;border-radius:3px;background:#e9ecef"></div></td>`;
            }
            const fresh = depsEdgeFreshness(dep);
            const unknown = depsNodeStatus(dep.target) === 'unknown';
            const label = fresh === 'fresh' ? `synchro ≤ ${DEPS_FRESH_DAYS} j`
                : fresh === 'stale' ? `${DEPS_FRESH_DAYS}–${DEPS_STALE_DAYS} j`
                : `> ${DEPS_STALE_DAYS} j ou jamais`;
            const tip = `${escapeHtml(src)} → ${escapeHtml(tgt)} — ${label}`
                + (unknown ? ' · cible hors registre' : '')
                + (dep.note ? ' · ' + escapeHtml(dep.note) : '');
            return `<td class="px-1 py-1"><div data-deps-edge="${indexOfDep.get(dep)}" title="${tip}" style="width:15px;height:15px;border-radius:3px;background:${DEPS_EDGE_COLORS[fresh]};${unknown ? 'outline:1.5px dashed #dc3545;outline-offset:1px;' : ''}cursor:help"></div></td>`;
        }).join('');
        return `<tr><td class="pe-2 text-end" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.7rem;white-space:nowrap">${escapeHtml(src)}</td>${cells}</tr>`;
    }).join('');

    container.innerHTML = `
        <div style="overflow-x:auto">
            <table class="table table-sm mb-0" style="border-collapse:collapse">
                <thead>
                    <tr><th class="pe-2 text-end" style="font-size:.6rem;color:#6c757d;vertical-align:bottom">src ↓ · cible →</th>${headCells}</tr>
                </thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>`;
}

function renderDependenciesTable() {
    const el = document.getElementById('depsTable');
    if (!el) return;
    depsBindTableEvents();
    if (!depsData.length) {
        el.innerHTML = '';
        return;
    }
    const rows = [...depsData].sort((a, b) => (a.source + a.target).localeCompare(b.source + b.target));
    const rowsHtml = rows.map(dep => {
        const fresh = depsEdgeFreshness(dep);
        const badge = fresh === 'fresh' ? 'bg-success' : fresh === 'stale' ? 'bg-warning text-dark' : 'bg-danger';
        const label = fresh === 'fresh' ? 'Récent' : fresh === 'stale' ? 'À resynchroniser' : 'Jamais / périmé';
        const targetStatus = DEPS_STATUS_LABELS[depsNodeStatus(dep.target)];
        const note = dep.note ? escapeHtml(dep.note) : '<span class="text-muted">—</span>';
        return `
            <tr data-deps-row="${escapeHtml(dep.source)}|${escapeHtml(dep.target)}" style="cursor:pointer">
                <td><code>${escapeHtml(dep.source)}</code></td>
                <td><i class="bi bi-arrow-right text-muted mx-1"></i><code>${escapeHtml(dep.target)}</code> ${targetStatus}</td>
                <td><span class="badge ${badge}">${label}</span></td>
                <td class="small text-muted">${depsFmtDate(dep.last_sync_at)}</td>
                <td class="small">${note}</td>
            </tr>`;
    }).join('');

    el.innerHTML = `
        <div class="table-responsive">
            <table class="table table-hover align-middle mb-0">
                <thead>
                    <tr><th>App source</th><th>Dépend de</th><th>Fraîcheur</th><th>Dernière synchro</th><th>Note</th></tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
            </table>
        </div>`;
}

// ===== Interactions (délégation, pas de timer) =====

function depsBindGraphEvents(container) {
    const wrapper = document.getElementById('depsGraphWrapper');
    if (!wrapper) return;

    if (!depsListenersBound) {
        depsListenersBound = true;

        // Tooltip sur les arêtes et nœuds
        wrapper.addEventListener('mousemove', (e) => {
            const tip = document.getElementById('depsTooltip');
            if (!tip) return;
            const edgeEl = e.target.closest('[data-deps-edge]');
            const nodeEl = e.target.closest('[data-deps-node]');
            let html = '';
            if (edgeEl) {
                const dep = depsData[parseInt(edgeEl.getAttribute('data-deps-edge'), 10)];
                if (dep) {
                    const srcApp = depsAppsIndex[dep.source];
                    const tgtApp = depsAppsIndex[dep.target];
                    html = `<strong>${escapeHtml(dep.source)}</strong> → <strong>${escapeHtml(dep.target)}</strong><br>
                            ${srcApp?.name ? escapeHtml(srcApp.name) + '<br>' : ''}${tgtApp?.name ? escapeHtml(tgtApp.name) + '<br>' : ''}
                            <span class="opacity-75">Dernière synchro : ${depsFmtDate(dep.last_sync_at)}</span><br>
                            <span class="opacity-75">Déclarée : ${depsFmtDate(dep.declared_at)}</span>
                            ${dep.note ? '<hr class="my-1"><span>' + escapeHtml(dep.note) + '</span>' : ''}`;
                }
            } else if (nodeEl) {
                const slug = nodeEl.getAttribute('data-deps-node');
                const app = depsAppsIndex[slug];
                const usedBy = depsData.filter(d => d.target === slug).length;
                const uses = depsData.filter(d => d.source === slug).length;
                html = `<strong>${escapeHtml(slug)}</strong>${app?.name ? ' — ' + escapeHtml(app.name) : ''}<br>
                        ${DEPS_STATUS_LABELS[depsNodeStatus(slug)]}<br>
                        <span class="opacity-75">Appelée par ${usedBy} app${usedBy > 1 ? 's' : ''} · appelle ${uses} service${uses > 1 ? 's' : ''}</span>`;
            }
            if (html) {
                tip.innerHTML = html;
                tip.classList.remove('d-none');
                const rect = wrapper.getBoundingClientRect();
                let x = e.clientX - rect.left + 14;
                let y = e.clientY - rect.top + 14;
                const tw = tip.offsetWidth, th = tip.offsetHeight;
                if (x + tw > rect.width) x = e.clientX - rect.left - tw - 10;
                if (y + th > rect.height) y = e.clientY - rect.top - th - 10;
                tip.style.left = x + 'px';
                tip.style.top = y + 'px';
            } else {
                tip.classList.add('d-none');
            }
        });
        wrapper.addEventListener('mouseleave', () => {
            document.getElementById('depsTooltip')?.classList.add('d-none');
        });

        // Sélection d'un nœud → mise en avant de ses liens
        container.addEventListener('click', (e) => {
            const nodeEl = e.target.closest('[data-deps-node]');
            if (!nodeEl) return;
            const slug = nodeEl.getAttribute('data-deps-node');
            depsSelectedNode = depsSelectedNode === slug ? null : slug;
            depsRenderMainView();
        });
    }
}

// Sélection depuis le tableau → même mise en avant dans le graphe
function depsBindTableEvents() {
    const el = document.getElementById('depsTable');
    if (!el || el.dataset.depsBound) return;
    el.dataset.depsBound = '1';
    el.addEventListener('click', (e) => {
        const row = e.target.closest('[data-deps-row]');
        if (!row) return;
        const [sourceSlug] = row.getAttribute('data-deps-row').split('|');
        depsSelectedNode = depsSelectedNode === sourceSlug ? null : sourceSlug;
        depsRenderMainView();
    });
}

// Expose pour data-action / init.js
window.depsRefresh = depsRefresh;
window.loadDependenciesGraph = loadDependenciesGraph;
