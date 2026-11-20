# Account setup and monetization checklist

You have to do these steps yourself: each one needs your identity, phone or email
verification, and your login. Run `python -m clipagent accounts` at any point to
see what's connected and how close each account is to monetization.

> The thresholds below are the publicly stated rules as of late 2026. They change
> and differ by country, so confirm them in each app before relying on them.

## To-do

- [ ] **Create the Instagram account** for the channel
- [ ] Switch Instagram to a **Professional (Creator or Business) account**
- [ ] Link Instagram to a **Facebook Page** (required for the Graph API)
- [ ] **Connect Instagram**: set `IG_USER_ID` and `IG_ACCESS_TOKEN` in `.env`
- [ ] Set up public hosting for rendered clips (needed for Reels): `PUBLIC_VIDEO_BASE_URL`
- [ ] **Connect TikTok**: create the developer app and set `TIKTOK_ACCESS_TOKEN`
- [ ] **Connect YouTube**: create the channel, set up OAuth and set the `YOUTUBE_*` keys
- [ ] **Get permission** from creators: join clipping programs and message creators (section 4)
- [ ] Register each one with `python -m clipagent sources add ...`
- [ ] Record your commentary lines in your own voice (section 5)
- [ ] Turn on monetization on each platform once eligible (below)
- [ ] Run `python -m clipagent accounts` and confirm all three show as connected

---

## 1. Instagram

1. Create the account in the Instagram app. Use a dedicated email and a name that matches
   your TikTok and YouTube handles.
2. Settings → Account type and tools → **Switch to professional account** → Creator
   (or Business). Choose the *Gaming video creator* category.
3. Create a Facebook Page for the channel and link it: Settings → Accounts Center, or Page
   settings → Linked accounts → Instagram.
