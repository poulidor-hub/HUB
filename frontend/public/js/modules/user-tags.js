/**
 * user-tags.js
 * Helpers mutualisés pour les formulaires de préférences (tags utilisateur).
 * Consommé par : le popup d'onboarding (Task 6), l'onglet admin "Mes préférences",
 * et la modale d'édition admin d'un utilisateur.
 */

/**
 * Construit le HTML des champs d'un formulaire de préférences.
 * - cat.is_multiple = true  -> checkboxes
 * - cat.is_multiple = false -> radios
 * @param {Array} categories - catégories actives ({slug,label,is_multiple,values})
 * @param {Object} selectedMap - { slug: [values] } sélectionnées
 * @param {string} idPrefix - préfixe d'id pour éviter les collisions DOM
 * @returns {string} HTML
 */
function buildTagsFormHTML(categories, selectedMap, idPrefix) {
    if (!Array.isArray(categories) || categories.length === 0) return '';

    return categories.map(cat => {
        const selected = selectedMap[cat.slug] || [];
        const inputType = cat.is_multiple ? 'checkbox' : 'radio';
        const inputName = idPrefix + '_' + cat.slug;

        const options = (cat.values || []).map(val => {
            const checked = selected.includes(val) ? 'checked' : '';
            const id = idPrefix + '_' + cat.slug + '_' + val.replace(/[^a-zA-Z0-9]/g, '_');
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
}

/**
 * Lit un conteneur de formulaire et retourne les sélections { slug: [values] }.
 * @param {string} containerSelector - sélecteur CSS du conteneur (ex: '#prefOnboardingFields')
 * @returns {Object} { category_slug: [values] }
 */
function collectTagsFromForm(containerSelector) {
    const tags = {};
    document.querySelectorAll(`${containerSelector} input:checked`).forEach(input => {
        const cat = input.getAttribute('data-category');
        if (!cat) return;
        if (!tags[cat]) tags[cat] = [];
        tags[cat].push(input.value);
    });
    return tags;
}
