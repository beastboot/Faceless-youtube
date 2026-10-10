// Odd Annals Telegram bot (Cloudflare Worker, free plan). Telegram sends every message here
// (webhook); the bot answers only the owner's chat, with live numbers from the YouTube Data and
// Analytics APIs, the pipeline's reports from GitHub, and Gemini for free-form questions.
// Deployed by scripts/deploy_bot.py, which also stores the secrets used below.

const REPO = 'beastboot/Faceless-youtube';
const RAW = `https://raw.githubusercontent.com/${REPO}/main`;
const DASHBOARD = 'https://beastboot.github.io/Faceless-youtube/dashboard/';
const CHANNELS = [{key: 'hi', suffix: '', name: 'Odd Annals (Hindi)'}, {key: 'ta', suffix: '_TA', name: 'Odd Annals Tamil'}];

const HELP = `<b>Odd Annals bot</b> — ask anything, or use:
/stats — subscribers, views, videos (both channels)
/latest — newest videos with views, likes, comments
/video &lt;link or number from /latest&gt; — one video in depth
/analytics [days] — watch hours, views, subs, top videos (default 7 days)
/monetize — progress to YouTube Partner Program
/report — today's reach report
/runs — recent automation runs (made / failed)
/dashboard — open the dashboard
Add <code>ta</code> for the Tamil channel, e.g. <code>/latest ta</code>.
Or just type a question: "aaj kaunsi video sabse achhi chal rahi hai?"`;

export default {
  async fetch(req, env, ctx) {
    if (req.method !== 'POST') return new Response('ok');
    if (req.headers.get('X-Telegram-Bot-Api-Secret-Token') !== env.WEBHOOK_SECRET) return new Response('no', {status: 403});
    const update = await req.json().catch(() => ({}));
    const msg = update.message || update.edited_message;
    if (!msg || String(msg.chat.id) !== String(env.TELEGRAM_CHAT_ID)) return new Response('ok');
    ctx.waitUntil(handle(env, msg.chat.id, (msg.text || '').trim()).catch((e) =>
      send(env, msg.chat.id, `⚠️ ${esc(String(e && e.message || e)).slice(0, 400)}`)));
    return new Response('ok');
  },
};

async function handle(env, chat, text) {
  if (!text) return;
  const [cmdRaw, ...args] = text.split(/\s+/);
  const cmd = cmdRaw.toLowerCase().replace(/@.*$/, '');
  const ch = args.some((a) => a.toLowerCase() === 'ta') ? CHANNELS[1] : CHANNELS[0];
  const rest = args.filter((a) => a.toLowerCase() !== 'ta');
  await tg(env, 'sendChatAction', {chat_id: chat, action: 'typing'});
  switch (cmd) {
    case '/start': case '/help': return send(env, chat, HELP);
    case '/stats': return send(env, chat, await statsText(env));
    case '/latest': return send(env, chat, await latestText(env, ch));
    case '/video': return send(env, chat, await videoText(env, ch, rest[0]));
    case '/analytics': return send(env, chat, await analyticsText(env, ch, parseInt(rest[0]) || 7));
    case '/monetize': return send(env, chat, await monetizeText(env, ch));
    case '/report': return send(env, chat, await reportText());
    case '/runs': return send(env, chat, await runsText());
    case '/dashboard': return send(env, chat, `📊 <a href="${DASHBOARD}">Open the dashboard</a>`);
    default: return send(env, chat, await askGemini(env, text, ch));
  }
}

// ---------- Telegram ----------
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}
async function send(env, chat, html) {
  for (let i = 0; i < html.length; i += 3900) {   // Telegram's limit is 4096 characters
    const part = html.slice(i, i + 3900);
    const r = await tg(env, 'sendMessage', {chat_id: chat, text: part, parse_mode: 'HTML', disable_web_page_preview: true});
    if (!r.ok) await tg(env, 'sendMessage', {chat_id: chat, text: part.replace(/<[^>]+>/g, '')});
  }
}

