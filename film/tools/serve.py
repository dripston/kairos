#!/usr/bin/env python3
"""Serve the film folder with HTTP Range support, so the soundtrack can seek with the scrubber.

    python3 film/tools/serve.py [port]      # then open http://127.0.0.1:8765/kairos_film.html

(Python's plain `http.server` has no Range support, so browsers cannot seek audio served by it.)
"""
import http.server
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        start = int(m.group(1)) if m and m.group(1) else 0
        end = min(int(m.group(2)) if m and m.group(2) else size - 1, size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, src, dst):
        n = getattr(self, "_remaining", None)
        if n is None:
            return super().copyfile(src, dst)
        try:
            while n > 0:
                chunk = src.read(min(65536, n))
                if not chunk:
                    break
                dst.write(chunk)
                n -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self._remaining = None


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://127.0.0.1:{port}/kairos_film.html", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
