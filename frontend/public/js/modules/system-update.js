// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Centre de mises à jour du parc (Hub + socle)
 *
 * Onglet « Mises à jour » de la page admin + badge navbar. Contrat des
 * routes (hub/app/routes/system_update.py, proxy vers le service hôte
 * `hubctl update --serve`) :
 *
 * - GET  /api/admin/system/update-status — TOLÉRANT : 200
 *   {"service": "ok", checked_at, meta, components} ; service hôte
 *   down ⇒ 200 {"service": "unreachable", "hint"} ; non-admin ⇒ 403.
 *   meta / components[rel_path] : {name, path, version, version_target,
 *   branch, commits_behind, dirty, update_available, error} — drifted
 *   n'est renseigné que sur meta.
 * - POST /api/admin/system/update — STRICT : 202 job lancé, 409 déjà
 *   en cours, 503 service injoignable / token absent (api.request jette
 *   avec le `detail` comme message, sans exposer le code HTTP).
 * - GET  /api/admin/system/update-job — TOLÉRANT :
 *   {state: "idle"|"running"|"success"|"failed", started_at?,
 *   finished_at?, exit_code?, log_path?, tail: [lignes]}.
 *   Service hôte down (Hub up) ⇒ 200 {"service": "unreachable"}
 *   (sans state) — traité comme un échec de poll, jamais un verdict.
 *
 * Front (spec §5.3/§6) :
 * - Badge navbar (admins only) : point orange à côté de
 *   #navbarVersionBadge si meta.update_available ou un composant est
 *   concerné ; silencieux si 403 (non-admin) ou service unreachable.
 *   Clic ⇒ ouvre la page admin sur l'onglet Mises à jour.
 * - Onglet : carte récap parc (checked_at, « Vérifier maintenant »),
 *   une ligne par composant (version → cible, badges à jour / X → Y /
 *   arbre sale / erreur, remèdes textuels), bouton « Mettre à jour le
 *   Hub et le socle » (disabled si rien à mettre à jour ou job en
 *   cours, anti double-clic via btn.disabled).
 * - Suivi live : polling 2 s SANS plafond temporel tant que
 *   state == "running" — le Hub redémarre en cours de mise à jour :
 *   fetch KO ou réponse 200 sans état de job (ex.
 *   {"service":"unreachable"}) sont ignorés mais comptés (≥ 3
 *   consécutifs ⇒ « Hub en redémarrage… » affiché, polling poursuivi ;
 *   le job survit côté hôte) — jamais traités comme un verdict.
 *   Garde in-flight + jeton de session : un seul tick en vol, et tout
 *   tick d'une session stoppée devient un no-op. Au verdict
 *   success/failed ⇒ stop, /api/health relue
 *   (badge version navbar), statut re-rendu, verdict + tail du log
 *   affichés dans le <pre>.
 *
 * Pas d'animations (charte PERFORMANCE.md) : états textuels + classes
 * utilitaires. Les fonctions restent globales pour conserver la
 * compatibilité avec les handlers data-action (cf. autres modules admin-*).
 */

// ===== État du module =====

let systemUpdateStatus = null;     // dernière réponse GET update-status
let systemUpdateTimer = null;      // setInterval du polling de job
let systemUpdatePollErrors = 0;    // échecs poll consécutifs (fetch KO ou réponse sans état)
let systemUpdateJobActive = false; // job en cours côté vue (bouton disabled)
let systemUpdatePollSession = 0;   // jeton de session : invalide les ticks en vol d'une session stoppée
let systemUpdatePollInFlight = false; // garde : un seul tick en vol à la fois

const SYSTEM_UPDATE_POLL_MS = 2000;
const SYSTEM_UPDATE_MAX_POLL_ERRORS = 3; // ≥ 3 échecs consécutifs ⇒ « redémarrage… »
const SYSTEM_UPDATE_RESTART_MSG = 'Hub en redémarrage… la mise à jour se poursuit côté hôte.';

