#!/usr/bin/env python3
"""Run a real command and record its output with timestamps, for the terminal shots.

    python3 termrec.py <out.json> <cwd> <shown command> -- <argv...>

Writes {"cmd": shown, "cwd": ..., "started": iso, "lines": [[t_seconds, text], ...], "exit": code, "dur": s}.
stdout and stderr are merged, ANSI codes stripped. Secrets never appear in these commands' output; any
sk_ token is masked anyway.
"""
import datetime, json, os, re, subprocess, sys, time

out, cwd, shown = sys.argv[1], sys.argv[2], sys.argv[3]
argv = sys.argv[sys.argv.index("--") + 1:]
env = dict(os.environ, PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb", COLUMNS="100")
t0 = time.time()
p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, bufsize=1)
lines = []
ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
for raw in p.stdout:
    s = ansi.sub("", raw.rstrip("\n"))
    s = re.sub(r"sk_[A-Za-z0-9_]+", "sk_***", s)
    lines.append([round(time.time() - t0, 3), s])
code = p.wait()
json.dump({"cmd": shown, "cwd": cwd, "started": datetime.datetime.now().isoformat(timespec="seconds"),
           "lines": lines, "exit": code, "dur": round(time.time() - t0, 2)}, open(out, "w"), ensure_ascii=False, indent=0)
print(out, "exit", code, "lines", len(lines), "dur", round(time.time() - t0, 1))
