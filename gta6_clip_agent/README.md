# GTA 6 Viral Clip Agent

Finds the best GTA 6 moments in footage from creators who have **given you
permission**, turns them into credited vertical shorts with your own commentary,
and posts them to TikTok, Instagram Reels and YouTube Shorts.

```
approved sources (clipping programs, direct permission, Creative Commons)
        │  discover + fetch (blocked for any channel not on the list)
        ▼
footage ──► analyze (audio + motion signals) ──► top moments per video
                                                   │
trends + your own stats ──► Claude judges frames, writes hook / title / caption / commentary
                                                   │
        render 1080x1920: hook text, commentary captions + voiceover, creator credit
                                                   │
                 you review + approve ──► publish (YT / IG / TikTok draft)
                                                   │
                       metrics ──► next scoring run, and estimated program payouts
```

## Setup

**Windows:** open PowerShell in the `gta6_clip_agent` folder and run

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

It installs Python and ffmpeg if missing, installs the packages, creates `.env`, and runs
the tests.

**Mac / Linux:**

```bash
cd gta6_clip_agent
pip install -r requirements.txt     # also needs ffmpeg on PATH
cp .env.example .env                # fill in the keys you have; every platform is optional
```

**First-time setup:** follow [SETUP.md](SETUP.md) to connect the accounts, get
permission from creators, and turn on monetization.

## Usage

```bash
# 1. Say who you are allowed to clip
python -m clipagent sources add @creator --permission clipping_program \
    --proof https://whop.com/their-campaign --payout 1.5 --tags creatorclips
python -m clipagent sources add @othercreator --permission direct --proof proofs/dm.png
python -m clipagent sources list

# 2. Get footage (only from approved sources)
python -m clipagent discover --cc     # their GTA 6 uploads, plus Creative Commons videos
python -m clipagent videos
python -m clipagent fetch --top 3
python -m clipagent import vod.mp4 --source 2   # a file the creator sent you

# 3. Make clips
python -m clipagent trends
python -m clipagent scan              # add --no-ai to skip Claude, --no-speech to skip subtitles
python -m clipagent render --min 65   # layout picked automatically from the creator's face
python -m clipagent compile           # 65-110s cut (TikTok only pays on 60s+)
python -m clipagent review
python -m clipagent approve 3 7       # or: reject 4

# 4. Post and learn
python -m clipagent publish --dry-run
python -m clipagent publish --platforms youtube,tiktok --yt-privacy unlisted
python -m clipagent metrics           # views, likes, estimated clipping-program payouts
python -m clipagent accounts          # connection check + monetization progress

python -m clipagent run               # trends + discover + fetch + scan + render + review
```

Nothing is posted until you `approve` it.

## The permission gate

Every source records *why* you may use it and the proof. The gate runs at
download, scan, render and publish:

| Permission | What it means | Proof needed |
|---|---|---|
| `clipping_program` | The creator runs a clipping campaign | Link to the campaign or its rules |
| `direct` | The creator agreed in writing | The email or DM (link or saved file) |
| `creative_commons` | That video carries YouTube's CC BY licence | Checked on the video itself, twice |
| `own` | Your own recordings | None |

- A download is refused unless the video's real channel matches the approved channel.
- Footage sitting in `footage/` with no source attached is skipped.
- If a permission expires or you `disable` a source, its clips can no longer be rendered or posted.
- Every clip carries the creator's credit on screen and in the caption, plus any
  hashtags their campaign requires.

Creative Commons has a known weakness: some uploaders mark videos CC that they
do not own. Look at those before approving the clips.

## Clip styles

Each source has a style, set with `--style` when you add it:

| Style | Audio | On screen | Length | Default for |
|---|---|---|---|---|
| `campaign` | The creator's original audio | Headline banner, subtitles of what they say, credit | 10–20 s | Clipping programs |
| `commentary` | Original audio lowered under your voiceover | Hook, your commentary captions, credit | 12–35 s | Direct permission, Creative Commons |

Headlines, titles and captions never use the creator's name: the channel sells the moment,
and the creator is credited separately on screen and in the caption. If a campaign's rules
require the name in the title, add that source with `--name-in-headline`.

Campaign style is what clipping programs pay for: the creator's own voice, untouched.
Commentary style adds more of your own input, which helps with platform monetization.

## Speech and layout

