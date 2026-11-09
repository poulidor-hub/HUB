// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Console Notifications unifiée
 *
 * Une page unique adaptée au rôle :
 *  - mode « Ma boîte » (tous les utilisateurs) : tableau paginé, chips avec
 *    compteurs, sélection multiple + actions rapides, détail slide-over avec
 *    auto mark-read, « Vider les lus », regroupement dépliable des alertes
 *    identiques (occurrences) — implémenté ici (Task 6) ;
 *  - mode « Gestion » (admins) : Task 7 (rendu placeholder en attendant).
 *
 * Reprend de l'ancienne boîte de réception (messages-inbox.js) : les cartes
 * flottantes SSE, les badges (cloche + navbar), les préférences de badge
 * admin et la migration des ids masqués du localStorage vers la suppression
 * serveur (receipt.hidden_at).
 *
 * Les fonctions restent globales (window.*) : le délégateur js/app.js résout
 * les data-action du markup #notificationsSection.
 */

// =============================================================================
// État du module
// =============================================================================

const _notif = {
    mode: 'inbox',        // 'inbox' | 'manage'
    page: 1,
    pageSize: 20,
    filters: { unread: false, urgent: false, priority: null, app: null, search: '', status: null },
    selection: new Set(),
    expandedGroups: new Set(),
};
let _notifMessages = [];      // items de la page courante (MessageBrief)
let _notifTotal = 0;
let _notifPages = 1;
let _notifUnreadTotal = 0;    // unread_total de la réponse /me (badge cloche + navbar)
let _notifSelectedId = null;  // message affiché dans le slide-over #notifDetailPanel
let _notifMarkReadTimer = null;
let _notifSearchTimer = null;
const _notifGroupMembers = new Map(); // messageId -> membres du groupe (refetch à l'expansion)

// Ancien stockage localStorage des messages masqués (hub_inbox_hidden_ids) :
// migré vers la suppression serveur puis retiré (migrateInboxHiddenIds).
const INBOX_HIDDEN_KEY = 'hub_inbox_hidden_ids';
let _hiddenMigrated = false;

// Préférence GLOBALE (instance) : priorités comptées dans le badge navbar messages.
// Défaut en mémoire = toutes, jusqu'au retour du GET (repli = toutes si erreur).
let messagesBadgePriorities = ['info', 'warning', 'urgent'];

// =============================================================================
// Migration localStorage → suppression serveur
// =============================================================================

/**
 * Migre (une seule fois par session) les ids masqués de l'ancienne inbox
 * (localStorage hub_inbox_hidden_ids) vers la suppression serveur réelle
 * (POST /me/delete-batch). Appelé en tête de showNotificationsPage().
 */
function migrateInboxHiddenIds() {
    if (_hiddenMigrated) return;
    _hiddenMigrated = true;
    let ids = [];
    try { ids = JSON.parse(localStorage.getItem(INBOX_HIDDEN_KEY) || '[]'); } catch { ids = []; }
    if (!Array.isArray(ids) || ids.length === 0) return;
    api.deleteMyBatch(ids).catch(() => {});
    localStorage.removeItem(INBOX_HIDDEN_KEY);
}

// =============================================================================
// Routeur / mode
// =============================================================================

/**
 * Point d'entrée du routeur showPage — cases 'inbox', 'messages' et
 * 'notifications'. mode = 'inbox' | 'manage' (manage réservé aux admins).
 */
function showNotificationsPage(mode) {
    migrateInboxHiddenIds();
    _notif.mode = (mode === 'manage' && currentUser?.is_admin) ? 'manage' : 'inbox';
    _notif.page = 1;
    _notif.selection.clear();
    _notif.expandedGroups.clear();
    _notifGroupMembers.clear();
    closeNotifDetail();
    notifSyncModeUI();
    loadNotifications();
}

/**
 * Bascule de mode depuis le sélecteur segmenté (admins uniquement).
 */
function notifSetMode(mode) {
    const target = (mode === 'manage' && currentUser?.is_admin) ? 'manage' : 'inbox';
    if (_notif.mode === target) return;
    _notif.mode = target;
    _notif.page = 1;
    _notif.selection.clear();
    _notif.expandedGroups.clear();
    _notifGroupMembers.clear();
    closeNotifDetail();
    notifSyncModeUI();
    loadNotifications();
}

