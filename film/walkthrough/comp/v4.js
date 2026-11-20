/* Kairos films v4 layer kinds, plugged into engine.js through window.KAIROS_EXT.
 *
 *   nebula  slow real nebula footage, dimmed, full frame (the stage for every platform shot)
 *   glass   a COMPLETE real page in a frosted-glass window; an eased camera zooms inside the window
 *   phone   a still, clean phone frame showing real chat screenshots (no tracking, no composite)
 *   flow    a dark/glass flow diagram: nodes with bodies, labelled edges, a travelling pulse, lit nodes, mini mode
 *   seq     a sequence diagram: lifelines, arrows drawn in order, notes
 *   title   centred text lines (cold open, flash cards, chapter cards, slogans)
 *   panel   a glass panel of result rows
 *   brand   the Kairos logo on the nebula, with an optional line
 * All motion is eased (no springs with overshoot, no jitter); everything is a pure function of t.
 */
(function () {
  var X = window.KAIROS_EXT = window.KAIROS_EXT || {};
  var TONE = { teal: "#2DD4BF", amber: "#FBBF24", red: "#F87171", violet: "#A78BFA", slate: "#CBD5E1", green: "#4ADE80", blue: "#60A5FA" };
  function ease(A, x) { return A.E.move(A.clamp(x, 0, 1)); }
  function enterT(A, L, t) { var e = A.envelope(L, t); return { p: A.E.enter(e.p), q: A.E.enter(e.q) }; }
  function layerRoot(A, L) { var r = A.el("div", "layer", A.stage()); r.style.width = A.W + "px"; r.style.height = A.H + "px"; L.root = r; return r; }
  function seekVideo(A, L, want) {
    if (!L.loaded) return A.attachVideo(L).then(function () { return seekVideo(A, L, want); });
    if (Math.abs(want - (L.lastSeek == null ? -1 : L.lastSeek)) < 1e-4) return null;
    L.lastSeek = want;
    var shown = A.presented(L.v);
    return new Promise(function (res) {
      var done = false; function fin() { if (!done) { done = true; res(); } }
      L.v.addEventListener("seeked", fin, { once: true }); setTimeout(fin, 3000);
      L.v.currentTime = want + ((A.film().halfFrame) ? 1 / 120 : 0);
    }).then(function () { L.painted = true; return shown; });
  }

  // ------------------------------------------------------------------ nebula
  X.nebula = {
    build: function (L, A) {
      var r = layerRoot(A, L); r.style.background = "#02040a"; r.style.overflow = "hidden";
      L.v = A.el("video", "", r); L.v.muted = true; L.v.preload = "auto"; L.v.playsInline = true;
      L.v.style.cssText = "position:absolute;left:0;top:0;width:" + A.W + "px;height:" + A.H + "px;object-fit:cover;transform-origin:50% 50%";
      L.vid = true; L.src = L.src || "comp/NEB1.mov";
      L.v.style.filter = "brightness(0.82) saturate(1.05)";
      var dd = L.dim != null ? L.dim : 0.45;
      var tint = L.tint || "2,5,14";   // technical: a soft blue (e.g. "6,22,48") so diagrams and cards stay readable
      var dim = A.el("div", "", r); dim.style.cssText = "position:absolute;inset:0;background:radial-gradient(ellipse 60% 55% at 50% 45%, rgba(" + tint + "," + Math.min(0.85, dd + 0.25) + ") 0%, rgba(" + tint + "," + dd + ") 65%, rgba(" + tint + "," + Math.min(0.9, dd + 0.2) + ") 100%)";
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t);
      var life = A.clamp((t - L.t0) / Math.max(1, L.t1 - L.t0), 0, 1), z = L.zoom || [1.04, 1.1];
      L.v.style.transform = "scale(" + A.lerp(z[0], z[1], life) + ")";
      var want = (L.from || 0) + (t - L.t0) * (L.rate || 1.0);
      if (L.len) { var per = 2 * L.len, m = want % per; want = m <= L.len ? m : per - m; }   // ping-pong: seamless, a new frame every frame
      if (!L.shownOnce) L.v.style.opacity = 0;          // never show an unpainted video element (no white/black blob)
      var pr = seekVideo(A, L, want);
      return Promise.resolve(pr).then(function () { L.shownOnce = true; L.v.style.opacity = 1; });
    }
  };

  // ------------------------------------------------------------------ glass window with a whole real page
  // The page is shown WHOLE first. A zoom targets a whole component (+ margin): the camera eases to it and a soft
  // focus clip hides everything outside it, so no text or phone is ever cut by the window edge. Images are
  // double-buffered (the old frame stays until the new one is decoded and painted), and a window never shows
  // before its first frame is ready.
  X.glass = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      var page = L.page;                                   // [x, y, w, h] of the source shown WHOLE (CSS px of the capture)
      var c = L.cap ? A.caps[L.cap] : null;
      if (!page) page = c ? [0, 0, c.log.viewport.w, c.log.viewport.h] : [0, 0, L.vw || 1920, L.vh || 1080];
      L.pageR = page;
      var maxw = L.maxw || 1640, maxh = L.maxh || 810, pad = L.pad != null ? L.pad : 16, head = L.title ? 46 : 0;
      var s = Math.min((maxw - 2 * pad) / page[2], (maxh - 2 * pad - head) / page[3]);
      var pw = page[2] * s, ph = page[3] * s, ww = pw + 2 * pad, wh = ph + 2 * pad + head;
      L.geo = { s0: s, pw: pw, ph: ph, ww: ww, wh: wh, pad: pad, head: head, cx: L.cx || A.W / 2, cy: L.cy || 462 };
      var win = A.el("div", "glasswin", r); L.win = win;
      win.style.width = ww + "px"; win.style.height = wh + "px";
      win.style.left = (L.geo.cx - ww / 2) + "px"; win.style.top = (L.geo.cy - wh / 2) + "px";
      if (L.title) { var hd = A.el("div", "glasshead", win, '<i></i><i></i><i></i><span>' + A.esc(L.title) + '</span>'); hd.style.height = head + "px"; }
      var pane = A.el("div", "glasspane", win); L.pane = pane;
      pane.style.left = pad + "px"; pane.style.top = (pad + head) + "px"; pane.style.width = pw + "px"; pane.style.height = ph + "px";
      var inner = A.el("div", "", pane); inner.style.cssText = "position:absolute;left:0;top:0;transform-origin:0 0"; L.inner = inner;
      if (L.video) {
        L.v = A.el("video", "", inner); L.v.muted = true; L.v.preload = "auto"; L.v.playsInline = true; L.vid = true; L.src = L.video;
        L.v.style.cssText = "position:absolute;left:0;top:0;width:" + (L.vw || 1920) + "px;height:" + (L.vh || 1080) + "px";
        L.media = [L.v];
      } else {
        L.ims = [0, 1].map(function () { var im = A.el("img", "", inner); im.style.cssText = "position:absolute;left:0;top:0;max-width:none;opacity:0"; return im; });
        L.front = 0; L.media = L.ims;
        L.track = A.resolveTrack(c, L.frames || [{ at: 0, label: L.label }]);
      }
      if (L.cursor) {
        L.cur = A.el("div", "cursor", r, '<svg viewBox="0 0 32 32" width="44" height="44"><path d="M6 3 L6 26 L12 20.5 L16.2 29.4 L20.3 27.5 L16.2 18.8 L24 18.8 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>');
        L.rip = A.el("div", "ripple", r);
      }
      L.win.style.opacity = 0;
    },
    update: function (L, t, A) {
      var g = L.geo, e = enterT(A, L, t);
      var ty = 0, sc = 1, op = e.p * e.q, type = (L.enter || {}).type || "rise", xt = (L.exit || {}).type || "fade";
      if (type === "rise") { ty += (1 - e.p) * 40; sc *= A.lerp(0.97, 1, e.p); }
      if (type === "zoom") { sc *= A.lerp(0.86, 1, e.p); }
      if (type === "push") { sc *= A.lerp(1.18, 1, e.p); }
      if (xt === "zoom") { sc *= A.lerp(1.25, 1, e.q); }
      if (xt === "push") { sc *= A.lerp(0.9, 1, e.q); }
      // the focus: the whole page, or a whole component (+ margin), eased from key to key (one continuous move)
      var P = L.pageR, keys = L.cam || [], full = [P[0], P[1], P[2], P[3]], M = L.margin != null ? L.margin : 10;
      function grow(rc) { var x0 = Math.max(P[0], rc[0] - M), y0 = Math.max(P[1], rc[1] - M); return [x0, y0, Math.min(P[0] + P[2], rc[0] + rc[2] + M) - x0, Math.min(P[1] + P[3], rc[1] + rc[3] + M) - y0]; }
      var foc = full;
      keys.forEach(function (k) {
        if (t < k.t) return;
        var tf = k.rect ? grow(k.rect) : full, s = ease(A, (t - k.t) / (k.dur || 1.2));
        foc = [A.lerp(foc[0], tf[0], s), A.lerp(foc[1], tf[1], s), A.lerp(foc[2], tf[2], s), A.lerp(foc[3], tf[3], s)];
      });
      // the window wraps the focus exactly: scale k (capped so captures are never upscaled), centred
      var maxw = L.maxw || 1640, maxh = L.maxh || 810, kmax = L.kmax || 2.0;
      var k = Math.min((maxw - 2 * g.pad) / foc[2], (maxh - 2 * g.pad - g.head) / foc[3], kmax);
      var pw = foc[2] * k, ph = foc[3] * k, ww = pw + 2 * g.pad, wh = ph + 2 * g.pad + g.head;
      L.win.style.width = ww + "px"; L.win.style.height = wh + "px";
      L.win.style.left = (g.cx - ww / 2) + "px"; L.win.style.top = (g.cy - wh / 2) + "px";
      L.pane.style.width = pw + "px"; L.pane.style.height = ph + "px";
      L.win.style.transform = "translateY(" + ty + "px) scale(" + sc + ")";
      L.inner.style.transform = "scale(" + k + ") translate(" + (-foc[0]) + "px," + (-foc[1]) + "px)";
      L.subjectNow = [[g.cx - ww / 2, g.cy - wh / 2, ww, wh]];
      if (L.cur) {
        var cs = A.cursorAt(L, t);
        if (cs && cs.vis > 0) {
          var px = g.cx - ww / 2 + g.pad + (cs.pos[0] - foc[0]) * k, py = g.cy - wh / 2 + g.pad + g.head + (cs.pos[1] - foc[1]) * k;
          var press = cs.click != null ? t - cs.click : 99, psc = press >= 0 && press < 0.2 ? 0.92 + 0.08 * A.E.enter(press / 0.2) : 1;
          L.cur.style.display = ""; L.cur.style.opacity = cs.vis * op;
          L.cur.style.transform = "translate(" + (px - 6) + "px," + (py - 4) + "px) scale(" + psc + ")";
          if (press >= 0 && press < 0.32) { var rp = A.E.enter(press / 0.32); L.rip.style.display = ""; L.rip.style.opacity = 0.55 * (1 - rp); L.rip.style.transform = "translate(" + px + "px," + py + "px) scale(" + A.lerp(0.42, 1, rp) + ")"; }
          else L.rip.style.display = "none";
        } else { L.cur.style.display = "none"; L.rip.style.display = "none"; }
      }
      // never show the window before its content has been painted for one committed frame
      function reveal() { if (L.ready && L.readyAt != null && L.readyAt !== t) L.win.style.opacity = op; else L.win.style.opacity = 0; }   // shown from the frame after its first image was painted
      if (L.video) {
        var want = (L.from || 0) + (t - L.t0) * (L.rate || 1);
        reveal();
        return Promise.resolve(seekVideo(A, L, want)).then(function () { if (!L.ready) { A.fresh(); L.readyAt = t; } L.ready = true; });
      }
      reveal();
      var c = A.caps[L.cap], fi = A.trackAt(L.track, t), url = A.frameUrl(c, fi);
      var front = L.ims[L.front], back = L.ims[1 - L.front];
      if (L.hideOld) { back.style.opacity = 0; L.hideOld = false; }        // the previous frame stayed one painted frame under the new one
      if (L.curUrl === url) return null;
      return A.getImg(url).then(function () {
        back.src = url; back.style.width = c.log.viewport.w + "px"; back.style.height = c.log.viewport.h + "px";
        return back.decode().catch(function () {});
      }).then(function () {
        back.style.zIndex = 2; front.style.zIndex = 1; back.style.opacity = 1;
        L.front = 1 - L.front; L.curUrl = url; L.hideOld = true; A.fresh(); if (!L.ready) L.readyAt = t; L.ready = true;
      });
    }
  };

  // ------------------------------------------------------------------ phone: a still frame with real chat screenshots
  X.phone = {
    build: function (L, A) {
      var r = layerRoot(A, L), h = L.h || 860, w = (h - 32) * (L.aspect || 0.4756) + 32;
      var ph = A.el("div", "phonebody", r); L.ph = ph;
      ph.style.width = w + "px"; ph.style.height = h + "px";
      ph.style.left = ((L.cx || A.W / 2) - w / 2) + "px"; ph.style.top = ((L.cy || A.H / 2) - h / 2) + "px";
      var scr = A.el("div", "phonescreen", ph); L.scr = scr;
      L.ims = (L.imgs || []).map(function (s) { var im = A.el("img", "", scr); im.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0"; return { s: s, im: im }; });
      A.el("div", "phonenotch", ph);
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t), life = A.clamp((t - L.t0) / Math.max(1, L.t1 - L.t0), 0, 1), pz = L.push || [1.0, 1.05];
      L.ph.style.opacity = e.p * e.q;
      L.ph.style.transform = "translateY(" + ((1 - e.p) * 30) + "px) scale(" + A.lerp(pz[0], pz[1], A.E.move(life)) + ")";
      var pend = [];
      L.ims.forEach(function (o, i) {
        var t0 = o.s.t, t1 = i + 1 < L.ims.length ? L.ims[i + 1].s.t : 1e9;
        var a = t < t0 ? 0 : A.clamp((t - t0) / 0.25, 0, 1);
        if (t >= t1) a = Math.max(0, 1 - (t - t1) / 0.25) > 0 ? 1 : 0;  // the next image fades in over it
        o.im.style.opacity = a;
        if (a > 0 && !o.loaded) { o.loaded = true; o.im.src = A.base + o.s.src; A.fresh(); pend.push(o.im.decode().catch(function () {})); }
      });
      L.subjectNow = [[parseFloat(L.ph.style.left), parseFloat(L.ph.style.top), parseFloat(L.ph.style.width), parseFloat(L.ph.style.height)]];
      return pend.length ? Promise.all(pend) : null;
    }
  };

  // ------------------------------------------------------------------ flow diagram (dark glass)
  function edgePath(a, b, e) {
    if (e.pts) return [[a.x + a.w / 2, a.y + a.h / 2]].concat(e.pts).concat([[b.x + b.w / 2, b.y + b.h / 2]]);
    var ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
    return [[ax, ay], [bx, by]];
  }
  function clipToBox(p, q, n) { // point where segment p→q leaves box n (p inside n)
    var cx = n.x + n.w / 2, cy = n.y + n.h / 2, dx = q[0] - p[0], dy = q[1] - p[1];
    var tx = dx ? ((dx > 0 ? n.x + n.w : n.x) - p[0]) / dx : 1e9, ty = dy ? ((dy > 0 ? n.y + n.h : n.y) - p[1]) / dy : 1e9;
    var s = Math.min(Math.abs(tx), Math.abs(ty)); s = Math.min(1, s); return [p[0] + dx * s, p[1] + dy * s];
  }
  X.flow = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      var wrap = A.el("div", "", r); L.wrap = wrap; wrap.style.cssText = "position:absolute;left:0;top:0;width:" + A.W + "px;height:" + A.H + "px;transform-origin:0 0";
      if (L.mini) { var pl = A.el("div", "glassmini", r); L.plate = pl; }
      if (L.mini) r.insertBefore(L.plate, wrap);
      var svg = A.sv("svg", { width: A.W, height: A.H, viewBox: "0 0 " + A.W + " " + A.H }, wrap); L.svg = svg;
      var defs = A.sv("defs", {}, svg);
      var mk = A.sv("marker", { id: "fah" + L.z, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "8", markerHeight: "8", orient: "auto-start-reverse" }, defs);
      A.sv("path", { d: "M0 0 L10 5 L0 10 z", fill: "rgba(226,232,240,.85)" }, mk);
      var glow = A.sv("filter", { id: "fglow" + L.z, x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
      A.sv("feGaussianBlur", { stdDeviation: "6" }, glow);
      L.byId = {}; L.nodes.forEach(function (n) { L.byId[n.id] = n; });
      L.edgeEls = (L.edges || []).map(function (e) {
        var a = L.byId[e.from], b = L.byId[e.to], pts = edgePath(a, b, e);
        pts[0] = clipToBox(pts[0], pts[1], a); pts[pts.length - 1] = clipToBox(pts[pts.length - 1], pts[pts.length - 2], b);
        var d = "M" + pts.map(function (p) { return p[0] + " " + p[1]; }).join(" L");
        var path = A.sv("path", { d: d, fill: "none", stroke: "rgba(226,232,240,.55)", "stroke-width": 3, "marker-end": "url(#fah" + L.z + ")", "stroke-dasharray": e.dashed ? "10 9" : "" }, svg);
        var len = 0; for (var i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
        var lab = null;
        if (e.label) {
          var mi = Math.floor((pts.length - 1) / 2), m0 = pts[mi], m1 = pts[mi + 1];
          lab = A.sv("text", { x: (m0[0] + m1[0]) / 2 + (e.lx || 0), y: (m0[1] + m1[1]) / 2 - 12 + (e.ly || 0), "text-anchor": "middle", "class": "flab" }, svg); lab.textContent = e.label;
        }
        return { e: e, path: path, pts: pts, len: len, lab: lab };
      });
      var ne_body;
      L.nodeEls = L.nodes.map(function (n) {
        var g = A.sv("g", {}, svg), col = TONE[n.tone || "teal"] || n.tone;
        var halo = A.sv("rect", { x: n.x - 6, y: n.y - 6, width: n.w + 12, height: n.h + 12, rx: 26, fill: "none", stroke: col, "stroke-width": 6, opacity: 0, filter: "url(#fglow" + L.z + ")" }, g);
        A.sv("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 22, "class": "fnode" }, g);
        A.sv("rect", { x: n.x, y: n.y + 18, width: 6, height: n.h - 36, rx: 3, fill: col }, g);
        var y = n.y + (n.body && n.body.length ? 66 : n.h / 2 + 16), tx = n.x + 36;
        var t1 = A.sv("text", { x: tx, y: y, "class": "ftitle" }, g); t1.textContent = n.title;
        ne_body = (n.body || []).map(function (line, i) { var tb = A.sv("text", { x: tx, y: y + 58 + i * 50, "class": "fbody" }, g); tb.textContent = line; return tb; });
        if (n.tag) { var tg = A.sv("text", { x: n.x + n.w - 26, y: n.y + 40, "text-anchor": "end", "class": "ftag", fill: col }, g); tg.textContent = n.tag; }
        g.style.transformBox = "fill-box"; g.style.transformOrigin = "50% 50%";
        return { n: n, g: g, halo: halo, body: ne_body };
      });
      L.dot = A.sv("circle", { r: 10, fill: "#F0FDFA", opacity: 0 }, svg);
      L.dotGlow = A.sv("circle", { r: 26, fill: "#2DD4BF", opacity: 0, filter: "url(#fglow" + L.z + ")" }, svg);
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t);
      L.root.style.opacity = e.p * e.q;
      if (L.mini) {
        var m = L.mini, s = m.s || 0.28;
        L.wrap.style.transform = "translate(" + m.x + "px," + m.y + "px) scale(" + s + ")";
        L.plate.style.left = (m.x - 14) + "px"; L.plate.style.top = (m.y - 14) + "px";
        L.plate.style.width = (A.W * s + 28) + "px"; L.plate.style.height = ((m.h || A.H) * s + 28) + "px";
      } else {
        L.wrap.style.transform = "none";
      }
      var lit = {}; (L.lit || []).forEach(function (k) { if (t >= k.t0 && t < k.t1) k.nodes.forEach(function (id) { lit[id] = Math.min(1, (t - k.t0) / 0.3, (k.t1 - t) / 0.3); }); });
      L.nodeEls.forEach(function (ne) {
        var a = A.E.enter(A.clamp((t - (ne.n.t != null ? ne.n.t : L.t0)) / 0.45, 0, 1));
        ne.g.style.opacity = a; ne.g.style.transform = "translateY(" + (1 - a) * 18 + "px) scale(" + A.lerp(0.94, 1, a) + ")";
        var anyLit = Object.keys(lit).length > 0, l = lit[ne.n.id] || 0;
        ne.halo.setAttribute("opacity", 0.85 * l);
        if (anyLit && !lit[ne.n.id]) ne.g.style.opacity = a * 0.45;
        if (ne.n.bodyT) ne.body.forEach(function (tb, i) {   // every check is there from the start, dimmed; each lights as it is spoken
          var bt = ne.n.bodyT[i], k = bt == null ? 1 : A.E.enter(A.clamp((t - bt) / 0.4, 0, 1));
          tb.style.opacity = 0.38 + 0.62 * k; tb.style.fill = t >= bt && t < bt + 1.8 ? "#7DF3E1" : "";
        });
      });
      L.edgeEls.forEach(function (ee) {
        var a = A.clamp((t - (ee.e.t != null ? ee.e.t : L.t0)) / 0.5, 0, 1), s = A.E.move(a);
        ee.path.style.strokeDasharray = ee.e.dashed ? "10 9" : ee.len + " " + ee.len;
        if (!ee.e.dashed) ee.path.style.strokeDashoffset = (1 - s) * ee.len;
        ee.path.style.opacity = a > 0 ? 1 : 0;
        if (ee.lab) ee.lab.style.opacity = A.clamp((t - (ee.e.t || L.t0) - 0.3) / 0.3, 0, 1);
      });
      // a pulse travelling node to node along the path
      var on = false;
      (L.pulses || []).forEach(function (pu) {
        var seg = pu.dur || 0.7, n = pu.path.length - 1, k = (t - pu.t) / seg;
        if (pu.loop) k = k % (n + (pu.rest || 0));
        if (k < 0 || k >= n) return;
        var i = Math.floor(k), f = A.E.move(k - i), a = L.byId[pu.path[i]], b = L.byId[pu.path[i + 1]];
        var ee = L.edgeEls.filter(function (x) { return x.e.from === a.id && x.e.to === b.id; })[0];
        var pts = ee ? ee.pts : [[a.x + a.w / 2, a.y + a.h / 2], [b.x + b.w / 2, b.y + b.h / 2]];
        var tot = 0, segs = []; for (var j = 1; j < pts.length; j++) { var d = Math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]); segs.push(d); tot += d; }
        var want = f * tot, p = pts[0];
        for (j = 1; j < pts.length; j++) { if (want <= segs[j - 1]) { var q = want / segs[j - 1]; p = [A.lerp(pts[j - 1][0], pts[j][0], q), A.lerp(pts[j - 1][1], pts[j][1], q)]; break; } want -= segs[j - 1]; p = pts[j]; }
        L.dot.setAttribute("cx", p[0]); L.dot.setAttribute("cy", p[1]); L.dotGlow.setAttribute("cx", p[0]); L.dotGlow.setAttribute("cy", p[1]);
        on = true;
      });
      L.dot.setAttribute("opacity", on ? 1 : 0); L.dotGlow.setAttribute("opacity", on ? 0.8 : 0);
      return null;
    }
  };

  // ------------------------------------------------------------------ sequence diagram
  X.seq = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      var svg = A.sv("svg", { width: A.W, height: A.H, viewBox: "0 0 " + A.W + " " + A.H }, r); L.svg = svg;
      var defs = A.sv("defs", {}, svg);
      var mk = A.sv("marker", { id: "sah" + L.z, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "8", markerHeight: "8", orient: "auto-start-reverse" }, defs);
      A.sv("path", { d: "M0 0 L10 5 L0 10 z", fill: "#E2E8F0" }, mk);
      var top = L.top || 150, bottom = L.bottom || 960, row = L.row || 74, y0 = top + 120;
      L.actors.forEach(function (a) {
        var col = TONE[a.tone || "slate"] || a.tone;
        a.g = A.sv("g", {}, svg);
        A.sv("rect", { x: a.x - 150, y: top, width: 300, height: 84, rx: 18, "class": "fnode" }, a.g);
        A.sv("rect", { x: a.x - 150, y: top + 80, width: 300, height: 4, rx: 2, fill: col }, a.g);
        var tt = A.sv("text", { x: a.x, y: top + 54, "text-anchor": "middle", "class": "ftitle" }, a.g); tt.textContent = a.label;
        A.sv("line", { x1: a.x, y1: top + 84, x2: a.x, y2: bottom, stroke: "rgba(226,232,240,.28)", "stroke-width": 2, "stroke-dasharray": "6 8" }, a.g);
      });
      var by = {}; L.actors.forEach(function (a) { by[a.id] = a; });
      L.msgEls = L.msgs.map(function (m, i) {
        var y = y0 + i * row + (m.dy || 0), a = by[m.from], b = by[m.to || m.from], g = A.sv("g", {}, svg), col = TONE[m.tone || "teal"] || m.tone;
        var x1 = a.x, x2 = b.x, d, len, lx;
        var left = m.from === (m.to || m.from) && x1 > 1300;
        if (m.from === (m.to || m.from)) { d = left ? "M" + x1 + " " + y + " h -90 v 34 h 84" : "M" + x1 + " " + y + " h 90 v 34 h -84"; len = 214; lx = left ? x1 - 110 : x1 + 110; }
        else { var dir = x2 > x1 ? 1 : -1; d = "M" + (x1 + dir * 6) + " " + y + " L" + (x2 - dir * 10) + " " + y; len = Math.abs(x2 - x1); lx = (x1 + x2) / 2; }
        var p = A.sv("path", { d: d, fill: "none", stroke: m.tone === "red" ? TONE.red : "#E2E8F0", "stroke-width": 3, "marker-end": "url(#sah" + L.z + ")", "stroke-dasharray": m.ret ? "9 8" : "" }, g);
        var anc = m.from === (m.to || m.from) ? (left ? "end" : "start") : "middle";
        var lab = A.sv("text", { x: lx, y: y - 14, "text-anchor": anc, "class": "slab", fill: col }, g); lab.textContent = m.label;
        var note = null; if (m.note) { note = A.sv("text", { x: lx, y: y + 34, "text-anchor": anc, "class": "snote" }, g); note.textContent = m.note; }
        return { m: m, g: g, p: p, len: len };
      });
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t); L.root.style.opacity = e.p * e.q;
      L.actors.forEach(function (a) { var k = A.E.enter(A.clamp((t - (a.t != null ? a.t : L.t0)) / 0.4, 0, 1)); a.g.style.opacity = k; });
      L.msgEls.forEach(function (me) {
        var k = A.clamp((t - me.m.t) / 0.5, 0, 1), s = A.E.move(k);
        me.g.style.opacity = k > 0 ? 1 : 0;
        if (!me.m.ret) { me.p.style.strokeDasharray = me.len + " " + me.len; me.p.style.strokeDashoffset = (1 - s) * me.len; }
        var lit = me.m.t1 != null && t >= me.m.t && t < me.m.t1;
        me.g.style.filter = lit ? "drop-shadow(0 0 10px rgba(45,212,191,.8))" : "";
      });
      return null;
    }
  };

  // ------------------------------------------------------------------ title (centred lines)
  X.title = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      if (L.bg) { var b = A.el("div", "", r); b.style.cssText = "position:absolute;inset:0;background:" + L.bg; L.bgEl = b; }
      var box = A.el("div", "v4title", r); L.box = box;
      box.style.top = (L.y != null ? L.y : A.H / 2) + "px"; if (L.maxw) box.style.maxWidth = L.maxw + "px";
      if (L.align) box.style.textAlign = L.align;
      L.parts = L.lines.map(function (ln) {
        var d = A.el("div", "v4line" + (ln.cls ? " " + ln.cls : ""), box, ln.html || A.esc(ln.text));
        d.style.fontSize = (ln.size || 64) + "px"; if (ln.weight) d.style.fontWeight = ln.weight; if (ln.color) d.style.color = ln.color;
        if (ln.track != null) d.style.letterSpacing = ln.track + "em"; if (ln.mt != null) d.style.marginTop = ln.mt + "px";
        if (ln.mono) d.style.fontFamily = "ui-monospace, SFMono-Regular, Menlo, monospace";
        return { ln: ln, d: d };
      });
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t);
      L.root.style.opacity = L.bg ? 1 : 1;
      L.box.style.opacity = e.q;
      var life = A.clamp((t - L.t0) / Math.max(1, L.t1 - L.t0), 0, 1);
      L.box.style.transform = "translate(-50%,-50%)";   // text holds still (no sub-pixel scale drift)
      if (L.bgDrift && L.bgEl) { var lf = A.clamp((t - L.t0) / Math.max(1, L.t1 - L.t0), 0, 1); L.bgEl.style.backgroundPosition = (40 + 20 * lf) + "% 50%"; }  // a slow light on the background only
      L.parts.forEach(function (p, i) {
        var t0 = p.ln.t != null ? p.ln.t : L.t0 - 0.3 + i * (L.stagger != null ? L.stagger : 0.18);
        var k = A.E.enter(A.clamp((t - t0) / (p.ln.dur || 0.6), 0, 1));
        p.d.style.opacity = k * (p.ln.opacity != null ? p.ln.opacity : 1);
        p.d.style.transform = "translateY(" + (1 - k) * (p.ln.rise != null ? p.ln.rise : 18) + "px)";
        if (p.ln.blur) p.d.style.filter = "blur(" + (1 - k) * 8 + "px)";
      });
      return null;
    }
  };

  // ------------------------------------------------------------------ panel: glass result rows
  X.panel = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      var p = A.el("div", "glasspanel", r); L.p = p;
      p.style.width = (L.w || 1280) + "px"; p.style.left = ((L.cx || A.W / 2) - (L.w || 1280) / 2) + "px"; p.style.top = (L.y || 150) + "px";
      if (L.heading) A.el("div", "gphead", p, A.esc(L.heading));
      L.rows = L.items.map(function (it) {
        var row = A.el("div", "gprow", p, '<div class="gpbig">' + (it.html || A.esc(it.big)) + '</div>' + (it.small ? '<div class="gpsmall">' + A.esc(it.small) + "</div>" : ""));
        return { it: it, row: row };
      });
      if (L.fine) L.fineEl = A.el("div", "gpfine", p, A.esc(L.fine));
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t);
      L.p.style.opacity = e.p * e.q; L.p.style.transform = "translateY(" + (1 - e.p) * 30 + "px)";
      L.rows.forEach(function (r, i) {
        var k = A.E.enter(A.clamp((t - (r.it.t != null ? r.it.t : L.t0 + 0.3 + i * 0.35)) / 0.5, 0, 1));
        r.row.style.opacity = k; r.row.style.transform = "translateX(" + (1 - k) * -24 + "px)";
      });
      if (L.fineEl) L.fineEl.style.opacity = A.clamp((t - L.t0 - 0.6 - L.rows.length * 0.35) / 0.5, 0, 1);
      L.subjectNow = [[parseFloat(L.p.style.left), parseFloat(L.p.style.top), L.w || 1280, L.p.offsetHeight]];
      return null;
    }
  };

  // ------------------------------------------------------------------ brand: logo + line on the nebula
  X.brand = {
    build: function (L, A) {
      var r = layerRoot(A, L);
      if (L.bg) { var b = A.el("div", "", r); b.style.cssText = "position:absolute;inset:0;background:" + L.bg; }
      var box = A.el("div", "v4title", r); L.box = box; box.style.top = (L.y || 500) + "px";
      L.logo = A.el("div", "v4logo", box, '<span class="mk">K</span><span class="wm">Kairos</span>');
      L.lns = (L.lines || []).map(function (s, i) { var d = A.el("div", "v4line", box, A.esc(s)); d.style.fontSize = (L.lineSize || 54) + "px"; d.style.fontWeight = 600; d.style.marginTop = (i ? 18 : 36) + "px"; return d; });
      if (L.credits) { L.cr = A.el("div", "v4credits", r, L.credits); }
    },
    update: function (L, t, A) {
      var e = enterT(A, L, t), k = A.E.enter(A.clamp((t - L.t0) / 0.9, 0, 1));
      L.box.style.opacity = e.q;
      L.box.style.transform = "translate(-50%,-50%) scale(" + A.lerp(L.from || 0.92, 1, k) + ")";
      L.logo.style.opacity = k; L.logo.style.filter = "blur(" + (1 - k) * 10 + "px)";
      L.lns.forEach(function (d, i) { var kk = A.E.enter(A.clamp((t - L.t0 - (L.lineAt || 0.7) - i * 0.5) / 0.6, 0, 1)); d.style.opacity = kk; d.style.transform = "translateY(" + (1 - kk) * 14 + "px)"; });
      if (L.cr) L.cr.style.opacity = A.clamp((t - L.t0 - 1.0) / 0.6, 0, 1) * e.q;
      return null;
    }
  };
})();