// ---------- Google APIs ----------
const tokens = {};
async function token(env, ch) {
  const t = tokens[ch.suffix];
  if (t && t.exp > Date.now()) return t.v;
  const id = env['YT_CLIENT_ID' + ch.suffix], secret = env['YT_CLIENT_SECRET' + ch.suffix], refresh = env['YT_REFRESH_TOKEN' + ch.suffix];
  if (!refresh) throw new Error(`${ch.name} is not connected to the bot`);
  const r = await fetch('https://oauth2.googleapis.com/token', {method: 'POST',
    body: new URLSearchParams({client_id: id, client_secret: secret, refresh_token: refresh, grant_type: 'refresh_token'})});
  const j = await r.json();
  if (!j.access_token) throw new Error(`YouTube sign-in failed for ${ch.name}: ${j.error || r.status}`);
  tokens[ch.suffix] = {v: j.access_token, exp: Date.now() + (j.expires_in - 60) * 1000};
  return j.access_token;
}
async function yt(env, ch, path, params) {
  const r = await fetch(`https://www.googleapis.com/youtube/v3/${path}?${new URLSearchParams(params)}`,
    {headers: {Authorization: `Bearer ${await token(env, ch)}`}});
  const j = await r.json();
  if (j.error) throw new Error(`YouTube: ${j.error.message}`);
  return j;
}
async function ya(env, ch, params) {
  const r = await fetch(`https://youtubeanalytics.googleapis.com/v2/reports?${new URLSearchParams({ids: 'channel==MINE', ...params})}`,
    {headers: {Authorization: `Bearer ${await token(env, ch)}`}});
  const j = await r.json();
  if (j.error) throw new Error(`Analytics: ${j.error.message}`);
  const cols = (j.columnHeaders || []).map((c) => c.name);
  return (j.rows || []).map((row) => Object.fromEntries(row.map((v, i) => [cols[i], v])));
}
const day = (d) => new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
const n = (x) => Number(x || 0).toLocaleString('en-IN');
const ago = (iso) => {
  const h = (Date.now() - Date.parse(iso)) / 36e5;
  return h < 1 ? `${Math.round(h * 60)} min ago` : h < 48 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
};
const dur = (iso) => {
  const m = /PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/.exec(iso || '') || [];
  return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
};

async function channel(env, ch) {
  const j = await yt(env, ch, 'channels', {part: 'snippet,statistics,contentDetails', mine: 'true'});
  return j.items && j.items[0];
}
async function latest(env, ch, max = 8) {
  const c = await channel(env, ch);
  const pl = await yt(env, ch, 'playlistItems', {part: 'contentDetails', playlistId: c.contentDetails.relatedPlaylists.uploads, maxResults: String(max)});
  const ids = (pl.items || []).map((i) => i.contentDetails.videoId);
  if (!ids.length) return [];
  const v = await yt(env, ch, 'videos', {part: 'snippet,statistics,status,contentDetails', id: ids.join(',')});
  return ids.map((id) => v.items.find((x) => x.id === id)).filter(Boolean);
}

// ---------- commands ----------
async function statsText(env) {
  const out = ['📈 <b>Channels right now</b>'];
  for (const ch of CHANNELS) {
    try {
      const c = await channel(env, ch);
      const s = c.statistics;
      out.push(`\n<b>${esc(c.snippet.title)}</b>\n👥 ${n(s.subscriberCount)} subscribers\n👁 ${n(s.viewCount)} views\n🎬 ${n(s.videoCount)} videos`);
    } catch (e) {
      out.push(`\n<b>${ch.name}</b>: ${esc(e.message)}`);
    }
  }
  return out.join('\n');
}

async function latestText(env, ch) {
  const vids = await latest(env, ch, 8);
  if (!vids.length) return `${ch.name}: no videos yet.`;
  const lines = vids.map((v, i) => {
    const s = v.statistics, short = dur(v.contentDetails.duration) <= 180;
    const status = v.status.privacyStatus === 'public' ? '' : v.status.publishAt ? ` ⏰ goes live ${ago(v.status.publishAt).replace(' ago', '')}` : ` 🔒 ${v.status.privacyStatus}`;
    return `${i + 1}. ${short ? '📱' : '🎞'} <a href="https://youtu.be/${v.id}">${esc(v.snippet.title.slice(0, 70))}</a>\n    👁 ${n(s.viewCount)} · 👍 ${n(s.likeCount)} · 💬 ${n(s.commentCount)} · ${ago(v.snippet.publishedAt)}${status}`;
  });
  return `🆕 <b>${ch.name} — latest videos</b>\n\n${lines.join('\n')}\n\nDetails: /video 1${ch.key === 'ta' ? ' ta' : ''}`;
}

