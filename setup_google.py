#!/usr/bin/env python3
"""
Run this on your laptop/desktop (it opens a browser), NOT on the Pi.

  pip install google-auth-oauthlib
  python setup_google.py

Needs credentials.json (OAuth "Desktop app" client) in this folder.
Produces token.json -> copy it into the pi_household_dashboard-main folder for the Pi.
"""
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]
here = Path(__file__).resolve().parent
flow = InstalledAppFlow.from_client_secrets_file(str(here / "credentials.json"), SCOPES)
creds = flow.run_local_server(port=0, prompt="consent")
(here / "token.json").write_text(creds.to_json())
print("Saved token.json")
