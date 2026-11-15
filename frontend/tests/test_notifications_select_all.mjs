#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Test autonome (Node >= 18, aucune dépendance) de la sélection multiple de
 * la console notifications (notifications.js).
 *
 * Régression couverte : le checkbox « tout sélectionner » doit sélectionner
 * TOUTES les notifications matchant les filtres courants, toutes pages
 * confondues (fetch paginé au page_size max backend = 100), pas seulement la
 * page affichée. Couvre aussi : persistance de la sélection au changement de
 * page, graduation des actions en masse (cap backend 500 IDs/requête, POST
 * unitaires gradués par 50), état checked/indéterminé du header.
 *
 * Usage : node frontend/tests/test_notifications_select_all.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// --- Chargement du module frontend (script à globales, pas un module ESM) ---
const modulePath = join(
    dirname(fileURLToPath(import.meta.url)),
    '..', 'public', 'js', 'modules', 'notifications.js'
);

// --- Stubs DOM minimaux ---
function makeEl(props = {}) {
    return Object.assign({
        checked: false, indeterminate: false, disabled: false,
        innerHTML: '', textContent: '', value: '',
        classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
        dataset: {}, style: {},
    }, props);
}

const els = new Map();
const headerCb = makeEl();               // checkbox select-all du thead
let rowCheckboxes = [];                  // checkboxes de la page affichée
globalThis.document = {
    getElementById: id => els.get(id) || null,
    querySelector: () => headerCb,
    querySelectorAll: sel => sel.includes('notifToggleSelect') ? rowCheckboxes : [],
    createElement: () => makeEl(),
};

// --- Stubs utilitaires externes au module ---
globalThis.escapeHtml = s => String(s);
globalThis.escapeAttr = s => String(s);
globalThis.parseUTCDate = s => new Date(s || Date.now());
globalThis.currentUser = { is_admin: true };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.confirm = () => true;
const toasts = [];
globalThis.showToast = (title, msg, kind) => { toasts.push({ title, msg, kind }); };
globalThis.updateMessageBellBadge = () => {};
globalThis.updateNavMessagesBadge = () => {};
globalThis.loadUnreadMessageCount = () => {};

els.set('notifSelectionBar', makeEl());
els.set('notifSelCount', makeEl());
els.set('notifTableHead', makeEl());
els.set('notifTableBody', makeEl());
els.set('notifPagination', makeEl());
els.set('notifAppFilter', makeEl());

(0, eval)(
    readFileSync(modulePath, 'utf8')
    // Les `const` d'un eval indirect restent confinés à son scope lexical :
    // on expose les internes testables via un shim évalué dans le même scope.
    + `
;globalThis.__notifTest = {
    _notif, _chunkArray, _syncNotifSelectionBar,
    loadNotifications, notifSetPage, notifSetPageSize, notifSetAppFilter,
    notifToggleSelectAll, notifBulkRead, notifBulkDelete, notifBulkArchive,
};`
);
const {
    _notif, _chunkArray, _syncNotifSelectionBar,
    loadNotifications, notifSetPage, notifSetPageSize, notifSetAppFilter,
    notifToggleSelectAll, notifBulkRead, notifBulkDelete, notifBulkArchive,
} = globalThis.__notifTest;
if (typeof notifToggleSelectAll !== 'function') {
    throw new Error('notifToggleSelectAll introuvable après chargement du module');
}

// --- Jeu de données + mock api ---
function msg(i) {
    return {
        id: i, title: `Notif ${i}`, body: 'x', priority: 'info',
        is_read: false, sender_app_slug: 'exemple-app', sender_type: 'app',
        created_at: '2026-11-15T10:00:00Z', occurrences: 1,
    };
}

let apiCalls = { getMyMessages: [], getMessages: [], markMessageRead: 0, deleteMyBatch: [], deleteMessagesByIds: [] };

