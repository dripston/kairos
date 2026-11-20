/* Kairos walkthrough compositor.
 *
 * Deterministic: everything on screen is a pure function of the film time t. The renderer calls
 * `await seek(t)` for every frame and snapshots the page. No CSS transitions, no timers.
 *
 * A film (films/*.js) is data: a list of layers, each with a time window and named moves:
 *   win      a real capture in a browser (or phone-less app) window: frames, camera (auto-zoom), cursor,
 *            lift-out callouts, match-cut entries/exits
 *   term     a terminal window replaying a recorded real run (typing cadence, highlight, camera)
 *   video    full-bleed b-roll
 *   words    kinetic word stack
 *   headline chapter headline + spec line
 *   callout  short overlay (on a cream card) over the product
 *   tag      small label pill (SIMULATED …)
 *   diagram  the architecture world: nodes, edges, dolly camera, dimming, edge-trace pulses
 *   stats    proof cards with number counters
 *   logo     lockup
 * Craft numbers follow research/film_craft.md: entrances cubic-bezier(.23,1,.32,1), moves and camera
 * cubic-bezier(.77,0,.175,1) or an overdamped spring (zeta 1.3, 90% in ~317 ms), cursor spring 170/20,
 * nothing enters from below scale .95, stagger 30–80 ms, blur <= 20 px.
 */
