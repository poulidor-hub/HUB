// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * user-tags.js
 * Helpers mutualisés pour les formulaires de préférences (tags utilisateur).
 * Consommé par : le popup d'onboarding (Task 6), l'onglet admin "Mes préférences",
 * et la modale d'édition admin d'un utilisateur.
 */

/**
 * Construit le HTML des champs d'un formulaire de préférences.
 * Wrapper conservé pour les call sites existants (popup onboarding,
 * onglet "Mes préférences", modale admin d'un utilisateur).
 * @param {Array} categories - catégories actives ({slug,label,is_multiple,values})
 * @param {Object} selectedMap - { slug: [values] } sélectionnées
 * @param {string} idPrefix - préfixe d'id pour éviter les collisions DOM
 * @returns {string} HTML
 */
function buildTagsFormHTML(categories, selectedMap, idPrefix) {
    if (!Array.isArray(categories) || categories.length === 0) return '';
    return categories
        .map(cat => buildPrefFieldHTML(cat, selectedMap[cat.slug] || [], idPrefix))
        .join('');
}

// ===== Composants de sélection (chips / sélecteur avec recherche) =====

/** Seuil de bascule chips → sélecteur avec recherche. */
const PREF_SELECT_THRESHOLD = 12;

/** Normalisation pour recherche : minuscules + sans accents (NFD). */
function normalizeSearch(text) {
    return String(text || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase();
}

/** escapeHtml n'échappe pas les guillemets — requis pour les attributs HTML. */
function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;');
}

/** Zone chips sélectionnées d'un sélecteur (HTML interne, sans le compteur). */
function _prefSelectedChipsHTML(slug, values) {
    if (!values.length) return '<span class="pref-select-hint">Aucune sélection</span>';
    return values.map(val => `<span class="pref-chip-sm">${escapeHtml(val)}
        <button type="button" class="pref-chip-x" aria-label="Retirer ${escapeAttr(val)}"
                data-action="prefSelectRemove" data-arg1="$el" data-arg2="${slug}" data-arg3="${escapeAttr(val)}"><i class="bi bi-x-lg" aria-hidden="true"></i></button>
    </span>`).join('');
}

/**
 * Construit le HTML du champ de sélection d'une catégorie.
 * - ≤ PREF_SELECT_THRESHOLD valeurs → chips cliquables (inputs natifs masqués)
 * - > PREF_SELECT_THRESHOLD valeurs → sélecteur avec recherche
 */
function buildPrefFieldHTML(cat, selected, idPrefix) {
    const sel = Array.isArray(selected) ? selected : [];
    const inputType = cat.is_multiple ? 'checkbox' : 'radio';
    const name = idPrefix + '_' + cat.slug;
    const values = cat.values || [];

    if (values.length <= PREF_SELECT_THRESHOLD) {
        const chips = values.map(val => {
            const checked = sel.includes(val) ? ' checked' : '';
            return `<label class="pref-chip">
                <input type="${inputType}" name="${name}" value="${escapeAttr(val)}" data-category="${cat.slug}"${checked}>
                <i class="bi bi-check-lg pref-chip-check" aria-hidden="true"></i><span>${escapeHtml(val)}</span>
            </label>`;
        }).join('');
        return `<div class="pref-chips" role="group" aria-label="${escapeAttr(cat.label)}">${chips}</div>`;
    }

    const options = values.map(val => {
        const checked = sel.includes(val) ? ' checked' : '';
        return `<label class="pref-select-option">
            <input type="${inputType}" name="${name}" value="${escapeAttr(val)}" data-category="${cat.slug}"${checked}>
            <span>${escapeHtml(val)}</span>
        </label>`;
    }).join('');

    return `<div class="pref-select" data-pref-slug="${cat.slug}">
        <div class="pref-select-selected">${_prefSelectedChipsHTML(cat.slug, sel)}
            <span class="pref-select-count">${sel.length}/${values.length} sélectionné${sel.length > 1 ? 's' : ''}</span>
        </div>
        <input type="search" class="form-control pref-select-search" placeholder="Rechercher…"
               aria-label="Rechercher dans ${escapeAttr(cat.label)}"
               data-action="prefSelectSearch" data-arg1="$el">
        <div class="pref-select-list">${options}</div>
        <div class="pref-select-noresult d-none">Aucun résultat</div>
        <div class="pref-select-footer">
            <span class="pref-select-results">${values.length} valeurs</span>
            ${cat.is_multiple ? `<button type="button" class="pref-select-clear" data-action="prefSelectClear" data-arg1="$el" data-arg2="${cat.slug}">Tout effacer</button>` : ''}
        </div>
    </div>`;
}

