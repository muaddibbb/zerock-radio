#!/usr/bin/env python3
"""Standalone helper (run inside drive_sync_venv, has google-api deps the main
app's system python lacks) — writes/updates one candidate's social-links .txt
file in the Palash links Drive folder.

Input: JSON on stdin: {
  "folder_id": str, "credentials_path": str,
  "filename": str, "content": str,
  "drive_file_id": str|null   # if known, update in place; else create + search-by-name first
}
Output: JSON on stdout: {"ok": true, "file_id": "..."} or {"ok": false, "error": "..."}
"""
import sys, json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

def main():
    req = json.load(sys.stdin)
    folder_id = req['folder_id']
    creds_path = req['credentials_path']
    filename = req['filename']
    content = req['content']
    file_id = req.get('drive_file_id')

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=['https://www.googleapis.com/auth/drive'])
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)

    media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain', resumable=False)

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
