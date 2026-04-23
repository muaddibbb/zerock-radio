# ZeRock SAM Poller - zerock-poller.ps1  v4.3
# Runs as a Windows Scheduled Task every 1 minute.
#
# Two-queue system:
#   zerock-delay-queue.txt  -> PS1 writes here after download. PAL never reads this.
#   zerock-queue.txt        -> PS1 moves entries here exactly at air time. PAL reads this.
#
# Task Scheduler setup (run once in Admin PowerShell):
#   schtasks /create /tn "ZeRockPoller" /tr "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File \"C:\Users\Public\ZeRock\zerock-poller.ps1\"" /sc MINUTE /mo 1 /f

$ServerUrl    = "http://zerock.kupernet.com:3001"
$DelayQueue   = "C:\Users\Public\ZeRock\zerock-delay-queue.txt"
$ActiveQueue  = "C:\Users\Public\ZeRock\zerock-queue.txt"
$MovedAtFile  = "C:\Users\Public\ZeRock\zerock-queue-moved.txt"
$DownloadDir  = "C:\Users\Public\ZeRock\Episodes"
$LogFile      = "C:\Users\Public\ZeRock\zerock-poller.log"

function Write-Log($msg) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$ts  $msg" | Out-File -FilePath $LogFile -Append -Encoding UTF8
    Write-Host "$ts  $msg"
}

# ── STEP 1: Poll server, download new episodes, write to delay queue ───────────
try {
    $response = Invoke-WebRequest -Uri "$ServerUrl/api/sam-poll" `
                                  -UseBasicParsing -TimeoutSec 15
    $entries = $response.Content | ConvertFrom-Json

    if ($entries -and $entries.Count -gt 0) {
        Write-Log "Found $($entries.Count) pending episode(s) on server"

        foreach ($entry in $entries) {
            $id        = $entry.id
            $mediaUrl  = $entry.mediaUrl
            $title     = $entry.title
            $publishTs = [long]$entry.publishTimestamp

            # Build safe local filename
            $safeName  = ($title -replace '[\\/:*?"<>|]', '') -replace '\s+', '_'
            $localPath = Join-Path $DownloadDir "$safeName.mp3"

            # Download if not already present
            if (-not (Test-Path $localPath)) {
                Write-Log "DOWNLOADING: $title"
                Write-Log "  From: $mediaUrl"
                Write-Log "  To:   $localPath"
                try {
                    Invoke-WebRequest -Uri $mediaUrl -OutFile $localPath `
                                      -UseBasicParsing -TimeoutSec 300
                    Write-Log "DOWNLOADED OK: $localPath"
                } catch {
                    Write-Log "DOWNLOAD FAILED: $title -- $_"
                    continue
                }
            } else {
                Write-Log "ALREADY EXISTS: $localPath"
            }

            # Ack server - server cleans up its copy
            try {
                Invoke-WebRequest -Uri "$ServerUrl/api/sam-ack-get/$id" `
                                  -UseBasicParsing -TimeoutSec 10 | Out-Null
                Write-Log "ACKED: $id"
            } catch {
                Write-Log "ACK FAILED for $id : $_"
            }

            # Write to delay queue
            $airDt  = [System.DateTimeOffset]::FromUnixTimeSeconds($publishTs).LocalDateTime
            $airStr = $airDt.ToString("yyyy-MM-dd HH:mm:ss")
            "$localPath|$title|$airStr" | Out-File -FilePath $DelayQueue -Append -Encoding UTF8
            Write-Log "DELAY QUEUE: $title (air: $airStr)"
        }
    } else {
        Write-Log "No new episodes on server"
    }
} catch {
    Write-Log "POLL ERROR: $_"
}

# ── STEP 2: Check delay queue - move due entries to active queue ───────────────
if (Test-Path $DelayQueue) {
    $lines     = Get-Content $DelayQueue -Encoding UTF8
    $stillWaiting = @()
    $now       = Get-Date

    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -eq '') { continue }

        $parts = $line -split '\|'
        if ($parts.Count -ge 3) {
            $localPath = $parts[0]
            $title     = $parts[1]
            $airStr    = $parts[2]

            try {
                $airDt = [datetime]::ParseExact($airStr, "yyyy-MM-dd HH:mm:ss", $null)

                if ($now -ge $airDt) {
                    # Air time reached - write only the file path for PAL
                    $localPath | Out-File -FilePath $ActiveQueue -Append -Encoding UTF8
                    # Record move time so PS1 can clear the file 2 minutes later
                    $now.ToString("yyyy-MM-dd HH:mm:ss") | Out-File -FilePath $MovedAtFile -Encoding UTF8 -Force
                    Write-Log "MOVED TO ACTIVE QUEUE: $title"
                } else {
                    $secsLeft = [int]($airDt - $now).TotalSeconds
                    Write-Log "WAITING: $title -> air in ${secsLeft}s (at $($airDt.ToString('HH:mm:ss')))"
                    $stillWaiting += $line
                }
            } catch {
                Write-Log "DATE PARSE ERROR for line: $line"
                $stillWaiting += $line
            }
        } else {
            $stillWaiting += $line
        }
    }

    # Write back only entries still waiting
    if ($stillWaiting.Count -gt 0) {
        $stillWaiting | Out-File -FilePath $DelayQueue -Encoding UTF8 -Force
    } else {
        Clear-Content -Path $DelayQueue
    }
}

# ── STEP 3: Clear active queue 2 minutes after last move ──────────────────────
if (Test-Path $MovedAtFile) {
    $movedAt = [datetime]::ParseExact(
        (Get-Content $MovedAtFile -Encoding UTF8 | Select-Object -First 1).Trim(),
        "yyyy-MM-dd HH:mm:ss", $null)
    if ((Get-Date) -ge $movedAt.AddMinutes(2)) {
        Clear-Content -Path $ActiveQueue
        Remove-Item -Path $MovedAtFile -Force
        Write-Log "CLEARED active queue (2min after move)"
    }
}

Write-Log "=== Done ==="
# Read-Host "Press Enter to exit"
