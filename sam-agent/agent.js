/**
 * ZeRock SAM Broadcaster Agent
 * Polls the ZeRock server for new episodes and schedules them in SAM Broadcaster Pro via HTTP API.
 *
 * Build: npm run build  (produces sam-agent.exe via pkg)
 * Config: config.json next to the EXE (copy from config.example.json)
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');

// ─── Config ───────────────────────────────────────────────────────────────────

const CONFIG_FILE = path.join(path.dirname(process.execPath), 'config.json');

function loadConfig() {
  if (!fs.existsSync(CONFIG_FILE)) {
    console.error(`[ERROR] config.json not found at: ${CONFIG_FILE}`);
    console.error('Copy config.example.json to config.json and fill in your settings.');
    process.exit(1);
  }
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
  } catch (e) {
    console.error('[ERROR] Failed to parse config.json:', e.message);
    process.exit(1);
  }
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

function get(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    lib.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve(data); }
      });
    }).on('error', reject);
  });
}

function post(url, body) {
  return new Promise((resolve, reject) => {
    const payload = typeof body === 'string' ? body : JSON.stringify(body);
    const parsed = new URL(url);
    const lib = parsed.protocol === 'https:' ? https : http;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload)
      }
    };
    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve(data); }
      });
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    const file = fs.createWriteStream(destPath);
    lib.get(url, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close();
        fs.unlinkSync(destPath);
        return downloadFile(res.headers.location, destPath).then(resolve).catch(reject);
      }
      res.pipe(file);
      file.on('finish', () => file.close(resolve));
    }).on('error', (err) => {
      fs.unlinkSync(destPath);
      reject(err);
    });
  });
}

// ─── SAM Broadcaster HTTP API ─────────────────────────────────────────────────
// SAM Pro 2018.7 exposes a web server (default port 8080) with a simple HTTP API.
// You must enable it in SAM: Settings → Web Server → Enable Web Server

async function samAddToScheduler(config, entry) {
  const { samHost, samPort, samPassword } = config;
  const base = `http://${samHost}:${samPort}`;

  // Build the schedule time string: SAM expects "YYYY-MM-DD HH:MM:SS"
  const dt = new Date(entry.publishTimestamp * 1000);
  const pad = n => String(n).padStart(2, '0');
  const scheduleStr = `${dt.getFullYear()}-${pad(dt.getMonth()+1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:00`;

  // SAM HTTP API: POST /api/scheduler/add
  // Parameters vary by SAM version — this uses the documented 2018.x endpoint format
  const body = {
    password: samPassword,
    filename: entry.localPath,
    title: entry.title,
    artist: entry.showName,
    starttime: scheduleStr,
    duration: 0,        // SAM will detect duration from file
    overlap: 0
  };

  console.log(`[SAM] Scheduling "${entry.title}" at ${scheduleStr}`);
  const result = await post(`${base}/api/scheduler/add`, body);
  console.log('[SAM] Response:', JSON.stringify(result));
  return result;
}

// ─── Main polling loop ────────────────────────────────────────────────────────

async function processEntry(config, entry) {
  const { serverUrl, downloadDir } = config;

  // Build local filename
  const safeTitle = entry.title.replace(/[^a-zA-Z0-9א-ת \-_.]/g, '').trim();
  const ext = path.extname(new URL(entry.mediaUrl).pathname) || '.mp3';
  const filename = `${safeTitle}${ext}`;
  const localPath = path.join(downloadDir, filename);

  console.log(`\n[AGENT] Processing: ${entry.title}`);
  console.log(`[AGENT] Media URL: ${entry.mediaUrl}`);

  // Download
  if (!fs.existsSync(localPath)) {
    console.log(`[DOWNLOAD] Saving to: ${localPath}`);
    await downloadFile(entry.mediaUrl, localPath);
    console.log('[DOWNLOAD] Done.');
  } else {
    console.log(`[DOWNLOAD] Already exists: ${localPath}`);
  }

  // Schedule in SAM
  entry.localPath = localPath;
  await samAddToScheduler(config, entry);

  // Acknowledge on server
  await post(`${serverUrl}/api/sam-ack/${entry.id}`, {});
  console.log(`[AGENT] Acknowledged entry ${entry.id}`);
}

async function poll(config) {
  const { serverUrl, pollIntervalSeconds } = config;
  try {
    const pending = await get(`${serverUrl}/api/sam-poll`);
    if (!Array.isArray(pending) || pending.length === 0) {
      process.stdout.write('.');
      return;
    }
    console.log(`\n[POLL] Found ${pending.length} pending episode(s)`);
    for (const entry of pending) {
      try {
        await processEntry(config, entry);
      } catch (err) {
        console.error(`[ERROR] Failed to process entry ${entry.id}:`, err.message);
      }
    }
  } catch (err) {
    console.error('\n[POLL ERROR]', err.message);
  }
}

async function main() {
  const config = loadConfig();
  const { downloadDir, pollIntervalSeconds = 60 } = config;

  // Ensure download directory exists
  if (!fs.existsSync(downloadDir)) {
    fs.mkdirSync(downloadDir, { recursive: true });
    console.log(`[AGENT] Created download directory: ${downloadDir}`);
  }

  console.log('╔══════════════════════════════════════════╗');
  console.log('║   ZeRock SAM Broadcaster Agent  v1.0     ║');
  console.log('╚══════════════════════════════════════════╝');
  console.log(`Server : ${config.serverUrl}`);
  console.log(`SAM    : ${config.samHost}:${config.samPort}`);
  console.log(`Polling every ${pollIntervalSeconds}s  (Ctrl+C to stop)\n`);

  // Poll immediately, then on interval
  await poll(config);
  setInterval(() => poll(config), pollIntervalSeconds * 1000);
}

main();
