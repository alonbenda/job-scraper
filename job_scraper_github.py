"""
GitHub Actions wrapper for job_scraper.py
Loads email and Supabase credentials from environment variables (GitHub Secrets)
then runs the scraper normally.
"""

import os
import json

# Load config and override email settings from environment variables
config_path = os.path.join(os.path.dirname(__file__), "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# Override email from GitHub Secrets
sender = os.environ.get("SENDER_EMAIL", "")
password = os.environ.get("SENDER_PASSWORD", "")
recipient = os.environ.get("RECIPIENT_EMAIL", "")

if sender and password and recipient:
    config["email"]["sender_email"] = sender
    config["email"]["sender_password"] = password
    config["email"]["recipient_email"] = recipient
    # Credentials are wired up, but whether email actually sends is governed by
    # config.json's "email.enabled" flag (off by default — the dashboard is the
    # primary way to see results now; flip that flag back to true to re-enable).
    print(f"✅ Email configured from GitHub Secrets → {recipient} (enabled={config['email']['enabled']})")
else:
    print("⚠️ Email secrets not set!")

# Check Supabase
supabase_url = os.environ.get("SUPABASE_URL", "")
supabase_key = os.environ.get("SUPABASE_KEY", "")
if supabase_url and supabase_key:
    print(f"✅ Supabase configured → {supabase_url}")
else:
    print("⚠️ Supabase secrets not set! Using local files.")

# Save updated config temporarily
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

# Now run the actual scraper
from job_scraper import run_scraper
run_scraper()