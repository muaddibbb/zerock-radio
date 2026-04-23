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

// ─── Multi-account Podbean token cache ────────────────────────────────────────
const _tokenCache = {};

async function getTokenForAccount(clientId, clientSecret) {
  const cached = _tokenCache[clientId];
  if (cached && Date.now() < cached.exp - 60000) return cached.tok;
  const creds = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  const r = await axios.post(
    'https://api.podbean.com/v1/oauth/token',
    'grant_type=client_credentials',
    { headers: { Authorization: `Basic ${creds}`, 'Content-Type': 'application/x-www-form-urlencoded' } }
  );
  _tokenCache[clientId] = { tok: r.data.access_token, exp: Date.now() + r.data.expires_in * 1000 };
  return _tokenCache[clientId].tok;
}

function podbeanAccounts() {
  const { PODBEAN_CLIENT_ID: id1, PODBEAN_CLIENT_SECRET: s1,
          PODBEAN_CLIENT_ID_2: id2, PODBEAN_CLIENT_SECRET_2: s2 } = process.env;
  const list = [];
  if (id1 && s1) list.push({ clientId: id1, clientSecret: s1, label: 'zerockradio' });
  if (id2 && s2) list.push({ clientId: id2, clientSecret: s2, label: 'rockzerock (fallback)' });
  return list;
}

