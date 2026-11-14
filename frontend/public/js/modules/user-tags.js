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

// ===== Secteur principal (radios ★ parmi les valeurs cochées en ①) =====

/** Métadonnées des catégories primary_enabled vues à la construction (slug → catégorie). */
const _prefPrimaryCats = {};

/**
 * Construit la section « Secteur principal » d'une catégorie primary_enabled :
 * radios ★ (même style chips) parmi les valeurs cochées. Affiche un message
 * tant qu'aucune valeur n'est cochée en ①.
 * @param {Object} cat - catégorie ({slug, label, ...})
 * @param {Array} selectedValues - valeurs cochées en ①
 * @param {string|null} primaryValue - primaire courante (null = aucune)
 * @param {string} idPrefix - préfixe du groupe radio (unique par conteneur :
 *        page / modale) pour éviter tout couplage document-wide des radios
 * @returns {string} HTML
 */
function buildPrimaryFieldHTML(cat, selectedValues, primaryValue, idPrefix = 'prefPrimary') {
    const slug = cat.slug;
    if (slug) _prefPrimaryCats[slug] = cat;
    const prefix = idPrefix || 'prefPrimary';
    const sel = Array.isArray(selectedValues) ? selectedValues : [];
    const groupName = prefix + '_primary_' + slug;
    const body = sel.length === 0
        ? '<p class="pref-primary-empty text-muted small mb-0">Cochez d\'abord vos choix ci-dessus pour désigner un secteur principal.</p>'
        : `<div class="pref-chips">${sel.map(val => {
            const checked = val === primaryValue ? ' checked' : '';
            return `<label class="pref-chip">
                <input type="radio" name="${groupName}" value="${escapeAttr(val)}" data-primary-category="${escapeAttr(slug)}"${checked}>
                <i class="bi bi-star-fill pref-chip-check" aria-hidden="true"></i><span>${escapeHtml(val)}</span>
            </label>`;
        }).join('')}</div>`;
    return `<div class="pref-primary mt-2" data-primary-slug="${escapeAttr(slug)}" data-primary-prefix="${escapeAttr(prefix)}" role="radiogroup" aria-label="Secteur principal — ${escapeAttr(cat.label || slug)}">
        <div class="text-secondary small mb-1"><i class="bi bi-star-fill text-warning me-1" aria-hidden="true"></i>Secteur principal</div>
        ${body}
    </div>`;
}

/**
 * Lit les radios « secteur principal » cochés d'un conteneur.
 * Les radios portent data-primary-category (et non data-category) pour rester
 * hors de collectTagsFromForm — la primaire appartient au payload `primary`.
 * @param {Element} rootEl - conteneur (défaut : document)
 * @returns {Object} { category_slug: value }
 */
function collectPrimaryFromForm(rootEl) {
    const scope = (rootEl && typeof rootEl.querySelectorAll === 'function') ? rootEl : document;
    const primary = {};
    scope.querySelectorAll('.pref-primary input[type="radio"]:checked').forEach(input => {
        const cat = input.getAttribute('data-primary-category');
        if (cat && input.value) primary[cat] = input.value;
    });
    return primary;
}

/**
 * Reconstruit les radios « principal » d'une catégorie après un changement des
 * chips/sélecteur : la primaire ne peut viser que les valeurs encore cochées.
 * La sélection courante est conservée si elle reste cochée.
 * @param {Element} el - input modifié (data-category) ou champ (.pref-select)
 */
function prefRebuildPrimaryAround(el) {
    if (!el) return;
    const slug = el.getAttribute('data-category') || el.getAttribute('data-pref-slug');
    if (!slug) return;
    const root = el.closest('[data-pref-root]');
    if (!root) return;
    const container = root.querySelector(`.pref-primary[data-primary-slug="${CSS.escape(slug)}"]`);
    if (!container) return; // catégorie sans section primaire (primary_enabled=0)
    const selected = [...root.querySelectorAll(`input[data-category="${CSS.escape(slug)}"]:checked`)].map(i => i.value);
    const prev = container.querySelector('input[type="radio"]:checked');
    const primary = (prev && selected.includes(prev.value)) ? prev.value : null;
    container.innerHTML = buildPrimaryFieldHTML(
        _prefPrimaryCats[slug] || { slug, label: slug },
        selected,
        primary,
        container.getAttribute('data-primary-prefix') || 'prefPrimary'
    );
    // Notifie le conteneur (la modale de préférences met à jour son bouton).
    root.dispatchEvent(new CustomEvent('pref:changed', { bubbles: true }));
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
    prefRebuildPrimaryAround(field);
}

