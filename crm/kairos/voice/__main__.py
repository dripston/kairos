"""Voice CLI.

    python3 -m kairos.voice list                      # call scenarios
    python3 -m kairos.voice call ivr_needs_time --branch   # replay journey, then place a SIMULATED call
    python3 -m kairos.voice export                    # write kairos/voice/agents/*.json
    python3 -m kairos.voice render-prompts            # Sarvam TTS → var/prompts/*.wav (needs SARVAM_API_KEY)
    python3 -m kairos.voice transcribe clip.wav       # Sarvam STT → voicebot NLU (needs SARVAM_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from kairos.db import REPO_ROOT, connect
from kairos.seed import BORROWER_ID, seed
from kairos.voice.executor import SCENARIOS, place_call, run_branch
from kairos.voice.flows import IVR_PROMPTS, VOICEBOT_PROMPTS, agent_specs, classify_utterance, extract_date

AGENTS_DIR = Path(__file__).resolve().parent / "agents"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("call")
    c.add_argument("scenario", choices=[s.key for s in SCENARIOS])
    c.add_argument("--db")
    c.add_argument("--branch", action="store_true", help="reset and replay the journey first (same saved state)")
    e = sub.add_parser("export")
    e.add_argument("--webhook-url", default="https://<kairos-host>/webhooks/sarvam")
    r = sub.add_parser("render-prompts")
    r.add_argument("--out", default=str(REPO_ROOT / "var" / "prompts"))
    r.add_argument("--speaker")
    t = sub.add_parser("transcribe")
    t.add_argument("audio")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        for s in SCENARIOS:
            print(f"{s.key:<24} {s.title}")
        return 0

    if args.cmd == "call":
        conn = connect(args.db)
        if conn.execute("SELECT 1 FROM borrowers WHERE borrower_id = ?", (BORROWER_ID,)).fetchone() is None:
            seed(conn)
        run = (run_branch if args.branch else place_call)(conn, args.scenario).to_dict()
        print(f"{run['scenario']} · touch {run['touch_id']}")
        if run["blocked_reason"]:
            print(f"  BLOCKED: {run['blocked_reason']}")
        else:
            print(f"  disposition {run['disposition']}, exit {run['exit_reason']}, intent "
                  f"{(run['intent'] or {}).get('intent', 'none')}")
            for turn in run["transcript"]:
                print(f"    {turn['speaker']:>8}: {turn['text']}")
        for ev in run["events"]:
            print(f"  {ev['status']:<9} {ev['event_type']:<20} {ev['event_id']}")
        return 0

    if args.cmd == "export":
        AGENTS_DIR.mkdir(exist_ok=True)
        for flow_id, spec in agent_specs(args.webhook_url).items():
            path = AGENTS_DIR / f"{flow_id}.json"
            path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        return 0

    from kairos.voice.sarvam import SarvamClient, SarvamNotConfigured

    try:
        client = SarvamClient()
    except SarvamNotConfigured as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.cmd == "render-prompts":
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for flow, prompts in (("ivr", IVR_PROMPTS), ("voicebot", VOICEBOT_PROMPTS)):
            for key, text in prompts.items():
                text = text.replace("{date}", "25 November")
                (out / f"{flow}_{key}.wav").write_bytes(client.tts(text, speaker=args.speaker))
                print(f"rendered {flow}_{key}.wav")
        return 0

    if args.cmd == "transcribe":
        text = client.stt(Path(args.audio).read_bytes(), filename=Path(args.audio).name)
        when = extract_date(text, datetime.now().astimezone())
        print(json.dumps({"transcript": text, "intent": classify_utterance(text),
                          "agreed_date": when.isoformat() if when else None}, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
