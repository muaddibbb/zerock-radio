// ─── Status Check ─────────────────────────────────────────────────────────────
async function checkStatus() {
  const badge = document.getElementById('statusBadge');
  const warning = document.getElementById('credentialsWarning');
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (data.configured) {
      badge.textContent = 'Ready';
      badge.className = 'status-badge ready';
    } else {
      badge.textContent = 'Not Configured';
      badge.className = 'status-badge not-ready';
      warning.style.display = 'block';
    }
  } catch {
    badge.textContent = 'Offline';
    badge.className = 'status-badge not-ready';
  }
}

checkStatus();

// ─── Set today's date and current time as default ─────────────────────────────
const dateInput = document.getElementById('date');
const timeInput = document.getElementById('scheduleTime');
const today = new Date().toISOString().split('T')[0];
dateInput.value = today;

// Default time to current local time (HH:MM)
const nowLocal = new Date();
timeInput.value = `${String(nowLocal.getHours()).padStart(2, '0')}:${String(nowLocal.getMinutes()).padStart(2, '0')}`;

// Update button label when date/time changes
function updateSubmitLabel() {
  const selectedDate = dateInput.value;
  const selectedTime = timeInput.value;
  if (!selectedDate || !selectedTime) return;
  const [yr, mo, dy] = selectedDate.split('-').map(Number);
  const [hr, mn] = selectedTime.split(':').map(Number);
  const scheduled = new Date(yr, mo - 1, dy, hr, mn, 0);
  const isInFuture = scheduled.getTime() > Date.now() + 60000;
  const label = document.getElementById('submitLabel');
  if (label) label.textContent = isInFuture ? 'Schedule Episode' : 'Publish Episode';
}

dateInput.addEventListener('change', updateSubmitLabel);
timeInput.addEventListener('change', updateSubmitLabel);
updateSubmitLabel();

