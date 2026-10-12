/**
 * Hub Application - téléchargement résilient (HTTP Range + reprise)
 *
 * Télécharge les gros fichiers (backups chiffrés, exports ZIP, snapshots) par
 * tranches afin de survivre aux coupures réseau :
 *   - requêtes Range (206 Partial Content) : une coupure ne fait perdre que la
 *     tranche en cours (4 Mo), pas le fichier entier ;
 *   - reprise automatique : chaque tranche est retentée (backoff exponentiel) ;
 *   - timeout par requête pour détecter les connexions pendantes ;
 *   - vérification de la taille finale contre le total annoncé par le serveur
 *     (impossible de récupérer un fichier tronqué silencieusement) ;
 *   - repli automatique en téléchargement simple si le serveur ignore les Range.
 *
 * Aucune dépendance externe ; fonctions globales comme les autres modules.
 * Backend : FileResponse Starlette gère nativement les Range (206).
 */

const DL_CHUNK_SIZE = 4 * 1024 * 1024;   // 4 Mo par tranche
const DL_MAX_RETRIES = 5;                // tentatives par tranche
const DL_REQUEST_TIMEOUT_MS = 90 * 1000; // timeout par requête (tranche)

function parseContentRange(value) {
    // "bytes 0-4194303/27208352" → { start, end, total } ; null sinon.
    // total peut être null si le serveur renvoie "*/<inconnu>".
    if (!value) return null;
    const m = /^bytes\s+(\d+)-(\d+)\/(\d+|\*)$/i.exec(value.trim());
    if (!m) return null;
    return {
        start: parseInt(m[1], 10),
        end: parseInt(m[2], 10),
        total: m[3] === '*' ? null : parseInt(m[3], 10)
    };
}

function formatDlBytes(n) {
    if (!Number.isFinite(n)) return '?';
    if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + ' Go';
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' Mo';
    if (n >= 1024) return (n / 1024).toFixed(0) + ' Ko';
    return String(n) + ' o';
}

function sleepDl(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * fetch avec timeout : une connexion qui reste pendante est abortée puis
 * retentée au niveau de la tranche, au lieu de bloquer indéfiniment.
 */
async function fetchDlWithTimeout(url, options = {}, timeoutMs = DL_REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } finally {
        clearTimeout(timer);
    }
}

// ---------------------------------------------------------------------------
// Indicateur de progression (pastille fixe, mise à jour par tranche — aucune
// animation continue, cf. PERFORMANCE.md)
// ---------------------------------------------------------------------------

