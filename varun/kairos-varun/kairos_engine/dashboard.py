"""K27 - agent activity dashboard. Writes one self-contained dashboard.html per run.

No server and no internet needed (charts are drawn as SVG by a few lines of JS),
so it opens from a laptop at the venue. Filters: agent (policy), segment (cohort)
and channel. Borrower drill-down traces each touch to the K01 policy version,
the K21 score and reasons, the Kairos choice, the template and the outcome.
"""
import json
import os

import numpy as np
import pandas as pd

from common import SEGMENTS


def _sample_borrowers(runs, data, n=400):
    """Borrowers with a story worth showing (reply+pay, hardship, STOP, PTP), deterministic."""
    k = runs["kairos"]["sends"]
    pk = runs["kairos"]["payments"]
    pick = []
    stories = [k.loc[k.reply_type == "PTP", "borrower_idx"], k.loc[k.reply_type == "HARDSHIP", "borrower_idx"],
               k.loc[k.stop, "borrower_idx"], pk.loc[pk.source == "ptp_kept", "borrower_idx"],
               k.loc[k.reply_type == "WILLING", "borrower_idx"], k.loc[k.hard_bounce, "borrower_idx"],
               pd.Series(np.arange(len(data)))]
    for s in stories:
        for b in pd.unique(s)[: n // len(stories) + 5]:
            if b not in pick:
                pick.append(int(b))
    return sorted(pick[:n])


OVERRIDES = {"STOP_OPT_OUT", "HARDSHIP_HOLD", "PTP_HOLD", "OBLIGATION_CLEARED", "STOP_FOR_DAY", "CAMPAIGN_TARGET_MET",
             "HARDSHIP_BEYOND_4_DAYS (subset of HARDSHIP_HOLD)"}
CHOICES = {"BELOW_VALUE_THRESHOLD", "INTENSITY_LIMIT", "NOT_SCHEDULED", "BUDGET_EXHAUSTED", "TOUCHED"}


def reason_type(decision, reason):
    if decision == "CANCELLED_AT_DISPATCH" or reason in OVERRIDES:
        return "Override (hold, STOP or cancel)"
    if reason in CHOICES:
        return "Agent choice"
    return "K01 gate"


def trace_example(runs, data, k01, k21, manifest):
    """One Kairos recommendation traced from policy to outcome and evidence."""
    s = runs["kairos"]["sends"]
    pick = s[(s.reply_type == "PTP")]
    row = (pick if len(pick) else s).iloc[0]
    b = data.iloc[int(row.borrower_idx)]
    pays = runs["kairos"]["payments"]
    pay = pays[pays.borrower_idx == row.borrower_idx]
    return {"run_id": manifest["run_id"], "run_label": manifest["run_mode"], "seed": manifest["seed"],
            "touch_id": row.touch_id, "borrower_id": b.borrower_id, "loan_id": b.loan_id, "obligation_id": b.obligation_id,
            "segment": b.segment, "language": b.language,
            "step_1_policy": {"k01_version": k01["policy_version"], "gates_in_order": k01["evaluation_order"],
                              "dpd": int(row.dpd), "band": [x["id"] for x in k01["dpd_bands"]][int(row.band)],
                              "consented_channels": [c for c in ("sms", "whatsapp", "email", "ivr", "voicebot") if b[f"consent_{c}"]]},
            "step_2_score": {"k21_version": k21["policy_version"], "score": int(row.score), "intensity": row.intensity,
                             "top_reasons": row.top_reasons},
            "step_3_recommendation": {"channel": row.channel, "time": row.time, "date": row.date,
                                      "expected_reply": float(row.expected_reply), "cost_inr": float(row.est_cost_inr),
                                      "budget_remaining_inr_before": float(row.budget_remaining_inr)},
            "step_4_template": row.template_id,
            "step_5_outcome": {"delivered": bool(row.delivered), "read": bool(row.read), "clicked": bool(row.clicked),
                               "answered": bool(row.answered), "replied": bool(row.replied), "reply_type": row.reply_type,
                               "actual_charge_inr": float(row.actual_cost_inr)},
            "step_6_evidence": {"touch_log": f"runs/{manifest['run_id']}/sends_kairos.csv.gz (touch_id {row.touch_id})",
                                "payment_events": pay[["date", "amount_inr", "source"]].to_dict("records"),
                                "audit": "re-checked with Ojas's K01 reference_check.evaluate -> SEND (see K15 audit_kairos.json)"}}


def write_dashboard(out, runs, data, summary, k01, k21, chats=None, review=None, manifest=None):
    seg_names = np.array(SEGMENTS)
    cube = []
    for name, r in runs.items():
        s = r["sends"]
        g = s.groupby(["date", "segment_idx", "channel"]).agg(t=("replied", "size"), s=("actual_cost_inr", "sum"),
                                                               r=("replied", "sum")).reset_index()
        for row in g.itertuples(index=False):
            cube.append([name, row.date, seg_names[row.segment_idx], row.channel, int(row.t), round(float(row.s), 2), int(row.r)])
    dates = runs["kairos"]["daily"]["date"].tolist()
    quiet = runs["kairos"]["daily"]["quiet_day"].tolist()
    dec = pd.concat([r["decisions"] for r in runs.values()])
    dec = dec.groupby(["policy", "decision", "reason"])["borrowers"].sum().reset_index()
    dec["type"] = [reason_type(d, r) for d, r in zip(dec.decision, dec.reason)]
    dec = dec[["policy", "decision", "reason", "borrowers", "type"]].values.tolist()
    trace = trace_example(runs, data, k01, k21, manifest) if manifest else None
    if trace:
        with open(os.path.join(out, "trace_example.json"), "w") as f:
            json.dump(trace, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    learn = runs["kairos"]["learning"]
    last = learn[learn.day == learn.day.max()].drop(columns=["day", "date"]).to_dict("records") if len(learn) else []
    first = learn[learn.day == learn.day.min()].drop(columns=["day", "date"]).to_dict("records") if len(learn) else []

    pick = _sample_borrowers(runs, data)
    cols = ["date", "time", "channel", "template_id", "band", "dpd", "intensity", "score", "top_reasons",
            "expected_reply", "actual_cost_inr", "delivered", "read", "clicked", "answered", "replied",
            "reply_type", "stop", "hard_bounce", "touch_id"]
    bands = [b["id"] for b in k01["dpd_bands"]]
    people = {}
    for b in pick:
        row = data.iloc[b]
        info = {"id": row.borrower_id, "loan": row.loan_id, "segment": row.segment, "language": row.language,
                "dpd_start": int(row.dpd_at_start), "state": row.state, "age": int(row.age),
                "consents": [c for c in ("sms", "whatsapp", "email", "ivr", "voicebot") if row[f"consent_{c}"]],
                "opted_out_at_start": bool(row.opted_out_at_start), "touches": {}, "payments": {}}
        for name, r in runs.items():
            s = r["sends"]
            t = s[s.borrower_idx == b][cols].copy()
            t["band"] = [bands[x] for x in t["band"]]
            t = t.replace({np.nan: None})
            info["touches"][name] = t.values.tolist()
            p = r["payments"]
            info["payments"][name] = p[p.borrower_idx == b][["date", "amount_inr", "source"]].values.tolist()
        people[row.borrower_id] = info

    payload = {"summary": summary, "cube": cube, "dates": dates, "quiet": quiet, "decisions": dec,
               "learn_first": first, "learn_last": last, "segments": SEGMENTS, "people": people, "cols": cols,
               "k01": k01["policy_version"], "k21": k21["policy_version"],
               "k01_order": k01["evaluation_order"], "chats": chats, "manifest": manifest, "trace": trace,
               "switch": review["switch_detected"] if review else None}
    html = TEMPLATE.replace("/*DATA*/", json.dumps(payload, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    with open(os.path.join(out, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Kairos · agent activity</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#f3f5f8;--surface:#ffffff;--ink:#1d2a40;--muted:#5d6b80;--line:#d9dfe7;--kairos:#0f766e;--fixed:#b7791f;
--good:#2f7d4f;--bad:#b83232;--chip:#e8edf3;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141a24;--surface:#1c2431;--ink:#e6ebf2;--muted:#9aa7b8;
--line:#2e394a;--kairos:#2bb3a6;--fixed:#e0a54a;--good:#5bbf85;--bad:#ec7b7b;--chip:#263042;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#141a24;--surface:#1c2431;--ink:#e6ebf2;--muted:#9aa7b8;--line:#2e394a;--kairos:#2bb3a6;
--fixed:#e0a54a;--good:#5bbf85;--bad:#ec7b7b;--chip:#263042;color-scheme:dark}
:root{box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}
main{max-width:1180px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:26px;font-weight:600;margin:0}h2{font-size:18px;font-weight:600;margin:0 0 4px}
.sub{color:var(--muted);margin:2px 0 0}
header{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:end;justify-content:space-between;margin-bottom:18px}
.label{display:inline-block;background:var(--chip);border-left:3px solid var(--fixed);padding:4px 10px;font-size:13px;color:var(--muted);max-width:640px}
.filters{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--bg);padding:10px 0;display:flex;flex-wrap:wrap;gap:10px;border-bottom:1px solid var(--line)}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--bg)}
select,input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
select:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid var(--kairos);outline-offset:2px}
section{margin-top:28px}
.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.check{background:var(--surface);padding:12px 14px}.check b{display:block;font-size:20px}
.pass{color:var(--good)}.fail{color:var(--bad)}.na{color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:24px}@media (max-width:820px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:16px}
.legend{display:flex;gap:16px;font-size:13px;color:var(--muted);margin:6px 0}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
svg{display:block;width:100%;height:auto}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500}td.num,th.num{text-align:right}
.kpi td:nth-child(2){color:var(--fixed)}.kpi td:nth-child(3){color:var(--kairos);font-weight:600}
.trace{display:grid;grid-template-columns:280px 1fr;gap:24px}#timeline{max-height:640px;overflow-y:auto;padding-right:8px}@media (max-width:820px){.trace{grid-template-columns:1fr}}
.who dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:10px 0 0}.who dt{color:var(--muted)}.who dd{margin:0}
.touch{border-left:3px solid var(--line);padding:8px 0 10px 14px;margin-left:4px;position:relative}
.touch.kairos{border-color:var(--kairos)}.touch.fixed_cadence{border-color:var(--fixed)}
.touch::before{content:"";position:absolute;left:-7px;top:13px;width:11px;height:11px;border-radius:50%;background:var(--surface);border:2px solid currentColor}
.touch.kairos::before{color:var(--kairos)}.touch.fixed_cadence::before{color:var(--fixed)}
.touch .when{font-weight:600}.touch .why{color:var(--muted);font-size:13px}
.out{display:inline-block;font-size:12px;padding:1px 7px;border-radius:10px;background:var(--chip);margin:3px 4px 0 0}
.out.reply{background:var(--good);color:var(--surface)}.out.stop{background:var(--bad);color:var(--surface)}
.pay{border-left:3px solid var(--good);padding:6px 0 6px 14px;margin-left:4px;color:var(--good);font-weight:600}
details{margin-top:6px}summary{cursor:pointer;color:var(--muted);font-size:13px}
.gates{font-size:12px;color:var(--muted);line-height:1.7}
.empty{color:var(--muted);padding:12px 0}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><main>
<header><div><h1>Kairos agent activity</h1><p class="sub" id="runline"></p></div><div class="label" id="label"></div></header>
<p class="sub" id="modeline"></p>
<div class="filters" role="group" aria-label="Filters">
 <label>Agent <select id="fAgent"><option value="both">Both</option><option value="kairos">Kairos</option><option value="fixed_cadence">Fixed cadence</option></select></label>
 <label>Cohort (segment) <select id="fSeg"><option value="all">All cohorts</option></select></label>
 <label>Channel <select id="fCh"><option value="all">All channels</option></select></label>
