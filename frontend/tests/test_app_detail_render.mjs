#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Test autonome (Node >= 18, aucune dépendance) des fonctions de rendu
 * PURES de la fiche application (admin-app-detail.js, passe 2).
 *
 * Couvert : rendu markdown minimal (échappement XSS AVANT parsing, titres,
 * puces groupées, paragraphes), formats durées / dates fr / uptime, URL
 * GitHub depuis git_repo_url (https, .git, ssh, non-GitHub), badges d'état
 * (Docker, connectivité, version avec update-check, utilisateurs, statut de
 * déploiement) et leur résistance XSS.
 *
 * Usage : node frontend/tests/test_app_detail_render.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// --- Chargement du module frontend (script à globales, pas un module ESM) ---
const modulePath = join(
    dirname(fileURLToPath(import.meta.url)),
    '..', 'public', 'js', 'modules', 'admin-app-detail.js'
);

// --- Stubs (le module n'accède pas au DOM au chargement) ---
// Même sémantique que utils.js : textContent -> innerHTML échappe & < >
globalThis.escapeHtml = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
globalThis.escapeAttr = s => String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
// Copie de parseUTCDate (utils.js) — sans DOM.
globalThis.parseUTCDate = isoString => {
    if (!isoString) return null;
    if (typeof isoString === 'string' && !isoString.endsWith('Z') && !isoString.includes('+') && !/[-]\d{2}:\d{2}$/.test(isoString)) {
        return new Date(isoString + 'Z');
    }
    return new Date(isoString);
};

(0, eval)(
    readFileSync(modulePath, 'utf8')
    // Les `let`/`const` d'un eval indirect restent confinés à son scope
    // lexical : on expose les fonctions testables via un shim évalué dans
    // le même scope.
    + `
;globalThis.__appDetailTest = {
    appDetailGithubUrl, formatAppDetailDateTime, formatAppDetailDuration,
    formatAppDetailUptime, renderAppDetailMarkdown,
    appDetailDockerBadgeHtml, appDetailHealthBadgeHtml,
    appDetailVersionBadgeHtml, appDetailUsersBadgeHtml, appDetailDeployBadgeHtml,
    formatAppDetailFrNumber, formatAppDetailRate,
    appDetailPushPoint, appDetailSparkPoints,
};`
);
const t = globalThis.__appDetailTest;
for (const [name, fn] of Object.entries(t)) {
    assert.equal(typeof fn, 'function', name + ' doit être une fonction après chargement du module');
}

// ===== Rendu markdown minimal =====

const md = [
    '# Titre',
    '',
    '## Sous-titre',
    'Un paragraphe.',
    '- item 1',
    '* item 2',
    '  - item 3',
    '',
    'Autre paragraphe.',
].join('\n');
const html = t.renderAppDetailMarkdown(md);
assert.ok(html.includes('<div class="fw-bold">Titre</div>'), 'titre # → div fw-bold, marqueur consommé');
assert.ok(html.includes('<div class="fw-bold">Sous-titre</div>'), 'titre ## sans le marqueur');
assert.ok(html.includes('<div class="fw-bold">Titre</div>') && !html.includes('># Titre'), 'aucun # visible pour le titre');
assert.ok(html.includes('<ul'), 'puces groupées dans <ul>');
assert.ok((html.match(/<li>/g) || []).length === 3, '3 puces (item 1/2/3), indentation tolérée');
assert.ok(html.includes('<p class="mb-1">Un paragraphe.</p>'), 'paragraphe simple');
assert.ok((html.match(/<\/ul>/g) || []).length === 1, 'un seul bloc <ul> (groupe continu)');

// XSS : échappement AVANT parsing
const xss = t.renderAppDetailMarkdown('# Hi <script>alert(1)</script>\n- <img src=x onerror="alert(2)">');
assert.ok(!xss.includes('<script>'), 'script balise neutralisé');
assert.ok(xss.includes('&lt;script&gt;'), 'script échappé en entités');
assert.ok(!xss.includes('<img src=x'), 'img balise neutralisée');
assert.ok(xss.includes('&lt;img src=x'), 'img échappée en entités');
assert.equal(t.renderAppDetailMarkdown(''), '', 'chaîne vide → vide');
assert.equal(t.renderAppDetailMarkdown(null), '', 'null → vide');

// ===== Durées =====

