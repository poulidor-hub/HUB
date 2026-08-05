/**
 * admin-user-tags.js
 * Module admin pour la gestion des préférences utilisateur
 */

// ─── Chargement ───────────────────────────────────────────────────

async function loadUserTagsAdmin() {
    try {
        const data = await api.getTagCategories();
        const categories = data.categories || [];
        renderTagCategoriesTable(categories);
        updateTagStats(categories);
    } catch (error) {
        console.error('Erreur chargement préférences:', error);
        document.getElementById('adminUserTagsTable').innerHTML =
            '<div class="data-error">Erreur lors du chargement</div>';
    }
}

function updateTagStats(categories) {
    document.getElementById('tagStatsTotal').textContent = categories.length;
    document.getElementById('tagStatsActive').textContent = categories.filter(c => c.is_active).length;
    document.getElementById('tagStatsMultiple').textContent = categories.filter(c => c.is_multiple).length;
    document.getElementById('tagStatsUnique').textContent = categories.filter(c => !c.is_multiple).length;
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

// ─── Rendu table ──────────────────────────────────────────────────

function renderTagCategoriesTable(categories) {
    const container = document.getElementById('adminUserTagsTable');
    if (!categories.length) {
        container.innerHTML = '<div class="data-empty">Aucune préférence définie</div>';
        return;
    }
    const rows = categories.map(cat => {
        const syncBtn = cat.source === 'm2m'
            ? `<button class="btn btn-sm btn-outline-primary me-1" onclick="handleImportTagM2m('${cat.slug}')" title="Synchroniser depuis l'application"><i class="bi bi-arrow-repeat"></i></button>`
            : '';
        return `
        <tr>
            <td><strong>${escapeHtml(cat.label)}</strong></td>
            <td>${cat.is_multiple ? '<span class="badge bg-info">Multiples</span>' : '<span class="badge bg-secondary">Unique</span>'}</td>
            <td>${(cat.values || []).length} choix</td>
            <td>${cat.is_active ? '<span class="badge bg-success">Actif</span>' : '<span class="badge bg-danger">Inactif</span>'}</td>
            <td>
                ${syncBtn}
                <button class="btn btn-sm btn-primary me-1" onclick="showTagCategoryModal('${cat.slug}')"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-danger" onclick="deleteTagCategory('${cat.slug}')"><i class="bi bi-trash"></i></button>
            </td>
        </tr>
    `}).join('');
    container.innerHTML = `
        <table class="table table-hover align-middle mb-0">
            <thead><tr><th>Préférence</th><th>Type</th><th>Choix</th><th>Statut</th><th>Actions</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

// ─── Modal CRUD ───────────────────────────────────────────────────

let tagModalInstance = null;

async function showTagCategoryModal(slug) {
    const modalEl = document.getElementById('tagCategoryModal');
    if (!tagModalInstance) tagModalInstance = new bootstrap.Modal(modalEl);

    document.getElementById('tagModalTitle').textContent = slug ? 'Modifier la préférence' : 'Nouvelle préférence';
    document.getElementById('tagFormLabel').value = '';
    document.getElementById('tagFormIsMultiple').checked = false;
    document.getElementById('tagFormSourceCsv').checked = true;
    document.getElementById('tagFormSourceM2m').checked = false;
    document.getElementById('tagFormSourceApp').value = '';
    document.getElementById('tagFormSourceAppGroup').classList.add('d-none');
    document.getElementById('tagFormM2mTable').innerHTML = '<option value="">-- Choisir --</option>';
    document.getElementById('tagFormM2mColumn').innerHTML = '<option value="">-- Choisir --</option>';
    document.getElementById('tagFormTableColGroup').classList.add('d-none');
    document.getElementById('tagFormValues').value = '';
    document.getElementById('tagFormM2mEndpoint').style.display = 'none';

    await loadSourceAppsDropdown();

    if (slug) {
        try {
            const data = await api.getTagCategories();
            const cat = (data.categories || []).find(c => c.slug === slug);
            if (cat) {
                document.getElementById('tagFormLabel').value = cat.label;
                document.getElementById('tagFormIsMultiple').checked = cat.is_multiple;
                if (cat.source === 'm2m') {
                    document.getElementById('tagFormSourceM2m').checked = true;
                    document.getElementById('tagFormSourceAppGroup').classList.remove('d-none');
                    document.getElementById('tagFormSourceApp').value = cat.source_app_id || '';
                }
                document.getElementById('tagFormValues').value = (cat.values || []).join('\n');
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
            const updateData = { label, is_multiple: isMultiple, values, is_active: existing.is_active };
            if (source === 'm2m') updateData.m2m_endpoint = m2mEndpoint;
            await api.updateTagCategory(slug, updateData);
            showToast('Préférence mise à jour', 'success');
        } else {
            if (!slug) { showToast('Le nom ne permet pas de générer un identifiant valide', 'danger'); return; }
            await api.createTagCategory({ slug, label, is_multiple: isMultiple, source, source_app_id: sourceAppId, m2m_endpoint: m2mEndpoint, values });
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
    // Si on édite une catégorie existante, sauvegarder d'abord puis sync
    if (slug) {
        const label = document.getElementById('tagFormLabel').value.trim();
        const isMultiple = document.getElementById('tagFormIsMultiple').checked;
        if (label) {
            try {
                const sourceAppId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
                await api.updateTagCategory(slug, { label, is_multiple: isMultiple, is_active: true });
                if (sourceAppId) {
                    // Mettre à jour source_app_id si changé (via une deuxième sauvegarde n'est pas possible via le PATCH actuel - on garde l'existant)
                }
            } catch (e) { /* sauvegarde échouée, on tente quand même la sync */ }
        }
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
        const sourceAppId = parseInt(document.getElementById('tagFormSourceApp').value) || null;
        const m2mTable = document.getElementById('tagFormM2mTable').value;
        const m2mColumn = document.getElementById('tagFormM2mColumn').value;
        if (!sourceAppId) { showToast('Sélectionnez d\'abord une application source', 'danger'); return; }
        if (!m2mTable || !m2mColumn) { showToast('Sélectionnez une table et une colonne (cliquez sur  🔍  pour découvrir)', 'danger'); return; }
        const m2mEndpoint = `/api/hub/data/values?table=${encodeURIComponent(m2mTable)}&column=${encodeURIComponent(m2mColumn)}`;
        const newSlug = slugify(label);
        await api.createTagCategory({ slug: newSlug, label, is_multiple: isMultiple, source: 'm2m', source_app_id: sourceAppId, m2m_endpoint: m2mEndpoint, values: [] });
        showToast('Préférence créée, synchronisation en cours...', 'success');
        await handleImportTagM2m(newSlug);
        tagModalInstance.hide();
    } catch (error) {
        showToast(error.message || 'Erreur', 'danger');
    }
}

// ─── Mes préférences (sélection personnelle) ──────────────────────

async function loadAdminMyTags() {
    const fieldsContainer = document.getElementById('adminMyTagsFormFields');
    if (!fieldsContainer) return;

    try {
        const [categoriesData, myTagsData] = await Promise.all([
            api.getActiveTagCategories(), api.getMyTags()
        ]);
        const categories = categoriesData.categories || [];
        const myTags = myTagsData.tags || {};

        if (!categories.length) {
            fieldsContainer.innerHTML = '<p class="text-muted mb-0">Aucune préférence disponible. Créez-en une ci-dessus.</p>';
            return;
        }

        fieldsContainer.innerHTML = buildTagsFormHTML(categories, myTags, 'admin_tag');
    } catch (error) {
        console.error('Erreur chargement préférences admin:', error);
        fieldsContainer.innerHTML = '<p class="text-danger mb-0">Erreur lors du chargement de vos préférences.</p>';
    }
}

async function handleSaveAdminMyTags() {
    const feedback = document.getElementById('adminMyTagsFeedback');
    if (feedback) feedback.textContent = '';

    const tags = {};
    document.querySelectorAll('#adminMyTagsFormFields input:checked').forEach(input => {
        const cat = input.getAttribute('data-category');
        if (!tags[cat]) tags[cat] = [];
        tags[cat].push(input.value);
    });

    try {
        await api.updateMyTags({ tags });
        if (feedback) {
            feedback.textContent = 'Préférences enregistrées';
            feedback.className = 'ms-2 small text-success';
        }
        showToast('Préférences enregistrées', 'success');
    } catch (error) {
        if (feedback) {
            feedback.textContent = error.message || 'Erreur';
            feedback.className = 'ms-2 small text-danger';
        }
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}

// ─── Onglet Utilisateurs (gestion par admin) ──────────────────────

let _tagCategoryLabels = {};

async function loadAdminTagUsers() {
    const container = document.getElementById('adminTagUsersTable');
    if (!container) return;
    try {
        const [users, categoriesData] = await Promise.all([
            api.getUsersWithTags(), api.getTagCategories()
        ]);
        _tagCategoryLabels = {};
        (categoriesData.categories || []).forEach(c => { _tagCategoryLabels[c.slug] = c.label; });
        renderAdminTagUsersTable(users);
    } catch (error) {
        console.error('Erreur chargement utilisateurs tags:', error);
        container.innerHTML = '<div class="data-error">Erreur lors du chargement</div>';
    }
}

function renderAdminTagUsersTable(users) {
    const container = document.getElementById('adminTagUsersTable');
    if (!users || !users.length) {
        container.innerHTML = '<div class="data-empty">Aucun utilisateur</div>';
        return;
    }
    const rows = users.map(u => {
        const tagCount = Object.keys(u.tags || {}).length;
        const badges = Object.entries(u.tags || {}).map(([slug, values]) => {
            const label = _tagCategoryLabels[slug] || slug;
            return `<span class="badge bg-light text-dark me-1 mb-1">${escapeHtml(label)}: ${escapeHtml(values.join(', '))}</span>`;
        }).join('') || '<span class="text-muted">Aucune préférence</span>';
        return `<tr>
            <td><strong>${escapeHtml(u.username)}</strong></td>
            <td>${badges}</td>
            <td>${tagCount}</td>
            <td><button class="btn btn-sm btn-primary" onclick="showUserTagsModal('${escapeHtml(u.username)}')"><i class="bi bi-pencil me-1"></i>Modifier</button></td>
        </tr>`;
    }).join('');
    container.innerHTML = `<table class="table table-hover align-middle mb-0">
        <thead><tr><th>Utilisateur</th><th>Préférences</th><th>Nb</th><th>Actions</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
}

let _userTagsModalInstance = null;
let _userTagsModalTarget = null;

async function showUserTagsModal(username) {
    const modalEl = document.getElementById('userTagsModal');
    if (!_userTagsModalInstance) _userTagsModalInstance = new bootstrap.Modal(modalEl);
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
        if (!categories.length) {
            document.getElementById('userTagsModalFields').innerHTML =
                '<p class="text-muted mb-0">Aucune préférence disponible. Créez-en d\'abord dans l\'onglet Catégories.</p>';
            return;
        }
        const sections = buildTagsFormHTML(categories, userTags, 'utm');
        document.getElementById('userTagsModalFields').innerHTML = sections;
    } catch (error) {
        document.getElementById('userTagsModalFields').innerHTML = '<p class="text-danger mb-0">Erreur lors du chargement.</p>';
    }
}

async function handleSaveUserTagsModal() {
    if (!_userTagsModalTarget) return;
    const tags = {};
    document.querySelectorAll('#userTagsModalFields input:checked').forEach(input => {
        const cat = input.getAttribute('data-category');
        if (!tags[cat]) tags[cat] = [];
        tags[cat].push(input.value);
    });
    try {
        await api.setUserTags(_userTagsModalTarget, { tags });
        showToast('Préférences de ' + _userTagsModalTarget + ' mises à jour', 'success');
        _userTagsModalInstance.hide();
        loadAdminTagUsers();
    } catch (error) {
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}
