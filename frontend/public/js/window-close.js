// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Minimal script pour les documents autonomes générés par document.write
 * (ex : page d'erreur de lancement d'app dans dashboard.js).
 * Ces documents héritent de la CSP du HUB (sans 'unsafe-inline') :
 * ils ne peuvent contenir ni handler inline ni script inline.
 * Chargé en 'self' depuis l'origine du HUB → autorisé par la CSP.
 */
(function () {
    function bind() {
        var btn = document.getElementById('closeWindowBtn');
        if (btn) btn.addEventListener('click', function () { window.close(); });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
