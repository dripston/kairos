/* Film B: "How Kairos works" (technical film). Data for comp/engine.js. Times in seconds.
 * Music: "Inspiring Technology" (The_Mountain, Pixabay), 80 BPM, bar = 3.0 s, phrase = 12 s. Hard stop at 108.0.
 * One diagram, never cleared; one colour per subsystem; every cut-in is a match-cut through its node and
 * focuses one real component (the rest of the page blurs back), so overlays never sit on live text.
 */
(function () {
  var C = { data: "#2563EB", learn: "#0F766E", rules: "#C2410C", act: "#7C3AED", understand: "#BE185D", people: "#15803D" };
  var X = [60, 540, 1020, 1500, 1980], NW = 420, NH = 180, ROWS = [40, 400, 780];
  var WW = 2460, WH = 1010;
  function node(id, col, row, title, sub, color, t, cap) { return { id: id, x: X[col], y: ROWS[row], w: NW, h: NH, title: title, sub: sub, color: color, t: t, cap: cap }; }
  var HARD_STOP = 108.0, END = 113.0;

  var nodes = [
    node("rec", 0, 1, "K03 · Record", "one per borrower", C.data, 5.5),
    node("churn", 1, 1, "K27 · Churn", "re-sorts daily", C.learn, 18.4),
    node("learn", 2, 1, "K09 · Learner", "channel × hour", C.learn, 19.2),
    node("ald", 3, 1, "K06 · Aladdin", "checks twice", C.rules, 30.4, "≈ safety shield · Alshiekh et al. 2018"),
    node("exec", 4, 1, "Executor", "sends on time", C.act, 42.4),
    node("chan", 4, 2, "Channels", "per-channel APIs", C.act, 43.1),
    node("hook", 3, 2, "Webhooks", "receipts back", C.act, 43.8),
    node("intent", 2, 2, "Intent ladder", "rules → model", C.understand, 54.4, "≈ reject option · Chow 1970"),
    node("people", 3, 0, "People", "can step in", C.people, 66.4),
    node("out", 1, 2, "Outcomes", "what happened", C.data, 78.4)
  ];
  var edges = [
    { id: "e1", from: "rec", to: "churn", t: 18.3 }, { id: "e2", from: "churn", to: "learn", t: 19.1 },
    { id: "e3", from: "learn", to: "ald", t: 30.3 }, { id: "e4", from: "ald", to: "exec", t: 42.3 },
    { id: "e5", from: "exec", to: "chan", t: 43.0 }, { id: "e6", from: "chan", to: "hook", t: 43.7 },
    { id: "e7", from: "hook", to: "intent", t: 54.3 }, { id: "e8", from: "people", to: "ald", t: 66.3 },
    { id: "e9", from: "intent", to: "out", t: 78.3 }, { id: "e10", from: "out", to: "learn", t: 78.9 }
  ];
  var LOOP = ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e9", "e10"];

  // cut-ins: [t0, t1, node]
  var cuts = [[9.6, 16.8, "rec"], [21.4, 29.0, "learn"], [33.4, 41.0, "ald"], [45.4, 53.0, "hook"], [57.4, 65.0, "intent"], [69.4, 77.0, "people"], [83.4, 101.6, "learn"]];

  var dia = {
    id: "dia", kind: "diagram", t0: 5.0, t1: HARD_STOP, enter: { dur: 0.6 }, exit: { dur: 0 }, ww: WW, wh: WH,
    nodes: nodes, edges: edges,
    away: cuts.map(function (c) { return [c[0] - 0.1, c[1] - 0.05]; }).concat([[101.5, 105.6]]),
    cam: [
      { t: 0, nodes: ["rec"], z: 1.3 },
      { t: 17.4, nodes: ["churn", "learn"], z: 1.25, dur: 1.1 },
      { t: 29.4, nodes: ["learn", "ald"], z: 1.25, dur: 1.1 },
      { t: 41.4, nodes: ["exec", "chan", "hook"], z: 1.15, dur: 1.1 },
      { t: 53.4, nodes: ["hook", "intent"], z: 1.2, dur: 1.1 },
      { t: 65.4, nodes: ["people", "ald"], z: 1.2, dur: 1.1 },
      { t: 77.4, full: true, z: 0.74, dur: 1.2 },
      { t: 105.5, full: true, z: 0.74, dur: 0.1 }
    ],
    focus: [
      { t: 5.5, nodes: ["rec"] }, { t: 18.4, nodes: ["churn", "learn"] }, { t: 30.4, nodes: ["ald"] },
      { t: 42.4, nodes: ["exec", "chan", "hook"] }, { t: 54.4, nodes: ["intent"] }, { t: 66.4, nodes: ["people", "ald"] },
      { t: 78.4, nodes: ["*"] }
    ],
    pulses: [{ t: 79.6, path: LOOP, speed: 1100, dwell: 0.05 }, { t: 105.8, path: LOOP, speed: 1700, dwell: 0.02 }]
  };

  function cutWin(c, spec) {
    var L = { kind: "win", t0: c[0], t1: c[1], enter: { type: "fromRect", dia: "dia", node: c[2], dur: 0.75 }, exit: { type: "toRect", dia: "dia", node: c[2], dur: 0.7 } };
    for (var k in spec) L[k] = spec[k];
    return L;
  }
  var OUT = "localhost/demo/journey/outbound";
  var DETAIL = [349, 374, 658, 226];       // "What just happened" card, Technical view
  var JSON_BLOCK = [358, 372, 640, 212];    // its raw event JSON once scrolled
  function focusCam(t, r, z, ay) { return { t: t, u: r[0] + r[2] / 2, v: r[1] + r[3] / 2, z: z, ay: ay || 430 }; }

  var layers = [
    // 1 · chapter headline (in from frame 0, so no empty frame)
    { kind: "headline", t0: -0.25, t1: 5.3, pos: "c", text: "How Kairos works.", sub: "One decision per borrower, per day. Every step checked.", size: 112, stagger: 0.15, exit: { dur: 0.45 } },
    dia,
    // 2 · K03 record: Vikram's record, focused
    cutWin(cuts[0], { cap: "ov", url: OUT, frames: [{ at: 0, label: "d1_churn_tech" }],
      cam: [{ t: 0, full: true }, focusCam(10.6, [13, 294, 312, 320], 2.3, 520)],
      focus: [{ t0: 10.6, t1: 16.6, ev: "t01_rec", pad: 4 }] }),
    { kind: "callout", t0: 11.4, t1: 16.3, x: 1150, y: 430, maxw: 640, text: "One record per borrower." },
    // 3 · K09 learner: posterior per channel × time, then the decision's raw payload
    cutWin(cuts[1], { cap: "ov", url: OUT, frames: [{ at: 0, label: "d1_learner_tech" }, { at: 25.4, label: "d1_learner_tech_scrolled" }],
      cam: [{ t: 0, full: true }, focusCam(22.4, DETAIL, 2.05), focusCam(25.3, JSON_BLOCK, 2.3)],
      focus: [{ t0: 22.4, t1: 25.4, rect: DETAIL }, { t0: 25.4, t1: 28.8, rect: JSON_BLOCK }] }),
    { kind: "callout", t0: 23.2, t1: 28.5, pos: "bl", text: "Learns from every reply." },
    // 4 · K06 Aladdin: plan-time check, then the send-time re-check (rule order, policy version)
    cutWin(cuts[2], { cap: "ov", url: OUT, frames: [{ at: 0, label: "d1_aladdin_tech_scrolled" }, { at: 37.4, label: "d1_recheck_tech_scrolled" }],
      cam: [{ t: 0, full: true }, focusCam(34.4, JSON_BLOCK, 2.3), focusCam(37.3, JSON_BLOCK, 2.36)],
      focus: [{ t0: 34.4, t1: 40.8, rect: JSON_BLOCK }] }),
    { kind: "callout", t0: 35.2, t1: 40.5, pos: "bl", text: "The rules decide." },
    // 5 · executor → channels → webhooks: the full system trace, counters climbing
    cutWin(cuts[3], { cap: "sys", url: "localhost/demo/live · System", frames: [{ at: cuts[3][0], label: "trace", play: 5, from: 8 }],
      cam: [{ t: 0, full: true }, focusCam(46.4, [410, 278, 322, 296], 2.45, 470), focusCam(49.6, [578, 295, 318, 258], 2.45, 470)],
      focus: [{ t0: 46.4, t1: 49.6, rect: [410, 278, 322, 296] }, { t0: 49.6, t1: 52.8, rect: [578, 295, 318, 258] }] }),
    { kind: "callout", t0: 47.2, t1: 52.5, pos: "bl", text: "Every receipt, once." },
    // 6 · intent ladder: rules decided; small model not needed; confidence 0.92
    cutWin(cuts[4], { cap: "ov", url: OUT, frames: [{ at: 0, label: "d3_intent_tech" }],
      cam: [{ t: 0, full: true }, focusCam(58.4, [349, 380, 658, 222], 2.05)],
      focus: [{ t0: 58.4, t1: 64.8, rect: [349, 380, 658, 222] }] }),
    { kind: "callout", t0: 59.2, t1: 64.5, pos: "bl", text: "Fails closed." },
    // 7 · people: approve a held call; an override at 8 pm is blocked by Aladdin; both land in the audit log
    cutWin(cuts[5], { cap: "aarav", url: OUT, frames: [{ at: 0, label: "held" }, { at: 71.25, label: "approved" }, { at: 73.0, label: "set2000" }, { at: 73.85, label: "blocked" }],
      cam: [{ t: 0, full: true }, { t: 70.0, ev: "appr_me", z: 2.3, ay: 460 }, { t: 72.6, rect: [1040, 462, 375, 190], z: 2.2, ay: 470 },
            { t: 74.2, u: 678, v: 426, z: 2.1, ay: 430 }],
      focus: [{ t0: 70.0, t1: 72.6, ev: "appr_me", pad: 6 }, { t0: 72.6, t1: 74.2, rect: [1040, 462, 375, 190] }, { t0: 74.2, t1: 76.8, rect: [349, 333, 658, 187] }],
      cursor: [{ t: 70.4, ev: "approve_btn", move: 0.01 }, { t: 71.2, ev: "approve_btn", click: true, move: 0.5 }, { t: 73.8, ev: "apply_btn", click: true, move: 0.7 }], cursorHold: 0.4 }),
    { kind: "callout", t0: 74.9, t1: 76.7, pos: "bl", text: "Even overrides are checked." },
    // 8–9 · the loop closes; real runs in the terminal (recorded 20 Nov; badges say how much each is sped up)
    { kind: "term", t0: cuts[6][0], t1: cuts[6][1], title: "kairos/backend — zsh", prompt: "%", rows: 17, maxCols: 90,
      enter: { type: "fromRect", dia: "dia", node: "learn", dur: 0.75 }, exit: { type: "toRect", dia: "dia", node: "learn", dur: 0.7 },
      cam: [{ t: 0, full: true }, { t: 84.4, u: 800, v: 330, z: 1.14, ay: 470 }],
      runs: [
        { rec: "determinism", at: 83.95, cadence: 0.012, fit: 1.2, keep: "^Matched|Verified payment \\(unique\\)|K01 cap breaches|K15 safety|Deterministic replay", hi: "IDENTICAL", badge: "638 s run · shown 500× faster" },
        { rec: "redteam", at: 88.0, cadence: 0.025, fit: 1.2, clear: true, keep: "PASS|FAIL|attacks:", hi: "49 PASS", badge: "3.7 s run" },
        { rec: "bench", at: 91.8, cadence: 0.025, fit: 0.6, clear: true, hi: "atomic decisions|cap breaches: 0", badge: "ledger benchmark · 15 s run, 15× faster" },
        { rec: "pytest", at: 94.6, cadence: 0.03, fit: 1.0, clear: true, hi: "passed", badge: "74 s run · shown 70× faster" },
        { rec: "ope", at: 97.2, cadence: 0.022, fit: 0.8, clear: true, keep: "^OPE|^replies per 1,000 decisions|^Greedy K09|^CN10|\\* = ground", hi: "^CN10", badge: "SIMULATED · 20 s run, 25× faster", prompt: "kairos/backend (research) %" }
      ] },
    { kind: "callout", t0: 85.9, t1: 87.9, pos: "br", text: "Same seed. Same hash." },
    { kind: "callout", t0: 89.9, t1: 91.7, pos: "br", text: "On our own red-team suite." },
    { kind: "callout", t0: 98.4, t1: 101.3, pos: "br", kicker: "Off-policy evaluation · SIMULATED", text: "From logs: 18.2. Truth: 18.4.", size: 60 },
    // 10 · built on research
    { kind: "callout", t0: 101.8, t1: 105.4, pos: "c", bare: true, text: "Built on research.", size: 76, stagger: 0.05,
      subHtml: '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 70px;margin-top:30px;font-size:36px;line-height:1.3;color:#57534E;text-align:left">' +
        "<div>Thompson · <i>Biometrika</i> 1933</div><div>Chapelle &amp; Li · NeurIPS 2011</div>" +
        "<div>Li et al. · WWW 2010</div><div>Badanidiyuru, Kleinberg &amp; Slivkins · FOCS 2013</div>" +
        "<div>Clayton &amp; Kaldor · <i>Biometrics</i> 1987</div><div>Abe et al. · KDD 2010</div>" +
        "<div>Yancey &amp; Settles · KDD 2020</div><div>Alshiekh et al. · AAAI 2018</div>" +
        "<div>Angelopoulos et al. · Learn then Test</div><div>Dudík, Langford &amp; Li · ICML 2011</div></div>", sub: " " },
    // 12 · hard stop → lockup in silence
    { kind: "logo", t0: HARD_STOP - 0.12, t1: END, y: 360, line: "Built to be trusted with every rupee.", lineSize: 60, stagger: 0.08,
      credits: "Music: “Inspiring Technology” by The_Mountain (Pixabay licence) · Voice: Sarvam bulbul:v3 (Neha), AI-generated<br>Every screen and terminal run is the real software · All data SIMULATED", creditsY: 900, exit: { dur: 0 } }
  ];

  window.FILM = {
    duration: END,
    captures: { ov: "captures/out_vikram", aarav: "captures/out_aarav", sys: "captures/live_system" },
    terms: { redteam: "term/redteam.json", bench: "term/bench.json", determinism: "term/determinism.json", pytest: "term/pytest.json", ope: "term/ope.json" },
    layers: layers
  };
})();