/** Lien « Tout effacer » (catégories multiples uniquement). */
function prefSelectClear(btnEl, slug) {
    const field = btnEl.closest('.pref-select');
    if (!field) return;
    field.querySelectorAll('.pref-select-list input:checked').forEach(i => { i.checked = false; });
    prefSyncFieldByContainer(field);
    prefRebuildPrimaryAround(field);
}

/** Change natif d'un input chips/sélecteur (souris ou clavier). */
function prefFieldChange(inputEl) {
    prefSyncFieldByContainer(inputEl.closest('.pref-select, .pref-card'));
    prefRebuildPrimaryAround(inputEl);
}

// Listener dédié : PAS de data-action sur ces inputs — le délégué click global
// (app.js) fait preventDefault() sur tout [data-action], ce qui annulerait le
// toggle natif des checkbox/radio. On écoute donc l'event change natif ici.
document.addEventListener('change', (e) => {
    if (e.target.matches('.pref-chip input[data-category], .pref-select-option input[data-category]')) {
        prefFieldChange(e.target);
    } else if (e.target.matches('.pref-primary input[type="radio"]')) {
        // Radio « secteur principal » : pas de rebuild (les radios ne filtrent
        // pas les chips), juste l'autosave de la page le cas échéant.
        const pageRoot = e.target.closest('[data-pref-autosave]');
        if (pageRoot) prefAutosave(pageRoot);
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

/**
 * Autosave debouncé : relit tout le formulaire (tags + primaire) et envoie
 * l'objet complet. Resynchronise le cache local depuis la réponse serveur et
 * signale les valeurs obsolètes rejetées (`ignored`).
 */
function prefAutosave(pageRoot) {
    setPrefSaveStatus('pending');
    clearTimeout(_prefSaveTimer);
    _prefSaveTimer = setTimeout(async () => {
        const tags = collectTagsFromForm('#' + pageRoot.id);
        const primary = collectPrimaryFromForm(pageRoot);
        const seq = ++_prefSaveSeq;
        try {
            const resp = await api.updateMyTags({ tags, primary });
            if (seq === _prefSaveSeq) {
                setPrefSaveStatus('saved');
                if (_prefDataCache) {
                    _prefDataCache.myTags = resp.tags || tags;
                    _prefDataCache.primary = resp.primary || {};
                }
                prefNotifyIgnored(resp.ignored);
            }
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

/**
 * Rend la page complète (sommaire + cartes) à partir des catégories et tags.
 * @param {Array} categories - catégories actives (avec primary_enabled + source_flag)
 * @param {Object} myTags - { slug: [values] } cochées
 * @param {Object} primaryMap - { slug: value } primaires courantes
 */
function renderPrefsPage(categories, myTags, primaryMap) {
    const summaryBody = document.querySelector('#prefSummary .pref-summary-body');
    const sections = document.getElementById('prefSections');
    if (!summaryBody || !sections) return;
    const primary = primaryMap || {};

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

    // Bandeau discret si au moins une catégorie est servie depuis le cache
    // (exemple-app injoignable — spec §6.2).
    const cacheBanner = categories.some(cat => cat.source_flag === 'cache')
        ? `<div class="alert alert-info py-2 px-3 mb-3" role="status">
               <i class="bi bi-cloud-slash me-1" aria-hidden="true"></i>
               Valeurs en cache — exemple-app injoignable, synchronisation dès son retour.
           </div>`
        : '';

    sections.innerHTML = cacheBanner + categories.map(cat => {
        // Section « Secteur principal » sous les chips pour les catégories
        // primary_enabled ; radios limitées aux valeurs actives affichées.
        const liveSelected = (myTags[cat.slug] || []).filter(v => (cat.values || []).includes(v));
        const primaryField = cat.primary_enabled
            ? buildPrimaryFieldHTML(cat, liveSelected, primary[cat.slug] || null, 'page')
            : '';
        return `
        <div class="pref-card card" id="prefCard_${cat.slug}" data-slug="${cat.slug}"${cat.primary_enabled ? ' data-pref-root="1"' : ''}>
            <div class="card-body">
                <div class="pref-card-head">
                    <h2 class="pref-card-title">${escapeHtml(cat.label)}</h2>
                    <span class="pref-card-type">${cat.is_multiple ? 'plusieurs choix' : '1 choix'}</span>
                </div>
                ${buildPrefFieldHTML(cat, myTags[cat.slug] || [], 'page')}
                ${primaryField}
            </div>
        </div>`;
    }).join('');

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

let _prefDataCache = null;            // { categories, myTags, primary, seenCategories, onboardingReason }
let _prefOnboardingInstance = null;
let _prefOnboardingRaison = null;     // 'onboarding' | 'newcat' | 'primary_missing' | 'primary_lost' | null
let _prefOnboardingShownSlugs = [];   // slugs réellement affichés dans le popup

/** Charge (et cache) les données préférences du user courant (raison serveur incluse). */
async function loadPreferencesData(force = false) {
    if (_prefDataCache && !force) return _prefDataCache;
    const [catsData, myData] = await Promise.all([
        api.getActiveTagCategories(),
        api.getMyTags(true), // onboarding_reason calculé serveur (spec §5)
    ]);
    _prefDataCache = {
        categories: catsData.categories || [],
        myTags: myData.tags || {},
        primary: myData.primary || {},
        seenCategories: myData.seen_categories || [],
        onboardingReason: myData.onboarding_reason ?? null,
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

/**
 * Décide d'afficher ou non la modale selon la raison calculée SERVEUR
 * (GET /my?with_reason=1 → onboarding_reason, spec §5). Le client n'est
 * qu'un exécutant : null → aucune modale, quoi qu'il ait pu se passer
 * dans la session (fini le sessionStorage prefOnboardingDismissed).
 */
async function maybeShowPreferencesOnboarding() {
    // Garde-fous inchangés (callback en cours, mot de passe à changer, modale ouverte)
    if (typeof getPendingCallback === 'function' && getPendingCallback()) return;
    if (currentUser?.must_change_password) return;
    if (document.querySelector('.modal.show')) return;

    const data = await loadPreferencesData();
    const cats = data.categories;
    if (!cats.length) return;

    const raison = data.onboardingReason;
    if (!raison) return; // choix complet + primaire valide + rien de nouveau

    const catsToShow = (raison === 'newcat')
        ? cats.filter(c => !data.seenCategories.includes(c.slug))
        : cats;
    if (!catsToShow.length) return;

    afficherPrefOnboarding(catsToShow, data.myTags, raison);
}

/** Bandeau Bootstrap des états ciblés (spec §5 : « n'existe plus, re-désignez »). */
function _prefOnboardingBanner(raison) {
    if (raison === 'primary_lost') {
        return `<div class="alert alert-warning py-2 px-3 mb-3" role="alert">
            <i class="bi bi-exclamation-triangle-fill me-1" aria-hidden="true"></i>
            Votre secteur principal n'existe plus chez la source : re-désignez-en un parmi vos secteurs.
        </div>`;
    }
    if (raison === 'primary_missing') {
        return `<div class="alert alert-warning py-2 px-3 mb-3" role="alert">
            <i class="bi bi-star-fill me-1" aria-hidden="true"></i>
            Il ne manque qu'une chose : désignez votre secteur principal.
        </div>`;
    }
    return '';
}

/**
 * Affiche la modale (2 temps) : ① secteurs (chips, composant existant) puis
 * ② secteur principal (radios parmi les cochées).
 * États ciblés primary_missing / primary_lost : ① réduite en lecture seule
 * (fieldset disabled — les tags sont conservés) + bandeau warning.
 * @param {Array} catsToShow - catégories affichées
 * @param {Object} selectedMap - { slug: [values] } cochées
 * @param {string} raison - 'onboarding' | 'newcat' | 'primary_missing' | 'primary_lost'
 */
function afficherPrefOnboarding(catsToShow, selectedMap, raison) {
    const modalEl = document.getElementById('preferencesOnboardingModal');
    if (!modalEl) return;
    if (!_prefOnboardingInstance) _prefOnboardingInstance = new bootstrap.Modal(modalEl);

    _prefOnboardingRaison = raison;
    _prefOnboardingShownSlugs = catsToShow.map(c => c.slug);

    const titleEl = document.getElementById('prefOnboardingTitle');
    const introEl = document.getElementById('prefOnboardingIntro');
    const fieldsEl = document.getElementById('prefOnboardingFields');

    const targeted = (raison === 'primary_missing' || raison === 'primary_lost');
    const primaryMap = (_prefDataCache && _prefDataCache.primary) || {};

    if (raison === 'newcat') {
        const labels = catsToShow.map(c => escapeHtml(c.label)).join(', ');
        titleEl.innerHTML = '<i class="bi bi-stars me-1"></i>Nouvelle catégorie disponible';
        introEl.textContent = 'De nouvelles préférences sont disponibles : ' + labels + '.';
    } else if (raison === 'primary_lost') {
        titleEl.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Secteur principal à re-désigner';
        introEl.textContent = 'Vos secteurs gérés sont conservés, mais le principal désigné n\'existe plus.';
    } else if (raison === 'primary_missing') {
        titleEl.innerHTML = '<i class="bi bi-star me-1"></i>Désignez votre secteur principal';
        introEl.textContent = 'Vos secteurs gérés sont conservés : désignez votre favori pour finaliser.';
    } else {
        titleEl.innerHTML = '<i class="bi bi-sliders2 me-1"></i>Personnalisez votre expérience';
        introEl.textContent = 'Choisissez vos secteurs gérés puis votre secteur principal.';
    }

    // ① Mes secteurs (composant chips/sélecteur existant ; read-only si état ciblé)
    const step1HTML = buildTagsFormHTML(catsToShow, selectedMap, 'prefOnb');
    const step1Label = '<div class="text-secondary small mb-1"><i class="bi bi-check2-square me-1" aria-hidden="true"></i>Mes secteurs' + (targeted ? ' (conservés)' : '') + '</div>';
    const step1 = step1HTML
        ? (targeted
            ? `<fieldset disabled aria-label="Secteurs conservés (non modifiables ici)">${step1Label}${step1HTML}</fieldset>`
            : step1Label + step1HTML)
        : '';

    // ② Secteur principal : radios parmi les cochées, par catégorie primary_enabled affichée.
    //    Filtrées sur les valeurs actives affichées en ① (stored ∩ live) : une primaire
    //    obsolète ne doit jamais être re-proposée, le PUT la filtrerait en `ignored`.
    const step2 = catsToShow.filter(c => c.primary_enabled)
        .map(cat => {
            const liveSelected = (selectedMap[cat.slug] || [])
                .filter(v => (cat.values || []).includes(v));
            return buildPrimaryFieldHTML(cat, liveSelected, primaryMap[cat.slug] || null, 'prefOnb');
        })
        .join('');

    const body = _prefOnboardingBanner(raison) + step1 + step2;
    fieldsEl.innerHTML = body
        ? `<div data-pref-root="1">${body}</div>`
        : '<p class="text-muted mb-0">Aucune préférence disponible pour le moment.</p>';

    _prefOnboardingInstance.show();
    updatePrefOnboardingSaveState();
}

/**
 * État du bouton « Enregistrer mes choix » : actif seulement avec ① au moins
 * une valeur cochée ET ② une primaire pour chaque catégorie primary_enabled
 * affichée. (Les chips d'un fieldset disabled restent collectées — état ciblé
 * primary_missing/primary_lost : ① est déjà satisfaite.)
 */
function updatePrefOnboardingSaveState() {
    const root = document.getElementById('prefOnboardingFields');
    const btn = document.querySelector('#preferencesOnboardingModal [data-action="handleSavePrefOnboarding"]');
    if (!root || !btn) return;
    const tags = collectTagsFromForm('#prefOnboardingFields');
    const primary = collectPrimaryFromForm(root);
    const needPrimary = [...root.querySelectorAll('.pref-primary[data-primary-slug]')]
        .map(el => el.getAttribute('data-primary-slug'));
    const hasTags = Object.values(tags).some(arr => Array.isArray(arr) && arr.length > 0);
    const primaryOk = needPrimary.every(slug => !!primary[slug]);
    btn.disabled = !(hasTags && primaryOk);
}

/** Toast warning pour les valeurs obsolètes rejetées par le serveur (PUT /my → ignored). */
function prefNotifyIgnored(ignored) {
    const entries = Object.entries(ignored || {});
    if (!entries.length) return;
    const count = entries.reduce((sum, [, vals]) => sum + (Array.isArray(vals) ? vals.length : 0), 0);
    if (!count) return;
    showToast(
        count + ' choix retiré' + (count > 1 ? 's' : ''),
        'N\'existe' + (count > 1 ? 'nt' : '') + ' plus chez la source — sélectionnez des valeurs à jour.',
        'warning'
    );
}

/**
 * Bouton "Enregistrer mes choix" : PUT {tags, primary} PUIS ack des catégories
 * affichées. Le cache local est resynchronisé depuis la réponse serveur
 * (valeurs filtrées / `ignored`).
 */
async function handleSavePrefOnboarding() {
    const fieldsRoot = document.getElementById('prefOnboardingFields');
    const tags = collectTagsFromForm('#prefOnboardingFields');
    const primary = fieldsRoot ? collectPrimaryFromForm(fieldsRoot) : {};
    const btn = document.querySelector('#preferencesOnboardingModal [data-action="handleSavePrefOnboarding"]');
    if (btn) btn.disabled = true;
    try {
        // 1. Enregistrer sélections + primaire (fusion avec l'existant pour ne
        //    pas écraser les catégories non affichées dans la modale).
        const existing = _prefDataCache ? _prefDataCache.myTags : {};
        const merged = { ...existing, ...tags };
        const resp = await api.updateMyTags({ tags: merged, primary });

        // 2. Ack des catégories affichées (l'utilisateur a interagi) — combiné
        //    au choix complet côté serveur, la fenêtre ne reviendra plus.
        if (_prefOnboardingShownSlugs.length) {
            await api.acknowledgeMyTags(_prefOnboardingShownSlugs);
        }

        // 3. Resync du cache depuis la réponse (valeurs filtrées par la source).
        if (_prefDataCache) {
            _prefDataCache.myTags = resp.tags || merged;
            _prefDataCache.primary = resp.primary || {};
            _prefDataCache.onboardingReason = null;
        }
        prefNotifyIgnored(resp.ignored);

        _prefOnboardingRaison = null;
        _prefOnboardingShownSlugs = [];
        _prefOnboardingInstance.hide();
        showToast('Préférences enregistrées', 'Cette fenêtre ne s\'affichera plus.', 'success');
    } catch (e) {
        showToast('Erreur', e.message || 'Erreur lors de l\'enregistrement', 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

/**
 * Au dismiss (« Plus tard » / ✕) : ne pose rien (spec §6.1) — aucune écriture,
 * aucun ack : la raison recalculée par le serveur au prochain login décidera
 * du réaffichage. (sessionStorage.prefOnboardingDismissed supprimé.)
 */
function handleDismissPrefOnboarding() {
    _prefOnboardingRaison = null;
    _prefOnboardingShownSlugs = [];
}

// Brancher les handlers une fois le DOM prêt
document.addEventListener('DOMContentLoaded', () => {
    const modalEl = document.getElementById('preferencesOnboardingModal');
    if (modalEl) {
        modalEl.addEventListener('hidden.bs.modal', handleDismissPrefOnboarding);
    }
    // Modale préférences : reconstruction des radios ② au changement des chips ①
    // + mise à jour de l'état du bouton « Enregistrer mes choix ».
    const fieldsEl = document.getElementById('prefOnboardingFields');
    if (fieldsEl) {
        fieldsEl.addEventListener('change', (e) => {
            if (e.target.matches('input[data-category]')) prefRebuildPrimaryAround(e.target);
            updatePrefOnboardingSaveState();
        });
        fieldsEl.addEventListener('pref:changed', () => updatePrefOnboardingSaveState());
    }
});
