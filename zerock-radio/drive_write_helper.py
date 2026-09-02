#!/usr/bin/env python3
"""Standalone helper (run inside drive_sync_venv, has google-api deps the main
app's system python lacks) — writes/updates one candidate's social-links .txt
file, or copies a binary file (e.g. a Palash communique PDF/DOC), into a Palash
Drive folder.

Auth: OAuth user credentials (not a service account — service accounts have no
storage quota and cannot create files in a normal personal Drive folder). The
credentials file holds a long-lived refresh_token; google-auth transparently
mints a fresh access token per run using it, so no re-login is ever needed
unless the user revokes access.

Input: JSON on stdin: {
  "folder_id": str, "credentials_path": str, "filename": str,
  "content": str,        # text content — mutually exclusive with file_path
  "file_path": str,      # local file to upload as-is (mimetype guessed from filename)
  "drive_file_id": str|null,  # if known, update in place; else create + search-by-name first
  "action": "delete"|None     # "delete" removes drive_file_id instead of writing (folder_id/filename unused)
}
Output: JSON on stdout: {"ok": true, "file_id": "..."} or {"ok": false, "error": "..."}
"""
import sys, json, mimetypes
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaFileUpload
import io

def main():
    req = json.load(sys.stdin)
    creds_path = req['credentials_path']
    file_id = req.get('drive_file_id')

    with open(creds_path) as f:
        tok = json.load(f)
    creds = Credentials(
        token=tok.get('token'),
        refresh_token=tok['refresh_token'],
        token_uri=tok.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=tok['client_id'],
        client_secret=tok['client_secret'],
        scopes=tok.get('scopes', ['https://www.googleapis.com/auth/drive']),
    )
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)

    if req.get('action') == 'delete':
        try:
            service.files().delete(fileId=file_id).execute()
            print(json.dumps({'ok': True, 'file_id': file_id}))
        except Exception as e:
            print(json.dumps({'ok': False, 'error': str(e)}))
            sys.exit(1)
        return

    folder_id = req['folder_id']
    filename = req['filename']
    content = req.get('content')
    file_path = req.get('file_path')

    if file_path:
        mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        media = MediaFileUpload(file_path, mimetype=mimetype, resumable=False)
    else:
        media = MediaIoBaseUpload(io.BytesIO((content or '').encode('utf-8')), mimetype='text/plain', resumable=False)

    try:
        if not file_id:
            # No known id yet — search by exact name in the folder first, to avoid
            # creating a duplicate if this is actually the first write after a restart
            # (candidate record's drive_file_id could be unset even if a file exists).
            q = (f"'{folder_id}' in parents and name = '{filename}' "
                 f"and trashed = false")
            resp = service.files().list(q=q, fields='files(id,name)', pageSize=1).execute()
            found = resp.get('files', [])
            if found:
                file_id = found[0]['id']

        if file_id:
            service.files().update(
                fileId=file_id, media_body=media,
                body={'name': filename}, fields='id').execute()
        else:
            created = service.files().create(
                body={'name': filename, 'parents': [folder_id]},
                media_body=media, fields='id').execute()
            file_id = created['id']

        print(json.dumps({'ok': True, 'file_id': file_id}))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}))
        sys.exit(1)

if __name__ == '__main__':
    main()
