/**
 * Téléchargement du certificat CA du Hub + guide d'installation par plateforme.
 *
 * Bouton navbar (bi-shield-check) → modale #certificateModal :
 *  - GET /api/pki/ca/info  : empreinte SHA-256 + validité affichées (contrôle utilisateur),
 *  - GET /api/pki/ca.crt   : téléchargement blob (même pattern que api.js exportAuditLogs).
 *
 * Extrait de la fonctionnalité 2026-10-06 : supprimer les avertissements
 * « certificat auto-signé » en faisant installer la CA racine par les utilisateurs.
 * Endpoints publics — aucune session requise côté API (CA publique par nature).
 */

let certificateModalInstance = null;

/**
 * Ouvre la modale certificat et charge les métadonnées de la CA.
 */
async function showCertificateModal() {
    const modalEl = document.getElementById('certificateModal');
    if (!modalEl) return;
    // Recréer l'instance si la modale a été retirée du DOM (re-render partiel)
    if (!certificateModalInstance || certificateModalInstance._element !== modalEl) {
        if (certificateModalInstance) certificateModalInstance.dispose();
        certificateModalInstance = new bootstrap.Modal(modalEl);
    }
    certificateModalInstance.show();
    await loadCaInfo();
}

/**
 * Charge /api/pki/ca/info et remplit empreinte + validité.
 * Échec (HTTP ou réseau) : message inline, la modale reste utilisable pour le guide.
 */
async function loadCaInfo() {
    const errEl = document.getElementById('certInfoError');
    const fpEl = document.getElementById('certFingerprint');
    const valEl = document.getElementById('certValidity');
    errEl.classList.add('d-none');
    fpEl.textContent = 'Chargement…';
    valEl.textContent = 'Chargement…';
    try {
        const resp = await fetch('/api/pki/ca/info', { signal: AbortSignal.timeout(5000) });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const info = await resp.json();
        fpEl.textContent = (info.fingerprint_sha256 || '').toUpperCase().replace(/(..)/g, '$1 ').trim();
        const from = new Date(info.not_before).toLocaleDateString('fr-FR');
        const to = new Date(info.not_after).toLocaleDateString('fr-FR');
        valEl.textContent = `du ${from} au ${to}`;
    } catch (error) {
        console.warn('Info CA : /api/pki/ca/info injoignable.', error);
        fpEl.textContent = 'Indisponible';
        valEl.textContent = 'Indisponible';
        errEl.textContent = "Impossible de récupérer les informations du certificat. Vérifiez votre connexion puis réessayez (le téléchargement peut aussi échouer).";
        errEl.classList.remove('d-none');
    }
}

/**
 * Télécharge la CA racine via blob → <a download> (nom imposé par le backend :
 * POULIDOR-Hub-Root-CA.crt, respecté grâce à l'en-tête Content-Disposition).
 */
async function downloadCaCert() {
    try {
        const resp = await fetch('/api/pki/ca.crt', { signal: AbortSignal.timeout(15000) });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        // Nom de fichier depuis Content-Disposition, fallback fixe
        const match = /filename="([^"]+)"/.exec(resp.headers.get('content-disposition') || '');
        const filename = match ? match[1] : 'POULIDOR-Hub-Root-CA.crt';
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
        if (typeof showToast === 'function') {
            showToast('Téléchargement lancé', `${filename} — suivez le guide ci-dessous pour l'installer.`, 'success');
        }
    } catch (error) {
        console.error('Téléchargement de la CA : échec.', error);
        if (typeof showToast === 'function') {
            showToast('Échec du téléchargement', 'Le certificat n\'a pas pu être récupéré. Réessayez.', 'danger');
        }
    }
}