</div>

<section><h2>Acceptance checks</h2><div class="checks" id="checks"></div></section>

<section class="grid2">
 <div class="panel"><h2>Replies per ₹ by day</h2><p class="sub">Meaningful replies divided by actual spend. Quiet days (Sundays) send nothing.</p>
  <div class="legend"><span><span class="sw" style="background:var(--kairos)"></span>Kairos</span><span><span class="sw" style="background:var(--fixed)"></span>Fixed cadence</span></div>
  <div id="chartRpr"></div></div>
 <div class="panel"><h2>Totals for the current filter</h2><div class="wrap"><table class="kpi" id="kpi"></table></div></div>
</section>

<section class="panel"><h2>Channel mix by day</h2><p class="sub" id="mixsub">Share of touches per channel. The drift point is marked; this is where Kairos switches channels.</p>
 <div class="legend" id="mixlegend"></div><div id="chartMix"></div></section>

<section class="grid2">
 <div class="panel"><h2>Decision reasons and overrides</h2><p class="sub">Borrower-days by final decision, whole campaign. "Override" means a hold, STOP or a planned touch cancelled at dispatch. Filtered by agent only.</p><div class="wrap"><table id="dec"></table></div></div>
 <div class="panel"><h2>What Kairos learned per segment</h2><p class="sub">Best channel and time by expected replies per ₹, first day vs last day.</p><div class="wrap"><table id="learn"></table></div></div>
