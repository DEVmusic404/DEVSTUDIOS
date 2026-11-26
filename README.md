# DEVSTUDIOS

DEV Command is a local Windows workspace for promoting official DEV Studios releases. It uses the studio’s YouTube channel for metadata, your local masters for footage, and your approval for public actions. There are no paid service dependencies or ad buys.

## Set up on C3-PO

Install Python 3.11 or 3.12 and put FFmpeg and FFprobe on your user PATH. Use a build with libx264 and the subtitles filter. Install and start Ollama if you want AI drafts. Pull a local model and put its exact name in the config. Whisper runs on CPU by default, with an optional CUDA setting if your CTranslate2 CUDA/cuDNN runtime is installed. The first transcription downloads the selected Whisper model.

Run these from PowerShell in your checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-media.txt
$DevCommandSettings = Join-Path $env:LOCALAPPDATA "DEV Command"
New-Item -ItemType Directory -Force $DevCommandSettings
Copy-Item config.example.toml (Join-Path $DevCommandSettings "config.toml")
```

Edit that config. The default master folder is `Videos\DEV Studios\Ready to Promote` under your user profile. Use the YouTube ID or exact video title as the master’s filename stem for preselection. You can always choose a master manually. The same local Windows account can run the dashboard and worker.

### Connect accounts

- **YouTube** — Enable YouTube Data API v3 and YouTube Analytics API in your Google Cloud project. Create a Desktop OAuth client, grant access to the studio owner, enter the studio channel ID in the config, and run `.\.venv\Scripts\python.exe -m devcommand.connect youtube C:\path\client_secret.json`. Choose the DEV Studios channel in consent. Google OAuth apps left in external testing can require periodic reconnection. The app imports YouTube metadata and replies to audience comments.
- **Meta** — Use a Facebook Login Meta app and a Page access token for the DEV Studios Page. Link the professional Instagram account to the Page. Enter the Page ID, linked Instagram professional account ID, and a Graph API version enabled for the app. Grant the applicable Page permissions including `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`, and `pages_read_user_content`, and Instagram permissions including `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, and `instagram_manage_insights`. Access requirements depend on your app and role. Run `.\.venv\Scripts\python.exe -m devcommand.connect meta` and paste the Page token at the hidden prompt. Re-run the command to replace expired or revoked credentials.
- **TikTok** — Approve the export, download the clip and caption, and upload in TikTok.

Google OAuth credentials and the Meta token live in the OS credential vault. Keep OAuth JSON outside the checkout. Restart both processes after changing account IDs or config. Post destinations are checked against live account identity before submission.

### Run

```powershell
.\scripts\install-worker.ps1
.\scripts\start-dashboard.ps1
```

You can also run `.\.venv\Scripts\python.exe -m devcommand.worker` from a second console. The scheduled task starts at your Windows sign-in so it can access that user’s credential vault. It continues when the dashboard closes. The app runs on loopback at http://127.0.0.1:8765. Resolve any connection messages on the Connections tab before approving a campaign.

## Studio workflow

1. Sync uploads or choose **Import older uploads** on Connections. Select a release and choose the original master. Suggest a cut or enter the timestamps and horizontal crop manually. Queue a preview and refresh the release to check rendering.
2. Play the captioned result. To revise a cut or crop, render a new preview. Draft destinations from the desired preview. With no master use **Draft Facebook link**. Configure Ollama for custom copy and timing, or edit the built-in captions and defaults.
3. Review media, captions, account IDs, and publish times. Select the destinations you want and approve in one action. TikTok becomes an export. Facebook and Instagram go to the queue.
4. In Inbox, edit or request a local reply draft and approve the exact reply. Collaboration leads are a manual shortlist for now.
5. Compare recent posts and YouTube’s seven-day analytics in Results. Meta counters are subject to API availability and represent provider totals. External views and subscriber trends are reported independently of post engagement. Set `campaign_landing_url` to a real landing route you operate to add UTM tags to Facebook link posts. View those clicks in your site's analytics.

## Recovery and live acceptance

The worker saves job IDs, submission state, remote container IDs and publication receipts. Older pending posts return to drafts once the stale threshold is exceeded. After a timeout or shutdown during a Reel submission, the worker checks the saved container or Reel. A job that needs human verification stays visible. Inspect the destination, then record the confirmed ID, or return the post to draft only after verifying it did not publish. Interrupted replies wait for confirmation. The media pause stops new media jobs and lets the current job finish.

For the first live campaign, sync an official DEVPOOL video with a local master, render two distinct hooks, and preview both. Start with a Facebook Page link post. Verify its receipt and a Page reply. Then approve Instagram and Facebook Reels and verify playback, comment import, and approved replies. In parallel, verify that a YouTube viewer comment imports and an approved reply appears under the studio identity. Check your Meta app’s local upload support on the connected account before relying on scheduled publishing.

Run the local checks with `python -m unittest discover -s tests -v`. They use fake platform responses. Live account publishing, permissions, actual Whisper output, and Windows scheduled-task behavior require a run on C3-PO.

## Platform documentation

- [YouTube Data API](https://developers.google.com/youtube/v3) · [Reply API](https://developers.google.com/youtube/v3/docs/comments/insert) · [YouTube Analytics reports](https://developers.google.com/youtube/analytics/channel_reports)
- [Facebook Reels publishing collection](https://www.postman.com/meta/facebook/request/rgkn91u/1-create-reel) · [Instagram content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [TikTok content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)
