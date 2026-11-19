# Kairos

Self-learning omnichannel collections orchestrator (CreditNirvana Agentathon · CM-02 + CM-03).

```
kairos/
├── frontend/   React + Vite demo app — Control Room, Campaign Management, Smart CRM, Learning & Proof
├── backend/    Python — K03 account/loan spine, K15 replay, K06 guardrails + ledger + executor + API,
│               K09 learner + outcomes + intent, K09 replay, K17/K18 voice engine,
│               K06 HITL, K26 reports, K07/K08 channels, K22 templates, red-team suite
├── crm/        Python — K05/K14 borrower page + shared event ingestion + replay, K17/K18 Sarvam IVR/voicebot
└── policy/     K01 outreach policy + K13 exceptions (Product-owned source of truth)
```

| Part | Start here |
|---|---|
| Frontend | [`frontend/`](frontend) — `npm install && npm run dev` |
| Backend | [`backend/README.md`](backend/README.md) — `pip install -e ".[dev]" && pytest` |
| CRM + voice | [`crm/README.md`](crm/README.md) — `cd crm && python3 -m kairos.web` (stdlib only) |
| Policy | [`policy/README.md`](policy/README.md) |
