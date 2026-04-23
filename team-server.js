const path = require('path');
const fs = require('fs');

const BASE_DIR = process.pkg ? path.dirname(process.execPath) : __dirname;

require('dotenv').config({ path: path.join(BASE_DIR, '.env') });
const express = require('express');
const axios = require('axios');
const schedule = require('node-schedule');
const nodemailer = require('nodemailer');

const app = express();
const PORT = process.env.TEAM_PORT || 3001;

app.use(express.urlencoded({ extended: false }));
app.use(express.json());
app.use(express.static(path.join(BASE_DIR, 'public')));

// ─── Team Members ─────────────────────────────────────────────────────────────

const TEAM_MEMBERS = [
  { name: 'Stas Leikin',     email: 'stas_leikin@intuit.com' },
  { name: 'Rami Cohen',      email: 'rami_cohen@intuit.com' },
  { name: 'Efrat Eliyahu',   email: 'efrat_eliyahu@intuit.com' },
  { name: 'Jacky Yatzik',    email: 'jacky_yatzik@intuit.com' },
  { name: 'Lior Vassertail', email: 'liorvassertail@gmail.com' },
  { name: 'Sergey Rura',     email: 'sergey_rura@intuit.com' },
  { name: 'Lev',             email: 'levguy@gmail.com' },
  { name: 'Roy Kuperman',    email: 'roy_kuperman@intuit.com' },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getWeekKey(d = new Date()) {
  const jan1 = new Date(d.getFullYear(), 0, 1);
  const weekNum = Math.ceil(((d - jan1) / 86400000 + jan1.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(weekNum).padStart(2, '0')}`;
}

function getWeekLabel(d = new Date()) {
  const day = d.getDay();
  const monday = new Date(d);
  monday.setDate(d.getDate() - ((day + 6) % 7));
  return `Week of ${monday.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`;
}

const TEAM_UPDATES_DATA_FILE = path.join(BASE_DIR, 'team-updates-data.json');

function saveTeamUpdate(name, updates, kudos, blockers) {
  let data = {};
  if (fs.existsSync(TEAM_UPDATES_DATA_FILE)) {
    try { data = JSON.parse(fs.readFileSync(TEAM_UPDATES_DATA_FILE, 'utf8')); } catch {}
  }
  const weekKey = getWeekKey();
  if (!data[weekKey]) data[weekKey] = {};
  data[weekKey][name] = { updates, kudos, blockers, submittedAt: new Date().toISOString() };
  fs.writeFileSync(TEAM_UPDATES_DATA_FILE, JSON.stringify(data, null, 2));
}

// ─── Email & Slack ────────────────────────────────────────────────────────────

async function sendWeeklyEmails(serverUrl, weekLabel) {
  if (!process.env.TEAM_EMAIL_USER || !process.env.TEAM_EMAIL_APP_PASSWORD) {
    console.warn('[TeamUpdate] Email not configured — skipping emails');
    return;
  }
  const transporter = nodemailer.createTransport({
    service: 'gmail',
    auth: { user: process.env.TEAM_EMAIL_USER, pass: process.env.TEAM_EMAIL_APP_PASSWORD },
  });
  for (const member of TEAM_MEMBERS) {
    const formUrl = `${serverUrl}/team-update?name=${encodeURIComponent(member.name)}`;
    await transporter.sendMail({
      from: `"Team Updates" <${process.env.TEAM_EMAIL_USER}>`,
      to: member.email,
      subject: `Weekly Update — ${weekLabel}`,
      html: `
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;background:#0f1117;color:#e2e8f0;border-radius:12px;">
          <h2 style="color:#6c63ff;margin:0 0 12px;">Hey ${member.name.split(' ')[0]} 👋</h2>
          <p style="margin:0 0 16px;color:#8892a4;">Meeting starts in 30 minutes. Take 2 minutes to share your weekly update with the team.</p>
          <p style="text-align:center;margin:28px 0;">
            <a href="${formUrl}" style="background:#6c63ff;color:#fff;text-decoration:none;padding:13px 28px;border-radius:8px;font-weight:600;font-size:1rem;display:inline-block;">Fill Weekly Update</a>
          </p>
          <p style="color:#4a5268;font-size:0.8rem;margin:0;">${weekLabel}</p>
        </div>
      `,
    });
    console.log(`[TeamUpdate] Email sent to ${member.email}`);
  }
}

async function sendSlackNotification(serverUrl, weekLabel) {
  const botToken = process.env.TEAM_SLACK_BOT_TOKEN;
  const channel  = process.env.TEAM_SLACK_CHANNEL || 'wft-row-care-leads';

  if (!botToken) {
    console.warn('[TeamUpdate] TEAM_SLACK_BOT_TOKEN not configured — skipping Slack');
    return;
  }

  const formUrl = `${serverUrl}/team-update`;

  await axios.post('https://slack.com/api/chat.postMessage',
    { channel, text: `<!here> please update your team notes prior to the meeting ${formUrl}` },
    { headers: { Authorization: `Bearer ${botToken}`, 'Content-Type': 'application/json' } }
  );
  console.log(`[TeamUpdate] Slack notification sent to #${channel}`);
}

async function sendWeeklyUpdateNotifications() {
  const serverUrl = process.env.TEAM_SERVER_URL || `http://localhost:${PORT}`;
  const weekLabel = getWeekLabel();
  console.log(`[TeamUpdate] Sending weekly notifications for ${weekLabel}`);
  try { await sendWeeklyEmails(serverUrl, weekLabel); } catch (e) { console.error('[TeamUpdate] Email error:', e.message); }
  try { await sendSlackNotification(serverUrl, weekLabel); } catch (e) { console.error('[TeamUpdate] Slack error:', e.message); }
}

// Every Tuesday at 10:30 AM Israel Time (30 min before the 11:00 AM team meeting)
schedule.scheduleJob({ hour: 10, minute: 30, dayOfWeek: 2, tz: 'Asia/Jerusalem' }, sendWeeklyUpdateNotifications);
console.log('[TeamUpdate] Scheduler registered — fires every Tuesday 10:30 AM Israel time');

// ─── Routes ───────────────────────────────────────────────────────────────────

app.get('/team-update', (req, res) => {
  res.sendFile(path.join(BASE_DIR, 'public', 'team-update.html'));
});

app.get('/team-display', (req, res) => {
  res.sendFile(path.join(BASE_DIR, 'public', 'team-display.html'));
});

app.get('/kudos-graph', (req, res) => {
  res.sendFile(path.join(BASE_DIR, 'public', 'kudos-graph.html'));
});

app.post('/api/team-update', (req, res) => {
  const { name, updates, kudos, blockers } = req.body;
  if (!name || !updates) return res.status(400).json({ error: 'Name and updates are required' });
  try {
    saveTeamUpdate(name, updates, kudos || '', blockers || '');
    res.json({ ok: true });
  } catch (err) {
    console.error('[TeamUpdate] Save failed:', err.message);
    res.status(500).json({ error: 'Failed to save update. Please try again.' });
  }
});

app.get('/api/team-updates', (req, res) => {
  let data = {};
  if (fs.existsSync(TEAM_UPDATES_DATA_FILE)) {
    try { data = JSON.parse(fs.readFileSync(TEAM_UPDATES_DATA_FILE, 'utf8')); } catch {}
  }
  const weekKey = getWeekKey();
  res.json({ weekKey, weekLabel: getWeekLabel(), updates: data[weekKey] || {} });
});

app.get('/api/team-updates-history', (req, res) => {
  let data = {};
  if (fs.existsSync(TEAM_UPDATES_DATA_FILE)) {
    try { data = JSON.parse(fs.readFileSync(TEAM_UPDATES_DATA_FILE, 'utf8')); } catch {}
  }
  res.json(data);
});

app.post('/api/team-update-trigger', async (req, res) => {
  try {
    await sendWeeklyUpdateNotifications();
    res.json({ ok: true, message: 'Weekly notifications sent' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`\n📋  Team Updates server running at http://localhost:${PORT}\n`);
});
