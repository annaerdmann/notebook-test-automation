"""`notebook-test dashboard --serve`: serve the dashboard page and its data from a folder of reports.

The reports come from a folder or from a git URL. A URL is cloned into the cache folder on start and
updated on a refresh (the button on the page, and on page load when the last update is over 10 minutes
old). The server binds to 127.0.0.1 only and keeps nothing on disk besides the cached clone: every
number is computed from the reports.
"""

import hashlib
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .status import build_issues, build_status, issues_markdown, llms_txt, load_reports

PAGE = Path(__file__).parent / "dashboard" / "index.html"
STALE_AFTER = 600  # seconds


class Source:
    """A folder of reports, or a git repository cloned into `cache`."""

    def __init__(self, source, cache):
        self.url = None if Path(source).expanduser().is_dir() else source
        self.folder = Path(source).expanduser().resolve() if not self.url else \
            Path(cache).expanduser() / hashlib.sha1(source.encode()).hexdigest()[:10]
        self.lock = threading.Lock()
        self.updated = 0.0
        self.error = None
        self._data = None

    def _git(self, *args):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env)

    def refresh(self):
        """Pull the newest reports. Returns an error message, or None."""
        with self.lock:
            try:
                if self.url:
                    if (self.folder / ".git").exists():
                        self._git("-C", str(self.folder), "fetch", "--depth", "1", "origin")
                        self._git("-C", str(self.folder), "reset", "--hard", "FETCH_HEAD")
                    else:
                        self.folder.parent.mkdir(parents=True, exist_ok=True)
                        self._git("clone", "--depth", "1", self.url, str(self.folder))
                self.error = None
            except subprocess.CalledProcessError as e:
                self.error = f"git failed: {(e.stderr or '').strip()[-300:]}"
            self.updated = time.time()
            self._data = None
            return self.error

    def data(self):
        with self.lock:
            if self._data is None:
                reports = load_reports(self.folder) if self.folder.is_dir() else []
                status, issues = build_status(reports), build_issues(reports)
                status["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.updated))
                status["source"] = self.url or str(self.folder)
                status["source_error"] = self.error
                self._data = {"status": status, "issues": issues, "reports": len(reports)}
            return self._data


def make_handler(source):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype, code=200):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _local_host(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost", "[::1]")

        def do_GET(self):
            if not self._local_host():
                return self._send("forbidden", "text/plain", 403)
            path = self.path.split("?")[0]
            if path == "/":
                if source.url and time.time() - source.updated > STALE_AFTER:
                    source.refresh()
                return self._send(PAGE.read_text(), "text/html; charset=utf-8")
            data = source.data()
            if path == "/status.json":
                return self._send(json.dumps(data["status"], indent=1), "application/json")
            if path == "/issues.json":
                return self._send(json.dumps(data["issues"], indent=1), "application/json")
            if path == "/issues.md":
                return self._send(issues_markdown(data["issues"], data["status"]["labels"]), "text/markdown; charset=utf-8")
            if path == "/llms.txt":
                return self._send(llms_txt(data["status"]), "text/plain; charset=utf-8")
            self._send("not found", "text/plain", 404)

        def do_POST(self):
            if self.path != "/refresh" or not self._local_host() or self.headers.get("Origin", "").rsplit(":", 1)[0] \
                    not in ("", "http://127.0.0.1", "http://localhost"):
                return self._send("forbidden", "text/plain", 403)
            error = source.refresh()
            self._send(json.dumps({"error": error}), "application/json")

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(source, port=8765, cache="~/.cache/notebook-test/results"):
    src = Source(source, cache)
    error = src.refresh()
    if error:
        print(f"notebook-test: {error}")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(src))
    print(f"Dashboard: http://127.0.0.1:{port}/  ({src.data()['reports']} report(s) from {src.url or src.folder}; Ctrl+C stops)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
