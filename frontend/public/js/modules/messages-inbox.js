// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — messages : partie ADMIN (liste, détail, lifecycle, compose)
 *
 * La partie utilisateur (boîte de réception, cartes flottantes, badges,
 * préférences de badge) a migré vers notifications.js (console unifiée,
 * Task 6). Ce fichier n'est plus chargé par index.html depuis le Task 5 :
 * le compose part dans messages-compose.js et le reste est absorbé par le
 * mode « Gestion » de notifications.js en Task 7, puis le fichier sera
 * supprimé (git rm).
 */

// =============================================================================
// MESSAGES — Admin (le compose est extrait en messages-compose.js en Task 7)
// =============================================================================

let messagesPage = 1;
let currentMessageTab = 'all'; // 'all' | 'broadcast' | 'user'
let msgViewMode = 'cards'; // 'cards' | 'compact'
let msgActiveFilters = { status: 'all', priority: '', sender: '' };
let msgSearchTimer = null;

// Préférence GLOBALE (instance) : priorités comptées dans le badge navbar messages.
// Défaut en mémoire = toutes, jusqu'au retour du GET (repli = toutes si erreur).
let messagesBadgePriorities = ['info', 'warning', 'urgent'];

function switchMessageTab(tab) {
    currentMessageTab = tab;
    messagesPage = 1;
    document.querySelectorAll('.msg-tab-card').forEach(el => el.classList.remove('active'));
    const tabId = tab === 'all' ? 'msgTabAll' : (tab === 'broadcast' ? 'msgTabBroadcast' : 'msgTabUser');
    document.getElementById(tabId)?.classList.add('active');
    loadMessagesPage();
}

function _getActiveTargetType() {
    return currentMessageTab === 'all' ? undefined : currentMessageTab;
}

// ===== Chips de filtres =====

function toggleMsgChip(button) {
    const filter = button.dataset.filter;
    const value = button.dataset.value;
    button.parentElement.querySelectorAll('.msg-chip').forEach(b => b.classList.remove('active'));
    button.classList.add('active');
    msgActiveFilters[filter] = value;
    messagesPage = 1;
    loadMessagesPage();
}

function debounceMsgSearch() {
    if (msgSearchTimer) clearTimeout(msgSearchTimer);
    msgSearchTimer = setTimeout(() => {
        messagesPage = 1;
        loadMessagesPage();
    }, 400);
}

// ===== Admin : Page Messages =====

async function loadMessagesPage() {
    const search = document.getElementById('msgFilterSearch')?.value || '';

    try {
        const targetType = _getActiveTargetType();
        const filters = {
            page: messagesPage,
            perPage: 50,
            priority: msgActiveFilters.priority || undefined,
            senderType: msgActiveFilters.sender || undefined,
            targetType: targetType,
            search: search || undefined,
            status: msgActiveFilters.status === 'all' ? undefined : msgActiveFilters.status,
        };

        const [data, statsAll, statsBroadcast, statsUser] = await Promise.all([
            api.getMessages(filters),
            api.getMessageStats(null, undefined),
            api.getMessageStats(null, 'broadcast'),
            api.getMessageStats(null, 'user'),
        ]);

        document.getElementById('msgTabAllBadge').textContent = statsAll.total || 0;
        document.getElementById('msgTabBroadcastBadge').textContent = statsBroadcast.total || 0;
        document.getElementById('msgTabUserBadge').textContent = statsUser.total || 0;

        _applyNavMessagesBadgeCount(_badgeCountFromStats(statsAll));

        if (msgViewMode === 'cards') {
            renderMessageCards(data);
        } else {
            renderMessageCompact(data);
        }
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les messages: ' + error.message, 'danger');
    }
}

function setMsgView(mode) {
    msgViewMode = mode;
    document.getElementById('msgViewCards')?.classList.toggle('active', mode === 'cards');
    document.getElementById('msgViewCompact')?.classList.toggle('active', mode === 'compact');
    loadMessagesPage();
}

// ===== Rendu Cartes =====

function _priorityIcon(p) {
    if (p === 'urgent') return '<i class="bi bi-exclamation-octagon-fill text-danger me-1" title="Urgent"></i>';
    if (p === 'warning') return '<i class="bi bi-exclamation-triangle-fill text-warning me-1" title="Warning"></i>';
    return '<i class="bi bi-info-circle-fill text-info me-1" title="Info"></i>';
}

