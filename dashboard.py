"""
Dashboard - Local web UI for the Job Scraper.
Reads jobs and site statuses from Supabase (shared by both the local
scheduled run and the GitHub Actions run) instead of relying on email.

Usage:
    python dashboard.py
"""

import webbrowser
from threading import Timer

from flask import Flask, jsonify, render_template, request

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


@app.route("/api/mark-clicked", methods=["POST"])
def api_mark_clicked():
    job_id = (request.get_json(silent=True) or {}).get("id", "")
    if job_id:
        db.mark_job_clicked(job_id)
    return jsonify({"ok": True})


@app.route("/api/dismiss", methods=["POST"])
def api_dismiss():
    job_id = (request.get_json(silent=True) or {}).get("id", "")
    if not job_id:
        return jsonify({"ok": False}), 400
    ok = db.dismiss_job(job_id)
    return jsonify({"ok": ok}), (200 if ok else 500)


if __name__ == "__main__":
    if not db.is_configured():
        print("WARNING: SUPABASE_URL / SUPABASE_KEY not set - the dashboard needs Supabase to show data.")

    url = f"http://{HOST}:{PORT}"
    Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Dashboard running at {url}")
    app.run(host=HOST, port=PORT, debug=False)