function notifSyncModeUI() {
    const isAdmin = !!currentUser?.is_admin;
    const mode = _notif.mode;
    document.getElementById('notifModeSwitch')?.classList.toggle('d-none', !isAdmin);
    document.getElementById('notifModeBtnInbox')?.classList.toggle('active', mode === 'inbox');
    document.getElementById('notifModeBtnManage')?.classList.toggle('active', mode === 'manage');
    document.getElementById('notifModeInbox')?.classList.toggle('d-none', mode !== 'inbox');
    document.getElementById('notifModeManage')?.classList.toggle('d-none', mode !== 'manage');
    document.getElementById('btnBulkArchive')?.classList.toggle('d-none', mode !== 'manage');
    // Chips de statut : visibles uniquement en mode Gestion
    ['notifChipActive', 'notifChipArchived', 'notifChipExpired'].forEach(id => {
        document.getElementById(id)?.classList.toggle('d-none', mode !== 'manage');
    });
    const heading = document.getElementById('notifHeading');
    if (heading) {
        heading.innerHTML = mode === 'manage'
            ? '<i class="bi bi-gear me-2"></i>Gestion des messages'
            : '<i class="bi bi-bell me-2"></i>Mes notifications';
    }
}

// =============================================================================
// Chargement (mode Ma boîte)
// =============================================================================

