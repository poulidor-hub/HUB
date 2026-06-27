/**
 * Hub Application - Global event delegation
 *
 * Remplace les handlers HTML inline (onclick, onsubmit, onchange, oninput)
 * par un système de delegation via attributs data-action/data-args.
 *
 * Usage dans le HTML :
 *   onclick="showPage('dashboard')" → data-action="showPage" data-args="dashboard"
 *   onclick="toggleMsgChip(this)"    → data-action="toggleMsgChip" data-args="$el"
 *   onsubmit="handleLogin()"         → data-action="handleLogin" sur le <form>
 *   onchange="filterApps()"          → data-action="filterApps" sur le <select>
 *   oninput="searchTable()"          → data-action="searchTable" sur le <input>
 *
 * Convention data-args : arguments séparés par "|", $el = l'élément cliqué
 */
(function() {
    function parseArgs(target, argsStr, event) {
        if (!argsStr) return [];
        return argsStr.split('|').map(function(a) {
            if (a === '$el') return target;
            if (a === 'event' || a === '$event') return event;
            return a;
        });
    }

    document.addEventListener('click', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;
        if (target.tagName === 'FORM') return;
        var fnName = target.getAttribute('data-action');
        if (fnName === 'onClickExpr') {
            var expr = target.getAttribute('data-args') || '';
            if (expr) {
                var args = expr.split('|').map(function(a) { return a === '$el' ? target : a; });
                var fn = window[args[0]];
                if (typeof fn === 'function') {
                    fn.apply(window, args.slice(1));
                }
            }
            return;
        }
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
        if (fnName === 'onChange') {
            var expr = target.getAttribute('data-args') || '';
            if (expr) {
                try { (new Function(expr))(); } catch(ex) { console.error('onChange error:', ex); }
            }
            return;
        }
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
