/**
 * Hub Application - Shared frontend helpers
 */

/**
 * Parse une date ISO renvoyee par l'API (stockee en UTC sans suffixe Z).
 * Si la chaine n'a pas d'indicateur de timezone, on ajoute 'Z' pour que
 * le navigateur l'interprete correctement comme UTC avant conversion locale.
 */
function parseUTCDate(isoString) {
    if (!isoString) return null;
    if (typeof isoString === 'string' && !isoString.endsWith('Z') && !isoString.includes('+') && !/[-]\d{2}:\d{2}$/.test(isoString)) {
        return new Date(isoString + 'Z');
    }
    return new Date(isoString);
}

/**
 * Valide qu'une URL de callback est sure (meme origine ou app connue).
 * Bloque les redirections vers des domaines externes.
 */
function isValidCallbackUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        if (parsed.origin === window.location.origin) {
            return true;
        }

        const hostname = parsed.hostname;
        if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1') {
            return true;
        }

        const parts = hostname.split('.');
        if (parts.length === 4 && parts.every(p => !isNaN(p))) {
            const first = parseInt(parts[0]);
            const second = parseInt(parts[1]);
            if (first === 10) return true;
            if (first === 172 && second >= 16 && second <= 31) return true;
            if (first === 192 && second === 168) return true;
        }

        if (!hostname.includes('.') || hostname.endsWith('.local') || hostname.endsWith('.lan')) {
            return true;
        }

        console.warn('SSO callback bloque - origine non autorisee:', parsed.origin);
        return false;
    } catch (e) {
        console.warn('SSO callback invalide:', url, e);
        return false;
    }
}

function normalizeUserProfileIds(profileIds) {
    return [...new Set(
        (profileIds || [])
            .map(profileId => parseInt(profileId, 10))
            .filter(profileId => Number.isInteger(profileId))
    )].sort((leftId, rightId) => leftId - rightId);
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatAppVersionLabel(version) {
    if (version === null || version === undefined) return '';
    const raw = String(version).trim();
    if (!raw) return '';

    const normalized = raw.replace(/^[vV]\s*/, '');
    return normalized ? `v${normalized}` : '';
}

/**
 * Formate une date ISO en format lisible.
 */
function formatDate(isoDate) {
    if (!isoDate) return '-';
    const date = parseUTCDate(isoDate);
    return date.toLocaleDateString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function normalizeProfileAppSearchText(value) {
    return String(value || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase();
}

/**
 * Formate une duree en secondes en chaine courte (ex: "1m 23s", "45s", "2h 5m").
 */
function formatBackupDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return '-';
    const total = Math.max(0, Math.floor(Number(seconds)));
    if (total < 60) return `${total}s`;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return s > 0 ? `${h}h ${m}m ${s}s` : `${h}h ${m}m`;
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

/**
 * Formate une taille de fichier.
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/**
 * Parse un CSV cote client pour extraire les mots de passe.
 */
function parseCsvLocally(text) {
    const lines = text.split(/\r?\n/);
    if (lines.length < 2) return [];

    const sep = lines[0].includes(';') ? ';' : ',';

    const results = [];
    for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line || line.startsWith('#')) continue;

        const parts = line.split(sep);
        results.push({
            username: (parts[0] || '').trim(),
            password: (parts[1] || '').trim(),
            profile: (parts[2] || '').trim()
        });
    }
    return results;
}

// =============================================================================
// ===== GLOBALES POUR LA DÉLÉGATION data-action =====
// =============================================================================

/**
 * data-action="noop" — bloque la propagation vers le data-action du parent
 * (closest() s'arrête ici) sans autre effet.
 * Remplace les anciens handlers stopPropagation inline.
 */
function noop() {}

/**
 * data-action="closeWindow" — ferme l'onglet/fenêtre courant (ouvert par script)
 */
function closeWindow() {
    window.close();
}
