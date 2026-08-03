#!/usr/bin/env python3
"""Local Discord-webhook mock: captures POST payloads for offline testing.

Verifies the detector's alert path end-to-end without a real Discord channel.

Usage (terminal A):
  python3 tools/mock_webhook.py --port 8099 --log /tmp/webhook_capture.jsonl

Usage (terminal B):
  python3 detector.py --test-webhook --webhook http://127.0.0.1:8099/hook
"""

import argparse
import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOG_PATH = "/tmp/webhook_capture.jsonl"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode(errors="replace")
        record = {
            "t": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "body": body,
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"[mock_webhook] POST {self.path}: {body[:200]}", flush=True)
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, format, *args):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--log", default="/tmp/webhook_capture.jsonl")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    global LOG_PATH
    LOG_PATH = args.log
    print(f"[mock_webhook] listening on http://{args.host}:{args.port} "
          f"(log: {args.log})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock_webhook] stopped", flush=True)


if __name__ == "__main__":
    main()
