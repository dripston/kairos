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

## Deployed demo (GitHub Pages) and CI/CD

- **CI** (`.github/workflows/ci.yml`, every push and PR): backend `pytest` on Python 3.9 and 3.11 plus the
  red-team suite, the CRM tests, and frontend `npm run lint` + `npm run build`.
- **CD** (`.github/workflows/pages.yml`): when CI passes on `main`, the UI is built with `VITE_BASE=/kairos/`
  and published to **https://dripston.github.io/kairos/**. `404.html` is a copy of `index.html`, so deep
  links such as `/kairos/crm/B-1042` work.
- Pages is static hosting: it cannot run the Python API. By default the Pages build uses
  `VITE_KAIROS_API=none`, so every panel shows its built-in mock straight away, labelled
  `SIMULATED · offline mock`.
- **Connect the deployed UI to a backend:** open the page with `?api=https://your-backend` (remembered in
  that browser; `?api=none` resets), or set the repository variable `KAIROS_PUBLIC_API` and re-run the
  Pages workflow. `KAIROS_CAMPAIGN` (default `CMP-JOURNEY`) picks the campaign the live panels read.
  The API's CORS already allows any origin. The page is HTTPS, so the backend must be HTTPS too.

### Local demo with live data

```bash
cd backend
python -c "from replay.journey import run; run(seed=42, quick=True, echo=False, db_path='demo_seed.db', cohort_size=40)"
KAIROS_DB=demo_seed.db uvicorn kairos_backend.api:app --port 8000      # SIMULATED journey data
cd ../frontend && printf 'VITE_KAIROS_API=http://127.0.0.1:8000\nVITE_KAIROS_CAMPAIGN=CMP-JOURNEY\n' > .env.local && npm run dev
```

The Campaign wizard's "Upload CSV" now calls `POST /intake/preview` (K24: accepted, quarantined by reason,
budgeted loans, pool, first contact); offline it falls back to a local row count marked "not validated".
