# Faceless YouTube Automation (history, facts & mysteries)

Runs every day on GitHub Actions with no human in the loop:

```
trending topics ─► topic picker (LLM + competitor outliers + own-channel stats)
   ─► Wikipedia research + freely-licensed images
   ─► 5 hooks scored by hookscore.py ─► full script (rotating story structure)
   ─► 10 titles linted by title.py ─► best title + thumbnail-text pairing
   ─► Edge neural voiceover (word-timed) ─► visuals per scene (Wikimedia / Pexels / Pixabay)
   ─► ffmpeg edit: Ken Burns, stock clips, burned captions (Shorts), music
   ─► thumbnail ─► description with chapters + sources + credits ─► tags
   ─► YouTube upload, scheduled publish time, SRT captions, playlist
   ─► history.json committed (dedup + performance feedback for tomorrow's picks)
```

Default output: **1 long video (~10 min) + 2 Shorts per day.** Total running cost: **$0**.

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
| `PIXABAY_API_KEY` | <https://pixabay.com/api/docs/> | stock video (Openverse images need no key) |

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

### 5. Optional
- Put royalty-free `.mp3` tracks from the YouTube Audio Library in `assets/music/` to add background music.
- Edit `channel/voice.md` to change the narrator's personality, and `config.yaml` to change the
  channel name, voices, counts, publish times and competitor channels.

## Local runs
```bash
cp .env.example .env    # fill in keys
.venv/bin/python main.py --longs 0 --shorts 1 --no-upload          # test a Short
.venv/bin/python main.py --topic "Dyatlov Pass incident" --format long --no-upload
.venv/bin/python main.py                                            # full daily run + upload
```
Each video's files land in `output/<date>-<format>-<topic>/`: `final.mp4`, `thumbnail.jpg`,
`script.json`, `metadata.json` and `captions.srt`.

## Monetization: what's actually required
- **YouTube Partner Program:** 1,000 subscribers **and** either 4,000 public watch hours in 12 months
  (long videos; Shorts views don't count toward them) or 10M Shorts views in 90 days.
- **"Inauthentic content" policy (July 2025):** YouTube demonetizes mass-produced, templated videos.
  This pipeline counters that with a different story structure on every video, research-grounded
  facts, real archival images, unique hooks and titles, and no repeated topics. But approval is a human
  review of your channel, so check the first week of videos yourself and adjust `channel/voice.md`.
- **AI disclosure:** narration by an AI voice over stock footage doesn't need the "altered or synthetic
  content" label. Set `contains_synthetic_media: true` if you ever add realistic AI-generated people or events.
- **Quota:** each upload costs about 1,600 of the 10,000 daily API units, so keep it to 5 or fewer videos a day.
