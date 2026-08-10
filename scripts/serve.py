#!/usr/bin/env python3
"""
Serve the viewer locally. Stdlib http.server with two deliberate changes.

    python scripts/serve.py [--port 8000] [--root viewer]

WHY NOT PLAIN `python -m http.server`
-------------------------------------
Both defaults are wrong for this project in ways that cost real debugging
time, and neither is reachable through the module's CLI.

  1. It binds every interface. That publishes the whole build output -
     and anything else under the server root - to the local network the
     moment you run it on a shared or untrusted one. This binds loopback.

  2. It sends no cache headers at all, which leaves a browser free to
     reuse index.html indefinitely. Editing the viewer and reloading then
     shows the previous file, so you debug a copy that no longer exists
     on disk. Ask anyone who has changed a line, reloaded, seen no
     difference, and gone looking for the bug in the data instead.
     Cache-Control: no-store ends that.

The root stays at viewer/ rather than the repo root, with data/ exposed
through a symlink the Makefile creates. Serving the repo root instead
would put .env - which holds the API key - one HTTP request away.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class NoStoreHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that forbids caching.

    end_headers is the one hook that runs for every response - files,
    directory listings, 304s and errors alike - so the header cannot be
    missed on whichever path happens to serve index.html.
    """

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):
        # no-store alone is not enough during the changeover. A browser
        # that cached index.html *before* this server started sending the
        # header still holds that copy and will revalidate it with
        # If-Modified-Since; SimpleHTTPRequestHandler answers 304 and the
        # browser reuses the stale file - reviving the exact bug this
        # class exists to kill, on precisely the reload where you would
        # least expect it. Drop the conditional headers so every request
        # gets a full 200.
        for header in ("If-Modified-Since", "If-None-Match"):
            del self.headers[header]
        return super().send_head()

    def log_message(self, fmt, *args):
        # Default logs every request to stderr, which buries the one line
        # that matters (the URL to open) under basemap tile noise.
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--root", default="viewer",
                    help="server root, relative to the repo (default: viewer)")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="interface to bind (default: loopback only)")
    args = ap.parse_args()

    root = (ROOT / args.root).resolve()
    if not root.is_dir():
        raise SystemExit("No such directory: %s" % root)

    # partial, not a subclass attribute: SimpleHTTPRequestHandler.__init__
    # assigns self.directory from its keyword argument, so a class-level
    # `directory` is silently overwritten with os.getcwd() and the server
    # quietly roots itself at the repo - exactly the mistake this module
    # exists to prevent.
    handler = functools.partial(NoStoreHandler, directory=str(root))

    # allow_reuse_address so a restart after Ctrl-C is not refused for the
    # length of TIME_WAIT.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.bind, args.port), handler) as httpd:
        print("http://%s:%d" % (args.bind, args.port), flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(flush=True)


if __name__ == "__main__":
    main()
