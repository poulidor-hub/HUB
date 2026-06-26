/**
 * Hub Application - messages et inbox (refonte)
 *
 * Extrait mecaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilite avec les handlers HTML inline.
 */

// =============================================================================
// MESSAGES — Admin + User overlay
// =============================================================================

let messagesPage = 1;
let currentMessageTab = 'all'; // 'all' | 'broadcast' | 'user'
let msgViewMode = 'cards'; // 'cards' | 'compact'
let msgActiveFilters = { status: 'all', priority: '', sender: '' };
let msgSearchTimer = null;

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

// ===== Vues sauvegardees =====

const SAVED_VIEWS_KEY = 'hub_msg_saved_views';

function _getSavedViews() {
    try { return JSON.parse(localStorage.getItem(SAVED_VIEWS_KEY) || '[]'); }
    catch { return []; }
}

function saveCurrentMsgView() {
    const name = prompt('Nom de cette vue :');
    if (!name) return;
    const views = _getSavedViews();
    views.push({ name, filters: { ...msgActiveFilters } });
    localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(views));
    renderSavedViews();
}

function loadSavedView(index) {
    const views = _getSavedViews();
    if (index >= views.length) return;
    const saved = views[index].filters;
    Object.entries(saved).forEach(([filter, value]) => {
        msgActiveFilters[filter] = value;
        const rowId = filter === 'status' ? 'msgChipStatus' : filter === 'priority' ? 'msgChipPriority' : 'msgChipSender';
        const chipRow = document.getElementById(rowId);
        if (chipRow) {
            chipRow.querySelectorAll('.msg-chip').forEach(b => b.classList.remove('active'));
            const match = chipRow.querySelector(`[data-value="${value}"]`);
            if (match) match.classList.add('active');
        }
    });
    messagesPage = 1;
    loadMessagesPage();
}

