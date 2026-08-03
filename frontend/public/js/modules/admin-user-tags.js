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
    const rows = categories.map(cat => `
        <tr>
            <td><strong>${escapeHtml(cat.label)}</strong></td>
            <td>${cat.is_multiple ? '<span class="badge bg-info">Multiples</span>' : '<span class="badge bg-secondary">Unique</span>'}</td>
            <td>${(cat.values || []).length} choix</td>
            <td>${cat.is_active ? '<span class="badge bg-success">Actif</span>' : '<span class="badge bg-danger">Inactif</span>'}</td>
            <td>
                <button class="btn btn-sm btn-primary me-1" onclick="showTagCategoryModal('${cat.slug}')"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-danger" onclick="deleteTagCategory('${cat.slug}')"><i class="bi bi-trash"></i></button>
            </td>
        </tr>
    `).join('');
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
    document.getElementById('tagFormValues').value = '';

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
            }
        } catch (e) {
            showToast('Erreur lors du chargement', 'danger');
            return;
        }
    }

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
    const label = document.getElementById('tagFormLabel').value.trim();
    const isMultiple = document.getElementById('tagFormIsMultiple').checked;
    const source = document.getElementById('tagFormSourceM2m').checked ? 'm2m' : 'csv';
    const sourceAppId = source === 'm2m' ? parseInt(document.getElementById('tagFormSourceApp').value) || null : null;
    const valuesRaw = document.getElementById('tagFormValues').value.trim();
    const values = valuesRaw ? valuesRaw.split('\n').map(v => v.trim()).filter(v => v) : [];

    if (!label) { showToast('Le nom de la préférence est obligatoire', 'danger'); return; }

    try {
        const existingData = await api.getTagCategories();
        const existing = (existingData.categories || []).find(c => c.label === label);
        const slug = existing ? existing.slug : slugify(label);

        if (existing) {
            await api.updateTagCategory(slug, {
                label, is_multiple: isMultiple, values, is_active: existing.is_active
            });
            showToast('Préférence mise à jour', 'success');
        } else {
            if (!slug) { showToast('Le nom ne permet pas de générer un identifiant valide', 'danger'); return; }
            await api.createTagCategory({ slug, label, is_multiple: isMultiple, source, source_app_id: sourceAppId, values });
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

        const sections = categories.map(cat => {
            const selected = myTags[cat.slug] || [];
            const inputType = cat.is_multiple ? 'checkbox' : 'radio';
            const inputName = 'admin_tag_' + cat.slug;

            const options = (cat.values || []).map(val => {
                const checked = selected.includes(val) ? 'checked' : '';
                const id = 'admin_tag_' + cat.slug + '_' + val.replace(/[^a-zA-Z0-9]/g, '_');
                return `<div class="form-check form-check-inline">
                    <input class="form-check-input" type="${inputType}" name="${inputName}"
                        id="${id}" value="${escapeHtml(val)}" ${checked} data-category="${cat.slug}">
                    <label class="form-check-label" for="${id}">${escapeHtml(val)}</label>
                </div>`;
            }).join('');

            return `<div class="mb-3">
                <label class="form-label fw-bold">${escapeHtml(cat.label)}</label>
                <div>${options}</div>
            </div>`;
        }).join('');

        fieldsContainer.innerHTML = sections;
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
