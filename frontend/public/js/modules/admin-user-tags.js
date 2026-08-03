/**
 * admin-user-tags.js
 * Module admin pour la gestion des catégories de tags utilisateur
 */

// ─── Chargement ───────────────────────────────────────────────────

async function loadUserTagsAdmin() {
    try {
        const data = await api.getTagCategories();
        const categories = data.categories || [];
        renderTagCategoriesTable(categories);
        updateTagStats(categories);
    } catch (error) {
        console.error('Erreur chargement tags:', error);
        document.getElementById('adminUserTagsTable').innerHTML =
            '<div class="data-error">Erreur lors du chargement des catégories</div>';
    }
}

function updateTagStats(categories) {
    document.getElementById('tagStatsTotal').textContent = categories.length;
    document.getElementById('tagStatsActive').textContent = categories.filter(c => c.is_active).length;
    document.getElementById('tagStatsMultiple').textContent = categories.filter(c => c.is_multiple).length;
    document.getElementById('tagStatsUnique').textContent = categories.filter(c => !c.is_multiple).length;
}

// ─── Rendu table ──────────────────────────────────────────────────

function renderTagCategoriesTable(categories) {
    const container = document.getElementById('adminUserTagsTable');
    if (!categories.length) {
        container.innerHTML = '<div class="data-empty">Aucune catégorie de tags définie</div>';
        return;
    }
    const rows = categories.map(cat => `
        <tr>
            <td><strong>${escapeHtml(cat.label)}</strong><br><small class="text-muted">${escapeHtml(cat.slug)}</small></td>
            <td>${cat.is_multiple ? '<span class="badge bg-info">Multiple</span>' : '<span class="badge bg-secondary">Unique</span>'}</td>
            <td>${cat.source === 'm2m' ? '<span class="badge bg-primary">M2M</span>' : '<span class="badge bg-success">CSV</span>'}</td>
            <td>${(cat.values || []).length}</td>
            <td>${cat.is_active ? '<span class="badge bg-success">Actif</span>' : '<span class="badge bg-danger">Inactif</span>'}</td>
            <td>
                <button class="btn btn-small btn-primary me-1" onclick="showTagCategoryModal('${cat.slug}')"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-small btn-danger" onclick="deleteTagCategory('${cat.slug}')"><i class="bi bi-trash"></i></button>
            </td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table class="table table-hover data-table">
            <thead><tr><th>Catégorie</th><th>Type</th><th>Source</th><th>Nb valeurs</th><th>Statut</th><th>Actions</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

// ─── Modal CRUD ───────────────────────────────────────────────────

let tagModalInstance = null;

async function showTagCategoryModal(slug) {
    const modalEl = document.getElementById('tagCategoryModal');
    if (!tagModalInstance) tagModalInstance = new bootstrap.Modal(modalEl);

    document.getElementById('tagModalTitle').textContent = slug ? 'Modifier la catégorie' : 'Nouvelle catégorie';
    document.getElementById('tagFormSlug').value = '';
    document.getElementById('tagFormLabel').value = '';
    document.getElementById('tagFormIsMultiple').checked = false;
    document.getElementById('tagFormSourceCsv').checked = true;
    document.getElementById('tagFormSourceM2m').checked = false;
    document.getElementById('tagFormSourceApp').value = '';
    document.getElementById('tagFormSourceAppGroup').classList.add('d-none');
    document.getElementById('tagFormValues').value = '';
    document.getElementById('tagFormSlug').disabled = false;

    await loadSourceAppsDropdown();

    if (slug) {
        try {
            const data = await api.getTagCategories();
            const cat = (data.categories || []).find(c => c.slug === slug);
            if (cat) {
                document.getElementById('tagFormSlug').value = cat.slug;
                document.getElementById('tagFormSlug').disabled = true;
                document.getElementById('tagFormLabel').value = cat.label;
                document.getElementById('tagFormIsMultiple').checked = cat.is_multiple;
                if (cat.source === 'm2m') {
                    document.getElementById('tagFormSourceM2m').checked = true;
                    document.getElementById('tagFormSourceAppGroup').classList.remove('d-none');
                    document.getElementById('tagFormSourceApp').value = cat.source_app_id || '';
                }
                document.getElementById('tagFormValues').value = (cat.values || []).join('\n');
            }
        } catch (e) {
            showToast('Erreur lors du chargement de la catégorie', 'danger');
            return;
        }
    }

    // Wrapper CSV import button
    const csvBtn = document.getElementById('btnImportCsvTag');
    if (csvBtn) csvBtn.onclick = () => handleImportTagCsv(slug);

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

function toggleTagSource() {
    const isM2m = document.getElementById('tagFormSourceM2m').checked;
    document.getElementById('tagFormSourceAppGroup').classList.toggle('d-none', !isM2m);
}

async function handleSaveTagCategory() {
    const slug = document.getElementById('tagFormSlug').value.trim();
    const label = document.getElementById('tagFormLabel').value.trim();
    const isMultiple = document.getElementById('tagFormIsMultiple').checked;
    const source = document.getElementById('tagFormSourceM2m').checked ? 'm2m' : 'csv';
    const sourceAppId = source === 'm2m' ? parseInt(document.getElementById('tagFormSourceApp').value) || null : null;
    const valuesRaw = document.getElementById('tagFormValues').value.trim();
    const values = valuesRaw ? valuesRaw.split('\n').map(v => v.trim()).filter(v => v) : [];

    if (!slug || !label) { showToast('Slug et label sont obligatoires', 'danger'); return; }

    try {
        const existingData = await api.getTagCategories();
        const existing = (existingData.categories || []).find(c => c.slug === slug);
        if (existing) {
            await api.updateTagCategory(slug, {
                label, is_multiple: isMultiple, values, is_active: existing.is_active
            });
            showToast('Catégorie mise à jour', 'success');
        } else {
            await api.createTagCategory({ slug, label, is_multiple: isMultiple, source, source_app_id: sourceAppId, values });
            showToast('Catégorie créée', 'success');
        }
        tagModalInstance.hide();
        loadUserTagsAdmin();
    } catch (error) {
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}

async function deleteTagCategory(slug) {
    if (!confirm(`Supprimer définitivement la catégorie "${slug}" ? Les sélections utilisateur seront perdues.`)) return;
    try {
        await api.deleteTagCategory(slug);
        showToast('Catégorie supprimée', 'success');
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
            showToast(`Import réussi : ${result.values ? result.values.length : 0} valeurs importées`, 'success');
            loadUserTagsAdmin();
        } catch (error) {
            showToast(error.message || 'Erreur lors de l\'import CSV', 'danger');
        }
    };
    input.click();
}

async function handleImportTagM2m(slug) {
    try {
        const result = await api.importTagM2m(slug);
        showToast(`Sync M2M réussie : ${result.values ? result.values.length : 0} valeurs importées`, 'success');
        loadUserTagsAdmin();
    } catch (error) {
        showToast(error.message || 'Erreur lors de la synchronisation M2M', 'danger');
    }
}
