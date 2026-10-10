# Odd Annals — Faceless YouTube Automation (Hindi-first animated history)

Runs every day on GitHub Actions with no human in the loop:

```
reach step: stats for every video ─► make private ones public ─► post question comments
   ─► retitle clear underperformers ─► reports/reach-report.md (+ Actions run summary)
trending topics ─► topic picker (LLM + competitor outliers + own views & retention)
   ─► Wikipedia research ─► 5 hooks scored by hookscore.py ─► script split into shots
   ─► one AI illustration per sentence (Cloudflare Lucid Origin / FLUX, Pollinations backup)
   ─► Hindi translation (+ optional English) ─► Edge neural narration per shot (word-timed)
   ─► ffmpeg: animated pans/zooms per shot, burned captions (Shorts), music
   ─► thumbnail ─► description with chapters + sources ─► tags
   ─► YouTube upload, scheduled for Indian prime time, captions, auto playlists
   ─► history.json committed (dedup + performance feedback for tomorrow's picks)
```

Default output: **Hindi — 1 long video (~10 min) + 2 Shorts per day**. Set `languages.en.enabled: true`
in `config.yaml` to also publish English versions of the same videos (same illustrations).
Total running cost: **$0** (free tiers).

Hook scoring, title linting and the "outlier = multiple of the channel's own median" research method
come from [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill) (MIT) and
are vendored in `src/quality/`.

---

## One-time setup (about 45 minutes, then never again)

