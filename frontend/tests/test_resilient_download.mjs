#!/usr/bin/env node
/**
 * Test autonome (Node >= 18, aucune dépendance) du téléchargement résilient.
 *
 * Reproduit le scénario de production (incident export 12,6 Mo / 27 Mo) :
 * un serveur HTTP Range-compliant coupe BRUTALEMENT le socket TCP en pleine
 * tranche. Le client (resilient-download.js) doit reprendre la tranche et
 * livrer un fichier strictement intègre.
 *
 * Usage : node frontend/tests/test_resilient_download.mjs
 */
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// --- Chargement du module frontend (script à globales, pas un module ESM) ---
const modulePath = join(
    dirname(fileURLToPath(import.meta.url)),
    '..', 'public', 'js', 'modules', 'resilient-download.js'
);
globalThis.escapeHtml = s => String(s);
(0, eval)(readFileSync(modulePath, 'utf8'));
if (typeof resilientDownload !== 'function') {
    throw new Error('resilientDownload introuvable après chargement du module');
}

const TOTAL = 27_208_352;             // taille exacte du backup de l'incident
const CHUNK = 4 * 1024 * 1024;        // même taille de tranche que le module

// Motif déterministe pour vérifier l'intégrité octet par octet.
const payload = Buffer.alloc(TOTAL);
for (let i = 0; i < TOTAL; i++) payload[i] = (i * 31 + 7) % 251;

let requests = 0;
let cuts = 0;

const server = http.createServer((req, res) => {
    requests++;
    const range = /^bytes=(\d+)-(\d+)$/.exec(req.headers.range || '');
    if (!range) {
        // Le client résilient envoie toujours un Range ; 200 = repli serveur sans Range.
        res.writeHead(200, {
            'Content-Type': 'application/octet-stream',
            'Content-Length': TOTAL
        });
        return res.end(payload);
    }
    const start = parseInt(range[1], 10);
    const end = parseInt(range[2], 10);
    res.writeHead(206, {
        'Content-Type': 'application/octet-stream',
        'Content-Range': `bytes ${start}-${end}/${TOTAL}`,
        'Content-Length': end - start + 1
    });
    const slice = payload.subarray(start, end + 1);

    // Simulateur de coupure : sur la 4e requête, envoyer la moitié de la
    // tranche puis détruire le socket (équivalent du « Failed to fetch »
    // constaté à 12,6 Mo / 27 Mo en production).
    if (requests === 4 && cuts === 0) {
        cuts++;
        res.write(slice.subarray(0, Math.floor(slice.length / 2)));
        setTimeout(() => res.socket.destroy(), 10);
        return;
    }
    res.end(slice);
});

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;

try {
    const blob = await resilientDownload(`http://127.0.0.1:${port}/download`, {}, { label: 'test' });
    if (blob.size !== TOTAL) {
        throw new Error(`Taille finale ${blob.size} != ${TOTAL}`);
    }
    const buf = Buffer.from(await blob.arrayBuffer());
    if (!buf.equals(payload)) {
        throw new Error('Intégrité rompue : le fichier reconstitué diffère de l\'original');
    }
    if (cuts !== 1) {
        throw new Error('Test invalide : la coupure simulée n\'a pas été déclenchée');
    }
    const expectedRequests = Math.ceil(TOTAL / CHUNK) + 1; // tranches + 1 reprise
    console.log(`OK : ${TOTAL} octets intègres | ${requests} requêtes (attendu ~${expectedRequests}) | ${cuts} coupure TCP subie puis reprise`);
    process.exitCode = 0;
} catch (e) {
    console.error('ÉCHEC :', e.message);
    process.exitCode = 1;
} finally {
    server.close();
}