async function loadNotifications() {
    if (_notif.mode === 'manage') { _renderManagePlaceholder(); return; }

    const f = _notif.filters;
    const params = {
        page: _notif.page,
        pageSize: _notif.pageSize,
        unread: f.unread,
        priority: f.priority || (f.urgent ? 'urgent' : null),
        senderApp: f.app,
        search: f.search,
        groupSimilar: true,
    };

    try {
        const data = await api.getMyMessages(params);
        _notifMessages = data.messages || [];
        _notifTotal = data.total || 0;
        _notifPages = data.pages || 1;
        _notifUnreadTotal = data.unread_total || 0;

        // Hors bornes (ex. dernière page vidée par des suppressions) : revenir
        // en page valide, jamais de liste vide incohérente.
        if (_notif.page > _notifPages && _notif.page > 1) {
            _notif.page = _notifPages;
            return loadNotifications();
        }

        _notif.expandedGroups.clear();
        _notifGroupMembers.clear();
        renderNotifTable();
        renderNotifChips();
        renderNotifPagination();
        _renderNotifAppFilter();
        _syncNotifSelectionBar();
        updateMessageBellBadge(_notifUnreadTotal);
    } catch (e) {
        const tbody = document.getElementById('notifTableBody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger py-4">Erreur de chargement des notifications</td></tr>';
    }
}

// =============================================================================
// Rendu du tableau
// =============================================================================

function renderNotifTable() {
    const thead = document.getElementById('notifTableHead');
    const tbody = document.getElementById('notifTableBody');
    if (!thead || !tbody) return;

    if (_notif.mode === 'manage') { _renderManagePlaceholder(); return; }

    thead.innerHTML = `<tr>
        <th style="width:2.2rem;"><input type="checkbox" class="form-check-input" title="Tout sélectionner" aria-label="Tout sélectionner" data-action="notifToggleSelectAll" data-args="$el"></th>
        <th>Titre</th>
        <th style="width:11rem;">App</th>
        <th style="width:7.5rem;">Reçu</th>
        <th style="width:7rem;">État</th>
        <th style="width:6rem;" class="text-end">Actions</th>
    </tr>`;

    if (_notifMessages.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-5"><i class="bi bi-inbox" style="font-size:2.5rem;opacity:.3;"></i><div class="mt-2">Aucune notification</div></td></tr>';
        return;
    }

    tbody.innerHTML = _notifMessages.map(m => _notifRowHtml(m)).join('');
}

function _notifPriorityIcon(p) {
    if (p === 'urgent') return '<i class="bi bi-exclamation-octagon-fill text-danger me-1" title="Urgent"></i>';
    if (p === 'warning') return '<i class="bi bi-exclamation-triangle-fill text-warning me-1" title="Warning"></i>';
    return '<i class="bi bi-info-circle-fill text-info me-1" title="Info"></i>';
}

function _notifRowHtml(m) {
    const prio = m.priority || 'info';
    const occ = m.occurrences || 1;
    const expanded = _notif.expandedGroups.has(m.id);
    const expander = occ > 1
        ? `<button type="button" class="btn btn-sm btn-link p-0 me-1 align-baseline" data-action="notifExpandGroup" data-arg1="${m.id}" title="${expanded ? 'Masquer' : 'Afficher'} les ${occ} notifications identiques">×${occ} <i class="bi ${expanded ? 'bi-caret-up-fill' : 'bi-caret-down-fill'} small"></i></button>`
        : '';
    const appLabel = m.sender_app_slug || (m.sender_type === 'system' ? 'Système' : 'Administration');
    const received = parseUTCDate(m.created_at);
    const stateBadge = m.is_read
        ? '<span class="badge bg-secondary">Lu</span>'
        : '<span class="badge bg-primary">Non lu</span>';
    const expiredBadge = _isExpired(m) ? ' <span class="badge bg-dark">Expiré</span>' : '';

    return `<tr class="${m.is_read ? '' : 'fw-semibold'}">
        <td><input type="checkbox" class="form-check-input" ${_notif.selection.has(m.id) ? 'checked' : ''} aria-label="Sélectionner" data-action="notifToggleSelect" data-arg1="${m.id}" data-arg2="$el"></td>
        <td role="button" data-action="openNotifDetail" data-arg1="${m.id}" class="text-break">${expander}${_notifPriorityIcon(prio)}${escapeHtml(m.title)}</td>
        <td class="small text-muted">${escapeHtml(appLabel)}</td>
        <td class="small text-muted" title="${received.toLocaleString('fr-FR')}">${_relativeTime(received)}</td>
        <td>${stateBadge}${expiredBadge}</td>
        <td class="text-end text-nowrap">
            <button type="button" class="btn btn-sm btn-outline-success" data-action="notifMarkRead" data-arg1="${m.id}" title="Marquer comme lu" ${m.is_read ? 'disabled' : ''}><i class="bi bi-check2"></i></button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="notifDeleteOne" data-arg1="${m.id}" title="Supprimer de ma boîte"><i class="bi bi-trash"></i></button>
        </td>
    </tr>${expanded ? _notifGroupSubRows(m) : ''}`;
}

/**
 * Sous-lignes d'un groupe déplié : les autres occurrences (le représentant
 * étant la ligne principale).
 */
function _notifGroupSubRows(m) {
    const members = (_notifGroupMembers.get(m.id) || []).filter(x => x.id !== m.id);
    if (members.length === 0) return '';
    return members.map(x => {
        const received = parseUTCDate(x.created_at);
        const source = x.sender_app_slug || (x.sender_type === 'system' ? 'Système' : 'Administration');
        return `<tr class="table-light small">
        <td></td>
        <td class="ps-4 text-break"><i class="bi bi-arrow-return-right text-muted me-1"></i>${escapeHtml(x.title)}</td>
        <td class="text-muted">${escapeHtml(source)}</td>
        <td class="text-muted" title="${received.toLocaleString('fr-FR')}">${_relativeTime(received)}</td>
        <td>${x.is_read ? '<span class="badge bg-secondary">Lu</span>' : '<span class="badge bg-primary">Non lu</span>'}</td>
        <td class="text-end text-nowrap">
            <button type="button" class="btn btn-sm btn-outline-success" data-action="notifMarkRead" data-arg1="${x.id}" title="Marquer comme lu" ${x.is_read ? 'disabled' : ''}><i class="bi bi-check2"></i></button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="notifDeleteOne" data-arg1="${x.id}" title="Supprimer de ma boîte"><i class="bi bi-trash"></i></button>
        </td>
    </tr>`;
    }).join('');
}

/**
 * Mode Gestion : rendu complet en Task 7 (notifRenderManage). Placeholder
 * volontaire pour que la page reste cohérente dès maintenant.
 */
function _renderManagePlaceholder() {
    const thead = document.getElementById('notifTableHead');
    const tbody = document.getElementById('notifTableBody');
    if (thead) thead.innerHTML = '';
    if (tbody) {
        tbody.innerHTML = '<tr><td class="text-center text-muted py-5"><i class="bi bi-gear" style="font-size:2.5rem;opacity:.3;"></i><div class="mt-2">Mode Gestion — rendu à venir.</div></td></tr>';
    }
    const pg = document.getElementById('notifPagination');
    if (pg) pg.innerHTML = '';
    _syncNotifSelectionBar();
}

// =============================================================================
// Chips, filtres, pagination
// =============================================================================

function renderNotifChips() {
    const f = _notif.filters;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('notifChipAllCount', _notifTotal);
    set('notifChipUnreadCount', _notifUnreadTotal);
    // Pas de compteur serveur dédié : estimation sur la page courante (occurrences sommées)
    set('notifChipUrgentCount', _notifMessages.reduce((s, m) => s + (m.priority === 'urgent' ? (m.occurrences || 1) : 0), 0));

    let activeId = 'notifChipAll';
    if (f.unread) activeId = 'notifChipUnread';
    else if (f.urgent) activeId = 'notifChipUrgent';
    else if (f.status) activeId = 'notifChip' + f.status.charAt(0).toUpperCase() + f.status.slice(1);
    ['notifChipAll', 'notifChipUnread', 'notifChipUrgent', 'notifChipActive', 'notifChipArchived', 'notifChipExpired']
        .forEach(id => document.getElementById(id)?.classList.toggle('active', id === activeId));
}

function notifSetChip(chip) {
    const f = _notif.filters;
    f.unread = false;
    f.urgent = false;
    f.status = null;
    if (chip === 'unread') f.unread = true;
    else if (chip === 'urgent') f.urgent = true;
    else if (chip === 'active' || chip === 'archived' || chip === 'expired') f.status = chip;
    // 'all' → réinitialisation
    _notif.page = 1;
    _notif.selection.clear();
    loadNotifications();
}

function notifSetAppFilter(el) {
    _notif.filters.app = el?.value || null;
    _notif.page = 1;
    _notif.selection.clear();
    loadNotifications();
}

function _renderNotifAppFilter() {
    const sel = document.getElementById('notifAppFilter');
    if (!sel) return;
    const current = _notif.filters.app || '';
    // Uniquement les vrais slugs d'app : le paramètre sender_app du backend
    // ne sait pas filtrer les messages admin/système (sender_app_slug null).
    const slugs = [...new Set(_notifMessages.map(m => m.sender_app_slug).filter(Boolean))];
    if (current && !slugs.includes(current)) slugs.unshift(current);
    let html = '<option value="">Toutes les sources</option>';
    for (const slug of slugs) {
        html += `<option value="${escapeHtml(slug)}"${slug === current ? ' selected' : ''}>${escapeHtml(slug)}</option>`;
    }
    sel.innerHTML = html;
}

function notifSearchInput(el) {
    if (_notifSearchTimer) clearTimeout(_notifSearchTimer);
    _notifSearchTimer = setTimeout(() => {
        _notifSearchTimer = null;
        _notif.filters.search = el?.value?.trim() || '';
        _notif.page = 1;
        _notif.selection.clear();
        loadNotifications();
    }, 300);
}

function notifSetPage(p) {
    p = parseInt(p, 10);
    if (!p || p < 1) return;
    _notif.page = p;
    _notif.selection.clear();
    loadNotifications();
}

function notifSetPageSize(el) {
    const size = parseInt(el?.value, 10);
    if (!size) return;
    _notif.pageSize = size;
    _notif.page = 1;
    _notif.selection.clear();
    loadNotifications();
}

function renderNotifPagination() {
    const container = document.getElementById('notifPagination');
    if (!container) return;
    if (_notifPages <= 1) {
        container.innerHTML = _notifTotal > 0
            ? `<div class="text-center text-muted small">${_notifTotal} notification(s)</div>`
            : '';
        return;
    }

    let html = '<div class="d-flex align-items-center justify-content-center gap-3 flex-wrap">';
    html += '<ul class="pagination pagination-sm mb-0">';
    html += `<li class="page-item ${_notif.page <= 1 ? 'disabled' : ''}"><a class="page-link" href="#" data-action="notifSetPage" data-arg1="${_notif.page - 1}">«</a></li>`;
    for (let i = 1; i <= _notifPages; i++) {
        if (i === 1 || i === _notifPages || (i >= _notif.page - 2 && i <= _notif.page + 2)) {
            html += `<li class="page-item ${i === _notif.page ? 'active' : ''}"><a class="page-link" href="#" data-action="notifSetPage" data-arg1="${i}">${i}</a></li>`;
        } else if (i === _notif.page - 3 || i === _notif.page + 3) {
            html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        }
    }
    html += `<li class="page-item ${_notif.page >= _notifPages ? 'disabled' : ''}"><a class="page-link" href="#" data-action="notifSetPage" data-arg1="${_notif.page + 1}">»</a></li>`;
    html += '</ul>';
    html += `<div class="d-flex align-items-center gap-2 small text-muted">${_notifTotal} notification(s)
        <select class="form-select form-select-sm" style="width:auto" aria-label="Notifications par page" data-action="notifSetPageSize" data-args="$el">
            <option value="20"${_notif.pageSize === 20 ? ' selected' : ''}>20 / page</option>
            <option value="50"${_notif.pageSize === 50 ? ' selected' : ''}>50 / page</option>
            <option value="100"${_notif.pageSize === 100 ? ' selected' : ''}>100 / page</option>
        </select>
    </div>`;
    html += '</div>';
    container.innerHTML = html;
}

// =============================================================================
// Sélection multiple
// =============================================================================

function notifToggleSelect(id, el) {
    id = parseInt(id, 10);
    if (!id) return;
    if (el && el.checked) _notif.selection.add(id);
    else _notif.selection.delete(id);
    _syncNotifSelectionBar();
}

function notifToggleSelectAll(el) {
    const checked = !!el?.checked;
    _notifMessages.forEach(m => {
        if (checked) _notif.selection.add(m.id);
        else _notif.selection.delete(m.id);
    });
    // Sync visuelle des cases sans re-render (préserve le focus/scroll)
    document.querySelectorAll('#notifTableBody input[data-action="notifToggleSelect"]').forEach(cb => { cb.checked = checked; });
    _syncNotifSelectionBar();
}

function _syncNotifSelectionBar() {
    const bar = document.getElementById('notifSelectionBar');
    if (!bar) return;
    const n = _notif.selection.size;
    bar.classList.toggle('d-none', n === 0);
    const count = document.getElementById('notifSelCount');
    if (count) count.textContent = n;
}

// =============================================================================
// Actions rapides
// =============================================================================

async function notifMarkRead(id) {
    id = parseInt(id, 10);
    if (!id) return;
    try {
        await api.markMessageRead(id);
        if (_notifSelectedId === id) {
            const m = _notifMessages.find(x => x.id === id);
            if (m) m.is_read = true;
        }
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifDeleteOne(id) {
    id = parseInt(id, 10);
    if (!id) return;
    if (!confirm('Supprimer cette notification de votre boîte ?')) return;
    try {
        await api.deleteMyMessage(id);
        _notif.selection.delete(id);
        if (_notifSelectedId === id) closeNotifDetail();
        showToast('Supprimée', 'La notification a été retirée de votre boîte.', 'success');
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifBulkRead() {
    const ids = [..._notif.selection];
    if (ids.length === 0) return;
    try {
        await Promise.all(ids.map(id => api.markMessageRead(id).catch(() => {})));
        showToast('Succès', `${ids.length} notification(s) marquée(s) comme lu(s).`, 'success');
        _notif.selection.clear();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifBulkDelete() {
    const ids = [..._notif.selection];
    if (ids.length === 0) return;
    if (!confirm(`Retirer ${ids.length} notification(s) de votre boîte ?`)) return;
    try {
        const result = await api.deleteMyBatch(ids);
        showToast('Succès', `${result.hidden ?? ids.length} notification(s) supprimée(s).`, 'success');
        _notif.selection.clear();
        closeNotifDetail();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifPurgeRead() {
    if (!confirm('Vider toutes les notifications lues de votre boîte ?')) return;
    try {
        const result = await api.purgeMyRead();
        showToast('Succès', `${result.hidden ?? 0} notification(s) lue(s) supprimée(s).`, 'success');
        closeNotifDetail();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

// =============================================================================
// Regroupement dépliable (occurrences)
// =============================================================================

/**
 * Déplie/replie le groupe d'un message : refetch search=<titre> + sender_app
 * puis filtrage local sur la clé exacte du groupe (titre + priorité +
 * coalesce(sender_app_slug, sender_type)), miroir du group_similar SQL.
 */
async function notifExpandGroup(messageId) {
    messageId = parseInt(messageId, 10);
    const m = _notifMessages.find(x => x.id === messageId);
    if (!m) return;

    if (_notif.expandedGroups.has(messageId)) {
        _notif.expandedGroups.delete(messageId);
        renderNotifTable();
        return;
    }

    try {
        const data = await api.getMyMessages({
            search: m.title,
            senderApp: m.sender_app_slug,
            page: 1,
            pageSize: 100,
        });
        const key = (x) => JSON.stringify([x.title, x.priority, x.sender_app_slug || x.sender_type]);
        const members = (data.messages || []).filter(x => key(x) === key(m));
        _notifGroupMembers.set(messageId, members);
        _notif.expandedGroups.add(messageId);
        renderNotifTable();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

// =============================================================================
// Détail slide-over (#notifDetailPanel)
// =============================================================================

function _clearNotifMarkReadTimer() {
    if (_notifMarkReadTimer) { clearTimeout(_notifMarkReadTimer); _notifMarkReadTimer = null; }
}

/**
 * Remplacement de l'ancien _resetInboxDetailView (appelé par auth-navigation
 * au changement de page / déconnexion) : referme le slide-over.
 */
function _resetNotifDetailView() {
    closeNotifDetail();
}

function openNotifDetail(id) {
    id = parseInt(id, 10);
    const m = _notifMessages.find(x => x.id === id);
    if (!m) return;
    _clearNotifMarkReadTimer();
    _notifSelectedId = id;
    _renderNotifDetail(m);
    document.getElementById('notifDetailPanel')?.classList.remove('d-none');

    if (!m.is_read) {
        // Auto mark-read après 1 s (timer historique de la boîte de réception)
        _notifMarkReadTimer = setTimeout(() => {
            _notifMarkReadTimer = null;
            const section = document.getElementById('notificationsSection');
            if (_notifSelectedId !== id || !section || section.classList.contains('d-none')) return;
            api.markMessageRead(id).then(() => {
                m.is_read = true;
                _notifUnreadTotal = Math.max(0, _notifUnreadTotal - 1);
                renderNotifTable();
                renderNotifChips();
                updateMessageBellBadge(_notifUnreadTotal);
            }).catch(() => {});
        }, 1000);
    }
}

function closeNotifDetail() {
    _clearNotifMarkReadTimer();
    _notifSelectedId = null;
    document.getElementById('notifDetailPanel')?.classList.add('d-none');
}

function _renderNotifDetail(m) {
    const prio = m.priority || 'info';
    const prioBadge = prio === 'urgent'
        ? '<span class="badge bg-danger">Urgent</span>'
        : prio === 'warning'
            ? '<span class="badge bg-warning text-dark">Warning</span>'
            : '<span class="badge bg-info text-dark">Info</span>';
    const source = m.sender_app_slug || (m.sender_type === 'system' ? 'Système' : (m.sender_username || 'Administration'));
    const sourceIcon = m.sender_app_slug ? '📦' : (m.sender_type === 'system' ? '⚙' : '👤');
    const created = parseUTCDate(m.created_at).toLocaleString('fr-FR');
    const expires = m.expires_at ? parseUTCDate(m.expires_at).toLocaleString('fr-FR') : null;
    const expiresSoon = m.expires_at && !_isExpired(m) && (parseUTCDate(m.expires_at) - new Date()) < 24 * 3600 * 1000
        ? '<span class="badge bg-warning text-dark">Expire bientôt</span>' : '';
    const readBadge = m.is_read ? '<span class="badge bg-secondary">Lu</span>' : '<span class="badge bg-primary">Non lu</span>';
    const action = m.action_url
        ? `<a href="${escapeHtml(m.action_url)}" target="_blank" rel="noopener" class="btn btn-primary btn-sm" data-action="notifClickAction" data-arg1="${m.id}" data-arg2="${escapeHtml(m.action_url)}"><i class="bi bi-box-arrow-up-right me-1"></i>${escapeHtml(m.action_label || 'Consulter')}</a>`
        : '';

    const titleEl = document.getElementById('notifDetailTitle');
    if (titleEl) titleEl.textContent = m.title || 'Notification';
    const prioEl = document.getElementById('notifDetailPriority');
    if (prioEl) prioEl.innerHTML = prioBadge;

    const body = document.getElementById('notifDetailBody');
    if (!body) return;
    body.innerHTML = `
        <div class="d-flex flex-wrap gap-2 align-items-center mb-2">${prioBadge}${readBadge}${expiresSoon}</div>
        <div class="text-muted small mb-3">
            <div>${sourceIcon} <strong>${escapeHtml(source)}</strong></div>
            <div class="mt-1">Reçu le ${created}</div>
            ${expires ? `<div>Expire le ${expires}</div>` : ''}
        </div>
        <div>${m.body ? escapeHtml(m.body).replace(/\n/g, '<br>') : '<span class="text-muted">(Aucun contenu)</span>'}</div>
        ${action ? `<div class="mt-3">${action}</div>` : ''}
        <hr class="my-3">
        <div class="d-flex gap-2">
            <button type="button" class="btn btn-sm btn-outline-success" data-action="notifMarkRead" data-arg1="${m.id}" title="Marquer comme lu" ${m.is_read ? 'disabled' : ''}>
                <i class="bi bi-check2 me-1"></i>Marquer comme lu
            </button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="notifDeleteOne" data-arg1="${m.id}" title="Supprimer de ma boîte">
                <i class="bi bi-trash me-1"></i>Supprimer
            </button>
        </div>`;
}

/**
 * Lien d'action d'une notification : track le clic (idempotent côté serveur)
 * puis ouvre l'URL.
 */
async function notifClickAction(id, url) {
    try { await api.markMessageClicked(id); } catch { /* suivi du clic best-effort */ }
    if (url) window.open(url, '_blank', 'noopener');
}

// =============================================================================
// Helpers dates
// =============================================================================

function _isExpired(m) { return m.expires_at && parseUTCDate(m.expires_at) < new Date(); }

function _relativeTime(date) {
    const diffMs = new Date() - date; const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "à l'instant"; if (mins < 60) return `Il y a ${mins} min`;
    const hours = Math.floor(mins / 60); if (hours < 24) return `Il y a ${hours}h`;
    const days = Math.floor(hours / 24); if (days < 7) return `Il y a ${days}j`;
    return date.toLocaleDateString('fr-FR');
}

// =============================================================================
// Badges (cloche utilisateur + lien navbar Messages, désormais public)
// =============================================================================

async function loadUnreadMessageCount() {
    try {
        const result = await api.getUnreadCount();
        updateMessageBellBadge(result.count || 0);
    } catch (e) { /* Silencieux */ }
}

function updateMessageBellBadge(count) {
    // Cloche de notifications (tous les utilisateurs)
    const bellBadge = document.getElementById('messageBellBadge');
    if (bellBadge) {
        if (count > 0) {
            bellBadge.textContent = count > 99 ? '99+' : count;
            bellBadge.style.display = 'inline';
        } else {
            bellBadge.style.display = 'none';
        }
    }
    // Badge du lien navbar Messages (public depuis la console unifiée)
    const navBadge = document.getElementById('navMessagesBadge');
    if (navBadge) {
        if (count > 0) {
            navBadge.textContent = count > 99 ? '99+' : count;
            navBadge.style.display = 'inline';
        } else {
            navBadge.style.display = 'none';
        }
    }
}

function _badgeCountFromStats(stats) {
    if (!stats) return 0;
    const byPriority = stats.by_priority || {};
    return messagesBadgePriorities.reduce((sum, p) => sum + (byPriority[p] || 0), 0);
}

function _applyNavMessagesBadgeCount(count) {
    const navBadge = document.getElementById('navMessagesBadge');
    if (!navBadge) return;
    if (count > 0) {
        navBadge.textContent = count > 99 ? '99+' : count;
        navBadge.style.display = 'inline';
    } else {
        navBadge.style.display = 'none';
    }
}

async function updateNavMessagesBadge() {
    try {
        const stats = await api.getMessageStats(null, undefined);
        _applyNavMessagesBadgeCount(_badgeCountFromStats(stats));
    } catch (e) { /* Silencieux */ }
}

// =============================================================================
// Préférence badge navbar : priorités comptées (globale instance, admin)
// =============================================================================

function _setMessagesBadgePriorities(priorities) {
    if (!Array.isArray(priorities)) {
        messagesBadgePriorities = ['info', 'warning', 'urgent'];
        return;
    }
    // [] conservé tel quel = badge masqué (comportement validé)
    messagesBadgePriorities = ['info', 'warning', 'urgent'].filter(p => priorities.includes(p));
}

async function loadMessagesBadgePriorities() {
    try {
        const result = await api.getBadgePriorities();
        _setMessagesBadgePriorities(result.priorities);
    } catch (e) {
        // Repli silencieux : toutes les priorités (comportement historique)
        messagesBadgePriorities = ['info', 'warning', 'urgent'];
    }
    _syncBadgePrefsUI();
}

function _syncBadgePrefsUI() {
    ['info', 'warning', 'urgent'].forEach(p => {
        const cb = document.getElementById(`msgBadgePref-${p}`);
        if (cb) cb.checked = messagesBadgePriorities.includes(p);
    });
}

async function saveMessagesBadgePriorities() {
    const priorities = ['info', 'warning', 'urgent']
        .filter(p => document.getElementById(`msgBadgePref-${p}`)?.checked);
    try {
        const result = await api.updateBadgePriorities(priorities);
        _setMessagesBadgePriorities(result.priorities);
        updateNavMessagesBadge(); // recalcul immédiat pour cet admin
    } catch (e) {
        showToast('Erreur', e.message || 'Impossible d\'enregistrer la préférence', 'danger');
        _syncBadgePrefsUI(); // revert visuel des checkboxes
    }
}

// Wiring des checkboxes (HTML statique de la SPA → présentes au chargement).
// data-bs-auto-close="outside" laisse le popover ouvert pendant les clics.
['info', 'warning', 'urgent'].forEach(p => {
    document.getElementById(`msgBadgePref-${p}`)?.addEventListener('change', saveMessagesBadgePriorities);
});

// =============================================================================
// SSE : message entrant + cartes flottantes (top-right)
// =============================================================================

function handleIncomingMessage(msgData) {
    const badge = document.getElementById('messageBellBadge');
    if (badge) {
        const current = parseInt(badge.textContent) || 0;
        updateMessageBellBadge(current + 1);
    }
    showNotificationCard(msgData);
    const section = document.getElementById('notificationsSection');
    if (section && !section.classList.contains('d-none')) { loadNotifications(); }
}

const _activeNotificationCards = [];

function showNotificationCard(msgData) {
    const container = document.getElementById('notificationBanner');
    if (!container) return;

    // Seul un id DB plausible (entier positif ≤ int32) autorise un mark-read.
    // Sans id valide (ex. ancien payload d'alerte), id d'affichage anonyme :
    // la carte se ferme sans POST /read — sinon on rappelait
    // POST /api/messages/<Date.now()>/read → overflow int32 (incident 09/11/2026).
    const dbId = Number(msgData.id);
    const hasValidId = Number.isInteger(dbId) && dbId > 0 && dbId <= 2147483647;
    const id = hasValidId ? dbId : `anon-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
    const priority = msgData.priority || 'normal';

    const icons = {
        urgent: '<svg class="notif-card-icon-svg notif-icon-urgent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
        warning: '<svg class="notif-card-icon-svg notif-icon-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        normal: '<svg class="notif-card-icon-svg notif-icon-normal" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    };

    const time = msgData.created_at
        ? parseUTCDate(msgData.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
        : new Date().toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

    const card = document.createElement('div');
    card.className = `notif-card notif-card-${priority}`;
    card.dataset.notifId = id;

    card.innerHTML = `
        <div class="notif-card-icon">${icons[priority] || icons.normal}</div>
        <div class="notif-card-content">
            <div class="notif-card-header">
                <strong class="notif-card-title">${escapeHtml(msgData.title || 'Notification')}</strong>
                    <button class="notif-card-close" data-action="dismissNotificationCard" data-arg1="${id}" data-arg2="true" title="Fermer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            ${msgData.body ? `<p class="notif-card-body">${escapeHtml(msgData.body)}</p>` : ''}
            ${msgData.action_url ? `<div class="notif-card-action-wrapper"><a href="${escapeHtml(msgData.action_url)}" target="_blank" rel="noopener noreferrer" class="notif-card-action" data-action="openNotificationAction" data-arg1="${id}" data-arg2="${escapeHtml(msgData.action_url)}">${escapeHtml(msgData.action_label || 'Consulter')}<svg class="notif-card-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a></div>` : ''}
            <div class="notif-card-meta">
                ${(msgData.sender_app_slug || msgData.sender_type) ? `<span class="notif-card-sender"><span class="notif-card-sender-dot"></span>${escapeHtml(msgData.sender_app_slug || msgData.sender_type)}</span>` : ''}
                <span class="notif-card-time"><svg class="notif-card-time-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>${time}</span>
            </div>
        </div>`;

    _activeNotificationCards.push(id);
    container.appendChild(card);
    updateNotificationBannerControls();

    if (priority !== 'urgent') { setTimeout(() => dismissNotificationCard(id, false), 15000); }
}

function dismissNotificationCard(id, markAsRead = false) {
    // markAsRead peut venir d'un data-arg2 (chaîne "true"/"false")
    const shouldMarkRead = markAsRead === true || markAsRead === 'true';
    const container = document.getElementById('notificationBanner');
    const card = container?.querySelector(`[data-notif-id="${id}"]`);
    if (card) {
        card.style.animation = 'notifCardSlideOut 0.25s ease-in forwards';
        setTimeout(() => {
            card.remove();
            const idx = _activeNotificationCards.indexOf(id);
            if (idx !== -1) _activeNotificationCards.splice(idx, 1);
            updateNotificationBannerControls();
        }, 250);

        if (shouldMarkRead && typeof id === 'number') {
            api.markMessageRead(id).then(() => {
                const badge = document.getElementById('messageBellBadge');
                const current = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, current - 1));
                const section = document.getElementById('notificationsSection');
                if (section && !section.classList.contains('d-none')) { loadNotifications(); }
            }).catch(() => {});
        }
    }
}

/**
 * data-action des liens d'action des notifications :
 * le délégateur preventDefault() le <a>, on gère l'ouverture ici.
 */
function openNotificationAction(id, url) {
    dismissNotificationCard(id, true);
    if (url) window.open(url, '_blank', 'noopener');
}

function dismissAllNotificationCards() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    const cardIds = Array.from(container.querySelectorAll('.notif-card')).map(c => parseInt(c.dataset.notifId)).filter(id => !isNaN(id));
    container.querySelectorAll('.notif-card').forEach(card => { card.style.animation = 'notifCardSlideOut 0.2s ease-in forwards'; });
    setTimeout(() => {
        container.querySelectorAll('.notif-card').forEach(c => c.remove());
        _activeNotificationCards.length = 0; updateNotificationBannerControls();
    }, 250);
    if (cardIds.length > 0) {
        Promise.all(cardIds.map(id => api.markMessageRead(id).catch(() => {}))).then(() => {
            loadUnreadMessageCount();
            const section = document.getElementById('notificationsSection');
            if (section && !section.classList.contains('d-none')) { loadNotifications(); }
        });
    }
}

function updateNotificationBannerControls() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    let controls = container.querySelector('.notif-banner-controls');
    const cards = container.querySelectorAll('.notif-card');
    if (cards.length > 1) {
        if (!controls) { controls = document.createElement('div'); controls.className = 'notif-banner-controls'; container.prepend(controls); }
        controls.innerHTML = `<span class="notif-banner-count">${cards.length} notifications</span><button class="notif-banner-dismiss-all" data-action="dismissAllNotificationCards">Tout fermer<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
    } else if (controls) { controls.remove(); }
}