(function () {
  "use strict";
  var W = 1920, H = 1080;
  var stage, FILM, layers = [], caps = {}, base = "../", FRESH = 0, QA = [];

  // ---------------------------------------------------------------- maths
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, s) { return a + (b - a) * s; }
  function bezier(x1, y1, x2, y2) {
    function a(p1, p2) { return 1 - 3 * p2 + 3 * p1; }
    function b(p1, p2) { return 3 * p2 - 6 * p1; }
    function c(p1) { return 3 * p1; }
    function calc(t, p1, p2) { return ((a(p1, p2) * t + b(p1, p2)) * t + c(p1)) * t; }
    function slope(t, p1, p2) { return 3 * a(p1, p2) * t * t + 2 * b(p1, p2) * t + c(p1); }
    return function (x) {
      if (x <= 0) return 0; if (x >= 1) return 1;
      var t = x;
      for (var i = 0; i < 8; i++) { var s = slope(t, x1, x2); if (Math.abs(s) < 1e-6) break; t -= (calc(t, x1, x2) - x) / s; }
      t = clamp(t, 0, 1);
      return calc(t, y1, y2);
    };
  }
  var E = {
    enter: bezier(0.23, 1, 0.32, 1),      // entrances: instant response, soft landing
    move: bezier(0.77, 0, 0.175, 1),      // on-screen moves and camera
    panel: bezier(0.32, 0.72, 0, 1),
    linear: function (x) { return clamp(x, 0, 1); }
  };
  // Closed-form step response of a damped spring (mass 1), from rest. Returns s(t) in 0..1(+overshoot).
  function springStep(k, c) {
    var w = Math.sqrt(k), z = c / (2 * w);
    if (Math.abs(z - 1) < 1e-4) return function (t) { return t <= 0 ? 0 : 1 - Math.exp(-w * t) * (1 + w * t); };
    if (z > 1) {
      var r = Math.sqrt(z * z - 1), l1 = w * (z - r), l2 = w * (z + r);
      return function (t) { return t <= 0 ? 0 : 1 - (l2 * Math.exp(-l1 * t) - l1 * Math.exp(-l2 * t)) / (l2 - l1); };
    }
    var wd = w * Math.sqrt(1 - z * z);
    return function (t) { return t <= 0 ? 0 : 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t)); };
  }
  // Camera spring: zeta 1.3, tuned so 90% is reached at 0.317 s (Cap's zoom spring).
  var camSpring = (function () {
    var z = 1.3, lo = 1, hi = 400;
    for (var i = 0; i < 60; i++) {
      var w = (lo + hi) / 2, s = springStep(w * w, 2 * z * w)(0.317);
      if (s < 0.9) lo = w; else hi = w;
    }
    var w0 = (lo + hi) / 2; return springStep(w0 * w0, 2 * z * w0);
  })();
  var cursorSpring = springStep(170, 20);
  function hash01(n) { var x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

  // Enter/exit envelope for a layer: returns {a: 0..1 opacity factor, p: entrance progress, q: exit progress}
  function envelope(L, t) {
    var ein = L.enter || {}, eout = L.exit || {};
    var di = ein.dur != null ? ein.dur : 0.5, dout = eout.dur != null ? eout.dur : 0.4;
    var p = di > 0 ? clamp((t - L.t0) / di, 0, 1) : 1;
    var q = dout > 0 ? clamp((L.t1 - t) / dout, 0, 1) : 1;
    return { p: p, q: q };
  }

  // ---------------------------------------------------------------- DOM helpers
  function el(tag, cls, parent, html) {
    var e = document.createElement(tag); if (cls) e.className = cls;
    if (html != null) e.innerHTML = html; if (parent) parent.appendChild(e); return e;
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e); return e;
  }
  function show(e, on) { if (on) e.classList.remove("hidden"); else e.classList.add("hidden"); }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  // Image cache: returns a promise that resolves once the image is decoded.
  var imgCache = {}, imgOrder = [];
  function getImg(url) {
    if (imgCache[url]) return imgCache[url];
    var im = new Image(); im.src = url;
    var p = im.decode().then(function () { return im; }, function () { console.error("img failed " + url); return im; });
    imgCache[url] = p; imgOrder.push(url);
    if (imgOrder.length > 80) { var old = imgOrder.shift(); delete imgCache[old]; }
    return p;
  }

  // ---------------------------------------------------------------- captures
  // caps[name] = {dir, log, frames[], byLabel{label: [frame…]}, ev{name: event}}
  function loadCap(name, dir) {
    return fetch(base + dir + "/log.json").then(function (r) { if (!r.ok) throw new Error("no capture " + dir); return r.json(); }).then(function (log) {
      var c = { dir: dir, log: log, frames: log.frames, byLabel: {}, ev: {} };
      log.frames.forEach(function (f) { (c.byLabel[f.label] = c.byLabel[f.label] || []).push(f); });
      log.events.forEach(function (e) { if (e.name) c.ev[e.name] = e; });
      caps[name] = c;
    });
  }
  function frameUrl(c, i) { return base + c.dir + "/f" + String(i).padStart(5, "0") + ".jpg"; }
  // A frame track is a list of entries resolved to [[t, frameIndex], …] (step function).
  //   {at, frame}                          a frame index
  //   {at, label, pick: "first"|"last"}    a labelled keyframe
  //   {at, label, play: speed, until}      play a labelled burst at real time / speed
  function resolveTrack(c, track) {
    var out = [];
    track.forEach(function (e) {
      if (e.frame != null) { out.push([e.at, e.frame]); return; }
      var fs = c.byLabel[e.label];
      if (!fs || !fs.length) { console.error("no frames for label " + e.label + " in " + c.dir); return; }
      if (e.play) {
        var t0 = fs[0].t, from = e.from || 0;
        fs.forEach(function (f) {
          var ft = e.at + (f.t - t0 - from) / e.play;
          if (f.t - t0 >= from && (e.until == null || ft < e.until)) out.push([ft, f.i]);
        });
      } else out.push([e.at, (e.pick === "last" ? fs[fs.length - 1] : fs[0]).i]);
    });
    out.sort(function (a, b) { return a[0] - b[0]; });
    return out;
  }
  function trackAt(tr, t) { var v = tr.length ? tr[0][1] : 1; for (var i = 0; i < tr.length; i++) { if (tr[i][0] <= t + 1e-6) v = tr[i][1]; else break; } return v; }
  function evRect(c, name) {
    var e = c && c.ev[name]; if (!e) { console.error("no event " + name); return [0, 0, 100, 100]; }
    return e.rect;
  }

  // ---------------------------------------------------------------- window layer (capture or terminal)
  // Geometry: content of size (cw, ch) CSS px shown in a window whose home rect on stage is L.home
  // [x, y, w] (height follows from aspect + chrome). Camera keys move a focus point (u, v) in content px
  // to an anchor (ax, ay) on stage at zoom z; each key is a target, reached with the camera spring
  // (or the move bezier when key.ease = "move").
  var CHROME = 56;
  function winGeom(L) {
    var hw = L.home || [140, 0, 1640];
    var k = hw[2] / L.cw, h = L.ch * k + CHROME;
    var y = hw[1] || (H - h) / 2;
    return { x: hw[0], y: y, w: hw[2], h: h, k: k };
  }
  function camTarget(L, g, key) {
    if (!key || key.full) return { u: L.cw / 2, v: L.ch / 2, z: 1, ax: g.x + g.w / 2, ay: g.y + g.h / 2 + (key && key.dy || 0) };
    var r = key.rect || (key.ev ? evRect(caps[L.cap], key.ev) : null);
    var u = key.u != null ? key.u : r[0] + r[2] / 2, v = key.v != null ? key.v : r[1] + r[3] / 2;
    var z = key.z;
    if (z == null) {
      var fill = key.fill || 0.7; // keep the subject inside the inner 70%
      z = Math.min(fill * W / (r[2] * g.k), fill * H / (r[3] * g.k));
      z = clamp(z, 1, key.zmax || 2.4);
    }
    return { u: u, v: v, z: z, ax: key.ax != null ? key.ax : W / 2, ay: key.ay != null ? key.ay : H / 2 };
  }
  function camAt(L, g, t) {
    var keys = L.cam || [{ t: L.t0, full: true }];
    var cur = camTarget(L, g, keys[0]);
    for (var i = 1; i < keys.length; i++) {
      var k = keys[i]; if (k.t > t) break;
      var tgt = camTarget(L, g, k);
      var s = k.ease === "move" ? E.move((t - k.t) / (k.dur || 0.9)) : camSpring(t - k.t);
      var lz = Math.log(cur.z), lz2 = Math.log(tgt.z);
      cur = { u: lerp(cur.u, tgt.u, s), v: lerp(cur.v, tgt.v, s), z: Math.exp(lerp(lz, lz2, s)), ax: lerp(cur.ax, tgt.ax, s), ay: lerp(cur.ay, tgt.ay, s) };
    }
    return cur;
  }
  // Transform for the window element (origin top-left at home): stage = home + T + z * local
  function camTransform(L, g, cam) {
    var lx = cam.u * g.k, ly = CHROME + cam.v * g.k;
    var tx = cam.ax - g.x - cam.z * lx, ty = cam.ay - g.y - cam.z * ly;
    // clamp: when the window is bigger than the stage, never show past its edges
    var left = g.x + tx, top = g.y + ty, ww = g.w * cam.z, hh = g.h * cam.z;
    if (ww >= W) { if (left > 0) tx -= left; if (left + ww < W) tx += W - (left + ww); }
    if (hh >= H) {
      var topLimit = -CHROME * cam.z * (cam.z > 1.15 ? 1 : 0); // when zoomed in, let the chrome slide off the top
      if (top > 0) ty -= top; if (top + hh < H) ty += H - (top + hh);
      if (g.y + ty > topLimit && topLimit < 0 && cam.z > 1.15) ty = Math.min(ty, topLimit - g.y + 0);
    }
    return { tx: tx, ty: ty, z: cam.z };
  }
  function toStage(g, tr, u, v) { return [g.x + tr.tx + tr.z * u * g.k, g.y + tr.ty + tr.z * (CHROME + v * g.k)]; }

  function buildWin(L) {
    var c = L.cap ? caps[L.cap] : null;
    if (c) { L.cw = L.cw || c.log.viewport.w; L.ch = L.ch || c.log.viewport.h; }
    L.cw = L.cw || 1600; L.ch = L.ch || 900;
    var root = el("div", "layer", stage); root.style.width = W + "px"; root.style.height = H + "px";
    var w = el("div", "win" + (L.kind === "term" ? " term" : ""), root);
    var g = winGeom(L); L.g = g;
    w.style.left = g.x + "px"; w.style.top = g.y + "px"; w.style.width = g.w + "px"; w.style.height = g.h + "px";
    var chrome = el("div", "chrome", w); var dots = el("div", "dots", chrome); el("i", "", dots); el("i", "", dots); el("i", "", dots);
    var url = el("div", "url", chrome, L.kind === "term" ? esc(L.title || "zsh")
      : '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>' + (L.url || ""));
    if (L.speed || L.kind === "term") L.badgeEl = el("div", "speed", chrome, esc(L.speed || ""));
    var content = el("div", "content", w);
    content.style.top = CHROME + "px"; content.style.width = L.cw + "px"; content.style.height = L.ch + "px";
    content.style.transformOrigin = "0 0"; content.style.transform = "scale(" + g.k + ")";
    L.root = root; L.win = w; L.content = content;
    if (c) {
      L.img = el("img", "", content); L.track = resolveTrack(c, L.frames || [{ at: L.t0, frame: 1 }]);
      L.dim = el("div", "", content); L.dim.style.cssText = "position:absolute;inset:0;background:#1C1917;opacity:0";
      L.liftEl = el("div", "", content); L.liftEl.style.cssText = "position:absolute;background-repeat:no-repeat;border-radius:14px;opacity:0";
      // focus mode: the page behind the subject blurs and fades toward cream; the subject card stays crisp
      L.veil = el("div", "", content); L.veil.style.cssText = "position:absolute;inset:0;background:#FBF8F1;opacity:0";
      L.crops = [0, 1, 2, 3].map(function () { var d = el("div", "crop", content); d.style.cssText = "position:absolute;background-repeat:no-repeat;border-radius:12px;opacity:0"; return d; });
      L.marks = (L.marks || []).map(function (m) { var e = el("div", "mark", content); return { m: m, e: e }; });
    }
    if (L.kind === "term") buildTerm(L);
    // cursor + ripple live on stage (constant size)
    L.cur = el("div", "cursor", root, '<svg viewBox="0 0 32 32" width="44" height="44"><path d="M6 3 L6 26 L12 20.5 L16.2 29.4 L20.3 27.5 L16.2 18.8 L24 18.8 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>');
    L.rip = el("div", "ripple", root);
    if (L.cursor) L.cursor.forEach(function (k) { if (k.ev && !k.at) { var r = evRect(c, k.ev); k.at = [r[0] + r[2] * (k.fx != null ? k.fx : 0.5), r[1] + r[3] * (k.fy != null ? k.fy : 0.55)]; } });
  }

  function cursorAt(L, t) {
    // keys: {t: arrival/click time, at: [u, v], click: bool, move: seconds of travel (default .55)}
    var ks = L.cursor; if (!ks || !ks.length) return null;
    var pos = ks[0].at.slice(), lastT = ks[0].t, click = null;
    for (var i = 1; i < ks.length; i++) {
      var k = ks[i], mv = k.move || 0.55, start = k.t - mv;
      if (start > t) break;
      // spring 170/20 toward the target from the move start; the last 160 ms is assisted so it lands on time
      var s = cursorSpring(t - start);
      var near = clamp((t - (k.t - 0.16)) / 0.16, 0, 1); s = lerp(s, 1, E.enter(near) * (t >= k.t ? 1 : near));
      var from = pos, to = k.at;
      var dx = to[0] - from[0], dy = to[1] - from[1], d = Math.sqrt(dx * dx + dy * dy);
      var arc = Math.sin(Math.PI * clamp(s, 0, 1)) * Math.min(0.08 * d, 40); // gentle arc
      pos = [lerp(from[0], to[0], s) - dy / (d || 1) * arc, lerp(from[1], to[1], s) + dx / (d || 1) * arc];
      if (t >= k.t) pos = to.slice();
      lastT = k.t;
      if (k.click && t >= k.t) click = k.t;
    }
    var hold = L.cursorHold != null ? L.cursorHold : 0.5, vis = 0;
    for (var j = 0; j < ks.length; j++) {
      var a = ks[j].t - (ks[j].move || 0.3) - 0.25, b = ks[j].t + hold;
      if (j + 1 < ks.length && ks[j + 1].t - (ks[j + 1].move || 0.3) - 0.25 <= b + 0.6) b = ks[j + 1].t; // short gaps stay visible
      vis = Math.max(vis, Math.min(clamp((t - a) / 0.2, 0, 1), clamp((b + 0.2 - t) / 0.2, 0, 1)));
    }
    return { pos: pos, click: click, vis: vis };
  }

  function nodeStageRect(diaId, nodeId, t) {
    var D = layers.filter(function (x) { return x.id === diaId; })[0]; if (!D) return [W / 2 - 100, H / 2 - 50, 200, 100];
    var cam = diaCamAt(D, t), n = D.byId[nodeId];
    return [W / 2 + (n.x - cam.cx) * cam.z, H / 2 + (n.y - cam.cy) * cam.z, n.w * cam.z, n.h * cam.z];
  }
  function updateWin(L, t) {
    var g = L.g, env = envelope(L, t);
    if (L.enter && L.enter.node && !L.enter.rect) L.enter.rect = nodeStageRect(L.enter.dia || "dia", L.enter.node, L.t0);
    if (L.exit && L.exit.node && !L.exit.rect) L.exit.rect = nodeStageRect(L.exit.dia || "dia", L.exit.node, L.t1);
    var cam = camAt(L, g, t);
    if (L.drift !== false) { var ph = (L.phase || 0) + t; cam.z *= 1 + 0.009 * Math.sin(ph * 2 * Math.PI / 11); cam.ax += 9 * Math.sin(ph * 2 * Math.PI / 15); cam.ay += 5 * Math.cos(ph * 2 * Math.PI / 13); }
    var tr = camTransform(L, g, cam);
    var op = 1, extra = "", blur = 0;
    var ein = L.enter || { type: "rise" }, eout = L.exit || { type: "fade" };
    // entrances
    if (env.p < 1) {
      var p = E.enter(env.p);
      if (ein.type === "rise") { op = p; extra = "translate(0," + (1 - p) * 70 + "px) "; tr = scaleAbout(tr, g, lerp(0.96, 1, p)); }
      else if (ein.type === "fade") op = p;
      else if (ein.type === "slideL") { extra = "translate(" + (1 - E.move(env.p)) * (W + 100) + "px,0) "; }
      else if (ein.type === "slideR") { extra = "translate(" + -(1 - E.move(env.p)) * (W + 100) + "px,0) "; }
      else if (ein.type === "fromRect") { // match-cut out of a node / element: the window grows out of a stage rect
        var m = E.move(env.p), r = ein.rect, s0 = r[2] / g.w;
        var trR = { tx: r[0] - g.x, ty: r[1] + r[3] / 2 - g.h * s0 / 2 - g.y, z: s0 };
        tr = { tx: lerp(trR.tx, tr.tx, m), ty: lerp(trR.ty, tr.ty, m), z: Math.exp(lerp(Math.log(trR.z), Math.log(tr.z), m)) };
        op = clamp(env.p * 4, 0, 1); blur = (1 - clamp(env.p * 3, 0, 1)) * 2;
      }
    }
    if (env.q < 1) {
      var q = env.q;
      if (eout.type === "fade") op *= E.enter(q);
      else if (eout.type === "slideL") extra += "translate(" + -(1 - E.move(1 - (1 - q))) * 0 + "px,0) " + "translate(" + -E.move(1 - q) * (W + 100) + "px,0) ";
      else if (eout.type === "slideR") extra += "translate(" + E.move(1 - q) * (W + 100) + "px,0) ";
      else if (eout.type === "toRect") {
        var m2 = E.move(1 - q), r2 = eout.rect, s2 = r2[2] / g.w;
        var trR2 = { tx: r2[0] - g.x, ty: r2[1] + r2[3] / 2 - g.h * s2 / 2 - g.y, z: s2 };
        tr = { tx: lerp(tr.tx, trR2.tx, m2), ty: lerp(tr.ty, trR2.ty, m2), z: Math.exp(lerp(Math.log(tr.z), Math.log(trR2.z), m2)) };
        op *= clamp(q * 4, 0, 1); blur = (1 - clamp(q * 3, 0, 1)) * 2;
      } else if (eout.type === "push") { // match-cut through an element: push until it fills the frame
        var m3 = E.move(1 - q), r3 = eout.rect;
        var push = camTransform(L, g, { u: r3[0] + r3[2] / 2, v: r3[1] + r3[3] / 2, z: Math.min(W / (r3[2] * g.k), H / (r3[3] * g.k)) * 1.05, ax: W / 2, ay: H / 2 });
        tr = { tx: lerp(tr.tx, push.tx, m3), ty: lerp(tr.ty, push.ty, m3), z: Math.exp(lerp(Math.log(tr.z), Math.log(push.z), m3)) };
        op *= clamp(q / 0.2, 0, 1); blur = (1 - clamp(q / 0.2, 0, 1)) * 2;
      }
    }
    L.win.style.transform = extra + "translate(" + tr.tx + "px," + tr.ty + "px) scale(" + tr.z + ")";
    L.win.style.opacity = op; L.win.style.filter = blur > 0.05 ? "blur(" + blur + "px)" : "";
    // cursor
    var cs = cursorAt(L, t);
    if (cs && cs.vis > 0) {
      var sp = toStage(g, tr, cs.pos[0], cs.pos[1]);
      var press = cs.click != null ? t - cs.click : 99;
      var sc = press >= 0 && press < 0.2 ? 0.92 + 0.08 * E.enter(press / 0.2) : 1;
      L.cur.style.display = ""; L.cur.style.opacity = cs.vis * op;
      L.cur.style.transform = "translate(" + (sp[0] - 6) + "px," + (sp[1] - 4) + "px) scale(" + sc + ")";
      if (press >= 0 && press < 0.32) {
        var rp = E.enter(press / 0.32);
        L.rip.style.display = ""; L.rip.style.opacity = 0.55 * (1 - rp);
        L.rip.style.transform = "translate(" + sp[0] + "px," + sp[1] + "px) scale(" + lerp(0.42, 1, rp) + ")";
      } else L.rip.style.display = "none";
    } else { L.cur.style.display = "none"; L.rip.style.display = "none"; }
    var pending = [];
    if (L.img) {
      var c = caps[L.cap], fi = trackAt(L.track, t), url = frameUrl(c, fi);
      if (L.curUrl !== url) {
        pending.push(getImg(url).then(function () { L.img.src = url; L.curUrl = url; FRESH++; return L.img.decode ? L.img.decode().catch(function () {}) : null; }));
      }
      // lift-out callout: a pixel-identical crop rises from the screen while the rest dims
      var lift = null; (L.lifts || []).forEach(function (lf) { if (t >= lf.t0 && t < lf.t1) lift = lf; });
      if (lift) {
        var lp = E.enter(clamp((t - lift.t0) / 0.4, 0, 1)) * E.enter(clamp((lift.t1 - t) / 0.35, 0, 1));
        var r = lift.rect || evRect(c, lift.ev);
        L.dim.style.opacity = 0.4 * lp; L.img.style.filter = "blur(" + 6 * lp + "px)";
        var le = L.liftEl;
        le.style.left = r[0] + "px"; le.style.top = r[1] + "px"; le.style.width = r[2] + "px"; le.style.height = r[3] + "px";
        le.style.backgroundImage = "url(" + url + ")"; le.style.backgroundSize = L.cw + "px " + L.ch + "px";
        le.style.backgroundPosition = -r[0] + "px " + -r[1] + "px"; le.style.opacity = lp > 0.001 ? 1 : 0;
        le.style.transform = "scale(" + lerp(1, 1.06, lp) + ")"; le.style.transformOrigin = "50% 50%";
        le.style.boxShadow = "0 " + 30 * lp + "px " + 60 * lp + "px -10px rgba(28,25,23," + 0.45 * lp + ")";
      } else { L.dim.style.opacity = 0; L.img.style.filter = ""; L.liftEl.style.opacity = 0; }
      var fo = null, prevFo = null, fl = L.focus || [];
      fl.forEach(function (f, i) { if (t >= f.t0 && t < f.t1) { fo = f; prevFo = i > 0 && Math.abs(fl[i - 1].t1 - f.t0) < 0.06 ? fl[i - 1] : null; } });
      if (fo) {
        var idx = fl.indexOf(fo), nextAdj = idx + 1 < fl.length && Math.abs(fl[idx + 1].t0 - fo.t1) < 0.06;
        var fin = prevFo ? 1 : E.enter(clamp((t - fo.t0) / 0.45, 0, 1)), fout = nextAdj ? 1 : E.enter(clamp((fo.t1 - t) / 0.4, 0, 1));
        var fp = fin * fout, cx = prevFo ? E.enter(clamp((t - fo.t0) / 0.35, 0, 1)) : 1;
        var rs = fo.rects || [fo.rect || evRect(c, fo.ev)];
        var olds = prevFo && cx < 1 ? (prevFo.rects || [prevFo.rect || evRect(c, prevFo.ev)]) : [];
        if (!lift) { L.img.style.filter = "blur(" + (12 * fp).toFixed(2) + "px)"; }
        L.veil.style.opacity = 0.72 * fp;
        var all = rs.map(function (r) { return { r: r, o: cx, pad: fo.pad }; }).concat(olds.map(function (r) { return { r: r, o: 1 - cx, pad: prevFo.pad }; }));
        L.crops.forEach(function (cr, i) {
          var it = all[i]; if (!it || fp <= 0.001 || it.o <= 0.001) { cr.style.opacity = 0; return; }
          var r = it.r, pad = it.pad != null ? it.pad : 0;
          cr.style.left = r[0] - pad + "px"; cr.style.top = r[1] - pad + "px"; cr.style.width = r[2] + 2 * pad + "px"; cr.style.height = r[3] + 2 * pad + "px";
          cr.style.backgroundImage = "url(" + url + ")"; cr.style.backgroundSize = L.cw + "px " + L.ch + "px";
          cr.style.backgroundPosition = -(r[0] - pad) + "px " + -(r[1] - pad) + "px"; cr.style.opacity = it.o;
          cr.style.boxShadow = "0 " + (18 * fp) + "px " + (44 * fp) + "px -12px rgba(70,48,16," + (0.30 * fp) + "), 0 0 0 1px rgba(70,48,16," + (0.08 * fp) + ")";
        });
        L.subjectNow = rs.map(function (r) { var a = toStage(g, tr, r[0], r[1]), b = toStage(g, tr, r[0] + r[2], r[1] + r[3]); return [a[0], a[1], b[0] - a[0], b[1] - a[1]]; });
      } else {
        L.veil.style.opacity = 0; L.crops.forEach(function (cr) { cr.style.opacity = 0; }); L.subjectNow = null;
      }
      L.marks.forEach(function (mk) {
        var m = mk.m, on = t >= m.t0 && t < m.t1;
        if (!on) { mk.e.style.opacity = 0; return; }
        var r = m.rect || evRect(c, m.ev), pad = m.pad != null ? m.pad : 8;
        var mp = E.enter(clamp((t - m.t0) / 0.3, 0, 1)) * E.enter(clamp((m.t1 - t) / 0.3, 0, 1));
        mk.e.style.left = r[0] - pad + "px"; mk.e.style.top = r[1] - pad + "px"; mk.e.style.width = r[2] + 2 * pad + "px"; mk.e.style.height = r[3] + 2 * pad + "px";
        mk.e.style.opacity = mp; mk.e.style.transform = "scale(" + lerp(0.97, 1, mp) + ")";
      });
    }
    if (L.kind === "term") updateTerm(L, t);
    return Promise.all(pending);
  }
  function scaleAbout(tr, g, s) { // scale the whole window about its own centre on stage
    var cx = g.x + tr.tx + g.w * tr.z / 2, cy = g.y + tr.ty + g.h * tr.z / 2, z = tr.z * s;
    return { tx: cx - g.w * z / 2 - g.x, ty: cy - g.h * z / 2 - g.y, z: z };
  }

  // ---------------------------------------------------------------- terminal replay
  // L.runs: [{rec: "redteam", at: film time the command starts typing, fit: seconds the output takes,
  //           keep: regex string of lines to show (optional), hi: regex string to highlight, gapAfter}]
  var recs = {};
  function buildTerm(L) {
    L.lines = []; var rows = L.rows || 16;
    for (var i = 0; i < rows; i++) L.lines.push(el("div", "ln", L.content));
    L.content.style.padding = "30px 40px";
  }
  function termLines(L, t) {
    var out = []; L.badgeNow = null;
    L.runs.forEach(function (run) {
      var rec = recs[run.rec]; if (!rec || t < run.at) return;
      if (run.clear) out = [];
      L.badgeNow = run.badge || null;
      var cmd = rec.cmd, typed = 0, tt = run.at, cps = run.cadence || 0.035;
      // typing: linear cadence (35–50 ms per character by default) with deterministic jitter
      for (var i = 0; i < cmd.length; i++) { tt += cps + cps * 0.4 * hash01(i + run.at * 7); if (tt <= t) typed = i + 1; }
      var doneTyping = tt + 0.25;
      out.push({ html: '<span class="p">' + esc(run.prompt || L.prompt || "kairos/backend %") + '</span> <span class="c">' + esc(cmd.slice(0, typed)) + "</span>" + (t < doneTyping + 0.1 ? '<span class="caret"></span>' : ""), cls: "" });
      if (t < doneTyping) return;
      var keep = run.keep ? new RegExp(run.keep) : null, hi = run.hi ? new RegExp(run.hi) : null;
      var lines = rec.lines.filter(function (l) { return !keep || keep.test(l[1]); });
      var dur = rec.dur || (lines.length ? lines[lines.length - 1][0] : 1);
      var fit = run.fit || 3;
      lines.forEach(function (l, j) {
        var at = doneTyping + (l[0] / dur) * fit + j * 0.04 * (run.stagger !== false ? 1 : 0);
        if (t >= at) {
          var cls = hi && hi.test(l[1]) ? "hi" : /PASS|passed|IDENTICAL/.test(l[1]) ? "ok" : run.dimAll ? "dim" : "";
          var txt = l[1].length > (L.maxCols || 92) ? l[1].slice(0, (L.maxCols || 92) - 1) + "…" : l[1];
          out.push({ html: esc(txt), cls: cls, hiAt: at });
        }
      });
    });
    return out;
  }
  function updateTerm(L, t) {
    var out = termLines(L, t), rows = L.lines.length, start = Math.max(0, out.length - rows);
    if (L.badgeEl) { L.badgeEl.textContent = L.badgeNow || ""; L.badgeEl.style.display = L.badgeNow ? "" : "none"; }
    // while a result line is lifted out (dark callout), the terminal behind it steps back: no text on text
    var dim = 0;
    layers.forEach(function (C) { if (C.kind === "callout" && C.dark && t >= C.t0 - 0.1 && t < C.t1) dim = Math.max(dim, Math.min(E.enter(clamp((t - C.t0 + 0.1) / 0.35, 0, 1)), E.enter(clamp((C.t1 - t) / 0.3, 0, 1)))); });
    L.content.style.filter = dim > 0.01 ? "blur(" + (4 * dim).toFixed(2) + "px)" : "";
    L.content.style.opacity = 1 - 0.6 * dim;
    for (var i = 0; i < rows; i++) {
      var o = out[start + i], e = L.lines[i];
      if (!o) { e.innerHTML = ""; e.className = "ln"; continue; }
      e.className = "ln"; e.innerHTML = o.cls ? '<span class="' + o.cls + '">' + o.html + "</span>" : o.html;
    }
  }

  // ---------------------------------------------------------------- simple layers
  function buildVideo(L) {
    var root = el("div", "layer vid", stage); root.style.width = W + "px"; root.style.height = H + "px";
    var v = el("video", "", root); v.muted = true; v.preload = "auto"; v.playsInline = true;
    v.style.filter = L.filter || (L.graded ? "" : "saturate(.95) contrast(1.06) sepia(.07) brightness(1.01)");
    if (L.shade !== false) el("div", "shade", root);
    L.root = root; L.v = v; L.lastSeek = -1; L.loaded = false;
    return null;
  }
  function attachVideo(L) {
    if (L.loading) return L.loading;
    L.loading = new Promise(function (res) {
      var settled = false; function ok() { if (!settled) { settled = true; L.loaded = true; res(); } }
      L.v.addEventListener("loadeddata", ok, { once: true });
      L.v.addEventListener("error", function () { console.error("video failed " + L.src); ok(); }, { once: true });
      // WebKit sometimes never fires loadeddata (decoder pressure): reload up to 3 times, then go on (qa_frames catches a black frame)
      setTimeout(function () {
        if (settled) return;
        L.tries = (L.tries || 0) + 1; console.error("video load timeout " + L.src + " try " + L.tries);
        if (L.tries >= 3) { ok(); return; }
        settled = true; L.loading = null; L.v.removeAttribute("src"); L.v.load();
        attachVideo(L).then(res);
      }, 6000);
      L.v.src = base + L.src; L.v.load();
    });
    return L.loading;
  }
  function detachVideo(L) {
    if (!L.loading) return;
    L.v.removeAttribute("src"); L.v.load(); L.loaded = false; L.loading = null; L.lastSeek = -1;
  }
  function updateVideo(L, t) {
    var env = envelope(L, t);
    L.root.style.opacity = E.enter(env.p) * E.enter(env.q);
    var life = clamp((t - L.t0) / (L.t1 - L.t0), 0, 1);
    var s = L.still ? 1 : 1.02 + 0.03 * life, ox = 50, oy = 50; // slow push on every hold
    if (L.push && t >= L.push.t0) { // camera pushes into a screen in the shot until it fills the frame
      var pp = E.move(clamp((t - L.push.t0) / (L.push.t1 - L.push.t0), 0, 1)), r = L.push.rect;
      var zEnd = L.push.z || Math.min(W / r[2], H / r[3]) * 1.02;
      s = Math.exp(lerp(Math.log(s), Math.log(zEnd), pp));
      ox = (r[0] + r[2] / 2) / W * 100; oy = (r[1] + r[3] / 2) / H * 100;
    }
    L.v.style.transformOrigin = ox + "% " + oy + "%";
    L.v.style.transform = "scale(" + s + ")";
    var want = (L.from || 0) + (t - L.t0) * (L.rate || 1);
    if (!L.loaded) return attachVideo(L).then(function () { return updateVideo(L, t); });
    if (Math.abs(want - L.lastSeek) < 1e-4) return null;
    L.lastSeek = want;
    var shown = presented(L.v);
    return new Promise(function (res) {
      var done = false; function fin() { if (!done) { done = true; res(); } }
      L.v.addEventListener("seeked", fin, { once: true }); setTimeout(fin, 3000);
      L.v.currentTime = want + (FILM.halfFrame ? 1 / 120 : 0);   // final2: the middle of the wanted frame
    }).then(function () { return shown; });
  }

  function buildText(L) {
    var root = el("div", "layer", stage); L.root = root; root.style.width = W + "px"; root.style.height = H + "px";
    if (L.bgCream) { var bgc = el("div", "", root); bgc.style.cssText = "position:absolute;inset:0;background:#FBF8F1"; }
    if (L.kind === "callout" || L.kind === "headline") {
      var c = el("div", "callout" + (L.bare || L.kind === "headline" ? " bare" : "") + (L.dark ? " dark" : ""), root);
      L.parts = [];
      if (L.kicker) L.parts.push(el("div", "k", c, esc(L.kicker)));
      L.parts.push(el("div", "h" + (L.xl ? " xl" : "") + (L.mono ? " mono" : ""), c, L.html || esc(L.text)));
      if (L.sub) L.parts.push(el("div", "s", c, L.subHtml || esc(L.sub)));
      if (L.kind === "headline") { L.parts[L.parts.length - 1 === 0 ? 0 : 0].style.fontSize = (L.size || 96) + "px"; if (L.sub) L.parts[L.parts.length - 1].style.fontSize = "46px"; }
      if (L.size && L.kind === "callout") L.parts[L.kicker ? 1 : 0].style.fontSize = L.size + "px";
      if (L.maxw) c.style.maxWidth = L.maxw + "px";
      place(c, L); L.box = c;
    } else if (L.kind === "tag") {
      var tg = el("div", "tag", root, (L.dot === false ? "" : "<i></i>") + esc(L.text)); place(tg, L); L.box = tg; L.parts = [tg];
    } else if (L.kind === "fine") {
      var f = el("div", "fine", root, L.html || esc(L.text)); place(f, L); L.box = f; L.parts = [f];
    } else if (L.kind === "words") {
      L.items = L.words.map(function (w) {
        var e = el("div", "callout bare", root, '<div class="h" style="font-size:' + (w.size || L.size || 120) + 'px;text-align:center">' + (w.html || esc(w.text)) + "</div>");
        e.style.left = "0"; e.style.width = W + "px"; e.style.maxWidth = "none"; e.style.top = (L.y || 430) + "px";
        return { w: w, e: e };
      });
    } else if (L.kind === "logo") {
      var lg = el("div", "logo", root, '<div class="mk">K</div><div class="wm">Kairos</div>');
      lg.style.left = "0"; lg.style.width = W + "px"; lg.style.justifyContent = "center"; lg.style.top = (L.y || 380) + "px";
      L.parts = [lg];
      if (L.line) { var ln = el("div", "callout bare", root, '<div class="h" style="font-size:' + (L.lineSize || 56) + 'px;font-weight:650;text-align:center;letter-spacing:-.02em">' + esc(L.line) + "</div>"); ln.style.left = "0"; ln.style.width = W + "px"; ln.style.maxWidth = "none"; ln.style.top = (L.y || 380) + 160 + "px"; L.parts.push(ln); }
      if (L.credits) { var cr = el("div", "fine", root, L.credits); cr.style.left = "0"; cr.style.width = W + "px"; cr.style.textAlign = "center"; cr.style.top = (L.creditsY || 930) + "px"; L.parts.push(cr); }
    } else if (L.kind === "stats") {
      var box = el("div", "stats", root); L.cards = L.items.map(function (it) {
        var s = el("div", "stat", box, '<div class="n">' + (it.pre || "") + '<span class="num"></span>' + (it.post || "") + '</div><div class="l">' + esc(it.label) + "</div>" + (it.fine ? '<div class="f">' + esc(it.fine) + "</div>" : ""));
        return { it: it, e: s, num: s.querySelector(".num") };
      });
      box.style.left = "0"; box.style.width = W + "px"; box.style.justifyContent = "center"; box.style.top = (L.y || 300) + "px";
      L.parts = [];
    }
  }
  function place(e, L) {
    var p = L.pos || "bl", m = 96; // 90% title-safe is 96 px at 1920
    if (L.x != null) { e.style.left = L.x + "px"; } else if (/l/.test(p)) e.style.left = m + "px"; else if (/r/.test(p)) e.style.right = m + "px";
    else { e.style.left = "0"; e.style.right = "0"; e.style.margin = "0 auto"; e.style.width = "fit-content"; e.style.textAlign = "center"; }
    if (L.y != null) e.style.top = L.y + "px"; else if (/t/.test(p)) e.style.top = 64 + "px"; else if (/b/.test(p)) e.style.bottom = 64 + "px"; else { e.style.top = "50%"; e.style.transform = "translateY(-50%)"; }
  }
  function updateText(L, t) {
    var env = envelope(L, t);
    if (L.kind === "words") {
      L.items.forEach(function (it, i) {
        var w = it.w, t1 = w.t1 != null ? w.t1 : (L.items[i + 1] ? L.items[i + 1].w.t : L.t1);
        var on = t >= w.t && t < t1; show(it.e, on); if (!on) return;
        var p = E.enter(clamp((t - w.t) / 0.22, 0, 1));
        it.e.style.opacity = p; it.e.style.transform = "translateY(" + ((1 - p) * 8 - 6 * clamp((t - w.t) / Math.max(0.5, t1 - w.t), 0, 1)) + "px) scale(" + (1 + 0.015 * clamp((t - w.t) / Math.max(0.5, t1 - w.t), 0, 1)) + ")";
        it.e.style.clipPath = "inset(" + (1 - p) * 40 + "% 0 0 0)";
      });
      return;
    }
    if (L.kind === "stats") {
      L.cards.forEach(function (cd, i) {
        var t0 = L.t0 + i * (L.stagger || 0.6), p = E.enter(clamp((t - t0) / 0.5, 0, 1)) * E.enter(env.q);
        cd.e.style.opacity = p; cd.e.style.transform = "translateY(" + ((1 - p) * 24 - 10 * clamp((t - L.t0) / (L.t1 - L.t0), 0, 1)) + "px) scale(" + lerp(0.96, 1, p) * (1 + 0.012 * clamp((t - L.t0) / (L.t1 - L.t0), 0, 1)) + ")";
        var it = cd.it, cp = E.enter(clamp((t - t0 - 0.1) / (it.count || 1.0), 0, 1));
        var v = it.from != null ? lerp(it.from, it.to, cp) : it.to;
        cd.num.textContent = it.fmt ? it.fmt(v) : (it.dec ? v.toFixed(it.dec) : Math.round(v).toLocaleString("en-IN"));
        cd.num.style.fontVariantNumeric = "tabular-nums";
      });
      return;
    }
    var parts = L.parts || [];
    parts.forEach(function (e, i) {
      var t0 = L.t0 + i * (L.stagger != null ? L.stagger : 0.12);
      var p = E.enter(clamp((t - t0) / (L.enterDur || 0.5), 0, 1)), q = E.enter(env.q);
      e.style.opacity = lerp(L.opa0 || 0, 1, p) * q;
      var y = (1 - p) * (L.kind === "logo" ? 14 : 10) - 8 * clamp((t - L.t0) / Math.max(1, L.t1 - L.t0), 0, 1);
      var s = lerp(0.96, 1, p);
      e.style.transform = (e === L.box && L.pos === "c" && L.y == null ? "translateY(-50%) " : "") + "translateY(" + y + "px) scale(" + s + ")";
    });
  }

  // ---------------------------------------------------------------- card (one real component, re-laid out large)
  function buildCard(L) {
    var root = el("div", "layer", stage); root.style.width = W + "px"; root.style.height = H + "px"; L.root = root;
    if (L.bg !== false) { var bg = el("div", "", root); bg.style.cssText = "position:absolute;inset:0;background:" + (L.bg || "#FBF8F1"); }
    L.box = el("div", "card", root); L.box.style.overflow = "hidden";
    L.im = el("img", "", L.box); L.im.style.cssText = "position:absolute;left:0;top:0;transform-origin:0 0;max-width:none";
    L.track = resolveTrack(caps[L.cap], L.frames);
    if (L.cursor) { // a cursor that clicks a real control on the card (capture CSS px)
      L.cur = el("div", "cursor", root, '<svg viewBox="0 0 32 32" width="44" height="44"><path d="M6 3 L6 26 L12 20.5 L16.2 29.4 L20.3 27.5 L16.2 18.8 L24 18.8 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>');
      L.rip = el("div", "ripple", root);
    }
  }
  function updateCard(L, t) {
    var env = envelope(L, t), c = caps[L.cap], fi = trackAt(L.track, t), url = frameUrl(c, fi);
    var r = L.rect, maxw = L.maxw || 1640, maxh = L.maxh || 720;
    var sc = Math.min(maxw / r[2], maxh / r[3], L.maxScale || 3.2);
    var w = r[2] * sc, h = r[3] * sc, cx = L.cx || W / 2, cy = L.cy || 450;
    var b = L.box, life = clamp((t - L.t0) / (L.t1 - L.t0), 0, 1);
    b.style.left = (cx - w / 2) + "px"; b.style.top = (cy - h / 2) + "px"; b.style.width = w + "px"; b.style.height = h + "px";
    var vw = c.log.viewport.w, vh = c.log.viewport.h;
    L.im.style.width = (vw * sc) + "px"; L.im.style.height = (vh * sc) + "px";
    L.im.style.transform = "translate(" + (-r[0] * sc) + "px," + (-r[1] * sc) + "px)";
    var p = E.enter(env.p), q = E.enter(env.q);
    b.style.opacity = p * q;
    b.style.transform = "translateY(" + ((1 - p) * 18 - 6 * life) + "px) scale(" + (lerp(0.97, 1, p) * (1 + 0.015 * life)) + ")";
    L.subjectNow = [[cx - w / 2, cy - h / 2, w, h]];
    if (L.cur) {
      var cs = cursorAt(L, t);
      if (cs && cs.vis > 0) {
        var spx = cx - w / 2 + (cs.pos[0] - r[0]) * sc, spy = cy - h / 2 + (cs.pos[1] - r[1]) * sc;
        var press = cs.click != null ? t - cs.click : 99, psc = press >= 0 && press < 0.2 ? 0.92 + 0.08 * E.enter(press / 0.2) : 1;
        L.cur.style.display = ""; L.cur.style.opacity = cs.vis * p * q;
        L.cur.style.transform = "translate(" + (spx - 6) + "px," + (spy - 4) + "px) scale(" + psc + ")";
        if (press >= 0 && press < 0.32) {
          var rp = E.enter(press / 0.32);
          L.rip.style.display = ""; L.rip.style.opacity = 0.55 * (1 - rp);
          L.rip.style.transform = "translate(" + spx + "px," + spy + "px) scale(" + lerp(0.42, 1, rp) + ")";
        } else L.rip.style.display = "none";
      } else { L.cur.style.display = "none"; L.rip.style.display = "none"; }
    }
    if (L.curUrl !== url) {
      return getImg(url).then(function () { L.im.src = url; L.curUrl = url; FRESH++; return L.im.decode().catch(function () {}); });
    }
    return L.im.decode().catch(function () {});
  }

  // ---------------------------------------------------------------- captions (closed captions, burned in)
  function buildCaptions(L) {
    var root = el("div", "layer", stage); root.style.width = W + "px"; root.style.height = H + "px"; L.root = root;
    L.pill = el("div", "cc" + (FILM.ccStyle === "v4" ? " v4" : ""), root);
  }
  function updateCaptions(L, t) {
    var cue = null; (FILM.cueList || []).forEach(function (c) { if (t >= c.t0 && t < c.t1) cue = c; });
    if (!cue) { L.pill.style.opacity = 0; L.cueNow = null; return null; }
    var a = Math.min(clamp((t - cue.t0) / 0.12, 0, 1), clamp((cue.t1 - t) / 0.12, 0, 1));
    // over a phone that reaches the bottom of the frame, the caption band moves to the clean top of the shot
    var top = null; (FILM.ccTop || []).forEach(function (sp) { if (t >= sp[0] && t < sp[1]) top = sp[2] || "top"; });
    if (top !== L.topNow) {
      L.pill.style.top = top ? "56px" : ""; L.pill.style.bottom = top ? "auto" : "";
      L.pill.style.left = top === "topleft" ? "96px" : ""; L.pill.style.transform = top === "topleft" ? "none" : "";
      L.topNow = top;
    }
    if (L.cueNow !== cue) { L.pill.innerHTML = (cue.who ? '<b>' + esc(cue.who) + ':</b> ' : "") + cue.lines.map(esc).join("<br>"); L.cueNow = cue; }
    L.pill.style.opacity = a;
    return null;
  }

  // ---------------------------------------------------------------- dots (a unit chart of real counts)
  function buildDots(L) {
    var root = el("div", "layer", stage); root.style.width = W + "px"; root.style.height = H + "px"; L.root = root;
    if (L.bgCream) { var bgc = el("div", "", root); bgc.style.cssText = "position:absolute;inset:0;background:#FBF8F1"; }
    var cv = el("canvas", "", root); cv.width = W; cv.height = H; cv.style.cssText = "position:absolute;left:0;top:0;width:1920px;height:1080px";
    L.cv = cv; L.ctx = cv.getContext("2d");
    var n = L.n, cols = L.cols || 61, gap = L.gap || 30, rows = Math.ceil(n / cols);
    L.x0 = (W - (cols - 1) * gap) / 2; L.y0 = (H - (rows - 1) * gap) / 2;
    // deterministic choice of the highlighted items
    var idx = [], used = {}, k = 0; while (idx.length < (L.hi || 0)) { var j = Math.floor(hash01(k++ * 7.31) * n); if (!used[j]) { used[j] = 1; idx.push(j); } }
    L.hiSet = used; L.cols = cols;
  }
  function updateDots(L, t) {
    var env = envelope(L, t), ctx = L.ctx, n = L.n;
    ctx.clearRect(0, 0, W, H);
    var appear = clamp((t - L.t0) / (L.fill || 1.2), 0, 1), lit = E.enter(clamp((t - (L.tHi || 1e9)) / 0.6, 0, 1));
    var a = E.enter(env.p) * E.enter(env.q), drift = 1 + 0.015 * clamp((t - L.t0) / (L.t1 - L.t0), 0, 1);
    ctx.save(); ctx.translate(W / 2, H / 2); ctx.scale(drift, drift); ctx.translate(-W / 2, -H / 2);
    for (var i = 0; i < n; i++) {
      var show = i / n <= appear; if (!show) break;
      var x = L.x0 + (i % L.cols) * L.gap, y = L.y0 + Math.floor(i / L.cols) * L.gap, isHi = !!L.hiSet[i];
      ctx.globalAlpha = a * (isHi ? 1 : lerp(0.9, 0.55, lit));
      ctx.fillStyle = L.theme === "dark" ? (isHi && lit > 0 ? "rgba(45,212,191," + (0.35 + 0.65 * lit) + ")" : "rgba(226,232,240,.55)")
                                         : (isHi && lit > 0 ? "rgba(15,118,110," + (0.25 + 0.75 * lit) + ")" : "#E2D9C8");
      var r = isHi ? lerp(7, 9, lit) : 7;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore(); ctx.globalAlpha = 1;
    return null;
  }

  // ---------------------------------------------------------------- diagram
  // L.nodes: [{id, x, y, w, h, title, idt (e.g. "K06"), sub, color, t (appear)}]
  // L.edges: [{id, from, to, pts (optional [[x,y]…] waypoints), t, label}]
  // L.cam:   [{t, node | rect | full, z, dur}] (move bezier, 0.9–1.2 s)
  // L.focus: [{t, node | nodes: [...]}] nodes not in focus dim to 35% after their own entrance
  // L.pulses:[{t, path: [edge ids], speed: px/s}] a bright token running along edges, ring flash on arrival
  function buildDiagram(L) {
    var root = el("div", "layer", stage); root.style.width = W + "px"; root.style.height = H + "px"; L.root = root;
    if (L.bgCream) { var bgc = el("div", "", root); bgc.style.cssText = "position:absolute;inset:0;background:#FBF8F1"; }
    if (L.mini) { var pl = el("div", "", root); pl.style.cssText = "position:absolute;left:" + (L.mini.x - 24) + "px;top:" + (L.mini.y - 22) + "px;width:" + ((L.ww || W) * L.mini.s + 48) + "px;height:" + ((L.wh || H) * L.mini.s + 44) + "px;border-radius:22px;background:rgba(251,248,241,.94);box-shadow:0 18px 40px -16px rgba(40,30,10,.45)"; }
    var world = el("div", "dia", root); L.world = world;
    var svg = sv("svg", { width: L.ww || W, height: L.wh || H, viewBox: "0 0 " + (L.ww || W) + " " + (L.wh || H) }, world);
    var defs = sv("defs", {}, svg);
    var pat = sv("pattern", { id: "dots", width: 36, height: 36, patternUnits: "userSpaceOnUse" }, defs);
    sv("circle", { cx: 18, cy: 18, r: 2.1, fill: "#E0D8C8" }, pat);
    sv("rect", { x: -2000, y: -1500, width: (L.ww || W) + 4000, height: (L.wh || H) + 3000, fill: "url(#dots)" }, svg);
    var mk = sv("marker", { id: "ah", viewBox: "0 0 10 10", refX: "8.5", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, defs);
    sv("path", { d: "M0 0 L10 5 L0 10 z", fill: "#B9B0A1" }, mk);
    L.byId = {};
    L.nodes.forEach(function (n) { L.byId[n.id] = n; });
    L.edgeEls = L.edges.map(function (e) {
      var a = L.byId[e.from], b = L.byId[e.to], pts = edgePts(a, b, e);
      var g = sv("g", { "class": "edge" }, svg);
      var d = "M" + pts.map(function (p) { return p[0] + " " + p[1]; }).join(" L");
      var path = sv("path", { d: d, "marker-end": "url(#ah)" }, g);
      var hot = sv("path", { d: d, style: "stroke:" + (b.color || "#0F766E") + ";stroke-width:6;opacity:0;fill:none;stroke-linecap:round" }, g);
      if (e.label) { var mid = pts[Math.floor(pts.length / 2) - (pts.length % 2 ? 0 : 1)], mid2 = pts[Math.floor(pts.length / 2)]; var tx = sv("text", { x: (mid[0] + mid2[0]) / 2, y: (mid[1] + mid2[1]) / 2 - 14, "text-anchor": "middle" }, g); tx.textContent = e.label; }
      var len = 0; for (var i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      return { e: e, g: g, path: path, hot: hot, pts: pts, len: len };
    });
    L.nodeEls = L.nodes.map(function (n) {
      var g = sv("g", { "class": "node" }, svg);
      var ring = sv("rect", { x: n.x - 8, y: n.y - 8, width: n.w + 16, height: n.h + 16, rx: 30, style: "fill:none;stroke:" + (n.color || "#0F766E") + ";stroke-width:5;opacity:0" }, g);
      var box = sv("rect", { "class": "box", x: n.x, y: n.y, width: n.w, height: n.h, rx: 24 }, g);
      box.style.setProperty("--nc", n.color || "#0F766E");
      var tx = n.x + 40, ty = n.y + (n.sub ? n.h / 2 - 4 : n.h / 2 + 16);
      var parts = String(n.title).split(" · ");
      var t1 = sv("text", { "class": "t", x: tx, y: ty }, g);
      if (parts.length > 1) {
        var a = sv("tspan", { style: "fill:" + (n.color || "#0F766E") }, t1); a.textContent = parts[0] + " ";
        var b = sv("tspan", {}, t1); b.textContent = parts.slice(1).join(" · ");
      } else {
        var dot = sv("circle", { cx: tx + 8, cy: ty - 13, r: 8, style: "fill:" + (n.color || "#0F766E") }, g);
        t1.setAttribute("x", tx + 28); t1.textContent = n.title;
      }
      if (n.sub) { var t2 = sv("text", { "class": "s", x: tx, y: ty + 52 }, g); t2.textContent = n.sub; }
      if (n.cap) { var t3 = sv("text", { "class": "cap", x: n.x + n.w / 2, y: n.y + n.h + 56, "text-anchor": "middle" }, g); t3.textContent = n.cap; }
      g.style.transformBox = "fill-box"; g.style.transformOrigin = "50% 50%";
      return { n: n, g: g, ring: ring };
    });
    L.token = sv("circle", { r: 11, "class": "token", opacity: 0 }, svg);
    L.tokenGlow = sv("circle", { r: 24, fill: "#0F766E", opacity: 0 }, svg);
  }
  function edgePts(a, b, e) {
    if (e.pts) return [anchor(a, e.pts[0])].concat(e.pts).concat([anchor(b, e.pts[e.pts.length - 1])]);
    var ac = [a.x + a.w / 2, a.y + a.h / 2], bc = [b.x + b.w / 2, b.y + b.h / 2];
    return [anchor(a, bc), anchor(b, ac)];
  }
  function anchor(n, toward) { // point on node border toward a point
    var cx = n.x + n.w / 2, cy = n.y + n.h / 2, dx = toward[0] - cx, dy = toward[1] - cy;
    var sx = dx === 0 ? 1e9 : (n.w / 2 + 6) / Math.abs(dx), sy = dy === 0 ? 1e9 : (n.h / 2 + 6) / Math.abs(dy), s = Math.min(sx, sy);
    return [cx + dx * s, cy + dy * s];
  }
  function diaCam(L, key) {
    if (key && key.cx != null) return { cx: key.cx, cy: key.cy, z: key.z || 1 };
    if (!key || key.full) { var fz = key && key.z || 1; return { cx: (L.ww || W) / 2, cy: (L.wh || H) / 2 + (key && key.dy || 0), z: fz }; }
    var r = key.rect;
    if (key.node) { var n = L.byId[key.node]; r = [n.x, n.y, n.w, n.h]; }
    if (key.nodes) { var xs = [], ys = []; key.nodes.forEach(function (id) { var n = L.byId[id]; xs.push(n.x, n.x + n.w); ys.push(n.y, n.y + n.h); }); r = [Math.min.apply(0, xs), Math.min.apply(0, ys), Math.max.apply(0, xs) - Math.min.apply(0, xs), Math.max.apply(0, ys) - Math.min.apply(0, ys)]; }
    var z = key.z || Math.min(0.7 * W / r[2], 0.7 * H / r[3]);
    return { cx: r[0] + r[2] / 2 + (key.dx || 0), cy: r[1] + r[3] / 2 + (key.dy || 0), z: z };
  }
  function diaCamAt(L, t) {
    var keys = L.cam || [{ t: 0, full: true }], cur = diaCam(L, keys[0]);
    for (var i = 1; i < keys.length; i++) {
      var k = keys[i]; if (k.t > t) break;
      var tg = diaCam(L, k), s = E.move((t - k.t) / (k.dur || 1.0));
      cur = { cx: lerp(cur.cx, tg.cx, s), cy: lerp(cur.cy, tg.cy, s), z: Math.exp(lerp(Math.log(cur.z), Math.log(tg.z), s)) };
    }
    return cur;
  }
  function updateDiagram(L, t) {
    var env = envelope(L, t);
    var cam = diaCamAt(L, t);
    cam.z *= 1 + 0.01 * Math.sin(t * 2 * Math.PI / 12); cam.cx += 10 * Math.sin(t * 2 * Math.PI / 16) / cam.z;
    // exit as match-cut push into a node: the camera keeps pushing until the node fills the frame
    var push = 1;
    if (L.pushes) L.pushes.forEach(function (pp) {
      if (t >= pp.t && t < pp.t + pp.dur + (pp.hold || 0) + pp.back) {
        var n = L.byId[pp.node], zEnd = Math.min(W / n.w, H / n.h) * 1.15;
        var a = clamp((t - pp.t) / pp.dur, 0, 1), b = clamp((t - (pp.t + pp.dur + (pp.hold || 0))) / pp.back, 0, 1);
        var m = E.move(a) * (1 - E.move(b));
        cam = { cx: lerp(cam.cx, n.x + n.w / 2, m), cy: lerp(cam.cy, n.y + n.h / 2, m), z: Math.exp(lerp(Math.log(cam.z), Math.log(zEnd), m)) };
        push = Math.min(push, 1 - clamp((m - 0.85) / 0.15, 0, 1));
      }
    });
    if (L.mini) { cam = { cx: (L.ww || W) / 2, cy: (L.wh || H) / 2, z: L.mini.s }; push = 1; }
    L.world.style.transform = L.mini ? "translate(" + L.mini.x + "px," + L.mini.y + "px) scale(" + L.mini.s + ")"
      : "translate(" + (W / 2 - cam.cx * cam.z) + "px," + (H / 2 - cam.cy * cam.z) + "px) scale(" + cam.z + ")";
    L.world.style.filter = push < 1 ? "blur(" + (1 - push) * 2 + "px)" : "";
    var away = 1;
    (L.away || []).forEach(function (a) {
      if (t >= a[0] && t < a[1]) away = Math.min(away, 1 - Math.min(E.enter(clamp((t - a[0]) / 0.45, 0, 1)), E.enter(clamp((a[1] - t) / 0.45, 0, 1))));
    });
    L.root.style.opacity = E.enter(env.p) * E.enter(env.q) * push * away;
    // focus set
    var focus = null; (L.focus || []).forEach(function (f) { if (t >= f.t) focus = f.nodes || (f.node ? [f.node] : null); });
    L.nodeEls.forEach(function (ne) {
      var n = ne.n, p = clamp((t - n.t) / 0.35, 0, 1);
      if (t < n.t) { ne.g.style.opacity = 0; return; }
      var pe = E.enter(p), dimmed = focus && focus.indexOf(n.id) < 0 && focus.indexOf("*") < 0;
      var dimT = 0; (L.focus || []).forEach(function (f) { if (t >= f.t) dimT = f.t; });
      var dp = dimmed ? E.enter(clamp((t - Math.max(dimT, n.t + 0.35)) / 0.4, 0, 1)) : 0;
      ne.g.style.opacity = pe * lerp(1, 0.45, dp);
      ne.g.style.transform = "scale(" + lerp(0.96, 1, pe) + ")";
      ne.g.classList.toggle("on", !!(focus && focus.indexOf(n.id) >= 0));
      ne.ring.style.opacity = 0;
    });
    L.edgeEls.forEach(function (ee) {
      var e = ee.e; if (t < e.t) { ee.g.style.opacity = 0; return; }
      var p = clamp((t - e.t) / 0.45, 0, 1);
      ee.g.style.opacity = 1;
      ee.path.style.strokeDasharray = ee.len + 400; ee.path.style.strokeDashoffset = (ee.len + 400) * (1 - E.enter(p));
      var a = L.byId[e.from], b = L.byId[e.to];
      var dim = focus && focus.indexOf("*") < 0 && (focus.indexOf(e.from) < 0 || focus.indexOf(e.to) < 0);
      ee.path.style.opacity = dim ? 0.45 : 1;
      ee.hot.style.opacity = 0;
    });
    // pulses
    L.token.setAttribute("opacity", 0); L.tokenGlow.setAttribute("opacity", 0);
    (L.pulses || []).forEach(function (pl) {
      var speed = pl.speed || 600, tt = pl.t;
      for (var i = 0; i < pl.path.length; i++) {
        var ee = L.edgeEls.filter(function (x) { return x.e.id === pl.path[i]; })[0]; if (!ee) continue;
        var d = ee.len / speed, dwell = pl.dwell != null ? pl.dwell : 0.12;
        if (t >= tt && t < tt + d) {
          var s = (t - tt) / d, pos = along(ee.pts, ee.len * s);
          L.token.setAttribute("cx", pos[0]); L.token.setAttribute("cy", pos[1]); L.token.setAttribute("opacity", 1);
          L.tokenGlow.setAttribute("cx", pos[0]); L.tokenGlow.setAttribute("cy", pos[1]); L.tokenGlow.setAttribute("opacity", 0.18);
          L.token.style.fill = L.byId[ee.e.to].color || "#0F766E"; L.tokenGlow.style.fill = L.token.style.fill;
          ee.hot.style.opacity = 0.9; ee.hot.style.strokeDasharray = ee.len; ee.hot.style.strokeDashoffset = ee.len * (1 - s);
        }
        var arrive = tt + d;
        var ne = L.nodeEls.filter(function (x) { return x.n.id === ee.e.to; })[0];
        if (ne && t >= arrive && t < arrive + 0.25 + dwell) { var rp = clamp((t - arrive) / 0.25, 0, 1); ne.ring.style.opacity = Math.max(+ne.ring.style.opacity || 0, 1 - rp * 0.6); ne.ring.style.transformBox = "fill-box"; ne.ring.style.transformOrigin = "50% 50%"; ne.ring.style.transform = "scale(" + lerp(0.98, 1.03, E.enter(rp)) + ")"; }
        tt = arrive + dwell;
      }
    });
    return null;
  }
  function along(pts, d) {
    for (var i = 1; i < pts.length; i++) {
      var l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      if (d <= l) { var s = d / l; return [lerp(pts[i - 1][0], pts[i][0], s), lerp(pts[i - 1][1], pts[i][1], s)]; }
      d -= l;
    }
    return pts[pts.length - 1];
  }

  // ---------------------------------------------------------------- film
  function load(film) {
    FILM = film; stage = document.getElementById("stage");
    if (film.bg) stage.style.background = film.bg;
    var jobs = [];
    Object.keys(film.captures || {}).forEach(function (k) {
      jobs.push(loadCap(k, film.captures[k]).catch(function (err) {
        console.error(err); window.loadWarnings = (window.loadWarnings || []).concat(String(err));
        caps[k] = { dir: film.captures[k], log: { viewport: { w: 1440, h: 810 }, frames: [], events: [] }, frames: [], byLabel: {}, ev: {} };
      }));
    });
    if (film.cues) jobs.push(fetch(base + film.cues).then(function (r) { return r.json(); }).then(function (j) { film.cueList = j; }));
    Object.keys(film.terms || {}).forEach(function (k) {
      jobs.push(fetch(base + film.terms[k]).then(function (r) { return r.json(); }).then(function (j) { recs[k] = j; }));
    });
    return Promise.all(jobs).then(function () {
      var vids = [];
      if (film.cues && !film.layers.some(function (L) { return L.kind === "captions"; })) film.layers.push({ kind: "captions", t0: 0, t1: film.duration, z: 1000 });
      film.layers.forEach(function (L, i) {
        L.z = L.z != null ? L.z : i;
        if (L.kind === "win" || L.kind === "term") buildWin(L);
        else if (L.kind === "video") vids.push(buildVideo(L));
        else if (L.kind === "diagram") buildDiagram(L);
        else if (L.kind === "dots") buildDots(L);
        else if (L.kind === "card") buildCard(L);
        else if (L.kind === "captions") buildCaptions(L);
        else if (EXT[L.kind]) { var vb = EXT[L.kind].build(L, API); if (vb && vb.then) vids.push(vb); }
        else buildText(L);
        L.root.style.zIndex = L.z;
        show(L.root, false);
        layers.push(L);
      });
      return Promise.all(vids);
    }).then(function () { return document.fonts ? document.fonts.ready : null; });
  }
  function seek(t) {
    var pending = []; FRESH = 0;
    layers.forEach(function (L) { if ((L.kind === "video" || L.vid) && (t < L.t0 - 1.5 || t > L.t1 + 0.5)) detachVideo(L); });
    layers.forEach(function (L) {
      var on = t >= L.t0 && t < L.t1;
      show(L.root, on);
      if (!on) return;
      var r;
      if (L.kind === "win" || L.kind === "term") r = updateWin(L, t);
      else if (L.kind === "video") r = updateVideo(L, t);
      else if (L.kind === "diagram") r = updateDiagram(L, t);
      else if (L.kind === "dots") r = updateDots(L, t);
      else if (L.kind === "card") r = updateCard(L, t);
      else if (L.kind === "captions") r = updateCaptions(L, t);
      else if (EXT[L.kind]) r = EXT[L.kind].update(L, t, API);
      else r = updateText(L, t);
      if (r) pending.push(r);
    });
    // QA: captions vs subjects
    layers.forEach(function (L) {
      if (L.kind !== "captions" || !L.cueNow) return;
      var b = L.pill.getBoundingClientRect();
      layers.forEach(function (C) { if ((C.kind === "card" || C.kind === "glass" || C.kind === "phone" || C.kind === "panel") && t >= C.t0 && t < C.t1 && C.subjectNow) C.subjectNow.forEach(function (r) {
        if (Math.min(b.right, r[0] + r[2]) - Math.max(b.left, r[0]) > 4 && Math.min(b.bottom, r[1] + r[3]) - Math.max(b.top, r[1]) > 4) QA.push({ t: t, kind: "caption over card", text: L.cueNow.lines.join(" ") });
      }); });
    });
    // QA: overlay collisions (collected for the renderer's report)
    var subj = [], openWin = false;
    layers.forEach(function (L) { if ((L.kind === "win") && t >= L.t0 && t < L.t1) { openWin = true; if (L.subjectNow) subj = subj.concat(L.subjectNow); else if (L.cam && t > L.t0 + 0.8) L._unfocused = true; } });
    layers.forEach(function (L) {
      if (L.kind !== "callout" || !(t >= L.t0 && t < L.t1) || !L.box) return;
      var b = L.box.getBoundingClientRect();
      subj.forEach(function (r) {
        var ix = Math.min(b.right, r[0] + r[2]) - Math.max(b.left, r[0]), iy = Math.min(b.bottom, r[1] + r[3]) - Math.max(b.top, r[1]);
        if (ix > 4 && iy > 4) QA.push({ t: t, kind: "callout over subject", text: L.text || L.html });
      });
      var winsOpen = layers.filter(function (W2) { return W2.kind === "win" && t >= W2.t0 && t < W2.t1; });
      winsOpen.forEach(function (W2) { if (!W2.subjectNow && !L.bare) QA.push({ t: t, kind: "callout over unfocused UI", text: L.text || L.html }); });
    });
    return Promise.all(pending).then(commitFrame).then(function () { return FRESH; });
  }
  function commitFrame() { // let WebKit paint and commit this exact state before the snapshot (bounded so it can never hang)
    return new Promise(function (res) {
      var done = false; function fin() { if (!done) { done = true; res(); } }
      requestAnimationFrame(function () { requestAnimationFrame(fin); }); setTimeout(fin, 120);
    });
  }
  function presented(v) { // resolves when the video's seeked frame has been handed to the compositor (or after 80 ms)
    return new Promise(function (res) {
      var done = false; function fin() { if (!done) { done = true; res(); } }
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(fin); setTimeout(fin, 80);
    });
  }
  window.seek = seek;
  // v4 layer kinds (comp/v4.js) plug in here: KAIROS_EXT[kind] = {build(L, API), update(L, t, API)}
  var EXT = window.KAIROS_EXT || {};
  var API = { W: W, H: H, el: el, sv: sv, esc: esc, E: E, clamp: clamp, lerp: lerp, envelope: envelope, show: show,
              caps: caps, getImg: getImg, frameUrl: frameUrl, resolveTrack: resolveTrack, trackAt: trackAt,
              attachVideo: attachVideo, detachVideo: detachVideo, cursorAt: cursorAt, presented: presented, stage: function () { return stage; }, base: base,
              fresh: function () { FRESH++; }, film: function () { return FILM; } };
  function describe(t) { // what is on screen at t (for QA.md): layers, focus subject, UI scale
    var out = [];
    layers.forEach(function (L) {
      if (!(t >= L.t0 && t < L.t1)) return;
      if (L.kind === "win" || L.kind === "term") {
        var cam = camAt(L, L.g, t), sc = L.g.k * cam.z;
        var fo = null; (L.focus || []).forEach(function (f) { if (t >= f.t0 && t < f.t1) fo = f; });
        out.push({ kind: L.kind, src: L.cap || (L.runs ? "terminal" : ""), url: L.url || L.title || "", zoom: +cam.z.toFixed(2), uiScale: +sc.toFixed(2),
                   text13px: Math.round(13 * sc), text16px: Math.round(16 * sc), focus: fo ? (fo.ev || (fo.rects ? fo.rects.length + " rect(s)" : "rect")) : null,
                   frame: L.track ? trackAt(L.track, t) : null });
      } else if (L.kind === "diagram") {
        var dc = diaCamAt(L, t); out.push({ kind: "diagram", zoom: +dc.z.toFixed(2), title_px: Math.round(48 * dc.z), sub_px: Math.round(36 * dc.z) });
      } else if (L.kind === "callout" || L.kind === "headline") {
        out.push({ kind: L.kind, text: (L.text || L.html || "").replace(/<[^>]+>/g, ""), px: L.size || (L.kind === "headline" ? 96 : (L.mono ? 50 : 72)) });
      } else if (L.kind === "card") {
        var cr = L.rect, csc = Math.min((L.maxw || 1640) / cr[2], (L.maxh || 720) / cr[3], L.maxScale || 3.2), cfi = trackAt(L.track, t), cfr = caps[L.cap].log.frames[cfi] || {};
        out.push({ kind: "card", src: L.cap, label: cfr.label || "", url: caps[L.cap].log.url || "", zoom: +csc.toFixed(2), text13px: Math.round(13 * csc), text16px: Math.round(16 * csc) });
      } else if (L.kind === "video") {
        out.push({ kind: "video", src: L.src, push: !!(L.push && t >= L.push.t0) });
      } else if (L.kind === "captions") {
        var cue = null; (FILM.cueList || []).forEach(function (c) { if (t >= c.t0 && t < c.t1) cue = c; });
        out.push({ kind: "captions", text: cue ? (cue.who ? cue.who + ": " : "") + cue.lines.join(" ") : "" });
      } else out.push({ kind: L.kind, text: L.text || (L.words ? L.words.map(function (w) { return w.html; }).join(" / ") : "") });
    });
    return out;
  }
  function clicks() { // every cursor click time that is actually on screen (for the click sound in the mix)
    var out = [];
    layers.forEach(function (L) { (L.cursor || []).forEach(function (k) { if (k.click && k.t >= L.t0 && k.t < L.t1) out.push(Math.round(k.t * 1000) / 1000); }); });
    return out.sort(function (a, b) { return a - b; });
  }
  window.KAIROS = { load: load, caps: caps, E: E, clicks: clicks, describe: describe, qa: function () { return QA; }, duration: function () { return FILM.duration; } };
})();
