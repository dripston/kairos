/* the real red-team run on its own, for the engineer's-monitor composite (technical film) */
window.FILM = { duration: 8, bg: "#16181A", terms: { redteam: "term/redteam.json" },
  layers: [{ kind: "term", t0: -1, t1: 8, title: "kairos/backend — zsh", prompt: "%", rows: 15, maxCols: 80, home: [0, 0, 1920], drift: false,
    enter: { dur: 0 }, exit: { dur: 0 }, cam: [{ t: 0, full: true }],
    runs: [{ rec: "redteam", at: -1, cadence: 0.001, fit: 2.0, keep: "PASS|FAIL|attacks:", hi: "49 PASS", badge: "3.7 s run" }] }] };
