"""Khinkali API Server — simple HTTP API for stats and photo upload."""

import os
import json
import base64
import re
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx

API_PORT = int(os.environ.get("KHINKALI_API_PORT", "5199"))
DATA_DIR = Path(os.environ.get("KHINKALI_DATA_DIR", "/Users/egoregerev/mcp-servers/khinkali-bot/data"))
PHOTOS_DIR = DATA_DIR / "photos"
STATS_FILE = DATA_DIR / "stats.json"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
TBILISI_TZ = timezone(timedelta(hours=4))

ANT_KEY = ""
try:
    auth = json.loads(Path("/opt/hermes-shared/auth.json").read_text())
    for cred in auth.get("credential_pool", {}).get("anthropic", []):
        ANT_KEY = cred.get("access_token", "")
        break
except Exception:
    pass


def load_stats():
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {}


def save_stats(stats):
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))


def get_user_stats(stats, user_id):
    if user_id not in stats:
        stats[user_id] = {"name": "", "total": 0, "today": 0, "record": 0, "session": 0, "today_date": "", "photos": []}
    s = stats[user_id]
    today = datetime.now(TBILISI_TZ).strftime("%Y-%m-%d")
    if s.get("today_date") != today:
        s["today"] = 0
        s["today_date"] = today
    return s


def count_khinkali_sync(img_b64):
    if not ANT_KEY:
        return 0
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANT_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 20,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                        {"type": "text", "text": "Look at this plate carefully. Count ALL individual khinkali (Georgian dumplings). Reply with ONLY the number."}
                    ]}],
                },
            )
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"].strip()
                nums = re.findall(r'\d+', text)
                if nums:
                    return int(nums[0])
    except Exception:
        pass
    return 0


def get_leaderboard():
    stats = load_stats()
    entries = sorted(
        [(uid, s) for uid, s in stats.items() if s.get("total", 0) > 0],
        key=lambda x: x[1]["total"], reverse=True,
    )[:10]
    return [{"rank": i+1, "name": s["name"], "total": s["total"], "today": s["today"], "record": s["record"]} for i, (uid, s) in enumerate(entries)]


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.startswith('/api/stats/'):
            uid = path.split('/')[-1]
            stats = load_stats()
            s = get_user_stats(stats, uid)
            self._json(s)
        elif path == '/api/leaderboard':
            self._json(get_leaderboard())
        elif path == '/api/health':
            self._json({"ok": True})
        else:
            self.send_error(404)

    def do_POST(self):
        if '/api/upload' not in self.path:
            self.send_error(404)
            return
        ctype = self.headers.get('Content-Type', '')
        clen = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(clen)

        uid = 'anon'
        uname = 'Guest'
        img_b64 = None
        photo_path = ''

        if 'multipart' in ctype:
            boundary = ctype.split('boundary=')[1].encode() if 'boundary=' in ctype else b''
            if boundary:
                parts = body.split(b'--' + boundary)
                for part in parts:
                    if b'name="user_id"' in part:
                        chunks = part.split(b'\r\n\r\n', 1)
                        if len(chunks) > 1:
                            uid = chunks[1].strip().decode()
                    elif b'name="user_name"' in part:
                        chunks = part.split(b'\r\n\r\n', 1)
                        if len(chunks) > 1:
                            uname = chunks[1].strip().decode()
                    elif b'name="photo"' in part:
                        chunks = part.split(b'\r\n\r\n', 1)
                        if len(chunks) > 1:
                            raw = chunks[1].rstrip(b'\r\n--')
                            if raw:
                                img_b64 = base64.b64encode(raw).decode()
                                ts = datetime.now(TBILISI_TZ).strftime('%Y%m%d_%H%M%S')
                                photo_path = str(PHOTOS_DIR / f'{uid}_{ts}.jpg')
                                with open(photo_path, 'wb') as fout:
                                    fout.write(raw)

        if not img_b64:
            self._json({"error": "no photo", "count": 0}, 400)
            return

        count = count_khinkali_sync(img_b64)
        if count == 0:
            self._json({"error": "no khinkali", "count": 0})
            return

        stats = load_stats()
        s = get_user_stats(stats, uid)
        s["name"] = uname
        s["total"] += count
        s["today"] += count
        s["session"] += count
        if s["session"] > s["record"]:
            s["record"] = s["session"]
        if photo_path:
            s["photos"].append({"path": photo_path, "count": count, "ts": datetime.now(TBILISI_TZ).strftime('%Y%m%d_%H%M%S')})
        save_stats(stats)
        self._json({"count": count, "stats": s})

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Khinkali API on port {API_PORT}")
    HTTPServer(('0.0.0.0', API_PORT), Handler).serve_forever()
