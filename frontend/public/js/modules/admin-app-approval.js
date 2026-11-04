// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - approbation applications
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

// ===== Approbation des Applications =====

let rejectAppModal = null;

/**
 * Met à jour le badge « apps en attente » de l'onglet Applications.
 * Source unique de vérité pour le badge (appelée par loadPendingAppsCount
 * côté SSE/login ET par renderAppsTable côté tableau admin).
 *
 * Sémantique : pending UNIQUEMENT. Une app rejetée n'attend pas
 * d'approbation ; elle reste visible via le filtre « Rejetées » du tableau.
 */
function updatePendingAppsBadge(count) {
    const badge = document.getElementById('pendingAppsBadge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count;
        badge.classList.remove('d-none');
    } else {
        badge.classList.add('d-none');
    }
}

/**
 * Charge et affiche le nombre d'applications en attente (badge)
 */
async function loadPendingAppsCount() {
    try {
        const data = await api.getPendingApps();
        updatePendingAppsBadge(data.total);
    } catch (error) {
        console.error('Erreur chargement compteur apps en attente:', error);
    }
}

/**
 * Approuve une application
 */
async function approveApp(slug) {
    if (!confirm(`Approuver l'application "${slug}" ?\n\nL'application et son Service Account seront activés.`)) {
        return;
    }
    
    try {
        const result = await api.approveApp(slug);
        showToast('Application approuvée', result.message, 'success');
        await loadAdminData();    // Rafraîchir le tableau principal (inclut les pending)
        await loadApps();         // Rafraîchir le dashboard
    } catch (error) {
        console.error('Erreur approbation:', error);
        showToast('Erreur', `Impossible d'approuver: ${error.message}`, 'danger');
    }
}

/**
 * Affiche le modal de rejet avec champ raison
 */
function showRejectAppModal(slug, el) {
    const name = el?.dataset?.appName || '';
    document.getElementById('rejectAppSlug').value = slug;
    document.getElementById('rejectAppName').textContent = name;
    document.getElementById('rejectAppReason').value = '';
    
    if (!rejectAppModal) {
        rejectAppModal = new bootstrap.Modal(document.getElementById('rejectAppModal'));
    }
    rejectAppModal.show();
}

/**
 * Confirme le rejet d'une application
 */
async function confirmRejectApp() {
    const slug = document.getElementById('rejectAppSlug').value;
    const reason = document.getElementById('rejectAppReason').value.trim();
    
    if (!reason) {
        showToast('Erreur', 'Veuillez saisir une raison de rejet', 'warning');
        return;
    }
    
    try {
        const result = await api.rejectApp(slug, reason);
        rejectAppModal.hide();
        showToast('Application rejetée', result.message, 'warning');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur rejet:', error);
        showToast('Erreur', `Impossible de rejeter: ${error.message}`, 'danger');
    }
}

/**
 * Supprime une application en attente ou rejetée
 */
async function deletePendingApp(slug) {
    if (!confirm(`Supprimer définitivement l'application "${slug}" ?\n\nCette action est irréversible.`)) {
        return;
    }
    
    try {
        await api.deletePendingApp(slug);
        showToast('Application supprimée', `L'application "${slug}" a été supprimée`, 'success');
        await loadAdminData();  // Rafraîchir le tableau principal
    } catch (error) {
        console.error('Erreur suppression:', error);
        showToast('Erreur', `Impossible de supprimer: ${error.message}`, 'danger');
    }
}
