---
name: hub-messages-integration
description: 'Implement or modify the Hub-side messaging contract: admin sends, app M2M sends via /api/messages/m2m/send, message sync/receipts for external apps, frontend notifications, SSE events, lifecycle stats, read/delivered/click tracking, and integration docs. Use for Hub notifications, user messages, M2M message sync, SSE delivery, or exemple-app support.'
argument-hint: 'Scope of the messaging work, for example: M2M send, sync endpoint, read receipts, frontend banner, admin archive, SSE event, or NotifCenter support'
---

# Hub Messages Integration

Use this skill when the task involves the Hub messaging system itself: storing messages, sending from admins or apps, delivering via SSE, acknowledging reads/clicks, or supporting external synchronization such as exemple-app.

## Architecture Recap

- The Hub is the source of truth for user messages in `user_messages` and `message_read_receipts` in [hub/app/models.py](../../../hub/app/models.py).
- Backend routes live in [hub/app/routes/messages.py](../../../hub/app/routes/messages.py), with realtime delivery helpers in [hub/app/routes/events.py](../../../hub/app/routes/events.py).
- Message schemas live in [hub/app/schemas.py](../../../hub/app/schemas.py).
- The static frontend receives and displays messages through [frontend/public/js/api.js](../../../frontend/public/js/api.js), [frontend/public/js/app.js](../../../frontend/public/js/app.js), and [frontend/public/css/style.css](../../../frontend/public/css/style.css).
- The external notification-center concept is documented in [exemple-app.md](../../../exemple-app.md).

## Procedure

1. Decide the scope.
   - Admin send/list/archive, user inbox/read/click, M2M app send, M2M sync/receipts/stats, SSE delivery, retention/purge, or frontend UI.

2. Backend message storage.
   - Preserve sender fields: `sender_type`, `sender_id`, and `sender_app_slug`.
   - Preserve target fields: `target_type`, `target_user_id`, `target_username`, and broadcast accounting.
   - Keep counters (`delivered_count`, `read_count`, `click_count`, `total_targets`) consistent when changing lifecycle behavior.

3. M2M send path.
   - `POST /api/messages/m2m/send` is service-account only.
   - Resolve source app from `X-Slug` first, then the service account's associated application.
   - Resolve relative `action_url` values against the sender app's `frontend_url` so notifications open the originating app correctly.
   - Emit targeted `user_message` SSE and broadcast `messages_updated` after successful sends.

4. Synchronization path.
   - `/api/messages/m2m/sync`, `/api/messages/m2m/receipts`, `/api/messages/m2m/stats`, and `/api/messages/m2m/{message_id}` are for service accounts that mirror Hub message history.
   - Keep pagination, `since` parsing, status fields, resend metadata, receipt details, and usernames stable for external consumers.

5. Frontend delivery path.
   - Use the existing Hub SSE stream `/api/events/stream`; do not create a second stream for messages.
   - Update `HubAPI` methods and `handleSSEEvent` in [frontend/public/js/app.js](../../../frontend/public/js/app.js) when backend behavior changes.
   - Preserve read-all, per-message read, delivered, and click calls so backend metrics stay meaningful.

6. Tests and docs.
   - Add or update focused coverage in [hub/tests/test_messages_lifecycle.py](../../../hub/tests/test_messages_lifecycle.py), [hub/tests/test_m2m_action_url.py](../../../hub/tests/test_m2m_action_url.py), or SSE tests as appropriate.
   - Update [exemple-app.md](../../../exemple-app.md) when external sync contracts change, and [INTEGRATION.md](../../../INTEGRATION.md) when app-facing message behavior changes.

## Guardrails

- Do not allow browser code to call M2M endpoints; service-account tokens are backend-only.
- Do not store message history in an external app as the primary source of truth unless explicitly designing a mirror/archive; the Hub owns delivery state.
- Do not strip SSE proxy settings in [frontend/nginx.conf](../../../frontend/nginx.conf); buffering breaks realtime delivery.
- Keep app slugs stable in message payloads because they connect notifications, app URLs, audit logs, and external sync.