// ─── Show Schedule ────────────────────────────────────────────────────────────
// day: 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri 6=Sat
// dir: 'upcoming' = next occurrence incl. today | 'last' = most recent incl. today
// rerun_offset: days after broadcast for rerun | rerun_time: HH:MM of rerun
const SHOW_SCHEDULE = {
  'Beat-oN מקומי':     { day: 4, time: '15:00', dir: 'upcoming', rerun_offset: 4,  rerun_time: '10:00' },
  'Black Parade':       { day: 0, time: '13:00', dir: 'upcoming', rerun_offset: 2,  rerun_time: '09:00' },
  'ON AIR':             { day: 1, time: '09:00', dir: 'upcoming', rerun_offset: 2,  rerun_time: '18:00' },
  'On the Mend':        { day: 3, time: '17:00', dir: 'upcoming', rerun_offset: 1,  rerun_time: '10:00' },
  'Oy Vavoy':           { day: 1, time: '11:00', dir: 'last',     rerun_offset: 2,  rerun_time: '12:00' },
  'Rocktrip':           { day: 4, time: '09:00', dir: 'upcoming', rerun_offset: 3,  rerun_time: '08:00' },
  'Shabi On The Rocks': { day: 3, time: '19:00', dir: 'upcoming', rerun_offset: 5,  rerun_time: '18:00' },
  'Stage Dive':         { day: 4, time: '18:00', dir: 'upcoming', rerun_offset: 3,  rerun_time: '12:00' },
  'The Breakdown':      { day: 2, time: '10:00', dir: 'upcoming', rerun_offset: 2,  rerun_time: '18:00' },
  'Time Warp':          { day: 2, time: '08:00', dir: 'upcoming', rerun_offset: 0,  rerun_time: '18:00' },
  'אני לא בפסקול':     { day: 0, time: '17:00', dir: 'last',     rerun_offset: 2,  rerun_time: '16:00' },
  'האחות':              { day: 3, time: '08:00', dir: 'upcoming', rerun_offset: 6,  rerun_time: '15:00' },
  'השאלטר':             { day: 1, time: '08:00', dir: 'upcoming', rerun_offset: 3,  rerun_time: '12:00' },
  'זה פרוג':            { day: 3, time: '11:00', dir: 'upcoming', rerun_offset: 4,  rerun_time: '11:00' },
  'זה רוק פורטה':       { broadcasterMap: {
    'אחיעד לוק': { day: 4, time: '08:00', dir: 'upcoming', rerun_offset: 0,  rerun_time: '16:00' },
    'ערן הר-פז':  { day: 4, time: '08:00', dir: 'upcoming', rerun_offset: 0,  rerun_time: '16:00' },
  }},
  'נגד כיוון הזיפים':  { day: 0, time: '09:00', dir: 'upcoming', rerun_offset: 2,  rerun_time: '13:00' },
  'סינגלס':             { day: 2, time: '12:00', dir: 'upcoming', rerun_offset: 2,  rerun_time: '11:00' },
  'סן פטרוק':           { broadcasterMap: {
    'אסף פלג':   { day: 1, time: '19:00', dir: 'upcoming', rerun_offset: 5, rerun_time: '10:00' },
    'איתמר עדן': { day: 1, time: '20:00', dir: 'upcoming', rerun_offset: 5, rerun_time: '11:00' },
    'רועי כנפו': { day: 4, time: '19:00', dir: 'upcoming', rerun_offset: 2, rerun_time: '14:00' },
    'רוני אורן': { day: 4, time: '20:00', dir: 'upcoming', rerun_offset: 2, rerun_time: '15:00' },
  }},
  'על הרוקר':           { time: '07:00', upload_time: '08:00' },  // manual date — no auto-fill; publishes 1h after broadcast
  'פטרוק לילה':         { broadcasterMap: {
    'איל אורטל':   { day: 0, time: '19:00', dir: 'upcoming', rerun_offset: 6, rerun_time: '09:00' },
    'מאיר הוברמן': { day: 4, time: '20:00', dir: 'upcoming', rerun_offset: 2, rerun_time: '13:00' },
    'אלירן קטנוב': { day: 2, time: '19:00', dir: 'upcoming', rerun_offset: 4, rerun_time: '12:00' },
  }},
  'שמונים ארומטיים':   { day: 3, time: '12:00', dir: 'upcoming' },
};

function getUpcomingDate(targetDay, time) {
  const d = new Date();
  const diff = (targetDay - d.getDay() + 7) % 7;
  d.setDate(d.getDate() + diff);
  // If the result is today and the show time has already passed, jump to next week
  if (diff === 0 && time) {
    const [h, m] = time.split(':').map(Number);
    const showTime = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m);
    if (showTime <= new Date()) d.setDate(d.getDate() + 7);
  }
  return d.toISOString().split('T')[0];
}

function getLastDate(targetDay, time) {
  const d = new Date();
  const diff = (d.getDay() - targetDay + 7) % 7;
  d.setDate(d.getDate() - diff);
  // If the result is today and the show hasn't aired yet, go back a week
  if (diff === 0 && time) {
    const [h, m] = time.split(':').map(Number);
    const showTime = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m);
    if (showTime > new Date()) d.setDate(d.getDate() - 7);
  }
  return d.toISOString().split('T')[0];
}

function applyShowSchedule(showName, broadcaster) {
  const sched = SHOW_SCHEDULE[showName];
  const isManualOverride = document.getElementById('useManualSchedule').checked;
  if (!sched) return;
  const entry = sched.broadcasterMap ? sched.broadcasterMap[broadcaster] : sched;
  if (!entry) return;

  if (typeof entry.day !== 'undefined') {
    // Fixed weekly show — auto-fill date, hide the manual date input (unless override active)
    dateInput.value = entry.dir === 'last' ? getLastDate(entry.day, entry.time) : getUpcomingDate(entry.day, entry.time);
    if (!isManualOverride) {
      document.getElementById('dateGroup').style.display = 'none';
      document.getElementById('timeGroup').style.display = 'none';
    }
    if (entry.time) timeInput.value = entry.time;
    updateSubmitLabel();
    updateScheduleSummary(entry);
  } else {
    // Manual-date show (e.g. על הרוקר) — show date/time fields for user to pick
    document.getElementById('dateGroup').style.display = '';
    document.getElementById('timeGroup').style.display = '';
    document.getElementById('scheduleSummary').style.display = 'none';
    if (entry.time) timeInput.value = entry.time;
    updateSubmitLabel();
  }
}

