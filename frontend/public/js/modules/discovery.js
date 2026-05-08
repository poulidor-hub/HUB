/**
 * Hub Application - discovery OpenAPI
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// =============================================================================
// DISCOVERY — Explorateur d'APIs (nouvelle version)
// =============================================================================

// État global Discovery
let discoveryApps = [];                // Toutes les apps chargées
let discoverySelectedApp = null;       // App actuellement sélectionnée {slug, name, ...}
let discoveryOpenApiSpec = null;       // Spec OpenAPI de l'app sélectionnée
let discoverySelectedEndpoint = null;  // Endpoint sélectionné depuis la spec
let discoveryRawData = null;           // Données brutes de la dernière réponse (pour filtrage)
let discoveryVisibleColumns = null;    // Set de colonnes visibles (null = toutes)
let discoveryHasOpenApi = false;       // L'app sélectionnée a-t-elle une spec OpenAPI ?
let discoveryMode = 'requester';       // Mode actuel : 'requester' ou 'm2m'
let discoveryM2MMarkdown = null;       // Markdown M2M mis en cache pour l'app sélectionnée

/**
 * Charge la page Discovery — liste les apps dans la sidebar
 */
async function loadDiscoveryPage() {
    try {
        const result = await api.getDiscoveryApps();
        discoveryApps = result.apps || [];
        document.getElementById('discoveryAppCount').textContent = result.total || 0;
        renderDiscoveryAppList(discoveryApps);

        // Restaurer la dernière app sélectionnée
        const lastSlug = sessionStorage.getItem('discovery_last_app');
        if (lastSlug) {
            const app = discoveryApps.find(a => a.slug === lastSlug);
            if (app) selectDiscoveryApp(app.slug);
        }
    } catch (error) {
        console.error('Erreur chargement Discovery:', error);
        showToast('Erreur', 'Impossible de charger les applications', 'danger');
    }
}

/**
 * Affiche la liste des apps dans la sidebar
 */
function renderDiscoveryAppList(apps) {
    const container = document.getElementById('discoveryAppList');

    if (!apps.length) {
        container.innerHTML = '<div class="text-center text-muted py-4"><i class="bi bi-inbox fs-3"></i><p class="mt-2 small">Aucune application active</p></div>';
        return;
    }

    container.innerHTML = apps.map(app => `
        <div class="discovery-app-item ${discoverySelectedApp?.slug === app.slug ? 'active' : ''}"
             onclick="selectDiscoveryApp('${escapeHtml(app.slug)}')" data-slug="${escapeHtml(app.slug)}">
            <div class="d-flex align-items-center gap-2">
                <i class="bi ${escapeHtml(app.icon || 'bi-app')}" style="color: ${escapeHtml(app.color || '#6c757d')}; font-size: 1.1rem;"></i>
                <div class="flex-grow-1 overflow-hidden">
                    <div class="fw-semibold text-truncate" style="font-size: 0.85rem;">${escapeHtml(app.name)}</div>
                    <div class="text-muted text-truncate" style="font-size: 0.72rem;">${escapeHtml(app.slug)}</div>
                </div>
                ${app.is_shared_service ? '<span class="badge bg-info-subtle text-info-emphasis" style="font-size: 0.65rem;">shared</span>' : ''}
            </div>
        </div>
    `).join('');
}

/**
 * Filtre la liste des apps dans la sidebar
 */
function filterDiscoveryApps() {
    const q = document.getElementById('discoveryAppSearch').value.toLowerCase().trim();
    const filtered = q
        ? discoveryApps.filter(a => a.name.toLowerCase().includes(q) || a.slug.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q))
        : discoveryApps;
    renderDiscoveryAppList(filtered);
}

/**
 * Sélectionne une app dans la sidebar et charge sa spec OpenAPI
 */
