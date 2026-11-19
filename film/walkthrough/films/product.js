/* Film A: "Kairos in action" (product film). Data for comp/engine.js. Times in seconds.
 * Music: "Corporate Inspirational" (AtlasAudio, Pixabay), 140 BPM, bar = 1.714 s, downbeat d(n) = 0.24 + 1.714 n.
 * Played 0 → 109.93 (end of the lift phrase), hard stop, lockup in silence to 115.
 * Every picture of the product is a real capture of the running pages (SIMULATED data). When the film talks about
 * one component, that component is in focus (crisp, lifted) and the rest of the page blurs back toward cream, so
 * overlays sit on calm space and never on live text.
 */
(function () {
  var HARD_STOP = 109.93, END = 115.0;
  var OUT = "localhost/demo/journey/outbound", IN = "localhost/demo/journey/inbound", LIVE = "localhost/demo/live";
  var K = 1640 / 1440; // capture px → stage px at zoom 1 (1440-wide captures in a 1640-wide window)

  // zoom that fits a subject rect inside the frame (wf of the width, hf of the height), capped
  function fitz(r, wf, hf, cap) { return Math.min((wf || 0.8) * 1920 / (r[2] * K), (hf || 0.6) * 1080 / (r[3] * K), cap || 2.4); }
  function cam(t, r, o) { o = o || {}; return { t: t, u: r[0] + r[2] / 2, v: r[1] + r[3] / 2, z: o.z || fitz(r, o.wf, o.hf, o.cap), ax: o.ax, ay: o.ay != null ? o.ay : 440 }; }
  function bbox(rs) { var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; rs.forEach(function (r) { x0 = Math.min(x0, r[0]); y0 = Math.min(y0, r[1]); x1 = Math.max(x1, r[0] + r[2]); y1 = Math.max(y1, r[1] + r[3]); }); return [x0, y0, x1 - x0, y1 - y0]; }
  function lbl(i) { return "v" + String(i).padStart(2, "0"); }

  // ---- outbound (Vikram) subjects, from the capture's own rects (step labels checked against the frames)
  var PICK = [318, 360, 259, 187], EV02 = [349, 333, 658, 162], EV03 = [349, 333, 658, 242], EV10 = [349, 333, 658, 260], EV17 = [349, 333, 658, 126];
  var PIPE_B = [547, 121, 702, 88], SWITCH = [191, 121, 346, 88];
  var PAID = [[13, 218, 640, 39], [13, 267, 312, 249]];
  function fast(a, b, t, dt) { var o = []; for (var i = a; i <= b; i++) o.push({ at: t + (i - a) * dt, label: lbl(i) }); return o; }

  // ---- inbound subjects
  var PHONE = [19, 130, 360, 431];
  function thead(h) { return [414, 200, 523, h]; }
  var QUEUE = [991, 178, 410, 168], CONVO = [972, 357, 448, 313];
  var phoneCam = function (t) { return cam(t, PHONE, { z: 1.95, ax: 640, ay: 520 }); };
  var headCam = function (t, h) { return cam(t, thead(h), { wf: 0.78, hf: 0.5, cap: 2.3, ay: 430 }); };

  var layers = [
    // ---- 1 · hook: Mumbai at night, then the fixed schedule's real numbers (seed-42 run)
    { kind: "video", t0: -0.1, t1: 3.67, src: "../assets/broll/city_aerial_mumbai_night.mp4", from: 4.0, filter: "saturate(.9) contrast(1.02)", enter: { dur: 0.01 }, exit: { dur: 0 } },
    { kind: "dots", t0: 3.67, t1: 7.1, n: 1262, hi: 23, tHi: 5.38, cols: 61, gap: 30, fill: 0.9, enter: { dur: 0.2 }, exit: { dur: 0.3 } },
    { kind: "words", t0: 3.67, t1: 7.1, y: 400, size: 150, words: [
      { t: 3.67, html: "1,262 messages." },
      { t: 5.38, html: "<em>23</em> replies." }] },
    { kind: "tag", t0: 3.9, t1: 7.0, pos: "b", text: "Fixed schedule · SIMULATED run, seed 42" },

    // ---- 2 · promise lockup
    { kind: "logo", t0: 7.1, t1: 9.95, y: 380, stagger: 0.07, exit: { dur: 0.3 } },
    { kind: "headline", t0: 7.45, t1: 9.95, pos: "c", y: 560, text: "Collections that decide who, when and how.", size: 60, exit: { dur: 0.35 } },

    // ---- 3–6 · journey 1: Vikram (outbound page, real engines)
    { kind: "win", cap: "ov", url: OUT, t0: 9.95, t1: 44.8, enter: { type: "rise", dur: 0.7 }, exit: { type: "fade", dur: 0.35 },
      frames: [{ at: 0, label: "picker" }, { at: 11.9, label: "v00" }, { at: 12.35, label: "v02" }, { at: 20.7, label: "v03" }]
        .concat(fast(4, 9, 24.5, 0.4)).concat([{ at: 26.9, label: "v10" }, { at: 31.0, label: "v11" }, { at: 31.4, label: "v12" }])
        .concat(fast(13, 16, 33.7, 0.3)).concat([{ at: 34.95, label: "v17" }])
        .concat(fast(18, 40, 38.8, 0.068)).concat([{ at: 40.4, label: "v41" }]),
      cam: [{ t: 0, full: true }, cam(10.9, PICK, { ay: 500 }), { t: 12.2, full: true },
            cam(13.2, EV02, { ay: 420 }), cam(20.6, EV03, { ay: 430 }), cam(24.4, PIPE_B, { ay: 420 }),
            cam(27.0, EV10, { ay: 430 }), cam(31.4, SWITCH, { ay: 420 }), cam(33.6, PIPE_B, { ay: 420 }),
            cam(35.0, EV17, { ay: 420 }), { t: 38.6, full: true }, cam(40.4, bbox(PAID), { ay: 430 })],
      focus: [{ t0: 10.9, t1: 12.3, rect: PICK, pad: 2 }, { t0: 12.3, t1: 20.6, rect: EV02 }, { t0: 20.6, t1: 24.4, rect: EV03 },
              { t0: 24.4, t1: 27.0, rect: PIPE_B }, { t0: 27.0, t1: 31.4, rect: EV10 }, { t0: 31.4, t1: 33.6, rect: SWITCH },
              { t0: 33.6, t1: 35.0, rect: PIPE_B }, { t0: 35.0, t1: 38.6, rect: EV17 }, { t0: 40.4, t1: 44.8, rects: PAID }],
      cursor: [{ t: 10.7, at: [720, 640], move: 0.01 }, { t: 11.8, ev: "card_vikram", click: true, move: 0.8 },
               { t: 38.4, at: [480, 640], move: 0.01 }, { t: 38.8, at: [69, 775], click: true, move: 0.38 }],
      cursorHold: 0.4 },
    { kind: "callout", t0: 15.6, t1: 20.3, pos: "bl", text: "Decided, not scheduled." },
    { kind: "callout", t0: 21.6, t1: 24.3, pos: "bl", text: "Checked twice." },
    { kind: "callout", t0: 31.9, t1: 34.6, pos: "bl", text: "SMS → WhatsApp." },
    { kind: "callout", t0: 35.6, t1: 38.4, pos: "bl", text: "Promise noted. Kairos goes quiet." },
    { kind: "callout", t0: 41.0, t1: 44.6, pos: "br", html: "₹0.65 spent. <em>₹7,409</em> back." },

    // ---- 7–8 · the team: approve a held call; an override at 8 pm is blocked
    { kind: "win", cap: "aarav", url: OUT, t0: 44.8, t1: 55.1, enter: { type: "fade", dur: 0.35 }, exit: { type: "fade", dur: 0.3 },
      frames: [{ at: 0, label: "held" }, { at: 47.05, label: "approved" }, { at: 48.9, label: "set2000" }, { at: 50.35, label: "blocked" }],
      cam: [cam(44.8, [1044, 448, 368, 49], { ay: 470 }), cam(47.1, [349, 659, 658, 67], { ay: 470 }),
            cam(48.5, [1040, 462, 375, 190], { ay: 470 }), cam(50.5, [349, 333, 658, 187], { ay: 420 })],
      focus: [{ t0: 44.8, t1: 47.1, rect: [1044, 448, 368, 49], pad: 4 }, { t0: 47.1, t1: 48.5, rect: [349, 659, 658, 67] },
              { t0: 48.5, t1: 50.5, rect: [1040, 462, 375, 190] }, { t0: 50.5, t1: 55.1, rect: [349, 333, 658, 187] }],
      cursor: [{ t: 45.4, at: [1150, 560], move: 0.01 }, { t: 47.0, ev: "approve_btn", click: true, move: 0.7 },
               { t: 49.3, ev: "otm", click: true, move: 0.6 }, { t: 50.3, ev: "apply_btn", click: true, move: 0.6 }],
      cursorHold: 0.4 },
    { kind: "callout", t0: 45.6, t1: 47.0, pos: "bl", text: "You approve the call." },
    { kind: "callout", t0: 51.3, t1: 54.8, pos: "bl", text: "Blocked. Even for you." },

    // ---- 9 · breath on the music's lift
    { kind: "headline", z: 300, t0: 55.15, t1: 59.4, pos: "c", text: "Now the borrower writes first.", size: 100, exit: { dur: 0.35 } },

    // ---- 10–13 · journey 2: inbound WhatsApp (real CM-03 engine). The borrower types; the operator clicks.
    { kind: "win", cap: "in", url: IN, t0: 55.05, t1: 87.7, enter: { type: "fade", dur: 0.5 }, exit: { type: "fade", dur: 0.4 },
      frames: [{ at: 0, label: "tabA" }, { at: 59.7, label: "emi" }, { at: 62.0, label: "otp" }, { at: 63.4, label: "paylink" },
               { at: 65.3, label: "fake" },
               { at: 71.9, label: "tabB" }, { at: 72.4, label: "hardship" }, { at: 74.9, label: "takeoverB" }, { at: 76.3, label: "suggested" }, { at: 77.2, label: "agentsent" },
               { at: 80.5, label: "tabA2" }, { at: 81.0, label: "stop" }],
      cam: [{ t: 0, full: true }, phoneCam(59.2), headCam(60.6, 211), phoneCam(62.9),
            phoneCam(65.0), headCam(66.6, 234),
            phoneCam(71.8), headCam(73.1, 180), cam(74.0, QUEUE, { ay: 470 }), cam(75.2, CONVO, { hf: 0.7, ay: 470 }), phoneCam(77.5),
            phoneCam(80.4), headCam(82.0, 180)],
      focus: [{ t0: 55.05, t1: 59.2, rects: [] }, { t0: 59.2, t1: 60.6, rect: PHONE }, { t0: 60.6, t1: 62.9, rect: thead(211) }, { t0: 62.9, t1: 65.0, rect: PHONE },
              { t0: 65.0, t1: 66.6, rect: PHONE }, { t0: 66.6, t1: 71.8, rect: thead(234) },
              { t0: 71.8, t1: 73.1, rect: PHONE }, { t0: 73.1, t1: 74.0, rect: thead(180) }, { t0: 74.0, t1: 75.2, rect: QUEUE }, { t0: 75.2, t1: 77.5, rect: CONVO },
              { t0: 77.5, t1: 80.4, rect: PHONE }, { t0: 80.4, t1: 82.0, rect: PHONE }, { t0: 82.0, t1: 87.7, rect: thead(180) }],
      cursor: [{ t: 74.2, at: [1150, 330], move: 0.01 }, { t: 74.85, ev: "takeoverB_btn", click: true, move: 0.5 },
               { t: 76.25, ev: "suggest_btn", click: true, move: 0.6 }, { t: 77.15, ev: "sendB_btn", click: true, move: 0.5 }],
      cursorHold: 0.3 },
    { kind: "callout", t0: 60.9, t1: 62.8, x: 1120, y: 780, maxw: 700, text: "No details before OTP." },
    { kind: "callout", t0: 67.6, t1: 71.6, pos: "bl", text: "Fake screenshot. Caught." },
    { kind: "callout", t0: 75.5, t1: 77.4, x: 96, y: 80, maxw: 620, text: "A person takes over." },
    { kind: "callout", t0: 82.8, t1: 87.4, pos: "bl", text: "STOP. Every channel." },

    // ---- 14 · results: the real console at the end of a full run, then the proof card
    { kind: "win", cap: "live", url: LIVE, t0: 87.7, t1: 93.1, enter: { type: "rise", dur: 0.6 }, exit: { type: "fade", dur: 0.4 },
      frames: [{ at: 0, label: "end_overview" }],
      cam: [{ t: 0, full: true }, cam(88.5, [1129, 105, 285, 145], { ay: 470 })],
      focus: [{ t0: 88.5, t1: 93.1, rect: [1129, 105, 285, 145] }] },
    { kind: "stats", t0: 93.0, t1: 99.7, y: 300, stagger: 0.12, exit: { dur: 0.4 }, items: [
      { pre: "≈", post: "×", from: 0, to: 2, count: 1.0, label: "money back per ₹ spent", fine: "vs a fixed-schedule stand-in" },
      { from: 0, to: 0, label: "rule breaches", fine: "every message checked twice" },
      { from: 0, to: 49, post: "<span style='font-size:.5em;color:#57534E'>/49</span>", count: 1.0, label: "attacks blocked", fine: "on our own red-team suite" }] },
    { kind: "fine", t0: 93.4, t1: 99.7, x: 0, y: 840, html: "<div style='width:1920px;text-align:center'>SIMULATED · synthetic borrowers · 3 unseen seeds · same budget and caps</div>" },

    // ---- 15 · the console, calmly: live activity in plain words, then the people who decide
    { kind: "win", cap: "live", url: LIVE, t0: 99.6, t1: HARD_STOP, enter: { type: "rise", dur: 0.6 }, exit: { type: "fade", dur: 0 },
      frames: [{ at: 99.6, label: "run", play: 6, from: 42 }],
      cam: [cam(99.6, [990, 265, 424, 330], { hf: 0.8, ay: 520 }), cam(104.6, [230, 597, 743, 193], { ay: 430 })],
      focus: [{ t0: 99.6, t1: 104.6, rect: [990, 265, 424, 330] }, { t0: 104.6, t1: HARD_STOP, rect: [230, 597, 743, 193] }] },
    { kind: "callout", t0: 105.4, t1: 109.8, pos: "tl", text: "People decide the hard cases." },

    // ---- 16 · hard stop → lockup in silence
    { kind: "logo", t0: HARD_STOP - 0.1, t1: END, y: 360, line: "The right message, at the right time. Or none at all.", lineSize: 56, stagger: 0.08, enterDur: 0.22,
      credits: "Music: “Corporate Inspirational” by AtlasAudio (Pixabay licence) · Voice: Sarvam bulbul:v3 (Neha), AI-generated<br>Opening shot: Pexels · Every product screen is the real software · All data SIMULATED", creditsY: 900, exit: { dur: 0 } }
  ];

  window.FILM = {
    duration: END,
    captures: { ov: "captures/out_vikram", aarav: "captures/out_aarav", "in": "captures/inbound2", live: "captures/live_run" },
    layers: layers
  };
})();