async function videoText(env, ch, ref) {
  let id = (ref || '1').match(/(?:v=|youtu\.be\/|shorts\/)([\w-]{11})/)?.[1] || (/^[\w-]{11}$/.test(ref || '') ? ref : null);
  if (!id) {
    const vids = await latest(env, ch, 10);
    const v = vids[(parseInt(ref) || 1) - 1];
    if (!v) return 'No such video. Try /latest first.';
    id = v.id;
  }
  const v = (await yt(env, ch, 'videos', {part: 'snippet,statistics,status,contentDetails', id})).items?.[0];
  if (!v) return 'Video not found on this channel.';
  const s = v.statistics, len = dur(v.contentDetails.duration);
  const out = [`🎬 <b><a href="https://youtu.be/${id}">${esc(v.snippet.title)}</a></b>`,
    `${v.status.privacyStatus} · ${Math.floor(len / 60)}:${String(len % 60).padStart(2, '0')} long · published ${ago(v.snippet.publishedAt)}`,
    `👁 ${n(s.viewCount)} views · 👍 ${n(s.likeCount)} · 💬 ${n(s.commentCount)}`];
  try {
    const since = v.snippet.publishedAt.slice(0, 10);
    const [a] = await ya(env, ch, {startDate: since, endDate: day(0), filters: `video==${id}`,
      metrics: 'views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,shares,likes'});
    if (a && a.views) {
      out.push(`\n<b>Analytics</b> (YouTube is 1-3 days behind)`,
        `⏱ ${n(Math.round(a.estimatedMinutesWatched))} min watched · avg ${Math.round(a.averageViewDuration)}s (${(+a.averageViewPercentage).toFixed(0)}% of the video)`,
        `➕ ${n(a.subscribersGained)} subscribers · 🔁 ${n(a.shares)} shares`);
      const src = await ya(env, ch, {startDate: since, endDate: day(0), filters: `video==${id}`, metrics: 'views',
        dimensions: 'insightTrafficSourceType', sort: '-views', maxResults: '4'});
      if (src.length) out.push('🚦 Traffic: ' + src.map((r) => `${r.insightTrafficSourceType.toLowerCase().replace(/_/g, ' ')} ${n(r.views)}`).join(' · '));
    } else {
      out.push('\nAnalytics: not in yet (YouTube needs 1-3 days).');
    }
  } catch (e) {
    out.push(`\nAnalytics: ${esc(e.message)}`);
  }
  return out.join('\n');
}

async function analyticsText(env, ch, days) {
  days = Math.min(Math.max(days, 1), 365);
  const [t] = await ya(env, ch, {startDate: day(days + 2), endDate: day(0),
    metrics: 'views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost,likes,shares'});
  const top = await ya(env, ch, {startDate: day(days + 2), endDate: day(0), dimensions: 'video',
    metrics: 'views,estimatedMinutesWatched,averageViewPercentage', sort: '-views', maxResults: '5'});
  if (!t) return `${ch.name}: no analytics for the last ${days} days yet (YouTube is 1-3 days behind).`;
  let titles = {};
  if (top.length) {
    const v = await yt(env, ch, 'videos', {part: 'snippet', id: top.map((r) => r.video).join(',')});
    titles = Object.fromEntries(v.items.map((x) => [x.id, x.snippet.title]));
  }
  return [`📊 <b>${ch.name} — last ${days} days</b> (YouTube is 1-3 days behind)`,
    `👁 ${n(t.views)} views`, `⏱ ${(t.estimatedMinutesWatched / 60).toFixed(1)} watch hours`,
    `🎯 avg ${Math.round(t.averageViewDuration)}s watched (${(+t.averageViewPercentage).toFixed(0)}%)`,
    `👥 +${n(t.subscribersGained)} / -${n(t.subscribersLost)} subscribers`, `👍 ${n(t.likes)} · 🔁 ${n(t.shares)}`,
    top.length ? '\n<b>Top videos</b>\n' + top.map((r, i) => `${i + 1}. <a href="https://youtu.be/${r.video}">${esc((titles[r.video] || r.video).slice(0, 60))}</a> — ${n(r.views)} views, ${(+r.averageViewPercentage).toFixed(0)}% watched`).join('\n') : ''].join('\n');
}

async function monetizeText(env, ch) {
  const c = await channel(env, ch);
  const subs = +c.statistics.subscriberCount;
  // Watch hours count public long-form views in the last 365 days; Shorts views count over 90 days.
  const longRows = await ya(env, ch, {startDate: day(365), endDate: day(0), metrics: 'estimatedMinutesWatched', filters: 'creatorContentType==VIDEO_ON_DEMAND'}).catch(() => []);
  const shortRows = await ya(env, ch, {startDate: day(90), endDate: day(0), metrics: 'views', filters: 'creatorContentType==SHORTS'}).catch(() => []);
  const hours = (longRows[0]?.estimatedMinutesWatched || 0) / 60, sv = shortRows[0]?.views || 0;
  const bar = (p) => '▓'.repeat(Math.round(Math.min(1, p) * 10)) + '░'.repeat(10 - Math.round(Math.min(1, p) * 10));
  return [`💰 <b>${ch.name} — Partner Program progress</b>`,
    `👥 Subscribers: ${n(subs)} / 1,000  ${bar(subs / 1000)} ${Math.round(subs / 10)}%`,
    `⏱ Watch hours (12 months, long videos): ${n(Math.round(hours))} / 4,000  ${bar(hours / 4000)}`,
    `   or 📱 Shorts views (90 days): ${n(sv)} / 1,00,00,000  ${bar(sv / 1e7)}`,
    `\nNeed 1,000 subscribers AND (4,000 hours OR 10M Shorts views).`].join('\n');
}

