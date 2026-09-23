# Frontend reuse assessment

## Scope and provenance

This assessment covers the frontend of the original Quayt repository at commit
`6202459d4f14e73109ff40d1fb81c0ade6f2760a` (committed 2026-06-23). The source was
inspected read-only from `/tmp/quayt-review.XljQ9v`; no implementation was copied.

The target repository is still a discovery scaffold. Its `clatch.json` declares protocol
2 and no commands or signals, and its own README deliberately leaves identity, commands,
signals, and implementation structure undecided. This report therefore recommends
boundaries and contract needs. It does not define backend authority or a final CLI.

The target must follow ClappKit's documented shape:

- one executable with a human window and an agent CLI;
- one Rust core holding shared client state and logic for both surfaces;
- a private GUI-to-CLI channel separate from the Clatch control pipe;
- CLI help and manifest commands kept exactly aligned;
- signals as notices only, with durable detail read through the CLI;
- signals emitted only for human actions;
- secrets absent from snapshots, CLI output, logs, and chat;
- authentication for a `clapp:app` owned by its window, not exposed as CLI login verbs.

The original repository confirms a server-rendered browser application rather than a
portable desktop frontend. The inspected tree contains 93 Jinja templates (40,940 lines),
10,559 lines of non-vendor static JavaScript, and 1,615 lines of shared CSS. Eighty-four
templates issue `fetch()` calls directly. There is no component or client-state boundary
that can be lifted into ClappKit intact.

## Executive decision

Reuse the Quayt identity, domain vocabulary, information architecture, role workflows,
form intent, and status presentation. Rebuild their implementation as a ClappKit desktop
client over an agreed versioned HTTPS/JSON contract.

Do not reuse the Jinja templates or global JavaScript as application code. They mix view
markup, same-origin navigation, authentication cookies, API calls, browser persistence,
and workflow state in page-sized scripts. Copying them would preserve browser-only
assumptions and create a second client state beside the Rust core.

Keep the existing Python/PostgreSQL system as the central operational authority. The
desktop core may cache presentation state and pending client intent, but it must not
decide roles, tenant scope, transition legality, prices, invoices, release eligibility,
or other server-owned rules. Hiding an action in the window is never authorization; the
service must authorize both GUI and CLI calls.

## Reuse classification

### Reuse directly

| Source area | Reusable material | Conditions and boundary |
|---|---|---|
| `app/static/img/icon.svg` | Current square Quayt crane/container mark and name treatment | Reuse as the brand master only if the PMs confirm it is the intended Quayt identity. Platform icon conversion, safe-area treatment, and packaging belong to the release workstream. |
| `docs/ARCHITECTURE.md`, `docs/QUAYT_REHBER.md` | Established Turkish domain terms and human-readable workflow names such as `Tahakkuk`, `Vezne`, `Puantor`, `Vardiya Amiri`, `Yard`, `Rıhtım`, `Sefer`, `Manifesto`, `CFS`, `Gate-In`, and `Gate-Out` | Reuse as product copy and discovery vocabulary. Runtime enum values and transition rules remain subject to the backend contract. |
| `app/static/i18n/tr.json`, `app/static/i18n/en.json` | Approved Turkish and English translations for stable product terms | Individual reviewed strings can be reused. Do not import the files wholesale: many entries contain HTML, route names, operational diagnostics, and browser-specific instructions. |

Direct reuse is intentionally narrow. The repository is MIT-licensed, but license
permission does not make browser-coupled code suitable for the new architecture. The
origin and usage rights of `app/static/img/hero-vessel.jpg` are not documented in the
repository, so it is excluded pending provenance.

### Adapt behind a new contract

