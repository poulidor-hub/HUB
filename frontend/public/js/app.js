/**
 * Hub Application - Global event delegation
 *
 * Remplace les handlers HTML inline (onclick, onsubmit, onchange, oninput)
 * par un système de delegation via attributs data-action.
 *
 * Usage dans le HTML :
 *   onclick="showPage('dashboard')"  → data-action="showPage" data-args="dashboard"
 *   onclick="toggleMsgChip(this)"    → data-action="toggleMsgChip" data-args="$el"
 *   onsubmit="handleLogin()"         → data-action="handleLogin" sur le <form>
 *   onchange="filterAuditLogs()"     → data-action="loadAuditPageLogsTab" sur le <select>
 *   oninput="filterUsers()"          → data-action="debounceMsgSearch" sur le <input>
 *
 * Convention data-args : arguments séparés par "|", $el = l'élément cliqué,
 * event = l'objet Event. Réservé aux valeurs statiques sans "|".
 *
 * Convention data-arg1..data-arg9 : arguments positionnels, un par attribut.
 * Valeurs brutes sans split ni évaluation — obligatoire pour les contenus
 * dynamiques (template literals) pouvant contenir "|" ou des quotes.
 * Mêmes keywords $el / event reconnus.
 */
(function() {
    function parseArgs(target, argsStr, event) {
        var resolve = function(a) {
            if (a === '$el') return target;
            if (a === 'event' || a === '$event') return event;
            return a;
        };
        // Attributs positionnels data-arg1..data-arg9 : prioritaires sur data-args
        var positional = [];
        for (var i = 1; i <= 9; i++) {
            var v = target.getAttribute('data-arg' + i);
            if (v === null || v === undefined) break;
            positional.push(v);
        }
        if (positional.length > 0) return positional.map(resolve);
        if (!argsStr) return [];
        return argsStr.split('|').map(resolve);
    }

    document.addEventListener('click', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;
        if (target.tagName === 'FORM') return;
        var fnName = target.getAttribute('data-action');
        var fn = window[fnName];
        if (typeof fn !== 'function') return;
        e.preventDefault();
        var args = parseArgs(target, target.getAttribute('data-args') || '', e);
        fn.apply(window, args);
    });

    document.addEventListener('submit', function(e) {
        var form = e.target.closest('form[data-action]');
        if (!form) return;
        var fnName = form.getAttribute('data-action');
        var fn = window[fnName];
        if (typeof fn !== 'function') return;
        e.preventDefault();
        var args = parseArgs(form, form.getAttribute('data-args') || '', e);
        fn.apply(window, args);
    });

    document.addEventListener('change', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;
        if (target.tagName === 'FORM') return;
        var fnName = target.getAttribute('data-action');
        var fn = window[fnName];
        if (typeof fn !== 'function') return;
        var args = parseArgs(target, target.getAttribute('data-args') || '', e);
        fn.apply(window, args);
    });

    document.addEventListener('input', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;
        if (target.tagName === 'FORM') return;
        var fnName = target.getAttribute('data-action');
        var fn = window[fnName];
        if (typeof fn !== 'function') return;
        var args = parseArgs(target, target.getAttribute('data-args') || '', e);
        fn.apply(window, args);
    });

    document.addEventListener('keydown', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;
        var fnName = target.getAttribute('data-action');
        if (fnName !== 'onKeydown') return;
        var fn = window[target.getAttribute('data-args')];
        if (typeof fn !== 'function') return;
        fn(e);
    });
})();
