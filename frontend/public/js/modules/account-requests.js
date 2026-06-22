/**
 * Hub Application - demandes de compte
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// =============================================================================
// ===== ACCOUNT REQUESTS (Demandes de compte) =====
// =============================================================================

let _accountRequestPollingTimer = null;
let approveRequestModal = null;
let rejectRequestModal = null;
let accountRequestModal = null;
let accountRequestNotificationAdminsModal = null;
let accountRequestFormConfigModal = null;
let _approveRequestContext = { firstName: '', lastName: '' };

/**
 * Ouvre la modale de demande de compte depuis la page de login.
 */
function showAccountRequestModal() {
    if (!accountRequestModal) {
        accountRequestModal = new bootstrap.Modal(document.getElementById('accountRequestModal'));
    }
    document.getElementById('accountRequestForm').reset();
    document.getElementById('accountRequestError').classList.add('d-none');
    applyAccountRequestFormConfig().finally(() => accountRequestModal.show());
}

/**
 * Récupère la configuration du formulaire (message + champs visibles) et
 * l'applique à la modale de demande de compte.
 */
async function applyAccountRequestFormConfig() {
    let config = { important_message: '', show_first_name: true, show_last_name: true, show_email: false };
    try {
        config = await api.getAccountRequestFormConfig();
    } catch (e) {
        console.warn('Configuration du formulaire indisponible, valeurs par défaut:', e);
    }

    const box = document.getElementById('accountRequestImportantBox');
    const msgSpan = document.getElementById('accountRequestImportantMessage');
    const message = (config.important_message || '').trim();
    if (message) {
        msgSpan.textContent = message;
        box.classList.remove('d-none');
    } else {
        msgSpan.textContent = '';
        box.classList.add('d-none');
    }

    toggleRequestField('accountRequestLastNameGroup', 'accountRequestLastName', config.show_last_name !== false, true);
    toggleRequestField('accountRequestFirstNameGroup', 'accountRequestFirstName', config.show_first_name !== false, true);
    toggleRequestField('accountRequestEmailGroup', 'accountRequestEmail', config.show_email === true, false);
}

/**
 * Affiche/masque un champ du formulaire et ajuste l'attribut required.
 * Un champ masqué requis bloquerait la soumission : on retire donc required.
 */
function toggleRequestField(groupId, inputId, visible, requiredWhenVisible) {
    const group = document.getElementById(groupId);
    const input = document.getElementById(inputId);
    if (!group || !input) return;
    if (visible) {
        group.classList.remove('d-none');
        input.required = !!requiredWhenVisible;
    } else {
        group.classList.add('d-none');
        input.required = false;
        input.value = '';
    }
}

/**
 * Soumet la demande de compte (endpoint public, sans token).
 */