// Point du badge navbar : token HUBCSS (style.css --vpn-amber), fallback
// orange fixe. Aucune animation — pastille statique.
const SYSTEM_UPDATE_DOT_COLOR = 'var(--vpn-amber, #d97706)';

// ===== Initialisation (badge navbar + activation onglet) =====

document.addEventListener('DOMContentLoaded', () => {
    initSystemUpdateBadge();

    const tabBtn = document.getElementById('systemUpdateTabBtn');
    if (tabBtn) {
        tabBtn.addEventListener('shown.bs.tab', () => {
            loadSystemUpdates(true);
        });
    }

    // Après le rechargement post-mise à jour (armSystemUpdateReload pose
    // #admin) : revenir directement sur la vue admin.
    if (window.location.hash === '#admin' && typeof showPage === 'function') {
        try { showPage('admin'); } catch (e) { /* vue par défaut sinon */ }
    }
});

/**
 * Badge navbar « mise à jour disponible » (admins only) : interroge le
 * statut une fois à l'initialisation. 403 (non-admin) / fetch échoué /
 * service unreachable ⇒ silencieux, aucun badge. Sinon, si méta OU un
 * composant est concerné, injecte un point orange cliquable à côté de
 * #navbarVersionBadge (clic ⇒ page admin, onglet Mises à jour).
 */
async function initSystemUpdateBadge() {
    // Sans token (écran de login) : tout appel renverrait 401 et
    // déclencherait le flux « session expirée » — on ne sonde que connecté.
    if (typeof api === 'undefined' || !api.token) return;
    let data;
    try {
        data = await api.getSystemUpdateStatus();
    } catch (error) {
        return; // 403 non-admin, session expirée… : silencieux
    }
    if (!data || data.service !== 'ok') return; // unreachable : silencieux
    if (systemUpdateAvailableAnywhere(data)) {
        showSystemUpdateBadgeDot();
    }
}

/** true si méta ou au moins un composant a update_available. */
function systemUpdateAvailableAnywhere(data) {
    if (data && data.meta && data.meta.update_available) return true;
    const components = (data && data.components) || {};
    return Object.keys(components).some(relPath => {
        return components[relPath] && components[relPath].update_available;
    });
}

/** Injecte le point orange cliquable dans le slot navbar (une seule fois). */
function showSystemUpdateBadgeDot() {
    const slot = document.getElementById('systemUpdateBadgeSlot');
    if (!slot || slot.childElementCount > 0) return;
    const dot = document.createElement('span');
    dot.setAttribute('data-action', 'openSystemUpdatesTab');
    dot.setAttribute('role', 'button');
    dot.setAttribute('aria-label', 'Mise à jour du Hub et du socle disponible');
    dot.setAttribute('title', "Mise à jour du Hub/socle disponible — cliquez pour ouvrir l'onglet Mises à jour");
    dot.style.display = 'inline-block';
    dot.style.width = '10px';
    dot.style.height = '10px';
    dot.style.borderRadius = '50%';
    dot.style.background = SYSTEM_UPDATE_DOT_COLOR;
    dot.style.cursor = 'pointer';
    slot.classList.remove('d-none');
    slot.appendChild(dot);
}

/** data-action du point navbar : ouvre la page admin sur l'onglet Mises à jour. */
function openSystemUpdatesTab() {
    if (typeof showPage === 'function') showPage('admin');
    const tabBtn = document.getElementById('systemUpdateTabBtn');
    if (!tabBtn || typeof bootstrap === 'undefined' || !bootstrap.Tab) return;
    if (tabBtn.classList.contains('active')) {
        // Déjà l'onglet actif : shown.bs.tab ne refera pas feu → rechargement direct.
        loadSystemUpdates(true);
    } else {
        bootstrap.Tab.getOrCreateInstance(tabBtn).show(); // shown.bs.tab → loadSystemUpdates
    }
}

// ===== Chargement / rendu de l'onglet =====

/**
 * Charge le statut du parc puis le rend. `force` ⇒ re-check git côté
 * hôte (?refresh=1) — activation de l'onglet et « Vérifier maintenant ».
 */
