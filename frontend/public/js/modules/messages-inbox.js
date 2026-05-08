/**
 * Hub Application - messages et inbox
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// =============================================================================
// MESSAGES — Admin + User overlay
// =============================================================================

let messagesPage = 1;
let sendMessageModal = null;
let currentMessageTab = 'all'; // 'all' | 'broadcast' | 'user'

function switchMessageTab(tab) {
    currentMessageTab = tab;
    messagesPage = 1;
    // Update active tab styling
    document.querySelectorAll('#messagesTabNav .nav-link').forEach(el => el.classList.remove('active'));
    const tabId = tab === 'all' ? 'msgTabAll' : (tab === 'broadcast' ? 'msgTabBroadcast' : 'msgTabUser');
    document.getElementById(tabId)?.classList.add('active');
    loadMessagesPage();
}

function _getActiveTargetType() {
    return currentMessageTab === 'all' ? undefined : currentMessageTab;
}

// ===== Admin : Page Messages =====

async function loadMessagesPage() {
    const priority = document.getElementById('msgFilterPriority')?.value || '';
    const senderType = document.getElementById('msgFilterSender')?.value || '';
    const senderApp = document.getElementById('msgFilterApp')?.value || '';
    const search = document.getElementById('msgFilterSearch')?.value || '';
    const hours = document.getElementById('msgFilterHours')?.value || '';
    const status = document.getElementById('msgFilterStatus')?.value || 'all';

    try {
        const targetType = _getActiveTargetType();
        const [stats, data, statsAll, statsBroadcast, statsUser] = await Promise.all([
            api.getMessageStats(hours || null, targetType),
            api.getMessages({
                page: messagesPage,
                perPage: 50,
                priority: priority || undefined,
                senderType: senderType || undefined,
                senderApp: senderApp || undefined,
                targetType: targetType,
                search: search || undefined,
                hours: hours || undefined,
                status: status === 'all' ? undefined : status,
            }),
            api.getMessageStats(hours || null, undefined),
            api.getMessageStats(hours || null, 'broadcast'),
            api.getMessageStats(hours || null, 'user'),
        ]);

        // KPI cards (basés sur statsAll pour vision globale)
        renderKpiCards(statsAll);

        // Compteurs secondaires par priorité (contextuels selon onglet target)
        document.getElementById('msgStatTotal').textContent = stats.total || 0;
        document.getElementById('msgStatInfo').textContent = stats.by_priority?.info || 0;
        document.getElementById('msgStatWarning').textContent = stats.by_priority?.warning || 0;
        document.getElementById('msgStatUrgent').textContent = stats.by_priority?.urgent || 0;

        // Tab badges
        document.getElementById('msgTabAllBadge').textContent = statsAll.total || 0;
        document.getElementById('msgTabBroadcastBadge').textContent = statsBroadcast.total || 0;
        document.getElementById('msgTabUserBadge').textContent = statsUser.total || 0;

        // Nav badge
        const navBadge = document.getElementById('navMessagesBadge');
        if (statsAll.total > 0) {
            navBadge.textContent = statsAll.total > 99 ? '99+' : statsAll.total;
            navBadge.style.display = 'inline';
        } else {
            navBadge.style.display = 'none';
        }

        renderMessagesTable(data);
    } catch (error) {
        showToast('Erreur', 'Impossible de charger les messages: ' + error.message, 'danger');
    }
}

function renderKpiCards(stats) {
    document.getElementById('kpiActiveCount').textContent = stats.total_active ?? 0;
    const rate = stats.avg_read_rate;
    document.getElementById('kpiReadRate').textContent =
        (rate !== undefined && rate !== null) ? `${Math.round(rate * 100)}%` : '—';
    document.getElementById('kpiAppsCount').textContent = stats.apps_count ?? 0;
    document.getElementById('kpiExpiredCount').textContent = stats.total_expired ?? 0;
}

function _statusBadge(m) {
    if (m.status === 'archived') {
        return '<span class="notif-status-badge archived"><i class="bi bi-archive-fill me-1"></i>Archivée</span>';
    }
    if (m.expires_at && parseUTCDate(m.expires_at) < new Date()) {
        return '<span class="notif-status-badge expired"><i class="bi bi-hourglass-bottom me-1"></i>Expirée</span>';
    }
    return '<span class="notif-status-badge active"><i class="bi bi-check-circle-fill me-1"></i>Active</span>';
}

function _sourceBadge(m) {
    if (m.sender_app_slug) {
        return `<span class="notif-source-badge app"><i class="bi bi-app-indicator me-1"></i>${escapeHtml(m.sender_app_slug)}</span>`;
    }
    if (m.sender_type === 'system') {
        return '<span class="notif-source-badge system"><i class="bi bi-gear-fill me-1"></i>Système</span>';
    }
    return `<span class="notif-source-badge admin"><i class="bi bi-person-fill me-1"></i>${escapeHtml(m.sender_username || 'Admin')}</span>`;
}

function _engagementBar(m) {
    const total = m.total_targets || 0;
    const read = m.read_count || 0;
    const pct = total > 0 ? Math.round((read / total) * 100) : 0;
    const barClass = pct >= 75 ? 'bg-success' : (pct >= 40 ? 'bg-info' : (pct > 0 ? 'bg-warning' : 'bg-secondary'));
    return `<div class="notif-engagement">
        <div class="notif-engagement-meta"><span>${read}/${total}</span><span class="text-muted">${pct}%</span></div>
        <div class="notif-engagement-bar"><div class="notif-engagement-bar-fill ${barClass}" style="width:${pct}%"></div></div>
    </div>`;
}

function _priorityIcon(p) {
    if (p === 'urgent') return '<i class="bi bi-exclamation-octagon-fill text-danger me-1" title="Urgent"></i>';
    if (p === 'warning') return '<i class="bi bi-exclamation-triangle-fill text-warning me-1" title="Warning"></i>';
    return '<i class="bi bi-info-circle-fill text-info me-1" title="Info"></i>';
}

function renderMessagesTable(data) {
    const tbody = document.getElementById('messagesTableBody');
    const selectAll = document.getElementById('msgSelectAll');
    if (selectAll) selectAll.checked = false;
    updateDeleteSelectedButton();

    if (!data.messages || data.messages.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Aucun message</td></tr>';
        document.getElementById('messagesPagination').innerHTML = '';
        return;
    }

    tbody.innerHTML = data.messages.map(m => {
        const created = parseUTCDate(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
        const expiresInfo = m.expires_at
            ? `<div class="small text-muted"><i class="bi bi-hourglass-split me-1"></i>Expire le ${parseUTCDate(m.expires_at).toLocaleDateString('fr-FR')}</div>`
            : '<div class="small text-muted"><i class="bi bi-infinity me-1"></i>Sans expiration</div>';
        const archivedInfo = m.archived_at
            ? `<div class="small text-muted"><i class="bi bi-archive me-1"></i>Archivée ${parseUTCDate(m.archived_at).toLocaleDateString('fr-FR')}</div>`
            : '';
        const bodyExtract = m.body ? `<div class="small text-muted text-truncate" style="max-width: 320px;">${escapeHtml(m.body)}</div>` : '';
        const targetLabel = m.target_type === 'broadcast'
            ? '<span class="notif-target-badge broadcast"><i class="bi bi-people-fill"></i>Tous</span>'
            : `<span class="notif-target-badge user" title="${escapeHtml(m.target_username || '')}"><i class="bi bi-person-fill"></i>@${escapeHtml(m.target_username || ('User #' + m.target_user_id))}</span>`;

        const isArchived = m.status === 'archived';
        const archiveBtn = isArchived
            ? `<button class="btn btn-outline-success btn-sm py-0 px-1" onclick="restoreMessageAction(${m.id})" title="Restaurer"><i class="bi bi-arrow-counterclockwise"></i></button>`
            : `<button class="btn btn-outline-secondary btn-sm py-0 px-1" onclick="archiveMessageAction(${m.id})" title="Archiver"><i class="bi bi-archive"></i></button>`;

        return `<tr>
            <td><input type="checkbox" class="msg-select-checkbox" value="${m.id}" onchange="updateDeleteSelectedButton()"></td>
            <td>
                <div class="d-flex align-items-start">
                    <div class="me-2 mt-1">${_priorityIcon(m.priority)}</div>
                    <div>
                        <div><strong>${escapeHtml(m.title)}</strong></div>
                        ${bodyExtract}
                        <div class="small text-muted mt-1">${targetLabel} <span class="ms-2"><i class="bi bi-clock me-1"></i>${created}</span></div>
                    </div>
                </div>
            </td>
            <td>${_statusBadge(m)}${expiresInfo}${archivedInfo}</td>
            <td>${_sourceBadge(m)}</td>
            <td>${_engagementBar(m)}</td>
            <td class="text-end">
                <div class="btn-group btn-group-sm" role="group">
                    <button class="btn btn-outline-primary btn-sm py-0 px-1" onclick="showMessageDetail(${m.id})" title="Détail"><i class="bi bi-eye"></i></button>
                    ${archiveBtn}
                    <button class="btn btn-outline-danger btn-sm py-0 px-1" onclick="hardDeleteMessageAction(${m.id})" title="Supprimer définitivement"><i class="bi bi-trash"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('');

    renderMessagesPagination(data.page, data.pages, data.total);
}

async function archiveMessageAction(id) {
    try {
        await api.archiveMessage(id);
        showToast('Archivée', 'La notification a été archivée', 'success');
        await loadMessagesPage();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function restoreMessageAction(id) {
    try {
        await api.restoreMessage(id);
        showToast('Restaurée', 'La notification a été restaurée', 'success');
        await loadMessagesPage();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

async function hardDeleteMessageAction(id) {
    if (!confirm('Supprimer définitivement cette notification ? Cette action est irréversible.')) return;
    try {
        await api.hardDeleteMessage(id);
        showToast('Supprimée', 'La notification a été supprimée définitivement', 'success');
        await loadMessagesPage();
    } catch (e) {
        showToast('Erreur', e.message, 'danger');
    }
}

function getMessagePriorityBadge(priority) {
    switch (priority) {
        case 'urgent': return '<span class="badge bg-danger msg-priority-urgent">Urgent</span>';
        case 'warning': return '<span class="badge bg-warning text-dark">Warning</span>';
        default: return '<span class="badge bg-info">Info</span>';
    }
}

function renderMessagesPagination(currentPageNum, totalPages, total) {
    const container = document.getElementById('messagesPagination');
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = `<ul class="pagination pagination-sm justify-content-center">`;
    html += `<li class="page-item ${currentPageNum <= 1 ? 'disabled' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${currentPageNum - 1})">«</a></li>`;
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPageNum - 2 && i <= currentPageNum + 2)) {
            html += `<li class="page-item ${i === currentPageNum ? 'active' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${i})">${i}</a></li>`;
        } else if (i === currentPageNum - 3 || i === currentPageNum + 3) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
    }
    html += `<li class="page-item ${currentPageNum >= totalPages ? 'disabled' : ''}"><a class="page-link" href="#" onclick="messagesGoToPage(${currentPageNum + 1})">»</a></li>`;
    html += `</ul><div class="text-center text-muted small">${total} message(s)</div>`;
    container.innerHTML = html;
}

function messagesGoToPage(page) {
    messagesPage = page;
    loadMessagesPage();
}

async function showMessageDetail(messageId) {
    try {
        const data = await api.getMessageDetail(messageId);
        const m = data.message;
        const receipts = data.read_receipts || [];

        const receiptsHtml = receipts.length > 0
            ? receipts.map(r => `<tr><td>${escapeHtml(r.username)}</td><td>${parseUTCDate(r.read_at).toLocaleString('fr-FR')}</td></tr>`).join('')
            : '<tr><td colspan="2" class="text-muted text-center">Aucun accusé de lecture</td></tr>';

        const modal = `
        <div class="modal fade" id="messageDetailModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">${getMessagePriorityBadge(m.priority)} ${escapeHtml(m.title)}</h5>
                        <button class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <small class="text-muted">Expéditeur</small><br>
                                <strong>${escapeHtml(m.sender_username || m.sender_app_slug || m.sender_type)}</strong>
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">Destinataire</small><br>
                                <strong>${m.target_type === 'broadcast' ? 'Tous (broadcast)' : escapeHtml(m.target_username || 'User #' + m.target_user_id)}</strong>
                            </div>
                        </div>
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <small class="text-muted">Date</small><br>${parseUTCDate(m.created_at).toLocaleString('fr-FR')}
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">Expiration</small><br>${m.expires_at ? parseUTCDate(m.expires_at).toLocaleString('fr-FR') : 'Aucune'}
                            </div>
                        </div>
                        ${m.body ? `<div class="alert alert-light mb-3">${escapeHtml(m.body)}</div>` : ''}
                        ${m.action_url ? `<div class="mb-3"><a href="${escapeHtml(m.action_url)}" target="_blank" class="btn btn-outline-primary btn-sm"><i class="bi bi-box-arrow-up-right me-1"></i>${escapeHtml(m.action_label || 'Ouvrir')}</a></div>` : ''}
                        <hr>
                        <h6>Accusés de lecture (${receipts.length}/${m.total_targets})</h6>
                        <table class="table table-sm"><thead><tr><th>Utilisateur</th><th>Lu le</th></tr></thead><tbody>${receiptsHtml}</tbody></table>
                    </div>
                </div>
            </div>
        </div>`;

        // Injecter et afficher
        const existing = document.getElementById('messageDetailModal');
        if (existing) existing.remove();
        document.body.insertAdjacentHTML('beforeend', modal);
        new bootstrap.Modal(document.getElementById('messageDetailModal')).show();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function showSendMessageModal() {
    // Charger la liste des utilisateurs pour le select
    try {
        const usersData = await api.request('/users/');
        const select = document.getElementById('msgTargetUsers');
        select.innerHTML = usersData.map(u =>
            `<option value="${u.id}">${escapeHtml(u.username)}</option>`
        ).join('');
    } catch (e) {
        // Pas critique, on continue
    }

    // Réinitialiser le formulaire
    document.getElementById('msgTitle').value = '';
    document.getElementById('msgBody').value = '';
    document.getElementById('msgPriority').value = 'info';
    document.getElementById('msgTargetType').value = 'broadcast';
    document.getElementById('msgExpiresAt').value = '';
    document.getElementById('msgActionUrl').value = '';
    document.getElementById('msgActionLabel').value = '';
    document.getElementById('msgTargetUsersGroup').classList.add('d-none');

    if (!sendMessageModal) {
        sendMessageModal = new bootstrap.Modal(document.getElementById('sendMessageModal'));
    }
    sendMessageModal.show();
}

function toggleMsgTargetUsers() {
    const type = document.getElementById('msgTargetType').value;
    document.getElementById('msgTargetUsersGroup').classList.toggle('d-none', type !== 'user');
}

async function confirmSendMessage() {
    const title = document.getElementById('msgTitle').value.trim();
    if (!title) {
        showToast('Erreur', 'Le titre est obligatoire', 'warning');
        return;
    }

    const targetType = document.getElementById('msgTargetType').value;
    let targetUserIds = null;
    if (targetType === 'user') {
        const select = document.getElementById('msgTargetUsers');
        targetUserIds = Array.from(select.selectedOptions).map(o => parseInt(o.value));
        if (targetUserIds.length === 0) {
            showToast('Erreur', 'Sélectionnez au moins un destinataire', 'warning');
            return;
        }
    }

    const expiresAt = document.getElementById('msgExpiresAt').value;
    const data = {
        title,
        body: document.getElementById('msgBody').value || null,
        priority: document.getElementById('msgPriority').value,
        target_type: targetType,
        target_user_ids: targetUserIds,
        action_url: document.getElementById('msgActionUrl').value || null,
        action_label: document.getElementById('msgActionLabel').value || null,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
    };

    try {
        const result = await api.sendMessage(data);
        showToast('Succès', `${result.sent} message(s) envoyé(s)`, 'success');
        if (sendMessageModal) sendMessageModal.hide();
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function purgeMessagesFiltered() {
    const hours = parseInt(document.getElementById('msgFilterHours')?.value) || null;
    const priority = document.getElementById('msgFilterPriority')?.value || null;
    const senderApp = document.getElementById('msgFilterApp')?.value || null;
    const targetType = _getActiveTargetType();
    if (!confirm('Archiver les notifications correspondant aux filtres ? Elles resteront consultables dans l’onglet « Archivées ».')) return;
    try {
        const result = await api.purgeMessages({ hours, priority, senderApp, targetType });
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function purgeAllMessages() {
    // Conservé pour rétrocompat — délègue désormais à la suppression de la sélection.
    return hardDeleteSelectedMessages();
}

async function hardDeleteSelectedMessages() {
    const selected = Array.from(document.querySelectorAll('.msg-select-checkbox:checked')).map(cb => parseInt(cb.value));
    if (selected.length === 0) {
        showToast('Information', 'Sélectionnez au moins une notification à supprimer.', 'info');
        return;
    }
    if (!confirm(`Suppression DÉFINITIVE de ${selected.length} notification(s) sélectionnée(s) ? Cette action est irréversible et supprime aussi les accusés de lecture.`)) return;
    try {
        const result = await api.deleteMessagesByIds(selected, true);
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function toggleSelectAllMessages(checkbox) {
    document.querySelectorAll('.msg-select-checkbox').forEach(cb => {
        cb.checked = checkbox.checked;
    });
    updateDeleteSelectedButton();
}

function updateDeleteSelectedButton() {
    const selected = document.querySelectorAll('.msg-select-checkbox:checked');
    const btn = document.getElementById('btnArchiveSelected');
    const count = document.getElementById('archiveSelectedCount');
    if (btn) {
        if (selected.length > 0) {
            btn.style.display = 'inline-flex';
            if (count) count.textContent = selected.length;
        } else {
            btn.style.display = 'none';
        }
    }

    // Bouton de suppression définitive (poubelle rouge)
    const delBtn = document.getElementById('btnDeleteSelected');
    const delCount = document.getElementById('deleteSelectedCount');
    if (delBtn) {
        delBtn.disabled = selected.length === 0;
        if (delCount) delCount.textContent = selected.length;
    }
}

async function archiveSelectedMessages() {
    const selected = Array.from(document.querySelectorAll('.msg-select-checkbox:checked')).map(cb => parseInt(cb.value));
    if (selected.length === 0) return;
    if (!confirm(`Archiver ${selected.length} notification(s) sélectionnée(s) ?`)) return;
    try {
        const result = await api.deleteMessagesByIds(selected, false);
        showToast('Succès', result.message, 'success');
        await loadMessagesPage();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// Conservé pour compatibilité (ancien bouton non utilisé)
async function deleteSelectedMessages() {
    return archiveSelectedMessages();
}

async function exportMessages() {
    const hours = parseInt(document.getElementById('msgFilterHours')?.value) || null;
    const priority = document.getElementById('msgFilterPriority')?.value || null;
    const senderApp = document.getElementById('msgFilterApp')?.value || null;
    const targetType = _getActiveTargetType();
    try {
        await api.exportMessages({ hours, priority, senderApp, targetType });
        showToast('Succès', 'Export téléchargé', 'success');
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

// ===== User : Notification bell + overlay =====

async function loadUnreadMessageCount() {
    try {
        const result = await api.getUnreadCount();
        updateMessageBellBadge(result.count || 0);
    } catch (e) {
        // Silencieux
    }
}

function updateMessageBellBadge(count) {
    const badge = document.getElementById('messageBellBadge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

// ===== User Inbox (3 volets : sidebar / liste / détail) =====

let _inboxMessages = [];
let _inboxFilter = { tab: 'inbox', appSlug: null };
let _inboxSelectedId = null;
let _inboxMarkReadTimer = null;
const INBOX_HIDDEN_KEY = 'hub_inbox_hidden_ids';

function _getInboxHiddenIds() {
    try {
        const raw = JSON.parse(localStorage.getItem(INBOX_HIDDEN_KEY) || '[]');
        return new Set(Array.isArray(raw) ? raw : []);
    } catch {
        return new Set();
    }
}

function _setInboxHidden(id) {
    try {
        const s = _getInboxHiddenIds();
        s.add(id);
        localStorage.setItem(INBOX_HIDDEN_KEY, JSON.stringify([...s]));
        return true;
    } catch {
        showToast('Limite atteinte', 'Impossible de mémoriser plus de notifications masquées sur ce navigateur.', 'warning');
        return false;
    }
}

function _getInboxLayout() {
    return document.querySelector('#userInboxSection .inbox-layout');
}

function _resetInboxDetailView() {
    _getInboxLayout()?.classList.remove('show-detail');
}

function _showInboxDetailView() {
    if (window.matchMedia('(max-width: 991.98px)').matches) {
        _getInboxLayout()?.classList.add('show-detail');
    }
}

function _clearInboxMarkReadTimer() {
    if (_inboxMarkReadTimer) {
        clearTimeout(_inboxMarkReadTimer);
        _inboxMarkReadTimer = null;
    }
}

async function loadInboxPage() {
    try {
        _inboxMessages = await api.getMyMessages() || [];
    } catch (e) {
        document.getElementById('inboxListBody').innerHTML =
            '<div class="text-center text-danger small py-4">Erreur de chargement</div>';
        return;
    }
    _renderInboxSidebar();
    _renderInboxList();

    const visibleMessages = _filterInboxMessages();
    const selectedMessage = visibleMessages.find(message => message.id === _inboxSelectedId);
    if (selectedMessage) {
        _renderInboxDetail(selectedMessage);
    } else {
        _inboxSelectedId = null;
        _renderInboxDetailEmpty();
        _resetInboxDetailView();
    }
}

function _renderInboxSidebar() {
    const unread = _inboxMessages.filter(m => !m.is_read && !_isExpired(m)).length;
    const badge = document.getElementById('inboxUnreadBadge');
    if (badge) {
        badge.textContent = unread > 99 ? '99+' : unread;
        badge.style.display = unread > 0 ? 'inline-block' : 'none';
    }

    const hidden = _getInboxHiddenIds();
    const apps = new Map();
    _inboxMessages.forEach(m => {
        if (hidden.has(m.id)) return;
        const slug = m.sender_app_slug || (m.sender_type === 'system' ? '__system' : '__admin');
        const label = m.sender_app_slug || (m.sender_type === 'system' ? 'Système' : 'Administration');
        const entry = apps.get(slug) || { label, count: 0, unread: 0 };
        entry.count += 1;
        if (!m.is_read) entry.unread += 1;
        apps.set(slug, entry);
    });
    const appList = document.getElementById('inboxAppList');
    if (apps.size === 0) {
        appList.innerHTML = '<div class="text-muted small px-3 py-2">Aucune source</div>';
    } else {
        appList.innerHTML = [...apps.entries()].map(([slug, info]) => {
            const active = _inboxFilter.appSlug === slug ? 'inbox-tab-active' : '';
            const badge = info.unread > 0 ? `<span class="badge bg-primary ms-auto">${info.unread}</span>` : `<span class="text-muted small ms-auto">${info.count}</span>`;
            return `<button class="inbox-tab ${active}" onclick="selectInboxApp('${slug}')">
                <i class="bi bi-app me-2"></i>${escapeHtml(info.label)} ${badge}
            </button>`;
        }).join('');
    }
}

function _isExpired(m) {
    return m.expires_at && parseUTCDate(m.expires_at) < new Date();
}

function _filterInboxMessages() {
    const hidden = _getInboxHiddenIds();
    let list = _inboxMessages.filter(m => !hidden.has(m.id));
    if (_inboxFilter.appSlug) {
        list = list.filter(m => {
            const slug = m.sender_app_slug || (m.sender_type === 'system' ? '__system' : '__admin');
            return slug === _inboxFilter.appSlug;
        });
    } else if (_inboxFilter.tab === 'inbox') {
        list = list.filter(m => !_isExpired(m));
    }
    // history: tout (sauf hidden)
    return list.sort((a, b) => parseUTCDate(b.created_at) - parseUTCDate(a.created_at));
}

function _renderInboxList() {
    const body = document.getElementById('inboxListBody');
    const title = document.getElementById('inboxListTitle');
    if (_inboxFilter.appSlug) {
        title.textContent = 'Filtre application';
    } else {
        title.textContent = _inboxFilter.tab === 'history' ? 'Historique' : 'Boîte de réception';
    }
    const list = _filterInboxMessages();
    if (list.length === 0) {
        body.innerHTML = '<div class="text-center text-muted small py-5"><i class="bi bi-inbox" style="font-size:2rem;"></i><div class="mt-2">Aucune notification</div></div>';
        return;
    }
    body.innerHTML = list.map(m => {
        const time = parseUTCDate(m.created_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        const readCls = m.is_read ? 'read' : 'unread';
        const selectedCls = _inboxSelectedId === m.id ? 'selected' : '';
        const prio = m.priority || 'info';
        const prioCls = prio === 'urgent' ? 'inbox-msg-item-priority-urgent'
                      : prio === 'warning' ? 'inbox-msg-item-priority-warning'
                      : 'inbox-msg-item-priority-info';
        const source = m.sender_app_slug || (m.sender_type === 'system' ? 'Système' : (m.sender_username || 'Admin'));
        return `<div class="inbox-msg-item ${readCls} ${selectedCls} ${prioCls}" onclick="selectInboxMessage(${m.id})">
            <div class="inbox-msg-item-top">
                <strong class="inbox-msg-item-title">${escapeHtml(m.title)}</strong>
                <span class="inbox-msg-item-time">${time}</span>
            </div>
            <div class="inbox-msg-item-source"><i class="bi bi-app-indicator me-1"></i>${escapeHtml(source)}</div>
            ${m.body ? `<div class="inbox-msg-item-extract">${escapeHtml(m.body)}</div>` : ''}
        </div>`;
    }).join('');
}

function selectInboxTab(tab) {
    _clearInboxMarkReadTimer();
    _inboxFilter.tab = tab;
    _inboxFilter.appSlug = null;
    _inboxSelectedId = null;
    document.getElementById('inboxTabInbox').classList.toggle('inbox-tab-active', tab === 'inbox');
    document.getElementById('inboxTabHistory').classList.toggle('inbox-tab-active', tab === 'history');
    _renderInboxSidebar();
    _renderInboxList();
    _renderInboxDetailEmpty();
    _resetInboxDetailView();
}

function selectInboxApp(slug) {
    _clearInboxMarkReadTimer();
    _inboxFilter.appSlug = slug;
    _inboxSelectedId = null;
    document.getElementById('inboxTabInbox').classList.remove('inbox-tab-active');
    document.getElementById('inboxTabHistory').classList.remove('inbox-tab-active');
    _renderInboxSidebar();
    _renderInboxList();
    _renderInboxDetailEmpty();
    _resetInboxDetailView();
}

function _renderInboxDetailEmpty() {
    document.getElementById('inboxDetail').innerHTML =
        `<div class="inbox-detail-empty">
            <i class="bi bi-envelope-open" style="font-size: 3rem; opacity: 0.3;"></i>
            <p class="mt-3">Sélectionnez un message pour le lire</p>
        </div>`;
}

function selectInboxMessage(id) {
    _clearInboxMarkReadTimer();
    _inboxSelectedId = id;
    const m = _inboxMessages.find(x => x.id === id);
    if (!m) return;
    _renderInboxList();
    _renderInboxDetail(m);
    _showInboxDetailView();

    if (!m.is_read) {
        _inboxMarkReadTimer = setTimeout(() => {
            if (_inboxSelectedId !== id || currentPage !== 'inbox') {
                _inboxMarkReadTimer = null;
                return;
            }

            api.markMessageRead(id).then(() => {
                m.is_read = true;
                m.read_at = new Date().toISOString();
                const badge = document.getElementById('messageBellBadge');
                const cur = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, cur - 1));
                _renderInboxSidebar();
                _renderInboxList();
            }).catch(() => {}).finally(() => {
                _inboxMarkReadTimer = null;
            });
        }, 1000);
    }
}

function _renderInboxDetail(m) {
    const created = parseUTCDate(m.created_at).toLocaleString('fr-FR');
    const expires = m.expires_at ? parseUTCDate(m.expires_at).toLocaleString('fr-FR') : null;
    const source = m.sender_app_slug || (m.sender_type === 'system' ? 'Système' : (m.sender_username || 'Admin'));
    const prio = m.priority || 'info';
    const prioBadge = prio === 'urgent' ? '<span class="badge bg-danger"><i class="bi bi-exclamation-octagon-fill me-1"></i>Urgent</span>'
                    : prio === 'warning' ? '<span class="badge bg-warning text-dark"><i class="bi bi-exclamation-triangle-fill me-1"></i>Warning</span>'
                    : '<span class="badge bg-info"><i class="bi bi-info-circle-fill me-1"></i>Info</span>';
    const action = m.action_url
        ? `<button type="button" class="btn btn-primary" data-action-url="${escapeHtml(m.action_url)}" onclick="inboxClickActionFromButton(this, ${m.id})">
              <i class="bi bi-box-arrow-up-right me-2"></i>${escapeHtml(m.action_label || 'Consulter')}
           </button>` : '';

    document.getElementById('inboxDetail').innerHTML = `
        <div class="inbox-detail-header">
            <div class="d-flex align-items-start gap-2">
                <button type="button" class="btn btn-sm btn-outline-secondary d-lg-none" onclick="returnToInboxList()" title="Retour à la liste">
                    <i class="bi bi-arrow-left"></i>
                </button>
                <div class="flex-grow-1">
                    <h4 class="mb-1">${escapeHtml(m.title)}</h4>
                    <div class="text-muted small">
                        ${prioBadge}
                        <span class="ms-2"><i class="bi bi-app-indicator me-1"></i>${escapeHtml(source)}</span>
                        <span class="ms-2"><i class="bi bi-clock me-1"></i>${created}</span>
                        ${expires ? `<span class="ms-2"><i class="bi bi-hourglass-split me-1"></i>Expire le ${expires}</span>` : ''}
                    </div>
                </div>
                <button class="btn btn-sm btn-outline-danger" onclick="userDeleteFromInbox(${m.id})" title="Retirer de ma liste">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
        <div class="inbox-detail-body">
            ${m.body ? `<div class="inbox-detail-text">${escapeHtml(m.body).replace(/\n/g, '<br>')}</div>` : '<div class="text-muted">(Aucun contenu)</div>'}
            ${action ? `<div class="mt-4">${action}</div>` : ''}
        </div>
    `;
}

function inboxClickActionFromButton(button, id) {
    const url = button?.dataset?.actionUrl;
    if (!url) {
        showToast('Lien indisponible', 'Aucune URL d’action n’est disponible pour cette notification.', 'warning');
        return;
    }
    inboxClickAction(id, url);
}

async function inboxClickAction(id, url) {
    try {
        await api.markMessageClicked(id);
    } catch {
        showToast('Information', 'Le suivi du clic a échoué, mais le lien va être ouvert.', 'warning');
    }
    window.open(url, '_blank', 'noopener');
}

function returnToInboxList() {
    _clearInboxMarkReadTimer();
    _inboxSelectedId = null;
    _renderInboxList();
    _renderInboxDetailEmpty();
    _resetInboxDetailView();
}

function userDeleteFromInbox(id) {
    if (!confirm('Retirer cette notification de votre boîte ? (Elle reste visible pour les administrateurs.)')) return;
    _clearInboxMarkReadTimer();
    if (!_setInboxHidden(id)) return;
    _inboxSelectedId = null;
    _renderInboxSidebar();
    _renderInboxList();
    _renderInboxDetailEmpty();
    _resetInboxDetailView();
}

// Compat : ancien appel toggleMessagePanel → redirige vers la page Inbox
function toggleMessagePanel() {
    showPage('inbox');
}

async function loadUserMessages() {
    return loadInboxPage();
}

async function markSingleMessageRead(messageId) {
    try {
        await api.markMessageRead(messageId);
        // Décrémenter le badge immédiatement (optimiste)
        const badge = document.getElementById('messageBellBadge');
        const current = parseInt(badge?.textContent) || 0;
        updateMessageBellBadge(Math.max(0, current - 1));
        // Rafraîchir l’inbox si visible
        const inbox = document.getElementById('userInboxSection');
        if (inbox && !inbox.classList.contains('d-none')) {
            await loadInboxPage();
        }
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

async function markAllUserMessagesRead() {
    try {
        await api.markAllMessagesRead();
        updateMessageBellBadge(0);
        const inbox = document.getElementById('userInboxSection');
        if (inbox && !inbox.classList.contains('d-none')) {
            await loadInboxPage();
        }
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}

function handleIncomingMessage(msgData) {
    // Incrémenter le badge
    const badge = document.getElementById('messageBellBadge');
    if (badge) {
        const current = parseInt(badge.textContent) || 0;
        updateMessageBellBadge(current + 1);
    }

    // Afficher une carte de notification flottante (style HUBapp_TEMPLATE)
    showNotificationCard(msgData);

    // Si l’inbox est ouverte, la rafraîchir
    const inbox = document.getElementById('userInboxSection');
    if (inbox && !inbox.classList.contains('d-none')) {
        loadInboxPage();
    }
}

// === Notification Cards (floating, top-right, HUBapp_TEMPLATE style) ===

const _activeNotificationCards = [];

function showNotificationCard(msgData) {
    const container = document.getElementById('notificationBanner');
    if (!container) return;

    const id = msgData.id || Date.now();
    const priority = msgData.priority || 'normal';

    // Icon SVG selon la priorité (style lucide)
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
                <button class="notif-card-close" onclick="dismissNotificationCard(${id}, true)" title="Fermer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            ${msgData.body ? `<p class="notif-card-body">${escapeHtml(msgData.body)}</p>` : ''}
            ${msgData.action_url ? `
                <div class="notif-card-action-wrapper">
                    <a href="${escapeHtml(msgData.action_url)}" target="_blank" rel="noopener noreferrer" class="notif-card-action" onclick="dismissNotificationCard(${id}, true)">
                        ${escapeHtml(msgData.action_label || 'Consulter')}
                        <svg class="notif-card-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    </a>
                </div>
            ` : ''}
            <div class="notif-card-meta">
                ${(msgData.sender_app_slug || msgData.sender_type) ? `
                    <span class="notif-card-sender">
                        <span class="notif-card-sender-dot"></span>
                        ${escapeHtml(msgData.sender_app_slug || msgData.sender_type)}
                    </span>
                ` : ''}
                <span class="notif-card-time">
                    <svg class="notif-card-time-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    ${time}
                </span>
            </div>
        </div>
    `;

    _activeNotificationCards.push(id);
    container.appendChild(card);
    updateNotificationBannerControls();

    // Auto-dismiss après 15s (sauf urgent) — sans marquer comme lu
    if (priority !== 'urgent') {
        setTimeout(() => dismissNotificationCard(id, false), 15000);
    }
}

function dismissNotificationCard(id, markAsRead = false) {
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

        // Marquer le message comme lu et décrémenter le badge immédiatement
        if (markAsRead && id) {
            api.markMessageRead(id).then(() => {
                const badge = document.getElementById('messageBellBadge');
                const current = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, current - 1));
                // Rafraîchir l'inbox si elle est ouverte
                const inbox = document.getElementById('userInboxSection');
                if (inbox && !inbox.classList.contains('d-none')) {
                    loadUserMessages();
                }
            }).catch(() => {});
        }
    }
}

function dismissAllNotificationCards() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    // Marquer tous les messages des cartes visibles comme lus
    const cardIds = Array.from(container.querySelectorAll('.notif-card'))
        .map(c => parseInt(c.dataset.notifId))
        .filter(id => !isNaN(id));
    container.querySelectorAll('.notif-card').forEach(card => {
        card.style.animation = 'notifCardSlideOut 0.2s ease-in forwards';
    });
    setTimeout(() => {
        container.querySelectorAll('.notif-card').forEach(c => c.remove());
        _activeNotificationCards.length = 0;
        updateNotificationBannerControls();
    }, 250);
    // Marquer comme lus et rafraîchir le badge
    if (cardIds.length > 0) {
        Promise.all(cardIds.map(id => api.markMessageRead(id).catch(() => {})))
            .then(() => {
                loadUnreadMessageCount();
                const inbox = document.getElementById('userInboxSection');
                if (inbox && !inbox.classList.contains('d-none')) {
                    loadInboxPage();
                }
            });
    }
}

function updateNotificationBannerControls() {
    const container = document.getElementById('notificationBanner');
    if (!container) return;
    let controls = container.querySelector('.notif-banner-controls');

    const cards = container.querySelectorAll('.notif-card');
    if (cards.length > 1) {
        if (!controls) {
            controls = document.createElement('div');
            controls.className = 'notif-banner-controls';
            container.prepend(controls);
        }
        controls.innerHTML = `
            <span class="notif-banner-count">${cards.length} notifications</span>
            <button class="notif-banner-dismiss-all" onclick="dismissAllNotificationCards()">
                Tout fermer
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        `;
    } else if (controls) {
        controls.remove();
    }
}
