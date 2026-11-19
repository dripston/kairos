# Kairos demo UI (React + Vite)

```bash
npm install
npm run dev          # http://localhost:5173
npm run build && npm run lint
```

## Backend

The UI reads the Kairos API at `VITE_KAIROS_API` (default `http://127.0.0.1:8000`; set it in `.env.local`).
Start it from `backend/` with `uvicorn kairos_backend.api:app`. `integration/all` serves every route the UI uses.

Every panel calls the API first and falls back to the built-in mock (`src/lib/mock.js`) per call when the API is
unreachable or the route is missing. The badge on each panel says which: `SIMULATED · API` (backend data, still
SIMULATED until real adapters are wired), `REPLAY`, `LIVE SANDBOX`, or `SIMULATED · offline mock`.

| View | Endpoints |
|---|---|
| Header | `/health`, `/cap-breaches`, `/openapi.json` (CM-03 link when `/cm03/console` exists) |
| Control Room | `/audit` (decisions), `/events/since` (live feed, falls back to `/audit`), `/campaigns/{id}`, `/hitl/policy`, `/hitl/approvals` + approve/reject, `/demo/recipients` |
| Smart CRM borrower | `/borrowers/{id}`, `/audit?borrower_id=`, `/hitl/borrowers/{id}` + `/actions`, `/hitl/manual/preview`, `/voice/select`, `/voice/calls/{id}` |
| Learning & Proof | `/reports/daily-segment` (segments + K09 arms), optional `public/k09_replay_report.json` |
| Voice bots | `/voice/registry`, `/voice/select`, `/audit` + `/voice/calls/{id}` |
| Live journey | `POST /demo/journey/run`, else `public/journey/journey.json`, else the built-in SIMULATED script |

The role switch sends `X-Kairos-Role` (operator | supervisor | policy_owner). It is a demo switch, not auth.
Phone numbers are masked before they reach a page; demo-recipient numbers are never shown or logged in full.