async function loadSystemUpdates(force) {
    const errorBox = document.getElementById('systemUpdateError');
    if (errorBox) errorBox.classList.add('d-none');
    try {
        const data = await api.getSystemUpdateStatus(!!force);
        systemUpdateStatus = data;
        renderSystemUpdates(data);
    } catch (error) {
        console.error('Erreur chargement statut mises à jour:', error);
        if (errorBox) {
            errorBox.textContent = 'Statut des mises à jour indisponible : ' +
                ((error && error.message) || 'erreur inconnue');
            errorBox.classList.remove('d-none');
        }
        return;
    }
    // Un job peut avoir été lancé ailleurs (autre admin, autre onglet,
    // page rechargée pendant l'exécution) : s'y rattacher pour poursuivre.
    resumeSystemUpdateJobWatch();
}

/** data-action « Vérifier maintenant » : re-check git forcé côté hôte. */
function checkSystemUpdatesNow() {
    loadSystemUpdates(true);
}

/**
 * Rendu de l'onglet : service hôte down ⇒ alerte dégradée (bouton
 * disabled) ; sinon carte récap méta + une ligne par composant + état
 * du bouton « Mettre à jour le Hub et le socle » (disabled si rien à
 * mettre à jour ou job en cours). Toute valeur serveur passe par
 * escapeHtml avant innerHTML.
 */
