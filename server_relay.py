import os
import sqlite3
import logging
from uuid import uuid4
from datetime import datetime
import threading
import tkinter as tk

from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit

SERVER_SESSION_ID = str(uuid4())
DB_PATH = "messages.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "boot.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.info("server_relay.py started.")

def init_db():
    conn = sqlite3.connect(DB_PATH)
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

def fetch_all():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, real_name, text, time FROM comments").fetchall()
    conn.close()
    return [{"name": r[0], "real_name": r[1], "text": r[2], "time": r[3]} for r in rows]

def insert_comment(entry):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO comments (name, real_name, text, time) VALUES (?, ?, ?, ?)",
        (entry["name"], entry["real_name"], entry["text"], entry["time"]),
    )
    conn.commit()
    conn.close()

app = Flask(__name__, template_folder="templates", static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

init_db()
message_log = fetch_all()

def _launch_indicator():
    root = tk.Tk()
    root.title("Server Relay Indicator")
    status_var, count_var = tk.StringVar(), tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Segoe UI", 10)).pack(padx=12, pady=(12, 6))
    tk.Label(root, textvariable=count_var, font=("Segoe UI", 10)).pack(padx=12, pady=(0, 12))

    def _tick():
        status_var.set(f"Session: {SERVER_SESSION_ID[:8]}…  Port 5000  (Running)")
        count_var.set(f"Message count: {len(message_log)}")
        root.after(1000, _tick)

    _tick()
    root.mainloop()

threading.Thread(target=_launch_indicator, daemon=True).start()

@app.route("/")
def index():
    return render_template("web_index.html", initial_messages=message_log, server_session_id=SERVER_SESSION_ID)

@socketio.on("connect")
def _on_connect():
    emit("history", message_log)
    logging.info("Client connected")

@socketio.on("history_request")
def _on_history_request():
    emit("history", message_log)

@socketio.on("new_comment")
def _on_new_comment(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "name": data.get("name", "名無し"),
        "real_name": data.get("real_name", ""),
        "text": data.get("text", ""),
        "time": data.get("time", now),
    }
    message_log.append(entry)
    insert_comment(entry)
    emit("new_comment", entry, broadcast=True)
    logging.info(f"{entry['name']}: {entry['text']}")

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5000)
