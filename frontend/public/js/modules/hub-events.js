// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

/**
 * Hub Application - Bus d'événements pub/sub
 *
 * Petit dispatcher pour découpler progressivement les modules métier
 * (SSE, dashboard, admin, etc.) sans imposer de framework.
 *
 * Usage:
 *   HubEvents.on('apps:updated', (payload) => { ... });
 *   HubEvents.emit('apps:updated', { source: 'sse' });
 *   HubEvents.off('apps:updated', handler);
 *
 * Les handlers sont exécutés de façon synchrone, dans l'ordre d'enregistrement.
 * Une exception dans un handler n'empêche pas l'exécution des suivants.
 */
const HubEvents = (() => {
    const listeners = new Map(); // event -> Set<handler>

    function on(event, handler) {
        if (typeof handler !== 'function') return () => {};
        if (!listeners.has(event)) listeners.set(event, new Set());
        listeners.get(event).add(handler);
        return () => off(event, handler);
    }

    function off(event, handler) {
        const set = listeners.get(event);
        if (!set) return;
        set.delete(handler);
        if (set.size === 0) listeners.delete(event);
    }

    function emit(event, payload) {
        const set = listeners.get(event);
        if (!set || set.size === 0) return;
        // Copie défensive : un handler peut s'auto-désabonner.
        for (const handler of Array.from(set)) {
            try {
                handler(payload);
            } catch (err) {
                console.error(`[HubEvents] handler error for "${event}":`, err);
            }
        }
    }

    function clear(event) {
        if (event === undefined) {
            listeners.clear();
        } else {
            listeners.delete(event);
        }
    }

    return { on, off, emit, clear };
})();

// Exposer globalement pour les scripts classiques.
window.HubEvents = HubEvents;