function renderSavedViews() {
    const container = document.getElementById('msgSavedViews');
    if (!container) return;
    const views = _getSavedViews();
    container.innerHTML = views.map((v, i) =>
        `<div class="msg-saved-view" onclick="loadSavedView(${i})">
            <i class="bi bi-bookmark me-1"></i>${escapeHtml(v.name)}
        </div>`
    ).join('');
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

        const navBadge = document.getElementById('navMessagesBadge');
        if (statsAll.total > 0) {
            if (navBadge) {
                navBadge.textContent = statsAll.total > 99 ? '99+' : statsAll.total;
                navBadge.style.display = 'inline';
            }
        } else if (navBadge) {
            navBadge.style.display = 'none';
        }

        if (msgViewMode === 'cards') {
            renderMessageCards(data);
        } else {
            renderMessageCompact(data);
        }

        renderSavedViews();
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

        return `<div class="msg-card ${opacity}" style="border-left: 4px solid ${prioColor};" onclick="openMessageDetail(${m.id})">
            <div class="msg-card-left" onclick="event.stopPropagation()">
                <input type="checkbox" class="form-check-input msg-select-checkbox" value="${m.id}" onchange="updateDeleteSelectedButton()">
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

        return `<div class="msg-compact-row" onclick="openMessageDetail(${m.id})">
            <input type="checkbox" class="form-check-input msg-select-checkbox" value="${m.id}" onclick="event.stopPropagation()" onchange="updateDeleteSelectedButton()">
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
            <button class="btn btn-sm btn-outline-secondary d-xl-none" onclick="closeMessageDetail()"><i class="bi bi-arrow-left"></i></button>
            <div class="flex-grow-1"></div>
            <button class="btn btn-sm btn-outline-secondary" onclick="closeMessageDetail()"><i class="bi bi-x-lg"></i></button>
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
                ? `<button class="btn btn-outline-success btn-sm" onclick="restoreMessageAction(${m.id})"><i class="bi bi-arrow-counterclockwise me-1"></i>Restaurer</button>`
                : `<button class="btn btn-outline-secondary btn-sm" onclick="archiveMessageAction(${m.id})"><i class="bi bi-archive me-1"></i>Archiver</button>`
            }
            <button class="btn btn-outline-danger btn-sm" onclick="hardDeleteMessageAction(${m.id})"><i class="bi bi-trash me-1"></i>Supprimer</button>
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
            `<div class="msg-template-item" onclick="loadTemplate(${t.id})">
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

// ===== User : Notification bell =====

async function loadUnreadMessageCount() {
    try {
        const result = await api.getUnreadCount();
        updateMessageBellBadge(result.count || 0);
    } catch (e) { /* Silencieux */ }
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

async function updateNavMessagesBadge() {
    const navBadge = document.getElementById('navMessagesBadge');
    if (!navBadge) return;
    try {
        const stats = await api.getMessageStats(null, undefined);
        const total = stats.total || 0;
        if (total > 0) { navBadge.textContent = total > 99 ? '99+' : total; navBadge.style.display = 'inline'; }
        else { navBadge.style.display = 'none'; }
    } catch (e) { /* Silencieux */ }
}

// ===== User Inbox (3 volets : sidebar / liste / detail) =====

let _inboxMessages = [];
let _inboxFilter = { tab: 'inbox', appSlug: null };
let _inboxSelectedId = null;
let _inboxMarkReadTimer = null;
const INBOX_HIDDEN_KEY = 'hub_inbox_hidden_ids';

function _getInboxHiddenIds() {
    try { const raw = JSON.parse(localStorage.getItem(INBOX_HIDDEN_KEY) || '[]'); return new Set(Array.isArray(raw) ? raw : []); }
    catch { return new Set(); }
}

function _setInboxHidden(id) {
    try {
        const s = _getInboxHiddenIds(); s.add(id);
        localStorage.setItem(INBOX_HIDDEN_KEY, JSON.stringify([...s])); return true;
    } catch {
        showToast('Limite atteinte', 'Impossible de memoriser plus de notifications masquees.', 'warning'); return false;
    }
}

function _getInboxLayout() { return document.querySelector('#userInboxSection .inbox-layout'); }
function _resetInboxDetailView() { _getInboxLayout()?.classList.remove('show-detail'); }
function _showInboxDetailView() {
    if (window.matchMedia('(max-width: 991.98px)').matches) { _getInboxLayout()?.classList.add('show-detail'); }
}
function _clearInboxMarkReadTimer() { if (_inboxMarkReadTimer) { clearTimeout(_inboxMarkReadTimer); _inboxMarkReadTimer = null; } }
function _isExpired(m) { return m.expires_at && parseUTCDate(m.expires_at) < new Date(); }

function _relativeTime(date) {
    const diffMs = new Date() - date; const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "a l'instant"; if (mins < 60) return `Il y a ${mins} min`;
    const hours = Math.floor(mins / 60); if (hours < 24) return `Il y a ${hours}h`;
    const days = Math.floor(hours / 24); if (days < 7) return `Il y a ${days}j`;
    return date.toLocaleDateString('fr-FR');
}

async function loadInboxPage() {
    try { _inboxMessages = await api.getMyMessages() || []; }
    catch (e) { document.getElementById('inboxListBody').innerHTML = '<div class="text-center text-danger small py-4">Erreur de chargement</div>'; return; }
    _renderInboxSidebar(); _renderInboxList();
    const visibleMessages = _filterInboxMessages();
    const selectedMessage = visibleMessages.find(message => message.id === _inboxSelectedId);
    if (selectedMessage) { _renderInboxDetail(selectedMessage); }
    else { _inboxSelectedId = null; _renderInboxDetailEmpty(); _resetInboxDetailView(); }
}

function _renderInboxSidebar() {
    const unread = _inboxMessages.filter(m => !m.is_read && !_isExpired(m)).length;
    const badge = document.getElementById('inboxUnreadBadge');
    if (badge) { badge.textContent = unread > 99 ? '99+' : unread; badge.style.display = unread > 0 ? 'inline-block' : 'none'; }

    const hidden = _getInboxHiddenIds();
    const apps = new Map();
    const appColors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

    _inboxMessages.forEach(m => {
        if (hidden.has(m.id)) return;
        const slug = m.sender_app_slug || (m.sender_type === 'system' ? '__system' : '__admin');
        const label = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : 'Administration');
        const entry = apps.get(slug) || { label, count: 0, unread: 0 };
        entry.count += 1; if (!m.is_read) entry.unread += 1;
        apps.set(slug, entry);
    });

    const appList = document.getElementById('inboxAppList');
    if (apps.size === 0) { appList.innerHTML = '<div class="text-muted small px-3 py-2">Aucune source</div>'; }
    else {
        let colorIdx = 0;
        appList.innerHTML = [...apps.entries()].map(([slug, info]) => {
            const color = appColors[colorIdx++ % appColors.length];
            const active = _inboxFilter.appSlug === slug ? 'inbox-tab-active' : '';
            const unreadBadge = info.unread > 0
                ? `<span class="inbox-app-unread" style="background:${color}">${info.unread}</span>`
                : `<span class="text-muted" style="font-size:0.72rem;">${info.count}</span>`;
            return `<button class="inbox-tab ${active}" onclick="selectInboxApp('${slug}')">
                <span class="inbox-app-dot" style="background:${color};"></span>
                <span class="flex-grow-1">${escapeHtml(info.label)}</span>
                ${unreadBadge}
            </button>`;
        }).join('');
    }
}

function _filterInboxMessages() {
    const hidden = _getInboxHiddenIds();
    let list = _inboxMessages.filter(m => !hidden.has(m.id));
    if (_inboxFilter.appSlug) {
        list = list.filter(m => { const slug = m.sender_app_slug || (m.sender_type === 'system' ? '__system' : '__admin'); return slug === _inboxFilter.appSlug; });
    } else if (_inboxFilter.tab === 'inbox') { list = list.filter(m => !_isExpired(m)); }
    return list.sort((a, b) => parseUTCDate(b.created_at) - parseUTCDate(a.created_at));
}

function _renderInboxList() {
    const body = document.getElementById('inboxListBody');
    const title = document.getElementById('inboxListTitle');
    if (_inboxFilter.appSlug) { title.textContent = 'Filtre application'; }
    else { title.textContent = _inboxFilter.tab === 'history' ? 'Historique' : 'Boite de reception'; }
    const list = _filterInboxMessages();
    if (list.length === 0) {
        body.innerHTML = '<div class="text-center text-muted small py-5"><i class="bi bi-inbox" style="font-size:2rem;"></i><div class="mt-2">Aucune notification</div></div>';
        return;
    }
    body.innerHTML = list.map(m => {
        const time = _relativeTime(parseUTCDate(m.created_at));
        const readCls = m.is_read ? 'read' : 'unread';
        const selectedCls = _inboxSelectedId === m.id ? 'selected' : '';
        const prio = m.priority || 'info';
        const prioCls = prio === 'urgent' ? 'inbox-msg-item-priority-urgent' : prio === 'warning' ? 'inbox-msg-item-priority-warning' : 'inbox-msg-item-priority-info';
        const source = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : (m.sender_username || 'Admin'));
        const expiresSoon = m.expires_at && !m.is_read && !_isExpired(m) && (parseUTCDate(m.expires_at) - new Date()) < 24 * 3600 * 1000
            ? '<span class="inbox-expiring-badge">Expire bientot</span>' : '';
        const isReadBadge = m.is_read ? '<span class="inbox-read-badge">✓ Lu</span>' : '';

        return `<div class="inbox-msg-item ${readCls} ${selectedCls} ${prioCls}" onclick="selectInboxMessage(${m.id})">
            <div class="inbox-msg-item-dot"></div>
            <div class="inbox-msg-item-content">
                <div class="inbox-msg-item-top">
                    <strong class="inbox-msg-item-title">${escapeHtml(m.title)}</strong>
                    ${expiresSoon}${isReadBadge}
                    <span class="inbox-msg-item-time">${time}</span>
                </div>
                <div class="inbox-msg-item-source">${escapeHtml(source)}</div>
                ${m.body ? `<div class="inbox-msg-item-extract">${escapeHtml(m.body)}</div>` : ''}
            </div>
        </div>`;
    }).join('');
}

function selectInboxTab(tab) {
    _clearInboxMarkReadTimer(); _inboxFilter.tab = tab; _inboxFilter.appSlug = null; _inboxSelectedId = null;
    document.getElementById('inboxTabInbox').classList.toggle('inbox-tab-active', tab === 'inbox');
    document.getElementById('inboxTabHistory').classList.toggle('inbox-tab-active', tab === 'history');
    _renderInboxSidebar(); _renderInboxList(); _renderInboxDetailEmpty(); _resetInboxDetailView();
}

function selectInboxApp(slug) {
    _clearInboxMarkReadTimer(); _inboxFilter.appSlug = slug; _inboxSelectedId = null;
    document.getElementById('inboxTabInbox').classList.remove('inbox-tab-active');
    document.getElementById('inboxTabHistory').classList.remove('inbox-tab-active');
    _renderInboxSidebar(); _renderInboxList(); _renderInboxDetailEmpty(); _resetInboxDetailView();
}

function _renderInboxDetailEmpty() {
    document.getElementById('inboxDetail').innerHTML = `<div class="inbox-detail-empty"><i class="bi bi-envelope-open" style="font-size: 3rem; opacity: 0.3;"></i><p class="mt-3">Selectionnez un message pour le lire</p></div>`;
}

function selectInboxMessage(id) {
    _clearInboxMarkReadTimer(); _inboxSelectedId = id;
    const m = _inboxMessages.find(x => x.id === id); if (!m) return;
    _renderInboxList(); _renderInboxDetail(m); _showInboxDetailView();

    if (!m.is_read) {
        _inboxMarkReadTimer = setTimeout(() => {
            if (_inboxSelectedId !== id || currentPage !== 'inbox') { _inboxMarkReadTimer = null; return; }
            api.markMessageRead(id).then(() => {
                m.is_read = true; m.read_at = new Date().toISOString();
                const badge = document.getElementById('messageBellBadge');
                const cur = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, cur - 1));
                _renderInboxSidebar(); _renderInboxList();
            }).catch(() => {}).finally(() => { _inboxMarkReadTimer = null; });
        }, 1000);
    }
}

function _renderInboxDetail(m) {
    const created = parseUTCDate(m.created_at).toLocaleString('fr-FR');
    const expires = m.expires_at ? parseUTCDate(m.expires_at).toLocaleString('fr-FR') : null;
    const source = m.sender_app_slug || (m.sender_type === 'system' ? 'Systeme' : (m.sender_username || 'Admin'));
    const sourceIcon = m.sender_app_slug ? '📦' : (m.sender_type === 'system' ? '⚙' : '👤');
    const prio = m.priority || 'info';
    const prioBadge = prio === 'urgent' ? '<span class="inbox-detail-badge inbox-detail-badge-urgent">🔴 Urgent</span>'
                    : prio === 'warning' ? '<span class="inbox-detail-badge inbox-detail-badge-warning">⚠ Warning</span>'
                    : '<span class="inbox-detail-badge inbox-detail-badge-info">ℹ Info</span>';
    const expiresSoon = m.expires_at && !_isExpired(m) && (parseUTCDate(m.expires_at) - new Date()) < 24 * 3600 * 1000
        ? '<span class="inbox-expiring-badge">Expire bientot</span>' : '';
    const action = m.action_url
        ? `<a href="${escapeHtml(m.action_url)}" target="_blank" rel="noopener" class="btn btn-primary btn-sm" onclick="inboxClickAction(${m.id}, '${escapeHtml(m.action_url)}')"><i class="bi bi-box-arrow-up-right me-1"></i>${escapeHtml(m.action_label || 'Consulter')}</a>` : '';

    document.getElementById('inboxDetail').innerHTML = `
        <div class="inbox-detail-header">
            <div class="d-flex align-items-start gap-2">
                <button type="button" class="btn btn-sm btn-outline-secondary d-lg-none" onclick="returnToInboxList()"><i class="bi bi-arrow-left"></i></button>
                <div class="flex-grow-1">
                    <div class="d-flex gap-2 align-items-center mb-1">${prioBadge}${expiresSoon}</div>
                    <h4 class="mb-1">${escapeHtml(m.title)}</h4>
                    <div class="text-muted small mt-2">
                        <div>${sourceIcon} <strong>${escapeHtml(source)}</strong></div>
                        <div class="mt-1">📅 Recu le ${created}</div>
                        ${expires ? `<div>⏰ Expire le ${expires}</div>` : ''}
                    </div>
                </div>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-secondary" onclick="markSingleMessageRead(${m.id})" title="Marquer comme lu" ${m.is_read ? 'disabled' : ''}><i class="bi bi-check2"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="userDeleteFromInbox(${m.id})" title="Retirer"><i class="bi bi-trash"></i></button>
                </div>
            </div>
        </div>
        <div class="inbox-detail-body">
            ${m.body ? `<div class="inbox-detail-text">${escapeHtml(m.body).replace(/\\n/g, '<br>')}</div>` : '<div class="text-muted">(Aucun contenu)</div>'}
            ${action ? `<div class="mt-3">${action}</div>` : ''}
        </div>`;
}

function inboxClickActionFromButton(button, id) {
    const url = button?.dataset?.actionUrl;
    if (!url) { showToast('Lien indisponible', '', 'warning'); return; }
    inboxClickAction(id, url);
}

async function inboxClickAction(id, url) {
    try { await api.markMessageClicked(id); } catch { showToast('Information', 'Suivi du clic echoue.', 'warning'); }
    window.open(url, '_blank', 'noopener');
}

function returnToInboxList() {
    _clearInboxMarkReadTimer(); _inboxSelectedId = null; _renderInboxList(); _renderInboxDetailEmpty(); _resetInboxDetailView();
}

function userDeleteFromInbox(id) {
    if (!confirm('Retirer cette notification de votre boite ?')) return;
    _clearInboxMarkReadTimer(); if (!_setInboxHidden(id)) return;
    _inboxSelectedId = null; _renderInboxSidebar(); _renderInboxList(); _renderInboxDetailEmpty(); _resetInboxDetailView();
}

function toggleMessagePanel() { showPage('inbox'); }
async function loadUserMessages() { return loadInboxPage(); }

async function markSingleMessageRead(messageId) {
    try {
        await api.markMessageRead(messageId);
        const badge = document.getElementById('messageBellBadge');
        const current = parseInt(badge?.textContent) || 0;
        updateMessageBellBadge(Math.max(0, current - 1));
        const inbox = document.getElementById('userInboxSection');
        if (inbox && !inbox.classList.contains('d-none')) { await loadInboxPage(); }
    } catch (error) { showToast('Erreur', error.message, 'danger'); }
}

async function markAllUserMessagesRead() {
    try {
        await api.markAllMessagesRead(); updateMessageBellBadge(0);
        const inbox = document.getElementById('userInboxSection');
        if (inbox && !inbox.classList.contains('d-none')) { await loadInboxPage(); }
    } catch (error) { showToast('Erreur', error.message, 'danger'); }
}

function handleIncomingMessage(msgData) {
    const badge = document.getElementById('messageBellBadge');
    if (badge) { const current = parseInt(badge.textContent) || 0; updateMessageBellBadge(current + 1); }
    showNotificationCard(msgData);
    const inbox = document.getElementById('userInboxSection');
    if (inbox && !inbox.classList.contains('d-none')) { loadInboxPage(); }
}

// === Notification Cards (floating, top-right) ===

const _activeNotificationCards = [];

function showNotificationCard(msgData) {
    const container = document.getElementById('notificationBanner');
    if (!container) return;

    const id = msgData.id || Date.now();
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
                <button class="notif-card-close" onclick="dismissNotificationCard(${id}, true)" title="Fermer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            ${msgData.body ? `<p class="notif-card-body">${escapeHtml(msgData.body)}</p>` : ''}
            ${msgData.action_url ? `<div class="notif-card-action-wrapper"><a href="${escapeHtml(msgData.action_url)}" target="_blank" rel="noopener noreferrer" class="notif-card-action" onclick="dismissNotificationCard(${id}, true)">${escapeHtml(msgData.action_label || 'Consulter')}<svg class="notif-card-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a></div>` : ''}
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

        if (markAsRead && id) {
            api.markMessageRead(id).then(() => {
                const badge = document.getElementById('messageBellBadge');
                const current = parseInt(badge?.textContent) || 0;
                updateMessageBellBadge(Math.max(0, current - 1));
                const inbox = document.getElementById('userInboxSection');
                if (inbox && !inbox.classList.contains('d-none')) { loadUserMessages(); }
            }).catch(() => {});
        }
    }
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
            const inbox = document.getElementById('userInboxSection');
            if (inbox && !inbox.classList.contains('d-none')) { loadInboxPage(); }
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
        controls.innerHTML = `<span class="notif-banner-count">${cards.length} notifications</span><button class="notif-banner-dismiss-all" onclick="dismissAllNotificationCards()">Tout fermer<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
    } else if (controls) { controls.remove(); }
}