| Source area | What to adapt | Required change |
|---|---|---|
| `app/templates/base.html`, `app/templates/hub.html` | Main information architecture: Saha, Rıhtım, Tarife & Finans, Rapor & Pano, and Operasyon Yönetimi, plus favorites and current-user context | Rebuild as a keyboard-accessible desktop shell. Populate visible workspaces and actions from server-returned capabilities. Store navigation preferences through the Rust core instead of browser `localStorage`. |
| `app/routes/ui.py`, `app/main.py` (`MENU_KEY_PREFIXES`, `check_menu_permission`) | Role-directed landing behavior and per-user menu selection | Replace route-prefix permissions with stable capability identifiers in the service contract. UI visibility remains presentation; every service mutation must still authorize tenant, actor, entity, and transition. |
| `app/models/user.py`, `app/routes/admin_users.py` | Roles `admin`, `operator`, `vardiya_amiri`, `muhasebe`, `veznedar`, `musteri`, `ust_yonetici`, plus the orthogonal `puantor_tipi` values `gemi`, `saha`, `her_ikisi` | Model these as server-provided actor/capability data. Do not encode a second role hierarchy in TypeScript or Rust. |
| `app/templates/dashboard.html`, `wallboard.html`, `vessel_call_wallboard.html`, `bi.html`, `bildirimler.html` | KPI groupings, attention queues, aging/revenue/operations summaries, live status, and wallboard density | Define typed read models with freshness and error metadata. Rebuild charts and tables for desktop resizing and non-visual summaries. |
| `app/templates/cfs.html`, `vardiya_amiri.html`, `planning.html`, `gate.html`, `yard.html`, `berth.html`, `equipment.html` | Saha, shift dispatch, planning, gate, yard, berth, and equipment workflows | Adapt each workflow only after backend-owned transition/action contracts are agreed. Mutations need idempotency, optimistic-version/conflict responses, and explicit forbidden/invalid-state errors. |
| `app/templates/vessel_calls.html`, `vessel_call_detail.html`, `_modals/vc_detail_modals.html`, `app/static/js/vc/*` | Sefer command center, stowage/BAPLIE, operations, work orders, services, mooring/pilot, laytime, KPI, and invoice context | Preserve the task grouping, not the 20-plus script-module implementation. Split it into focused desktop panels backed by one core entity snapshot. |
| `app/templates/services.html`, `service_detail.html`, `customers.html`, `customer_detail.html`, `calculations*.html`, `vezne.html`, `cari.html`, `aging.html`, `acente_fatura.html`, `teslim_kontrol.html` | Tariff, customer, contract, calculation, cashier, receivables, aging, invoicing, and release-control work | Use server-calculated monetary values, currency metadata, and allowed actions. The client formats values and gathers input; it does not recompute authoritative totals or release rules. |
| `app/templates/admin_users.html`, `tenant_setup_wizard.html`, `tanimlamalar.html`, `saas_admin.html`, `admin_compliance.html`, `integrations.html` | Tenant onboarding, user/capability assignment, master data, compliance, and integration status | Adapt to explicit administrative APIs with actor scope and auditable results. Capability assignment must use stable IDs rather than paths. |
| `app/static/i18n/tr.json`, `en.json`, `_i18n_bootstrap.html`, `app/services/i18n.py` | Turkish-default localization, English parity, parameterized messages, currency-aware copy | Convert reviewed strings into a typed client catalog. Ban executable/HTML translations; use components for emphasis and links. French and Chinese are placeholders only and must not be advertised as supported. |
| `app/static/css/style.css`, `app/templates/base.html` styles | Quayt palette (`#0A2540`, `#00D4D8`, slate/mist/status colors), cards, compact tables, status badges, and full-width operations layout | Convert into scoped desktop design tokens and components. Verify contrast, focus indication, forced colors, reduced motion, and narrow-window behavior before adoption. |
| `app/templates/login.html`, `parola_belirle.html`, `2fa_setup_required.html`, `guvenlik.html`, `forbidden.html`, `maintenance.html`, `offline.html` | Login, first-password, 2FA, security, forbidden, maintenance, connectivity, and retry states | Rebuild as explicit client state machines. Credentials and 2FA secrets enter only the window and secret storage path; snapshots and CLI expose only safe status. Server error codes must replace translated-string matching. |

### Retain only as behavioral reference