- **Speech-to-text** (`clipagent/transcribe.py`) runs locally with faster-whisper. The
  transcript is used three ways: talking-heavy stretches score higher, Claude reads what is
  said in each candidate, and clip edges snap to pauses so nobody is cut off mid-sentence.
  It also provides the word-timed subtitles.
- **Layout** (`clipagent/layout.py`) looks for the creator's face in each clip:
  - a small face-cam gives the **stacked** layout (creator on top, scene below, headline
    banner across the middle),
  - a creator who fills the frame gives a 9:16 **crop** centred on their face,
  - no steady face gives the full frame over a **blurred** background.

  Override with `render --mode stack|crop|blur`.

Reaction videos have two owners: the creator owns the reaction, the game publisher owns the
trailer on screen. The creator's permission covers only their part, so the agent favours
moments where the creator is talking over stretches of raw trailer.

## Covers

Every rendered clip also gets a cover image in `out/covers/`, styled after the channel's
profile picture: a dark navy-purple backdrop made from a frame of the clip, a striped
sunset disc, a cyan neon "GTA 6" tag (or "TOP 3" on a compilation), the clip's headline in
ice blue, and a thin sunset-gradient frame. Pick it as the cover when you post on TikTok and
Instagram. YouTube Shorts usually only lets you choose a frame from the video.

## Commentary and voiceover

Platforms demonetize channels that repost footage with nothing added, even with
permission. So for commentary-style sources Claude writes one or two sentences of
original commentary per clip. It is burned in as captions and spoken over the clip,
with the original audio lowered underneath.

**Your own voice (recommended).** It counts for more than a synthetic voice.

```bash
python -m clipagent script                       # the lines to record, and the file name for each
# record each line on your phone or computer and save it as voice/clip_00012.m4a
python -m clipagent voice-test voice/clip_00012.m4a --pitch -3   # hear the effect first
python -m clipagent render --ids 12              # re-render that clip with your voice
```

The agent removes background hiss, trims silence, evens out the volume and lowers
the pitch by `VOICE_PITCH` semitones (default `-3`; try `-2` to `-5`). Or pick a named
effect with `VOICE_PRESET`: `natural_low`, `warm`, `narrator`, `different` (same pitch,
different tone) or `light`. Try one with `voice-test me.m4a --preset warm`. A lower pitch
makes your voice less recognisable to people who know you, but it is not real
anonymity: the effect can be reversed.

**Delivery tuning.** Research on voice and engagement points to delivery over pitch:
variation in loudness, a brisk pace, no dead air, clean audio. So the agent shortens any
pause longer than `VOICE_MAX_PAUSE` (0.35 s), speeds a slow read up toward
`VOICE_TARGET_WPM` (170 words a minute, by at most 12%), and compresses only lightly so
the natural rise and fall of your voice survives.

**Synthetic voice (fallback).** Used only for clips with no recording: ElevenLabs
(`ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`), or the free local `piper` / `espeak-ng`.
With neither, the commentary is captions only.

## What each platform API allows

| Platform | Trend research | Your analytics | Publishing |
|---|---|---|---|
| YouTube | Public search + stats (API key) | Video stats | Resumable upload (OAuth, `youtube.upload`) |
| Instagram | Hashtag top media (Business/Creator account) | Media insights | Reels via Graph API; the video must be at a public URL (`PUBLIC_VIDEO_BASE_URL`) |
| TikTok | No public search (Research API is for approved researchers only) | Your own videos (`video.list`) | Draft to your TikTok inbox (`video.upload`). Direct posting needs an audited app |

## How moments are scored

1. **Signals** (`clipagent/analyze.py`): loudness, sudden audio spikes, motion and cuts,
   sampled 4x/sec and z-scored. Windows of 12–35 s are scored on how strong the first
   3 seconds are, how intense the clip stays, and whether it ends on action.
2. **Claude** (`clipagent/score.py`): sees 8 frames per candidate plus current trends,
   your best past posts and which creator the footage is from. It returns a 0–100 score,
   a keep/drop decision, a trim, the hook, a title, a caption, hashtags and the commentary.

Model and effort are set with `CLAUDE_MODEL` / `CLAUDE_EFFORT` (default `claude-opus-5-5`, `high`).

## Tests

```bash
python -m pytest -q tests     # builds synthetic videos; no network or API calls
```
