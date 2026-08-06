// SLO poste partagé — purge des sessions distantes via iframes cachées.
// Au login HUB d'un user B par-dessus un user A, le backend renvoie `remote_logout`
// (liste d'apps OIDC où A avait une session). Le frontend ouvre une iframe cachée
// vers `{app}/logout` pour chaque entrée : la SPA distante (ex. Vikunja) purge alors
// son localStorage + cookie HttpOnly via son propre endpoint /user/logout.
//
// Spec : docs/superpowers/specs/2026-08-06-slo-shared-workstation-design.md
//
// Convention : ce codebase n'utilise PAS d'ES modules natifs (scripts globaux via
// <script src> pour conserver la compatibilité avec les handlers HTML inline).
// `purgeRemoteSessions` est donc exposée en global, comme les autres modules du
// dossier js/modules/.

const SLO_DEFAULT_IFRAME_TIMEOUT_MS = 1500;

/**
 * Purge les sessions distantes en ouvrant des iframes cachées vers /logout.
 *
 * @param {Array<{logout_url: string}>} logoutList - Entrées renvoyées par POST /api/auth/token
 * @param {number} [timeoutMs=1500] - Durée d'affichage des iframes
 * @returns {Promise<void>}
 */
async function purgeRemoteSessions(logoutList, timeoutMs = SLO_DEFAULT_IFRAME_TIMEOUT_MS) {
    if (!Array.isArray(logoutList) || logoutList.length === 0) return;

    const frames = logoutList
        .filter(entry => entry && typeof entry.logout_url === 'string')
        .map(entry => {
            const iframe = document.createElement('iframe');
            iframe.style.display = 'none';
            iframe.style.width = '0';
            iframe.style.height = '0';
            iframe.setAttribute('aria-hidden', 'true');
            // allow-scripts : la SPA distante doit exécuter son JS de logout
            // allow-same-origin : pour qu'elle puisse accéder à son localStorage/cookie
            iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
            iframe.src = entry.logout_url;
            document.body.appendChild(iframe);
            return iframe;
        });

    if (frames.length === 0) return;

    // Laisser le temps à la SPA distante de charger + exécuter son logout
    await new Promise(resolve => setTimeout(resolve, timeoutMs));

    frames.forEach(f => {
        try { f.remove(); } catch (_) { /* ignore */ }
    });
}
