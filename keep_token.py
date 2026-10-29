#!/usr/bin/env python3
"""
Get a Google Keep "master token" for gkeepapi. Run on your laptop.

  pip install gpsoauth
  python keep_token.py

Steps it asks for:
 1. In a desktop browser, open https://accounts.google.com/EmbeddedSetup and sign in.
    (The page may sit on a spinner after sign-in; that's fine.)
 2. Open DevTools > Application (Storage in Firefox) > Cookies > accounts.google.com
    and copy the value of the cookie named  oauth_token  (starts with "oauth2_4/").
 3. Paste it below within a few minutes; it expires quickly.

The resulting token gives broad access to your Google account. Treat it like a
password: only put it in config.yaml on the Pi, and don't share that file.
"""
import getpass
import gpsoauth

email = input("Google account email: ").strip()
oauth_token = getpass.getpass("oauth_token cookie value (hidden): ").strip()
res = gpsoauth.exchange_token(email, oauth_token, "0123456789abcdef")
token = res.get("Token")
if not token:
    raise SystemExit(f"Failed: {res}")
print("\nPaste this into config.yaml -> todo.keep.master_token:\n")
print(token)