// ─── Interactions (délégation data-action) ────────────────────────

/** Recherche instantanée insensible à la casse et aux accents (event input). */
function prefSelectSearch(inputEl) {
    const field = inputEl.closest('.pref-select');
    if (!field) return;
    const q = normalizeSearch(inputEl.value);
    let visible = 0;
    const opts = field.querySelectorAll('.pref-select-option');
    opts.forEach(opt => {
        const match = normalizeSearch(opt.textContent).includes(q);
        opt.classList.toggle('d-none', !match);
        if (match) visible++;
    });
    const noresult = field.querySelector('.pref-select-noresult');
    if (noresult) noresult.classList.toggle('d-none', visible !== 0);
    const results = field.querySelector('.pref-select-results');
    if (results) results.textContent = visible + ' résultat' + (visible > 1 ? 's' : '') + ' sur ' + opts.length;
}

/** Re-rend la zone chips sélectionnées + compteur d'un sélecteur. */
function prefSelectRefresh(field) {
    const slug = field.getAttribute('data-pref-slug');
    const values = [...field.querySelectorAll('.pref-select-list input:checked')].map(i => i.value);
    const total = field.querySelectorAll('.pref-select-list input').length;
    field.querySelector('.pref-select-selected').innerHTML =
        _prefSelectedChipsHTML(slug, values) +
        `<span class="pref-select-count">${values.length}/${total} sélectionné${values.length > 1 ? 's' : ''}</span>`;
}

/** Bouton ✕ d'une chip sélectionnée (décoche l'input + synchronise). */
function prefSelectRemove(btnEl, slug, value) {
    const field = btnEl.closest('.pref-select');
    if (!field) return;
    const input = [...field.querySelectorAll('.pref-select-list input')].find(i => i.value === value);
    if (input) input.checked = false;
    prefSyncFieldByContainer(field);
}

/** Lien « Tout effacer » (catégories multiples uniquement). */
function prefSelectClear(btnEl, slug) {
    const field = btnEl.closest('.pref-select');
    if (!field) return;
    field.querySelectorAll('.pref-select-list input:checked').forEach(i => { i.checked = false; });
    prefSyncFieldByContainer(field);
}

/** Change natif d'un input chips/sélecteur (souris ou clavier). */
function prefFieldChange(inputEl) {
    prefSyncFieldByContainer(inputEl.closest('.pref-select, .pref-card'));
}

// Listener dédié : PAS de data-action sur ces inputs — le délégué click global
// (app.js) fait preventDefault() sur tout [data-action], ce qui annulerait le
// toggle natif des checkbox/radio. On écoute donc l'event change natif ici.
document.addEventListener('change', (e) => {
    if (e.target.matches('.pref-chip input[data-category], .pref-select-option input[data-category]')) {
        prefFieldChange(e.target);
    }
});

