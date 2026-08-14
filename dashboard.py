"""
Dashboard - Local web UI for the Job Scraper.
Reads jobs and site statuses from Supabase (shared by both the local
scheduled run and the GitHub Actions run) instead of relying on email.

Usage:
    python dashboard.py
"""

import webbrowser
from threading import Timer

from flask import Flask, jsonify, render_template

import db

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 5057


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/jobs")
def api_jobs():
    return jsonify({
        "jobs": db.get_jobs(),
        "last_viewed_at": db.get_last_viewed(),
    })


@app.route("/api/sites")
def api_sites():
    return jsonify({"sites": db.get_sites_status()})


@app.route("/api/mark-viewed", methods=["POST"])
def api_mark_viewed():
    db.set_last_viewed()
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not db.is_configured():
        print("WARNING: SUPABASE_URL / SUPABASE_KEY not set - the dashboard needs Supabase to show data.")

    url = f"http://{HOST}:{PORT}"
    Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Dashboard running at {url}")
    app.run(host=HOST, port=PORT, debug=False)
