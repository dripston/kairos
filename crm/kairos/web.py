"""Kairos CRM web app: borrower detail page + replay + event ingestion endpoints.

    python3 -m kairos.web                 # http://127.0.0.1:8042/borrowers/B-1042

Routes
  GET  /borrowers/<id>               borrower detail page (K05 / K14)
  POST /borrowers/<id>/replay        form: action=all|step|reset
  POST /borrowers/<id>/voice         form: scenario=<key>  (IVR / voicebot call, SIMULATED)
  GET  /voice/flows                  exported IVR + voicebot agent specs (JSON)
  GET  /api/borrowers/<id>           the same read model as JSON
  POST /api/events                   ingest one normalized envelope (shared path)
  POST /webhooks/<sms|whatsapp|ledger|sarvam>  raw provider payload -> FIXTURE adapter -> shared path
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from kairos.adapters.fixtures import (FixtureLedgerAdapter, FixtureSarvamCallAdapter, FixtureSmsDlrAdapter,
                                     FixtureWhatsAppInboundAdapter)
from kairos.db import DEFAULT_DB_PATH, connect
from kairos.ingest import IngestError, ingest_event
from kairos.replay import replay_state, run_all, run_next
from kairos.seed import BORROWER_ID, seed
from kairos.views import borrower_view
from kairos.voice.executor import run_branch, scenario_list
from kairos.voice.flows import agent_specs

WEBHOOK_ADAPTERS = {
    "sms": FixtureSmsDlrAdapter(),
    "whatsapp": FixtureWhatsAppInboundAdapter(),
    "ledger": FixtureLedgerAdapter(),
    "sarvam": FixtureSarvamCallAdapter(),
}

CATEGORY_LABEL = {
    "DISPATCH": "Dispatch", "PROVIDER_DELIVERY": "Provider delivery", "CALL_OUTCOME": "Call outcome",
    "BORROWER_MESSAGE": "Borrower message", "BORROWER_INTENT": "Borrower intent", "AGENT_OUTPUTS": "Agent outputs",
    "SUPERVISOR": "Supervisor", "EXECUTION": "Executor", "PAYMENT_CLAIM": "Payment claim",
    "VERIFIED_PAYMENT": "Verified payment", "CONSENT": "Consent",
}

CSS = """
:root{--bg:#f3f5f4;--card:#fff;--ink:#14282c;--muted:#5b6d70;--line:#d9e2e0;--brand:#12343a;--accent:#0f6b5c;
--wait:#8a5a00;--wait-bg:#fff4dc;--ok:#0f6b3a;--ok-bg:#e3f4ea;--warn:#a1360f;--warn-bg:#fde8df;--info:#1d4e89;--info-bg:#e5eefb;
--sim:#6b3fa0;--sim-bg:#f0e8fb;--chip:#eef2f1}
@media (prefers-color-scheme:dark){:root{--bg:#0f1719;--card:#162124;--ink:#e6eeee;--muted:#9bb0b2;--line:#28383b;--brand:#0b1f23;
--accent:#56c2ad;--wait:#f2c46b;--wait-bg:#3a2e12;--ok:#7bd8a2;--ok-bg:#143022;--warn:#f59b7b;--warn-bg:#3b1d14;--info:#8fb6ee;
--info-bg:#17263b;--sim:#c9a6f5;--sim-bg:#2a1f3a;--chip:#1f2d30}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header.top{background:var(--brand);color:#fff;padding:14px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header.top b{letter-spacing:.04em}header.top .sim{margin-left:auto}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px;display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px}
@media (max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 10px}
.summary p{margin:2px 0}.summary p:first-child{font-weight:600}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:999px;white-space:nowrap}
.b-wait{background:var(--wait-bg);color:var(--wait)}.b-ok{background:var(--ok-bg);color:var(--ok)}.b-warn{background:var(--warn-bg);color:var(--warn)}
.b-info{background:var(--info-bg);color:var(--info)}.b-sim{background:var(--sim-bg);color:var(--sim)}.b-muted{background:var(--chip);color:var(--muted)}
.next{border-left:4px solid var(--wait)}.next.SEND{border-left-color:var(--accent)}.next.NONE{border-left-color:var(--ok)}
.next.HANDOFF{border-left-color:var(--warn)}.next .head{font-size:18px;font-weight:700;margin:4px 0}.next .then{color:var(--accent);font-weight:600}
.next .reason{margin:6px 0 0}.next .basis{color:var(--muted);font-size:12px;margin-top:6px}
.loans{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.loan{border:1px solid var(--line);border-radius:10px;padding:14px}.loan .row{display:flex;justify-content:space-between;gap:8px;align-items:center}
.loan .amt{font-size:20px;font-weight:700}.loan ul{margin:8px 0 0;padding:0;list-style:none;font-size:13px}.loan li{margin:4px 0}
.id{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--chip);padding:1px 6px;border-radius:5px;color:var(--muted);white-space:nowrap}
.muted{color:var(--muted)}
ol.tl{list-style:none;margin:0;padding:0;position:relative}ol.tl:before{content:"";position:absolute;left:7px;top:6px;bottom:6px;width:2px;background:var(--line)}
ol.tl li{position:relative;padding:0 0 16px 28px}ol.tl li:before{content:"";position:absolute;left:2px;top:6px;width:12px;height:12px;border-radius:50%;background:var(--card);border:2px solid var(--accent)}
ol.tl li.cat-PROVIDER_DELIVERY:before{border-color:var(--info)}ol.tl li.cat-BORROWER_MESSAGE:before,ol.tl li.cat-BORROWER_INTENT:before{border-color:var(--wait)}
ol.tl li.cat-SUPERVISOR:before{border-color:var(--wait);background:var(--wait)}ol.tl li.cat-PAYMENT_CLAIM:before{border-color:var(--warn)}
ol.tl li.cat-VERIFIED_PAYMENT:before{border-color:var(--ok);background:var(--ok)}ol.tl li.cat-EXECUTION:before{border-color:var(--muted)}
ol.tl li.cat-AGENT_OUTPUTS:before{border-color:var(--sim)}ol.tl li.cat-CALL_OUTCOME:before{border-color:var(--info)}
.agents{margin:6px 0 0;padding:0;list-style:none;font-size:13px}.agents li{display:flex;gap:6px;align-items:baseline;margin:3px 0}
.agents .k{font-weight:700;min-width:34px}.tx .k{min-width:64px;color:var(--muted);font-weight:600}
.voice form{display:flex;flex-direction:column;gap:6px}.voice button{text-align:left;font:inherit;font-size:13px;border-radius:8px;
padding:7px 10px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}ol.tl .agents li{padding:0}ol.tl .agents li:before{content:none}.summary .line{display:flex;gap:8px}.summary .lbl{font-weight:700;min-width:62px;color:var(--muted)}
.tl .meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12px}.tl .title{font-weight:600;margin:2px 0}.tl .detail{font-size:14px;color:var(--muted)}
.tl .ids{margin-top:4px;display:flex;gap:4px;flex-wrap:wrap}
.replay button{font:inherit;font-weight:600;border-radius:8px;padding:8px 12px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
.replay button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.replay form{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.steps{list-style:none;margin:0;padding:0;font-size:14px}.steps li{padding:8px 0;border-top:1px solid var(--line)}
.steps .st{display:flex;gap:8px;align-items:flex-start}.steps .chk{font-size:12px;color:var(--muted);margin:4px 0 0 26px}
.steps .chk div:before{content:"✓ ";color:var(--ok)}.steps .err{color:var(--warn);font-size:12px;margin:4px 0 0 26px}
.dot{width:18px;flex:none;text-align:center;font-weight:700}.pass{color:var(--ok)}.fail{color:var(--warn)}.todo{color:var(--muted)}
.note{font-size:12px;color:var(--muted);margin-top:10px}
"""


def _badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{escape(text)}</span>'


def _id(value: Optional[str], prefix: str = "") -> str:
    return f'<span class="id">{escape(prefix)}{escape(value)}</span>' if value else ""


def render_borrower(view: Dict[str, Any], replay: Dict[str, Any]) -> str:
    b, na, pos = view["borrower"], view["next_action"], view["position"]
    action_cls = {"WAIT": "b-wait", "SEND": "b-info", "NONE": "b-ok", "HANDOFF": "b-warn"}[na["action"]]

    loans_html = []
    for loan in view["loans"]:
        status = _badge("EMI CLEARED", "b-ok") if loan["status"] == "CLEARED" else _badge("OVERDUE", "b-warn")
        items = []
        for c in loan["claims"]:
            cls = "b-ok" if c["status"] == "VERIFIED" else "b-wait"
            items.append(f"<li>Claim {_id(c['claim_id'])} {_badge(c['status'].replace('_', ' '), cls)}</li>")
        for p in loan["payments"]:
            items.append(f"<li>Verified payment ₹{p['amount']:,} {_id(p['ledger_ref'])}</li>")
        for t in loan["touches"]:
            cls = {"PENDING": "b-info", "CANCELED": "b-muted", "SENT": "b-ok"}[t["status"]]
            why = f' <span class="muted">· {escape(t["status_reason"])}</span>' if t["status_reason"] else ""
            items.append(f"<li>{escape(t['channel_name'])} {escape(t['purpose'].lower())} {_id(t['touch_id'])} "
                         f"{_badge(t['status'], cls)}{why}</li>")
        loans_html.append(f"""
        <div class="loan">
          <div class="row"><b>{escape(loan['label'])}</b>{status}</div>
          <div class="muted">{escape(loan['product'])} · {_id(loan['loan_id'])}</div>
          <div class="row" style="margin-top:8px"><span class="amt">₹{loan['overdue_amount']:,}</span>
            <span class="muted">overdue · EMI ₹{loan['emi_amount']:,} · {loan['dpd']} DPD</span></div>
          <div class="muted" style="font-size:12px">POS ₹{loan['pos']:,} · TOS ₹{loan['tos']:,} · bucket {escape(loan['bucket'])}</div>
          <div style="margin-top:4px">{_badge('DATASET', 'b-sim')} <span class="muted" style="font-size:12px">
            {escape(loan['source'])}; dataset DPD {loan['source_dpd']}, demo clock +30 days</span></div>
          <ul>{''.join(items)}</ul>
        </div>""")

    tl_html = []
    for e in view["timeline"]:
        prov = _badge(e["provenance"], "b-sim")
        fixture = _badge(f"FIXTURE · {e['fixture_of']}", "b-sim") if e["fixture_of"] else ""
        ids = "".join(x for x in (_id(e["loan_id"]), _id(e["campaign_id"]), _id(e["touch_id"]),
                                  _id(e["event_id"])) if x)
        detail = escape(e["detail"] or "")
        if e["transcript"]:
            detail += '<ul class="agents tx">' + "".join(
                f'<li><span class="k">{"Bot" if t["speaker"] == "bot" else "Borrower"}</span>'
                f'<span>{escape(t["text"])}</span></li>' for t in e["transcript"]) + "</ul>"
        if e["agents"]:
            detail = '<ul class="agents">' + "".join(
                f'<li><span class="k">{escape(a["task_id"])}</span><span>{escape(a["headline"])} '
                f'<span class="muted">· {escape(a["owner"])}</span> '
                f'{_badge("FIXTURE", "b-sim") if a["is_fixture"] else _badge("BUILT", "b-ok")}</span></li>'
                for a in e["agents"]) + "</ul>"
        tl_html.append(f"""
        <li class="cat-{e['category']}">
          <div class="meta"><span class="muted">{escape(e['when'])}</span>
            {_badge(CATEGORY_LABEL.get(e['category'], e['category']), 'b-muted')}{prov}{fixture}</div>
          <div class="title">{escape(e['title'])}</div>
          <div class="detail">{detail}</div>
          <div class="ids">{ids}</div>
        </li>""")

    done = {r["n"]: r for r in replay["results"]}
    step_html = []
    for s in replay["steps"]:
        r = done.get(s["n"])
        if r is None:
            mark, cls, extra = "○", "todo", ""
        else:
            mark, cls = ("✓", "pass") if r["passed"] else ("✗", "fail")
            extra = (f'<div class="chk">{"".join(f"<div>{escape(c)}</div>" for c in r["checks"])}</div>'
                     + (f'<div class="err">{escape(r["error"])}</div>' if r["error"] else ""))
        step_html.append(f'<li><div class="st"><span class="dot {cls}">{mark}</span>'
                         f'<span>{s["n"]}. {escape(s["title"])}</span></div>{extra}</li>')

    dup_note = ""
    if view["duplicates"]:
        dup_note = (f'<div class="note">{len(view["duplicates"])} duplicate event(s) ignored by ingestion: '
                    + ", ".join(f'{_id(d["attempted_event_id"])} → original {_id(d["original_event_id"])}'
                                for d in view["duplicates"]) + "</div>")
    step_disabled = " disabled" if replay["cursor"] >= replay["total"] else ""
    then = f'<div class="then">{escape(na["then"])}</div>' if na["then"] else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kairos · {escape(b['borrower_id'])}</title>
<style>{CSS}</style></head><body>
<header class="top"><b>KAIROS</b><span>Borrower {escape(b['borrower_id'])}</span>
  <span class="sim">{_badge('SYNTHETIC BORROWER', 'b-sim')} {_badge('SIMULATED DATA', 'b-sim')}</span></header>
<main>
 <section>
  <div class="card summary">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
      <h1>{escape(b['display_name'])}</h1>{_id(b['borrower_id'])}{_badge(f"{b['dpd']} DPD", 'b-info')}
      {_badge(f"{pos['open']} of {pos['total']} loans overdue · ₹{pos['overdue']:,}", 'b-warn' if pos['open'] else 'b-ok')}
      {_badge('NEXT ACTION: ' + na['action'], action_cls)}</div>
    <div class="muted" style="font-size:13px;margin-bottom:8px">{escape(b['persona'] or '')} · {escape(b['language'] or '')}
      · K05 summary (channel evidence, then borrower intent)</div>
    <div class="line"><span class="lbl">Channel</span><span>{escape(view['summary'][0].removeprefix('Channel: '))}</span></div>
    <div class="line"><span class="lbl">Intent</span><span>{escape(view['summary'][1].removeprefix('Intent: '))}</span></div>
  </div>
  <div class="card next {na['action']}"><h2>Next action</h2>
    <div class="head">{escape(na['headline'])}</div>{then}
    <p class="reason">{escape(na['reason'])}</p>
    <div class="basis">Based on: {escape(na['basis'])} · as of {escape(view['sim_now_display'])} (simulated clock)</div>
  </div>
  <div class="card"><h2>Loans</h2><div class="loans">{''.join(loans_html)}</div></div>
  <div class="card"><h2>Timeline · oldest first</h2>
    <ol class="tl">{''.join(tl_html) or '<li class="muted">No events yet.</li>'}</ol>{dup_note}</div>
 </section>
 <aside>
  <div class="card replay"><h2>Replay synthetic journey</h2>
    <form method="post" action="/borrowers/{escape(b['borrower_id'])}/replay">
      <button class="primary" name="action" value="all">Replay journey</button>
      <button name="action" value="step"{step_disabled}>Next step</button>
      <button name="action" value="reset">Reset</button>
    </form>
    <ol class="steps">{''.join(step_html)}</ol>
    <div class="note">Every step sends a raw provider or ledger payload through a
      {_badge('FIXTURE', 'b-sim')} adapter (Rehan's interfaces are still pending) and then into the one shared
      <span class="id">ingest_event</span> path. That is the same path <span class="id">POST /api/events</span> and
      <span class="id">/webhooks/*</span> use.</div>
  </div>
  <div class="card voice"><h2>Voice: Sarvam IVR + voicebot</h2>
    <form method="post" action="/borrowers/{escape(b['borrower_id'])}/voice">
      {''.join(f'<button name="scenario" value="{escape(k)}">{escape(t)}</button>' for k, t in scenario_list())}
    </form>
    <div class="note">Each button starts a fresh branch from the same saved state (journey replayed), then
      plans a voice touch, rechecks consent and holds, and runs the flow
      (<a href="/voice/flows">ivr_emi_menu_hi_v1, voicebot_callback_hi_v1</a>) against a scripted borrower.
      The call-completion webhook goes through the Sarvam adapter into <span class="id">ingest_event</span>.
      {_badge('SIMULATED', 'b-sim')} until the Sarvam voice-agent route is authorized.</div>
  </div>
 </aside>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Kairos/0.1"
    db_path: str = str(DEFAULT_DB_PATH)

    def _conn(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        if conn.execute("SELECT 1 FROM borrowers WHERE borrower_id = ?", (BORROWER_ID,)).fetchone() is None:
            seed(conn)
        return conn

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, indent=2, default=str), "application/json")

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/borrowers", "/borrowers/"):
            return self._redirect(f"/borrowers/{BORROWER_ID}")
        if path == "/voice/flows":
            return self._json(200, agent_specs())
        conn = self._conn()
        try:
            m = re.fullmatch(r"/(api/)?borrowers/([\w-]+)", path)
            if not m:
                return self._send(404, "Not found", "text/plain")
            view = borrower_view(conn, m.group(2))
            if view is None:
                return self._send(404, f"Unknown borrower {escape(m.group(2))}", "text/plain")
            if m.group(1):
                return self._json(200, {**view, "replay": replay_state(conn)})
            return self._send(200, render_borrower(view, replay_state(conn)))
        finally:
            conn.close()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        conn = self._conn()
        try:
            m = re.fullmatch(r"/borrowers/([\w-]+)/replay", path)
            if m:
                action = parse_qs(self._body().decode()).get("action", ["all"])[0]
                if action == "reset":
                    seed(conn)
                elif action == "step":
                    run_next(conn)
                else:
                    run_all(conn)
                return self._redirect(f"/borrowers/{m.group(1)}")

            m = re.fullmatch(r"/borrowers/([\w-]+)/voice", path)
            if m:
                key = parse_qs(self._body().decode()).get("scenario", [""])[0]
                if key not in dict(scenario_list()):
                    return self._send(400, f"Unknown scenario {escape(key)}", "text/plain")
                run_branch(conn, key)
                return self._redirect(f"/borrowers/{m.group(1)}")

            if path == "/api/events" or path.startswith("/webhooks/"):
                try:
                    payload = json.loads(self._body() or b"{}")
                    if path == "/api/events":
                        envelope = payload
                    else:
                        adapter = WEBHOOK_ADAPTERS.get(path.rsplit("/", 1)[-1])
                        if adapter is None:
                            return self._json(404, {"error": f"No adapter for {path}"})
                        envelope = adapter.normalize(payload)
                    result = ingest_event(conn, envelope)
                except (IngestError, ValueError, KeyError) as exc:
                    return self._json(422, {"error": f"{type(exc).__name__}: {exc}"})
                return self._json(200, result.to_dict())
            return self._send(404, "Not found", "text/plain")
        finally:
            conn.close()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[kairos] {self.address_string()} {fmt % args}")


class KairosServer(ThreadingHTTPServer):
    """On shutdown, in-flight requests finish (their per-request DB connection closes in `finally`)."""
    daemon_threads = False
    block_on_close = True  # server_close() joins every request thread


def close_db(db_path: str) -> None:
    """Final flush: fold the WAL back into the database file so nothing is left half-written."""
    if db_path == ":memory:":
        return
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _raise_interrupt(signum, _frame) -> None:
    raise KeyboardInterrupt(signum)


def serve(server: KairosServer, db_path: str, install_signals: bool = True) -> str:
    """serve_forever until Ctrl-C or SIGTERM, then stop cleanly: no new requests, in-flight ones finish,
    the DB is checkpointed and closed. Returns why it stopped."""
    import signal
    if install_signals:
        signal.signal(signal.SIGTERM, _raise_interrupt)  # SIGINT is already KeyboardInterrupt
    why = "stopped"
    try:
        server.serve_forever()
    except KeyboardInterrupt as e:
        why = "SIGTERM" if e.args and e.args[0] == signal.SIGTERM else "SIGINT"
    finally:
        server.server_close()
        close_db(db_path)
    return why


def main() -> None:
    parser = argparse.ArgumentParser(description="Kairos CRM borrower page + replay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8042)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    Handler.db_path = args.db
    server = KairosServer((args.host, args.port), Handler)
    print(f"Kairos running on http://{args.host}:{args.port}/borrowers/{BORROWER_ID}  (db: {args.db})")
    why = serve(server, args.db)
    print(f"Kairos CRM stopped cleanly ({why}); database closed.")


if __name__ == "__main__":
    main()