function renderSystemUpdates(data) {
    const parcCard = document.getElementById('systemUpdateParcCard');
    const compsBox = document.getElementById('systemUpdateComponents');
    const runBtn = document.getElementById('systemUpdateRunBtn');
    const runHint = document.getElementById('systemUpdateRunHint');
    if (!parcCard || !compsBox) return;

    if (!data || data.service !== 'ok') {
        const hint = (data && data.hint) ? String(data.hint) : 'vérifier hubctl-update.service (systemctl status)';
        parcCard.innerHTML = `
            <div class="alert alert-warning mb-3">
                <strong><i class="bi bi-plug me-1"></i>Service de mise à jour hôte injoignable.</strong>
                ${escapeHtml(hint)}
            </div>`;
        compsBox.innerHTML = '';
        if (runBtn) runBtn.disabled = true;
        if (runHint) runHint.textContent = 'Service hôte indisponible — mise à jour impossible pour le moment.';
        return;
    }

    const meta = data.meta || {};
    const components = data.components || {};
    const relPaths = Object.keys(components);
    const metaAvailable = !!meta.update_available;
    const anyAvailable = metaAvailable || systemUpdateAvailableAnywhere(data);

    parcCard.innerHTML = _renderSystemUpdateMetaCard(data, meta, metaAvailable);

    if (relPaths.length === 0) {
        compsBox.innerHTML = `
            <div class="text-center text-muted py-3">
                <i class="bi bi-boxes display-6"></i>
                <p class="mt-2 mb-0">Aucun composant socle déclaré</p>
            </div>`;
    } else {
        const rows = relPaths.map(relPath => _renderSystemUpdateRow(relPath, components[relPath] || {})).join('');
        compsBox.innerHTML = `
            <div class="card border-0 shadow-sm mb-3">
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead>
                            <tr>
                                <th>Composant</th>
                                <th>Version</th>
                                <th>État</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
    }

    if (runBtn) runBtn.disabled = systemUpdateJobActive || !anyAvailable;
    if (runHint) {
        runHint.textContent = systemUpdateJobActive
            ? 'Mise à jour en cours — suivi ci-dessous.'
            : (anyAvailable
                ? 'Le service Hub sera coupé quelques dizaines de secondes.'
                : 'Tout est à jour.');
    }
}

/**
 * Carte récap « Parc » : nom, version / cible, branche, retard, date du
 * check, badges (à jour / X → Y / arbre sale / divergé) et remèdes
 * textuels le cas échéant (drifted n'existe que sur méta).
 */
function _renderSystemUpdateMetaCard(data, meta, metaAvailable) {
    const badges = [metaAvailable
        ? `<span class="badge bg-warning text-dark">${escapeHtml(String(meta.version || '?'))} → ${escapeHtml(String(meta.version_target || '?'))}</span>`
        : '<span class="badge bg-success">à jour</span>'];
    if (meta.dirty) badges.push('<span class="badge bg-warning text-dark">arbre sale</span>');
    if (meta.drifted && meta.drifted.length) badges.push('<span class="badge bg-danger">divergé</span>');

    const checkedAt = _systemUpdateDate(data && data.checked_at);
    const remedies = _systemUpdateRemedies(meta);
    const behind = (typeof meta.commits_behind === 'number' && meta.commits_behind > 0)
        ? ` · ${escapeHtml(String(meta.commits_behind))} commit(s) de retard` : '';

    return `
        <div class="card border-0 shadow-sm mb-3">
            <div class="card-body py-3">
                <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
                    <div>
                        <h6 class="mb-1"><i class="bi bi-box-seam me-1"></i>${escapeHtml(String(meta.name || 'Parc POULIDOR'))}</h6>
                        <small class="text-muted">
                            Version <strong>v${escapeHtml(String(meta.version || '?'))}</strong>
                            ${meta.version_target ? ` · cible v${escapeHtml(String(meta.version_target))}` : ''}
                            · branche <code>${escapeHtml(String(meta.branch || '?'))}</code>${behind}
                        </small>
                    </div>
                    <div class="text-end">
                        <div>${badges.join(' ')}</div>
                        <div><small class="text-muted">${checkedAt ? escapeHtml('vérifié le ' + checkedAt) : ''}</small></div>
                    </div>
                </div>
                ${remedies}
            </div>
        </div>`;
}

/** Ligne d'un composant socle : nom, version locale → cible, badges + remèdes. */
function _renderSystemUpdateRow(relPath, comp) {
    const name = comp.name || relPath;
    const badges = [comp.update_available
        ? `<span class="badge bg-warning text-dark">${escapeHtml(String(comp.version || '?'))} → ${escapeHtml(String(comp.version_target || '?'))}</span>`
        : '<span class="badge bg-success">à jour</span>'];
    if (comp.dirty) badges.push('<span class="badge bg-warning text-dark">arbre sale</span>');
    if (comp.error) badges.push('<span class="badge bg-danger">erreur</span>');

    const behind = (typeof comp.commits_behind === 'number' && comp.commits_behind > 0)
        ? ` · ${escapeHtml(String(comp.commits_behind))} commit(s) de retard` : '';

    return `
        <tr>
            <td>
                <strong>${escapeHtml(String(name))}</strong><br>
                <small class="text-muted">${escapeHtml(String(relPath))}</small>
            </td>
            <td>
                <code>${escapeHtml(String(comp.version || '?'))}</code> → <code>${escapeHtml(String(comp.version_target || '?'))}</code>${behind}
            </td>
            <td>
                <div>${badges.join(' ')}</div>
                ${_systemUpdateRemedies(comp)}
            </td>
        </tr>`;
}

/**
 * Remèdes textuels d'un composant (ou de la méta) : arbre sale →
 * « committer ou nettoyer l'arbre du dépôt avant la mise à jour » ;
 * fetch failed → « vérifier l'accès réseau/git du serveur » (verbatim
 * contrat front, spec §6). Chaîne vide si rien à signaler.
 */
function _systemUpdateRemedies(comp) {
    const lines = [];
    if (comp && comp.dirty) {
        lines.push("committer ou nettoyer l'arbre du dépôt avant la mise à jour");
    }
    if (comp && comp.error) {
        lines.push(`${escapeHtml(String(comp.error))} — vérifier l'accès réseau/git du serveur`);
    }
    if (lines.length === 0) return '';
    return `
        <div class="mt-1 small">
            ${lines.map(line => `<div><i class="bi bi-exclamation-triangle-fill text-warning me-1"></i>${line}</div>`).join('')}
        </div>`;
}

/** ISO backend affiché en local, valeur brute telle quelle si non parsable. */
function _systemUpdateDate(iso) {
    if (iso === null || iso === undefined || iso === '') return '';
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? String(iso) : parsed.toLocaleString('fr-FR');
}