assert.equal(t.formatAppDetailDuration('2026-11-17T10:00:00', '2026-11-17T10:00:45'), '45 s');
assert.equal(t.formatAppDetailDuration('2026-11-17T10:00:00', '2026-11-17T10:01:30'), '1 min 30 s');
assert.equal(t.formatAppDetailDuration('2026-11-17T10:00:00', '2026-11-17T12:05:00'), '2 h 05');
assert.equal(t.formatAppDetailDuration('2026-11-17T10:00:00', null), null, 'fin absente (déploiement en cours) → null');
assert.equal(t.formatAppDetailDuration(null, null), null);
assert.ok(t.formatAppDetailDuration('2026-11-17T10:01:00', '2026-11-17T10:00:00').startsWith('0'), 'inversion clampée à 0');

// ===== Dates fr =====

// TZ-indépendant : l'attendu est construit depuis le même parse (parseUTCDate
// ajoute 'Z' → conversion locale) ; on teste la mise en forme, pas le fuseau.
{
    const iso = '2026-11-17T10:30:00';
    const d = globalThis.parseUTCDate(iso);
    const expected = d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
        + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    assert.equal(t.formatAppDetailDateTime(iso), expected);
}
assert.equal(t.formatAppDetailDateTime(null), '—');
assert.equal(t.formatAppDetailDateTime('pas-une-date'), '—');

// ===== Uptime =====

assert.equal(t.formatAppDetailUptime(90061), '1 j 1 h');
assert.equal(t.formatAppDetailUptime(3660), '1 h 1 min');
assert.equal(t.formatAppDetailUptime(120), '2 min');
assert.equal(t.formatAppDetailUptime(0), '0 min');
assert.equal(t.formatAppDetailUptime(null), '0 min');

// ===== URL GitHub =====

assert.equal(t.appDetailGithubUrl('https://github.com/owner/repo.git'), 'https://github.com/owner/repo');
assert.equal(t.appDetailGithubUrl('https://github.com/owner/repo'), 'https://github.com/owner/repo');
assert.equal(t.appDetailGithubUrl('git@github.com:owner/repo.git'), 'https://github.com/owner/repo');
assert.equal(t.appDetailGithubUrl('https://gitlab.com/owner/repo.git'), null, 'non-GitHub → null (pas de lien CHANGELOG)');
assert.equal(t.appDetailGithubUrl(''), null);
assert.equal(t.appDetailGithubUrl(null), null);

// ===== Badges d'état =====

assert.ok(t.appDetailDockerBadgeHtml({ docker: 'running' }).includes('text-bg-success'));
assert.ok(t.appDetailDockerBadgeHtml({ docker: 'running' }).includes('en marche'));
assert.ok(t.appDetailDockerBadgeHtml({ docker: 'exited' }).includes('text-bg-warning'));
assert.ok(t.appDetailDockerBadgeHtml({ docker: 'exited' }).includes('arrêté'));
assert.ok(t.appDetailDockerBadgeHtml(null).includes('text-bg-light'));
assert.ok(t.appDetailDockerBadgeHtml(null).includes('inconnu'));

assert.ok(t.appDetailHealthBadgeHtml({ health: 'healthy', latency_ms: 42 }).includes('Health : en ligne · 42 ms'));
assert.ok(t.appDetailHealthBadgeHtml({ health: 'healthy', latency_ms: 42 }).includes('text-bg-success'));
assert.ok(t.appDetailHealthBadgeHtml({ health: 'unhealthy' }).includes('text-bg-danger'));
assert.ok(t.appDetailHealthBadgeHtml({ health: 'unhealthy' }).includes('hors ligne'));
assert.equal(t.appDetailHealthBadgeHtml(null), '', 'sonde indisponible → pas de badge (rangée allégée v6)');
assert.equal(t.appDetailHealthBadgeHtml({}), '', 'état sans health → pas de badge');

// Version : à jour / update dispo / inconnue / XSS
const upToDate = t.appDetailVersionBadgeHtml('1.4.2', { update_available: false, current_version: '1.4.2' });
assert.ok(upToDate.includes('v1.4.2 — à jour'), 'label normalisé avec v minuscule');
assert.ok(upToDate.includes('bg-success-subtle'), 'ton « à jour » vert clair');
const update = t.appDetailVersionBadgeHtml('1.4.2', { update_available: true, current_version: '1.4.2', latest_version: '1.5.0' });
assert.ok(update.includes('text-bg-warning'));
assert.ok(update.includes('v1.4.2 — nouvelle version dispo (v1.5.0)'));
assert.ok(t.appDetailVersionBadgeHtml('v2.0.0', null).includes('v2.0.0 — à jour'), 'update-check absent → version enregistrée');
assert.ok(t.appDetailVersionBadgeHtml(null, null).includes('version inconnue'));
assert.ok(t.appDetailVersionBadgeHtml(null, { update_available: false, repo_configured: false }).includes('version inconnue'));
const xssVersion = t.appDetailVersionBadgeHtml('<b>x</b>', null);
assert.ok(!xssVersion.includes('<b>'), 'version échappée');

