# GTA 6 Viral Clip Agent

Turns your long GTA 6 gameplay recordings into ready-to-post vertical shorts for
TikTok, Instagram Reels and YouTube Shorts, and learns from what performs.

```
footage/*.mp4 ──► analyze (audio + motion signals) ──► top N moments per recording
                                                         │
trends (YT search, IG hashtags, your own stats) ──► Claude judges frames, writes hook/title/caption
                                                         │
                                       render 1080x1920 (blur-fill, hook text, -14 LUFS)
                                                         │
                                    you review + approve ──► publish (YT / IG / TikTok draft)
                                                         │
                                       metrics ──► fed back into the next scoring run
```

## Setup

```bash
cd gta6_clip_agent
pip install -r requirements.txt     # also needs ffmpeg on PATH
cp .env.example .env                # fill in the keys you have; every platform is optional
```

Drop your recordings (OBS, console capture, etc.) into `footage/`.

## Usage

```bash
python -m clipagent trends            # pull trending GTA 6 shorts metadata
python -m clipagent scan              # find + score moments (add --no-ai to skip Claude)
python -m clipagent render --min 65   # render candidates scoring >= 65
python -m clipagent review            # see rendered clips, hooks and Claude's reasoning
python -m clipagent approve 3 7       # or: reject 4
python -m clipagent publish --dry-run
python -m clipagent publish --platforms youtube,tiktok --yt-privacy unlisted
python -m clipagent metrics           # refresh views/likes; best posts steer future scoring

python -m clipagent run               # trends + scan + render + review in one go
```

Nothing is posted until you `approve` it.

## What each platform API allows

| Platform | Trend research | Your analytics | Publishing |
|---|---|---|---|
| YouTube | Public search + stats (API key) | Video stats | Resumable upload (OAuth, `youtube.upload`) |
| Instagram | Hashtag top media (Business/Creator account) | Media insights | Reels via Graph API; the video must be at a public URL (`PUBLIC_VIDEO_BASE_URL`) |
| TikTok | No public search (Research API is for approved researchers only) | Your own videos (`video.list`) | Draft to your TikTok inbox (`video.upload`). Direct posting needs an audited app |

TikTok drafts are actually useful: you can add a trending sound in the app before
posting, and that often matters more than anything else.

## Footage and copyright

The agent edits **your own captures**. It reads only metadata (titles, hashtags,
counts) from other creators' posts to see which topics are trending, and never
downloads their videos. Re-uploading other people's clips or Rockstar's trailers
gets you copyright strikes and limits reach on all three platforms.

## How moments are scored

1. **Signals** (`clipagent/analyze.py`): loudness, sudden audio spikes, motion and cuts,
   sampled 4x/sec and z-scored. Windows of 12–35 s are scored on how strong the first
   3 seconds are, how intense the clip stays, and whether it ends on action (which helps loops).
2. **Claude** (`clipagent/score.py`): sees 8 frames per candidate plus current trends and
   your best past posts. It returns a 0–100 score, a keep/drop decision, a trim, the on-screen
   hook, a title, a caption and hashtags. It uses structured JSON output, and the
   trend context is prompt-cached across candidates.

Model and effort are set with `CLAUDE_MODEL` / `CLAUDE_EFFORT` (default `claude-opus-5-5`, `high`).

## Tests

```bash
python -m pytest -q tests     # builds a synthetic video; no network or API calls
```
