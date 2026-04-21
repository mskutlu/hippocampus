---
date: 2026-04-20
status: draft
version: 1.2.0
owner: anon
---

# PRD — Hippocampus V1.2: Web UI

## 1. Introduction / Overview

The CLI and rules-file injection are enough for day-to-day AI work, but a
browser dashboard makes long-term ownership easier: spot decayed fragments,
browse associations, audit the feedback log, tweak settings, and manually
curate memories without shelling out to `hippo`.

V1.2 adds a local web UI served by a FastAPI backend. Local only,
single-user, no auth. Runs on `127.0.0.1:7878` by default.

## 2. Goals

- **G1** Browse: fragments table, per-fragment detail, stats, ledger, associations, feedback log.
- **G2** Mutate: pin / unpin, forget (negative feedback), delete, add a new fragment, log progress.
- **G3** Inspect: hybrid recall scores, decay timeline, settings.
- **G4** Launch from CLI: `hippo web` starts the server.
- **G5** Zero build step — single static `index.html` bundled with vanilla JS.
- **G6** Fast — uses existing tool functions, no separate code paths.

## 3. Functional Requirements

### 3.1 Backend (FastAPI)

1. **FR-1** `GET /api/stats` — returns `tools.get_stats()` payload.
2. **FR-2** `GET /api/fragments` — list with `?tag=&min_confidence=&limit=`.
3. **FR-3** `GET /api/fragments/{id}` — full fragment + associations (no boost).
4. **FR-4** `POST /api/fragments` — body `{content, summary?, tags?, source_type?, pinned?}` → `remember()`.
5. **FR-5** `POST /api/fragments/{id}/pin` / `/unpin`.
6. **FR-6** `POST /api/fragments/{id}/forget` — body `{reason?}`.
7. **FR-7** `DELETE /api/fragments/{id}` — hard delete.
8. **FR-8** `POST /api/recall` — body `{query, limit?, min_confidence?, context_tag?}` (returns scored hits, does boost).
9. **FR-9** `GET /api/top?limit=` — top-N for the injection block.
10. **FR-10** `GET /api/progress?client=` — current ledger.
11. **FR-11** `POST /api/progress` — body `{kind, content, details?, client?}`.
12. **FR-12** `POST /api/progress/end` — body `{client?, distill?, summary?, tags?}`.
13. **FR-13** `POST /api/progress/undo` — body `{client?}`.
14. **FR-14** `GET /api/embeddings/stats` — `search.stats()`.
15. **FR-15** `POST /api/embeddings/reindex` — body `{force?, batch?}` → `reindex()`.
16. **FR-16** `GET /api/config` / `POST /api/config` — read / update user settings.
17. **FR-17** `GET /api/feedback?limit=` — recent feedback events.
18. **FR-18** `GET /api/associations/{fragment_id}` — neighbours.
19. **FR-19** CORS is DISABLED — the server binds to `127.0.0.1` and serves the SPA from the same origin, so no CORS is needed.

### 3.2 Frontend (vanilla HTML + JS)

20. **FR-20** Single `index.html` served from `/`. No build, no npm.
21. **FR-21** Tabs: **Dashboard** / **Fragments** / **Working Memory** / **Feedback** / **Settings**.
22. **FR-22** Dashboard: counts, average confidence, top-N preview, recent feedback.
23. **FR-23** Fragments: sortable table, quick-filter by tag, click → detail drawer with actions (pin/unpin, forget, delete, copy id).
24. **FR-24** Working Memory: per-client session picker + live ledger; input form to `log_progress`.
25. **FR-25** Feedback: table of recent events.
26. **FR-26** Settings: editable form for `working_block_mode`, `auto_end_idle_minutes`, `semantic_weight`, `embedding_provider`, `embedding_model`.
27. **FR-27** Dark-first styling, minimal CSS, readable without JS frameworks.
28. **FR-28** Page auto-refreshes stats every 30 seconds.

### 3.3 CLI

29. **FR-29** `hippo web [--host 127.0.0.1] [--port 7878] [--no-browser]` starts the server. Without `--no-browser`, opens the default browser.
30. **FR-30** The server is a separate optional extra: `pip install -e '.[web]'` pulls in fastapi + uvicorn. Without it, `hippo web` prints an install hint.

### 3.4 Observability / safety

31. **FR-31** The server logs every request to `~/.hippocampus/logs/web-<date>.log`.
32. **FR-32** Mutations require a simple CSRF-style token: server generates a random token on startup, exposes it at `/api/csrf` (same-origin only), the UI reads it and sends it in the `X-Hippo-Token` header on POST/DELETE. Because the server binds to localhost this is defence-in-depth only — it prevents a drive-by browser tab on the same machine from hitting the API.
33. **FR-33** Bind to loopback by default. `--host 0.0.0.0` is allowed but prints a prominent warning.

## 4. Non-Goals

- **N-1** Multi-user auth (single-user local tool).
- **N-2** SSE / WebSocket live updates — polling every 30s is fine.
- **N-3** Graphical associations viewer (d3, cytoscape) — table is enough for V1.2.
- **N-4** Hosting outside localhost — no TLS, no production config.

## 5. Technical Considerations

- FastAPI + uvicorn. `fastapi[standard]` pulls enough batteries; we keep
  the install slim by using the non-standard extras list.
- Static files served via FastAPI `StaticFiles` mount.
- Frontend uses `fetch()` directly; no bundler. Template literals for HTML.

## 6. Success Metrics

- **M-1** Dashboard loads and shows accurate stats.
- **M-2** Can add a fragment, pin it, forget it, see decay applied, all from the browser.
- **M-3** Working-memory tab: log entries appear immediately; the block auto-updates.
- **M-4** Settings persist and take effect on the next operation.