4. At [developers.facebook.com](https://developers.facebook.com), create an app of type
   *Business*. Add the **Instagram Graph API** product with these permissions:
   `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `pages_show_list`.
5. In the Graph API Explorer, generate a user token and exchange it for a **long-lived token**
   (it lasts 60 days, so refresh it before it expires). Get your IG user id with
   `GET /me/accounts?fields=instagram_business_account`.
6. Put both in `.env` as `IG_ACCESS_TOKEN` and `IG_USER_ID`.

**Monetization:** check Professional dashboard → *Monetization status* and the available tools
(gifts, subscriptions, bonuses). Eligibility depends on your country and is often invite-based.
Your account must pass the Partner Monetization and Content Monetization policies.

## 2. TikTok

1. Use your existing account or create one. Switch to a **Business account**
   (Settings and privacy → Account).
2. At [developers.tiktok.com](https://developers.tiktok.com), create an app (type "Other").
   Fill in the icon, description, and the site links below, and verify the URL prefix:
   - Web/Desktop URL: `https://hj8vgm6pgn-dot.github.io/poly-bot-test/`
   - Terms of Service: `https://hj8vgm6pgn-dot.github.io/poly-bot-test/terms.html`
   - Privacy Policy: `https://hj8vgm6pgn-dot.github.io/poly-bot-test/privacy.html`
3. Add the products **Login Kit** and **Content Posting API**, and the scopes
   `user.info.basic`, `user.info.stats`, `video.list`, `video.upload`.
4. In Login Kit, add this **Redirect URI**:
   `https://hj8vgm6pgn-dot.github.io/poly-bot-test/callback.html`
5. Create a **Sandbox** (tab at the top), give it the same products, scopes and redirect URI,
   and add your own TikTok account as a target user. The sandbox has its own client key
   and secret.
6. Put the sandbox **client key** and **client secret** in `.env` as `TIKTOK_CLIENT_KEY` and
   `TIKTOK_CLIENT_SECRET`.
7. Run `python -m clipagent tiktok-login`. Open the link it prints, approve the app, copy the
   code from the page you land on, and paste it back. The agent renews the sign-in by itself
   after that.
8. Uploads go to your **TikTok inbox as drafts**. Finish each post in the app and add a
   trending sound. Posting directly needs TikTok to approve the app in Production, which
   requires a demo video of the tool working.

**Monetization (Creator Rewards Program):** 18+, an eligible country, about **10,000 followers**
and **100,000 views in the last 30 days**. **Only original videos longer than 1 minute earn.**
Use `python -m clipagent compile` to build 65–110s cuts from your best moments.

## 3. YouTube

1. Create the channel in YouTube Studio with the Google account you'll use.
2. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable
   **YouTube Data API v3** and **YouTube Analytics API**.
3. Create an **API key** and put it in `.env` as `YOUTUBE_API_KEY` (used for trend search).
4. Create an **OAuth client** (Desktop app) with these scopes: `youtube.upload`,
   `youtube.readonly`, `yt-analytics.readonly`. Authorize once to get a **refresh token**
   (for example with the OAuth Playground), then set `YOUTUBE_CLIENT_ID`,
   `YOUTUBE_CLIENT_SECRET` and `YOUTUBE_REFRESH_TOKEN`.
5. Verify the channel with a phone number (Studio → Settings → Channel → Feature eligibility).
   This lets you upload videos over 15 minutes and use custom thumbnails.
6. Until the Google Cloud app is verified, API uploads may be locked to private. Publish them
   from Studio, or submit the app for an API audit.

**Monetization (YouTube Partner Program):**
- *Fan-funding tier:* 500 subscribers, 3 public uploads in 90 days, and 3M Shorts views in
  90 days (or 3,000 watch hours).
- *Full YPP with ad revenue:* **1,000 subscribers** and **10M Shorts views in 90 days**
  (or 4,000 long-form watch hours in 12 months).
- Apply in Studio → *Earn*, and link an **AdSense** account for payouts.

---

## 4. Getting footage you are allowed to clip

The agent only clips channels on its approved-sources list, and each one needs proof.

### Clipping programs

Creators (and agencies working for them) pay clippers per view and publish the rules.

1. Find campaigns: search Whop for "clipping" or "content rewards", and check the Discord
   servers and channel descriptions of GTA 6 streamers for a "clipping program" link.
2. Read the rules before you join. Note the payout per 1,000 views, required hashtags or
   mentions, which platforms count, minimum view counts, and whether they supply raw footage.
3. Join, then register it:
   ```bash
   python -m clipagent sources add @creator --permission clipping_program \
       --proof <campaign link> --payout <rate> --tags <required,hashtags> --rules "<key rules>"
   ```
4. Most campaigns pay only after you **submit your post links** on their platform. The agent
   tracks estimated payouts (`metrics`), but submitting the links is on you.

For Twitch or Kick streamers add `--platform twitch` (or `kick`) and use their login name.

### Direct permission

Mid-sized creators often say yes, because a clips channel is free promotion. A message
that works:

> Hi [name], I run ViceOnTheIce, a GTA 6 shorts channel on TikTok, Reels and YouTube Shorts.
> I'd like to clip moments from your GTA 6 videos into vertical shorts. Every clip would
> credit you on screen and in the caption with your handle, and I'd take any clip down on
> request. Are you OK with that? If you have rules (hashtags, links, things to avoid),
> send them and I'll follow them.

Save the reply (screenshot or email) in a `proofs/` folder and register it:

```bash
python -m clipagent sources add @creator --permission direct --proof proofs/creator_dm.png
```

If they set an end date, add `--expires YYYY-MM-DD`. If they withdraw permission, run
`sources disable <id>` and delete the posted clips.

### Creative Commons

`python -m clipagent discover --cc` finds GTA 6 videos published under YouTube's CC BY
licence and credits them the way the licence requires. Expect few results, and check each
one: some uploaders mark videos CC that they do not own.

### Things permission does not cover

- A creator can only give permission for **their own** recording and voice. Music playing in
  their stream can still trigger a claim on your clip.
- Keep the proof. If a clip gets a copyright claim, the proof is what you dispute it with.
- Content ID claims can still happen automatically even when you have permission.

## 5. Voiceover in your own voice (recommended)

1. Run `python -m clipagent script`. It lists each clip's commentary line and the file
   name to save it under.
2. Record each line. On iPhone, Voice Memos works: record, then share the file to your
   computer and rename it (for example `clip_00012.m4a`). Put it in the `voice/` folder.
3. Run `python -m clipagent voice-test voice/clip_00012.m4a` and listen to
   `out/voice_test.wav`. By default the agent only cleans the recording up and keeps your
   natural voice. To change the sound, add `--pitch -3` (deeper) or `--preset warm`, and
   put the setting you like in `.env` as `VOICE_PITCH` or `VOICE_PRESET`.
4. Run `python -m clipagent render --ids 12` to rebuild the clip with your voice.

Tips: record in a quiet room, hold the phone a hand's width from your mouth, and read
with energy. The line does not have to match the script word for word.

A synthetic voice is the fallback for clips you have not recorded: set
`ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`. TikTok asks you to label AI-generated
content, so turn on its "AI-generated" label for clips that use a synthetic voice. Clips
with your own pitch-shifted voice do not need it.

---

## Staying monetizable with clips of other creators

All three platforms demonetize *reused* content, and permission alone does not exempt you.
What keeps a clips channel eligible:

- **Add something to every clip.** The agent adds a hook, commentary captions and a
  voiceover. Your own recorded voice is stronger than a synthetic one.
- **Credit the creator** on screen and in the caption. The agent does this automatically.
- **Only use approved sources.** Never re-upload Rockstar trailers or creators who have
  not agreed.
- Do not present the videos as official, and do not post leaked or pre-release material.
- Make each upload different: do not post the same clip twice on one platform. Posting
  the same video to all three platforms is fine.
- Post consistently. One or two posts a day per platform is a good target.

Be realistic about YouTube: it is the strictest of the three about reused content, and
clips channels are sometimes refused monetization even when they follow all of this.
Clipping-program payouts do not depend on platform monetization, so they are the more
reliable income early on.

## Example: registering the Kai Cenat GTA 6 Marathon campaign (Content Rewards)

Its rules require his name in the caption or overlay, the hashtag `#KAICENAT`, him as the
main focus, no logos, and no templated thumbnails. Registered like this, the agent applies
all of that to every clip from his channels:

```bash
python -m clipagent sources add @KaiCenatLive --permission clipping_program \
    --proof "<campaign link on contentrewards.com>" --tags KAICENAT \
    --name-in-headline --plain-cover --credit "Kai Cenat" \
    --rules "Kai must be the main focus. Marathon streams from 18 Nov 2026 only. No logos. Nothing negative. No re-uploads of his own Shorts. Pool pays only above 750K total views."
python -m clipagent sources add @KaiCenatGaming --permission clipping_program \
    --proof "<campaign link on contentrewards.com>" --tags KAICENAT \
    --name-in-headline --plain-cover --credit "Kai Cenat"
python -m clipagent sources add kaicenat --platform twitch --permission clipping_program \
    --proof "<campaign link on contentrewards.com>" --tags KAICENAT \
    --name-in-headline --plain-cover --credit "Kai Cenat"
```

The Twitch source is the quick route. `discover --period 7d` lists his most-viewed Twitch
clips of the week, each a short moment viewers have already picked. `fetch --top 5` then
downloads the five biggest, and `scan` and `render` turn them into your own vertical cuts.
Check each one is from the GTA 6 marathon (18 Nov 2026 or later) before approving it.

## Example: pay-per-view campaigns (DiegoLoveless, Bobby VanRiper, KreekCraft)

Fill in the exact wording, hashtags and files from each campaign's brief after joining.
`--cta` puts a call to action on screen for the last seconds and in the caption.
`--overlay` keeps a supplied logo on screen for the whole clip.

```bash
python -m clipagent sources add diegoloveless --platform twitch --permission clipping_program \
    --proof "<campaign link>" --payout 1.0 --name-in-headline \
    --cta "Watch live: twitch.tv/diegoloveless" --credit "DiegoLoveless"

python -m clipagent sources add <bobby's twitch login> --platform twitch --permission clipping_program \
    --proof "<campaign link>" --payout 1.5 --name-in-headline --credit "Bobby VanRiper"

python -m clipagent sources add <kreekcraft's channel> --permission clipping_program \
    --proof "<campaign link>" --payout 1.0 --name-in-headline --credit "KreekCraft" \
    --overlay assets/kreekcraft_logo.png --overlay-pos top-right \
    --cta "Watch the full stream on KreekCraft's channel"
```
