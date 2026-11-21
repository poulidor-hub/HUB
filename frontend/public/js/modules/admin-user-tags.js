// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * admin-user-tags.js
 * Module admin pour la gestion des préférences utilisateur.
 * Refonte « dense pro » (maquettes validées) : tables compactes avec toolbar
 * de filtres côté client (aucune animation — re-render innerHTML ciblé),
 * modale de création/édition en sections ①②③, passerelle ★ dans la modale
 * d'édition d'un utilisateur.
 */

// ─── État module (persiste entre les re-renders, aucun stockage externe) ──

// Onglet Catégories
let _adminCategories = [];        // dernières catégories chargées
let _adminCatSearchTerm = '';     // filtre client courant (label/slug)
let _adminCatSearchTimer = null;  // debounce ~200 ms

// Onglet Utilisateurs
const _adminUsersState = {
    users: [],        // dernières données GET /tags/users
    search: '',       // filtre client (nom + username)
    primary: 'all',   // all | with | without
    prefs: 'all',     // all | with | without
    sortKey: 'username', // username | primary
    sortDir: 'asc',   // asc | desc
    page: 1,
    pageSize: 50,     // pagination client 50/page
};
let _adminUsersSearchTimer = null; // debounce ~200 ms

// Palette d'avatars (déterministe par hash du username — maquettes validées)
const _ADMIN_AVATAR_COLORS = [
    '#2563eb', '#7c3aed', '#0891b2', '#ea580c',
    '#059669', '#d97706', '#db2777', '#4f46e5',
];

// ─── Chargement (onglet Catégories) ───────────────────────────────

async function loadUserTagsAdmin() {
    try {
        const [catsData, users] = await Promise.all([
            api.getTagCategories(),
            // « renseignés » nécessite les users : si cet appel échoue, on
            // garde quand même la table des catégories fonctionnelle.
            api.getUsersWithTags().catch(() => []),
        ]);
        const categories = catsData.categories || [];
        _adminCategories = categories;
        _adminUsersState.users = Array.isArray(users) ? users : [];
        renderTagCategoriesTable(categories);
        updateTagStats(categories, _adminUsersState.users);
    } catch (error) {
        console.error('Erreur chargement préférences:', error);
        document.getElementById('adminUserTagsTable').innerHTML =
            '<div class="data-error">Erreur lors du chargement</div>';
    }
}

/** Stats discrètes de la toolbar : « X/Y actives · ★ Z · n/total renseignés ». */
function updateTagStats(categories, users) {
    const el = document.getElementById('tagCatStats');
    if (!el) return;
    const total = categories.length;
    const active = categories.filter(c => c.is_active).length;
    const primary = categories.filter(c => c.primary_enabled).length;
    const list = Array.isArray(users) ? users : [];
    const filled = list.filter(u => _adminUserHasTags(u)).length;
    el.textContent = active + '/' + total + ' actives · ★ ' + primary +
        ' · ' + filled + '/' + list.length + ' renseignés';
}

// ─── Slug auto-généré ─────────────────────────────────────────────

function slugify(text) {
    return text
        .toString()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '') // enlever les accents
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .substring(0, 80);
}

/** Aperçu live du slug sous le champ nom (modale de création/édition). */
function updateTagFormSlug() {
    const label = document.getElementById('tagFormLabel').value.trim();
    const el = document.getElementById('tagFormSlugPreview');
    if (el) el.textContent = 'slug : ' + (slugify(label) || '—');
}

// ─── Contrôle segmenté Unique | Multiples ─────────────────────────
// L'état reste porté par la checkbox cachée #tagFormIsMultiple : la logique
// de sauvegarde (handleSaveTagCategory) et le pré-remplissage ne changent pas.

function setTagFormMultiple(el, val) {
    const cb = document.getElementById('tagFormIsMultiple');
    if (cb) cb.checked = (val === 1 || val === '1' || val === true);
    _syncTagFormMultipleUI();
}

function _syncTagFormMultipleUI() {
    const cb = document.getElementById('tagFormIsMultiple');
    const multiple = !!(cb && cb.checked);
    const uniqueBtn = document.getElementById('tagSegUnique');
    const multipleBtn = document.getElementById('tagSegMultiple');
    if (uniqueBtn) uniqueBtn.setAttribute('aria-pressed', String(!multiple));
    if (multipleBtn) multipleBtn.setAttribute('aria-pressed', String(multiple));
}

