"""K27 · Agent activity dashboard - verification (9 tests against the raw logs).

Usage:  python3 reference_check.py            (uses the latest run; runs the pipeline if none)
        python3 reference_check.py --rebuild
"""
import importlib.util
import json
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kairos_engine"))
from checks_common import Runner, latest_run, load_logs  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import CHANNELS, SEGMENTS, file_sha, load_all, ojas_file, path  # noqa: E402

R = Runner("K27 agent activity dashboard", HERE)
man, rdir = latest_run("--rebuild" in sys.argv)
cfg, k01, k21 = load_all()
L = load_logs(rdir)
html = open(os.path.join(HERE, "outputs", "dashboard.html"), encoding="utf-8").read()
trace = json.load(open(os.path.join(HERE, "outputs", "trace_example.json")))
print(f"Run {man['run_id']} · {man['label']}\n")

# T01 self-contained + parses + ids exist
m = re.search(r"const D=(\{.*?\});\nconst \$=", html, re.S)
D = json.loads(m.group(1)) if m else None
ext = re.findall(r"<script[^>]+src=", html)
used = set(re.findall(r'\$\("([A-Za-z]+)"\)', html))
have = set(re.findall(r'id="([A-Za-z]+)"', html))
ok = D is not None and not ext and used <= have
R.check("K27-T01", ok, f"{len(html) / 1e6:.1f} MB single file; external scripts: {len(ext)}; embedded data parsed: {D is not None}; "
        f"{len(used)} element ids used by the script, missing: {sorted(used - have) or 'none'}")

# T02 filters
cube = pd.DataFrame(D["cube"], columns=["policy", "date", "segment", "channel", "touches", "spend", "replies"])
has_sel = all(f'id="{i}"' in html for i in ("fAgent", "fSeg", "fCh"))
ok = has_sel and set(D["segments"]) == set(SEGMENTS) and set(cube.channel) == set(CHANNELS) and set(cube.policy) == {"kairos", "fixed_cadence"} \
    and 'value="kairos"' in html and 'value="fixed_cadence"' in html
R.check("K27-T02", ok, f"filters present: {has_sel}; cohorts {len(D['segments'])}, channels {sorted(set(cube.channel))}, agents {sorted(set(cube.policy))}")

# T03 activity totals
ok, parts = True, []
for pol, lg in L.items():
    c, s = cube[cube.policy == pol], lg["sends"]
    trip = (int(c.touches.sum()), round(c.spend.sum(), 1), int(c.replies.sum()))
    raw = (len(s), round(s.actual_cost_inr.sum(), 1), int(s.replied.sum()))
    ok &= trip[0] == raw[0] and trip[2] == raw[2] and abs(trip[1] - raw[1]) < 0.5
    parts.append(f"{pol}: dashboard {trip[0]:,} touches / ₹{trip[1]:,.0f} / {trip[2]:,} replies = raw log")
R.check("K27-T03", ok, " | ".join(parts))

# T04 decisions
dec = pd.read_csv(os.path.join(rdir, "decisions_daily.csv")).groupby(["policy", "decision", "reason"]).borrowers.sum()
dd = pd.DataFrame(D["decisions"], columns=["policy", "decision", "reason", "borrowers", "type"]).set_index(["policy", "decision", "reason"])
same = dec.sort_index().equals(dd.borrowers.sort_index().astype(dec.dtype))
types = set(dd.type)
ok = same and {"K01 gate", "Override (hold, STOP or cancel)", "Agent choice"} <= types
R.check("K27-T04", ok, f"{len(dd)} decision rows equal decisions_daily: {same}; types shown: {sorted(types)}")

# T05 drill-down
col = D["cols"].index("touch_id")
bad, n = 0, 0
for bid, p in D["people"].items():
    for pol, lg in L.items():
        n += 1
        mine = sorted(t[col] for t in p["touches"][pol])
        raw = sorted(lg["sends"].loc[lg["sends"].borrower_id == bid, "touch_id"])
        bad += mine != raw
R.check("K27-T05", bad == 0 and len(D["people"]) >= 100, f"{len(D['people'])} sample borrowers x 2 agents: {n - bad}/{n} timelines equal the raw log")

