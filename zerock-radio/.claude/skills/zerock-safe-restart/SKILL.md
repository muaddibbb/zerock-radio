---
name: zerock-safe-restart
description: Restart the ZeRock radio web app (zerock-radio-web) only when nothing is on air, now or at a later time via n8n. Use whenever radio_app.py, a .liq file or board/schedule code changed and must go live, or when asked to schedule a restart.
---

# Safe restart of `zerock-radio-web`

Restarting the web app does not cut the audio. Restarting `zerock-liquidsoap` or `zerock-icecast` does: only do that when the stream is already dead or the user asked.

## Rules
- Never restart while a show is on air, while an upload is running, or in minutes :00-:02 of an hour (shows trigger then, and a restart can leave `upload_in_progress` stuck).
- If the user asked to make the change live, run the helper without asking. Otherwise tell them and ask.
- Do not run long sleeping scripts on rocky. Use the helper once, or schedule it from n8n.

## Now
`ssh -p 22222 -i ~/.ssh/zerock_deploy roy@rocky.kupernet.com '~/restart_if_clear.sh <tag>'`
- `<tag>` is unique per change, e.g. `rakevet-img-20261025`. The helper is idempotent per tag: once it restarted it writes `/tmp/restart_if_clear.<tag>.done` and later calls do nothing.
- It checks `/api/on-air` (`"show": true` = a broadcaster show is playing) and uploads, restarts, runs `POST /api/wp-sync`, and WhatsApps Roy (goes to "Note to Self").
- Output "not now" means a show is on air. The helper exists only on rocky (it contains the sudo password; never copy it into this repo).

## Later (the show ends at a known time)
Create a one-time n8n workflow on 192.168.1.114 (login and REST details in memory `project_n8n_server`):
1. Schedule Trigger with a cron rule list that starts a few minutes after the show ends, e.g. `5,10,20,30,45 23 * * *` and `5,30 0 * * *`, workflow setting `"timezone":"Asia/Jerusalem"`.
2. SSH node, credential id `b9nm5BIcBnZl3kB4` ("Rocky Radio SSH Password"), command `~/restart_if_clear.sh <tag>`.
3. Create with `POST /rest/workflows` (body must include `"active": false`), then `POST /rest/workflows/<id>/activate {"versionId": "<current>"}`. `PATCH active:true` does NOT activate.
4. After the `.done` marker exists: deactivate, archive and `DELETE` the workflow, and remove temp files.
Nested `ssh ... bash -s <<EOF` swallows the rest of the script from stdin: copy a script file and run it with `ssh -n`.

## After
- `systemctl is-active zerock-radio-web` and `GET /api/on-air` both answer.
- For a board change, load https://zerockradio.com/ and confirm.
