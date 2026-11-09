// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application — Panneau de composition des messages (#composeSlideIn)
 *
 * Extrait en Task 7 de l'ancien module inbox (supprimé) : openComposePanel,
 * closeComposePanel, toggleComposeTargetUsers, setComposeExpiresPreset,
 * toggleComposePreview, confirmSendMessage, loadTemplate et les helpers de
 * draft/templates — COMPOSE_DRAFT_KEY et toute référence #composeSlideIn ont
 * bougé ensemble. Les noms de fonctions restent identiques : le markup
 * #composeSlideIn (overlay global dans index.html) et ses data-action ne
 * changent pas.
 *
 * Les fonctions restent globales (window.*) : le délégateur js/app.js résout
 * les data-action du markup.
 */

// =============================================================================
// Compose Slide-in
// =============================================================================

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
        // Ancienne liste admin supprimée (Task 7) : recharger la console
        // notifications si elle est active (le SSE messages_updated resync
        // aussi badges et liste).
        if (typeof loadNotifications === 'function') await loadNotifications();
    } catch (error) {
        showToast('Erreur', error.message, 'danger');
    }
}