| Source area | Behavior worth retaining | Why the implementation is not reusable |
|---|---|---|
| `docs/QUAYT_REHBER.md`, design documents under `docs/`, and workflow tests under `tests/` | User journeys, state names, customer-type differences, expected empty/forbidden states, and regression scenarios | Documentation and tests contain useful product evidence but can lag current server rules. They are acceptance inputs, not an API contract. |
| `app/static/js/global_search.js` | `Cmd/Ctrl+K`, debounced cross-entity search, Escape to close, Enter to open the first result | It builds an unlabelled DOM overlay, manages state in module globals, and navigates browser URLs. Retain the interaction concept and rebuild accessible listbox/dialog behavior through shared state. |
| Keyboard block in `app/templates/base.html` | Shortcut help plus `Alt+S`, `Alt+N`, `Alt+C`, `Alt+H`, Escape, and input-field guards | Shortcut choices are useful evidence. Desktop accelerators need conflict review, discoverability, focus preservation, and CLI-aligned actions. The Co-Pilot toggle is not retained as a chat surface. |
| Toast/loading/input helpers in `app/templates/base.html` | Visible pending, success, warning, error, retry, and field-validation states | The helpers rely on global DOM mutation and toasts are not announced to assistive technology. Rebuild from typed operation state and structured errors. |
| `app/events.py`, EventSource use in templates, and live dashboard behavior | A remote event feed invalidates or refreshes affected entity views | SSE is an existing service behavior, not Clatch signaling. The Rust core should own reconnection, ordering, staleness, and snapshot updates. Remote updates must not emit Clatch signals. |
| `app/static/el_terminali_offline.js`, `app/static/sw.js` | Connectivity banner, queued field intent, retry count, and reconciliation need | IndexedDB, service workers, background sync, and browser cache versioning do not belong in the desktop core. These are strong requirements for the later mobile app. |
| `app/static/barcode_scanner.js` | Camera scanning, hardware scanners that type as keyboards, duplicate-scan suppression | `BarcodeDetector`, `getUserMedia`, and document key listeners are browser/device-specific. Retain as mobile and peripheral acceptance evidence. |
| `app/templates/report_view.html`, `bi.html`, `calculation_detail.html` | Chart, report, barcode, PDF, CSV, and spreadsheet presentation | Vendored Chart.js/JsBarcode files and `window.open` downloads should not be copied. Define export/media responses and render/download them through the desktop bridge and ClappKit media boundary. |
| `app/templates/portal.html` | Customer account, invoice approval/rejection, payment initiation, service request, vessel tracking, timeline, and self-service container filters | The 2,712-line page is a useful workflow inventory but combines many contracts and browser redirects in one global script. Split by task and target surface. |

### Discard from the desktop clapp

| Source area | Decision | Reason |
|---|---|---|
| All Jinja templates as executable UI, especially `app/templates/base.html` and page-inline scripts | Discard implementation | They depend on server rendering, cookies, relative URLs, global DOM IDs, browser navigation, and page reloads. |
| `app/templates/ai_copilot.html` and the floating Co-Pilot chat widget in `base.html` | Discard | ClappKit explicitly keeps conversation in Clatch; the clapp window must not become a second chat surface. AI-derived operational results may appear in normal domain views if the backend contract supplies them. |
| `landing.html`, `about.html`, `demo_request.html`, `pricing.html` | Discard from desktop | These are public marketing/acquisition pages. They belong on a public web property, not inside an authenticated operations desktop. |
| `app/templates/admin_system_update.html` | Discard and hand off | Application install/update/rollback presentation belongs to Clatch/release ownership. The frontend must not expose the original server's self-update mechanism as a desktop app update control. |
| `admin_backups.html`, `admin_js_errors.html`, `admin_outbox.html`, `admin_digest.html`, `admin_demurrage.html`, `admin_warehouse_cron.html`, `admin_copilot_debug.html` | Exclude from the first desktop product surface | These are service operations/debug consoles. If PMs later require an operations console, backend/release/security owners must define safe read/action contracts and scope. |
| `app/static/sw.js`, `app/static/manifest.json`, browser install prompts, Cache API behavior | Discard from desktop | Tauri/ClappKit packaging and lifecycle replace the PWA install/cache model. Mobile requirements are tracked separately below. |
| `app/static/js/vendor/chart-4.4.0.umd.min.js`, `jsbarcode-3.11.6.all.min.js` | Do not copy | Dependency selection is outside discovery, and bundled browser builds do not establish the future desktop dependency choice. |
| `app/static/img/hero-vessel.jpg` | Do not reuse yet | The repository does not document image provenance. |
| Google Fonts requests embedded in templates | Discard | A packaged desktop client must not depend on a third-party font request for basic rendering. |