async function handleAccountRequest(event) {
    event.preventDefault();
    const errorDiv = document.getElementById('accountRequestError');
    errorDiv.classList.add('d-none');

    const firstName = document.getElementById('accountRequestFirstName').value.trim();
    const lastName = document.getElementById('accountRequestLastName').value.trim();
    const email = document.getElementById('accountRequestEmail').value.trim();
    const username = document.getElementById('accountRequestUsername').value.trim();
    const password = document.getElementById('accountRequestPassword').value;
    const confirmPassword = document.getElementById('accountRequestPasswordConfirm').value;

    if (password !== confirmPassword) {
        errorDiv.textContent = 'Les mots de passe ne correspondent pas';
        errorDiv.classList.remove('d-none');
        return;
    }
    if (password.length < 8) {
        errorDiv.textContent = 'Le mot de passe doit contenir au moins 8 caractères';
        errorDiv.classList.remove('d-none');
        return;
    }

    // N'envoyer que les champs renseignés (les champs masqués/optionnels
    // peuvent être renseignés par l'admin lors de l'approbation).
    const payload = { username, password };
    if (firstName) payload.first_name = firstName;
    if (lastName) payload.last_name = lastName;
    if (email) payload.email = email;

    try {
        const response = await fetch(`${API_BASE}/account-requests/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();

        if (!response.ok) {
            errorDiv.textContent = data.detail || 'Erreur lors de la soumission';
            errorDiv.classList.remove('d-none');
            return;
        }

        // Stocker les infos pour le polling (sans mot de passe)
        sessionStorage.setItem('accountRequest', JSON.stringify({
            requestId: data.request_id,
            pollingToken: data.polling_token
        }));

        accountRequestModal.hide();
        showAccountRequestWaiting();
        startAccountRequestPolling(data.request_id, data.polling_token);
    } catch (err) {
        errorDiv.textContent = 'Erreur réseau, veuillez réessayer';
        errorDiv.classList.remove('d-none');
    }
}

/**
 * Affiche l'écran d'attente de validation.
 */
function showAccountRequestWaiting() {
    document.getElementById('loginSection').classList.add('d-none');
    document.getElementById('accountRequestWaiting').classList.remove('d-none');
    document.getElementById('accountRequestWaitingPending').classList.remove('d-none');
    document.getElementById('accountRequestWaitingRejected').classList.add('d-none');
    document.getElementById('accountRequestWaitingApproved').classList.add('d-none');
}

/**
 * Lance le polling HTTP toutes les 10 secondes pour vérifier le statut.
 */
function startAccountRequestPolling(requestId, pollingToken) {
    stopAccountRequestPolling();

    _accountRequestPollingTimer = setInterval(async () => {
        try {
            const response = await fetch(
                `${API_BASE}/account-requests/${requestId}/status?token=${encodeURIComponent(pollingToken)}`
            );
            if (!response.ok) return;
            const data = await response.json();

            if (data.status === 'approved') {
                stopAccountRequestPolling();
                sessionStorage.removeItem('accountRequest');

                // Plus d'auto-login : l'administrateur prévient l'utilisateur par
                // email avec l'identifiant à utiliser. On affiche une confirmation.
                document.getElementById('accountRequestWaitingPending').classList.add('d-none');
                document.getElementById('accountRequestWaitingRejected').classList.add('d-none');
                document.getElementById('accountRequestWaitingApproved').classList.remove('d-none');
            } else if (data.status === 'rejected') {
                stopAccountRequestPolling();
                sessionStorage.removeItem('accountRequest');
                document.getElementById('accountRequestWaitingPending').classList.add('d-none');
                document.getElementById('accountRequestWaitingRejected').classList.remove('d-none');
                document.getElementById('accountRequestRejectionReason').textContent =
                    data.rejection_reason || 'Votre demande a été refusée.';
            }
        } catch (err) {
            console.warn('Erreur polling demande de compte:', err);
        }
    }, 10000); // 10 secondes
}

/**
 * Arrête le polling.
 */
function stopAccountRequestPolling() {
    if (_accountRequestPollingTimer) {
        clearInterval(_accountRequestPollingTimer);
        _accountRequestPollingTimer = null;
    }
}

/**
 * Retour à la page de login depuis l'écran d'attente.
 */
function backToLoginFromRequest() {
    stopAccountRequestPolling();
    sessionStorage.removeItem('accountRequest');
    document.getElementById('accountRequestWaiting').classList.add('d-none');
    document.getElementById('loginSection').classList.remove('d-none');
}

// --- Reprendre le polling si on recharge la page pendant l'attente ---
(function resumeAccountRequestPolling() {
    const stored = sessionStorage.getItem('accountRequest');
    if (stored) {
        try {
            const { requestId, pollingToken } = JSON.parse(stored);
            if (requestId && pollingToken) {
                setTimeout(() => {
                    showAccountRequestWaiting();
                    startAccountRequestPolling(requestId, pollingToken);
                }, 100);
            }
        } catch (e) {
            sessionStorage.removeItem('accountRequest');
        }
    }
})();


// =============================================================================
// ===== ADMIN : Onglet Demandes de compte =====
// =============================================================================

/**
 * Charge le badge du nombre de demandes pending.
 */
async function loadPendingRequestsCount() {
    try {
        const data = await api.getAccountRequestsCount();
        const badge = document.getElementById('accountRequestsTabCount');
        if (badge) {
            badge.textContent = data.count;
            if (data.count > 0) {
                badge.classList.remove('d-none');
            } else {
                badge.classList.add('d-none');
            }
        }
    } catch (e) {
        console.warn('Erreur chargement compteur demandes:', e);
    }
}

/**
 * Charge et affiche la liste des demandes de compte.
 */
async function loadAccountRequestsTab() {
    try {
        // Charger l'état du toggle inscriptions
        try {
            const settingData = await api.getSetting('account_requests_enabled');
            const toggle = document.getElementById('toggleAccountRequests');
            if (toggle) toggle.checked = settingData.value === 'true';
        } catch (e) {
            console.error('Impossible de charger le setting account_requests_enabled:', e);
        }

        const statusFilter = document.getElementById('accountRequestsFilterStatus')?.value || '';
        const data = await api.getAccountRequests({ status: statusFilter });

        // Stats — charger toutes les demandes pour les compteurs
        const allData = await api.getAccountRequests({ status: '' });
        const allReqs = allData.requests || [];
        document.getElementById('reqStatPending').textContent = allReqs.filter(r => r.status === 'pending').length;
        document.getElementById('reqStatApproved').textContent = allReqs.filter(r => r.status === 'approved').length;
        document.getElementById('reqStatRejected').textContent = allReqs.filter(r => r.status === 'rejected').length;

        const requests = data.requests || [];
        const container = document.getElementById('accountRequestsTable');

        if (requests.length === 0) {
            container.innerHTML = '<div class="text-center text-muted py-4">Aucune demande</div>';
            return;
        }

        const statusBadge = (st) => {
            switch (st) {
                case 'pending': return '<span class="badge bg-warning text-dark">En attente</span>';
                case 'approved': return '<span class="badge bg-success">Approuvée</span>';
                case 'rejected': return '<span class="badge bg-danger">Refusée</span>';
                default: return `<span class="badge bg-secondary">${st}</span>`;
            }
        };

        let html = `<table class="table table-hover table-sm mb-0">
            <thead class="table-light">
                <tr>
                    <th>Nom</th>
                    <th>Prénom</th>
                    <th>Identifiant</th>
                    <th>Date</th>
                    <th>Statut</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>`;

        for (const req of requests) {
            const date = parseUTCDate(req.created_at).toLocaleDateString('fr-FR', {
                day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
            });
            const safeFN = escapeHtml(req.first_name).replace(/'/g, "\\'");
            const safeLN = escapeHtml(req.last_name).replace(/'/g, "\\'");
            const safeUN = escapeHtml(req.username).replace(/'/g, "\\'");
            const safeEmail = escapeHtml(req.email || '').replace(/'/g, "\\'");

            html += `<tr>
                <td>${escapeHtml(req.last_name)}</td>
                <td>${escapeHtml(req.first_name)}</td>
                <td><code>${escapeHtml(req.username)}</code></td>
                <td><small>${date}</small></td>
                <td>${statusBadge(req.status)}</td>
                <td>`;

            if (req.status === 'pending') {
                html += `
                    <button class="btn btn-success btn-sm me-1" onclick="showApproveRequestModal(${req.id}, '${safeFN}', '${safeLN}', '${safeUN}', '${safeEmail}')" title="Approuver">
                        <i class="bi bi-check-lg"></i>
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="showRejectRequestModal(${req.id}, '${safeFN}', '${safeLN}')" title="Refuser">
                        <i class="bi bi-x-lg"></i>
                    </button>`;
            } else if (req.status === 'rejected' && req.rejection_reason) {
                html += `<small class="text-muted" title="${escapeHtml(req.rejection_reason)}"><i class="bi bi-info-circle"></i> ${escapeHtml(req.rejection_reason.substring(0, 50))}${req.rejection_reason.length > 50 ? '...' : ''}</small>`;
            } else if (req.status === 'approved' && req.reviewed_by) {
                html += `<small class="text-muted">par ${escapeHtml(req.reviewed_by)}</small>`;
            }

            html += `</td></tr>`;
        }

        html += '</tbody></table>';
        container.innerHTML = html;

        // Mettre à jour le badge
        loadPendingRequestsCount();
    } catch (err) {
        console.error('Erreur chargement demandes:', err);
        document.getElementById('accountRequestsTable').innerHTML =
            '<div class="text-center text-danger py-4">Erreur de chargement</div>';
    }
}

/**
 * Toggle activation/désactivation des demandes de compte.
 */
async function handleToggleAccountRequests(checked) {
    try {
        await api.updateSetting('account_requests_enabled', checked ? 'true' : 'false');
        showToast('Inscriptions', checked ? 'Demandes de compte activées' : 'Demandes de compte désactivées', 'success');
    } catch (e) {
        console.error('Erreur toggle account requests:', e);
        showToast('Erreur', 'Impossible de modifier le paramètre', 'danger');
        // Remettre le toggle dans l'état précédent
        const toggle = document.getElementById('toggleAccountRequests');
        if (toggle) toggle.checked = !checked;
    }
}

async function showAccountRequestNotificationAdminsModal() {
    if (!accountRequestNotificationAdminsModal) {
        accountRequestNotificationAdminsModal = new bootstrap.Modal(document.getElementById('accountRequestNotificationAdminsModal'));
    }

    const list = document.getElementById('accountRequestNotificationAdminsList');
    const error = document.getElementById('accountRequestNotificationAdminsError');
    const saveBtn = document.getElementById('saveAccountRequestNotificationAdminsBtn');
    error.classList.add('d-none');
    error.textContent = '';
    saveBtn.disabled = true;
    list.innerHTML = '<small class="text-muted">Chargement...</small>';
    accountRequestNotificationAdminsModal.show();

    try {
        const data = await api.getAccountRequestNotificationAdmins();
        renderAccountRequestNotificationAdmins(data);
        saveBtn.disabled = false;
    } catch (err) {
        list.innerHTML = '<small class="text-danger">Erreur de chargement</small>';
        error.textContent = err.message || 'Impossible de charger les administrateurs';
        error.classList.remove('d-none');
    }
}

function renderAccountRequestNotificationAdmins(data) {
    const list = document.getElementById('accountRequestNotificationAdminsList');
    const admins = data.admins || [];
    const selectedIds = new Set((data.selected_admin_ids || []).map(id => parseInt(id)));

    if (admins.length === 0) {
        list.innerHTML = '<small class="text-muted">Aucun administrateur actif</small>';
        return;
    }

    list.innerHTML = admins.map(admin => {
        const adminId = parseInt(admin.id);
        const checkboxId = `accountRequestNotificationAdmin${adminId}`;
        const email = admin.email ? `<small class="text-muted ms-1">${escapeHtml(admin.email)}</small>` : '';
        return `
            <div class="form-check py-1">
                <input class="form-check-input account-request-notification-admin-cb" type="checkbox" value="${adminId}" id="${checkboxId}" ${selectedIds.has(adminId) ? 'checked' : ''}>
                <label class="form-check-label" for="${checkboxId}">
                    <span>${escapeHtml(admin.username)}</span>${email}
                </label>
            </div>`;
    }).join('');
}

async function handleSaveAccountRequestNotificationAdmins(event) {
    event.preventDefault();
    const saveBtn = document.getElementById('saveAccountRequestNotificationAdminsBtn');
    const error = document.getElementById('accountRequestNotificationAdminsError');
    const adminIds = Array.from(document.querySelectorAll('.account-request-notification-admin-cb:checked'))
        .map(cb => parseInt(cb.value));

    error.classList.add('d-none');
    error.textContent = '';
    saveBtn.disabled = true;

    try {
        await api.updateAccountRequestNotificationAdmins(adminIds);
        accountRequestNotificationAdminsModal.hide();
        showToast('Notifications', 'Destinataires mis à jour', 'success');
        loadPendingRequestsCount();
    } catch (err) {
        error.textContent = err.message || "Impossible d'enregistrer les destinataires";
        error.classList.remove('d-none');
    } finally {
        saveBtn.disabled = false;
    }
}

/**
 * Ouvre la modale de configuration du formulaire de demande de compte.
 */
async function showAccountRequestFormConfigModal() {
    if (!accountRequestFormConfigModal) {
        accountRequestFormConfigModal = new bootstrap.Modal(document.getElementById('accountRequestFormConfigModal'));
    }

    const error = document.getElementById('accountRequestFormConfigError');
    const saveBtn = document.getElementById('saveAccountRequestFormConfigBtn');
    error.classList.add('d-none');
    error.textContent = '';
    saveBtn.disabled = true;
    accountRequestFormConfigModal.show();

    try {
        const config = await api.getAccountRequestFormConfig();
        document.getElementById('accountRequestFormConfigMessage').value = config.important_message || '';
        document.getElementById('accountRequestFormConfigShowLastName').checked = config.show_last_name !== false;
        document.getElementById('accountRequestFormConfigShowFirstName').checked = config.show_first_name !== false;
        document.getElementById('accountRequestFormConfigShowEmail').checked = config.show_email === true;
        saveBtn.disabled = false;
    } catch (err) {
        error.textContent = err.message || 'Impossible de charger la configuration';
        error.classList.remove('d-none');
    }
}

/**
 * Enregistre la configuration du formulaire de demande de compte.
 */
async function handleSaveAccountRequestFormConfig(event) {
    event.preventDefault();
    const saveBtn = document.getElementById('saveAccountRequestFormConfigBtn');
    const error = document.getElementById('accountRequestFormConfigError');
    error.classList.add('d-none');
    error.textContent = '';
    saveBtn.disabled = true;

    const config = {
        important_message: document.getElementById('accountRequestFormConfigMessage').value.trim(),
        show_last_name: document.getElementById('accountRequestFormConfigShowLastName').checked,
        show_first_name: document.getElementById('accountRequestFormConfigShowFirstName').checked,
        show_email: document.getElementById('accountRequestFormConfigShowEmail').checked,
    };

    try {
        await api.updateAccountRequestFormConfig(config);
        accountRequestFormConfigModal.hide();
        showToast('Formulaire', 'Configuration enregistrée', 'success');
    } catch (err) {
        error.textContent = err.message || "Impossible d'enregistrer la configuration";
        error.classList.remove('d-none');
    } finally {
        saveBtn.disabled = false;
    }
}

/**
 * Ouvre la modale d'approbation avec la liste des profils.
 */
async function showApproveRequestModal(requestId, firstName, lastName, username, email) {
    if (!approveRequestModal) {
        approveRequestModal = new bootstrap.Modal(document.getElementById('approveRequestModal'));
    }
    document.getElementById('approveRequestId').value = requestId;
    const displayName = `${firstName || ''} ${lastName || ''}`.trim();
    document.getElementById('approveRequestName').textContent = displayName || '(nom non renseigné)';
    document.getElementById('approveRequestUsername').textContent = username;
    document.getElementById('approveRequestUsernameInput').value = username;
    document.getElementById('approveRequestLastNameInput').value = lastName || '';
    document.getElementById('approveRequestFirstNameInput').value = firstName || '';
    document.getElementById('approveRequestEmail').value = email || '';
    _approveRequestContext = { firstName: firstName || '', lastName: lastName || '' };

    // Charger les profils disponibles
    const profilesContainer = document.getElementById('approveRequestProfiles');
    profilesContainer.innerHTML = '<small class="text-muted">Chargement...</small>';

    try {
        const profiles = await api.getProfiles();
        if (profiles.length === 0) {
            profilesContainer.innerHTML = '<small class="text-muted">Aucun profil disponible</small>';
        } else {
            profilesContainer.innerHTML = profiles.map(p => `
                <div class="form-check">
                    <input class="form-check-input approve-profile-cb" type="checkbox" value="${p.id}" id="approveProfile${p.id}">
                    <label class="form-check-label" for="approveProfile${p.id}">
                        <span class="badge" style="background-color: ${p.color || '#6c757d'}">${escapeHtml(p.name)}</span>
                        ${p.description ? `<small class="text-muted ms-1">${escapeHtml(p.description)}</small>` : ''}
                    </label>
                </div>
            `).join('');
        }
    } catch (e) {
        profilesContainer.innerHTML = '<small class="text-danger">Erreur de chargement des profils</small>';
    }

    approveRequestModal.show();
}

/**
 * Soumet l'approbation de la demande.
 */
async function handleApproveRequest(event) {
    event.preventDefault();
    const requestId = document.getElementById('approveRequestId').value;
    const profileIds = Array.from(document.querySelectorAll('.approve-profile-cb:checked')).map(cb => parseInt(cb.value));
    const username = document.getElementById('approveRequestUsernameInput').value.trim().toLowerCase();
    const firstName = document.getElementById('approveRequestFirstNameInput').value.trim();
    const lastName = document.getElementById('approveRequestLastNameInput').value.trim();
    const email = document.getElementById('approveRequestEmail').value.trim();

    if (!username || username.length < 3) {
        showToast('Erreur', "L'identifiant doit contenir au moins 3 caractères", 'warning');
        return;
    }

    const payload = {
        profile_ids: profileIds,
        username,
        first_name: firstName,
        last_name: lastName,
    };
    if (email) payload.email = email;

    try {
        const result = await api.approveAccountRequest(requestId, payload);
        approveRequestModal.hide();

        const finalUsername = (result && result.username) || username;
        openAccountReadyMailto(email, firstName, finalUsername);

        showToast('Demande approuvée', "Compte créé. L'email d'information est prêt à être envoyé.", 'success');
        loadAccountRequestsTab();
    } catch (err) {
        showToast('Erreur', err.message || "Impossible d'approuver la demande", 'danger');
    }
}

/**
 * Ouvre le client mail de l'admin avec un message pré-rempli indiquant à
 * l'utilisateur que son compte est prêt et l'identifiant à utiliser.
 */
function openAccountReadyMailto(email, firstName, username) {
    const origin = window.location.origin;
    const greeting = firstName ? `Bonjour ${firstName},` : 'Bonjour,';
    const subject = 'Votre compte HUB est prêt';
    const body =
        `${greeting}\n\n` +
        `Votre demande de compte a été acceptée et votre compte est désormais prêt à être utilisé.\n\n` +
        `Identifiant de connexion : ${username}\n` +
        `Mot de passe : celui que vous avez choisi lors de votre demande.\n\n` +
        `Vous pouvez vous connecter dès maintenant : ${origin}\n\n` +
        `Cordialement,\nL'équipe d'administration`;

    const mailto = `mailto:${email || ''}` +
        `?subject=${encodeURIComponent(subject)}` +
        `&body=${encodeURIComponent(body)}`;

    const a = document.createElement('a');
    a.href = mailto;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

/**
 * Ouvre la modale de refus.
 */
function showRejectRequestModal(requestId, firstName, lastName) {
    if (!rejectRequestModal) {
        rejectRequestModal = new bootstrap.Modal(document.getElementById('rejectRequestModal'));
    }
    document.getElementById('rejectRequestId').value = requestId;
    document.getElementById('rejectRequestName').textContent = `${firstName} ${lastName}`;
    document.getElementById('rejectRequestReason').value = '';
    rejectRequestModal.show();
}

/**
 * Soumet le refus de la demande.
 */
async function handleRejectRequest(event) {
    event.preventDefault();
    const requestId = document.getElementById('rejectRequestId').value;
    const reason = document.getElementById('rejectRequestReason').value.trim();

    if (!reason) {
        showToast('Erreur', 'La raison du refus est obligatoire', 'warning');
        return;
    }

    try {
        await api.rejectAccountRequest(requestId, { rejection_reason: reason });
        rejectRequestModal.hide();
        showToast('Demande refusée', 'La demande a été refusée', 'warning');
        loadAccountRequestsTab();
    } catch (err) {
        showToast('Erreur', err.message || 'Impossible de refuser la demande', 'danger');
    }
}
