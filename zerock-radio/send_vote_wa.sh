#!/bin/bash
# Skip 2026-05-21 — no Mitsad this week, resumes 2026-05-28
if [ "$(date +%Y-%m-%d)" = "2026-05-21" ]; then
  exit 0
fi
# Master Mitsad hold switch (same file radio_app.py checks) — skip while present
if [ -f /home/roy/zerock-radio/.mitsad_on_hold ]; then
  exit 0
fi
curl -s -X POST http://127.0.0.1:7733/send \
  -H 'Content-Type: application/json' \
  -d '{"to":"972547464415-1621406038@g.us","message":"ההצבעה למצעד הרוק של ישראל פתוחה.\nמוזמנות ומוזמנים להצביע ולהשפיע\nhttps://linktr.ee/rockzerock"}'