/** Synchronise l'UI après tout changement (natif ou programmatique). */
function prefSyncFieldByContainer(field) {
    if (!field) return;
    if (field.classList.contains('pref-select')) prefSelectRefresh(field);
    const pageRoot = field.closest('[data-pref-autosave]');
    if (pageRoot) {
        updatePrefSummaryDots(pageRoot);
        prefAutosave(pageRoot);
    }
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

// ===== Page « Mes préférences » (sommaire sticky + autosave) =====

let _prefObserver = null;
let _prefSaveTimer = null;
let _prefSaveSeq = 0;
let _prefStatusTimer = null;

/** Indicateur d'état d'enregistrement (dans l'en-tête de page). */
function setPrefSaveStatus(state) {
    const el = document.getElementById('prefSaveStatus');
    if (!el) return;
    clearTimeout(_prefStatusTimer);
    if (state === 'pending') {
        el.className = 'pref-save-status';
        el.innerHTML = '<i class="bi bi-arrow-repeat" aria-hidden="true"></i> Enregistrement…';
        return;
    }
    if (state === 'saved') {
        el.className = 'pref-save-status saved';
        el.innerHTML = '<i class="bi bi-check-lg" aria-hidden="true"></i> Enregistré';
        _prefStatusTimer = setTimeout(() => setPrefSaveStatus('idle'), 2000);
        return;
    }
    if (state === 'error') {
        el.className = 'pref-save-status error';
        el.innerHTML = '<i class="bi bi-exclamation-triangle" aria-hidden="true"></i> Échec de l\'enregistrement';
        return;
    }
    // idle
    el.className = 'pref-save-status';
    el.textContent = '';
}

/** Autosave debouncé : relit tout le formulaire et envoie l'objet complet. */
function prefAutosave(pageRoot) {
    setPrefSaveStatus('pending');
    clearTimeout(_prefSaveTimer);
    _prefSaveTimer = setTimeout(async () => {
        const tags = collectTagsFromForm('#' + pageRoot.id);
        const seq = ++_prefSaveSeq;
        try {
            await api.updateMyTags({ tags });
            if (seq === _prefSaveSeq) setPrefSaveStatus('saved');
        } catch (e) {
            if (seq === _prefSaveSeq) {
                setPrefSaveStatus('error');
                showToast('Erreur', e.message || 'Échec de l\'enregistrement des préférences', 'danger');
            }
        }
    }, 600);
}

/** Pastilles du sommaire : remplies si ≥ 1 choix dans la catégorie. */
function updatePrefSummaryDots(pageRoot) {
    const summary = document.getElementById('prefSummary');
    if (!summary) return;
    pageRoot.querySelectorAll('.pref-card[data-slug]').forEach(card => {
        const item = summary.querySelector(`.pref-summary-item[data-slug="${card.dataset.slug}"]`);
        if (item) item.classList.toggle('done', !!card.querySelector('input:checked'));
    });
}

/** Entrée de sommaire → scroll doux vers la carte. */
function prefScrollTo(slug) {
    const card = document.getElementById('prefCard_' + slug);
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/** Rend la page complète (sommaire + cartes) à partir des catégories et tags. */
function renderPrefsPage(categories, myTags) {
    const summaryBody = document.querySelector('#prefSummary .pref-summary-body');
    const sections = document.getElementById('prefSections');
    if (!summaryBody || !sections) return;

    if (_prefObserver) { _prefObserver.disconnect(); _prefObserver = null; }

    if (!categories.length) {
        const msg = (typeof currentUser !== 'undefined' && currentUser?.is_admin)
            ? 'Aucune préférence définie. Créez-en une dans l\'onglet Catégories.'
            : 'Aucune préférence disponible pour le moment.';
        summaryBody.innerHTML = '';
        sections.innerHTML = `<div class="data-empty">${msg}</div>`;
        return;
    }

    summaryBody.innerHTML = categories.map(cat => `
        <a href="#" class="pref-summary-item" data-slug="${cat.slug}"
           data-action="prefScrollTo" data-arg1="${cat.slug}">
            <span class="pref-summary-dot" aria-hidden="true"></span>${escapeHtml(cat.label)}
        </a>`).join('');

    sections.innerHTML = categories.map(cat => `
        <div class="pref-card card" id="prefCard_${cat.slug}" data-slug="${cat.slug}">
            <div class="card-body">
                <div class="pref-card-head">
                    <h2 class="pref-card-title">${escapeHtml(cat.label)}</h2>
                    <span class="pref-card-type">${cat.is_multiple ? 'plusieurs choix' : '1 choix'}</span>
                </div>
                ${buildPrefFieldHTML(cat, myTags[cat.slug] || [], 'page')}
            </div>
        </div>`).join('');

    updatePrefSummaryDots(document.getElementById('prefPageRoot'));
    setPrefSaveStatus('idle');

    // Surlignage de la section visible dans le sommaire
    _prefObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            const slug = entry.target.getAttribute('data-slug');
            const item = document.querySelector(`#prefSummary .pref-summary-item[data-slug="${slug}"]`);
            if (!item || item.classList.contains('active')) return;
            document.querySelectorAll('#prefSummary .pref-summary-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
        });
    }, { root: null, rootMargin: '-84px 0px -60% 0px', threshold: 0 });
    sections.querySelectorAll('.pref-card').forEach(c => _prefObserver.observe(c));
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

        _prefOnboardingRaison = null;
        _prefOnboardingShownSlugs = [];
        _prefOnboardingInstance.hide();
        showToast('Préférences enregistrées', '', 'success');

        // Invalider le cache pour les prochains calculs
        _prefDataCache = null;
        sessionStorage.removeItem('prefOnboardingDismissed');
    } catch (e) {
        showToast('Erreur', e.message || 'Erreur lors de l\'enregistrement', 'danger');
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
