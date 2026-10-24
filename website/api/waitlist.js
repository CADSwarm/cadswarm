// Waitlist API.
//   POST /api/waitlist            add or update a sign-up (public)
//   GET  /api/waitlist            list sign-ups (needs Authorization: Bearer $ADMIN_TOKEN)
//   GET  /api/waitlist?format=csv same list as a CSV file
import { Redis } from '@upstash/redis';
import { timingSafeEqual } from 'node:crypto';

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL,
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN,
});

const INDEX = 'wl:index';
const entryKey = (email) => `wl:entry:${email}`;
const FIELDS = { name: 80, role: 40, team: 40, agent: 40, build: 600 };
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const MAX_PER_HOUR = 10;

function clean(value, max) {
  return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max) : '';
}

function isAdmin(req) {
  const want = process.env.ADMIN_TOKEN || '';
  const got = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (want.length < 16 || got.length !== want.length) return false;
  return timingSafeEqual(Buffer.from(got), Buffer.from(want));
}

async function signUp(req, res) {
  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = null; } }
  if (!body || typeof body !== 'object') return res.status(400).json({ error: 'Send the form as JSON.' });
  if (body.website) return res.status(200).json({ ok: true });          // honeypot: bots fill every field

  const email = clean(body.email, 120).toLowerCase();
  if (!EMAIL.test(email)) return res.status(400).json({ error: 'Enter an email address like sam@example.com.' });

  const ip = (req.headers['x-forwarded-for'] || '').split(',')[0].trim() || 'unknown';
  const rl = `wl:rl:${ip}`;
  const hits = await redis.incr(rl);
  if (hits === 1) await redis.expire(rl, 3600);
  if (hits > MAX_PER_HOUR) return res.status(429).json({ error: 'Too many sign-ups from this network. Try again in an hour.' });

  const now = new Date().toISOString();
  const entry = { email, updatedAt: now };
  for (const [k, max] of Object.entries(FIELDS)) entry[k] = clean(body[k], max);

  const added = await redis.zadd(INDEX, { nx: true }, { score: Date.now(), member: email });
  if (added) entry.joinedAt = now;
  await redis.hset(entryKey(email), entry);
  return res.status(200).json({ ok: true, alreadyJoined: !added });
}

async function list(req, res) {
  if (!isAdmin(req)) return res.status(401).json({ error: 'Wrong or missing admin token.' });
  const emails = await redis.zrange(INDEX, 0, -1, { rev: true });
  let rows = [];
  if (emails.length) {
    const p = redis.pipeline();
    emails.forEach((e) => p.hgetall(entryKey(e)));
    rows = (await p.exec()).filter(Boolean);
  }
  if (req.query.format === 'csv') {
    const cols = ['joinedAt', 'name', 'email', 'role', 'team', 'agent', 'build'];
    const esc = (v) => {
      let s = String(v ?? '');
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;                          // keep spreadsheets from running formulas
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', 'attachment; filename="cadswarm-waitlist.csv"');
    return res.status(200).send([cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n'));
  }
  return res.status(200).json({ count: rows.length, signups: rows });
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    if (req.method === 'POST') return await signUp(req, res);
    if (req.method === 'GET') return await list(req, res);
    res.setHeader('Allow', 'GET, POST');
    return res.status(405).json({ error: 'Method not allowed.' });
  } catch (err) {
    console.error('waitlist error', err);
    return res.status(500).json({ error: 'Couldn’t save your spot just now. Try again in a minute.' });
  }
}