function updateScheduleSummary(entry) {
  const summary = document.getElementById('scheduleSummary');
  if (!entry || typeof entry.day === 'undefined') { summary.style.display = 'none'; return; }
  const dateStr = dateInput.value;
  if (!dateStr) { summary.style.display = 'none'; return; }
  const [yr, mo, dy] = dateStr.split('-').map(Number);
  const [bh, bm] = (entry.time || '00:00').split(':').map(Number);
  const broadcastDt = new Date(yr, mo - 1, dy, bh, bm, 0);
  const fmtOpts = { weekday: 'long', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
  let html = `<div>📡 <strong>Broadcast:</strong> ${broadcastDt.toLocaleString('he-IL', fmtOpts)}</div>`;
  if (entry.rerun_offset !== undefined && entry.rerun_time) {
    const [rh, rm] = entry.rerun_time.split(':').map(Number);
    const rerunDt = new Date(broadcastDt);
    rerunDt.setDate(rerunDt.getDate() + entry.rerun_offset);
    rerunDt.setHours(rh, rm, 0, 0);
    html += `<div>🔄 <strong>Rerun:</strong> ${rerunDt.toLocaleString('he-IL', fmtOpts)}</div>`;
  }
  summary.innerHTML = html;
  summary.style.display = 'block';
}

// ─── Manual schedule toggle ───────────────────────────────────────────────────
function _showNeedsManualDate(showName) {
  if (!showName) return false;
  const sched = SHOW_SCHEDULE[showName];
  if (!sched || sched.broadcasterMap) return false;
  return typeof sched.day === 'undefined';
}

function onManualScheduleChange() {
  const checked = document.getElementById('useManualSchedule').checked;
  const showName = document.getElementById('showName').value;
  const alwaysShowDate = _showNeedsManualDate(showName);
  document.getElementById('dateGroup').style.display = (checked || alwaysShowDate) ? '' : 'none';
  document.getElementById('timeGroup').style.display = (checked || alwaysShowDate) ? '' : 'none';
  if (checked) {
    document.getElementById('scheduleSummary').style.display = 'none';
  } else {
    // Restore auto summary based on current show selection
    const broadcaster = document.getElementById('broadcaster').value.trim();
    if (showName && !alwaysShowDate) {
      const entry = _getSummaryEntry(showName, broadcaster);
      updateScheduleSummary(entry);
    }
  }
}

// ─── Hide summary when no show selected ──────────────────────────────────────
function _getSummaryEntry(showName, broadcaster) {
  const sched = SHOW_SCHEDULE[showName];
  if (!sched) return null;
  return sched.broadcasterMap ? (sched.broadcasterMap[broadcaster] || null) : sched;
}

// ─── Show → auto-fill broadcaster + show/hide Episode Text ───────────────────
const SHOW_BROADCASTER = {
  'Beat-oN מקומי':        'יובל ביטון',
  'סינגלס':               'יובל ביטון',
  'ON AIR':               'רועי קופרמן',
  'Time Warp':            'רועי קופרמן',
  'Oy Vavoy':             'יותם "דפיילר" אבני',
  'Shabi On The Rocks':   'דוד שאבי',
  'השאלטר':               'דוד שאבי',
  'The Breakdown':        'עדן גולן',
  'Stage Dive':           'עדן גולן',
  'אני לא בפסקול':        'שיר אסולין',
  'המטריה':               'דורית אורן',
  'זה פרוג':              'ערן הר-פז',
  'שמונים ארומטיים':      'ערן הר-פז',
  'Black Parade':         'מתן בכור',
  'On the Mend':          'נופר נירן',
  'Rocktrip':             'אלעד אביגן',
  'האחות':                'אפרת קוטגרו',
  'נגד כיוון הזיפים':     'אחיעד לוק',
  'עוד יום':              'יובל יוספסון',
  'רכבת לילה':            'יובל יוספסון',
};

document.getElementById('showName').addEventListener('change', function () {
  const isShabi = this.value === 'Shabi On The Rocks';
  const group = document.getElementById('episodeTextGroup');
  const input = document.getElementById('episodeText');
  group.style.display = isShabi ? '' : 'none';
  input.required = isShabi;

  if (document.getElementById('useManualSchedule').checked) return;

  if (!this.value) {
    document.getElementById('scheduleSummary').style.display = 'none';
    return;
  }

  const broadcaster = SHOW_BROADCASTER[this.value];
  if (broadcaster) {
    document.getElementById('broadcaster').value = broadcaster;
  }
  applyShowSchedule(this.value, broadcaster || document.getElementById('broadcaster').value.trim());
});

document.getElementById('broadcaster').addEventListener('change', function () {
  if (document.getElementById('useManualSchedule').checked) return;
  const showName = document.getElementById('showName').value;
  if (showName) applyShowSchedule(showName, this.value.trim());
});

// ─── File Drop Zone ───────────────────────────────────────────────────────────
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('audioFile');
const dropContent = document.getElementById('dropContent');

function updateFileDisplay(file) {
  if (!file) return;
  const size = (file.size / 1024 / 1024).toFixed(1);
  dropZone.classList.add('file-selected');
  dropContent.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M9 18V5l12-2v13"/>
      <circle cx="6" cy="18" r="3"/>
      <circle cx="18" cy="16" r="3"/>
    </svg>
    <p><strong>${file.name}</strong></p>
    <span>${size} MB selected</span>
  `;
}

fileInput.addEventListener('change', (e) => {
  if (e.target.files[0]) updateFileDisplay(e.target.files[0]);
});

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    // Transfer files to input
    const dt = new DataTransfer();
    dt.items.add(files[0]);
    fileInput.files = dt.files;
    updateFileDisplay(files[0]);
  }
});

// ─── Progress Helpers ─────────────────────────────────────────────────────────
const STEPS = [
  'Authenticating with Podbean...',
  'Fetching podcast ID...',
  'Requesting upload authorization...',
  'Uploading audio file',
  'Publishing episode to Podbean...'
];

const STEP_PROGRESS = [15, 25, 40, 80, 95];

function addLogLine(text, state = 'active') {
  const log = document.getElementById('progressLog');
  const line = document.createElement('div');
  line.className = `log-line ${state}`;
  line.innerHTML = `<div class="log-dot"></div><span>${text}</span>`;
  log.appendChild(line);
  line.scrollIntoView({ behavior: 'smooth' });
  return line;
}

function setProgress(pct) {
  document.getElementById('progressFill').style.width = `${pct}%`;
}

// ─── Form Submission ──────────────────────────────────────────────────────────
const form = document.getElementById('uploadForm');
const submitBtn = document.getElementById('submitBtn');
const progressPanel = document.getElementById('progressPanel');
const successPanel = document.getElementById('successPanel');
const errorPanel = document.getElementById('errorPanel');

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  // Reset panels
  progressPanel.style.display = 'block';
  successPanel.style.display = 'none';
  errorPanel.style.display = 'none';
  document.getElementById('progressLog').innerHTML = '';
  setProgress(5);

  submitBtn.disabled = true;
  submitBtn.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    Uploading...
  `;

  const style = document.createElement('style');
  style.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
  document.head.appendChild(style);

  const formData = new FormData(form);

  // Compute Unix timestamp client-side to preserve the user's local timezone.
  // If the show has a separate upload_time (e.g. על הרוקר: broadcast 07:00, upload 08:00),
  // use that for the Podbean/WP publish time instead of the broadcast time.
  const dateVal = document.getElementById('date').value;   // YYYY-MM-DD
  const timeVal = document.getElementById('scheduleTime').value; // HH:MM
  const [yr, mo, dy] = dateVal.split('-').map(Number);
  const [hr, mn] = timeVal.split(':').map(Number);
  const _showSchedEntry = (() => {
    const _s = SHOW_SCHEDULE[document.getElementById('showName').value];
    return (_s && !_s.broadcasterMap) ? _s : null;
  })();
  const _uploadTimeStr = (_showSchedEntry && _showSchedEntry.upload_time) ? _showSchedEntry.upload_time : timeVal;
  const [uhr, umn] = _uploadTimeStr.split(':').map(Number);
  const publishTimestamp = Math.floor(new Date(yr, mo - 1, dy, uhr, umn, 0).getTime() / 1000);
  formData.set('publishTimestamp', publishTimestamp);

  // Attach WordPress show term ID from selected option
  const showSelect = document.getElementById('showName');
  const wpShowId = showSelect.options[showSelect.selectedIndex]?.dataset?.wpId || '';
  formData.set('wpShowId', wpShowId);

  // Manual schedule: validate date+time are set
  const isManual = document.getElementById('useManualSchedule').checked;
  if (isManual && (!document.getElementById('date').value || !document.getElementById('scheduleTime').value)) {
    progressPanel.style.display = 'none';
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.99 12 19.79 19.79 0 0 1 1.98 3.38 2 2 0 0 1 3.95 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91A16 16 0 0 0 12 14.99l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg> Publish Episode`;
    errorPanel.style.display = 'flex';
    document.getElementById('errorMessage').textContent = 'Please enter a date and time for the manual schedule.';
    return;
  }

  // Attach WordPress broadcaster term ID (if name matches a known broadcaster)
  const BROADCASTER_WP_IDS = {
    'אחיעד לוק': 83, 'אלעד אביגן': 76, 'אפרת קוטגרו': 315,
    'דוד שאבי': 78, 'דורית אורן': 312, 'טל אופיר': 263,
    'יובל ביטון': 85, 'יובל יוספסון': 37, 'יותם "דפיילר" אבני': 75,
    'מתן בכור': 87, 'נופר נירן': 310, 'עדן גולן': 255,
    'ערן הר-פז': 63, 'רועי קופרמן': 304, 'שיר אסולין': 84,
  };
  const broadcasterName = document.getElementById('broadcaster').value.trim();
  const wpBroadcasterId = BROADCASTER_WP_IDS[broadcasterName] || '';
  formData.set('wpBroadcasterId', wpBroadcasterId);

  try {
    const response = await fetch('/api/upload', {
      method: 'POST',
      body: formData
    });

    // Read streaming response line-by-line
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let stepIndex = 0;
    let currentLine = null;
    let episodeUrl = null;
    let hasError = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const { message } = JSON.parse(line);

          if (message.startsWith('SUCCESS:')) {
            if (currentLine) currentLine.className = 'log-line done';
            const url = message.replace('SUCCESS:', '');
            episodeUrl = url.replace('Episode published! URL: ', '').trim();
            addLogLine('Episode published successfully!', 'done');
            setProgress(100);
          } else if (message.startsWith('SCHEDULED:')) {
            if (currentLine) currentLine.className = 'log-line done';
            const parts = message.replace('SCHEDULED:', '').split('|');
            episodeUrl = parts[0].trim();
            const publishTs = parseInt(parts[1], 10);
            const scheduledDate = new Date(publishTs * 1000);
            const formatted = scheduledDate.toLocaleString(undefined, {
              dateStyle: 'medium', timeStyle: 'short'
            });
            addLogLine(`Episode scheduled for ${formatted}`, 'done');
            setProgress(100);
            // Flag as scheduled for success panel
            episodeUrl = `__scheduled__|${episodeUrl}|${formatted}`;
          } else if (message.startsWith('ERROR:')) {
            hasError = true;
            const errMsg = message.replace('ERROR:', '');
            document.getElementById('errorMessage').textContent = errMsg;
          } else {
            if (currentLine) currentLine.className = 'log-line done';
            currentLine = addLogLine(message, 'active');
            if (stepIndex < STEP_PROGRESS.length) {
              setProgress(STEP_PROGRESS[stepIndex++]);
            }
          }
        } catch {
          // ignore parse errors
        }
      }
    }

    if (hasError) {
      progressPanel.style.display = 'none';
      errorPanel.style.display = 'flex';
    } else if (episodeUrl) {
      progressPanel.style.display = 'none';
      successPanel.style.display = 'flex';

      if (episodeUrl.startsWith('__scheduled__|')) {
        const [, url, formatted] = episodeUrl.split('|');
        document.getElementById('successTitle').textContent = 'Episode Live on Podbean!';
        document.getElementById('episodeUrl').textContent = `WordPress post scheduled for ${formatted}`;
        document.getElementById('episodeLink').href = url;
      } else {
        document.getElementById('successTitle').textContent = 'Episode Published!';
        document.getElementById('episodeUrl').textContent = episodeUrl;
        document.getElementById('episodeLink').href = episodeUrl;
      }
    }

  } catch (err) {
    progressPanel.style.display = 'none';
    errorPanel.style.display = 'flex';
    document.getElementById('errorMessage').textContent = err.message || 'Network error. Please try again.';
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.99 12 19.79 19.79 0 0 1 1.98 3.38 2 2 0 0 1 3.95 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91A16 16 0 0 0 12 14.99l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>
      </svg>
      Publish Episode
    `;
  }
});