# T06 re-evaluate the traced recommendation with Ojas's K01 reference
spec = importlib.util.spec_from_file_location("k01ref", ojas_file("k01_reference")[0])
ref = importlib.util.module_from_spec(spec); spec.loader.exec_module(ref)
s = L["kairos"]["sends"]
row = s[s.touch_id == trace["touch_id"]].iloc[0]
port = pd.read_csv(os.path.join(path("data", "generated", f"seed_{man['seed']}"), "borrowers.csv"))
b = port[port.borrower_id == row.borrower_id].iloc[0]
mine = s[s.borrower_id == row.borrower_id].sort_values(["date", "time"])
before = mine[(mine.date < row.date) | ((mine.date == row.date) & (mine.time < row.time))]
today = before[before.date == row.date]
same_ch = before[before.channel == row.channel]
mins = lambda d, t: (date.fromisoformat(d) - date(2000, 1, 1)).days * 1440 + int(t[:2]) * 60 + int(t[3:])
gap = "" if same_ch.empty else str(mins(row.date, row.time) - mins(same_ch.date.iloc[-1], same_ch.time.iloc[-1]))
wk = lambda d: date.fromisoformat(d).isocalendar()[1]
pays = L["kairos"]["payments"]
start = date.fromisoformat(cfg["campaign_start"])
iso = lambda m_: "" if m_ < 0 else f"{(start + timedelta(days=int(m_) // 1440)).isoformat()}T{int(m_) % 1440 // 60:02d}:{int(m_) % 60:02d}"
templates = pd.read_csv(os.path.join(path("data", "generated", f"seed_{man['seed']}"), "templates.csv"))
approved = set(templates.loc[templates.status == "APPROVED", "template_id"])
c = {"date": row.date, "time": row.time, "dpd": str(row.dpd), "dpd_age_hours": str(row.dpd_age_hours), "channel": row.channel,
     "consent": "Y" if b[f"consent_{row.channel}"] else "N",
     "opted_out": "Y" if (b.opted_out_at_start or before.stop.any()) else "N",
     "hardship_until": iso(row.hardship_until_min), "hardship_supervisor": "Y" if row.hardship_supervisor else "N",
     "ptp_date": (start + timedelta(days=int(row.ptp_day))).isoformat() if row.ptp_day >= 0 else "",
     "obligation_cleared": "Y" if (pays[pays.borrower_id == row.borrower_id].date <= row.date).any() else "N",
     "stop_for_day": "Y" if (today.replied | today.answered | today.stop).any() else "N",
     "sends_today_channel": str(int((today.channel == row.channel).sum())), "sends_today_all": str(len(today)),
     "mins_since_last_same_channel": gap, "official_cap": "", "lender": "",
     "sends_week_channel": str(int(((before.channel == row.channel) & (before.date.map(wk) == wk(row.date))).sum())),
     "sends_week_all": str(int((before.date.map(wk) == wk(row.date)).sum())),
     "template_approved": "Y" if row.template_id in approved else "N", "budget_remaining_inr": str(row.budget_remaining_inr),
     "est_cost_inr": str(row.est_cost_inr), "campaign_target_met": "Y" if row.campaign_target_met else "N"}
got = ref.evaluate(k01, c)
R.check("K27-T06", got == ("SEND", "ELIGIBLE"), f"touch {trace['touch_id']} ({row.borrower_id}, {row.date} {row.time} {row.channel}) "
        f"rebuilt from the raw log -> Ojas's K01 reference says {got[0]} / {got[1]}")

# T07 trace fields
t3, t5 = trace["step_3_recommendation"], trace["step_5_outcome"]
ev = trace["step_6_evidence"]["touch_log"].split(" ")[0]
ok = (t3["channel"], t3["time"], t3["date"]) == (row.channel, row.time, row.date) and trace["step_4_template"] == row.template_id \
    and row.template_id in approved and trace["step_2_score"]["score"] == row.score and trace["step_2_score"]["intensity"] == row.intensity \
    and t5["replied"] == bool(row.replied) and t5["reply_type"] == (row.reply_type if isinstance(row.reply_type, str) else "") \
    and trace["step_1_policy"]["k01_version"] == k01["policy_version"] and trace["step_2_score"]["k21_version"] == k21["policy_version"] \
    and os.path.exists(os.path.join(os.path.dirname(HERE), ev))
R.check("K27-T07", ok, f"policy K01 {trace['step_1_policy']['k01_version']} -> score {row.score} {row.intensity} ({row.top_reasons}) -> "
        f"{row.channel} {row.time} -> template {row.template_id} (approved) -> outcome reply={bool(row.replied)} {row.reply_type} -> evidence {ev} exists")

# T08 no sensitive fields
raw = pd.read_csv(path("data", "raw", "PL_Dummy_Collections_Data_500.csv"))
names = [n for n in raw.Name.unique() if n in html]
phones = re.findall(r"(?<!\d)[6-9]\d{9}(?!\d)", html)
R.check("K27-T08", not names and not phones, f"borrower names found: {len(names)}; 10-digit mobile numbers found: {len(phones)} (IDs only: BRWnnnnnn / PLnnnnnnn)")

# T09 vs K26
k = pd.read_csv(os.path.join(rdir, "kpis.csv"))
ok, parts = True, []
for pol in L:
    c = cube[cube.policy == pol]
    v = c.replies.sum() / c.spend.sum()
    kv = float(k[(k.policy == pol) & (k.kpi == "replies_per_inr")].value.iloc[0])
    ok &= abs(v - kv) < 1e-4
    parts.append(f"{pol} {v:.4f} = K26 {kv:.4f}")
R.check("K27-T09", ok, " | ".join(parts))

R.finish(f"run {man['run_id']}")
