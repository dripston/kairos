# Kairos: final files archive

The "A" Team · CreditNirvana Agentathon · 20 Nov 2026
*Collections that decide who, when and how.*

This branch holds only the finished deliverables. It has no code. The code is on `main`.

| Folder | What's in it |
|---|---|
| `Films/` | Product film (3:40) and Technical film (3:24), 4K. Each is split into 95 MB parts because GitHub caps files at 100 MB. See below. |
| `Documents/` | Intro slides (.pptx, .key), Judges' Q&A prep, Demo walkthrough, Technical reference, Explained simply, plus the business-flow, architecture and implementation briefs (PDF) |
| `Frontend recordings/` | 10 captioned screen recordings of the live demo site (see `INDEX.md`) |
| `Proof recordings/` | R01–R10 real-time proof recordings, plus `RUNBOOK.md` (how to run each demo) |
| `Code backup/` | Git bundle and patch of code that was only ever local. It is now also pushed as the `archive/*` branches below. |

## Rebuilding a film

Download every part of the film, put them in one folder, then run:

```bash
cat "Kairos - Product film (4K).mp4.part-"* > "Kairos - Product film (4K).mp4"
cat "Kairos - Technical film (4K).mp4.part-"* > "Kairos - Technical film (4K).mp4"
shasum -a 256 -c SHA256SUMS
```

Both lines of the check should say `OK`.

## Other places

- Code: branch `main`; film sources on `film/walkthrough`
- Code that was never pushed before cleanup: `archive/k09-replay-wip`, `archive/k05-standalone` and `archive/uncommitted-changes`
- Demo site: https://dripston.github.io/kairos/control
- Backend: https://kairos-backend-5ifc.onrender.com

All numbers in these files come from simulated tests on synthetic borrowers.
