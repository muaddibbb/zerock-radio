#!/usr/bin/env python3
"""Re-run the OAuth consent flow for the ZeRock Palash Drive integration.

The OAuth app is still in Google's 'Testing' publishing status, which caps
refresh tokens at 7 days regardless of use (confirmed 2026-09-08 — every
Drive sync silently failed with invalid_grant once the token crossed that
line). Full verification to move to Production requires a privacy policy /
ToS / app review disproportionate to a personal single-user tool, so the
practical fix is just re-running this every ~week (radio_app.py's
_palash_oauth_token_watchdog sends a WhatsApp reminder before it lapses).

Pulls the current (expiring/expired) token from the server to reuse its
client_id/secret, opens a browser for fresh Google consent, then pushes the
new token back to the server. Needs a browser — run this locally, not on
the headless server.

Requires: pip install google-auth-oauthlib google-auth google-api-python-client
"""
import json
import subprocess
import tempfile
import os

SSH_KEY  = os.path.expanduser('~/.ssh/zerock_deploy')
SSH_HOST = 'roy@rocky.kupernet.com'
REMOTE_TOKEN_PATH = '/home/roy/palash_oauth_token.json'

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def main():
    with tempfile.TemporaryDirectory() as tmp:
        local_old = os.path.join(tmp, 'old_token.json')
        subprocess.run(
            ['scp', '-i', SSH_KEY, f'{SSH_HOST}:{REMOTE_TOKEN_PATH}', local_old],
            check=True,
        )
        with open(local_old) as f:
            old = json.load(f)

        client_config = {
            "installed": {
                "client_id": old['client_id'],
                "client_secret": old['client_secret'],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": old.get('token_uri', 'https://oauth2.googleapis.com/token'),
                "redirect_uris": ["http://localhost"],
            }
        }

        flow = InstalledAppFlow.from_client_config(
            client_config, scopes=['https://www.googleapis.com/auth/drive'])
        creds = flow.run_local_server(port=0)

        new_token = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }

        # Verify before touching the server
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        about = service.about().get(fields='user').execute()
        print(f"Authenticated as: {about['user']['emailAddress']}")

        local_new = os.path.join(tmp, 'new_token.json')
        with open(local_new, 'w') as f:
            json.dump(new_token, f, indent=2)

        subprocess.run(
            ['ssh', '-i', SSH_KEY, SSH_HOST,
             f'mv {REMOTE_TOKEN_PATH} {REMOTE_TOKEN_PATH}.bak.$(date +%s) 2>/dev/null; true'],
            check=True,
        )
        subprocess.run(
            ['scp', '-i', SSH_KEY, local_new, f'{SSH_HOST}:{REMOTE_TOKEN_PATH}'],
            check=True,
        )
        subprocess.run(
            ['ssh', '-i', SSH_KEY, SSH_HOST, f'chmod 600 {REMOTE_TOKEN_PATH}'],
            check=True,
        )
        print("New token deployed to server.")


if __name__ == '__main__':
    main()