function renderMessageCards(data) {
    const body = document.getElementById('msgListBody');
    if (!body) return;
    updateDeleteSelectedButton();

    if (!data.messages || data.messages.length === 0) {
        body.innerHTML = `<div class="text-center text-muted py-5">
            <i class="bi bi-inbox" style="font-size:3rem;opacity:.3;"></i>
            <p class="mt-2">Aucun message</p>
        </div>`;
        document.getElementById('messagesPagination').innerHTML = '';
        return;
    }

    body.innerHTML = data.messages.map(m => {
        const created = parseUTCDate(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        const prioColor = m.priority === 'urgent' ? '#dc3545' : m.priority === 'warning' ? '#f59e0b' : '#4a90d9';
        const prioIcon = m.priority === 'urgent' ? '🔴' : m.priority === 'warning' ? '⚠' : 'ℹ';
        const prioBadge = m.priority === 'urgent'
            ? '<span class="msg-card-badge msg-card-badge-urgent">Urgent</span>'
            : m.priority === 'warning'
            ? '<span class="msg-card-badge msg-card-badge-warning">Warning</span>'
            : '<span class="msg-card-badge msg-card-badge-info">Info</span>';

        let statusBadge = '';
        if (m.status === 'archived') {
            statusBadge = '<span class="msg-card-badge msg-card-badge-archived">Archive</span>';
        } else if (m.expires_at && parseUTCDate(m.expires_at) < new Date()) {
            statusBadge = '<span class="msg-card-badge msg-card-badge-expired">Expire</span>';
        } else if (m.status === 'active') {
            statusBadge = '<span class="msg-card-badge msg-card-badge-active">Actif</span>';
        }

        const expired = m.status === 'archived' || (m.expires_at && parseUTCDate(m.expires_at) < new Date());
        const opacity = expired ? 'msg-card-expired' : '';

        const sourceLabel = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : (m.sender_username || 'Admin'));
        const sourceIcon = m.sender_app_slug ? '📦' : (m.sender_type === 'system' ? '⚙' : '👤');

        const targetLabel = m.target_type === 'broadcast'
            ? '📢 Global'
            : `👤 ${m.target_username || ('User #' + m.target_user_id)}`;

        const total = m.total_targets || 0;
        const read = m.read_count || 0;
        const pct = total > 0 ? Math.round((read / total) * 100) : 0;
        const barColor = pct >= 75 ? '#059669' : (pct >= 40 ? '#6366f1' : (pct > 0 ? '#f59e0b' : '#d1d5db'));

        const expiresInfo = m.expires_at
            ? `<span>⏰ Expire ${parseUTCDate(m.expires_at).toLocaleDateString('fr-FR')}</span>`
            : '<span>∞ Sans expiration</span>';

        return `<div class="msg-card ${opacity}" style="border-left: 4px solid ${prioColor};" data-action="openMessageDetail" data-arg1="${m.id}">
            <div class="msg-card-left" data-action="noop">
                <input type="checkbox" class="form-check-input msg-select-checkbox" value="${m.id}" data-action="updateDeleteSelectedButton">
                <span style="font-size:1.1rem;">${prioIcon}</span>
            </div>
            <div class="msg-card-body">
                <div class="msg-card-header">
                    <strong class="msg-card-title">${escapeHtml(m.title)}</strong>
                    <div class="msg-card-badges">${prioBadge}${statusBadge}</div>
                </div>
                ${m.body ? `<div class="msg-card-extract">${escapeHtml(m.body).substring(0, 120)}</div>` : ''}
                <div class="msg-card-meta">
                    <span>${sourceIcon} ${escapeHtml(sourceLabel)}</span>
                    <span>${targetLabel}</span>
                    ${expiresInfo}
                    <span class="ms-auto msg-card-engagement">
                        <span style="color:${barColor};font-weight:600;">${read}/${total} (${pct}%)</span>
                        <div class="msg-card-progress-bar"><div class="msg-card-progress-fill" style="width:${pct}%;background:${barColor};"></div></div>
                    </span>
                </div>
            </div>
        </div>`;
    }).join('');

    renderMessagesPagination(data.page, data.pages, data.total);
}