// Utilisateurs
assert.ok(t.appDetailUsersBadgeHtml(['a', 'b', 'c']).includes('3 utilisateurs'));
assert.ok(t.appDetailUsersBadgeHtml(['a']).includes('1 utilisateur'), 'singulier');
assert.ok(t.appDetailUsersBadgeHtml([]).includes('0 utilisateurs'));
assert.ok(t.appDetailUsersBadgeHtml(null).includes('0 utilisateurs'));

// Statut de déploiement
assert.ok(t.appDetailDeployBadgeHtml('success').includes('text-bg-success'));
assert.ok(t.appDetailDeployBadgeHtml('success').includes('Réussi'));
assert.ok(t.appDetailDeployBadgeHtml('failed').includes('text-bg-danger'));
assert.ok(t.appDetailDeployBadgeHtml('running').includes('text-bg-warning'));
assert.ok(t.appDetailDeployBadgeHtml('running').includes('En cours'));
assert.ok(t.appDetailDeployBadgeHtml(undefined).includes('inconnu'));

// Zones scrollables (historique déploiements + changelog) : wrapper et règle CSS
const detailSource = readFileSync(join(dirname(modulePath), 'admin-app-detail.js'), 'utf8');
const styleSource = readFileSync(join(dirname(modulePath), '..', '..', 'css', 'style.css'), 'utf8');
const indexSource = readFileSync(join(dirname(modulePath), '..', '..', 'index.html'), 'utf8');
assert.ok(detailSource.includes("'<div class=\"text-muted mb-1\">Historique :</div><div class=\"appdetail-scroll\">' + items + '</div>'"),
    "l'historique des déploiements doit être enveloppé dans .appdetail-scroll");
assert.ok(indexSource.includes('id="appDetailChangelogBody" class="appdetail-scroll"'),
    "le corps du changelog (squelette statique v6) doit porter .appdetail-scroll dans index.html");
assert.ok(styleSource.includes('.appdetail-scroll { max-height: 16rem; overflow-y: auto; overscroll-behavior: contain; }'),
    "la règle .appdetail-scroll doit exister dans style.css (16rem — maquette v6)");

// ===== Mini-cockpit v6 : formats fr, débit réseau, buffer 30, sparklines =====

// Format fr (virgule décimale)
assert.equal(t.formatAppDetailFrNumber(2.1, 1), '2,1');
assert.equal(t.formatAppDetailFrNumber(86, 1), '86');
assert.equal(t.formatAppDetailFrNumber(NaN, 1), '—', 'non fini → —');
assert.equal(t.formatAppDetailFrNumber('pas-un-nombre', 1), '—');

// Débit réseau : formatage auto o/s → Ko/s → Mo/s, garde non-fini
assert.equal(t.formatAppDetailRate(null), '—', '1ᵉʳ tick (pas de delta) → —');
assert.equal(t.formatAppDetailRate(undefined), '—');
assert.equal(t.formatAppDetailRate(NaN), '—');
assert.equal(t.formatAppDetailRate(-5), '—', 'compteurs qui reculent → —');
assert.equal(t.formatAppDetailRate(340), '340 o/s');
assert.equal(t.formatAppDetailRate(2048), '2 Ko/s');
assert.equal(t.formatAppDetailRate(512 * 1024), '512 Ko/s');
assert.equal(t.formatAppDetailRate(1.2 * 1048576), '1,2 Mo/s');
assert.equal(t.formatAppDetailRate(15 * 1048576), '15 Mo/s');

// Buffer 30 points max par métrique (FIFO)
{
    const buf = [];
    for (let i = 0; i < 50; i++) t.appDetailPushPoint(buf, i);
    assert.equal(buf.length, 30, 'buffer plafonné à 30 points');
    assert.equal(buf[0], 20, 'les plus anciens points sont évacués (FIFO)');
    assert.equal(buf[buf.length - 1], 49, 'le dernier point est conservé');
    const before = [];
    t.appDetailPushPoint(before, NaN);
    t.appDetailPushPoint(before, 'x');
    assert.equal(before.length, 0, 'valeurs non finies ignorées');
}