### 1. YouTube channel
1. Create the channel (use a Brand Account so it isn't tied to your personal name).
2. Verify it at <https://www.youtube.com/verify>. This is needed for custom thumbnails and videos over 15 minutes.

### 2. Free API keys
| Key | Where | Used for |
|---|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | scripts, topics, SEO |
| `GROQ_API_KEY` | <https://console.groq.com/keys> | backup LLM if Gemini is rate-limited |
| `PEXELS_API_KEY` | <https://www.pexels.com/api/> (optional; new keys paused) | stock video |
| `PIXABAY_API_KEY` | <https://pixabay.com/api/docs/> | backup visuals if image generation fails |
| `CF_ACCOUNT_ID` + `CF_API_TOKEN` | <https://dash.cloudflare.com/profile/api-tokens> → "Workers AI" template | AI illustrations (free 10,000 neurons/day) |

### 3. YouTube upload access (Google Cloud)
1. Go to <https://console.cloud.google.com/> and create a project. Under **APIs & Services → Library**, enable **YouTube Data API v3**.
2. Open **OAuth consent screen** and choose External. Add your email as a test user, then **click "Publish app" (In production)**.
   Leave it in "Testing" and the refresh token **expires every 7 days**, and the automation stops.
3. Open **Credentials → Create credentials → OAuth client ID → Desktop app** and download the JSON.
4. Run the following locally and sign in with the account that owns the channel:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python scripts/get_youtube_token.py ~/Downloads/client_secret_XXXX.json
   ```
   Google shows an "unverified app" warning. That's expected because it's your own app: click Advanced, then Continue.
5. **Submit the API audit form:** <https://support.google.com/youtube/contact/yt_api_form>.
   Until Google approves it, **every video uploaded through the API is locked to Private**.
   That's YouTube's rule for new API projects. Approval is free and usually takes a few days to a few weeks.
   While you wait, the videos still upload and you can make them public in YouTube Studio.

### 4. GitHub
1. Create a **private** repo and push this folder to it.
2. Under **Settings → Secrets and variables → Actions**, add all 7 values from `.env.example`.
3. Under **Actions → Daily videos → Run workflow**, tick **no_upload** for the first test run.
   Download the `videos-…` artifact and watch the result.
4. After that it runs by itself every day at 05:30 UTC. Videos go public at the times set in `config.yaml`.

### 5. Reach data (watch hours, retention, subscribers)
1. Enable **YouTube Analytics API**: <https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com>
2. Re-run `scripts/get_youtube_token.py` (it now also asks for read-only analytics access) and update
   `YT_REFRESH_TOKEN` in `.env` and in the GitHub secret.
Without this the daily reach report still shows views, likes, comments and privacy.

### 6. Optional
- Put royalty-free `.mp3` tracks from the YouTube Audio Library in `assets/music/` to add background music.
- Edit `channel/voice.md` to change the narrator's personality, and `config.yaml` to change the
  channel name, voices, counts, publish times and competitor channels.

## Regional channels (Tamil, Telugu, Bengali, Marathi)
Each language is the same daily story and the same pictures, translated and narrated, uploaded to
its **own channel** (YouTube recommends one language per channel). Per language, once:
1. Create the YouTube channel (e.g. *Odd Annals Tamil*) and verify it.
2. Create a **new Google Cloud project** for it (each project has its own 10,000/day upload quota),
   enable YouTube Data API v3, set up the OAuth consent screen (publish it), create a Desktop OAuth client.
3. `python scripts/get_youtube_token.py client_secret_xxx.json TA`, sign in with that channel, and add
   the three printed `YT_*_TA` values to `.env` and GitHub secrets.
4. In `config.yaml` set `languages.ta.enabled: true`.

## Local runs
```bash
cp .env.example .env    # fill in keys
.venv/bin/python main.py --longs 0 --shorts 1 --no-upload          # test a Short
.venv/bin/python main.py --topic "Dyatlov Pass incident" --format long --no-upload
.venv/bin/python main.py                                            # full daily run + upload
.venv/bin/python main.py --reach                                    # reach report + fixes only
.venv/bin/python main.py --upload-dir output/<run>/hi               # retry a failed upload
```
Each video's files land in `output/<date>-<format>-<topic>/`: `final.mp4`, `thumbnail.jpg`,
`script.json`, `metadata.json` and `captions.srt`.

## Copyright: why videos won't get strikes
| Part of the video | Source | Why it's safe |
|---|---|---|
| Illustrations | Generated by FLUX (Cloudflare) / Pollinations | New images, nobody else's work; prompts forbid logos, text and modern public figures |
| Stock clips (B-roll) | Pixabay / Pexels | Their licences allow commercial use in videos, no attribution needed |
| Fallback images | Wikimedia / Openverse, **public domain, CC0 or CC BY only** | Share-alike and non-commercial licences are filtered out; CC BY credits go in the description |
| Music + whooshes | **Synthesised by ffmpeg** at render time | No third-party audio at all, so Content ID has nothing to match |
| Script | Written by the AI from facts, in its own words | Facts aren't copyrightable; it's told never to copy sentences; Wikipedia is cited |
| Fonts | Anton, Mukta (SIL Open Font License) | Free for commercial use, including in videos |

**If you add music** to `assets/music/`, only use **YouTube Audio Library** tracks filtered to
*"Attribution not required"* (Studio > Audio Library). Avoid "free" music from random sites: much of it
is registered with Content ID and gets claimed.

**The daily reach report flags any claim** (a regional block or a rejection) as a ⚠️ alert.

**Narration voice:** Edge neural voices come from Microsoft's free read-aloud service through the open-source
`edge-tts` library. YouTube can't strike you over them, but Microsoft doesn't officially license that service
for commercial use. If you want an officially licensed voice, Azure Speech's free tier
(500,000 characters/month, the same voices) covers this channel's volume. Ask me to switch.

**Don't** add movie or TV clips, news footage, songs, or other creators' videos. Those *do* cause strikes,
and three strikes in 90 days delete a channel.

## Monetization: what's actually required
- **YouTube Partner Program:** 1,000 subscribers **and** either 4,000 public watch hours in 12 months
  (long videos; Shorts views don't count toward them) or 10M Shorts views in 90 days.
- **"Inauthentic content" policy (July 2025):** YouTube demonetizes mass-produced, templated videos.
  This pipeline counters that with a different story structure on every video, research-grounded
  facts, real archival images, unique hooks and titles, and no repeated topics. But approval is a human
  review of your channel, so check the first week of videos yourself and adjust `channel/voice.md`.
- **AI disclosure:** the illustrations are clearly stylized animation and the narration is an AI voice, so
  YouTube's "altered or synthetic content" label isn't required; descriptions still state it. Set
  `contains_synthetic_media: true` if you ever switch to realistic AI images of real people or events.
- **Quota:** each upload costs about 1,600 of the 10,000 daily API units, so keep it to 5 or fewer videos a day.
