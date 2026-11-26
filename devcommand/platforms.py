import json
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx
import keyring

from .store import ACCOUNTS, C, MEDIA, db, rows, run

SERVICE = "DEV Command"
YT = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
VERSION = C.get("graph_version", "v25.0")
if not re.fullmatch(r"v\d+\.\d+", VERSION):
    raise ValueError("Invalid Meta Graph API version.")
GRAPH = f"https://graph.facebook.com/{VERSION}"


class PlatformError(RuntimeError):
    pass


class ConnectionInterrupted(PlatformError):
    pass


def secret(name):
    return keyring.get_password(SERVICE, name)


def request(method, url, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)
    except httpx.TransportError as exc:
        raise ConnectionInterrupted(f"Connection interrupted ({type(exc).__name__}).") from None
    if not response.is_success:
        if response.status_code in {401, 403}:
            raise PlatformError(f"Access denied ({response.status_code}). Check permissions and reconnect.")
        try:
            details = response.json().get("error", {})
            code = details.get("code") if isinstance(details, dict) else None
        except ValueError:
            code = None
        if code == 190:
            raise PlatformError("Meta login expired or was revoked. Reconnect the Page token.")
        raise PlatformError(f"Provider returned HTTP {response.status_code}" + (f" (code {code})." if code else "."))
    return response.json()


def youtube_token():
    raw = secret("youtube")
    if not raw:
        raise PlatformError("Connect YouTube using the Windows setup command.")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    credentials = Credentials.from_authorized_user_info(json.loads(raw))
    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except Exception:
            raise PlatformError("YouTube login could not refresh. Check connectivity or reconnect.") from None
        keyring.set_password(SERVICE, "youtube", credentials.to_json())
    return credentials.token


def account_token(platform, account):
    if not account or account != C.get(ACCOUNTS[platform]):
        raise PlatformError("Destination account has changed or is missing. Review configuration.")
    if platform == "youtube":
        token = youtube_token()
        result = request("GET", f"{YT}/channels", token, params={"part": "id", "mine": "true"})
        valid = any(item["id"] == account for item in result["items"])
    else:
        token = secret("meta")
        if not token:
            raise PlatformError("Connect the Meta Page token using the Windows setup command.")
        result = request("GET", f"{GRAPH}/me", token, params={"fields": "id,instagram_business_account"})
        valid = result["id"] == C.get("facebook_page") and (
            platform == "facebook" or (result.get("instagram_business_account") or {}).get("id") == account)
    if not valid:
        raise PlatformError("The signed-in account differs from the configured destination.")
    return token


def releases(backfill=False):
    channel = C.get("youtube_channel")
    token = account_token("youtube", channel)
    playlist = request("GET", f"{YT}/channels", token, params={"part": "contentDetails", "id": channel}
                       )["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    params = {"part": "snippet,contentDetails", "playlistId": playlist, "maxResults": 50}
    while True:
        result = request("GET", f"{YT}/playlistItems", token, params=params)
        ids = [item["contentDetails"]["videoId"] for item in result.get("items", [])]
        batch = request("GET", f"{YT}/videos", token, params={"part": "snippet,status", "id": ",".join(ids)}) if ids else {}
        with db() as conn:
            for video in batch.get("items", []):
                snippet = video["snippet"]
                if (video["status"].get("privacyStatus") == "public" and snippet["channelId"] == channel
                        and snippet.get("liveBroadcastContent", "none") == "none"):
                    conn.execute(
                        "INSERT INTO releases VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "title=excluded.title,description=excluded.description,published=excluded.published",
                        (video["id"], snippet["title"], snippet["description"], snippet["publishedAt"]))
        if not backfill or not result.get("nextPageToken"):
            return
        params["pageToken"] = result["nextPageToken"]


def save_comment(platform, remote, release, account, author, body, created):
    run("INSERT INTO comments(platform,remote,release,account,author,body,created) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(platform,remote) DO UPDATE SET author=excluded.author, body=excluded.body",
        (platform, remote, release, account, author, body, created))


def comments(platform):
    account = C.get(ACCOUNTS[platform])
    token = account_token(platform, account)
    if platform == "youtube":
        for release in rows("SELECT id FROM releases ORDER BY published DESC LIMIT 25"):
            try:
                data = request("GET", f"{YT}/commentThreads", token, params={
                    "part": "snippet", "videoId": release["id"], "maxResults": 100, "order": "time", "textFormat": "plainText"})
            except PlatformError as exc:
                if "HTTP 404" in str(exc) or "Access denied (403)" in str(exc):
                    continue
                raise
            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]
                s = top["snippet"]
                if (s.get("authorChannelId") or {}).get("value") != account:
                    save_comment(platform, top["id"], release["id"], account, s["authorDisplayName"], s["textDisplay"], s["publishedAt"])
        return
    for post in rows("SELECT * FROM posts WHERE platform=? AND account=? AND state='published' ORDER BY due DESC LIMIT 25",
                     (platform, account)):
        fields = "id,text,username,timestamp,from" if platform == "instagram" else "id,message,from,created_time"
        data = request("GET", f"{GRAPH}/{post['receipt']}/comments", token, params={"fields": fields, "limit": 100})
        for item in data.get("data", []):
            author = item.get("from") or {}
            if author.get("id") != account:
                save_comment(platform, item["id"], post["release"], account, item.get("username") or author.get("name") or "Viewer",
                             item.get("text", item.get("message", "")), item.get("timestamp", item.get("created_time", "")))