// ─── Rendu table Catégories (dense, filtre client) ────────────────

function renderTagCategoriesTable(categories) {
    const container = document.getElementById('adminUserTagsTable');
    if (!container) return;
    if (!categories.length) {
        container.innerHTML = '<div class="data-empty">Aucune préférence définie</div>';
        return;
    }
    const q = normalizeSearch(_adminCatSearchTerm);
    const filtered = q
        ? categories.filter(c =>
            normalizeSearch(c.label).includes(q) || normalizeSearch(c.slug).includes(q))
        : categories;
    if (!filtered.length) {
        container.innerHTML = '<div class="data-empty">Aucune préférence ne correspond à la recherche</div>';
        return;
    }
    const rows = filtered.map(cat => {
        const syncBtn = cat.source === 'm2m'
            ? `<button class="btn btn-sm btn-outline-primary me-1" data-action="handleImportTagM2m" data-arg1="${escapeAttr(cat.slug)}" title="Synchroniser depuis l'application"><i class="bi bi-arrow-repeat"></i></button>`
            : '';
        const appRef = cat.source_app_name || cat.source_app_id;
        const sourceBadge = cat.source === 'm2m'
            ? `<span class="pref-badge pref-badge-m2m" title="Synchronisée M2M${appRef ? ' — ' + escapeHtml(String(appRef)) : ''}">M2M · ${escapeHtml(appRef ? String(appRef) : '?')}</span>`
            : '<span class="pref-badge pref-badge-manual">Manuel</span>';
        return `
        <tr>
            <td>
                <div class="pref-cat-name">${escapeHtml(cat.label)}</div>
                <div class="pref-cat-slug">${escapeHtml(cat.slug)}</div>
            </td>
            <td>${cat.is_multiple
                ? '<span class="pref-badge pref-badge-multi">Multiples</span>'
                : '<span class="pref-badge pref-badge-unique">Unique</span>'}</td>
            <td>${cat.primary_enabled
                ? '<span class="pref-badge pref-badge-star" title="Secteur principal autorisé">★ Activé</span>'
                : '<span class="pref-cat-none">—</span>'}</td>
            <td>${sourceBadge}</td>
            <td class="pref-num">${(cat.values || []).length}</td>
            <td>${cat.is_active
                ? '<span class="pref-badge pref-badge-on">Actif</span>'
                : '<span class="pref-badge pref-badge-off">Inactif</span>'}</td>
            <td class="text-nowrap">
                ${syncBtn}
                <button class="btn btn-sm btn-outline-secondary me-1" data-action="showTagCategoryModal" data-arg1="${escapeAttr(cat.slug)}" title="Modifier"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger" data-action="deleteTagCategory" data-arg1="${escapeAttr(cat.slug)}" title="Supprimer"><i class="bi bi-trash"></i></button>
            </td>
        </tr>
    `}).join('');
    container.innerHTML = `
        <table class="table table-striped table-hover align-middle mb-0 pref-table">
            <thead><tr><th scope="col">Préférence</th><th scope="col">Type</th><th scope="col">★ Principal</th><th scope="col">Source</th><th scope="col">Choix</th><th scope="col">Statut</th><th scope="col">Actions</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

/** Recherche légère de l'onglet Catégories (debounce 200 ms, filtre client). */
function filterAdminTagCategories(el) {
    clearTimeout(_adminCatSearchTimer);
    _adminCatSearchTimer = setTimeout(() => {
        _adminCatSearchTerm = el ? el.value : '';
        renderTagCategoriesTable(_adminCategories);
    }, 200);
}

// ─── Modal CRUD (sections ①②③) ───────────────────────────────────

let tagModalInstance = null;

async function showTagCategoryModal(slug) {
    const modalEl = document.getElementById('tagCategoryModal');
    if (!tagModalInstance) tagModalInstance = new bootstrap.Modal(modalEl);

    document.getElementById('tagModalTitle').textContent = '＋ Nouvelle préférence';
    const saveBtn = document.getElementById('tagModalSaveBtn');
    if (saveBtn) saveBtn.textContent = 'Créer la préférence';
    document.getElementById('tagFormLabel').value = '';
    document.getElementById('tagFormIsMultiple').checked = false;
    document.getElementById('tagFormPrimaryEnabled').checked = false;
    document.getElementById('tagFormSourceCsv').checked = true;
    document.getElementById('tagFormSourceM2m').checked = false;
    document.getElementById('tagFormSourceApp').value = '';
    document.getElementById('tagFormSourceAppGroup').classList.add('d-none');
    document.getElementById('tagFormM2mTable').innerHTML = '<option value="">-- Choisir --</option>';
    document.getElementById('tagFormM2mColumn').innerHTML = '<option value="">-- Choisir --</option>';
    document.getElementById('tagFormTableColGroup').classList.add('d-none');
    document.getElementById('tagFormValues').value = '';
    updateTagFormSlug();
    _syncTagFormMultipleUI();

    await loadSourceAppsDropdown();

    if (slug) {
        try {
            const data = await api.getTagCategories();
            const cat = (data.categories || []).find(c => c.slug === slug);
            if (cat) {
                document.getElementById('tagModalTitle').textContent = 'Modifier : ' + cat.label;
                if (saveBtn) saveBtn.textContent = 'Enregistrer';
                document.getElementById('tagFormLabel').value = cat.label;
                document.getElementById('tagFormIsMultiple').checked = cat.is_multiple;
                document.getElementById('tagFormPrimaryEnabled').checked = !!cat.primary_enabled;
                if (cat.source === 'm2m') {
                    document.getElementById('tagFormSourceM2m').checked = true;
                    document.getElementById('tagFormSourceAppGroup').classList.remove('d-none');
                    document.getElementById('tagFormSourceApp').value = cat.source_app_id || '';
                }
                document.getElementById('tagFormValues').value = (cat.values || []).join('\n');
                updateTagFormSlug();
                _syncTagFormMultipleUI();
                toggleTagSource();
            }
        } catch (e) {
            showToast('Erreur lors du chargement', 'danger');
            return;
        }
    } else {
        toggleTagSource();
    }

    const csvBtn = document.getElementById('btnImportCsvTag');
    if (csvBtn) csvBtn.onclick = () => handleImportTagCsv(slug);

    const syncBtn = document.getElementById('btnSyncM2m');
    if (syncBtn) syncBtn.onclick = () => handleSyncM2mFromModal(slug);

    tagModalInstance.show();
}

async function loadSourceAppsDropdown() {
    try {
        const apps = await api.getAllApps();
        const select = document.getElementById('tagFormSourceApp');
        select.innerHTML = '<option value="">-- Sélectionner une app --</option>';
        (Array.isArray(apps) ? apps : []).forEach(app => {
            select.innerHTML += `<option value="${app.id}">${escapeHtml(app.name)} (${escapeHtml(app.slug)})</option>`;
        });
    } catch (e) {
        console.warn('Impossible de charger les apps:', e);
    }
}

async function discoverSourceTables() {
    const appId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
    if (!appId) { showToast('Sélectionnez d\'abord une application', 'danger'); return; }

    const tableSelect = document.getElementById('tagFormM2mTable');
    const colSelect = document.getElementById('tagFormM2mColumn');
    const group = document.getElementById('tagFormTableColGroup');

    tableSelect.innerHTML = '<option value="">Chargement...</option>';
    colSelect.innerHTML = '<option value="">-- Choisir --</option>';
    group.classList.remove('d-none');

    try {
        const data = await api.discoverSourceTables(appId);
        const tables = data.tables || [];
        tableSelect.innerHTML = '<option value="">-- Choisir --</option>' +
            tables.map(t => `<option value="${t}">${t}</option>`).join('');
        if (tables.length === 0) showToast('Aucune table trouvée dans cette application', 'warning');
    } catch (error) {
        tableSelect.innerHTML = '<option value="">Erreur</option>';
        showToast(error.message || 'Impossible de découvrir les tables', 'danger');
    }
}

async function discoverSourceColumns() {
    const appId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
    const table = document.getElementById('tagFormM2mTable').value;
    if (!appId || !table) return;

    const colSelect = document.getElementById('tagFormM2mColumn');
    colSelect.innerHTML = '<option value="">Chargement...</option>';

    try {
        const data = await api.discoverSourceColumns(appId, table);
        const columns = data.columns || [];
        colSelect.innerHTML = '<option value="">-- Choisir --</option>' +
            columns.map(c => `<option value="${c}">${c}</option>`).join('');
    } catch (error) {
        colSelect.innerHTML = '<option value="">Erreur</option>';
        showToast(error.message || 'Impossible de découvrir les colonnes', 'danger');
    }
}

function toggleTagSource() {
    const isM2m = document.getElementById('tagFormSourceM2m').checked;
    document.getElementById('tagFormSourceAppGroup').classList.toggle('d-none', !isM2m);
    document.getElementById('tagFormValuesGroup').classList.toggle('d-none', isM2m);
}

async function handleSaveTagCategory() {
    const label = document.getElementById('tagFormLabel').value.trim();
    const isMultiple = document.getElementById('tagFormIsMultiple').checked;
    const primaryEnabled = document.getElementById('tagFormPrimaryEnabled').checked;
    const source = document.getElementById('tagFormSourceM2m').checked ? 'm2m' : 'csv';
    const sourceAppId = source === 'm2m' ? parseInt(document.getElementById('tagFormSourceApp').value) || null : null;
    const m2mTable = source === 'm2m' ? document.getElementById('tagFormM2mTable').value : '';
    const m2mColumn = source === 'm2m' ? document.getElementById('tagFormM2mColumn').value : '';
    const m2mEndpoint = (m2mTable && m2mColumn)
        ? `/api/hub/data/values?table=${encodeURIComponent(m2mTable)}&column=${encodeURIComponent(m2mColumn)}`
        : (source === 'm2m' ? '/api/hub/data/values' : null);
    const valuesRaw = document.getElementById('tagFormValues').value.trim();
    const values = valuesRaw ? valuesRaw.split('\n').map(v => v.trim()).filter(v => v) : [];

    if (!label) { showToast('Le nom de la préférence est obligatoire', 'danger'); return; }

    try {
        const existingData = await api.getTagCategories();
        const existing = (existingData.categories || []).find(c => c.label === label);
        const slug = existing ? existing.slug : slugify(label);

        if (existing) {
            const updateData = { label, is_multiple: isMultiple, values, is_active: existing.is_active, primary_enabled: primaryEnabled };
            if (source === 'm2m') updateData.m2m_endpoint = m2mEndpoint;
            await api.updateTagCategory(slug, updateData);
            showToast('Préférence mise à jour', 'success');
        } else {
            if (!slug) { showToast('Le nom ne permet pas de générer un identifiant valide', 'danger'); return; }
            // m2m_endpoint: str non-Optional côté backend (default '/api/tags/export')
            // — ne pas envoyer la clé quand elle est nulle (source csv), sinon 422.
            const payload = { slug, label, is_multiple: isMultiple, source, source_app_id: sourceAppId, values, primary_enabled: primaryEnabled };
            if (m2mEndpoint) payload.m2m_endpoint = m2mEndpoint;
            await api.createTagCategory(payload);
            showToast('Préférence créée', 'success');
        }
        tagModalInstance.hide();
        loadUserTagsAdmin();
    } catch (error) {
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}

async function deleteTagCategory(slug) {
    if (!confirm('Supprimer cette préférence ? Les choix des utilisateurs seront perdus.')) return;
    try {
        await api.deleteTagCategory(slug);
        showToast('Préférence supprimée', 'success');
        loadUserTagsAdmin();
    } catch (error) {
        showToast(error.message || 'Erreur lors de la suppression', 'danger');
    }
}

// ─── Import CSV/M2M ───────────────────────────────────────────────

function handleImportTagCsv(slug) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,.txt';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
            const result = await api.importTagCsv(slug, file, 'replace');
            showToast(result.values.length + ' choix importés', 'success');
            loadUserTagsAdmin();
        } catch (error) {
            showToast(error.message || 'Erreur lors de l\'import', 'danger');
        }
    };
    input.click();
}

async function handleImportTagM2m(slug) {
    try {
        const result = await api.importTagM2m(slug);
        showToast(result.values.length + ' choix importés depuis l\'application', 'success');
        loadUserTagsAdmin();
    } catch (error) {
        showToast(error.message || 'Erreur lors de la synchronisation', 'danger');
    }
}

async function handleSyncM2mFromModal(slug) {
    // Si on édite une catégorie existante, sauvegarder d'abord puis sync.
    // La sauvegarde bascule AUSSI la source en m2m (source, source_app_id,
    // m2m_endpoint) — sinon l'import échoue en 400 « pas configurée en
    // source M2M » sur une catégorie restée en CSV.
    if (slug) {
        const label = document.getElementById('tagFormLabel').value.trim();
        const isMultiple = document.getElementById('tagFormIsMultiple').checked;
        const primaryEnabled = document.getElementById('tagFormPrimaryEnabled').checked;
        const sourceAppId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
        const m2mTable = document.getElementById('tagFormM2mTable').value;
        const m2mColumn = document.getElementById('tagFormM2mColumn').value;
        if (!label) { showToast('Saisissez d\'abord un nom de préférence', 'danger'); return; }
        if (!sourceAppId) { showToast('Sélectionnez d\'abord une application source', 'danger'); return; }
        if (!m2mTable || !m2mColumn) { showToast('Sélectionnez une table et une colonne (cliquez sur  🔍  pour découvrir)', 'danger'); return; }
        const m2mEndpoint = `/api/hub/data/values?table=${encodeURIComponent(m2mTable)}&column=${encodeURIComponent(m2mColumn)}`;
        try {
            await api.updateTagCategory(slug, {
                label, is_multiple: isMultiple, is_active: true,
                source: 'm2m', source_app_id: sourceAppId, m2m_endpoint: m2mEndpoint,
                primary_enabled: primaryEnabled,
            });
        } catch (e) { /* sauvegarde échouée, on tente quand même la sync */ }
        await handleImportTagM2m(slug);
        try {
            const data = await api.getTagCategories();
            const cat = (data.categories || []).find(c => c.slug === slug);
            if (cat) document.getElementById('tagFormValues').value = (cat.values || []).join('\n');
        } catch (e) { /* ignore */ }
        return;
    }
    // Si c'est une création, sauvegarder d'abord puis sync
    const label = document.getElementById('tagFormLabel').value.trim();
    if (!label) { showToast('Saisissez d\'abord un nom de préférence', 'danger'); return; }
    try {
        const isMultiple = document.getElementById('tagFormIsMultiple').checked;
        const primaryEnabled = document.getElementById('tagFormPrimaryEnabled').checked;
        const sourceAppId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
        const m2mTable = document.getElementById('tagFormM2mTable').value;
        const m2mColumn = document.getElementById('tagFormM2mColumn').value;
        if (!sourceAppId) { showToast('Sélectionnez d\'abord une application source', 'danger'); return; }
        if (!m2mTable || !m2mColumn) { showToast('Sélectionnez une table et une colonne (cliquez sur  🔍  pour découvrir)', 'danger'); return; }
        const m2mEndpoint = `/api/hub/data/values?table=${encodeURIComponent(m2mTable)}&column=${encodeURIComponent(m2mColumn)}`;
        const newSlug = slugify(label);
        await api.createTagCategory({ slug: newSlug, label, is_multiple: isMultiple, source: 'm2m', source_app_id: sourceAppId, m2m_endpoint: m2mEndpoint, values: [], primary_enabled: primaryEnabled });
        showToast('Préférence créée, synchronisation en cours...', 'success');
        await handleImportTagM2m(newSlug);
        tagModalInstance.hide();
    } catch (error) {
        showToast(error.message || 'Erreur', 'danger');
    }
}

// ─── Mes préférences (sélection personnelle, autosave) ────────────

async function loadAdminMyTags() {
    try {
        const [categoriesData, myTagsData] = await Promise.all([
            api.getActiveTagCategories(), api.getMyTags()
        ]);
        renderPrefsPage(categoriesData.categories || [], myTagsData.tags || {}, myTagsData.primary || {});
    } catch (error) {
        console.error('Erreur chargement préférences:', error);
        const sections = document.getElementById('prefSections');
        if (sections) sections.innerHTML = '<div class="data-error">Erreur lors du chargement de vos préférences.</div>';
    }
}

// ─── Onglet Utilisateurs (table dense : filtres, tri, pagination) ──

let _tagCategoryLabels = {};

/** Primaire d'un user : {slug: value} — rendu défensif (undefined → ''). */
function _adminUserPrimaryValue(u) {
    const primary = (u && u.primary && typeof u.primary === 'object') ? u.primary : {};
    const entry = Object.entries(primary).find(([, v]) => v !== undefined && v !== null && String(v).trim() !== '');
    return entry ? String(entry[1]) : '';
}

/** Au moins une entrée non vide dans tags. */
function _adminUserHasTags(u) {
    return Object.values((u && u.tags) || {}).some(v => Array.isArray(v) && v.length > 0);
}

/** Nom complet affiché en sous-texte (champs tolérés : full_name | name). */
function _adminUserFullName(u) {
    return (u && (u.full_name || u.name)) || '';
}

/** Initiales d'avatar : nom complet sinon username (ex. « VP »). */
function _adminAvatarInitials(username, fullName) {
    const src = String(fullName || '').trim() || String(username || '?');
    const parts = src.split(/[\s._-]+/).filter(Boolean);
    const ini = parts.length >= 2 ? (parts[0][0] + parts[1][0]) : src.slice(0, 2);
    return (ini || '?').toUpperCase();
}

/** Couleur d'avatar déterministe (hash simple du username). */
function _adminAvatarColor(seed) {
    const s = String(seed || '?');
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return _ADMIN_AVATAR_COLORS[h % _ADMIN_AVATAR_COLORS.length];
}

/** Filtres → tri client → liste complète (sans pagination). */
function _adminFilteredSortedUsers() {
    const s = _adminUsersState;
    const q = normalizeSearch(s.search);
    const list = s.users.filter(u => {
        if (q) {
            const hay = normalizeSearch(String(u.username || '') + ' ' + _adminUserFullName(u));
            if (!hay.includes(q)) return false;
        }
        const hasPrimary = !!_adminUserPrimaryValue(u);
        if (s.primary === 'with' && !hasPrimary) return false;
        if (s.primary === 'without' && hasPrimary) return false;
        const hasTags = _adminUserHasTags(u);
        if (s.prefs === 'with' && !hasTags) return false;
        if (s.prefs === 'without' && hasTags) return false;
        return true;
    });
    const dir = s.sortDir === 'desc' ? -1 : 1;
    list.sort((a, b) => {
        const va = (s.sortKey === 'primary' ? _adminUserPrimaryValue(a) : String(a.username || '')).toLowerCase();
        const vb = (s.sortKey === 'primary' ? _adminUserPrimaryValue(b) : String(b.username || '')).toLowerCase();
        return va < vb ? -dir : va > vb ? dir : 0;
    });
    return list;
}

/** Fenêtre de pages compacte (≤ 7 : tout ; sinon 1 … c-1 c c+1 … n). */
function _adminPagerPages(cur, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const pages = [1];
    const from = Math.max(2, cur - 1);
    const to = Math.min(total - 1, cur + 1);
    if (from > 2) pages.push('…');
    for (let p = from; p <= to; p++) pages.push(p);
    if (to < total - 1) pages.push('…');
    pages.push(total);
    return pages;
}

async function loadAdminTagUsers() {
    const container = document.getElementById('adminTagUsersTable');
    if (!container) return;
    try {
        const [users, categoriesData] = await Promise.all([
            api.getUsersWithTags(), api.getTagCategories()
        ]);
        _tagCategoryLabels = {};
        (categoriesData.categories || []).forEach(c => { _tagCategoryLabels[c.slug] = c.label; });
        _adminUsersState.users = Array.isArray(users) ? users : [];
        _adminUsersState.page = 1;
        renderAdminTagUsersTable();
    } catch (error) {
        console.error('Erreur chargement utilisateurs tags:', error);
        container.innerHTML = '<div class="data-error">Erreur lors du chargement</div>';
    }
}

/** Recherche utilisateurs (debounce 200 ms, filtre client nom + username). */
function filterAdminTagUsers(el) {
    clearTimeout(_adminUsersSearchTimer);
    _adminUsersSearchTimer = setTimeout(() => {
        _adminUsersState.search = el ? el.value : '';
        _adminUsersState.page = 1;
        renderAdminTagUsersTable();
    }, 200);
}

function filterAdminTagUsersPrimary(el) {
    _adminUsersState.primary = (el && el.value) || 'all';
    _adminUsersState.page = 1;
    renderAdminTagUsersTable();
}

function filterAdminTagUsersPrefs(el) {
    _adminUsersState.prefs = (el && el.value) || 'all';
    _adminUsersState.page = 1;
    renderAdminTagUsersTable();
}

/** Tri client au clic th « Utilisateur » / « Secteur principal » (asc/desc). */
function adminTagUsersSort(el, key) {
    const s = _adminUsersState;
    if (s.sortKey === key) {
        s.sortDir = s.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        s.sortKey = key;
        s.sortDir = 'asc';
    }
    s.page = 1;
    renderAdminTagUsersTable();
}

function adminTagUsersPage(el, arg) {
    if (arg === 'prev') _adminUsersState.page--;
    else if (arg === 'next') _adminUsersState.page++;
    else _adminUsersState.page = parseInt(arg, 10) || 1;
    renderAdminTagUsersTable();
}

function renderAdminTagUsersTable() {
    const container = document.getElementById('adminTagUsersTable');
    if (!container) return;
    const countEl = document.getElementById('tagUsersCount');
    const count = _adminUsersState.users.length;

    if (!count) {
        if (countEl) countEl.textContent = '0 utilisateur';
        container.innerHTML = '<div class="data-empty">Aucun utilisateur</div>';
        return;
    }

    const list = _adminFilteredSortedUsers();
    if (countEl) countEl.textContent = list.length + ' utilisateur' + (list.length > 1 ? 's' : '');
    if (!list.length) {
        container.innerHTML = '<div class="data-empty">Aucun utilisateur ne correspond aux filtres</div>';
        return;
    }

    const s = _adminUsersState;
    const totalPages = Math.max(1, Math.ceil(list.length / s.pageSize));
    s.page = Math.min(Math.max(1, s.page), totalPages);
    const start = (s.page - 1) * s.pageSize;
    const pageUsers = list.slice(start, start + s.pageSize);

    const sortArrow = key => key === s.sortKey ? (s.sortDir === 'asc' ? ' ▲' : ' ▼') : '';
    const ariaSort = key => key === s.sortKey ? (s.sortDir === 'asc' ? 'ascending' : 'descending') : 'none';

    const rows = pageUsers.map(u => {
        const name = _adminUserFullName(u);
        const primaryVal = _adminUserPrimaryValue(u);
        const primaryCell = primaryVal
            ? `<span class="pref-badge pref-badge-primary" title="Secteur principal">★ ${escapeHtml(primaryVal)}</span>`
            : '<span class="pref-badge pref-badge-none" title="Pas de secteur principal">— aucun</span>';
        const entries = Object.entries((u && u.tags) || {}).filter(([, vals]) => Array.isArray(vals) && vals.length > 0);
        let chips;
        if (!entries.length) {
            chips = '<span class="pref-cat-none">Aucune préférence</span>';
        } else {
            chips = entries.slice(0, 3).map(([slug, vals]) => {
                const label = _tagCategoryLabels[slug] || slug;
                const full = label + ' : ' + vals.join(', ');
                return `<span class="pref-chip-mini" title="${escapeAttr(full)}">${escapeHtml(full)}</span>`;
            }).join('');
            if (entries.length > 3) {
                chips += `<span class="pref-chip-mini" title="${entries.length - 3} autre(s) préférence(s)">+${entries.length - 3}</span>`;
            }
        }
        return `<tr>
            <td><div class="pref-user-cell">
                <span class="pref-avatar" style="background:${_adminAvatarColor(u.username)}" aria-hidden="true">${escapeHtml(_adminAvatarInitials(u.username, name))}</span>
                <span class="pref-user-meta">
                    <span class="pref-user-name">${escapeHtml(u.username)}</span>
                    ${name ? `<span class="pref-user-sub">${escapeHtml(name)}</span>` : ''}
                </span>
            </div></td>
            <td>${primaryCell}</td>
            <td>${chips}</td>
            <td class="text-nowrap"><button class="btn btn-sm btn-outline-primary" data-action="showUserTagsModal" data-arg1="${escapeAttr(u.username)}" title="Modifier les préférences de ${escapeAttr(u.username)}"><i class="bi bi-pencil me-1"></i>Modifier</button></td>
        </tr>`;
    }).join('');

    const pages = _adminPagerPages(s.page, totalPages);
    const pager = `<div class="pref-pager">
        <span>${start + 1}–${start + pageUsers.length} sur ${list.length}</span>
        <span class="pref-pager-pages">
            <button type="button" class="pref-pager-btn" data-action="adminTagUsersPage" data-arg1="prev" title="Page précédente"${s.page <= 1 ? ' disabled' : ''}>‹</button>
            ${pages.map(p => p === '…'
                ? '<span class="pref-pager-ellipsis">…</span>'
                : `<button type="button" class="pref-pager-btn${p === s.page ? ' active' : ''}" data-action="adminTagUsersPage" data-arg1="${p}" title="Page ${p}"${p === s.page ? ' aria-current="page"' : ''}>${p}</button>`
            ).join('')}
            <button type="button" class="pref-pager-btn" data-action="adminTagUsersPage" data-arg1="next" title="Page suivante"${s.page >= totalPages ? ' disabled' : ''}>›</button>
        </span>
    </div>`;

    container.innerHTML = `
        <table class="table table-striped table-hover align-middle mb-0 pref-table">
            <thead><tr>
                <th scope="col" class="pref-th-sort" data-action="adminTagUsersSort" data-arg1="username" aria-sort="${ariaSort('username')}" title="Trier par utilisateur">Utilisateur${sortArrow('username')}</th>
                <th scope="col" class="pref-th-sort" data-action="adminTagUsersSort" data-arg1="primary" aria-sort="${ariaSort('primary')}" title="Trier par secteur principal">Secteur principal ★${sortArrow('primary')}</th>
                <th scope="col">Préférences</th>
                <th scope="col">Actions</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
        ${pager}`;
}

// ─── Modale d'édition des préférences d'un utilisateur ────────────

let _userTagsModalInstance = null;
let _userTagsModalTarget = null;

/** Listener unique : reconstruction des radios ★ quand les chips changent. */
function _bindUserTagsModalRebuild() {
    const fields = document.getElementById('userTagsModalFields');
    if (!fields || fields.dataset.prefBound) return;
    fields.dataset.prefBound = '1';
    fields.addEventListener('change', (e) => {
        if (e.target.matches('input[data-category]')) prefRebuildPrimaryAround(e.target);
    });
}

async function showUserTagsModal(username) {
    const modalEl = document.getElementById('userTagsModal');
    if (!_userTagsModalInstance) {
        _userTagsModalInstance = new bootstrap.Modal(modalEl);
        _bindUserTagsModalRebuild();
    }
    _userTagsModalTarget = username;
    document.getElementById('userTagsModalUsername').textContent = username;
    document.getElementById('userTagsModalFields').innerHTML = '<p class="text-muted">Chargement...</p>';
    _userTagsModalInstance.show();
    try {
        const [categoriesData, userTagsData] = await Promise.all([
            api.getActiveTagCategories(), api.getUserTags(username)
        ]);
        const categories = categoriesData.categories || [];
        const userTags = userTagsData.tags || {};
        const userPrimary = userTagsData.primary || {};
        if (!categories.length) {
            document.getElementById('userTagsModalFields').innerHTML =
                '<p class="text-muted mb-0">Aucune préférence disponible. Créez-en d\'abord dans l\'onglet Catégories.</p>';
            return;
        }
        // Passerelle ★ : le primary du user est passé au helper partagé pour
        // afficher la section « Secteur principal » des catégories qui
        // l'autorisent (extension rétro-compatible de buildTagsFormHTML).
        const sections = `<div data-pref-root="1">` +
            buildTagsFormHTML(categories, userTags, 'utm', userPrimary) +
            '</div>';
        document.getElementById('userTagsModalFields').innerHTML = sections;
    } catch (error) {
        document.getElementById('userTagsModalFields').innerHTML = '<p class="text-danger mb-0">Erreur lors du chargement.</p>';
    }
}

async function handleSaveUserTagsModal() {
    if (!_userTagsModalTarget) return;
    const fieldsRoot = document.getElementById('userTagsModalFields');
    // collectTagsFromForm ignore les radios ★ (data-primary-category) ;
    // collectPrimaryFromForm les lit — payload { tags, primary } complet.
    const tags = collectTagsFromForm('#userTagsModalFields');
    const primary = fieldsRoot ? collectPrimaryFromForm(fieldsRoot) : {};
    try {
        await api.setUserTags(_userTagsModalTarget, { tags, primary });
        showToast('Préférences de ' + _userTagsModalTarget + ' mises à jour', 'success');
        _userTagsModalInstance.hide();
        loadAdminTagUsers();
    } catch (error) {
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}