async function selectDiscoveryApp(slug) {
    const app = discoveryApps.find(a => a.slug === slug);
    if (!app) return;

    discoverySelectedApp = app;
    sessionStorage.setItem('discovery_last_app', slug);

    // Mettre à jour la sidebar
    document.querySelectorAll('.discovery-app-item').forEach(el => {
        el.classList.toggle('active', el.dataset.slug === slug);
    });

    // Mettre à jour l'en-tête
    document.getElementById('discoveryAppTitle').textContent = app.name;
    document.getElementById('discoveryAppDesc').textContent = app.description || '';

    // Afficher le toggle mode et revenir au requêteur
    document.getElementById('discoveryModeToggle').style.display = '';
    discoveryM2MMarkdown = null;
    switchDiscoveryMode('requester');

    // Réinitialiser
    discoveryOpenApiSpec = null;
    discoverySelectedEndpoint = null;
    discoveryHasOpenApi = false;
    clearDiscoveryResult();

    const endpointSelect = document.getElementById('discoveryEndpointSelect');
    endpointSelect.innerHTML = '<option value="">⏳ Chargement des endpoints…</option>';
    endpointSelect.disabled = true;
    document.getElementById('discoveryEndpointInfo').classList.add('d-none');
    document.getElementById('discoveryParamsSection').classList.add('d-none');
    document.getElementById('discoveryBodySection').classList.add('d-none');
    document.getElementById('discoveryPath').value = '/';
    document.getElementById('discoveryMethod').value = 'GET';

    // Charger la spec OpenAPI via le proxy backend
    try {
        const result = await api.getAppOpenApi(slug);

        if (result.spec && result.spec.paths) {
            discoveryOpenApiSpec = result.spec;
            discoveryHasOpenApi = true;
            populateDiscoveryEndpointSelect(result.spec);
            document.getElementById('discoveryEndpointRow').classList.remove('d-none');
            document.getElementById('discoveryQuerySection').classList.add('d-none');
        } else {
            // Pas de spec OpenAPI — mode manuel
            discoveryHasOpenApi = false;
            endpointSelect.innerHTML = '<option value="">Pas de spec OpenAPI — mode manuel</option>';
            endpointSelect.disabled = true;
            document.getElementById('discoveryQuerySection').classList.remove('d-none');
            showToast('Info', `${app.name} n'a pas de spécification OpenAPI — mode manuel activé`, 'info');
        }
    } catch (error) {
        console.error('Erreur chargement OpenAPI:', error);
        endpointSelect.innerHTML = '<option value="">❌ Erreur de chargement</option>';
        document.getElementById('discoveryQuerySection').classList.remove('d-none');
    }
}

/**
 * Remplit le sélecteur d'endpoints depuis la spec OpenAPI
 */