// ===== Lancement + suivi live (spec §5.3) =====

/**
 * data-action du bouton « Mettre à jour le Hub et le socle » :
 * confirmation (coupure du service de quelques dizaines de secondes) →
 * POST strict (202 job lancé ; 409 déjà en cours ⇒ toast + rattachement
 * au job en cours ; 503 verbalisé) → polling.
 */
async function runSystemUpdateFlow() {
    const btn = document.getElementById('systemUpdateRunBtn');
    if (btn && btn.disabled) return; // anti double-clic
    if (!confirm('Lancer la mise à jour du Hub et du socle ?\n\n' +
        'Le service Hub sera coupé quelques dizaines de secondes ' +
        '(pull, build, redémarrage). Gardez cette page ouverte : le suivi ' +
        'reprendra automatiquement après le redémarrage.')) return;
    if (btn) btn.disabled = true;
    let res;
    try {
        res = await api.runSystemUpdate(); // 202 ; 409/503 → throw (message = detail)
    } catch (error) {
        const msg = (error && error.message) || 'Erreur inconnue';
        // Sans accent : le service hôte renvoie « update deja en cours »
        // (update_serve.py, hubctl) — la regex doit matcher les deux
        // graphies (d[eé]j[aà]) pour rattraper le 409 passthrough.
        if (/d[eé]j[aà]\s+en\s+cours/i.test(msg)) {
            // 409 passthrough : un job tourne déjà (autre admin / onglet) —
            // on s'y rattache plutôt que de rester bloqué.
            showToast('Mise à jour', 'Une mise à jour est déjà en cours — suivi rattaché au job en cours.', 'warning');
            attachToRunningSystemUpdateJob();
        } else {
            showToast('Mise à jour', msg, 'danger');
            if (btn) btn.disabled = false;
        }
        return;
    }
    systemUpdateJobActive = true;
    showToast('Mise à jour', 'Mise à jour du Hub et du socle lancée — suivi ci-dessous.', 'info');
    showSystemUpdateJobPanel('running', 'Job soumis au service hôte…',
        (res && typeof res === 'object') ? res : null);
    pollSystemUpdateJob();
}

/** Rattache la vue à un job déjà en cours (409 ou statut lu « running »). */
function attachToRunningSystemUpdateJob() {
    systemUpdateJobActive = true;
    const btn = document.getElementById('systemUpdateRunBtn');
    if (btn) btn.disabled = true;
    pollSystemUpdateJob();
}

/**
 * Suit l'état du job côté hôte : setInterval 2 s, AUCUN plafond
 * temporel tant que state == "running" (le job survit au redémarrage du
 * Hub — reconnexion auto). Deux gardes évitent les ticks parasites :
 * une garde in-flight (un seul await en vol — un tick > 2 s n'ouvre pas
 * de second tick concurrent) et un jeton de session incrémenté à chaque
 * start/finish du polling (un tick en vol d'une session stoppée devient
 * un no-op : aucun DOM, aucun verdict écrasé après finish). Erreurs
 * ignorées mais comptées — fetch KO OU réponse 200 sans état de job
 * (ex. {"service":"unreachable"}, service hôte down, Hub up) : ce
 * n'est PAS un verdict ; ≥ 3 consécutifs ⇒ « Hub en redémarrage… »
 * affiché dans le panneau, polling poursuivi. success/failed ⇒ verdict
 * (cf. finishSystemUpdateJob).
 */