def reply(comment, token):
    platform = comment["platform"]
    if platform == "youtube":
        return request("POST", f"{YT}/comments", token, params={"part": "snippet"}, json={
            "snippet": {"parentId": comment["remote"], "textOriginal": comment["reply"]}})["id"]
    edge = "replies" if platform == "instagram" else "comments"
    return request("POST", f"{GRAPH}/{comment['remote']}/{edge}", token, data={"message": comment["reply"]})["id"]


def checkpoint(post, stage, remote):
    run("UPDATE posts SET stage=?,remote=? WHERE id=?", (stage, remote, post["id"]))


def upload(url, token, file):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "rupload.facebook.com":
        raise PlatformError("Unexpected Meta upload endpoint.")
    with file.open("rb") as video:
        response = request("POST", url, headers={"Authorization": f"OAuth {token}", "offset": "0",
                           "file_size": str(file.stat().st_size), "Content-Length": str(file.stat().st_size),
                           "Content-Type": "application/octet-stream"},
                           content=iter(lambda: video.read(1024 * 1024), b""), timeout=180)
    if response.get("success") is False:
        raise PlatformError("Meta reported an unsuccessful media upload.")


def publish(post, token):
    platform, account, caption = post["platform"], post["account"], post["caption"]
    root = f"{GRAPH}/{account}"
    if post["kind"] == "link":
        checkpoint(post, "submitted", "")
        return request("POST", f"{root}/feed", token, data={
            "message": caption, "link": post["target_url"]})["id"]
    file = MEDIA / post["clip"] / "clip.mp4"
    remote, stage = post["remote"], post["stage"]
    if platform == "facebook":
        if not remote:
            result = request("POST", f"{root}/video_reels", token, data={"upload_phase": "start"})
            remote = result["video_id"]
            checkpoint(post, "created", remote)
        if stage != "uploaded":
            upload(f"https://rupload.facebook.com/video-upload/{VERSION}/{remote}", token, file)
            checkpoint(post, "uploaded", remote)
        checkpoint(post, "submitted", remote)
        result = request("POST", f"{root}/video_reels", token, data={
            "upload_phase": "finish", "video_state": "PUBLISHED", "video_id": remote, "description": caption})
        if not result.get("success"):
            raise PlatformError("Facebook did not confirm that it accepted the Reel.")
        return None
    if not remote:
        result = request("POST", f"{root}/media", token, data={
            "media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": "true"})
        remote = result["id"]
        checkpoint(post, "created", remote)
        upload(result.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{VERSION}/{remote}", token, file)
        checkpoint(post, "uploaded", remote)
        return None
    if stage == "created":
        status = request("GET", f"{GRAPH}/{remote}", token, params={"fields": "status_code"})["status_code"]
        if status != "FINISHED":
            raise PlatformError(f"Instagram upload status is {status}. Verify before retrying.")
        checkpoint(post, "uploaded", remote)
    status = request("GET", f"{GRAPH}/{remote}", token, params={"fields": "status_code"})["status_code"]
    if status == "IN_PROGRESS":
        return None
    if status != "FINISHED":
        raise PlatformError(f"Instagram container reported {status}.")
    checkpoint(post, "submitted", remote)
    return request("POST", f"{root}/media_publish", token, data={"creation_id": remote})["id"]


def reconcile(post, token):
    if post["platform"] == "facebook" and post["remote"]:
        result = request("GET", f"{GRAPH}/{post['remote']}", token, params={"fields": "status"})
        status = result.get("status", {})
        phase = status.get("publishing_phase", {}).get("status")
        if phase == "complete" and status.get("video_status") == "ready":
            return post["remote"]
        if phase in {"error", "failed"} or status.get("video_status") == "error":
            raise PlatformError("Facebook reported a processing error. Verify before resubmitting.")
    if post["platform"] == "instagram" and post["remote"]:
        status = request("GET", f"{GRAPH}/{post['remote']}", token, params={"fields": "status_code"})["status_code"]
        if status == "PUBLISHED":
            raise PlatformError("Instagram confirms publication. Record the Reel ID from Meta before closing this job.")
    return None


def permalink(platform, receipt, token):
    if platform == "instagram":
        return request("GET", f"{GRAPH}/{receipt}", token, params={"fields": "permalink"}).get("permalink", "")
    if platform == "facebook":
        try:
            item = request("GET", f"{GRAPH}/{receipt}", token, params={"fields": "permalink_url"})
            return item.get("permalink_url", "")
        except PlatformError:
            return f"https://www.facebook.com/{receipt}"
    return ""


def analytics():
    token = account_token("youtube", C.get("youtube_channel"))
    end = date.today() - timedelta(days=1)
    params = {"ids": "channel==MINE", "startDate": str(end - timedelta(days=6)), "endDate": str(end)}
    subs = request("GET", ANALYTICS, token, params={**params, "metrics": "subscribersGained,subscribersLost"})
    traffic = request("GET", ANALYTICS, token, params={**params, "metrics": "views", "dimensions": "insightTrafficSourceType"})
    return {"start": params["startDate"], "end": str(end), "subscribers": (subs.get("rows") or [[]])[0],
            "external_views": next((row[1] for row in traffic.get("rows", []) if row[0] == "EXT_URL"), None)}