function setupInbox(total) {
    apiCalls = { getMyMessages: [], getMessages: [], markMessageRead: 0, deleteMyBatch: [], deleteMessagesByIds: [] };
    const all = Array.from({ length: total }, (_, k) => msg(k + 1));
    globalThis.api = {
        async getMyMessages(params) {
            apiCalls.getMyMessages.push(params);
            const page = params.page || 1, size = params.pageSize || 20;
            return {
                messages: all.slice((page - 1) * size, page * size),
                total: all.length, page, page_size: size,
                pages: Math.max(1, Math.ceil(all.length / size)),
                unread_total: all.length,
            };
        },
        async getMessages(params) {
            apiCalls.getMessages.push(params);
            const page = params.page || 1, size = params.perPage || 50;
            return {
                messages: all.slice((page - 1) * size, page * size),
                total: all.length, page, per_page: size,
                pages: Math.max(1, Math.ceil(all.length / size)),
            };
        },
        async markMessageRead() { apiCalls.markMessageRead++; return { marked_read: 1 }; },
        async deleteMyBatch(ids) { apiCalls.deleteMyBatch.push(ids); return { hidden: ids.length }; },
        async deleteMessagesByIds(ids, hard) { apiCalls.deleteMessagesByIds.push({ ids, hard }); return { affected: ids.length, deleted: ids.length }; },
    };
    // État module : inbox, page 1, 20/page, filtres par défaut, sélection vide
    _notif.mode = 'inbox';
    _notif.page = 1;
    _notif.pageSize = 20;
    _notif.filters = { unread: false, urgent: false, priority: null, app: null, search: '', status: null };
    _notif.selection.clear();
    rowCheckboxes = [];
    headerCb.checked = false; headerCb.indeterminate = false;
    toasts.length = 0;
    return all;
}

// Réinitialise les checkboxes de ligne comme le ferait le rendu du tableau
function fakePageRows(ids) {
    rowCheckboxes = ids.map(id => makeEl({ dataset: { arg1: String(id) } }));
    // Le rendu réel marque checked selon la sélection courante
    rowCheckboxes.forEach(cb => { cb.checked = _notif.selection.has(parseInt(cb.dataset.arg1, 10)); });
}

