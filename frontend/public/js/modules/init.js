// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - initialisation DOM et listeners globaux
 *
 * Extrait mécaniquement de frontend/public/js/app.js.
 * Les fonctions restent globales pour conserver la compatibilité avec les handlers HTML inline.
 */

/**
 * Met à jour le badge de version de la navbar depuis /health (VERSION fichier,
 * source unique de vérité) — le texte statique de index.html n'est qu'un
 * fallback de build (maintenu par sync_version.py). Échec silencieux : le
 * badge statique reste affiché.
 */
async function updateHubVersionBadge() {
    const badge = document.getElementById('navbarVersionBadge');
    if (!badge) return;
    try {
        const resp = await fetch('/api/health', { signal: AbortSignal.timeout(3000) });
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.version) badge.textContent = `v${data.version}`;
    } catch (error) {
        console.warn('Badge de version : /health injoignable, fallback statique.', error);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    updateHubVersionBadge();

    // Initialize Bootstrap components
    // (La modale appFormModal n'existe plus — fiche v6 : son formulaire est
    // inline dans #appDetailPane ; pas d'instance Bootstrap à créer.)
    userFormModal = new bootstrap.Modal(document.getElementById('userFormModal'));
    serviceAccountFormModal = new bootstrap.Modal(document.getElementById('serviceAccountFormModal'));
    saCredentialsModal = new bootstrap.Modal(document.getElementById('saCredentialsModal'));
    oidcClientFormModal = new bootstrap.Modal(document.getElementById('oidcClientFormModal'));
    oidcClientCredentialsModal = new bootstrap.Modal(document.getElementById('oidcClientCredentialsModal'));
    renameUsernameModal = new bootstrap.Modal(document.getElementById('renameUsernameModal'));
    toast = new bootstrap.Toast(document.getElementById('toast'));

    // Renommage d'identifiant utilisateur (confirmation)
    const renameConfirmBtn = document.getElementById('renameConfirmBtn');
    if (renameConfirmBtn) {
        renameConfirmBtn.addEventListener('click', confirmRename);
    }

    // Mettre à jour le preview quand la couleur change
    const appColorInput = document.getElementById('appColor');
    if (appColorInput) {
        appColorInput.addEventListener('input', updateAppFormPreview);
    }

    // Navigation admin : l'ancien mécanisme d'onglets à 2 niveaux (barre
    // d'onglets, mémorisation du sous-onglet en localStorage, rangée de
    // pilules conditionnelle) est remplacé par le hub « cockpit » de
    // admin-hub.js (routing par hash).

    // Écouter l'événement de session expirée depuis l'API
    // Cela permet de déconnecter immédiatement l'utilisateur si sa session expire
    // (par exemple après un reboot du serveur)
    window.addEventListener('hub:sessionExpired', (event) => {
        console.log('Événement hub:sessionExpired reçu');
        handleSessionExpired(event.detail?.message);
    });

    window.addEventListener('hub:beaconTokenUpdated', (event) => {
        if (event.detail?.beacon_token) {
            beaconToken = event.detail.beacon_token;
            console.log('🔑 Beacon token mis à jour après refresh');
        }
    });

    // Écouter quand la page redevient visible (utilisateur revient sur l'onglet)
    // Utile pour détecter un retour après un redémarrage serveur
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && api.token && currentUser) {
            // Vérifier si le token est expiré ou proche de l'expiration
            // Les timers setTimeout sont throttlés/suspendus par le navigateur
            // quand l'onglet est en arrière-plan, le refresh proactif peut avoir été raté
            const expiry = api._parseTokenExpiry(api.token);
            if (expiry && expiry.getTime() - Date.now() < 2 * 60 * 1000) {
                console.log('👁️ Token expiré ou proche de l\'expiration au retour — refresh...');
                api.tryRefreshToken();
            }
            // Vérifier si la connexion SSE est toujours active
            if (!eventSource || eventSource.readyState !== EventSource.OPEN) {
                console.log('👁️ Page redevenue visible - reconnexion SSE...');
                initEventSource(true);  // Reconnexion avec rafraîchissement (badges inclus)
            } else {
                // SSE déjà connecté : resynchroniser les badges au cas où des
                // événements auraient été throttlés pendant l'arrière-plan.
                refreshAllBadges();
            }
        }
    });

    // Gérer la fermeture du navigateur/onglet - tenter un logout synchrone
    // Utilise sendBeacon pour envoyer la requête même si la page se ferme
    window.addEventListener('beforeunload', (event) => {
        if (api.token && currentUser) {
            // Utiliser sendBeacon pour tenter de fermer la session côté serveur
            // Note: sendBeacon ne peut pas inclure de headers Authorization
            // On utilise donc un endpoint dédié avec le token en body
            try {
                const data = JSON.stringify({
                    reason: 'browser_closed',
                    user_id: currentUser.id,
                    beacon_token: beaconToken || ''
                });
                navigator.sendBeacon('/api/auth/beacon-logout', data);
                console.log('📤 Beacon logout envoyé');
            } catch (e) {
                console.warn('Beacon logout failed:', e);
            }
        }
    });

    // Vérifier les paramètres URL
    const params = new URLSearchParams(window.location.search);
    const callbackUrl = params.get('callback');
    const action = params.get('action');
    
    // Si action=logout, l'utilisateur a été déconnecté d'une application
    // => Fermer l'onglet automatiquement pour éviter d'accumuler les onglets Hub
    if (action === 'logout') {
        console.log('Déconnexion depuis une application - fermeture de l\'onglet');
        // Effacer tout callback en attente
        clearPendingCallback();
        
        // Tenter de fermer l'onglet
        // Note: window.close() fonctionne uniquement si l'onglet a été ouvert par script (window.open)
        // ce qui est le cas quand on ouvre une app depuis le Hub
        try {
            window.close();
        } catch (e) {
            console.log('Impossible de fermer l\'onglet automatiquement');
        }
        
        // Si l'onglet n'a pas pu être fermé (ex: ouvert manuellement), 
        // afficher une page minimaliste indiquant la déconnexion
        // avec un délai pour laisser le temps à window.close() de s'exécuter
        setTimeout(() => {
            // Si on arrive ici, c'est que l'onglet n'a pas été fermé
            document.body.innerHTML = `
                <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; font-family: system-ui, sans-serif; background: #f8f9fa;">
                    <i class="bi bi-check-circle-fill" style="font-size: 4rem; color: #198754; margin-bottom: 1rem;"></i>
                    <h2 style="color: #212529; margin-bottom: 0.5rem;">Déconnecté</h2>
                    <p style="color: #6c757d;">Vous pouvez fermer cet onglet.</p>
                    <button data-action="closeWindow" style="margin-top: 1rem; padding: 0.5rem 1.5rem; background: #0d6efd; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem;">
                        Fermer l'onglet
                    </button>
                </div>
            `;
        }, 100);
        
        return; // Stopper l'initialisation normale
    }
    // Sinon, si un callback est présent, c'est une demande de connexion SSO
    // URL format: http://hub/?callback=http://app:port/callback
    else if (callbackUrl) {
        setPendingCallback(decodeURIComponent(callbackUrl));
        // Nettoyer l'URL sans recharger la page
        window.history.replaceState({}, '', window.location.pathname);
        console.log('SSO callback enregistré:', getPendingCallback());
    }

    // Check if already logged in
    // Vérifier l'authentification au chargement
    if (api.token) {
        loadCurrentUser();
    } else {
        // Pas de token - afficher directement le login
        showLoginScreen();
    }

    // Auto-generate slug from name
    document.getElementById('appName').addEventListener('input', (e) => {
        const slugField = document.getElementById('appSlug');
        if (!document.getElementById('appId').value) {
            slugField.value = e.target.value
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/(^-|-$)/g, '');
        }
    });
    
    // Update URL preview when ports change
    document.getElementById('appCustomHostValue').addEventListener('input', updateUrlPreview);
});