function populateDiscoveryEndpointSelect(spec) {
    const select = document.getElementById('discoveryEndpointSelect');
    select.innerHTML = '<option value="">— Sélectionner un endpoint —</option>';

    const endpointsByTag = {};

    for (const [path, methods] of Object.entries(spec.paths || {})) {
        for (const [method, details] of Object.entries(methods)) {
            if (!['get', 'post', 'put', 'patch', 'delete'].includes(method.toLowerCase())) continue;
            const tag = (details.tags || ['Autre'])[0];
            if (!endpointsByTag[tag]) endpointsByTag[tag] = [];

            endpointsByTag[tag].push({
                method: method.toUpperCase(),
                path,
                summary: details.summary || '',
                description: details.description || '',
                operationId: details.operationId || '',
                parameters: details.parameters || [],
                requestBody: details.requestBody || null,
                responses: details.responses || {},
                tags: details.tags || ['Autre'],
            });
        }
    }

    for (const [tag, endpoints] of Object.entries(endpointsByTag)) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📁 ${tag}`;
        endpoints.forEach(ep => {
            const opt = document.createElement('option');
            opt.value = JSON.stringify(ep);
            opt.textContent = `[${ep.method}] ${ep.path} — ${ep.summary || ep.operationId || 'Sans description'}`;
            optgroup.appendChild(opt);
        });
        select.appendChild(optgroup);
    }

    select.disabled = false;
    const total = Object.values(endpointsByTag).flat().length;
    showToast('Endpoints chargés', `${total} endpoint(s) disponible(s)`, 'success');
}

/**
 * Appelé quand on sélectionne un endpoint dans le dropdown
 */
function onDiscoveryEndpointChange() {
    const select = document.getElementById('discoveryEndpointSelect');
    const val = select.value;

    if (!val) {
        document.getElementById('discoveryEndpointInfo').classList.add('d-none');
        document.getElementById('discoveryParamsSection').classList.add('d-none');
        document.getElementById('discoveryBodySection').classList.add('d-none');
        discoverySelectedEndpoint = null;
        return;
    }

    try {
        const ep = JSON.parse(val);
        discoverySelectedEndpoint = ep;

        // Badge méthode
        const methodBadge = document.getElementById('discoveryEndpointMethod');
        const colors = { GET: 'bg-success', POST: 'bg-primary', PUT: 'bg-warning text-dark', PATCH: 'bg-info', DELETE: 'bg-danger' };
        methodBadge.className = `badge ${colors[ep.method] || 'bg-secondary'}`;
        methodBadge.textContent = ep.method;
        document.getElementById('discoveryEndpointPath').textContent = ep.path;
        document.getElementById('discoveryEndpointDescription').textContent = ep.description || ep.summary || '';
        document.getElementById('discoveryEndpointInfo').classList.remove('d-none');

        // Remplir les champs de requête
        document.getElementById('discoveryMethod').value = ep.method;
        document.getElementById('discoveryPath').value = ep.path;

        // Paramètres
        generateDiscoveryParams(ep);

        // Body
        if (['POST', 'PUT', 'PATCH'].includes(ep.method) && ep.requestBody) {
            document.getElementById('discoveryBodySection').classList.remove('d-none');
            generateDiscoveryBodyExample(ep.requestBody);
        } else {
            document.getElementById('discoveryBodySection').classList.add('d-none');
            document.getElementById('discoveryBody').value = '';
        }
    } catch (e) {
        console.error('Erreur parsing endpoint:', e);
    }
}

/**
 * Appelé quand l'utilisateur tape manuellement un path
 */
function onDiscoveryPathInput() {
    // Désélectionner l'endpoint du dropdown si l'utilisateur modifie le path
    const select = document.getElementById('discoveryEndpointSelect');
    if (select.value) {
        // Vérifier si le path correspond encore
        try {
            const ep = JSON.parse(select.value);
            if (document.getElementById('discoveryPath').value.trim() !== ep.path) {
                select.value = '';
                discoverySelectedEndpoint = null;
                document.getElementById('discoveryEndpointInfo').classList.add('d-none');
            }
        } catch (e) { /* ignore */ }
    }
}

/**
 * Génère les champs de paramètres pour un endpoint
 */
function generateDiscoveryParams(endpoint) {
    const section = document.getElementById('discoveryParamsSection');
    const container = document.getElementById('discoveryParams');
    const params = endpoint.parameters || [];

    if (!params.length) {
        section.classList.add('d-none');
        return;
    }

    section.classList.remove('d-none');
    container.innerHTML = '';

    params.forEach(param => {
        const typeIcon = { path: 'bi-link-45deg', query: 'bi-question-circle', header: 'bi-card-heading' }[param.in] || 'bi-tag';
        const row = document.createElement('div');
        row.className = 'row g-2 mb-1 align-items-center';
        row.innerHTML = `
            <div class="col-md-3">
                <div class="input-group input-group-sm">
                    <span class="input-group-text"><i class="bi ${typeIcon}"></i></span>
                    <input type="text" class="form-control" value="${escapeHtml(param.name)}" readonly>
                </div>
            </div>
            <div class="col-md-5">
                <input type="text" class="form-control form-control-sm discovery-param-value"
                       data-param-name="${escapeHtml(param.name)}"
                       data-param-in="${param.in}"
                       placeholder="${escapeHtml(param.description || param.schema?.type || 'valeur')}"
                       ${param.required ? 'required' : ''}>
            </div>
            <div class="col-md-4">
                <small class="text-muted">
                    ${param.required ? '<span class="badge bg-danger me-1">Requis</span>' : ''}
                    <span class="badge bg-secondary">${param.in}</span>
                    ${param.schema?.type ? `<span class="badge bg-light text-dark">${param.schema.type}</span>` : ''}
                </small>
            </div>
        `;
        container.appendChild(row);
    });

    // Mettre à jour le path en temps réel
    container.querySelectorAll('.discovery-param-value').forEach(input => {
        input.addEventListener('input', updateDiscoveryPathWithParams);
    });
}

/**
 * Met à jour le path avec les paramètres remplis
 */
function updateDiscoveryPathWithParams() {
    if (!discoverySelectedEndpoint) return;

    let path = discoverySelectedEndpoint.path;
    const queryParams = [];

    document.querySelectorAll('.discovery-param-value').forEach(input => {
        const name = input.dataset.paramName;
        const loc = input.dataset.paramIn;
        const val = input.value.trim();
        if (!val) return;
        if (loc === 'path') path = path.replace(`{${name}}`, encodeURIComponent(val));
        else if (loc === 'query') queryParams.push(`${encodeURIComponent(name)}=${encodeURIComponent(val)}`);
    });

    let fullPath = path;
    if (queryParams.length) fullPath += '?' + queryParams.join('&');
    document.getElementById('discoveryPath').value = fullPath;
}

/**
 * Génère un exemple de body depuis la spec OpenAPI
 */
function generateDiscoveryBodyExample(requestBody) {
    const textarea = document.getElementById('discoveryBody');
    try {
        const jsonContent = requestBody?.content?.['application/json'];
        if (jsonContent?.schema) {
            textarea.value = JSON.stringify(generateSchemaExample(jsonContent.schema), null, 2);
        } else if (jsonContent?.example) {
            textarea.value = JSON.stringify(jsonContent.example, null, 2);
        }
    } catch (e) {
        console.warn('Impossible de générer exemple body:', e);
    }
}

/**
 * Génère un exemple à partir d'un schéma JSON (récursif)
 */
function generateSchemaExample(schema, depth = 0) {
    if (depth > 5) return null;
    if (schema.example !== undefined) return schema.example;
    if (schema.$ref) return { '_ref': schema.$ref.split('/').pop() };

    switch (schema.type) {
        case 'object': {
            const obj = {};
            for (const [k, v] of Object.entries(schema.properties || {})) obj[k] = generateSchemaExample(v, depth + 1);
            return obj;
        }
        case 'array':
            return schema.items ? [generateSchemaExample(schema.items, depth + 1)] : [];
        case 'string':
            if (schema.enum) return schema.enum[0];
            if (schema.format === 'date') return '2024-01-01';
            if (schema.format === 'date-time') return '2024-01-01T00:00:00Z';
            if (schema.format === 'email') return 'user@example.com';
            return schema.default || 'string';
        case 'integer':
        case 'number':
            return schema.default || schema.minimum || 0;
        case 'boolean':
            return schema.default !== undefined ? schema.default : true;
        default:
            return null;
    }
}

/**
 * Ajoute une ligne de query param manuel
 */
function addDiscoveryQueryParam() {
    const container = document.getElementById('discoveryQueryParams');
    const row = document.createElement('div');
    row.className = 'row g-1 mb-1 discovery-qp-row';
    row.innerHTML = `
        <div class="col-5">
            <input type="text" class="form-control form-control-sm" placeholder="clé">
        </div>
        <div class="col-6">
            <input type="text" class="form-control form-control-sm" placeholder="valeur">
        </div>
        <div class="col-1">
            <button class="btn btn-outline-danger btn-sm py-0" onclick="this.closest('.discovery-qp-row').remove()">
                <i class="bi bi-x"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
}

