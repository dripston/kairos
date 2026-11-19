# Kairos

Self-learning omnichannel collections orchestrator (CreditNirvana Agentathon · CM-02 + CM-03).

```
kairos/
├── frontend/   React + Vite demo app — Control Room, Campaign Management, Smart CRM, Learning & Proof
├── backend/    Python — K06 Supervisor guardrails, atomic send ledger, executor, API
├── spine-replay/  Python — K03 account/loan spine + synthetic dataset, K15 safety/load/story replay
└── policy/     K01 outreach policy + K13 exceptions (Product-owned source of truth)
```

| Part | Start here |
|---|---|
| Frontend | [`frontend/`](frontend) — `npm install && npm run dev` |
| Backend | [`backend/README.md`](backend/README.md) — `pip install -e ".[dev]" && pytest` |
| Spine + replay | [`spine-replay/README.md`](spine-replay/README.md) — `cd spine-replay && python3 run_replay.py` (stdlib only) |
| Policy | [`policy/README.md`](policy/README.md) |