async function reportText() {
  const r = await fetch(`${RAW}/reports/reach-report.md`, {cf: {cacheTtl: 60}});
  if (!r.ok) return 'No reach report yet.';
  const md = await r.text();
  // Turn the markdown table into compact lines for a phone screen.
  const lines = md.split('\n').map((l) => {
    if (/^\|[-:| ]+\|$/.test(l) || /^\| Video \|/.test(l)) return null;
    if (l.startsWith('|')) {
      const c = l.split('|').slice(1, -1).map((x) => x.trim());
      const t = c[0].replace(/\[(.*?)\]\((.*?)\)/, (_, a, b) => `<a href="${b}">${esc(a.slice(0, 55))}</a>`);
      return `• ${t}\n   👁 ${c[3]} · 👍 ${c[4]} · 💬 ${c[5]} · watched ${c[6]} · ${c[9] || ''}`;
    }
    return esc(l.replace(/^#+\s*/, '').replace(/\*\*/g, '')).replace(/^&gt; /, '');
  }).filter((l) => l !== null);
  return '📋 ' + lines.join('\n').replace(/\n{3,}/g, '\n\n').slice(0, 7600);
}

async function runsText() {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/runs?per_page=6`, {headers: {'User-Agent': 'odd-annals-bot'}});
  const j = await r.json();
  const icon = (x) => x.status !== 'completed' ? '⏳ running' : x.conclusion === 'success' ? '✅ passed' : x.conclusion === 'cancelled' ? '⚪ cancelled' : '❌ failed';
  return '⚙️ <b>Recent runs</b>\n' + (j.workflow_runs || []).map((x) =>
    `${icon(x)} — <a href="${x.html_url}">${esc(x.name)}</a> · ${x.event} · ${ago(x.created_at)}`).join('\n');
}

// ---------- free-form questions ----------
async function askGemini(env, question, ch) {
  const ctx = {};
  const safe = async (k, f) => { try { ctx[k] = await f(); } catch (e) { ctx[k] = `unavailable: ${e.message}`; } };
  await Promise.all([
    safe('channels', async () => Promise.all(CHANNELS.map(async (c) => {
      const x = await channel(env, c); return {name: x.snippet.title, ...x.statistics};
    }))),
    safe('latest_videos', async () => (await latest(env, ch, 10)).map((v) => ({id: v.id, title: v.snippet.title,
      published: v.snippet.publishedAt, privacy: v.status.privacyStatus, seconds: dur(v.contentDetails.duration), ...v.statistics}))),
    safe('last_28_days_by_video', async () => ya(env, ch, {startDate: day(30), endDate: day(0), dimensions: 'video',
      metrics: 'views,estimatedMinutesWatched,averageViewPercentage,subscribersGained', sort: '-views', maxResults: '15'})),
    safe('daily_history', async () => (await fetch(`${RAW}/data/daily.json`)).json()),
    safe('reach_report', async () => (await (await fetch(`${RAW}/reports/reach-report.md`)).text()).slice(0, 5000)),
  ]);
  const prompt = `You are the analytics assistant of the owner of the YouTube channel "Odd Annals" (Hindi history and
mystery animations, automated daily uploads; a Tamil sister channel). Answer the owner's question using ONLY the data
below; say plainly when the data doesn't contain the answer (analytics lag 1-3 days). Be short and concrete, with
numbers, and end with one practical suggestion if useful. Reply in the language/style the owner used (Hindi, Hinglish
or English). Private videos were made private on purpose by the owner (e.g. sensitive topics): never suggest
publishing them. Scheduled videos (publishAt) go public by themselves. Telegram HTML only: <b>, <i>, <a href>; no markdown, no tables.
Today: ${new Date().toISOString().slice(0, 10)}. Channel in focus: ${ch.name}.
DATA: ${JSON.stringify(ctx).slice(0, 30000)}
QUESTION: ${question}`;
  for (const model of ['gemini-flash-latest', 'gemini-flash-lite-latest']) {
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${env.GEMINI_API_KEY}`, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({contents: [{parts: [{text: prompt}]}], generationConfig: {temperature: 0.3}})});
    const j = await r.json().catch(() => ({}));
    const out = j.candidates?.[0]?.content?.parts?.map((p) => p.text).join('');
    if (out) return out.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/```\w*/g, '');
  }
  return "I couldn't reach Gemini right now. Try /stats, /latest or /analytics.";
}
