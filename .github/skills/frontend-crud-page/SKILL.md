---
name: frontend-crud-page
description: 'Build or update a Hub static frontend admin view: plain JavaScript list/detail/create/edit/delete flows, HubAPI wrapper methods, Bootstrap modals/tables/forms, pagination/filter state, SSE refresh behavior, and CSS in frontend/public. Use for adding or fixing an admin page, table, modal, or resource workflow.'
argument-hint: 'UI scope, for example: app admin table, users modal, messages list, backups panel, filters, pagination, or action buttons'
---

# Hub Static Frontend Page

Use this skill when the task is to add or rework a frontend page or workflow in the Hub's static admin interface.

This skill covers the frontend only. For backend endpoints, use the prompt [/add-backend-endpoint](../../prompts/add-backend-endpoint.prompt.md).

## What This Repository Uses

- API wrapper: `HubAPI` in [frontend/public/js/api.js](../../../frontend/public/js/api.js).
- UI logic and page state: [frontend/public/js/app.js](../../../frontend/public/js/app.js).
- Markup shell and modals: [frontend/public/index.html](../../../frontend/public/index.html).
- Styling: [frontend/public/css/style.css](../../../frontend/public/css/style.css).
- Nginx proxy for browser API calls and SSE: [frontend/nginx.conf](../../../frontend/nginx.conf).

## Procedure

1. Define the backend contract.
   - Confirm the endpoint path, auth requirement, request body, response shape, and side effects.
   - Add a method to `HubAPI` before using the endpoint from UI code.

2. Implement read state.
   - Store page-level data in existing global state patterns in [frontend/public/js/app.js](../../../frontend/public/js/app.js).
   - Keep filters, page numbers, selected ids, and loading/error states explicit.
   - Use existing toast/modal helpers and keep DOM updates idempotent.

3. Implement writes.
   - Disable submit/action buttons while the request is in flight.
   - On success, update or reload the affected list and show a concise toast.
   - Preserve server-side validation messages surfaced through `HubAPI.request`.

4. Wire markup and styling.
   - Use Bootstrap components and Bootstrap Icons already loaded by [frontend/public/index.html](../../../frontend/public/index.html).
   - Keep layout dense and administrative; avoid landing-page or decorative patterns.
   - Put reusable visual rules in [frontend/public/css/style.css](../../../frontend/public/css/style.css), not inline styles.

5. Keep realtime behavior coherent.
   - If the resource is affected by SSE events, update `handleSSEEvent` and the appropriate debounce path.
   - Do not open extra EventSource connections for normal admin resources; the Hub already has a central SSE stream.

6. Validate.
   - Rebuild/restart the frontend container with `docker compose up -d --build hub-frontend` for static asset changes.
   - Exercise the screen in a browser at the configured Hub frontend port.

## Anti-Patterns To Avoid

- Adding React, Vite, TanStack Query, or a second HTTP client for a local change.
- Direct `fetch` calls in UI code when a `HubAPI` method should exist.
- Hardcoded hosts or ports in browser code; use relative `/api` paths.
- Updating tables after mutations without refreshing affected state.
- Calling M2M-only endpoints from the browser.

## Guardrails

- Respect current auth visibility: hide or disable admin actions for non-admin users, but keep backend enforcement authoritative.
- For Blob URL downloads, delay `URL.revokeObjectURL` after click so the browser can start the download.
- Keep text and controls responsive in the existing static layout; test both narrow and desktop widths for admin tables and modals.
