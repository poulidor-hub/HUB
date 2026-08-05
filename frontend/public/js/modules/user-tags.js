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

// ===== Popup d'onboarding Préférences =====

let _prefDataCache = null;            // { categories, myTags, seenCategories }
let _prefOnboardingInstance = null;
let _prefOnboardingRaison = null;     // 'onboarding' | 'newcat' | null
let _prefOnboardingShownSlugs = [];   // slugs réellement affichés dans le popup

/** Charge (et cache) les données préférences du user courant. */
async function loadPreferencesData(force = false) {
    if (_prefDataCache && !force) return _prefDataCache;
    const [catsData, myData] = await Promise.all([
        api.getActiveTagCategories(),
        api.getMyTags(),
    ]);
    _prefDataCache = {
        categories: catsData.categories || [],
        myTags: myData.tags || {},
        seenCategories: myData.seen_categories || [],
    };
    return _prefDataCache;
}

/**
 * Met à jour la visibilité de l'entrée navbar Préférences.
 * Masquée pour les non-admins quand aucune catégorie active n'existe.
 */
function updatePreferencesNavVisibility() {
    const navLink = document.getElementById('navUserTags');
    if (!navLink) return;
    const hasCats = _prefDataCache && _prefDataCache.categories.length > 0;
    const isAdmin = !!currentUser?.is_admin;
    // Visible si admin (pour créer des catégories) OU si au moins une catégorie active
    navLink.classList.toggle('d-none', !(isAdmin || hasCats));
}

/** Point d'entrée appelé depuis showDashboard() après login. */
async function initPreferencesFlow() {
    try {
        await loadPreferencesData();
        updatePreferencesNavVisibility();
        // Légère temporisation pour laisser le dashboard se stabiliser
        setTimeout(() => maybeShowPreferencesOnboarding(), 600);
    } catch (e) {
        console.error('initPreferencesFlow:', e);
    }
}

/** Décide d'afficher ou non le popup, selon la logique B + Oui. */
async function maybeShowPreferencesOnboarding() {
    // Garde-fous
    if (typeof getPendingCallback === 'function' && getPendingCallback()) return;
    if (currentUser?.must_change_password) return;
    if (document.querySelector('.modal.show')) return;

    const data = await loadPreferencesData();
    const cats = data.categories;
    if (!cats.length) return;

    const seen = new Set(data.seenCategories);
    const aChoisi = Object.values(data.myTags).some(arr => Array.isArray(arr) && arr.length > 0);
    const nonVues = cats.filter(c => !seen.has(c.slug));

    const raison = (!aChoisi) ? 'onboarding'
                 : (nonVues.length > 0) ? 'newcat'
                 : null;
    if (!raison) return;

    // Session-guard : ne pas réafficher le popup onboarding déjà dismissé cette session
    if (raison === 'onboarding' && sessionStorage.getItem('prefOnboardingDismissed') === '1') return;

    const catsToShow = (raison === 'newcat') ? nonVues : cats;
    afficherPrefOnboarding(catsToShow, data.myTags, raison);
}

/** Affiche le modal avec les catégories données. */
function afficherPrefOnboarding(catsToShow, selectedMap, raison) {
    const modalEl = document.getElementById('preferencesOnboardingModal');
    if (!modalEl) return;
    if (!_prefOnboardingInstance) _prefOnboardingInstance = new bootstrap.Modal(modalEl);

    _prefOnboardingRaison = raison;
    _prefOnboardingShownSlugs = catsToShow.map(c => c.slug);

    const titleEl = document.getElementById('prefOnboardingTitle');
    const introEl = document.getElementById('prefOnboardingIntro');
    const fieldsEl = document.getElementById('prefOnboardingFields');

    if (raison === 'newcat') {
        const labels = catsToShow.map(c => escapeHtml(c.label)).join(', ');
        titleEl.innerHTML = '<i class="bi bi-stars me-1"></i>Nouvelle catégorie disponible';
        introEl.textContent = 'De nouvelles préférences sont disponibles : ' + labels + '.';
    } else {
        titleEl.innerHTML = '<i class="bi bi-sliders2 me-1"></i>Personnalisez votre expérience';
        introEl.textContent = 'Choisissez vos préférences pour affiner votre portail.';
    }

    fieldsEl.innerHTML = buildTagsFormHTML(catsToShow, selectedMap, 'prefOnb') ||
        '<p class="text-muted mb-0">Aucune préférence disponible pour le moment.</p>';

    _prefOnboardingInstance.show();
}

/** Bouton "Enregistrer mes choix" : enregistre les tags + ack toutes les cats affichées. */
async function handleSavePrefOnboarding() {
    const tags = collectTagsFromForm('#prefOnboardingFields');
    try {
        // 1. Enregistrer les sélections (fusion avec l'existant pour ne pas écraser les cats non affichées)
        const existing = _prefDataCache ? _prefDataCache.myTags : {};
        const merged = { ...existing, ...tags };
        await api.updateMyTags({ tags: merged });

        // 2. Ack toutes les catégories affichées (l'utilisateur a interagi)
        if (_prefOnboardingShownSlugs.length) {
            await api.acknowledgeMyTags(_prefOnboardingShownSlugs);
        }

        _prefOnboardingInstance.hide();
        showToast('Préférences enregistrées', 'success');

        // Invalider le cache pour les prochains calculs
        _prefDataCache = null;
        sessionStorage.removeItem('prefOnboardingDismissed');
    } catch (e) {
        showToast(e.message || 'Erreur lors de l\'enregistrement', 'danger');
    }
}

/** Au dismiss (fermeture / "Plus tard") : ack selon la raison. */
async function handleDismissPrefOnboarding() {
    const raison = _prefOnboardingRaison;
    const shownSlugs = _prefOnboardingShownSlugs.slice();
    _prefOnboardingRaison = null;
    _prefOnboardingShownSlugs = [];

    if (raison === 'onboarding') {
        // B : pas d'ack serveur ; le popup reviendra au prochain login tant que rien n'est choisi.
        sessionStorage.setItem('prefOnboardingDismissed', '1');
        return;
    }
    if (raison === 'newcat' && shownSlugs.length) {
        // Oui : ack persistant pour ne pas resurgir pour ces catégories.
        try {
            await api.acknowledgeMyTags(shownSlugs);
        } catch (e) {
            console.error('acknowledge failed:', e);
        }
    }
}

// Brancher le handler de fermeture une fois le DOM prêt
document.addEventListener('DOMContentLoaded', () => {
    const modalEl = document.getElementById('preferencesOnboardingModal');
    if (modalEl) {
        modalEl.addEventListener('hidden.bs.modal', handleDismissPrefOnboarding);
    }
});