// ─── Exécution de la requête ─────────────────────────────────────────

/**
 * Exécute la requête via le proxy backend
 */
async function executeDiscoveryRequest() {
    if (!discoverySelectedApp) {
        showToast('Erreur', 'Sélectionnez d\'abord une application', 'warning');
        return;
    }

    const method = document.getElementById('discoveryMethod').value;
    let path = document.getElementById('discoveryPath').value.trim();

    if (!path) {
        showToast('Erreur', 'Chemin de l\'endpoint requis', 'warning');
        return;
    }

    // Collecter les query params manuels (mode fallback)
    let queryParams = null;
    const qpRows = document.querySelectorAll('.discovery-qp-row');
    if (qpRows.length > 0) {
        queryParams = {};
        qpRows.forEach(row => {
            const inputs = row.querySelectorAll('input');
            const k = inputs[0]?.value.trim();
            const v = inputs[1]?.value.trim();
            if (k) queryParams[k] = v;
        });
        if (Object.keys(queryParams).length === 0) queryParams = null;
    }

    // Body
    let body = null;
    if (['POST', 'PUT', 'PATCH'].includes(method)) {
        const bodyText = document.getElementById('discoveryBody').value.trim();
        if (bodyText) {
            try {
                body = JSON.parse(bodyText);
            } catch (e) {
                showToast('Erreur', 'Corps JSON invalide', 'danger');
                return;
            }
        }
    }

    const btn = document.getElementById('discoveryExecBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>…';

    try {
        const result = await api.proxyRequest(
            discoverySelectedApp.slug,
            method,
            path,
            queryParams,
            null, // headers gérés par le proxy
            body
        );

        renderDiscoveryResults(result);

    } catch (error) {
        console.error('Erreur proxy:', error);
        showToast('Erreur', error.message, 'danger');

        // Afficher l'erreur dans la zone résultats
        document.getElementById('discoveryResultsArea').style.display = '';
        document.getElementById('discoveryResultStatus').textContent = 'Erreur';
        document.getElementById('discoveryResultStatus').className = 'badge bg-danger';
        document.getElementById('discoveryResultTime').textContent = '';
        document.getElementById('discoveryResultCount').textContent = '';
        document.getElementById('discoveryTableWrapper').classList.add('d-none');
        const raw = document.getElementById('discoveryResultRaw');
        raw.classList.remove('d-none');
        raw.textContent = `Erreur: ${error.message}`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Exécuter';
    }
}

// ─── Rendu des résultats ─────────────────────────────────────────────

/**
 * Affiche les résultats dans la zone dédiée
 */
function renderDiscoveryResults(result) {
    const area = document.getElementById('discoveryResultsArea');
    area.style.display = '';

    // Status badge
    const statusBadge = document.getElementById('discoveryResultStatus');
    statusBadge.textContent = `${result.status} ${result.status_text || ''}`.trim();
    statusBadge.className = `badge ${result.status >= 200 && result.status < 300 ? 'bg-success' : result.status >= 400 ? 'bg-danger' : 'bg-warning text-dark'}`;

    document.getElementById('discoveryResultTime').textContent = `${result.elapsed_ms}ms`;

    // Erreur de connexion ?
    if (result.error) {
        document.getElementById('discoveryResultCount').textContent = '';
        document.getElementById('discoveryTableWrapper').classList.add('d-none');
        const raw = document.getElementById('discoveryResultRaw');
        raw.classList.remove('d-none');
        raw.textContent = `Erreur: ${result.error}`;
        discoveryRawData = null;
        return;
    }

    // Déterminer les données
    const data = result.body != null ? result.body : result.body_text;
    discoveryRawData = data;
    discoveryVisibleColumns = null;

    // Essayer le rendu tableau
    if (data && typeof data === 'object') {
        // Si c'est un tableau d'objets → tableau multi-lignes
        let rows = null;
        if (Array.isArray(data)) {
            rows = data;
        } else if (Array.isArray(data.items)) {
            rows = data.items;
        } else if (Array.isArray(data.data)) {
            rows = data.data;
        } else if (Array.isArray(data.results)) {
            rows = data.results;
        }

        if (rows && rows.length > 0 && typeof rows[0] === 'object' && !Array.isArray(rows[0])) {
            renderDiscoveryTable(rows);
            return;
        }

        // Objet unique → tableau clé/valeur
        if (!Array.isArray(data)) {
            renderDiscoveryObjectTable(data);
            return;
        }
    }

    // Fallback : JSON brut
    document.getElementById('discoveryResultCount').textContent = '';
    document.getElementById('discoveryTableWrapper').classList.add('d-none');
    const raw = document.getElementById('discoveryResultRaw');
    raw.classList.remove('d-none');
    raw.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

/**
 * Rend un tableau multi-lignes (array d'objets)
 */
function renderDiscoveryTable(rows) {
    document.getElementById('discoveryTableWrapper').classList.remove('d-none');
    document.getElementById('discoveryResultRaw').classList.add('d-none');
    document.getElementById('discoveryResultCount').textContent = `${rows.length} résultat(s)`;

    // Collecter toutes les clés
    const allKeys = new Set();
    rows.forEach(row => {
        if (row && typeof row === 'object') Object.keys(row).forEach(k => allKeys.add(k));
    });
    const columns = Array.from(allKeys);

    // Initialiser les colonnes visibles (toutes par défaut)
    if (!discoveryVisibleColumns) {
        discoveryVisibleColumns = new Set(columns);
    }

    // Peupler le sélecteur de colonnes
    populateDiscoveryColumnSelector(columns);

    // Peupler le filtre par champ
    const fieldSelect = document.getElementById('discoveryFilterField');
    const prevField = fieldSelect.value;
    fieldSelect.innerHTML = '<option value="">Champ</option>';
    columns.forEach(col => {
        const opt = document.createElement('option');
        opt.value = col;
        opt.textContent = col;
        if (col === prevField) opt.selected = true;
        fieldSelect.appendChild(opt);
    });

    // Détecter les types par colonne
    const colTypes = {};
    columns.forEach(col => {
        colTypes[col] = detectDiscoveryFieldType(rows.map(r => r?.[col]));
    });

    // Construire le thead
    const thead = document.getElementById('discoveryTableHead');
    thead.innerHTML = '';
    const headerRow = document.createElement('tr');
    columns.forEach(col => {
        if (!discoveryVisibleColumns.has(col)) return;
        const th = document.createElement('th');
        th.className = 'discovery-th';
        const typeColors = { string: 'text-bg-secondary', number: 'text-bg-primary', boolean: 'text-bg-info', object: 'text-bg-warning', array: 'text-bg-warning', null: 'text-bg-light' };
        th.innerHTML = `${escapeHtml(col)} <span class="badge ${typeColors[colTypes[col]] || 'text-bg-light'}" style="font-size: 0.6rem;">${colTypes[col]}</span>`;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    // Construire le tbody
    const tbody = document.getElementById('discoveryTableBody');
    tbody.innerHTML = '';

    // Appliquer les filtres
    let filtered = applyDiscoveryDataFilters(rows, columns);
    document.getElementById('discoveryResultCount').textContent = `${filtered.length}/${rows.length} résultat(s)`;

    filtered.forEach(row => {
        const tr = document.createElement('tr');
        columns.forEach(col => {
            if (!discoveryVisibleColumns.has(col)) return;
            const td = document.createElement('td');
            td.className = 'discovery-td';
            const val = row?.[col];
            td.innerHTML = formatDiscoveryCellValue(val);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
}

/**
 * Rend un objet unique sous forme clé/valeur
 */
function renderDiscoveryObjectTable(obj) {
    document.getElementById('discoveryTableWrapper').classList.remove('d-none');
    document.getElementById('discoveryResultRaw').classList.add('d-none');
    document.getElementById('discoveryResultCount').textContent = `${Object.keys(obj).length} champ(s)`;

    // Pas de sélecteur de colonnes pour un objet
    document.getElementById('discoveryColumnSelector').innerHTML = '';

    const thead = document.getElementById('discoveryTableHead');
    thead.innerHTML = '<tr><th class="discovery-th">Champ</th><th class="discovery-th">Type</th><th class="discovery-th">Valeur</th></tr>';

    const tbody = document.getElementById('discoveryTableBody');
    tbody.innerHTML = '';

    for (const [key, val] of Object.entries(obj)) {
        const tr = document.createElement('tr');
        const typeName = val === null ? 'null' : Array.isArray(val) ? 'array' : typeof val;
        const typeColors = { string: 'text-bg-secondary', number: 'text-bg-primary', boolean: 'text-bg-info', object: 'text-bg-warning', array: 'text-bg-warning', null: 'text-bg-light' };
        tr.innerHTML = `
            <td class="discovery-td fw-semibold">${escapeHtml(key)}</td>
            <td class="discovery-td"><span class="badge ${typeColors[typeName] || 'text-bg-light'}" style="font-size: 0.65rem;">${typeName}</span></td>
            <td class="discovery-td">${formatDiscoveryCellValue(val)}</td>
        `;
        tbody.appendChild(tr);
    }
}

/**
 * Formate une valeur pour l'affichage dans une cellule
 */
function formatDiscoveryCellValue(val) {
    if (val === null || val === undefined) return '<span class="text-muted fst-italic">null</span>';
    if (typeof val === 'boolean') return `<span class="badge ${val ? 'text-bg-success' : 'text-bg-secondary'}">${val}</span>`;
    if (typeof val === 'number') return `<span class="text-primary">${val}</span>`;
    if (typeof val === 'object') {
        const json = JSON.stringify(val);
        if (json.length > 80) return `<code class="small" title="${escapeHtml(json)}">${escapeHtml(json.substring(0, 77))}…</code>`;
        return `<code class="small">${escapeHtml(json)}</code>`;
    }
    // String
    const str = String(val);
    if (str.length > 100) return `<span title="${escapeHtml(str)}">${escapeHtml(str.substring(0, 97))}…</span>`;
    return escapeHtml(str);
}

/**
 * Détecte le type dominant d'une colonne
 */
function detectDiscoveryFieldType(values) {
    const types = {};
    for (const v of values) {
        if (v === null || v === undefined) { types['null'] = (types['null'] || 0) + 1; continue; }
        if (Array.isArray(v)) { types['array'] = (types['array'] || 0) + 1; continue; }
        const t = typeof v;
        types[t] = (types[t] || 0) + 1;
    }
    // Retourner le type le plus fréquent (hors null)
    let best = 'null', bestCount = 0;
    for (const [t, c] of Object.entries(types)) {
        if (t !== 'null' && c > bestCount) { best = t; bestCount = c; }
    }
    return best;
}

// ─── Filtres ─────────────────────────────────────────────────────────

/**
 * Peuple le sélecteur de colonnes visibles
 */
function populateDiscoveryColumnSelector(columns) {
    const container = document.getElementById('discoveryColumnSelector');
    container.innerHTML = '';
    columns.forEach(col => {
        const div = document.createElement('div');
        div.className = 'form-check';
        div.innerHTML = `
            <input class="form-check-input" type="checkbox" id="disc_col_${escapeHtml(col)}"
                   value="${escapeHtml(col)}" ${discoveryVisibleColumns?.has(col) ? 'checked' : ''} onchange="toggleDiscoveryColumn('${escapeHtml(col)}')">
            <label class="form-check-label small" for="disc_col_${escapeHtml(col)}">${escapeHtml(col)}</label>
        `;
        container.appendChild(div);
    });
}

/**
 * Toggle la visibilité d'une colonne
 */
function toggleDiscoveryColumn(col) {
    if (!discoveryVisibleColumns) return;
    if (discoveryVisibleColumns.has(col)) {
        discoveryVisibleColumns.delete(col);
    } else {
        discoveryVisibleColumns.add(col);
    }
    applyDiscoveryFilters();
}

/**
 * Applique tous les filtres et re-rend le tableau
 */
function applyDiscoveryFilters() {
    if (!discoveryRawData) return;

    // Retrouver les rows
    let rows = null;
    if (Array.isArray(discoveryRawData)) {
        rows = discoveryRawData;
    } else if (typeof discoveryRawData === 'object') {
        rows = discoveryRawData.items || discoveryRawData.data || discoveryRawData.results;
    }

    if (rows && Array.isArray(rows) && rows.length > 0 && typeof rows[0] === 'object') {
        renderDiscoveryTable(rows);
    }
}

/**
 * Applique les filtres de données sur les rows
 */
function applyDiscoveryDataFilters(rows, columns) {
    let result = [...rows];

    // Filtre par champ
    const field = document.getElementById('discoveryFilterField')?.value;
    const op = document.getElementById('discoveryFilterOp')?.value;
    const val = document.getElementById('discoveryFilterValue')?.value?.trim();
    if (field && val) {
        result = result.filter(row => {
            const cellVal = String(row?.[field] ?? '').toLowerCase();
            const searchVal = val.toLowerCase();
            switch (op) {
                case 'eq': return cellVal === searchVal;
                case 'neq': return cellVal !== searchVal;
                case 'contains':
                default: return cellVal.includes(searchVal);
            }
        });
    }

    // Recherche texte globale
    const search = document.getElementById('discoveryFilterSearch')?.value?.trim().toLowerCase();
    if (search) {
        result = result.filter(row => {
            return columns.some(col => {
                const v = row?.[col];
                return String(v ?? '').toLowerCase().includes(search);
            });
        });
    }

    return result;
}

/**
 * Copie le JSON brut dans le presse-papier
 */
function copyDiscoveryResult() {
    if (discoveryRawData) {
        const text = typeof discoveryRawData === 'string' ? discoveryRawData : JSON.stringify(discoveryRawData, null, 2);
        navigator.clipboard.writeText(text);
        showToast('Copié', 'Résultat copié dans le presse-papier', 'success');
    }
}

/**
 * Efface les résultats
 */
function clearDiscoveryResult() {
    document.getElementById('discoveryResultsArea').style.display = 'none';
    document.getElementById('discoveryTableBody').innerHTML = '';
    document.getElementById('discoveryTableHead').innerHTML = '';
    document.getElementById('discoveryResultRaw').textContent = '';
    document.getElementById('discoveryFilterSearch').value = '';
    document.getElementById('discoveryFilterField').value = '';
    document.getElementById('discoveryFilterValue').value = '';
    discoveryRawData = null;
    discoveryVisibleColumns = null;
}

// ─── Discovery : M2M Setup ───────────────────────────────────────────

/**
 * Bascule entre le mode requêteur et le mode M2M Setup
 */
function switchDiscoveryMode(mode) {
    discoveryMode = mode;
    const requester = document.getElementById('discoveryRequester');
    const results = document.getElementById('discoveryResultsArea');
    const m2mPanel = document.getElementById('discoveryM2MPanel');
    const btnReq = document.getElementById('discoveryModeRequester');
    const btnM2M = document.getElementById('discoveryModeM2M');

    if (mode === 'm2m') {
        requester.classList.add('d-none');
        results.style.display = 'none';
        m2mPanel.classList.remove('d-none');
        btnReq.classList.remove('active');
        btnM2M.classList.add('active');
        // Charger le M2M si une app est sélectionnée
        if (discoverySelectedApp) {
            loadM2MSetup(discoverySelectedApp.slug);
        }
    } else {
        m2mPanel.classList.add('d-none');
        requester.classList.remove('d-none');
        btnReq.classList.add('active');
        btnM2M.classList.remove('active');
    }
}

/**
 * Charge le bloc M2M Setup pour une app
 */
async function loadM2MSetup(slug) {
    const content = document.getElementById('discoveryM2MContent');
    const copyBtn = document.getElementById('discoveryM2MCopyBtn');

    // Si déjà en cache pour cette app
    if (discoveryM2MMarkdown && discoverySelectedApp?.slug === slug) {
        return;
    }

    content.innerHTML = `
        <div class="text-center text-muted py-5">
            <div class="spinner-border spinner-border-sm"></div>
            <p class="mt-2 small">Génération du bloc M2M…</p>
        </div>`;
    copyBtn.disabled = true;

    try {
        const result = await api.getM2MSetup(slug);
        discoveryM2MMarkdown = result.markdown;

        content.innerHTML = `<pre class="discovery-m2m-pre mb-0">${escapeHtml(result.markdown)}</pre>`;
        copyBtn.disabled = false;
    } catch (error) {
        console.error('Erreur chargement M2M Setup:', error);
        content.innerHTML = `
            <div class="text-center text-danger py-5">
                <i class="bi bi-exclamation-triangle fs-3 d-block mb-2"></i>
                Erreur lors du chargement du setup M2M
                <div class="small text-muted mt-1">${escapeHtml(error.message || String(error))}</div>
            </div>`;
        copyBtn.disabled = true;
    }
}

/**
 * Copie le Markdown M2M dans le presse-papier
 */
function copyM2MSetup() {
    if (!discoveryM2MMarkdown) return;
    navigator.clipboard.writeText(discoveryM2MMarkdown).then(() => {
        showToast('Copié', 'Bloc M2M copié dans le presse-papier — collez-le dans votre IA coder', 'success');
    }).catch(() => {
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = discoveryM2MMarkdown;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('Copié', 'Bloc M2M copié dans le presse-papier', 'success');
    });
}
