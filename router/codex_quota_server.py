#!/usr/bin/env python3
"""
Codex Quota HTTP Server
读取 codex_keepalive.sh 写入的状态文件，返回解析后的 JSON
"""
import json
import logging
import os
import re
import sys
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("codex-quota")

PORT = 18432
STATUS_FILE = "/tmp/codex_status.json"


def parse_status(raw: str) -> dict:
    result = {
        "account": None,
        "plan": None,
        "model": None,
        "hourly_limit": None,
        "weekly_limit": None,
    }
    m = re.search(r"Account:\s+\[?([^\]╮\n]+?)\]?\s*\(([^)╮\n]+)\)", raw)
    if m:
        result["account"] = m.group(1).strip()
        result["plan"] = m.group(2).strip()
    m = re.search(r"Model:\s+(.+)", raw)
    if m:
        result["model"] = m.group(1).strip()
    m = re.search(r"5h limit:\s+\[([^\]]+)\]\s+(\d+)%\s+left\s+\(resets\s+([^)]+)\)", raw)
    if m:
        result["hourly_limit"] = {
            "bar": m.group(1).strip(),
            "pct": int(m.group(2)),
            "resets": m.group(3).strip(),
        }
    m = re.search(r"Weekly limit:\s+\[([^\]]+)\]\s+(\d+)%\s+left\s+\(resets\s+([^)]+)\)", raw)
    if m:
        result["weekly_limit"] = {
            "bar": m.group(1).strip(),
            "pct": int(m.group(2)),
            "resets": m.group(3).strip(),
        }
    return result


def read_status_file() -> dict:
    if not os.path.exists(STATUS_FILE):
        return {"error": "status file not found", "file": STATUS_FILE}
    try:
        with open(STATUS_FILE, "r") as f:
            # 非阻塞读
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return data
    except Exception as e:
        return {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            exists = os.path.exists(STATUS_FILE)
            self.send_json({"status": "ok", "status_file_exists": exists})

        elif path == "/quota":
            data = read_status_file()
            if "error" in data:
                self.send_json(data, status=503)
                return
            raw = data.get("raw", "")
            parsed = parse_status(raw)
            parsed["timestamp"] = data.get("timestamp")
            self.send_json(parsed)

        elif path == "/raw":
            data = read_status_file()
            self.send_json(data)

        else:
            self.send_error(404)


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    log.info("Codex Quota Server | PORT=%s | STATUS_FILE=%s", PORT, STATUS_FILE)
    log.info("  GET /health  - 服务状态")
    log.info("  GET /quota   - 解析后的额度 JSON")
    log.info("  GET /raw     - 原始输出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
