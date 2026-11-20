# Kairos

Self-learning omnichannel collections orchestrator (CreditNirvana Agentathon · CM-02 + CM-03).

**Presenting or demoing? Start with [`DEMO_DAY.md`](DEMO_DAY.md)**: live URLs, the 30-minute checklist, do's and don'ts.
Live demo: [dripston.github.io/kairos/control](https://dripston.github.io/kairos/control) ·
backend [kairos-backend-5ifc.onrender.com](https://kairos-backend-5ifc.onrender.com/health) ·
[Engine Room](https://kairos-backend-5ifc.onrender.com/demo/engines) ·
[Journey 1](https://kairos-backend-5ifc.onrender.com/demo/journey/outbound) ·
[Journey 2](https://kairos-backend-5ifc.onrender.com/demo/journey/inbound)

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
| Sarvam models (optional) | [`backend/README.md` → Sarvam models](backend/README.md#sarvam-models-prism--sarvam-105b--deepseek) — prism / sarvam-105b / deepseek, off without a key |