function renderMessageCompact(data) {
    const body = document.getElementById('msgListBody');
    if (!body) return;
    updateDeleteSelectedButton();

    if (!data.messages || data.messages.length === 0) {
        body.innerHTML = `<div class="text-center text-muted py-5">Aucun message</div>`;
        document.getElementById('messagesPagination').innerHTML = '';
        return;
    }

    body.innerHTML = data.messages.map(m => {
        const created = parseUTCDate(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        const prioIcon = _priorityIcon(m.priority);
        const sourceLabel = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : (m.sender_username || 'Admin'));
        const total = m.total_targets || 0;
        const read = m.read_count || 0;
        const pct = total > 0 ? Math.round((read / total) * 100) : 0;

        return `<div class="msg-compact-row" data-action="openMessageDetail" data-arg1="${m.id}">
            <input type="checkbox" class="form-check-input msg-select-checkbox" value="${m.id}" data-action="updateDeleteSelectedButton">
            <span class="msg-compact-prio">${prioIcon}</span>
            <span class="msg-compact-title">${escapeHtml(m.title)}</span>
            <span class="msg-compact-source">${escapeHtml(sourceLabel)}</span>
            <span class="msg-compact-time">${created}</span>
            <span class="msg-compact-engagement">${read}/${total} (${pct}%)</span>
        </div>`;
    }).join('');

    renderMessagesPagination(data.page, data.pages, data.total);
}

function renderMessagesPagination(currentPageNum, totalPages, total) {
    const container = document.getElementById('messagesPagination');
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = `<ul class="pagination pagination-sm justify-content-center">`;
    html += `<li class="page-item ${currentPageNum <= 1 ? 'disabled' : ''}"><a class="page-link" href="#" data-action="messagesGoToPage" data-arg1="${currentPageNum - 1}">«</a></li>`;
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPageNum - 2 && i <= currentPageNum + 2)) {
            html += `<li class="page-item ${i === currentPageNum ? 'active' : ''}"><a class="page-link" href="#" data-action="messagesGoToPage" data-arg1="${i}">${i}</a></li>`;
        } else if (i === currentPageNum - 3 || i === currentPageNum + 3) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
    }
    html += `<li class="page-item ${currentPageNum >= totalPages ? 'disabled' : ''}"><a class="page-link" href="#" data-action="messagesGoToPage" data-arg1="${currentPageNum + 1}">»</a></li>`;
    html += `</ul><div class="text-center text-muted small">${total} message(s)</div>`;
    container.innerHTML = html;
}

function messagesGoToPage(page) {
    messagesPage = page;
    loadMessagesPage();
}

// ===== Detail Panel =====

