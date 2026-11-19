#!/usr/bin/env python3
"""Insert (or refresh) the "New original score" rows at the top of the Music options section of
audio/check.html, each with an <audio> player and a "Preview with voice" button that plays the track
with the Neha voice-over lines at their timeline.json start times (music ducked to 30% under a line).

    python3 film/tools/score_page.py
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "..", "audio", "check.html")
ORIG = os.path.join(HERE, "..", "music", "original")

VARIANTS = [  # (file stem, label, details, voice timeline relative to audio/) in page order
    ("v3_cinematic_trailer", 'V3 "Cinematic Trailer"',
     "≈99 BPM · D minor → C minor (Under the hood) → D major · hits locked to the film · 4:07.7", "film_timeline.json"),
    ("v3_trailer_short", 'V3 "Cinematic Trailer" (short cut)',
     "same score cut to the 2-minute film · 1:59.3", "film_timeline_short.json"),
    ("v3_tech", 'V3 "Under the hood" (technical film)',
     "≈99 BPM · C minor → E♭ major (human in the loop) → C major · synth arps, sub pulse, ticking hats · 2:15.6",
     "film_timeline_tech.json"),
    ("v1_uplifting", 'V1 "Uplifting"', "112 BPM · D major · piano, strings, soft four-on-the-floor · 2:20 (old timeline)",
     "vo_neha/timeline.json"),
    ("v2_modern_tech", 'V2 "Modern tech"', "118 BPM · A minor → C major · e-piano, synth bass, claps · 2:20 (old timeline)",
     "vo_neha/timeline.json"),
]
START, END = "<!-- original-score:start -->", "<!-- original-score:end -->"
SSTART, SEND = "<!-- original-score-js:start -->", "<!-- original-score-js:end -->"

rows = []
for stem, label, info, tl in VARIANTS:
    if not os.path.exists(os.path.join(ORIG, stem + ".m4a")):
        continue
    src = f"../music/original/{stem}.m4a"
    rows.append(f'<div class=r><span class=n>{label.split()[0]}</span><div class=c><div class=t>New original score: {label} '
                f'<small>· {info}</small></div><audio controls preload=none src="{src}"></audio>'
                f'<p style="margin:6px 0 0"><button class=pv data-src="{src}" data-tl="{tl}">▶ Preview with voice</button></p></div></div>')
block = (START + '<p>Original compositions made for this film (Apple General MIDI instruments plus synthesised '
         'impacts; see music/original/README.md). "Preview with voice" plays the film\'s voice-over and dialogue '
         'on top, with the music ducked to 30% under each line.</p>' + "".join(rows) + END)

js = SSTART + r"""<script>(()=>{const tls={};let cur=null;
const load=async u=>tls[u]||(tls[u]=await (await fetch(u)).json());
const clip=l=>l.src?l.src.replace(/^audio\//,''):'vo_neha/'+l.line+'.wav';
document.querySelectorAll('button.pv').forEach(b=>b.onclick=async()=>{
 const was=cur&&cur.btn===b; if(cur)cur.stop(); if(was)return;
 const t=await load(b.dataset.tl||'vo_neha/timeline.json'); document.querySelectorAll('audio').forEach(x=>x.pause());
 const m=new Audio(b.dataset.src), lines=t.lines.map(l=>({...l,a:new Audio(clip(l)),on:false}));
 let raf=0,vol=1; const st={btn:b,stop(){cancelAnimationFrame(raf);m.pause();lines.forEach(l=>l.a.pause());b.textContent='▶ Preview with voice';cur=null}};
 cur=st; b.textContent='■ Stop preview';
 const tick=()=>{const ct=m.currentTime; let talking=false;
  for(const l of lines){ if(!l.on&&ct>=l.start&&ct<l.end){l.on=true;l.a.currentTime=Math.max(0,ct-l.start);l.a.play()}
   if(ct>=l.start-0.08&&ct<l.end+0.1)talking=true }
  vol+=((talking?0.3:1)-vol)*0.18; m.volume=Math.max(0,Math.min(1,vol));
  if(m.ended){st.stop();return} raf=requestAnimationFrame(tick)};
 m.onended=()=>st.stop(); await m.play(); raf=requestAnimationFrame(tick);
});})();</script>""" + SEND

html = open(PAGE, encoding="utf-8").read()
if START in html:
    html = re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: block, html, flags=re.S)
else:
    html = html.replace("<h2>Music options</h2>", "<h2>Music options</h2>" + block, 1)
if SSTART in html:
    html = re.sub(re.escape(SSTART) + ".*?" + re.escape(SEND), lambda _: js, html, flags=re.S)
else:
    html = html.rstrip("\n") + "\n" + js + "\n"
open(PAGE, "w", encoding="utf-8").write(html)
print(f"check.html: {len(rows)} original score rows")
