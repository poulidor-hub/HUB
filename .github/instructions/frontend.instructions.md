---
description: "Use when editing the Hub static frontend, plain JavaScript API client, dashboard UI, Bootstrap styling, nginx frontend proxy, or SSE behavior."
name: "Hub Frontend Guidelines"
applyTo:
  - "frontend/public/**/*.html"
  - "frontend/public/**/*.css"
  - "frontend/public/**/*.js"
  - "frontend/nginx.conf"
  - "frontend/docker-entrypoint.sh"
---

# Hub Frontend Guidelines

- The Hub frontend is a static HTML/CSS/JavaScript app served by nginx, not React, Vite, or TanStack Query. Keep changes in [frontend/public/index.html](../../frontend/public/index.html), [frontend/public/js/api.js](../../frontend/public/js/api.js), [frontend/public/js/app.js](../../frontend/public/js/app.js), and [frontend/public/css/style.css](../../frontend/public/css/style.css).
- Keep API access centralized in `HubAPI` in [frontend/public/js/api.js](../../frontend/public/js/api.js). Add or reuse wrapper methods there before adding direct `fetch` calls in UI logic.
- Use relative `/api` paths. [frontend/nginx.conf](../../frontend/nginx.conf) proxies `/api/` to `hub-api:8000`; do not hardcode localhost, container IPs, or external Hub URLs in browser code.
- Preserve token handling in `HubAPI`: access token storage, refresh token rotation, proactive refresh, session-expired events, and `ensureFreshToken(appSlug)` before opening registered apps.
- SSE is part of the product contract. Keep `/api/events/stream`, reconnect behavior, beacon token renewal, event debouncing, and handlers for `apps_updated`, `user_permissions_changed`, `force_logout`, `messages_updated`, and `user_message` coherent with the backend.
- Match the current UI stack: Bootstrap, Bootstrap Icons, plain DOM functions, global page state, and small helper functions. Avoid adding a frontend framework unless the task explicitly asks for a migration.
- For downloads created with Blob URLs, delay `URL.revokeObjectURL` after `a.click()` so browsers have time to start the download.
- Validate non-trivial frontend changes by rebuilding/restarting the frontend container with `docker compose up -d --build hub-frontend` and checking the affected screen in the browser. There is no `npm run build` step in this repository.