function pollSystemUpdateJob() {
    if (systemUpdateTimer) return; // polling déjà actif
    systemUpdatePollSession += 1;  // nouvelle session : les ticks résiduels ne comptent plus
    systemUpdatePollInFlight = false;
    systemUpdatePollErrors = 0;
    systemUpdateTimer = setInterval(async function () {
        if (systemUpdatePollInFlight) return; // tick précédent toujours en vol
        systemUpdatePollInFlight = true;
        const session = systemUpdatePollSession;
        try {
            let job;
            try {
                job = await api.getSystemUpdateJob();
            } catch (e) {
                // Hub en redémarrage : erreur transitoire ignorée mais
                // comptée — retente au prochain tick (pattern pollDeployStatus).
                if (session !== systemUpdatePollSession) return; // session stoppée entre-temps
                systemUpdatePollErrors += 1;
                if (systemUpdatePollErrors >= SYSTEM_UPDATE_MAX_POLL_ERRORS) {
                    showSystemUpdateJobPanel('running', SYSTEM_UPDATE_RESTART_MSG);
                }
                return;
            }
            if (session !== systemUpdatePollSession) return; // verdict déjà tombé : no-op
            // 200 sans état de job (ex. {"service":"unreachable"} — Hub up,
            // service hôte down) : ce n'est PAS un verdict. Compté comme un
            // échec de poll, retente au prochain tick — sans cette garde,
            // state === undefined finissait en faux « Échec » + polling tué.
            if (!job || job.state === undefined || job.service === 'unreachable') {
                systemUpdatePollErrors += 1;
                if (systemUpdatePollErrors >= SYSTEM_UPDATE_MAX_POLL_ERRORS) {
                    showSystemUpdateJobPanel('running', SYSTEM_UPDATE_RESTART_MSG);
                }
                return;
            }
            systemUpdatePollErrors = 0;
            const state = job.state;
            if (state === 'running' || state === 'idle') {
                // idle = état réinitialisé côté hôte (job en cours de soumission).
                showSystemUpdateJobPanel('running',
                    state === 'running' ? 'Mise à jour en cours…' : 'En attente du service hôte…',
                    job);
                return;
            }
            finishSystemUpdateJob(state, job);
        } finally {
            // Session active : libère la garde pour le tick suivant.
            // Session stoppée : la garde est réinitialisée par le prochain
            // pollSystemUpdateJob (nouvelle session) — pas de reset tardif
            // qui pourrait rouvrir un tick concurrent d'une autre session.
            if (session === systemUpdatePollSession) systemUpdatePollInFlight = false;
        }
    }, SYSTEM_UPDATE_POLL_MS);
}

/**
 * Verdict (success | failed) : stop du polling, badge version navbar
 * rechargé (/api/health), statut re-rendu, toast + verdict et tail du
 * log affichés dans le <pre> (échec : exit_code + remèdes du journal).
 */
function finishSystemUpdateJob(state, job) {
    systemUpdatePollSession += 1; // invalide tout tick en vol de la session stoppée (aucun re-render « En cours » après le verdict)
    clearInterval(systemUpdateTimer);
    systemUpdateTimer = null;
    systemUpdatePollErrors = 0;
    systemUpdatePollInFlight = false;
    systemUpdateJobActive = false;

    const ok = state === 'success';
    // Le Hub vient (probablement) de redémarrer : badge + statut
    // rechargés — best effort, le verdict reste affiché dans le panneau.
    if (typeof updateHubVersionBadge === 'function') updateHubVersionBadge();
    loadSystemUpdates(true);

    const exitCode = (job && job.exit_code !== undefined && job.exit_code !== null)
        ? String(job.exit_code) : '?';
    showToast('Mise à jour',
        ok ? 'Mise à jour du Hub et du socle terminée avec succès.'
           : `Mise à jour échouée (exit_code=${exitCode}) — journal et remède ci-dessous.`,
        ok ? 'success' : 'danger');
    showSystemUpdateJobPanel(state,
        ok ? 'Mise à jour terminée — versions à jour.'
           : `Mise à jour échouée (exit_code=${exitCode})`,
        job);
    // La page admin (token, données chargées, SSE) peut être irrécupérable
    // après le redémarrage du Hub : elle se recharge automatiquement.
    // Succès : compte à rebours court pour lire le verdict. Échec :
    // rechargement manuel seulement (l'admin lit d'abord journal et remède).
    armSystemUpdateReload(ok ? 8 : 0);
}

