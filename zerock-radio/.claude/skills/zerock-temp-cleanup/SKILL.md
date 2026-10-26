---
name: zerock-temp-cleanup
description: Clean stale temp files on the ZeRock servers when the disk/temp watchdog reports stuck files or low disk. Deletes only files that no queue entry references.
---

# Stale temp file cleanup

The watchdog (`_local_temp_watchdog`) WhatsApps Roy when free disk is under 3 GB local / 50 GB NAS, or when files in `LOCAL_TEMP` (`/home/roy/zerock-radio/shows`) or `NAS_TEMP` (`/mnt/nas/Music/ZeRock_Temp`) look stuck.

## Procedure (on rocky)
1. List candidates older than 14 days: `find <dir> -type f -mtime +14 -printf '%s %TY-%Tm-%Td %p\n'`.
2. For each file, check whether any entry in `schedule.json` references it (`file_path` or `nas_path`). Keep every referenced file, e.g. `autorerun_*` files used by upcoming reruns.
3. Safe to delete: unreferenced files, `_seek_*` / `_resume_*` temp files, chart leftovers (`<id>_pl##_*`, `<id>_pa##_*`).
4. Show the user the list with sizes before deleting unless they asked to clean up. Report count and size freed, and which files were kept and why.
5. Check `df -h / /mnt/nas` afterwards.

## Related
- Logs `logs/web.log` and `logs/liquidsoap.log` grow without rotation: truncate in place (`truncate -s 0`), no restart needed.
- Never run unbounded audio renders on rocky (one filled the disk).