function dlProgressShow(label) {
    // Environnement sans DOM (tests Node) → progression silencieuse.
    if (typeof document === 'undefined' || !document.body) {
        return { update() {}, done() {} };
    }
    dlProgressHide(); // une seule pastille à la fois
    const el = document.createElement('div');
    el.id = 'resilientDownloadProgress';
    el.className = 'position-fixed bg-white border rounded shadow-sm p-2';
    el.style.cssText = 'bottom:1rem;right:1rem;z-index:2000;width:280px;';
    el.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-1">
            <small class="fw-bold text-truncate" data-dl-label>${escapeHtml(label)}</small>
            <button type="button" class="btn-close btn-close-sm ms-2" data-dl-close aria-label="Masquer"></button>
        </div>
        <div class="progress" style="height:6px;">
            <div class="progress-bar bg-primary" role="progressbar" data-dl-bar
                 style="width:0%;" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
        </div>
        <small class="text-muted" data-dl-text>…</small>
    `;
    document.body.appendChild(el);
    el.querySelector('[data-dl-close]').addEventListener('click', () => el.remove());
    return {
        update(received, total) {
            if (!el.isConnected) return; // masquée par l'utilisateur → téléchargement en silence
            const text = el.querySelector('[data-dl-text]');
            const bar = el.querySelector('[data-dl-bar]');
            if (total) {
                const pct = Math.min(100, Math.round((received / total) * 100));
                bar.style.width = pct + '%';
                bar.setAttribute('aria-valuenow', String(pct));
                text.textContent = `${formatDlBytes(received)} / ${formatDlBytes(total)} (${pct} %)`;
            } else {
                text.textContent = formatDlBytes(received);
            }
        },
        done() { el.remove(); }
    };
}

function dlProgressHide() {
    const existing = document.getElementById('resilientDownloadProgress');
    if (existing) existing.remove();
}

/**
 * Télécharge `url` de façon résiliente et retourne un Blob complet.
 *
 * @param {string} url  Endpoint de téléchargement.
 * @param {Object} headers  En-têtes (auth incluse) envoyés sur CHAQUE requête.
 * @param {Object} [opts]
 * @param {string} [opts.label]  Libellé affiché dans la pastille de progression.
 * @param {number} [opts.chunkSize]  Taille de tranche (défaut 4 Mo).
 * @returns {Promise<Blob>}
 * @throws {Error} Message explicite si le fichier est incomplet après retries.
 */
async function resilientDownload(url, headers = {}, opts = {}) {
    const label = opts.label || 'Téléchargement';
    const chunkSize = Math.max(1024 * 1024, opts.chunkSize || DL_CHUNK_SIZE);
    const progress = dlProgressShow(label);
    try {
        // 1) Sonde : première tranche. 206 = support Range, 200 = non supporté.
        const first = await fetchDlWithTimeout(url, {
            headers: { ...headers, 'Range': `bytes=0-${chunkSize - 1}` }
        });
        if (!first.ok) {
            let detail = `HTTP ${first.status}`;
            try {
                const err = await first.json();
                if (err?.detail) detail = err.detail;
            } catch (_) { /* corps non JSON */ }
            throw new Error(detail);
        }

        const probeRange = parseContentRange(first.headers.get('Content-Range'));

        // 2) Repli simple : serveur sans Range (200) ou total inconnu.
        //    On consomme la réponse déjà ouverte (pas de double requête).
        if (first.status !== 206 || !probeRange || probeRange.total === null) {
            const blob = await first.blob();
            progress.done();
            return blob;
        }

        // 3) Mode tranches avec reprise.
        const total = probeRange.total;
        const chunks = [];
        let received = 0;

        const firstBuf = await first.arrayBuffer();
        chunks.push(firstBuf);
        received += firstBuf.byteLength;
        progress.update(received, total);

        while (received < total) {
            const start = received;
            const end = Math.min(start + chunkSize, total) - 1;
            const expected = end - start + 1;
            let buf = null;
            let lastErr = null;

            for (let attempt = 0; attempt <= DL_MAX_RETRIES; attempt++) {
                try {
                    const resp = await fetchDlWithTimeout(url, {
                        headers: { ...headers, 'Range': `bytes=${start}-${end}` }
                    });
                    if (resp.status !== 206) {
                        throw new Error(`HTTP ${resp.status} au lieu de 206 pour la tranche ${start}-${end}`);
                    }
                    const candidate = await resp.arrayBuffer();
                    if (candidate.byteLength !== expected) {
                        throw new Error(`Tranche ${start}-${end} incomplète (${candidate.byteLength}/${expected} o)`);
                    }
                    buf = candidate;
                    break;
                } catch (e) {
                    lastErr = e;
                    if (attempt < DL_MAX_RETRIES) {
                        // Backoff exponentiel : 0,5 s / 1 s / 2 s / 4 s / 8 s
                        await sleepDl(500 * Math.pow(2, attempt));
                    }
                }
            }

            if (!buf) {
                throw new Error(
                    `Téléchargement interrompu : tranche ${start}-${end} non récupérée ` +
                    `après ${DL_MAX_RETRIES + 1} tentatives (${lastErr?.message || 'erreur réseau'}). ` +
                    `Vérifiez la connexion puis relancez.`
                );
            }

            chunks.push(buf);
            received += buf.byteLength;
            progress.update(received, total);
        }

        if (received !== total) {
            throw new Error(`Téléchargement incomplet : ${received} / ${total} octets reçus`);
        }

        progress.done();
        return new Blob(chunks, { type: 'application/octet-stream' });
    } catch (e) {
        progress.done();
        throw e;
    }
}
