const path = require('path');
const fs = require('fs');
const FormData = require('form-data');

// When running as a pkg EXE, __dirname is inside the snapshot (read-only).
// Use BASE_DIR for anything writable (uploads, queue, .env).
const BASE_DIR = process.pkg ? path.dirname(process.execPath) : __dirname;

require('dotenv').config({ path: path.join(BASE_DIR, '.env') });
const express = require('express');
const multer = require('multer');
const axios = require('axios');
const schedule = require('node-schedule');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.urlencoded({ extended: false }));
app.use(express.json());

// ─── Auth ──────────────────────────────────────────────────────────────────────
const AUTH_TOKEN = require('crypto').createHash('sha256').update('YudaKaka2026!').digest('hex');

function parseCookies(req) {
  const cookies = {};
  (req.headers.cookie || '').split(';').forEach(c => {
    const [k, ...v] = c.trim().split('=');
    if (k) cookies[k.trim()] = v.join('=').trim();
  });
  return cookies;
}

app.get('/login', (req, res) => {
  res.sendFile(path.join(BASE_DIR, 'public', 'login.html'));
});

app.post('/login', (req, res) => {
  if (req.body.password === 'YudaKaka2026!') {
    res.setHeader('Set-Cookie', `auth=${AUTH_TOKEN}; HttpOnly; Path=/; Max-Age=2592000`);
    return res.redirect('/');
  }
  res.redirect('/login?error=1');
});

app.get('/', (req, res) => {
  if (parseCookies(req).auth !== AUTH_TOKEN) return res.redirect('/login');
  res.sendFile(path.join(BASE_DIR, 'public', 'index.html'));
});

// Protect all API routes
app.use('/api', (req, res, next) => {
  if (parseCookies(req).auth !== AUTH_TOKEN) return res.status(401).json({ error: 'Unauthorized' });
  next();
});

app.use(express.static(path.join(BASE_DIR, 'public')));

const UPLOADS_DIR = path.join(BASE_DIR, 'uploads');
const MEDIA_DIR   = path.join(BASE_DIR, 'media');
if (!fs.existsSync(MEDIA_DIR)) fs.mkdirSync(MEDIA_DIR, { recursive: true });
app.use('/media', express.static(MEDIA_DIR));

const upload = multer({
  dest: UPLOADS_DIR,
  limits: { fileSize: 500 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('audio/') || file.originalname.match(/\.(mp3|mp4|wav|ogg|flac|m4a)$/i)) {
      cb(null, true);
    } else {
      cb(new Error('Only audio files are allowed'));
    }
  }
});

if (!fs.existsSync(UPLOADS_DIR)) fs.mkdirSync(UPLOADS_DIR, { recursive: true });

// ─── Podbean API ───────────────────────────────────────────────────────────────

let cachedToken = null;
let tokenExpiresAt = 0;

async function getAccessToken() {
  if (cachedToken && Date.now() < tokenExpiresAt - 60000) return cachedToken;

  const { PODBEAN_CLIENT_ID, PODBEAN_CLIENT_SECRET } = process.env;
  if (!PODBEAN_CLIENT_ID || !PODBEAN_CLIENT_SECRET) {
    throw new Error('Missing PODBEAN_CLIENT_ID or PODBEAN_CLIENT_SECRET in .env');
  }

  const credentials = Buffer.from(`${PODBEAN_CLIENT_ID}:${PODBEAN_CLIENT_SECRET}`).toString('base64');
  const response = await axios.post(
    'https://api.podbean.com/v1/oauth/token',
    'grant_type=client_credentials',
    { headers: { 'Authorization': `Basic ${credentials}`, 'Content-Type': 'application/x-www-form-urlencoded' } }
  );

  cachedToken = response.data.access_token;
  tokenExpiresAt = Date.now() + response.data.expires_in * 1000;
  return cachedToken;
}

async function getPodcastId(accessToken) {
  const response = await axios.get('https://api.podbean.com/v1/podcasts', {
    params: { access_token: accessToken }
  });
  const podcasts = response.data.podcasts;
  if (!podcasts || podcasts.length === 0) throw new Error('No podcasts found on your Podbean account');
  return podcasts[0].id;
}

async function authorizeUpload(accessToken, filename, filesize, contentType) {
  const response = await axios.get('https://api.podbean.com/v1/files/uploadAuthorize', {
    params: { access_token: accessToken, filename, filesize, content_type: contentType }
  });
  return response.data;
}

