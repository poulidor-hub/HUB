// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Console Notifications unifiée
 *
 * Une page unique adaptée au rôle :
 *  - mode « Ma boîte » (tous les utilisateurs) : tableau paginé, chips avec
 *    compteurs, sélection multiple + actions rapides, détail slide-over avec
 *    auto mark-read, « Vider les lus », regroupement dépliable des alertes
 *    identiques (occurrences) ;
 *  - mode « Gestion » (admins) : vue système des messages (GET /messages avec
 *    group_similar), chips statut/priorité/source, sélection → archivage /
 *    suppression en masse, taux de lecture, modal de purge par critère.
 *
 * Reprend de l'ancien module inbox (supprimé en Task 7) : les cartes
 * flottantes SSE, les badges (cloche + navbar), les préférences de badge
 * admin et la migration des ids masqués du localStorage vers la suppression
 * serveur (receipt.hidden_at). Le compose vit dans messages-compose.js.
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
    _notifSanitizeFiltersForMode();
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
    _notifSanitizeFiltersForMode();
    _notif.page = 1;
    _notif.selection.clear();
    _notif.expandedGroups.clear();
    _notifGroupMembers.clear();
    closeNotifDetail();
    notifSyncModeUI();
    loadNotifications();
}

/**
 * Les chips visibles diffèrent selon le mode : les statuts (actif/archivé/
 * expiré) n'existent que côté Gestion (GET /messages?status=…), le filtre
 * « non lus » que côté Ma boîte (GET /me?unread=true). Un filtre qui change
 * de mode sans support resterait actif de façon invisible.
 */
function _notifSanitizeFiltersForMode() {
    if (_notif.mode === 'manage') {
        _notif.filters.unread = false;
    } else {
        _notif.filters.status = null;
    }
}