## Information architecture and role workspaces

The original navigation groups are useful, but the new shell should present a focused
workspace for the signed-in actor rather than one mega-menu containing the whole product.
The service returns the actor, tenant, roles, capability IDs, and permitted actions; the
core derives a presentation model and both surfaces read it.

| Actor/workspace | Primary desktop tasks evidenced upstream | Recommended desktop shape |
|---|---|---|
| Administrator | Tenant setup, users, capability assignment, master data, integrations, compliance, full operational access | Setup/status home, scoped administration sections, and explicit switch into operational workspaces. Keep system-service/release consoles separate. |
| Operator | CFS/GYT, work orders, gate, yard, berth, equipment, manifests, vessel calls, stowage, planning, operational reports | Operations command center with attention queue, global search, active sefer context, and task-specific panels. |
| Vardiya amiri | View shift work, assign puantor/equipment, dispatch, approve/reject completed work | Shift board with assignment, conflicts, pending approvals, and a complete audit trail. |
| Puantor (`gemi`, `saha`, `her_ikisi`) | Start/pause/resume/complete work, tally, load/discharge, warehouse tasks | Do not make the handheld workflow a dense desktop workspace. Desktop provides supervisor/inspection views; the action-first experience belongs in mobile. |
| Muhasebe | Calculations, tahakkuk, invoices, current accounts, aging, reports, currency context | Finance workbench with server-calculated totals, exception queues, approvals, exports, and conflict-safe edits. |
| Veznedar | Lookup, collect, refund, third-party payment approval, cash report | Cashier workspace optimized for keyboard entry, explicit confirmation, receipt/export status, and reversible error handling where the service allows it. |
| Üst yönetici | Dashboard, wallboard, BI, high-level operational and financial indicators | Read-oriented executive workspace with drill-down, freshness timestamps, and accessible chart summaries. |
| Müşteri/acente | Own containers, calculations, invoices, approvals, payments, requests, vessel tracking | A limited self-service workspace may remain on desktop if PMs include this role. The frequent track/request/notification tasks should also be planned for mobile. |

The current route/menu mapping is useful evidence but is not a stable identity scheme.
Paths such as `/arrival` and `/work-orders` already redirect into `/cfs`, and `base.html`
contains compatibility bridges for those old permissions. The new contract should use
stable capability IDs unrelated to URLs or visual grouping.

## Mapping workflows to ClappKit

| Workflow concern | Human window | Shared Rust core | Agent CLI parity | Clatch signal behavior |
|---|---|---|---|---|
| Connection and auth | Collect login/2FA and show tenant/session status | Store secrets privately; snapshot only safe connection, actor, tenant, expiry, and capability status | Report safe status and actionable auth-required errors; never print credentials or 2FA material | No durable auth data in signals |
| Role workspace and navigation | Render only server-granted workspaces and actions | Own actor/capability snapshot and local preferences | List the same capabilities and available operations | None for passive navigation |
| Search, lists, and details | Search and inspect sefer, BL, container, customer, work order, invoice, and report data | Own query, filters, paging, selection, freshness, and structured failures | Equivalent search/list/show semantics against the same core; output limits must not repaginate shared state | None for reads |
| Workflow mutations | Forms and explicit confirmations for allowed transitions | Build typed requests, attach optimistic version/idempotency data, submit, and merge authoritative responses | Equivalent mutation through separately grantable command families; `-h` must document every declared verb | Only a human-triggered successful action may emit a declared notice; agent-originated writes never signal |
| Remote live updates | Show changed/stale/reconnecting states without losing the user's work | Own HTTPS/event-stream lifecycle and update the ordered snapshot | Reads immediately see the same updated state | Remote server events do not become Clatch signals |
| Ambiguity/conflict | Preserve the draft, show candidates or a conflict, and let the human choose | Represent ambiguity/conflict as state instead of guessing | Expose the same candidates and selection operation | A human resolution may emit a notice if PMs declare one |
| Exports/uploads/media | Choose files, show progress, open completed artifacts | Use ClappKit paths/media boundary; keep secrets and unsafe paths out of snapshots | Request/status/retrieve the same artifact through safe paths | Signal contains at most an entity/artifact reference, never file bytes or durable content |
| Errors | Inline field errors, page-level retry, stale data marker, forbidden state, and operation result | Normalize transport/auth/forbidden/validation/conflict/rate/service errors into stable typed state | Return non-zero with the same stable error meaning and useful stderr | `app.toAgentRefused` must be surfaced to the human when a user action tries to signal and fan-out is refused |