// ==========================================================================
// 1. _chunkArray
// ==========================================================================
{
    assert.deepEqual(_chunkArray([], 500), []);
    assert.deepEqual(_chunkArray([1, 2, 3], 500), [[1, 2, 3]]);
    assert.deepEqual(_chunkArray([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
    assert.deepEqual(_chunkArray(Array.from({ length: 750 }, (_, i) => i), 500).map(c => c.length), [500, 250]);
    console.log('ok 1 — _chunkArray');
}

// ==========================================================================
// 2. Select-all étendu : sélectionne TOUTES les pages (inbox, 250 items)
// ==========================================================================
{
    setupInbox(250);
    await loadNotifications();
    fakePageRows(Array.from({ length: 20 }, (_, k) => k + 1)); // page affichée = 20 items

    const callsBefore = apiCalls.getMyMessages.length;
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    const selCalls = apiCalls.getMyMessages.slice(callsBefore);

    assert.equal(_notif.selection.size, 250, 'la sélection doit couvrir les 250 items, pas la page');
    assert.ok(selCalls.every(p => p.pageSize === 100), 'fetch paginé au page_size max (100)');
    assert.deepEqual(selCalls.map(p => p.page), [1, 2, 3], 'pages 1..3 fetchées');
    assert.equal(els.get('notifSelCount').textContent, 250, 'barre de sélection = total');
    assert.ok(rowCheckboxes.every(cb => cb.checked), 'cases de la page affichée cochées');
    assert.ok(toasts.some(t => t.msg.includes('toutes pages confondues')), 'toast de sélection étendue');
    assert.equal(headerCb.checked, true, 'header coché (page entière sélectionnée)');
    console.log('ok 2 — select-all étendu (250 items, 3 pages fetchées)');
}

// ==========================================================================
// 3. La sélection survit au changement de page / page size
// ==========================================================================
{
    notifSetPage(2);
    assert.equal(_notif.selection.size, 250, 'sélection conservée au changement de page');
    assert.equal(_notif.page, 2);

    notifSetPageSize(makeEl({ value: '50' }));
    assert.equal(_notif.selection.size, 250, 'sélection conservée au changement de page size');

    notifSetAppFilter(makeEl({ value: 'autre-app' }));
    assert.equal(_notif.selection.size, 0, 'sélection vidée par un changement de filtre');
    console.log('ok 3 — persistance de la sélection (page/pageSize) + reset filtre');
}

// ==========================================================================
// 4. Décocher le select-all vide TOUTE la sélection
// ==========================================================================
{
    setupInbox(60);
    await loadNotifications();
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    assert.equal(_notif.selection.size, 60);

    headerCb.checked = false;
    await notifToggleSelectAll(headerCb);
    assert.equal(_notif.selection.size, 0, 'décochage = reset complet');
    assert.equal(headerCb.checked, false);
    assert.equal(headerCb.indeterminate, false);
    console.log('ok 4 — unselect-all complet');
}

// ==========================================================================
// 5. Header indéterminé sur sélection partielle de la page
// ==========================================================================
{
    setupInbox(60);
    await loadNotifications();
    fakePageRows(Array.from({ length: 20 }, (_, k) => k + 1));
    _notif.selection.add(1); _notif.selection.add(2);
    _syncNotifSelectionBar();
    assert.equal(headerCb.indeterminate, true, 'sélection partielle => indéterminé');
    assert.equal(headerCb.checked, false);
    console.log('ok 5 — état indéterminé du header');
}

// ==========================================================================
// 6. notifBulkRead : POST unitaires gradués par lots de 50
// ==========================================================================
{
    setupInbox(120);
    await loadNotifications();
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    assert.equal(_notif.selection.size, 120);

    await notifBulkRead();
    assert.equal(apiCalls.markMessageRead, 120, '120 POST unitaires');
    assert.ok(toasts.some(t => t.msg.startsWith('120 notification(s) marquée(s)'), 'toast read 120'));
    assert.equal(_notif.selection.size, 0, 'sélection vidée après action');
    console.log('ok 6 — bulk read gradué (120 POST)');
}

// ==========================================================================
// 7. Suppression boîte : cap 500 => lots (750 items => 2 requêtes)
// ==========================================================================
{
    setupInbox(750);
    await loadNotifications();
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    assert.equal(_notif.selection.size, 750);

    await notifBulkDelete();
    assert.deepEqual(apiCalls.deleteMyBatch.map(c => c.length), [500, 250], 'lots de 500 max');
    assert.ok(toasts.some(t => t.msg === '750 notification(s) supprimée(s).'), 'total agrégé dans le toast');
    assert.equal(_notif.selection.size, 0);
    console.log('ok 7 — bulk delete inbox en 2 lots (500 + 250)');
}

// ==========================================================================
// 8. Mode Gestion : select-all via GET /messages + archivage en lots
// ==========================================================================
{
    setupInbox(300);
    _notif.mode = 'manage';
    await loadNotifications(); // => notifRenderManage
    const callsBefore = apiCalls.getMessages.length;
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    const selCalls = apiCalls.getMessages.slice(callsBefore);

    assert.equal(_notif.selection.size, 300);
    assert.ok(selCalls.length >= 3, 'fetch paginé côté Gestion');
    assert.ok(selCalls.every(p => p.perPage === 100), 'perPage max 100 côté Gestion');

    await notifBulkArchive();
    assert.deepEqual(apiCalls.deleteMessagesByIds.map(c => c.hard), [false], 'archivage soft');
    assert.ok(apiCalls.deleteMessagesByIds.every(c => c.ids.length <= 500));
    assert.ok(toasts.some(t => t.msg === '300 message(s) archivé(s).'));
    console.log('ok 8 — mode Gestion : select-all + archivage');

    // Hard delete en 2 lots (750)
    setupInbox(750);
    _notif.mode = 'manage';
    await loadNotifications();
    headerCb.checked = true;
    await notifToggleSelectAll(headerCb);
    await notifBulkDelete(); // manage => hard delete
    assert.deepEqual(apiCalls.deleteMessagesByIds.map(c => c.hard), [true, true]);
    assert.deepEqual(apiCalls.deleteMessagesByIds.map(c => c.ids.length), [500, 250]);
    assert.ok(toasts.some(t => t.msg === '750 message(s) supprimé(s) définitivement.'));
    console.log('ok 8b — mode Gestion : hard delete en 2 lots');
}

console.log('\nTOUS LES TESTS PASSENT');