// ─── Reset / Retry ────────────────────────────────────────────────────────────
document.getElementById('resetBtn').addEventListener('click', () => {
  form.reset();
  dropZone.classList.remove('file-selected');
  dropContent.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="17 8 12 3 7 8"/>
      <line x1="12" y1="3" x2="12" y2="15"/>
    </svg>
    <p><strong>Click to select</strong> or drag and drop your audio file</p>
    <span>MP3, MP4, WAV, OGG, FLAC, M4A — up to 500 MB</span>
  `;
  dateInput.value = today;
  const resetNow = new Date();
  timeInput.value = `${String(resetNow.getHours()).padStart(2, '0')}:${String(resetNow.getMinutes()).padStart(2, '0')}`;
  updateSubmitLabel();
  document.getElementById('successTitle').textContent = 'Episode Published!';
  // Reset manual schedule state
  document.getElementById('useManualSchedule').checked = false;
  document.getElementById('dateGroup').style.display = 'none';
  document.getElementById('timeGroup').style.display = 'none';
  document.getElementById('scheduleSummary').style.display = 'none';
  successPanel.style.display = 'none';
  progressPanel.style.display = 'none';
  form.scrollIntoView({ behavior: 'smooth' });
});

document.getElementById('retryBtn').addEventListener('click', () => {
  errorPanel.style.display = 'none';
  progressPanel.style.display = 'none';
  document.getElementById('successTitle').textContent = 'Episode Published!';
});

// ─── Upload History Panel ─────────────────────────────────────────────────────

async function loadUploadHistory() {
  const panel = document.getElementById('uploadHistoryPanel');
  const list  = document.getElementById('uploadHistoryList');
  if (!panel || !list) return;
  try {
    const entries = await fetch('/api/upload-log').then(r => r.json());
    if (!entries.length) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    list.innerHTML = entries.map(e => {
      const dt = e.publishTimestamp
        ? new Date(e.publishTimestamp * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })
        : (e.uploadedAt ? new Date(e.uploadedAt).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '');
      return `
        <div class="upload-history-item">
          <span class="upload-history-dot done"></span>
          <span class="upload-history-title">${e.title || ''}</span>
          <span class="upload-history-time">${dt}</span>
        </div>`;
    }).join('');
  } catch { if (panel) panel.style.display = 'none'; }
}

loadUploadHistory();