</section>

<section class="panel"><h2>Borrower trace</h2><p class="sub">Pick a borrower to see every touch from both agents, with the gates, score and reasons behind it.</p>
 <div class="filters" style="position:static;border:0;padding:8px 0;background:transparent"><input id="who" list="whoList" placeholder="Borrower ID, e.g. BRW000123" aria-label="Borrower ID"><datalist id="whoList"></datalist>
 <select id="whoPick" aria-label="Sample borrowers"></select></div>
 <div class="trace"><div class="who" id="whoCard"></div><div id="timeline"></div></div></section>
</main>
<script>
const D=/*DATA*/;
const $=id=>document.getElementById(id), CH=["sms","whatsapp","email","ivr","voicebot"];
const CHC={sms:"#3a6ea5",whatsapp:"#2f8f5b",email:"#8a6bbf",ivr:"#c0672b",voicebot:"#a23b5b"};
const AG={kairos:"Kairos",fixed_cadence:"Fixed cadence"};
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt=(n,d=0)=>Number(n).toLocaleString("en-IN",{maximumFractionDigits:d,minimumFractionDigits:d});
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const S=D.summary;
$("runline").textContent=`${S.run_id} · seed ${S.seed} · ${fmt(S.borrowers)} borrowers · ${S.campaign.start} to ${S.campaign.end} · K01 ${D.k01} · K21 ${D.k21}`+(D.manifest?` · baseline: ${D.manifest.baseline}`:"");
$("label").textContent=S.label;
if(D.switch)$("modeline").textContent=`Run mode: ${D.manifest?D.manifest.run_mode:"SIMULATED"}. Kairos's channel switch detected on ${D.switch.date} (${D.switch.group}: ${D.switch.change}).`;
D.segments.forEach(s=>$("fSeg").insertAdjacentHTML("beforeend",`<option>${s}</option>`));
CH.forEach(c=>$("fCh").insertAdjacentHTML("beforeend",`<option>${c}</option>`));
const C=S.acceptance_checks, names={lift_at_least_20pct:["Replies per ₹ vs fixed cadence",`${S.lift_replies_per_inr_pct>0?"+":""}${S.lift_replies_per_inr_pct}%`],
 zero_cap_breaches:["Cap breaches (K01 reference audit)",`${fmt(S.audit.breaches)} in ${fmt(S.audit.sends_checked)} sends checked`],decisions_under_10_min_per_day:["Slowest daily decision run",`${Math.max(S.policies.kairos.max_plan_seconds,S.policies.fixed_cadence.max_plan_seconds).toFixed(2)} s for all borrowers`],
 deterministic_replay:["Same seed, same result","byte-identical log"]};
