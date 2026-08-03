/**
 * user-tags.js
 * Widget dashboard : tags utilisateur
 */

async function loadUserTagsWidget() {
    const container = document.getElementById('userTagsWidget');
    if (!container) return;

    try {
        const [categoriesData, myTagsData] = await Promise.all([
            api.getActiveTagCategories(), api.getMyTags()
        ]);
        const categories = categoriesData.categories || [];
        const myTags = myTagsData.tags || {};

        if (!categories.length) { container.classList.add('d-none'); return; }
        container.classList.remove('d-none');
        renderUserTagsWidget(container, categories, myTags);
    } catch (error) {
        console.error('Erreur chargement tags utilisateur:', error);
        container.classList.add('d-none');
    }
}

function renderUserTagsWidget(container, categories, myTags) {
    const sections = categories.map(cat => {
        const selected = myTags[cat.slug] || [];
        const inputType = cat.is_multiple ? 'checkbox' : 'radio';
        const inputName = 'tag_' + cat.slug;

        const options = (cat.values || []).map(val => {
            const checked = selected.includes(val) ? 'checked' : '';
            const id = 'tag_' + cat.slug + '_' + val.replace(/[^a-zA-Z0-9]/g, '_');
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

    container.innerHTML = `<div class="card">
        <h2>Mes préférences</h2>
        <div id="userTagsFormFields">${sections}</div>
        <div class="mt-3">
            <button class="btn btn-primary" onclick="handleSaveUserTags()">
                <i class="bi bi-check-lg me-1"></i>Enregistrer mes choix
            </button>
            <span id="userTagsFeedback" class="ms-2 small"></span>
        </div>
    </div>`;
}

async function handleSaveUserTags() {
    const feedback = document.getElementById('userTagsFeedback');
    if (feedback) { feedback.textContent = ''; }

    const tags = {};
    document.querySelectorAll('#userTagsFormFields input:checked').forEach(input => {
        const cat = input.getAttribute('data-category');
        if (!tags[cat]) tags[cat] = [];
        tags[cat].push(input.value);
    });

    try {
        await api.updateMyTags({ tags });
        if (feedback) { feedback.textContent = 'Préférences enregistrées'; feedback.className = 'ms-2 small text-success'; }
        showToast('Préférences enregistrées', 'success');
    } catch (error) {
        if (feedback) { feedback.textContent = error.message || 'Erreur'; feedback.className = 'ms-2 small text-danger'; }
        showToast(error.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}
