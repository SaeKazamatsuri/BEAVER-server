import os
import re
import sqlite3
import logging
from uuid import uuid4
from datetime import datetime
import threading
import tkinter as tk

from flask import Flask, render_template, request
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
               time TEXT
           )"""
    )
    conn.commit()
    conn.close()


def fetch_all_for(db_path: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, real_name, text, time FROM comments ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [{"name": r[0], "real_name": r[1], "text": r[2], "time": r[3]} for r in rows]


def insert_comment_for(db_path: str, entry: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO comments (name, real_name, text, time) VALUES (?, ?, ?, ?)",
        (entry["name"], entry["real_name"], entry["text"], entry["time"]),
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


def _launch_indicator():
    root = tk.Tk()
    root.title("Server Relay Indicator")
    status_var, count_var = tk.StringVar(), tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Segoe UI", 10)).pack(
        padx=12, pady=(12, 6)
    )
    tk.Label(root, textvariable=count_var, font=("Segoe UI", 10)).pack(
        padx=12, pady=(0, 12)
    )

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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    raw_session = None
    if isinstance(data, dict):
        raw_session = data.get("session")
    if not raw_session:
        raw_session = request.args.get("session", "default")
    session_key, path = ensure_session(raw_session)
    entry = {
        "name": data.get("name", "名無し"),
        "real_name": data.get("real_name", ""),
        "text": data.get("text", ""),
        "time": data.get("time", now),
    }
    with storage_lock:
        message_logs[session_key].append(entry)
    insert_comment_for(path, entry)
    emit("new_comment", entry, broadcast=True, to=session_key)
    logging.info(f"[{session_key}] {entry['name']}: {entry['text']}")


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000)