/**
 * Rechargement automatique de la page admin après une mise à jour terminée
 * (retour du Hub = token/données/SSE potentiellement périmés). Injecte dans
 * le panneau une ligne « La page sera rechargée dans N s » + bouton
 * « Recharger maintenant » ; delaySec = 0 ⇒ bouton seul (cas échec).
 * Pose #admin pour que le rechargement revienne sur la vue admin.
 */
function armSystemUpdateReload(delaySec) {
    const logEl = document.getElementById('systemUpdateLog');
    const panel = document.getElementById('systemUpdateJobPanel');
    if (!panel || !logEl) return;
    try { window.location.hash = 'admin'; } catch (e) { /* hash refusé : reload simple */ }

    let row = document.getElementById('systemUpdateReloadRow');
    if (!row) {
        row = document.createElement('div');
        row.id = 'systemUpdateReloadRow';
        row.className = 'mt-2 d-flex align-items-center flex-wrap gap-2';
        logEl.parentNode.insertBefore(row, logEl.nextSibling);
    }
    row.innerHTML = '';

    const txt = document.createElement('span');
    txt.className = 'text-muted';
    row.appendChild(txt);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-primary btn-sm';
    btn.textContent = 'Recharger maintenant';
    btn.addEventListener('click', () => { window.location.reload(); });
    row.appendChild(btn);

    if (!delaySec || delaySec <= 0) {
        txt.textContent = 'Rechargez la page pour repartir sur un état sain.';
        return;
    }
    let left = delaySec;
    const tick = () => {
        if (left <= 0) { window.location.reload(); return; }
        txt.textContent = 'La page sera rechargée automatiquement dans ' + left + ' s.';
        left -= 1;
    };
    tick();
    setInterval(tick, 1000);
}

/** Rattache la vue au job si un job est déjà en cours (au chargement de l'onglet). */
async function resumeSystemUpdateJobWatch() {
    let job;
    try {
        job = await api.getSystemUpdateJob();
    } catch (e) {
        return; // service hôte down : pas de suivi possible, silencieux
    }
    if (job && job.state === 'running') {
        attachToRunningSystemUpdateJob();
    }
}

/**
 * Panneau de suivi : badge d'état + titre (échappés), log_path en
 * méta, tail du journal dans le <pre> (textContent — aucune injection
 * HTML du log). Sans tail : texte d'attente pendant l'exécution,
 * message d'échec explicite sinon.
 */
function showSystemUpdateJobPanel(state, headline, job) {
    const panel = document.getElementById('systemUpdateJobPanel');
    const stateEl = document.getElementById('systemUpdateJobState');
    const metaEl = document.getElementById('systemUpdateJobMeta');
    const logEl = document.getElementById('systemUpdateLog');
    if (!panel || !stateEl || !logEl) return;
    panel.classList.remove('d-none');

    const STATE_BADGES = {
        running: 'bg-warning text-dark',
        success: 'bg-success',
        failed: 'bg-danger',
        idle: 'bg-secondary'
    };
    const STATE_LABELS = {
        running: 'En cours',
        success: 'Succès',
        failed: 'Échec',
        idle: 'Inactif'
    };
    stateEl.innerHTML = `<span class="badge ${STATE_BADGES[state] || 'bg-secondary'} me-2">${STATE_LABELS[state] || escapeHtml(String(state))}</span>${escapeHtml(String(headline || ''))}`;

    if (metaEl) {
        metaEl.textContent = (job && job.log_path) ? String(job.log_path) : '';
    }

    const tail = (job && Array.isArray(job.tail)) ? job.tail : null;
    if (tail && tail.length > 0) {
        logEl.textContent = tail.join('\n');
        logEl.scrollTop = logEl.scrollHeight; // suivre la fin du journal
    } else if (state === 'running') {
        logEl.textContent = "Mise à jour en cours — le journal complet sera affiché à la fin du job.";
    } else if (state === 'failed') {
        logEl.textContent = 'Échec — journal indisponible' +
            ((job && job.log_path) ? ` (${String(job.log_path)})` : '') + '.';
    } else {
        logEl.textContent = '';
    }
}