// Sparklines : points SVG normalisés (haut = valeur haute), gardes
assert.equal(t.appDetailSparkPoints([]), '', 'aucun point → polyline vide');
assert.equal(t.appDetailSparkPoints([5]), '', 'un seul point → polyline vide');
assert.equal(t.appDetailSparkPoints([3, 3, 3]).split(' ').length, 3, 'palier constant → 3 points');
{
    const pts = t.appDetailSparkPoints([0, 10], 100, 22, 2);
    assert.equal(pts, '0,20 100,2', 'montée : min en bas (y=20), max en haut (y=2)');
    const many = t.appDetailSparkPoints(Array.from({ length: 30 }, (_, i) => i));
    assert.equal(many.split(' ').length, 30, '30 points → 30 coordonnées');
    for (const pair of many.split(' ')) {
        const y = parseFloat(pair.split(',')[1]);
        assert.ok(y >= 2 && y <= 20, 'y dans [pad, height-pad]');
    }
}

// Squelette statique v6 : zones du mini-cockpit + grille présentes dans index.html
assert.ok(indexSource.includes('id="appDetailMiniMonitor"'), 'mini-cockpit dans le squelette');
assert.ok(indexSource.includes('id="appDetailCpuSpark"') && indexSource.includes('id="appDetailNetSpark"'),
    'sparklines CPU/Réseau statiques dans le squelette');
assert.ok(indexSource.includes('class="fgrid mb-3" id="appDetailGrid"'), 'grille 2 colonnes v6');
assert.ok(indexSource.includes('id="appDetailColLeft"') && indexSource.includes('id="appDetailColRight"'),
    'colonnes indépendantes gauche/droite');
assert.ok(detailSource.includes('APP_DETAIL_COCKPIT_MAX_POINTS = 30'), 'buffer 30 points déclaré');
assert.ok(detailSource.includes('APP_DETAIL_COCKPIT_POLL_MS = 10000'), 'poll cockpit 10 s déclaré');

// ===== Réglages inline v6 : la modale d'édition a migré dans la fiche =====

// La modale (et son backdrop) est supprimée d'index.html
assert.ok(!indexSource.includes('id="appFormModal"'), "plus de modale appFormModal dans index.html");
assert.ok(!detailSource.includes('appFormModal'), "admin-app-detail.js ne référence plus appFormModal");
const formSource = readFileSync(join(dirname(modulePath), 'admin-app-form.js'), 'utf8');
assert.ok(!formSource.includes('appFormModal.'), 'admin-app-form.js n\'appelle plus appFormModal (show/hide)');

// Le formulaire inline vit dans la carte Réglages du squelette (mêmes IDs app*)
assert.ok(indexSource.includes('id="appDetailSettingsCard"'), 'carte Réglages dans le squelette');
assert.ok(indexSource.includes('<form id="appForm" data-action="handleSaveApp"'), 'formulaire inline (flux save inchangé)');
assert.ok(indexSource.includes('data-action="resetAppDetailForm"'), 'bouton Réinitialiser branché');
for (const fieldId of ['appId', 'appMode', 'appIcon', 'appColor', 'appName', 'appSlug', 'appDescription',
    'appGroup', 'appCategory', 'appGitRepoUrl', 'appApiUrl', 'appFrontendUrl', 'appCallbackUrl',
    'appActive', 'appRequiresAuth', 'appIsSharedService', 'appIsUnmanaged']) {
    const count = indexSource.split('id="' + fieldId + '"').length - 1;
    assert.equal(count, 1, 'champ #' + fieldId + ' unique dans index.html (migration sans doublon)');
}

// Flux conservés : préfill extrait, réorientation editApp, reset création
assert.ok(formSource.includes('function prefillAppFormFromApp('), 'préfill édition extrait (préfillAppFormFromApp)');
assert.ok(formSource.includes('function resetAppFormForCreate('), 'reset création extrait (resetAppFormForCreate)');
assert.ok(formSource.includes('function resetAppDetailForm('), 'action Réinitialiser (resetAppDetailForm)');
assert.ok(detailSource.includes('function openAppDetailCreate('), 'mode création (openAppDetailCreate)');
assert.ok(indexSource.includes('id="appDetailCreateNote"'), 'encart « Nouvelle application » dans le squelette');

console.log('OK — test_app_detail_render : toutes les assertions passent.');
