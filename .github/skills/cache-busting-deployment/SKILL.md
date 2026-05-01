---
name: cache-busting-deployment
description: 'Configure, verify, or troubleshoot Hub static frontend cache-busting: nginx no-store HTML, cache-safe fixed JS/CSS assets, optional hashed assets, service-worker absence, stale frontend, old CSS/JS after deploy, or browser cache reports.'
argument-hint: 'Cache/deployment task, for example: fix stale Hub frontend after deploy, verify nginx cache headers, bump static asset URLs'
---

# Hub Static Frontend Cache-Busting Protocol

Use this skill whenever the task is to configure, deploy, or debug browser caching for this Hub's static frontend. The goal is simple: users must never stay stuck on an old HTML, CSS, or JavaScript file after a deploy.

This repository does not use React, Vite, npm, or generated `dist/` assets. The frontend is plain static HTML/CSS/JavaScript under [frontend/public](../../../frontend/public), copied into the nginx image by [frontend/Dockerfile](../../../frontend/Dockerfile), and served through [frontend/nginx.conf](../../../frontend/nginx.conf). Any deployment or caching work must verify all four checks below.

## The Four Required Checks

1. Asset cache identity.
   - Current Hub assets have fixed paths such as `css/style.css`, `js/api.js`, and `js/app.js`.
   - Do not give immutable long-lived caching to fixed filenames.
   - If a future build step introduces content-hashed filenames, only those hashed files may use immutable one-year caching.
   - For fixed frontend assets, bump query-string versions in [frontend/public/index.html](../../../frontend/public/index.html) when needed to break already-cached browser entries.

2. Strict HTML cache control.
   - Apply these headers to `index.html` and any other HTML entry points:
     - `Cache-Control: no-cache, no-store, must-revalidate`
     - `Pragma: no-cache`
     - `Expires: 0`
   - This is the critical protection against browsers serving an outdated app shell.

3. Aggressive caching for hashed assets.
   - Apply these headers to hashed JS, CSS, fonts, and images:
     - `Cache-Control: public, max-age=31536000, immutable`
   - Only do this for assets whose filenames change when content changes.
   - In the current static Hub, fixed JS/CSS/image/font files should be served with no-store/no-cache or must-revalidate headers instead.

4. Service worker invalidation.
   - If the app uses a service worker or PWA features, add an explicit update strategy so an older worker cannot pin users to stale assets.
   - At minimum, check for updates on load and activate the new worker promptly (`skipWaiting` / `clientsClaim` or equivalent for the stack in use).
   - If no service worker exists, record that this check is not applicable rather than ignoring it silently.

## What This Repository Uses

- Frontend source: [frontend/public/index.html](../../../frontend/public/index.html), [frontend/public/css/style.css](../../../frontend/public/css/style.css), [frontend/public/js/api.js](../../../frontend/public/js/api.js), and [frontend/public/js/app.js](../../../frontend/public/js/app.js).
- Frontend serving: [frontend/nginx.conf](../../../frontend/nginx.conf).
- Container packaging: [frontend/Dockerfile](../../../frontend/Dockerfile) copies [frontend/public](../../../frontend/public) directly into `/usr/share/nginx/html/`.
- Runtime bootstrap: [frontend/docker-entrypoint.sh](../../../frontend/docker-entrypoint.sh) starts nginx directly; it does not build or rewrite frontend assets.

## Procedure

1. Confirm asset strategy.
   - Check whether assets are still fixed filenames or whether a real hash-producing build step has been added.
   - For the current static setup, treat all local assets as mutable fixed URLs unless [frontend/public/index.html](../../../frontend/public/index.html) uses a bumped query string.

2. Set HTML entry points to never be cached.
   - In [frontend/nginx.conf](../../../frontend/nginx.conf), configure `index.html` (and any future HTML entry points) with:

```nginx
add_header Cache-Control "no-cache, no-store, must-revalidate" always;
add_header Pragma "no-cache" always;
add_header Expires "0" always;
```

3. Cache only hashed assets aggressively.
   - If hashed assets exist, use long-lived immutable caching only for those hashed paths:

```nginx
add_header Cache-Control "public, max-age=31536000, immutable" always;
```

   - Do not apply `immutable` to fixed files like `style.css`, `api.js`, `app.js`, `favicon.svg`, or images without content hashes.

4. Keep fixed assets fresh.
   - Serve fixed local JS/CSS/images/fonts with strict no-store/no-cache headers, or add a reliable query-string version in [frontend/public/index.html](../../../frontend/public/index.html) and bump it on release.
   - If users already received long-lived immutable caching for a fixed file, changing the URL is required to force their browser to fetch the new file.

5. Check service-worker behavior.
   - Search the frontend for service worker registration, Workbox, PWA plugins, or `navigator.serviceWorker` usage.
   - If present, implement and verify an update path that refreshes clients onto the newest version.
   - If absent, state clearly that the repository currently has no service worker layer.

6. Validate before calling deployment done.
   - Rebuild/restart the frontend container with `docker compose up -d --build hub-frontend`.
   - Verify the server config applies strict HTML headers and safe fixed-asset headers.
   - Example header checks after the container is up: `curl -I http://localhost/index.html`, `curl -I http://localhost/css/style.css`, and `curl -I http://localhost/js/app.js`.
   - If a service worker exists, test an update flow instead of relying on a cold load only.

## Expected Outcome

After a deploy:

- the browser revalidates `index.html` immediately,
- fixed frontend assets are not pinned forever by browser cache,
- only truly hashed assets may stay cached safely,
- and any service worker update path does not trap the user on a previous release.

## Guardrails

- Never weaken HTML caching for convenience; stale `index.html` is the main cause of users seeing an old SPA after deploy.
- Never give `immutable` caching to non-hashed HTML entry points.
- Never give `immutable` caching to fixed local asset names in this Hub.
- When editing nginx rules, keep the existing `/api/` proxy and SSE behavior intact.
- If deployment changes touch frontend config or nginx config, finish with a frontend container rebuild and a header review rather than assuming defaults are still correct.