function notifSyncModeUI() {
    const isAdmin = !!currentUser?.is_admin;
    const mode = _notif.mode;
    document.getElementById('notifModeSwitch')?.classList.toggle('d-none', !isAdmin);
    document.getElementById('notifModeBtnInbox')?.classList.toggle('active', mode === 'inbox');
    document.getElementById('notifModeBtnManage')?.classList.toggle('active', mode === 'manage');
    document.getElementById('notifModeInbox')?.classList.toggle('d-none', mode !== 'inbox');
    document.getElementById('notifModeManage')?.classList.toggle('d-none', mode !== 'manage');
    // Barre de sélection : Ma boîte = Marquer lu / Supprimer de ma boîte ;
    // Gestion = Archiver / Supprimer définitivement.
    document.getElementById('btnBulkRead')?.classList.toggle('d-none', mode === 'manage');
    document.getElementById('btnBulkArchive')?.classList.toggle('d-none', mode !== 'manage');
    // Chips de statut : visibles uniquement en mode Gestion ; le chip « Non
    // lus » n'a pas d'équivalent GET /messages (vue système) : Gestion seul.
    ['notifChipActive', 'notifChipArchived', 'notifChipExpired'].forEach(id => {
        document.getElementById(id)?.classList.toggle('d-none', mode !== 'manage');
    });
    document.getElementById('notifChipUnread')?.classList.toggle('d-none', mode === 'manage');
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
    if (_notif.mode === 'manage') { await notifRenderManage(); return; }

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

// =============================================================================
// Mode Gestion (admin) — vue système des messages
// =============================================================================

/**
 * Mode Gestion : GET /messages (vue système, group_similar) + rendu du
 * tableau console. Colonnes : ☑ | P | Titre (+×N ▾) | Source | Cible |
 * Envoyé | État | Lus (x/y) | Actions (⬆/⬇ · 🗑).
 */
async function notifRenderManage() {
    const thead = document.getElementById('notifTableHead');
    const tbody = document.getElementById('notifTableBody');
    if (!thead || !tbody) return;

    const f = _notif.filters;
    const params = {
        page: _notif.page,
        perPage: _notif.pageSize,
        groupSimilar: true,
        status: f.status || undefined,
        priority: f.priority || (f.urgent ? 'urgent' : null),
        senderApp: f.app,
        search: f.search,
    };

    try {
        const data = await api.getMessages(params);
        _notifMessages = data.messages || [];
        _notifTotal = data.total || 0;
        _notifPages = data.pages || 1;

        // Hors bornes (ex. dernière page vidée par une purge) : revenir en
        // page valide plutôt que d'afficher une liste vide incohérente.
        if (_notif.page > _notifPages && _notif.page > 1) {
            _notif.page = _notifPages;
            return notifRenderManage();
        }

        _notif.expandedGroups.clear();
        _notifGroupMembers.clear();
        renderNotifManageTable();
        renderNotifChips();
        renderNotifPagination();
        _renderNotifAppFilter();
        _syncNotifSelectionBar();
    } catch (e) {
        thead.innerHTML = '';
        tbody.innerHTML = '<tr><td colspan="9" class="text-center text-danger py-4">Erreur de chargement des messages</td></tr>';
        const pg = document.getElementById('notifPagination');
        if (pg) pg.innerHTML = '';
        _syncNotifSelectionBar();
    }
}

function renderNotifManageTable() {
    const thead = document.getElementById('notifTableHead');
    const tbody = document.getElementById('notifTableBody');
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr>
        <th style="width:2.2rem;"><input type="checkbox" class="form-check-input" title="Tout sélectionner" aria-label="Tout sélectionner" data-action="notifToggleSelectAll" data-args="$el"></th>
        <th style="width:5.5rem;">P</th>
        <th>Titre</th>
        <th style="width:10rem;">Source</th>
        <th style="width:10rem;">Cible</th>
        <th style="width:7.5rem;">Envoyé</th>
        <th style="width:6.5rem;">État</th>
        <th style="width:8.5rem;">Lus</th>
        <th style="width:6.5rem;" class="text-end">Actions</th>
    </tr>`;

    if (_notifMessages.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-5"><i class="bi bi-envelope-paper" style="font-size:2.5rem;opacity:.3;"></i><div class="mt-2">Aucun message</div></td></tr>';
        return;
    }

    tbody.innerHTML = _notifMessages.map(m => _notifManageRowHtml(m)).join('');
}

/**
 * Chip de priorité (colonne P).
 */
function _notifPriorityChip(p) {
    const prio = p || 'info';
    const label = prio === 'urgent' ? 'Urgent' : prio === 'warning' ? 'Warning' : 'Info';
    return `<span class="notif-chip notif-chip-${prio}">${label}</span>`;
}

/**
 * Badge d'état (cycle de vie) : Actif / Expiré / Archivé — les messages
 * programmés n'ont aucun de ces trois statuts → chip neutre « Programmé ».
 */
function _notifStateChip(m) {
    if (m.status === 'archived') return '<span class="notif-status-badge archived">Archivé</span>';
    if (_isExpired(m)) return '<span class="notif-status-badge expired">Expiré</span>';
    if (m.status === 'active') return '<span class="notif-status-badge active">Actif</span>';
    return '<span class="notif-status-badge">Programmé</span>';
}

/**
 * Badge de source : app émettrice (slug) / système / administration.
 */
function _notifSourceBadge(m) {
    if (m.sender_app_slug) {
        return `<span class="notif-source-badge app" title="${escapeHtml(m.sender_app_slug)}">${escapeHtml(m.sender_app_slug)}</span>`;
    }
    if (m.sender_type === 'system') {
        return '<span class="notif-source-badge system" title="Système">Système</span>';
    }
    const by = m.sender_username ? ` title="Par ${escapeHtml(m.sender_username)}"` : '';
    return `<span class="notif-source-badge admin"${by}>Administration</span>`;
}

/**
 * Badge de cible : broadcast global ou utilisateur spécifique.
 */
function _notifTargetBadge(m) {
    if (m.target_type === 'broadcast') {
        return '<span class="notif-target-badge broadcast"><i class="bi bi-broadcast-pin me-1"></i>Global</span>';
    }
    const who = m.target_username || (m.target_user_id ? `User #${m.target_user_id}` : '—');
    return `<span class="notif-target-badge user"><i class="bi bi-person me-1"></i>${escapeHtml(who)}</span>`;
}

/**
 * Taux de lecture read_count/total_targets + mini-barre Bootstrap .progress
 * (sans animation — cf. .notif-table .progress-bar dans style.css).
 */
function _notifReadRateHtml(m) {
    const total = m.total_targets || 0;
    const read = m.read_count || 0;
    const pct = total > 0 ? Math.round((read / total) * 100) : 0;
    const barColor = pct >= 75 ? '#059669' : (pct >= 40 ? '#6366f1' : (pct > 0 ? '#f59e0b' : '#d1d5db'));
    return `<div style="min-width:6.5rem;">
        <div class="d-flex justify-content-between small text-muted mb-1"><span>${read}/${total}</span><span>${pct}%</span></div>
        <div class="progress" style="height:5px;" role="progressbar" aria-label="Taux de lecture" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
            <div class="progress-bar" style="width:${pct}%;background:${barColor};"></div>
        </div>
    </div>`;
}

function _notifManageRowHtml(m) {
    const occ = m.occurrences || 1;
    const expanded = _notif.expandedGroups.has(m.id);
    const expander = occ > 1
        ? `<button type="button" class="btn btn-sm btn-link p-0 me-1 align-baseline" data-action="notifExpandGroup" data-arg1="${m.id}" title="${expanded ? 'Masquer' : 'Afficher'} les ${occ} messages identiques">×${occ} <i class="bi ${expanded ? 'bi-caret-up-fill' : 'bi-caret-down-fill'} small"></i></button>`
        : '';
    const received = parseUTCDate(m.created_at);
    const dimmed = (m.status === 'archived' || _isExpired(m)) ? ' text-muted' : '';

    return `<tr class="${dimmed}">
        <td><input type="checkbox" class="form-check-input" ${_notif.selection.has(m.id) ? 'checked' : ''} aria-label="Sélectionner" data-action="notifToggleSelect" data-arg1="${m.id}" data-arg2="$el"></td>
        <td>${_notifPriorityChip(m.priority)}</td>
        <td class="text-break">${expander}${escapeHtml(m.title)}</td>
        <td>${_notifSourceBadge(m)}</td>
        <td>${_notifTargetBadge(m)}</td>
        <td class="small text-muted" title="${received.toLocaleString('fr-FR')}">${_relativeTime(received)}</td>
        <td>${_notifStateChip(m)}</td>
        <td>${_notifReadRateHtml(m)}</td>
        <td class="text-end text-nowrap">
            ${m.status === 'archived'
                ? `<button type="button" class="btn btn-sm btn-outline-success" data-action="notifRestoreOne" data-arg1="${m.id}" title="Restaurer"><i class="bi bi-arrow-counterclockwise"></i></button>`
                : `<button type="button" class="btn btn-sm btn-outline-secondary" data-action="notifArchiveOne" data-arg1="${m.id}" title="Archiver"><i class="bi bi-archive"></i></button>`}
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="notifHardDeleteOne" data-arg1="${m.id}" title="Supprimer définitivement"><i class="bi bi-trash"></i></button>
        </td>
    </tr>${expanded ? _notifManageGroupSubRows(m) : ''}`;
}

/**
 * Sous-lignes d'un groupe déplié (mode Gestion) : les autres occurrences,
 * mêmes colonnes que la ligne représentante.
 */
function _notifManageGroupSubRows(m) {
    const members = (_notifGroupMembers.get(m.id) || []).filter(x => x.id !== m.id);
    if (members.length === 0) return '';
    return members.map(x => {
        const received = parseUTCDate(x.created_at);
        const dimmed = (x.status === 'archived' || _isExpired(x)) ? ' text-muted' : '';
        return `<tr class="table-light small${dimmed}">
        <td></td>
        <td>${_notifPriorityChip(x.priority)}</td>
        <td class="ps-4 text-break"><i class="bi bi-arrow-return-right text-muted me-1"></i>${escapeHtml(x.title)}</td>
        <td>${_notifSourceBadge(x)}</td>
        <td>${_notifTargetBadge(x)}</td>
        <td class="text-muted" title="${received.toLocaleString('fr-FR')}">${_relativeTime(received)}</td>
        <td>${_notifStateChip(x)}</td>
        <td>${_notifReadRateHtml(x)}</td>
        <td class="text-end text-nowrap">
            ${x.status === 'archived'
                ? `<button type="button" class="btn btn-sm btn-outline-success" data-action="notifRestoreOne" data-arg1="${x.id}" title="Restaurer"><i class="bi bi-arrow-counterclockwise"></i></button>`
                : `<button type="button" class="btn btn-sm btn-outline-secondary" data-action="notifArchiveOne" data-arg1="${x.id}" title="Archiver"><i class="bi bi-archive"></i></button>`}
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="notifHardDeleteOne" data-arg1="${x.id}" title="Supprimer définitivement"><i class="bi bi-trash"></i></button>
        </td>
    </tr>`;
    }).join('');
}

// ----- Lifecycle (une ligne) : archiver / restaurer / supprimer -----

async function notifArchiveOne(id) {
    id = parseInt(id, 10);
    if (!id) return;
    try {
        await api.archiveMessage(id);
        showToast('Archivé', 'Le message a été archivé.', 'success');
        updateNavMessagesBadge();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifRestoreOne(id) {
    id = parseInt(id, 10);
    if (!id) return;
    try {
        await api.restoreMessage(id);
        showToast('Restauré', 'Le message a été restauré.', 'success');
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function notifHardDeleteOne(id) {
    id = parseInt(id, 10);
    if (!id) return;
    const m = _notifMessages.find(x => x.id === id);
    if (!confirm(`Supprimer définitivement « ${m?.title || id} » ? Cette action est irréversible.`)) return;
    try {
        await api.hardDeleteMessage(id);
        _notif.selection.delete(id);
        showToast('Supprimé', 'Le message a été supprimé définitivement.', 'success');
        updateNavMessagesBadge();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
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
    if (_notif.mode === 'manage') return _notifManageBulkHardDelete(ids);
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

// =============================================================================
// Mode Gestion : actions en masse + purge
// =============================================================================

/**
 * Archivage en masse (sélection) — DELETE /messages/batch sans hard=true.
 */
async function notifBulkArchive() {
    const ids = [..._notif.selection];
    if (ids.length === 0) return;
    if (!confirm(`Archiver ${ids.length} message(s) sélectionné(s) ?`)) return;
    try {
        const result = await api.deleteMessagesByIds(ids, false);
        showToast('Succès', result.message || `${ids.length} message(s) archivé(s).`, 'success');
        _notif.selection.clear();
        updateNavMessagesBadge();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

/**
 * Suppression DÉFINITIVE en masse (sélection) — DELETE /messages/batch?hard=true.
 * Derrière le bouton « Supprimer » de la barre de sélection en mode Gestion.
 */
async function _notifManageBulkHardDelete(ids) {
    if (!confirm(`Suppression DÉFINITIVE de ${ids.length} message(s) ? Cette action est irréversible.`)) return;
    try {
        const result = await api.deleteMessagesByIds(ids, true);
        showToast('Succès', result.message || `${ids.length} message(s) supprimé(s).`, 'success');
        _notif.selection.clear();
        updateNavMessagesBadge();
        await loadNotifications();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

/**
 * Export CSV des messages (bouton de la barre Gestion) : reprend les filtres
 * courants supportés par l'endpoint export (priorité, app source).
 */
async function exportMessages() {
    const f = _notif.filters;
    try {
        await api.exportMessages({
            priority: f.priority || (f.urgent ? 'urgent' : null),
            senderApp: f.app,
        });
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

// ----- Modal purge (#purgeModal) -----

let _purgePreviewTimer = null;
let _purgePreviewSeq = 0; // responses périmées (l'aperçu est async + débounced)

/**
 * Option radio sélectionnée dans la modal de purge.
 */
function _purgeSelectedOption() {
    return document.querySelector('input[name="purgeOpt"]:checked')?.value || 'read';
}

/**
 * Mapping imposé option → query params (partagés par /purge-preview et
 * DELETE /messages) :
 *   lus     → read_only:true
 *   expirés → expired_only:true & exclude_urgent_unread:true
 *   vieux   → hours: jours*24 (+ older_than_days, forme native de l'aperçu)
 *   app     → sender_app:<slug>
 */
function _purgeBuildParams() {
    const opt = _purgeSelectedOption();
    const params = {};
    if (opt === 'read') {
        params.read_only = true;
    } else if (opt === 'expired') {
        params.expired_only = true;
        params.exclude_urgent_unread = true;
    } else if (opt === 'older') {
        const days = Math.min(3650, Math.max(1, parseInt(document.getElementById('purgeOlderDays')?.value, 10) || 90));
        params.hours = days * 24;
        params.older_than_days = days;
    } else if (opt === 'app') {
        const slug = document.getElementById('purgeAppSelect')?.value || '';
        if (slug) params.sender_app = slug;
    }
    return params;
}

/**
 * Libellé humain du critère (confirmation).
 */
function _purgeOptionLabel() {
    const opt = _purgeSelectedOption();
    if (opt === 'read') return 'les messages lus par tous leurs destinataires';
    if (opt === 'expired') return 'les messages expirés (urgents jamais lus épargnés)';
    if (opt === 'older') {
        const days = Math.min(3650, Math.max(1, parseInt(document.getElementById('purgeOlderDays')?.value, 10) || 90));
        return `les messages de plus de ${days} jour(s)`;
    }
    const slug = document.getElementById('purgeAppSelect')?.value || '';
    return `les messages de l'app « ${slug} »`;
}

/**
 * Ouvre la modal de purge : peuple la liste des apps sources puis lance
 * l'aperçu du critère par défaut.
 */
async function openPurgeModal() {
    const modalEl = document.getElementById('purgeModal');
    if (!modalEl) return;
    await _purgePopulateApps();
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    purgePreviewRefresh();
}

/**
 * Alimente #purgeAppSelect avec les slugs émetteurs (stats messages, comptes
 * sur la période complète).
 */
async function _purgePopulateApps() {
    const sel = document.getElementById('purgeAppSelect');
    if (!sel) return;
    try {
        const stats = await api.getMessageStats(null, undefined);
        const byApp = stats?.by_app || {};
        const slugs = Object.keys(byApp).sort();
        sel.innerHTML = slugs.length > 0
            ? slugs.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)} (${byApp[s]})</option>`).join('')
            : '<option value="">Aucune app émettrice</option>';
    } catch (e) {
        sel.innerHTML = '<option value="">Liste indisponible</option>';
    }
}

/**
 * Changement d'option radio : relance l'aperçu (le délégateur déclenche à la
 * fois input et change → debounce partagé avec purgePreviewRefresh).
 */
function purgeSelectOption() {
    purgePreviewRefresh();
}

/**
 * Aperçu du nombre de messages concernés (debounced) : un seul appel
 * /purge-preview, dont on lit le compteur correspondant à l'option —
 *   read → resp.read ; expired → resp.expired ;
 *   older → resp.older_than_days.count (l'aperçu ne connaît que les jours) ;
 *   app → resp.per_app (top 5 apps actives émettrices).
 */
function purgePreviewRefresh() {
    if (_purgePreviewTimer) clearTimeout(_purgePreviewTimer);
    _purgePreviewTimer = setTimeout(_purgePreviewDoRefresh, 350);
}

async function _purgePreviewDoRefresh() {
    _purgePreviewTimer = null;
    const label = document.getElementById('purgeCountLabel');
    const modalEl = document.getElementById('purgeModal');
    if (!label || !modalEl || !modalEl.classList.contains('show')) return; // modal fermée : pas d'appel

    const opt = _purgeSelectedOption();
    const params = _purgeBuildParams();
    const seq = ++_purgePreviewSeq;
    try {
        const preview = await api.purgePreview(params);
        if (seq !== _purgePreviewSeq) return; // une requête plus récente a répondu

        let count = 0;
        if (opt === 'read') count = preview.read || 0;
        else if (opt === 'expired') count = preview.expired || 0;
        else if (opt === 'older') count = preview.older_than_days?.count || 0;
        else if (opt === 'app') {
            const slug = document.getElementById('purgeAppSelect')?.value || '';
            count = (preview.per_app || []).find(e => e.sender_app_slug === slug)?.count || 0;
        }
        label.textContent = `Purger ${count} messages`;
    } catch (e) {
        if (seq === _purgePreviewSeq) label.textContent = 'Aperçu indisponible : ' + e.message;
    }
}

/**
 * Purge : DELETE /messages avec le mapping du critère (+ hard si la case
 * « Suppression définitive » est cochée), puis toast + rechargement.
 */
async function confirmPurge() {
    const opt = _purgeSelectedOption();
    // Garde-fou : une purge « app » sans slug sélectionné partirait sans
    // aucun filtre (avec hard=true ⇒ suppression de TOUS les messages).
    if (opt === 'app' && !(document.getElementById('purgeAppSelect')?.value)) {
        showToast('Information', 'Sélectionnez d\'abord une application source.', 'info');
        return;
    }
    const params = _purgeBuildParams();
    const hard = !!document.getElementById('purgeHardCheck')?.checked;
    if (hard) params.hard = true;

    const countLabel = document.getElementById('purgeCountLabel')?.textContent || '';
    const scope = _purgeOptionLabel();
    const action = hard
        ? 'SUPPRESSION DÉFINITIVE (irréversible)'
        : 'archivage (récupérable via le chip « Archivés »)';
    if (!confirm(`Purger ${scope} — ${countLabel} ?\nAction : ${action}.`)) return;

    try {
        const result = await api.purgeMessages(params);
        showToast(hard ? 'Supprimés' : 'Archivés', result.message || `${result.affected ?? 0} message(s).`, 'success');
        const modalEl = document.getElementById('purgeModal');
        if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        updateNavMessagesBadge();
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
 * Le refetch suit le mode : GET /me (Ma boîte) ou GET / (Gestion, vue
 * système, avec le filtre de statut courant).
 */
async function notifExpandGroup(messageId) {
    messageId = parseInt(messageId, 10);
    const m = _notifMessages.find(x => x.id === messageId);
    if (!m) return;

    if (_notif.expandedGroups.has(messageId)) {
        _notif.expandedGroups.delete(messageId);
        _notifRerenderTable();
        return;
    }

    const key = (x) => JSON.stringify([x.title, x.priority, x.sender_app_slug || x.sender_type]);
    try {
        let members;
        if (_notif.mode === 'manage') {
            const data = await api.getMessages({
                search: m.title,
                senderApp: m.sender_app_slug,
                status: _notif.filters.status || undefined,
                page: 1,
                perPage: 100,
            });
            members = (data.messages || []).filter(x => key(x) === key(m));
        } else {
            const data = await api.getMyMessages({
                search: m.title,
                senderApp: m.sender_app_slug,
                page: 1,
                pageSize: 100,
            });
            members = (data.messages || []).filter(x => key(x) === key(m));
        }
        _notifGroupMembers.set(messageId, members);
        _notif.expandedGroups.add(messageId);
        _notifRerenderTable();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

/**
 * Re-rendu du tableau selon le mode courant (le markup thead/tbody est
 * partagé entre Ma boîte et Gestion).
 */
function _notifRerenderTable() {
    if (_notif.mode === 'manage') renderNotifManageTable();
    else renderNotifTable();
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
