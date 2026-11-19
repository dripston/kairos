import os
import signal
import sqlite3
import tempfile
import threading
import unittest
import urllib.request

from kairos.db import connect
from kairos.seed import BORROWER_ID, seed
from kairos.web import Handler, KairosServer, serve


class ShutdownTest(unittest.TestCase):
    def test_clean_stop_finishes_requests_and_checkpoints_the_db(self):
        db = os.path.join(tempfile.mkdtemp(), "crm.db")
        conn = connect(db)
        seed(conn)
        conn.close()
        Handler.db_path = db
        server = KairosServer(("127.0.0.1", 0), Handler)
        out = {}
        t = threading.Thread(target=lambda: out.update(why=serve(server, db, install_signals=False)))
        t.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/borrowers/{BORROWER_ID}"
        self.assertEqual(urllib.request.urlopen(url, timeout=5).status, 200)
        server.shutdown()          # what SIGTERM / Ctrl-C lead to
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(out["why"], "stopped")
        self.assertFalse(os.path.exists(db + "-wal") and os.path.getsize(db + "-wal") > 0)  # WAL folded back
        c = sqlite3.connect(db)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_sigterm_becomes_a_clean_interrupt(self):
        from kairos.web import _raise_interrupt
        with self.assertRaises(KeyboardInterrupt) as e:
            _raise_interrupt(signal.SIGTERM, None)
        self.assertEqual(e.exception.args[0], signal.SIGTERM)