async function openMessageDetail(messageId) {
    try {
        const data = await api.getMessageDetail(messageId);
        const m = data.message;
        const receipts = data.read_receipts || [];

        document.getElementById('msgDetail').innerHTML = _renderMessageDetailHTML(m, receipts);

        if (window.matchMedia('(max-width: 1199.98px)').matches) {
            document.querySelector('.msg-admin-layout')?.classList.add('show-detail');
        }
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function closeMessageDetail() {
    document.getElementById('msgDetail').innerHTML = `
        <div class="msg-detail-empty">
            <i class="bi bi-chat-square-text" style="font-size: 3rem; opacity: .2;"></i>
            <p class="mt-3 text-muted">Selectionnez un message pour voir son detail</p>
        </div>`;
    document.querySelector('.msg-admin-layout')?.classList.remove('show-detail');
}

function _renderMessageDetailHTML(m, receipts) {
    const prioBadge = m.priority === 'urgent'
        ? '<span class="msg-card-badge msg-card-badge-urgent">🔴 Urgent</span>'
        : m.priority === 'warning'
        ? '<span class="msg-card-badge msg-card-badge-warning">⚠ Warning</span>'
        : '<span class="msg-card-badge msg-card-badge-info">ℹ Info</span>';

    const statusBadge = m.status === 'archived'
        ? '<span class="msg-card-badge msg-card-badge-archived">📁 Archive</span>'
        : (m.expires_at && parseUTCDate(m.expires_at) < new Date())
        ? '<span class="msg-card-badge msg-card-badge-expired">⏰ Expire</span>'
        : '<span class="msg-card-badge msg-card-badge-active">✅ Actif</span>';

    const sourceLabel = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : (m.sender_username || 'Admin'));
    const targetLabel = m.target_type === 'broadcast' ? 'Tous (broadcast)' : (m.target_username || `User #${m.target_user_id}`);

    const readCount = receipts.filter(r => r.read_at).length;
    const deliveredCount = receipts.filter(r => r.delivered_at && !r.read_at).length;

    const timelineHtml = receipts.length > 0
        ? receipts.map(r => {
            const statusIcon = r.read_at ? '🟢' : r.delivered_at ? '🟡' : '⚪';
            const statusText = r.read_at
                ? `Lu ${parseUTCDate(r.read_at).toLocaleString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
                : r.delivered_at ? 'Delivre' : 'Non delivre';
            const clickIcon = r.clicked_at ? ' <span style="font-size:0.7rem;background:#dcfce7;color:#166534;padding:0 4px;border-radius:4px;">clic</span>' : '';
            return `<div class="msg-timeline-row">
                <span>${statusIcon}</span>
                <strong>${escapeHtml(r.username)}</strong>
                <span class="ms-auto" style="color:#6b7280;">${statusText}${clickIcon}</span>
            </div>`;
        }).join('')
        : '<div class="text-muted small py-2">Aucun accuse de lecture</div>';

    const expiresAtStr = m.expires_at ? parseUTCDate(m.expires_at).toLocaleString('fr-FR') : 'Sans expiration';
    const createdAtStr = parseUTCDate(m.created_at).toLocaleString('fr-FR');

    return `<div class="msg-detail-content">
        <div class="msg-detail-topbar">
            <button class="btn btn-sm btn-outline-secondary d-xl-none" data-action="closeMessageDetail"><i class="bi bi-arrow-left"></i></button>
            <div class="flex-grow-1"></div>
            <button class="btn btn-sm btn-outline-secondary" data-action="closeMessageDetail"><i class="bi bi-x-lg"></i></button>
        </div>
        <div class="msg-detail-header">
            <div class="d-flex gap-2 mb-2">${prioBadge} ${statusBadge}</div>
            <h5 class="mb-1">${escapeHtml(m.title)}</h5>
            <div class="text-muted small">Envoye le ${createdAtStr} · Expire le ${expiresAtStr}</div>
        </div>
        <div class="msg-detail-meta">
            <div><i class="bi bi-person me-1"></i>Expediteur : <strong>${escapeHtml(sourceLabel)}</strong></div>
            <div><i class="bi bi-bullseye me-1"></i>Cible : <strong>${targetLabel}</strong></div>
            ${m.sender_app_slug ? `<div><i class="bi bi-app me-1"></i>App : <strong>${escapeHtml(m.sender_app_slug)}</strong></div>` : ''}
        </div>
        ${m.body ? `<div class="msg-detail-body-text">${escapeHtml(m.body).replace(/\\n/g, '<br>')}</div>` : ''}
        ${m.action_url ? `<a href="${escapeHtml(m.action_url)}" target="_blank" class="btn btn-primary btn-sm mt-2"><i class="bi bi-box-arrow-up-right me-1"></i>${escapeHtml(m.action_label || 'Consulter')}</a>` : ''}
        <div class="msg-detail-section">
            <h6>Timeline de lecture <span class="text-muted">(${readCount + deliveredCount}/${m.total_targets || 0})</span></h6>
            <div class="msg-timeline">${timelineHtml}</div>
        </div>
        <div class="msg-detail-actions">
            ${m.status === 'archived'
                ? `<button class="btn btn-outline-success btn-sm" data-action="restoreMessageAction" data-arg1="${m.id}"><i class="bi bi-arrow-counterclockwise me-1"></i>Restaurer</button>`
                : `<button class="btn btn-outline-secondary btn-sm" data-action="archiveMessageAction" data-arg1="${m.id}"><i class="bi bi-archive me-1"></i>Archiver</button>`
            }
            <button class="btn btn-outline-danger btn-sm" data-action="hardDeleteMessageAction" data-arg1="${m.id}"><i class="bi bi-trash me-1"></i>Supprimer</button>
        </div>
    </div>`;
}

// Legacy alias
function showMessageDetail(id) {
    return openMessageDetail(id);
}

// ===== Lifecycle actions (archive/restore/delete) =====

async function archiveMessageAction(id) {
    try {
        await api.archiveMessage(id);
        showToast('Archivee', 'La notification a ete archivee', 'success');
        await loadMessagesPage();
        if (currentUser?.is_admin) updateNavMessagesBadge();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function restoreMessageAction(id) {
    try {
        await api.restoreMessage(id);
        showToast('Restoree', 'La notification a ete restauree', 'success');
        await loadMessagesPage();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function hardDeleteMessageAction(id) {
    if (!confirm('Supprimer definitivement cette notification ? Cette action est irreversible.')) return;
    try {
        await api.hardDeleteMessage(id);
        showToast('Supprimee', 'La notification a ete supprimee definitivement', 'success');
        await loadMessagesPage();
        if (currentUser?.is_admin) updateNavMessagesBadge();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

function toggleSelectAllMessages(checkbox) {
    document.querySelectorAll('.msg-select-checkbox').forEach(cb => { cb.checked = checkbox.checked; });
    updateDeleteSelectedButton();
}

function updateDeleteSelectedButton() {
    const selected = document.querySelectorAll('.msg-select-checkbox:checked');
    const countSpan = document.getElementById('msgSelectedNum');
    const selectionDiv = document.getElementById('msgSelectionCount');
    const archiveBtn = document.getElementById('btnArchiveSelected');
    const deleteBtn = document.getElementById('btnDeleteSelected');

    if (selectionDiv) selectionDiv.style.display = selected.length > 0 ? '' : 'none';
    if (countSpan) countSpan.textContent = selected.length;
    if (archiveBtn) archiveBtn.style.display = selected.length > 0 ? '' : 'none';
    if (deleteBtn) deleteBtn.style.display = selected.length > 0 ? '' : 'none';
}

async function archiveSelectedMessages() {
    const selected = Array.from(document.querySelectorAll('.msg-select-checkbox:checked')).map(cb => parseInt(cb.value));
    if (selected.length === 0) return;
    if (!confirm(`Archiver ${selected.length} notification(s) selectionnee(s) ?`)) return;
    try {
        const result = await api.deleteMessagesByIds(selected, false);
        showToast('Succes', result.message, 'success');
        await loadMessagesPage();
        if (currentUser?.is_admin) updateNavMessagesBadge();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function hardDeleteSelectedMessages() {
    const selected = Array.from(document.querySelectorAll('.msg-select-checkbox:checked')).map(cb => parseInt(cb.value));
    if (selected.length === 0) { showToast('Information', 'Selectionnez au moins une notification.', 'info'); return; }
    if (!confirm(`Suppression DEFINITIVE de ${selected.length} notification(s) ? Cette action est irreversible.`)) return;
    try {
        const result = await api.deleteMessagesByIds(selected, true);
        showToast('Succes', result.message, 'success');
        await loadMessagesPage();
        if (currentUser?.is_admin) updateNavMessagesBadge();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function purgeMessagesFiltered() {
    const priority = msgActiveFilters.priority || null;
    const targetType = _getActiveTargetType();
    if (!confirm('Archiver les notifications correspondant aux filtres ?')) return;
    try {
        const result = await api.purgeMessages({ priority, targetType });
        showToast('Succes', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function exportMessages() {
    const priority = msgActiveFilters.priority || null;
    const targetType = _getActiveTargetType();
    try {
        await api.exportMessages({ priority, targetType });
        showToast('Succes', 'Export telecharge', 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// Legacy
async function purgeAllMessages() { return hardDeleteSelectedMessages(); }
async function deleteSelectedMessages() { return archiveSelectedMessages(); }

// ===== Compose Slide-in =====

let _composeDraftTimer = null;
const COMPOSE_DRAFT_KEY = 'hub_compose_draft';
let _composeIsPreview = false;

async function openComposePanel() {
    try {
        const usersData = await api.request('/users/');
        const select = document.getElementById('composeTargetUsers');
        if (select) select.innerHTML = usersData.map(u =>
            `<option value="${u.id}">${escapeHtml(u.username)}</option>`
        ).join('');
    } catch (e) { /* Pas critique */ }

    try {
        const templates = await api.getTemplates();
        const container = document.getElementById('composeTemplateList');
        if (container) container.innerHTML = templates.map(t =>
            `<div class="msg-template-item" data-action="loadTemplate" data-arg1="${t.id}">
                <span>${escapeHtml(t.name)}</span>
                <span class="text-muted small">${t.default_priority}</span>
            </div>`
        ).join('');
    } catch (e) { /* Pas critique */ }

    const draft = _getComposeDraft();
    if (draft) {
        document.getElementById('composeMsgTitle').value = draft.title || '';
        document.getElementById('composeMsgBody').value = draft.body || '';
        document.getElementById('composeMsgPriority').value = draft.priority || 'info';
        document.getElementById('composeMsgTargetType').value = draft.targetType || 'broadcast';
        document.getElementById('composeMsgExpiresAt').value = draft.expiresAt || '';
        document.getElementById('composeActionUrl').value = draft.actionUrl || '';
        document.getElementById('composeActionLabel').value = draft.actionLabel || '';
        document.getElementById('composeScheduledAt').value = draft.scheduledAt || '';
    } else {
        _resetComposeForm();
    }

    document.getElementById('composeSlideIn').classList.remove('d-none');
    toggleComposeTargetUsers();
    _startComposeDraftAutoSave();
}

function closeComposePanel() {
    document.getElementById('composeSlideIn').classList.add('d-none');
    _clearComposeDraftTimer();
    _saveComposeDraft();
}

function _resetComposeForm() {
    document.getElementById('composeMsgTitle').value = '';
    document.getElementById('composeMsgBody').value = '';
    document.getElementById('composeMsgPriority').value = 'info';
    document.getElementById('composeMsgTargetType').value = 'broadcast';
    document.getElementById('composeMsgExpiresAt').value = '';
    document.getElementById('composeActionUrl').value = '';
    document.getElementById('composeActionLabel').value = '';
    document.getElementById('composeScheduledAt').value = '';
    document.getElementById('composeTargetUsersGroup')?.classList.add('d-none');
    document.getElementById('composeTitle').textContent = 'Nouveau message';
    document.getElementById('composeSubtitle').textContent = 'Envoi immediat';
    _showComposeFormMode();
}

function toggleComposeTargetUsers() {
    const type = document.getElementById('composeMsgTargetType').value;
    document.getElementById('composeTargetUsersGroup').classList.toggle('d-none', type !== 'user');
}

function setComposeExpiresPreset() {
    const hours = parseInt(document.getElementById('composeExpiresPreset').value);
    if (!hours && hours !== 0) return;
    if (hours === 0) { document.getElementById('composeMsgExpiresAt').value = ''; return; }
    const d = new Date();
    d.setHours(d.getHours() + hours);
    document.getElementById('composeMsgExpiresAt').value = d.toISOString().slice(0, 16);
}

async function loadTemplate(templateId) {
    try {
        templateId = parseInt(templateId, 10);
        const templates = await api.getTemplates();
        const tmpl = templates.find(t => t.id === templateId);
        if (!tmpl) return;
        document.getElementById('composeMsgTitle').value = tmpl.title_template || '';
        document.getElementById('composeMsgBody').value = tmpl.body_template || '';
        document.getElementById('composeMsgPriority').value = tmpl.default_priority || 'info';
        document.getElementById('composeMsgTargetType').value = tmpl.default_target_type || 'broadcast';
        toggleComposeTargetUsers();
        if (tmpl.default_expiry_hours) {
            document.getElementById('composeExpiresPreset').value = tmpl.default_expiry_hours.toString();
            setComposeExpiresPreset();
        }
        if (tmpl.default_action_url) document.getElementById('composeActionUrl').value = tmpl.default_action_url;
        if (tmpl.default_action_label) document.getElementById('composeActionLabel').value = tmpl.default_action_label;
    } catch (e) {
        showToast('Erreur', 'Impossible de charger le template', 'warning');
    }
}

// --- Draft auto-save ---

function _getComposeDraft() {
    try { return JSON.parse(localStorage.getItem(COMPOSE_DRAFT_KEY)); }
    catch { return null; }
}

function _saveComposeDraft() {
    const draft = {
        title: document.getElementById('composeMsgTitle')?.value,
        body: document.getElementById('composeMsgBody')?.value,
        priority: document.getElementById('composeMsgPriority')?.value,
        targetType: document.getElementById('composeMsgTargetType')?.value,
        expiresAt: document.getElementById('composeMsgExpiresAt')?.value,
        actionUrl: document.getElementById('composeActionUrl')?.value,
        actionLabel: document.getElementById('composeActionLabel')?.value,
        scheduledAt: document.getElementById('composeScheduledAt')?.value,
    };
    localStorage.setItem(COMPOSE_DRAFT_KEY, JSON.stringify(draft));
}

function _startComposeDraftAutoSave() {
    _clearComposeDraftTimer();
    _composeDraftTimer = setInterval(_saveComposeDraft, 3000);
}

function _clearComposeDraftTimer() {
    if (_composeDraftTimer) { clearInterval(_composeDraftTimer); _composeDraftTimer = null; }
}

// --- Preview ---

function toggleComposePreview() {
    _composeIsPreview = !_composeIsPreview;
    if (_composeIsPreview) { _showPreview(); }
    else { _showComposeFormMode(); }
}

function _showComposeFormMode() {
    document.getElementById('composeFormMode')?.classList.remove('d-none');
    document.getElementById('composePreviewMode')?.classList.add('d-none');
    document.getElementById('composePreviewBtn').innerHTML = '<i class="bi bi-eye me-1"></i>Preview';
    _composeIsPreview = false;
}

function _showPreview() {
    document.getElementById('composeFormMode')?.classList.add('d-none');
    document.getElementById('composePreviewMode')?.classList.remove('d-none');
    document.getElementById('composePreviewBtn').innerHTML = '<i class="bi bi-pencil me-1"></i>Modifier';

    const title = document.getElementById('composeMsgTitle').value || '(Sans titre)';
    const body = document.getElementById('composeMsgBody').value || '';
    const priority = document.getElementById('composeMsgPriority').value;
    const targetType = document.getElementById('composeMsgTargetType').value;
    const borderColor = priority === 'urgent' ? '#dc3545' : priority === 'warning' ? '#f59e0b' : '#4a90d9';
    const prioLabel = priority === 'urgent' ? 'Urgent' : priority === 'warning' ? 'Warning' : 'Info';
    const targetLabel = targetType === 'broadcast' ? 'Tous les utilisateurs' : 'Utilisateurs specifiques';

    document.getElementById('composePreviewCard').innerHTML = `
        <div style="border-left:4px solid ${borderColor};padding:14px;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.06);max-width:380px;">
            <div style="font-weight:700;font-size:0.9rem;margin-bottom:6px;">${escapeHtml(title)}</div>
            ${body ? `<div style="font-size:0.82rem;color:#6b7280;margin-bottom:8px;">${escapeHtml(body)}</div>` : ''}
            <span style="font-size:0.7rem;padding:2px 8px;border-radius:10px;background:#dbeafe;color:#1e40af;">${prioLabel}</span>
        </div>`;

    document.getElementById('composePreviewSummary').innerHTML = `
        <div style="font-size:0.75rem;color:#6b7280;margin-top:12px;">
            <div>Priorite: <strong>${prioLabel}</strong></div>
            <div>Cible: <strong>${targetLabel}</strong></div>
            ${document.getElementById('composeScheduledAt').value ? `<div style="color:#d97706;">Envoi differe: ${document.getElementById('composeScheduledAt').value}</div>` : ''}
        </div>`;
}

// --- Send ---

async function confirmSendMessage() {
    const title = document.getElementById('composeMsgTitle').value.trim();
    if (!title) { showToast('Erreur', 'Le titre est obligatoire', 'warning'); return; }

    const targetType = document.getElementById('composeMsgTargetType').value;
    let targetUserIds = null;
    if (targetType === 'user') {
        const select = document.getElementById('composeTargetUsers');
        targetUserIds = Array.from(select.selectedOptions).map(o => parseInt(o.value));
        if (targetUserIds.length === 0) { showToast('Erreur', 'Selectionnez au moins un destinataire', 'warning'); return; }
    }

    const scheduledAtVal = document.getElementById('composeScheduledAt').value;
    const data = {
        title,
        body: document.getElementById('composeMsgBody').value || null,
        priority: document.getElementById('composeMsgPriority').value,
        target_type: targetType,
        target_user_ids: targetUserIds,
        action_url: document.getElementById('composeActionUrl').value || null,
        action_label: document.getElementById('composeActionLabel').value || null,
        expires_at: document.getElementById('composeMsgExpiresAt').value
            ? new Date(document.getElementById('composeMsgExpiresAt').value).toISOString() : null,
        scheduled_at: scheduledAtVal ? new Date(scheduledAtVal).toISOString() : null,
    };

    try {
        const result = await api.sendMessage(data);
        if (result.scheduled) {
            showToast('Programme', `Message programme pour ${scheduledAtVal}`, 'success');
            document.getElementById('composeSubtitle').textContent = 'Envoi programme';
        } else {
            showToast('Succes', `${result.sent} message(s) envoye(s)`, 'success');
        }
        localStorage.removeItem(COMPOSE_DRAFT_KEY);
        _clearComposeDraftTimer();
        closeComposePanel();
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// Legacy alias
function showSendMessageModal() { return openComposePanel(); }
function toggleMsgTargetUsers() { return toggleComposeTargetUsers(); }