Exact CLI verbs should be designed only after the backend contracts and first desktop
scope are approved. The command families must be coarse enough to grant safely and broad
enough to avoid mirroring every button. Whatever is chosen must exist in all three places:
the Rust dispatcher, `<cli> -h`, and `clatch.json.connector.commands`.

## Localization findings

- Turkish and English are the only complete catalogs. Each currently contains 6,381
  scalar leaves; French and Chinese contain only five metadata leaves and explicitly say
  `coming_soon`.
- Ninety-two of 93 templates contain `data-i18n` markup, so localization is a genuine
  cross-product requirement.
- The current catalog includes route text, operational diagnostics, emoji, HTML fragments,
  and browser instructions. Several keys are auto-generated from filenames and source
  text. The new catalog needs stable semantic keys and typed placeholders.
- Currency is tenant-dependent. The current browser bootstraps a server-calculated local
  currency; the desktop should receive currency code/precision/display metadata with the
  relevant read model and never assume TRY.
- Language preference should live in shared client settings. A cookie plus cross-tab
  `BroadcastChannel` is a browser solution and should not be reproduced.

## Accessibility and keyboard findings

There are useful intentions in the original UI: real buttons are common, some forms use
labels and input modes, mobile CSS enforces 44 px primary hit targets, global search has
keyboard entry, and the shell documents shortcuts.

The implementation does not meet the accessibility bar for direct reuse:

- no inspected template uses `aria-live`, `aria-modal`, or `aria-expanded`;
- there is no `:focus-visible` styling or `prefers-reduced-motion` handling;
- search, shortcut help, and many page modals do not trap focus, restore focus, or expose
  dialog semantics;
- 42 clickable `div` occurrences and seven clickable table-row occurrences depend on
  pointer behavior;
- toasts, loading states, and dynamic results are not announced;
- many SVGs and emoji are visual-only without consistent accessible names;
- responsive table-to-card CSS depends on optional `data-label` values and is not a
  substitute for proper table semantics.

The desktop rewrite should require full keyboard reachability, predictable focus order,
Escape behavior, focus restoration, semantic dialogs/listboxes/tabs/tables, announced
status changes, reduced motion, high-contrast/forced-color support, zoom/reflow, and
platform shortcut review. Keyboard shortcuts must be driven from the same action registry
used by the GUI and CLI so help text cannot drift.

## Responsive desktop guidance

The current CSS establishes useful intent at 1024, 768, and 640 px and uses full-width
operations layouts. Reuse those scenarios as test cases, not as CSS:

- wide command-center layout for tables, planning, stowage, and dashboards;
- medium layout that preserves primary actions while collapsing secondary panels;
- narrow desktop window with single-column forms, accessible overflow, and no hidden
  authorization or destructive action;
- minimum-window handling for dialogs, tables, and charts;
- wallboard/presentation mode as a deliberate desktop state rather than browser
  `no-chrome` CSS.

## Screens for the later mobile app

The following source areas are mobile requirements and should not drive the initial
desktop architecture:

| Mobile area | Upstream evidence | Mobile responsibility |
|---|---|---|
| Handheld terminal | `el_terminali.html`, `el_terminali_v2.html`, `puantor.html` | Assigned work list; start/pause/resume/complete; tally; load/discharge; concise supervisor mode. |
| Scanning and peripherals | `app/static/barcode_scanner.js`, barcode fields in gate/warehouse/tally flows | Camera barcode/QR scanning, hardware scanner input, duplicate suppression, haptic feedback, device permission/error handling. |
| Offline field actions | `app/static/el_terminali_offline.js`, `app/static/sw.js`, `offline.html` | Durable encrypted queue, explicit pending/conflict status, idempotent replay, reconciliation, and user-controlled retry. The current automatic 30-second replay is only a behavioral prototype. |
| Warehouse and gate capture | `warehouse.html`, `app/static/js/warehouse.js`, `gate.html`, `saha_load.html`, `saha_discharge.html` | Receipt/delivery/gate checks, photos, seal/damage capture, field-friendly validation, and intermittent-network handling. |
| Customer self-service | `track.html`, `public_request.html`, focused parts of `portal.html` | Container/BL tracking, service requests, invoice approval/rejection, payment handoff, vessel notifications, and request timeline. |
| Notifications | `bildirimler.html`, push handling in `app/static/sw.js` | Mobile push permission, deep links, action inbox, and safe redaction on lock screens. |

Planning, yard design, berth scheduling, stowage grids, finance reconciliation, bulk user
administration, report building, and tenant setup remain desktop-first. Executive KPI
views may later gain a read-only mobile companion, but wallboards belong on large displays.

## Browser-only assumptions to remove

- same-origin relative `fetch('/api/...')` and cookie authentication;
- Jinja-injected JSON, currency, IDs, and route state;
- `window.Quayt`, global functions, global DOM IDs, inline event handlers, and `innerHTML`;
- browser history and path redirects as workflow/navigation state;
- `localStorage` for favorites and view settings, cookies for language, and
  `BroadcastChannel` for cross-tab sync;
- service worker, Cache API, IndexedDB, Background Sync, Web Push, and PWA manifest;
- `navigator.onLine`, `sendBeacon`, `window.open`, hidden download links, and browser PDF
  tabs;
- `BarcodeDetector`, `getUserMedia`, vibration, and scanner-wide document key listeners;
- externally hosted Google Fonts;
- direct EventSource lifecycle in individual screens;
- page-level scripts independently loading and mutating the same domain entities.

Where a capability remains relevant, the Rust core or an explicit bridge owns it and
publishes safe, serializable state. The React/TypeScript window should call the core only
through the ClappKit bridge shape.

## Contract handoffs required before implementation

### Backend

Provide versioned HTTPS/JSON contracts for:

- authentication, 2FA, session expiry/revocation, and safe reconnect behavior;
- actor, tenant, role/capability IDs, allowed entity actions, and permission-denied errors;
- search, list paging/filter/sort, entity details, freshness/revision fields, and event-feed
  invalidation;
- every retained workflow transition, including optimistic conflict and idempotency rules;
- monetary values, currency metadata, rounding, invoice/release decisions, and exports;
- upload/download/media constraints;
- stable structured errors independent of translated prose.

The frontend will not infer these rules from legacy templates or endpoints.

### Security

Define desktop secret storage, session/token lifecycle, tenant-bound request proof,
redaction rules, safe snapshot fields, local cache sensitivity, and mobile offline-data
requirements. The legacy cookie, IndexedDB, and service-worker behaviors are not accepted
as the new security model.

### QA

Use the legacy workflows and tests as scenario seeds, then add desktop-specific gates for
keyboard-only use, focus management, screen-reader announcements, color/forced-color
behavior, zoom/reflow, narrow windows, stale/conflict handling, GUI/CLI parity, and
permission-negative behavior.

### Release

Confirm the Quayt brand asset, produce required platform icon formats, and own packaging,
installation, update, and rollback presentation. The legacy `admin_system_update.html`
must not cross into the frontend-owned desktop surface.

## Risks and recommendation

The largest risk is mistaking a broad, mature-looking browser UI for a reusable client.
Its 93 pages encode substantial product knowledge, but state and authority are distributed
across server-rendered HTML, page scripts, cookies, URL guards, and backend behavior. A
template-by-template port would create drift between the window, CLI, and service.

Proceed by selecting one complete role journey after the backend contract review, then
build its read models and mutations in the Rust core first. Expose the same operations to
the window and CLI, pin manifest/help parity, and add further workspaces only through that
shared seam. Use the original repository as an acceptance corpus throughout, not as a
code source.