async function uploadFileToS3(presignedUrl, filePath, contentType) {
  const fileStream = fs.createReadStream(filePath);
  const fileSize = fs.statSync(filePath).size;
  await axios.put(presignedUrl, fileStream, {
    headers: { 'Content-Type': contentType, 'Content-Length': fileSize },
    maxContentLength: Infinity,
    maxBodyLength: Infinity,
    timeout: 300000
  });
}

async function createEpisode(accessToken, podcastId, title, description, mediaKey) {
  const params = new URLSearchParams({
    access_token: accessToken,
    podcast_id: podcastId,
    title,
    content: description,
    media_key: mediaKey,
    status: 'publish',
    type: 'public'
  });

  const response = await axios.post('https://api.podbean.com/v1/episodes', params.toString(), {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
  return response.data.episode;
}

// ─── WordPress API ─────────────────────────────────────────────────────────────

const SHOW_SLUGS = {
  'Beat-oN מקומי': 'beat-on',
  'Black Parade': 'black-parade',
  'ON AIR': 'on-air',
  'On the Mend': 'mend',
  'Oy Vavoy': 'oy-vavoy',
  'Rocktrip': 'rocktrip',
  'Shabi On The Rocks': 'sotr',
  'Stage Dive': 'stage-dive',
  'The Breakdown': 'breakdown',
  'Time Warp': 'time-warp',
  'אני לא בפסקול': 'pascal',
  'האחות': 'nurse',
  'המטריה': 'mitria',
  'השאלטר': 'hash',
  'זה פרוג': 'prog',
  'זה רוק פורטה': 'forte',
  'מורידים את הרף': 'moridim',
  'נגד כיוון הזיפים': 'zifim',
  'סינגלס': 'singles',
  'סן פטרוק': 'patrock',
  'ספיישלים': 'special',
  'עוד יום': 'od-yom',
  'על הרוקר': 'al-harocker',
  'פטרוק לילה': 'patrock',
  'רדיו זה פופ': 'zepop',
  'רכבת לילה': 'night-train',
  'שמונים ארומטיים': 'shmonim',
};

const SHOW_FEATURED_IMAGES = {
  'Beat-oN מקומי': 14326,
  'Black Parade': 375,
  'ON AIR': 10872,
  'On the Mend': 14312,
  'Oy Vavoy': 8064,
  'Rocktrip': 14447,
  'Shabi On The Rocks': 563,
  'Stage Dive': 12842,
  'The Breakdown': 8062,
  'Time Warp': 12266,
  'אני לא בפסקול': 461,
  'האחות': 12987,
  'המטריה': 12840,
  'השאלטר': 10875,
  'זה פרוג': 2085,
  'זה רוק פורטה': 4382,
  'מורידים את הרף': 685,
  'נגד כיוון הזיפים': 450,
  'סינגלס': 374,
  'סן פטרוק': 389,
  'ספיישלים': 1432,
  'עוד יום': 11943,
  'על הרוקר': 769,
  'פטרוק לילה': 388,
  'רדיו זה פופ': 4911,
  'רכבת לילה': 3028,
  'שמונים ארומטיים': 393,
};

async function createWordPressEpisode(title, content, publishTimestamp, wpShowId, wpBroadcasterId, showName, date, podbeanUrl) {
  const { WP_URL, WP_USERNAME, WP_APP_PASSWORD } = process.env;
  if (!WP_URL || !WP_USERNAME || !WP_APP_PASSWORD) {
    console.warn('[WordPress] Credentials not configured — skipping WP post');
    return null;
  }

  const now = Math.floor(Date.now() / 1000);
  const isScheduled = publishTimestamp && publishTimestamp > now + 60;

  const body = {
    title,
    content,
    status: isScheduled ? 'future' : 'publish',
  };

  if (isScheduled) {
    body.status = 'future';
    body.date_gmt = new Date(publishTimestamp * 1000).toISOString().slice(0, 19);
  }

  if (wpShowId) {
    body.shows = [parseInt(wpShowId, 10)];
  }

  if (wpBroadcasterId) {
    body.broadcasters = [parseInt(wpBroadcasterId, 10)];
  }

  const featuredMedia = SHOW_FEATURED_IMAGES[showName];
  if (featuredMedia) {
    body.featured_media = featuredMedia;
  }

  const showSlug = SHOW_SLUGS[showName];
  if (showSlug && date) {
    const [y, m, d] = date.split('-');
    const dateSuffix = `${d}${m}${y.slice(2)}`;
    body.slug = `${showSlug}-${dateSuffix}`;
  }

  // ACF fields — date stored as Ymd (ACF default), podbean_link as URL
  body.acf = {};
  if (date) {
    body.acf.date = date.replace(/-/g, ''); // YYYY-MM-DD → YYYYMMDD
  }
  if (podbeanUrl) {
    body.acf.podbean_link = podbeanUrl;
  }

  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const response = await axios.post(
    `${WP_URL}/wp-json/wp/v2/episodes`,
    body,
    { headers: { 'Authorization': `Basic ${credentials}`, 'Content-Type': 'application/json' } }
  );

  console.log(`[WordPress] Episode created: ${response.data.link} (status: ${response.data.status})`);
  return response.data;
}

async function updateWordPressEpisodeTime(wpPostId, newTimestamp) {
  const { WP_URL, WP_USERNAME, WP_APP_PASSWORD } = process.env;
  if (!WP_URL || !WP_USERNAME || !WP_APP_PASSWORD) return null;
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const now = Math.floor(Date.now() / 1000);
  const isScheduled = newTimestamp > now + 60;
  const body = {
    status:   isScheduled ? 'future' : 'publish',
    date_gmt: new Date(newTimestamp * 1000).toISOString().slice(0, 19),
  };
  const response = await axios.post(
    `${WP_URL}/wp-json/wp/v2/episodes/${wpPostId}`,
    body,
    { headers: { 'Authorization': `Basic ${credentials}`, 'Content-Type': 'application/json' } }
  );
  console.log(`[WordPress] Episode ${wpPostId} rescheduled to ${body.date_gmt}`);
  return response.data;
}

// ─── Rocky Radio Queue ────────────────────────────────────────────────────────
// rocky.kupernet.com (public DNS) = 192.168.1.166 — using internal IP for LAN reliability
const ROCKY_URL = process.env.ROCKY_URL || 'http://192.168.1.166:5000';

function getShowKey(showName, broadcaster) {
  // Shows where key depends on broadcaster
  const broadcasterKeys = {
    'סן פטרוק': {
      'אסף פלג':   'san_patrock_assaf',
      'איתמר עדן': 'san_patrock_itamar',
      'רועי כנפו': 'san_patrock_roi',
      'רוני אורן': 'san_patrock_roni',
    },
    'פטרוק לילה': {
      'איל אורטל':   'patrock_laila_eyal',
      'אלירן קטנוב': 'patrock_laila_eliran',
      'מאיר הוברמן': 'patrock_laila_meir',
    },
  };
  if (broadcasterKeys[showName]) {
    return (broadcasterKeys[showName][broadcaster]) || null;
  }
  const map = {
    'Beat-oN מקומי':     'beat_on',
    'Black Parade':       'black_parade',
    'ON AIR':             'on_air',
    'On the Mend':        'on_the_mend',
    'Oy Vavoy':           'oy_vavoy',
    'Rocktrip':           'rocktrip',
    'Shabi On The Rocks': 'shabi',
    'Stage Dive':         'stage_dive',
    'The Breakdown':      'breakdown',
    'Time Warp':          'time_warp',
    'אני לא בפסקול':     'pascal',
    'האחות':              'haachot',
    'השאלטר':             'hashulter',
    'זה פרוג':            'ze_prog',
    'זה רוק פורטה':       'forte',
    'נגד כיוון הזיפים':  'zifim',
    'סינגלס':             'singles',
    'על הרוקר':           'al_harocker',
  };
  return map[showName] || null;
}

// Queue a file on Rocky by copying it to /media/ (HTTP-accessible) then calling Rocky's download endpoint.
// This avoids multipart-file-forwarding issues — Rocky fetches the file itself over HTTP.
async function queueOnRocky(filePath, originalName, showKey, broadcaster, date, existingMediaUrl, isManual, scheduleTime) {
  if (!showKey) {
    console.log(`[Rocky] No show_key mapping for this show, skipping`);
    return { ok: false, reason: 'no_key' };
  }
  try {
    let mediaUrl = existingMediaUrl || null;

    if (!mediaUrl) {
      // Copy file into /media/ so Rocky can download it via HTTP
      const safeOrig = (originalName || 'show.mp3').replace(/[^a-zA-Z0-9._-]/g, '_');
      const mediaFilename = `${Date.now()}_${safeOrig}`;
      const mediaFilePath = path.join(MEDIA_DIR, mediaFilename);
      fs.copyFileSync(filePath, mediaFilePath);
      const serverUrl = process.env.SERVER_URL || 'http://zerock.kupernet.com:3001';
      mediaUrl = `${serverUrl}/media/${mediaFilename}`;
      console.log(`[Rocky] Serving file at ${mediaUrl}`);
    }

    const manualBroadcastTime = (isManual && date && scheduleTime) ? `${date}T${scheduleTime}` : '';

    const resp = await axios.post(`${ROCKY_URL}/api/schedule-url`, {
      show_key:               showKey,
      broadcaster:            broadcaster || '',
      manual_date:            date || '',
      media_url:              mediaUrl,
      original_name:          originalName || 'show.mp3',
      mode:                   'queue_only',  // ZeRock already handled Podbean/WP; Rocky just queues for radio
      manual_schedule:        isManual || false,
      manual_broadcast_time:  manualBroadcastTime,
    }, { timeout: 30000 });

    console.log('[Rocky] Queued via URL:', resp.data);
    return { ok: true, data: resp.data };
  } catch (err) {
    console.error('[Rocky] Queue failed:', err.response?.data || err.message);
    return { ok: false, error: err.response?.data?.error || err.message };
  }
}

// ─── Upload Log ───────────────────────────────────────────────────────────────

const UPLOAD_LOG_FILE = path.join(BASE_DIR, 'upload-log.json');

function loadUploadLog() {
  if (!fs.existsSync(UPLOAD_LOG_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(UPLOAD_LOG_FILE, 'utf8')); } catch { return []; }
}

function addToUploadLog(entry) {
  const log = loadUploadLog();
  log.push({ ...entry, id: `${Date.now()}`, uploadedAt: Date.now() });
  // Keep last 100 entries
  if (log.length > 100) log.splice(0, log.length - 100);
  fs.writeFileSync(UPLOAD_LOG_FILE, JSON.stringify(log, null, 2));
}


// ─── Routes ───────────────────────────────────────────────────────────────────

app.get('/api/status', (req, res) => {
  const configured = !!(process.env.PODBEAN_CLIENT_ID && process.env.PODBEAN_CLIENT_SECRET);
  res.json({ configured });
});

// UI: returns combined upload history (all uploads, last 30)
app.get('/api/upload-log', (req, res) => {
  res.json(loadUploadLog().slice(-30).reverse()); // most recent 30, newest first
});

app.post('/api/reschedule-wp', async (req, res) => {
  const { wp_post_id, new_timestamp } = req.body || {};
  if (!wp_post_id || !new_timestamp) {
    return res.status(400).json({ error: 'wp_post_id and new_timestamp required' });
  }
  try {
    const result = await updateWordPressEpisodeTime(parseInt(wp_post_id, 10), parseInt(new_timestamp, 10));
    res.json({ ok: true, link: result?.link });
  } catch (err) {
    console.error('[WordPress] Reschedule failed:', err.response?.data || err.message);
    res.status(500).json({ ok: false, error: err.response?.data?.message || err.message });
  }
});

app.get('/api/wp-test', async (req, res) => {
  const { WP_URL, WP_USERNAME, WP_APP_PASSWORD } = process.env;
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  try {
    const typesRes = await axios.get(`${WP_URL}/wp-json/wp/v2/types`, {
      headers: { 'Authorization': `Basic ${credentials}` }
    });
    const types = Object.keys(typesRes.data);

    let episodesEndpoint = null;
    try {
      const epRes = await axios.get(`${WP_URL}/wp-json/wp/v2/episodes`, {
        headers: { 'Authorization': `Basic ${credentials}` }
      });
      episodesEndpoint = { ok: true, count: epRes.data.length };
    } catch (e) {
      episodesEndpoint = { ok: false, error: e.response?.data?.message || e.message };
    }

    res.json({ availableTypes: types, episodesEndpoint });
  } catch (err) {
    res.status(500).json({ error: err.response?.data || err.message });
  }
});

app.post('/api/upload', upload.single('audioFile'), async (req, res) => {
  const tempFilePath = req.file ? req.file.path : null;

  try {
    const {
      showName, episodeNumber, episodeText, broadcaster, date, scheduleTime,
      publishTimestamp: rawTs, playlist, wpShowId, wpBroadcasterId,
      manual_schedule, skipWP
    } = req.body;
    const isManual = manual_schedule === 'on';
    const doSkipWP = skipWP === '1' || skipWP === 'true';

    if (!showName || !broadcaster || !date || !scheduleTime) {
      return res.status(400).json({ error: 'All form fields are required' });
    }
    if (!req.file) {
      return res.status(400).json({ error: 'Audio file is required' });
    }

    const [y, m, d] = date.split('-');
    const formattedDate = `${d}/${m}/${y.slice(2)}`;

    const publishTimestamp = parseInt(rawTs, 10);
    const now = Math.floor(Date.now() / 1000);
    const isScheduled = publishTimestamp && publishTimestamp > now + 60;

    const title = showName === 'Shabi On The Rocks'
      ? ['Shabi on the Rocks', episodeNumber, episodeText ? `- ${episodeText}` : '', `דוד שאבי ${formattedDate}`].filter(Boolean).join(' ')
      : [showName, episodeNumber, `- ${broadcaster} ${formattedDate}`].filter(Boolean).join(' ');
    const description = [
      `<strong>Show:</strong> ${showName}`,
      `<strong>Episode:</strong> ${episodeNumber}`,
      `<strong>Broadcaster:</strong> ${broadcaster}`,
      `<strong>Date:</strong> ${formattedDate}`,
      ``,
      `<strong>Playlist:</strong>`,
      playlist.replace(/\n/g, '<br/>')
    ].join('\n');

    res.writeHead(200, { 'Content-Type': 'text/plain', 'Transfer-Encoding': 'chunked' });
    const send = (msg) => res.write(JSON.stringify({ message: msg }) + '\n');

    // ── Upload to Podbean + WordPress ─────────────────────────────────────────
    send('Authenticating with Podbean...');
    const accessToken = await getAccessToken();

    send('Fetching podcast ID...');
    const podcastId = await getPodcastId(accessToken);

    send('Requesting upload authorization...');
    const originalName = req.file.originalname;
    const fileSize = req.file.size;
    const contentType = req.file.mimetype || 'audio/mpeg';

    const { presigned_url, file_key } = await authorizeUpload(accessToken, originalName, fileSize, contentType);

    send(`Uploading audio file (${(fileSize / 1024 / 1024).toFixed(1)} MB)...`);
    await uploadFileToS3(presigned_url, tempFilePath, contentType);

    send('Publishing episode to Podbean...');
    const episode = await createEpisode(accessToken, podcastId, title, description, file_key);

    // Queue on Rocky Radio
    const showKeyR = getShowKey(showName, broadcaster);
    const rockyR = await queueOnRocky(tempFilePath, req.file.originalname, showKeyR, broadcaster, date, null, isManual, scheduleTime);
    send(rockyR.ok ? 'Queued for Rocky Radio!' : `Rocky Radio: ${rockyR.reason || rockyR.error || 'skipped'}`);

    // Log upload
    addToUploadLog({
      title, showName, broadcaster, date, scheduleTime,
      publishTimestamp,
      podbeanUrl: episode.permalink_url,
      type: 'podbean'
    });

    let wpPostId = null;
    if (doSkipWP) {
      send('WordPress update skipped (Podbean only).');
      if (isScheduled) {
        send(`SCHEDULED:${episode.permalink_url || ''}|${publishTimestamp}|`);
      } else {
        send(`SUCCESS:Episode published! URL: ${episode.permalink_url}|`);
      }
    } else if (isScheduled) {
      send('Scheduling WordPress episode...');
      try {
        const wpResult = await createWordPressEpisode(title, description, publishTimestamp, wpShowId, wpBroadcasterId, showName, date, episode.media_url);
        wpPostId = wpResult?.id || null;
        send('WordPress episode scheduled!');
      } catch (err) {
        const wpErr = err.response?.data?.message || err.message;
        send(`WordPress warning: ${wpErr}`);
        console.error('[WordPress] Scheduled post failed:', err.response?.data || err.message);
      }
      send(`SCHEDULED:${episode.permalink_url || ''}|${publishTimestamp}|${wpPostId || ''}`);
    } else {
      send('Publishing to WordPress...');
      try {
        const wpResult = await createWordPressEpisode(title, description, null, wpShowId, wpBroadcasterId, showName, date, episode.media_url);
        wpPostId = wpResult?.id || null;
        send('WordPress episode published!');
      } catch (err) {
        const wpErr = err.response?.data?.message || err.message;
        send(`WordPress warning: ${wpErr}`);
        console.error('[WordPress] Post failed:', err.response?.data || err.message);
      }
      send(`SUCCESS:Episode published! URL: ${episode.permalink_url}|${wpPostId || ''}`);
    }

    res.end();

  } catch (err) {
    console.error('Upload error:', err.response?.data || err.message);
    const errorMsg = err.response?.data?.error_description || err.message || 'Upload failed';
    if (res.headersSent) {
      res.write(JSON.stringify({ message: `ERROR:${errorMsg}` }) + '\n');
      res.end();
    } else {
      res.status(500).json({ error: errorMsg });
    }
  } finally {
    if (tempFilePath && fs.existsSync(tempFilePath)) {
      try { fs.unlinkSync(tempFilePath); } catch {}
    }
  }
});

app.listen(PORT, () => {
  console.log(`\n🎙  ZeRock Podbean Uploader running at http://localhost:${PORT}\n`);
});