// Errors that mean "this account can't handle this upload — try the next one"
function isPodbeanQuotaError(err) {
  const code = err.response?.data?.error;
  const status = err.response?.status;
  return ['storage_limit_exceeded', 'monthly_bandwidth_exceeded', 'episode_limit_exceeded',
          'quota_exceeded', 'limit_exceeded', 'account_suspended', 'account_inactive',
          'account_quota_exceeded'].includes(code)
      || status === 402;
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

// WP shows taxonomy term IDs (from /wp-json/wp/v2/shows)
const WP_SHOW_TERM_IDS = {
  'Beat-oN מקומי':         317,
  'Black Parade':           49,
  'ON AIR':                 305,
  'On the Mend':            316,
  'Oy Vavoy':               71,
  'Rocktrip':               318,
  'RockTrip':               318,
  'Shabi On The Rocks':     53,
  'Stage Dive':             313,
  'The Breakdown':          253,
  'Time Warp':              308,
  'אני לא בפסקול':          43,
  'האחות':                  314,
  'השאלטר':                 306,
  'זה פרוג':                149,
  'זה רוק פורטה':           45,
  'מצעד הרוק של ישראל':    58,
  'נגד כיוון הזיפים':       42,
  'סינגלס':                 48,
  'סן פטרוק':               44,
  'פטרוק לילה':             50,
  'על הרוקר':               38,
  'ערב של אלבומים':         60,
  'מורידים את הרף':         28,
  'ספיישלים':               144,
  'רדיו זה פופ':            195,
};

// WP broadcasters taxonomy term IDs (from /wp-json/wp/v2/broadcasters)
const WP_BROADCASTER_TERM_IDS = {
  'יובל יוספסון':   37,
  'ערן הר-פז':      63,
  'ליאת בלו':       65,
  'ירון חכם':       39,
  'אלעד אביגן':     76,
  "ג'קי שרגא":      187,
  'ירון הורינג':    72,
  'רועי ויינברג':   67,
  'גלית קורני':     77,
  'אחיעד לוק':      83,
  'מתן בכור':       87,
  'טל סיון ובר קציר': 79,
  'אפרת קוטגרו':    315,
  'דוד שאבי':       78,
  'דורית אורן':     312,
  'יובל ביטון':     85,
  'יותם "דפיילר" אבני': 75,
  'שיר אסולין':     84,
  'נופר נירן':      310,
  'עדן גולן':       255,
  'עופר פרוינד':    168,
  'רועי קופרמן':    304,
  'סיון פישמן':     262,
  'טל אופיר':       263,
};

async function createWordPressEpisode(title, content, publishTimestamp, wpShowId, wpBroadcasterId, showName, date, podbeanUrl, broadcaster) {
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

  // Shows taxonomy — look up by show name, fall back to passed wpShowId
  const showTermId = WP_SHOW_TERM_IDS[showName] || (wpShowId ? parseInt(wpShowId, 10) : null);
  if (showTermId) {
    body.shows = [showTermId];
  }

  // Broadcasters taxonomy — look up by broadcaster name, fall back to passed wpBroadcasterId
  const broadcasterTermId = WP_BROADCASTER_TERM_IDS[broadcaster] || (wpBroadcasterId ? parseInt(wpBroadcasterId, 10) : null);
  if (broadcasterTermId) {
    body.broadcasters = [broadcasterTermId];
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
async function queueOnRocky(filePath, originalName, showKey, broadcaster, date, existingMediaUrl, isManual, scheduleTime, wpPostId) {
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
      wp_post_id:             wpPostId || null,
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

// Fetch latest Podbean episode for a given show (used by Rocky auto-rerun)
app.get('/api/latest-podbean-episode', async (req, res) => {
  const { showName, broadcaster } = req.query;
  if (!showName) return res.status(400).json({ error: 'showName required' });

  try {
    const accounts = podbeanAccounts();
    if (!accounts.length) return res.status(500).json({ error: 'No Podbean accounts configured' });

    // Try accounts in order until we get a result
    let episodes = [];
    for (const acct of accounts) {
      try {
        const accessToken = await getTokenForAccount(acct.clientId, acct.clientSecret);
        const podcastId   = await getPodcastId(accessToken);
        // Fetch up to 200 most recent episodes
        const epResp = await axios.get('https://api.podbean.com/v1/episodes', {
          params: { access_token: accessToken, podcast_id: podcastId, offset: 0, limit: 200 }
        });
        episodes = epResp.data.episodes || [];
        if (episodes.length) break;
      } catch (e) {
        console.warn(`[LatestEpisode] Account error: ${e.message}`);
      }
    }

    if (!episodes.length) return res.status(404).json({ error: 'No episodes found on Podbean' });

    // Filter: title must start with showName; if broadcaster given, title must include it
    const snLower = showName.toLowerCase().trim();
    const bcLower = (broadcaster || '').toLowerCase().trim();

    const matches = episodes.filter(ep => {
      const t = (ep.title || '').toLowerCase();
      return t.startsWith(snLower) && (!bcLower || t.includes(bcLower));
    });

    if (!matches.length) {
      return res.status(404).json({ error: `No episodes found for: ${showName}${broadcaster ? ' / ' + broadcaster : ''}` });
    }

    // Podbean returns newest first; sort by publish_time descending to be safe
    matches.sort((a, b) => (b.publish_time || 0) - (a.publish_time || 0));
    const ep = matches[0];

    return res.json({
      mediaUrl:    ep.media_url,
      title:       ep.title,
      episodeId:   ep.id,
      publishTime: ep.publish_time
    });

  } catch (err) {
    console.error('[LatestEpisode]', err.response?.data || err.message);
    return res.status(500).json({ error: err.message });
  }
});

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

// Explicitly publish a WP episode post at air time (bypasses wp-cron).
// Accepts { wp_post_id } OR fallback { show_name, date } to look up by slug.
app.post('/api/wp-publish', async (req, res) => {
  const { WP_URL, WP_USERNAME, WP_APP_PASSWORD } = process.env;
  if (!WP_URL || !WP_USERNAME || !WP_APP_PASSWORD) {
    return res.status(503).json({ error: 'WordPress not configured' });
  }
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const headers = { Authorization: `Basic ${credentials}`, 'Content-Type': 'application/json' };
  const { wp_post_id, show_name, date: broadcastDate } = req.body || {};

  let postId = wp_post_id ? parseInt(wp_post_id, 10) : null;

  // Fallback: look up by slug when no ID is stored
  if (!postId && show_name && broadcastDate) {
    const showSlug = SHOW_SLUGS[show_name];
    if (showSlug) {
      const [y, m, d] = broadcastDate.split('-');
      const slug = `${showSlug}-${d}${m}${y.slice(2)}`;
      try {
        const srResp = await axios.get(`${WP_URL}/wp-json/wp/v2/episodes`, {
          params: { slug, status: 'future,publish', per_page: 1 },
          headers: { Authorization: `Basic ${credentials}` }
        });
        postId = srResp.data?.[0]?.id || null;
        if (postId) console.log(`[WordPress] Resolved slug ${slug} → post ${postId}`);
      } catch (e) {
        console.error('[WordPress] Slug lookup failed:', e.response?.data || e.message);
      }
    }
  }

  if (!postId) {
    return res.status(404).json({ ok: false, error: 'WP post not found (no ID and slug lookup failed)' });
  }

  try {
    const patchResp = await axios.post(
      `${WP_URL}/wp-json/wp/v2/episodes/${postId}`,
      { status: 'publish' },
      { headers }
    );
    console.log(`[WordPress] Published post ${postId}: ${patchResp.data.link}`);
    res.json({ ok: true, post_id: postId, link: patchResp.data.link, status: patchResp.data.status });
  } catch (err) {
    console.error('[WordPress] Publish failed:', err.response?.data || err.message);
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
      manual_schedule
    } = req.body;
    const isManual = manual_schedule === 'on';

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
      : showName === 'על הרוקר'
        ? `על הרוקר בעריכת ${broadcaster} - ${formattedDate}`
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

    // ── Upload to Podbean (with per-account fallback) + WordPress ────────────
    const originalName = req.file.originalname;
    const fileSize = req.file.size;
    const contentType = req.file.mimetype || 'audio/mpeg';

    const accounts = podbeanAccounts();
    if (!accounts.length) throw new Error('No Podbean accounts configured in .env');

    let episode = null;
    for (let i = 0; i < accounts.length; i++) {
      const acct = accounts[i];
      try {
        send(`Authenticating with Podbean (${acct.label})...`);
        const accessToken = await getTokenForAccount(acct.clientId, acct.clientSecret);
        send('Fetching podcast ID...');
        const podcastId = await getPodcastId(accessToken);
        send('Requesting upload authorization...');
        const { presigned_url, file_key } = await authorizeUpload(accessToken, originalName, fileSize, contentType);
        send(`Uploading audio file (${(fileSize / 1024 / 1024).toFixed(1)} MB)...`);
        await uploadFileToS3(presigned_url, tempFilePath, contentType);
        send('Publishing episode to Podbean...');
        episode = await createEpisode(accessToken, podcastId, title, description, file_key);
        if (i > 0) console.log(`[Podbean] Used fallback account: ${acct.label}`);
        break; // success — stop trying accounts
      } catch (err) {
        const errCode = err.response?.data?.error;
        const errDesc = err.response?.data?.error_description || err.response?.data?.error || err.message;
        if (i < accounts.length - 1 && isPodbeanQuotaError(err)) {
          send(`Podbean ${acct.label} quota/limit: ${errDesc} — switching to fallback account...`);
          console.log(`[Podbean] Quota error on ${acct.label}: ${errCode} — trying next account`);
          continue;
        }
        throw err; // no more accounts or non-quota error
      }
    }

    // ── WordPress first (so wp_post_id is available for Rocky) ─────────────
    let wpPostId = null;
    if (isScheduled) {
      send('Scheduling WordPress episode...');
      try {
        const wpResult = await createWordPressEpisode(title, description, publishTimestamp, wpShowId, wpBroadcasterId, showName, date, episode.media_url, broadcaster);
        wpPostId = wpResult?.id || null;
        send('WordPress episode scheduled!');
      } catch (err) {
        const wpErr = err.response?.data?.message || err.message;
        send(`WordPress warning: ${wpErr}`);
        console.error('[WordPress] Scheduled post failed:', err.response?.data || err.message);
      }
    } else {
      send('Publishing to WordPress...');
      try {
        const wpResult = await createWordPressEpisode(title, description, null, wpShowId, wpBroadcasterId, showName, date, episode.media_url, broadcaster);
        wpPostId = wpResult?.id || null;
        send('WordPress episode published!');
      } catch (err) {
        const wpErr = err.response?.data?.message || err.message;
        send(`WordPress warning: ${wpErr}`);
        console.error('[WordPress] Post failed:', err.response?.data || err.message);
      }
    }

    // Queue on Rocky Radio (with wp_post_id so Rocky can publish at air time)
    const showKeyR = getShowKey(showName, broadcaster);
    const rockyR = await queueOnRocky(tempFilePath, req.file.originalname, showKeyR, broadcaster, date, null, isManual, scheduleTime, wpPostId);
    send(rockyR.ok ? 'Queued for Rocky Radio!' : `Rocky Radio: ${rockyR.reason || rockyR.error || 'skipped'}`);

    // Log upload
    addToUploadLog({
      title, showName, broadcaster, date, scheduleTime,
      publishTimestamp,
      podbeanUrl: episode.permalink_url,
      type: 'podbean'
    });

    if (isScheduled) {
      send(`SCHEDULED:${episode.permalink_url || ''}|${publishTimestamp}|${wpPostId || ''}`);
    } else {
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
