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

1. Use your existing account or create one. Switch to a **Creator / Business account**
   (Settings → Account).
2. At [developers.tiktok.com](https://developers.tiktok.com), create an app and add **Login Kit**
   and the **Content Posting API**. Request these scopes:
   `user.info.basic`, `user.info.stats`, `video.list`, `video.upload`.
3. Run the OAuth login once to get an access token and refresh token. Put the access token
   in `.env` as `TIKTOK_ACCESS_TOKEN`. Access tokens expire after about 24h; refresh them
   with the refresh token.
4. Uploads go to your **TikTok inbox as drafts**. Finish each post in the app and add a
   trending sound. To post directly from the agent, the app has to pass TikTok's audit.

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

## Staying monetizable with gameplay content

All three platforms demonetize *reused* or *unoriginal* content. Raw gameplay with no added
value is the most common reason gaming channels get rejected. To stay eligible:

- Use **only your own captures**. Never re-upload other creators' clips or Rockstar trailers.
- Add **your own creative input** to every video: commentary or voiceover, reactions, edits,
  and the hook text the agent adds. Voiceover counts most.
- Rockstar/Take-Two generally lets creators monetize their own gameplay videos. Don't
  present your videos as official, and don't post leaked or pre-release material.
- Make each upload different: don't post the same clip twice on one platform. Cross-posting
  the same video to all three platforms is fine.
- Post consistently. One or two posts a day per platform is a good target.
