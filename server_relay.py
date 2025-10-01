import os
import re
import sqlite3
import logging
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import threading
import tkinter as tk

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

SERVER_SESSION_ID = str(uuid4())
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "boot.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.info("server_relay.py started.")

try:
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9), "JST")

app = Flask(__name__, template_folder="templates", static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

storage_lock = threading.Lock()
message_logs = {}

def sanitize_session(value: str) -> str:
    if not value:
        return "default"
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", value)
    s = s.strip("_")
    if not s:
        return "default"
    return s[:64]

def db_path_for(session_name: str) -> str:
    return os.path.join(BASE_DIR, f"{session_name}.db")

def init_db_for(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS comments (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT,
               real_name TEXT,
               text TEXT,
               time TEXT,
               stamp_filename TEXT
           )"""
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(comments)")]
    if "stamp_filename" not in cols:
        conn.execute("ALTER TABLE comments ADD COLUMN stamp_filename TEXT")
    conn.commit()
    conn.close()

def to_hhmm(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"(\d{1,2}):(\d{2})", value)
    if m:
        h = int(m.group(1))
        mm = m.group(2)
        return f"{h:02d}:{mm}"
    return value

def fetch_all_for(db_path: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name, real_name, text, time, stamp_filename FROM comments ORDER BY id ASC").fetchall()
    conn.close()
    out = []
    for r in rows:
        stamp = r[4]
        out.append({
            "name": r[0],
            "real_name": r[1],
            "text": r[2],
            "time": to_hhmm(r[3]),
            "stamp": stamp,
            "stamp_url": f"/static/stamp/{stamp}" if stamp else None
        })
    return out

def insert_comment_for(db_path: str, entry: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO comments (name, real_name, text, time, stamp_filename) VALUES (?, ?, ?, ?, ?)",
        (entry["name"], entry["real_name"], entry["text"], entry["time"], entry.get("stamp")),
    )
    conn.commit()
    conn.close()

def ensure_session(session_value: str):
    session_key = sanitize_session(session_value)
    path = db_path_for(session_key)
    init_db_for(path)
    with storage_lock:
        if session_key not in message_logs:
            message_logs[session_key] = fetch_all_for(path)
    return session_key, path

def list_stamps():
    folder = os.path.join(app.static_folder, "stamp")
    if not os.path.isdir(folder):
        return []
    allow = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    names = []
    for f in os.listdir(folder):
        p = os.path.join(folder, f)
        if os.path.isfile(p):
            ext = os.path.splitext(f)[1].lower()
            if ext in allow:
                names.append(f)
    names.sort()
    return [{"name": n, "url": f"/static/stamp/{n}"} for n in names]

def _launch_indicator():
    root = tk.Tk()
    root.title("Server Relay Indicator")
    status_var, count_var = tk.StringVar(), tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Segoe UI", 10)).pack(padx=12, pady=(12, 6))
    tk.Label(root, textvariable=count_var, font=("Segoe UI", 10)).pack(padx=12, pady=(0, 12))
    def _tick():
        with storage_lock:
            total_msgs = sum(len(v) for v in message_logs.values())
            session_count = len(message_logs)
        status_var.set(f"Session: {SERVER_SESSION_ID[:8]}…  Port 5000  (Running)")
        count_var.set(f"Sessions: {session_count}  Total messages: {total_msgs}")
        root.after(1000, _tick)
    _tick()
    root.mainloop()

threading.Thread(target=_launch_indicator, daemon=True).start()

@app.route("/api/stamps")
def api_stamps():
    return jsonify(list_stamps())

@app.route("/")
def index():
    raw_session = request.args.get("session", "default")
    session_key, _ = ensure_session(raw_session)
    with storage_lock:
        initial = list(message_logs[session_key])
    return render_template(
        "web_index.html",
        initial_messages=initial,
        server_session_id=SERVER_SESSION_ID,
        session_name=session_key,
    )

@socketio.on("connect")
def _on_connect():
    raw_session = request.args.get("session", "default")
    session_key, _ = ensure_session(raw_session)
    join_room(session_key)
    with storage_lock:
        msgs = list(message_logs[session_key])
    emit("history", msgs)
    logging.info(f"Client connected session={session_key}")

@socketio.on("history_request")
def _on_history_request(data=None):
    raw_session = None
    if isinstance(data, dict):
        raw_session = data.get("session")
    if not raw_session:
        raw_session = request.args.get("session", "default")
    session_key, _ = ensure_session(raw_session)
    with storage_lock:
        msgs = list(message_logs[session_key])
    emit("history", msgs)

@socketio.on("new_comment")
def _on_new_comment(data):
    now_hhmm = datetime.now(JST).strftime("%H:%M")
    raw_session = None
    if isinstance(data, dict):
        raw_session = data.get("session")
    if not raw_session:
        raw_session = request.args.get("session", "default")
    session_key, path = ensure_session(raw_session)
    requested_stamp = None
    if isinstance(data, dict):
        requested_stamp = data.get("stamp") or data.get("stamp_filename")
    valid = {s["name"] for s in list_stamps()}
    if requested_stamp not in valid:
        requested_stamp = None
    entry = {
        "name": data.get("name", "名無し"),
        "real_name": data.get("real_name", ""),
        "text": data.get("text", "") if not requested_stamp else "",
        "time": now_hhmm,
        "stamp": requested_stamp,
        "stamp_url": f"/static/stamp/{requested_stamp}" if requested_stamp else None,
    }
    with storage_lock:
        message_logs[session_key].append(entry)
    insert_comment_for(path, entry)
    emit("new_comment", entry, broadcast=True, to=session_key)
    if requested_stamp:
        logging.info(f"[{session_key}] {entry['name']}: [STAMP] {requested_stamp}")
    else:
        logging.info(f"[{session_key}] {entry['name']}: {entry['text']}")

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000)