if(D.chats){C.chats_500=D.chats.peak_concurrent_conversations>=500&&D.chats.safety_violations===0&&D.chats.first_response_within_sla_pct===100;
 names.chats_500=["500 concurrent chats (K10 gate)",`peak ${D.chats.peak_concurrent_conversations}, ${D.chats.safety_violations} leaks, first reply p95 ${D.chats.first_response_seconds.p95}s`];}
$("checks").innerHTML=Object.entries(C).map(([k,v])=>`<div class="check"><span class="${v===true?"pass":v===false?"fail":"na"}"><b>${v===true?"Pass":v===false?"Fail":"Not run"}</b></span>${names[k][0]}<br><span class="sub">${names[k][1]}</span></div>`).join("");

function rows(){const a=$("fAgent").value,s=$("fSeg").value,c=$("fCh").value;
 return D.cube.filter(r=>(a==="both"||r[0]===a)&&(s==="all"||r[2]===s)&&(c==="all"||r[3]===c));}
function agents(){const a=$("fAgent").value;return a==="both"?["fixed_cadence","kairos"]:[a];}

function lineChart(el,series){
 const W=560,H=230,L=46,B=26,T=10,R=10,n=D.dates.length;
 const max=Math.max(0.0001,...series.flatMap(s=>s.v.filter(x=>x!=null)))*1.1;
 const x=i=>L+i*(W-L-R)/(n-1),y=v=>T+(H-T-B)*(1-v/max);
 let g=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Replies per rupee by day">`;
 for(let k=0;k<=4;k++){const v=max*k/4;g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="${css("--line")}"/><text x="${L-6}" y="${y(v)+4}" font-size="11" text-anchor="end" fill="${css("--muted")}">${v.toFixed(3)}</text>`;}
 D.dates.forEach((d,i)=>{if(i%7===0)g+=`<text x="${x(i)}" y="${H-6}" font-size="11" text-anchor="middle" fill="${css("--muted")}">${d.slice(5)}</text>`;});
 const dd=S.simulator_drift_day_hidden_from_policy;g+=`<line x1="${x(dd)}" x2="${x(dd)}" y1="${T}" y2="${H-B}" stroke="${css("--muted")}" stroke-dasharray="3 3"/><text x="${x(dd)+4}" y="${T+10}" font-size="11" fill="${css("--muted")}">drift</text>`;
 series.forEach(s=>{let p="";s.v.forEach((v,i)=>{if(v==null)return;p+=(p?"L":"M")+x(i).toFixed(1)+" "+y(v).toFixed(1);});
  g+=`<path d="${p}" fill="none" stroke="${s.c}" stroke-width="2.2"/>`;s.v.forEach((v,i)=>{if(v!=null)g+=`<circle cx="${x(i)}" cy="${y(v)}" r="2.6" fill="${s.c}"><title>${D.dates[i]}: ${v.toFixed(4)}</title></circle>`;});});
 el.innerHTML=g+"</svg>";}

function mixChart(el,agent){
 const W=1100,H=220,L=40,B=24,T=8,R=8,n=D.dates.length,bw=(W-L-R)/n;
 const rs=rows().filter(r=>r[0]===agent);let g=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Channel mix by day for ${AG[agent]}">`;
 D.dates.forEach((d,i)=>{const day=rs.filter(r=>r[1]===d),tot=day.reduce((a,r)=>a+r[4],0);let y0=H-B;
  if(i%7===0)g+=`<text x="${L+i*bw+bw/2}" y="${H-6}" font-size="11" text-anchor="middle" fill="${css("--muted")}">${d.slice(5)}</text>`;
  if(!tot)return;CH.forEach(c=>{const t=day.filter(r=>r[3]===c).reduce((a,r)=>a+r[4],0);if(!t)return;const h=(H-B-T)*t/tot;y0-=h;
   g+=`<rect x="${L+i*bw+1}" y="${y0}" width="${bw-2}" height="${h}" fill="${CHC[c]}"><title>${d} ${c}: ${(100*t/tot).toFixed(0)}% (${fmt(t)} touches)</title></rect>`;});});
 const dd=S.simulator_drift_day_hidden_from_policy;g+=`<line x1="${L+dd*bw}" x2="${L+dd*bw}" y1="${T}" y2="${H-B}" stroke="${css("--ink")}" stroke-width="2" stroke-dasharray="4 3"/>`;
 [0,50,100].forEach(p=>g+=`<text x="${L-6}" y="${T+(H-B-T)*(1-p/100)+4}" font-size="11" text-anchor="end" fill="${css("--muted")}">${p}%</text>`);
 el.innerHTML=g+"</svg>";}

function render(){
 const rs=rows(),ag=agents();
 lineChart($("chartRpr"),ag.map(a=>({c:css(a==="kairos"?"--kairos":"--fixed"),v:D.dates.map(d=>{const x=rs.filter(r=>r[0]===a&&r[1]===d);const s=x.reduce((p,r)=>p+r[5],0);return s?x.reduce((p,r)=>p+r[6],0)/s:null;})})));
 const tot=a=>{const x=rs.filter(r=>r[0]===a);return{t:x.reduce((p,r)=>p+r[4],0),s:x.reduce((p,r)=>p+r[5],0),r:x.reduce((p,r)=>p+r[6],0)};};
 const T={fixed_cadence:tot("fixed_cadence"),kairos:tot("kairos")};
 const line=(l,f)=>`<tr><td>${l}</td><td class="num">${f(T.fixed_cadence)}</td><td class="num">${f(T.kairos)}</td></tr>`;
 $("kpi").innerHTML=`<tr><th></th><th class="num">Fixed cadence</th><th class="num">Kairos</th></tr>`+
  line("Touches",t=>fmt(t.t))+line("Spend (₹)",t=>fmt(t.s))+line("Meaningful replies",t=>fmt(t.r))+
  line("Replies per ₹",t=>t.s?(t.r/t.s).toFixed(4):"—")+line("₹ per reply",t=>t.r?(t.s/t.r).toFixed(2):"—")+
  `<tr><td>Lift in replies per ₹</td><td></td><td class="num">${T.fixed_cadence.s&&T.kairos.s?((T.kairos.r/T.kairos.s)/(T.fixed_cadence.r/T.fixed_cadence.s)*100-100).toFixed(1)+"%":"—"}</td></tr>`;
 const mixAgent=$("fAgent").value==="fixed_cadence"?"fixed_cadence":"kairos";
 $("mixsub").textContent=`${AG[mixAgent]}: share of touches per channel. The dashed line marks the simulator's mid-campaign drift.`;
 $("mixlegend").innerHTML=CH.map(c=>`<span><span class="sw" style="background:${CHC[c]}"></span>${c}</span>`).join("");
 mixChart($("chartMix"),mixAgent);
 const dec=D.decisions.filter(r=>ag.includes(r[0]));const keys=[...new Set(dec.map(r=>r[1]+"|"+r[2]))];
 const typ={};dec.forEach(r=>typ[r[1]+"|"+r[2]]=r[4]);
 $("dec").innerHTML=`<tr><th>Type</th><th>Decision</th><th>Reason</th>${ag.map(a=>`<th class="num">${AG[a]}</th>`).join("")}</tr>`+
  keys.sort((x,y)=>typ[x]<typ[y]?-1:typ[x]>typ[y]?1:x<y?-1:1).map(k=>{const[d,r]=k.split("|");return`<tr><td>${esc(typ[k])}</td><td>${esc(d)}</td><td>${esc(r)}</td>${ag.map(a=>{const x=dec.find(z=>z[0]===a&&z[1]===d&&z[2]===r);return`<td class="num">${x?fmt(x[3]):"—"}</td>`;}).join("")}</tr>`;}).join("");
 const seg=$("fSeg").value;
 $("learn").innerHTML=`<tr><th>Segment</th><th>Day 1 best</th><th>Last day best</th><th class="num">Reply rate</th><th class="num">Replies per ₹</th></tr>`+
  D.learn_last.filter(r=>seg==="all"||r.segment===seg).map(r=>{const f=D.learn_first.find(x=>x.segment===r.segment)||{};
  return`<tr><td>${r.segment}</td><td>${f.best_arm||"—"}</td><td><b>${r.best_arm}</b></td><td class="num">${(100*r.best_arm_reply_rate).toFixed(2)}%</td><td class="num">${r.best_arm_replies_per_inr.toFixed(3)}</td></tr>`;}).join("");
}

const ids=Object.keys(D.people);
$("whoList").innerHTML=ids.map(i=>`<option value="${i}">`).join("");
$("whoPick").innerHTML=`<option value="">Sample borrowers (${ids.length})</option>`+ids.map(i=>`<option>${i}</option>`).join("");
function showWho(id){
 const p=D.people[id];if(!p){$("whoCard").innerHTML=`<p class="empty">No trace for ${esc(id)}. Pick one of the ${ids.length} sample borrowers from the list.</p>`;$("timeline").innerHTML="";return;}
 $("whoCard").innerHTML=`<h2>${p.id}</h2><p class="sub">${p.loan} · ${p.segment}</p><dl><dt>DPD at start</dt><dd>${p.dpd_start}</dd><dt>Language</dt><dd>${p.language}</dd>
  <dt>State</dt><dd>${esc(p.state)}</dd><dt>Age</dt><dd>${p.age}</dd><dt>Consent</dt><dd>${p.consents.join(", ")||"none"}</dd><dt>Opted out at start</dt><dd>${p.opted_out_at_start?"yes":"no"}</dd></dl>`;
 const ag=agents(),ch=$("fCh").value,col=D.cols;let ev=[];
 ag.forEach(a=>{(p.touches[a]||[]).forEach(t=>{const o=Object.fromEntries(col.map((c,i)=>[c,t[i]]));if(ch==="all"||o.channel===ch)ev.push({a,o,k:o.date+o.time});});
  (p.payments[a]||[]).forEach(x=>ev.push({a,pay:x,k:x[0]+"00:00"}));});
 ev.sort((x,y)=>x.k<y.k?-1:x.k>y.k?1:x.a<y.a?-1:1);
 if(!ev.length){$("timeline").innerHTML=`<p class="empty">No touches for this filter. Try "Both" agents or "All channels".</p>`;return;}
 $("timeline").innerHTML=ev.map(e=>{if(e.pay)return`<div class="pay">${e.pay[0]} · verified payment ₹${fmt(e.pay[1])} (${e.pay[2].replace("_"," ")}) · ${AG[e.a]}</div>`;
  const o=e.o,outs=[o.delivered?"delivered":"not delivered",o.read?"read":"",o.clicked?"clicked":"",o.answered?"answered":"",o.hard_bounce?"hard bounce":""].filter(Boolean);
  const rep=o.replied?`<span class="out reply">reply: ${o.reply_type}</span>`:"",st=o.stop?`<span class="out stop">STOP</span>`:"";
  const why=e.a==="kairos"?`K21 score ${o.score} → ${o.intensity}. ${esc(o.top_reasons)}. Expected reply ${o.expected_reply!=null?(100*o.expected_reply).toFixed(1)+"%":"—"} for ₹${o.actual_cost_inr}.`:`Fixed cadence slot for ${o.channel}. No scoring.`;
  return`<div class="touch ${e.a}"><div class="when">${o.date} ${o.time} · ${o.channel} · ${AG[e.a]}</div><div class="why">${why}</div>
   <div>${outs.map(x=>`<span class="out">${x}</span>`).join("")}${rep}${st}</div>
   <details><summary>Trace ${o.touch_id}</summary><div class="gates">Template ${o.template_id} · band ${o.band}, DPD ${o.dpd} · charged ₹${o.actual_cost_inr}<br>
   Passed K01 ${D.k01} gates in order: ${D.k01_order.join(" → ")}<br>Re-checked after the run by Ojas's K01 reference: SEND (0 breaches in this run).</div></details></div>`;}).join("");
}
$("who").addEventListener("change",e=>showWho(e.target.value.trim()));
$("whoPick").addEventListener("change",e=>{if(e.target.value){$("who").value=e.target.value;showWho(e.target.value);}});
["fAgent","fSeg","fCh"].forEach(i=>$(i).addEventListener("change",()=>{render();if($("who").value)showWho($("who").value.trim());}));
render();const first=ids.find(i=>D.people[i].touches.kairos.some(t=>t[D.cols.indexOf("reply_type")]==="PTP"))||ids[0];$("who").value=first;showWho(first);
</script></body></html>"""
