import json
import math
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import creative, platforms
from .store import ACCOUNTS, C, MASTER, MEDIA, db, initialize, masters, rows, run, save_setting, setting, source_path
from .worker import clip_hash

BASE = Path(__file__).parent
TZ = ZoneInfo(C.get("timezone", "America/Chicago"))


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["localtime"] = lambda ts: datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%dT%H:%M")


@app.middleware("http")
async def local_only(request, call_next):
    if request.url.hostname not in {"127.0.0.1", "localhost"}:
        return Response("Open DEV Command on localhost.", status_code=403)
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin != f"{request.url.scheme}://{request.url.netloc}":
            return Response("Open the local dashboard to submit changes.", status_code=403)
    return await call_next(request)


def one(table, identity):
    result = rows(f"SELECT * FROM {table} WHERE id=?", (identity,))
    if not result:
        raise HTTPException(404)
    return result[0]


def new_id():
    return uuid.uuid4().hex


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cut_fields(data):
    start, duration, crop = (float(data[key]) for key in ("start", "duration", "crop"))
    require(all(map(math.isfinite, (start, duration, crop))) and start >= 0 and 5 <= duration <= 60 and 0 <= crop <= 1,
            "Use a valid start, a duration of 5–60 seconds, and a crop position between 0 and 1.")
    return start, duration, crop


@app.get("/")
def home(request: Request, view: str = "campaigns", release: str = "", error: str = ""):
    catalog = rows("SELECT * FROM releases ORDER BY published DESC")
    selected = one("releases", release) if release else (catalog[0] if catalog else None)
    files = masters()
    suggested = [f for f in files if selected and (selected["id"].lower() in Path(f).stem.lower() or
                 re.sub(r"\W+", "", selected["title"].lower()) == re.sub(r"\W+", "", Path(f).stem.lower()))]
    clips = rows("SELECT * FROM clips WHERE release=? ORDER BY rowid DESC", (selected["id"],)) if selected else []
    posts = rows("SELECT posts.*,releases.title,clips.hook FROM posts JOIN releases ON releases.id=posts.release "
                 "LEFT JOIN clips ON clips.id=posts.clip ORDER BY due DESC")
    inbox = rows("SELECT comments.*, releases.title FROM comments JOIN releases ON releases.id=comments.release "
                 "ORDER BY release, created DESC LIMIT 300")
    grouped = {}
    for comment in inbox:
        grouped.setdefault(comment["title"], []).append(comment)
    return templates.TemplateResponse(request, "index.html", dict(
        view=view, catalog=catalog, selected=selected, clips=clips, posts=posts, inbox=inbox, grouped=grouped,
        files=files, suggested=suggested, folder=MASTER, error=error, accounts={p:C.get(k,"") for p,k in ACCOUNTS.items()},
        paused=setting("media_paused") == "1", worker=time.time() - int(setting("worker_seen", "0")) < 120,
        health=rows("SELECT substr(key,8) AS name,value FROM settings WHERE key LIKE 'health:%'"),
        now=int(time.time()), tz=str(TZ), analytics=json.loads(setting("analytics", "{}")),
        leads=rows("SELECT * FROM leads ORDER BY id DESC"), suggestion=json.loads(setting("cut:" + selected["id"], "{}")) if selected else {}))


@app.get("/clips/{identity}/{file}")
def clip_file(identity: str, file: str, download: bool = False):
    clip = one("clips", identity)
    if clip["state"] != "ready" or file not in {"clip.mp4", "captions.srt"}:
        raise HTTPException(404)
    path = MEDIA / clip["id"] / file
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=f"DEV-{identity[:8]}-{file}" if download else None)


@app.get("/export/{identity}/{file}")
def export(identity: str, file: str):
    post = one("posts", identity)
    if post["state"] != "export_ready":
        raise HTTPException(404)
    if file == "caption.txt":
        return Response(post["caption"], media_type="text/plain", headers={"Content-Disposition": "attachment; filename=caption.txt"})
    if file == "clip.mp4":
        if clip_hash(post["clip"]) != post["approved_hash"]:
            raise HTTPException(409, "The approved clip changed.")
        return clip_file(post["clip"], file, True)
    raise HTTPException(404)


def campaign_link(release):
    original = f"https://www.youtube.com/watch?v={release}"
    configured = C.get("campaign_landing_url", "")
    if not configured:
        return original
    target = urlsplit(configured.replace("{video_id}", release))
    require(target.scheme == "https" and target.hostname, "Set a valid HTTPS campaign landing URL.")
    query = dict(parse_qsl(target.query, keep_blank_values=True))
    query.update(utm_source="facebook", utm_medium="organic_social", utm_campaign=release)
    return urlunsplit(target._replace(query=urlencode(query)))


def make_post(release, platform, kind, clip, caption, due):
    account = "Manual TikTok upload" if platform == "tiktok" else C.get(ACCOUNTS[platform], "")
    run("INSERT INTO posts(id,release,platform,account,kind,target_url,clip,caption,due) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (new_id(), release, platform, account, kind, campaign_link(release) if kind == "link" else "", clip, caption, due))


def drafts(release, clip_id):
    clip = one("clips", clip_id) if clip_id else None
    require(not clip or (clip["release"] == release["id"] and clip["state"] == "ready"), "Choose a ready clip from this release.")
    transcript = clip["transcript"] if clip else ""
    hook = (clip["hook"] if clip else "") or release["title"]
    url = f"https://www.youtube.com/watch?v={release['id']}"
    proposals = {}
    if C.get("ollama_model"):
        proposals = creative.captions(release, transcript, hook)
    variants = [("link", "facebook", "link", None, f"New from DEV Studios — {release['title']}", 0)]
    if clip:
        variants += [
            ("facebook", "facebook", "reel", clip_id, f"{hook}\nWatch the full release: {url}", 2),
            ("instagram", "instagram", "reel", clip_id, f"{hook}\nFind {release['title']} on DEV Studios’ YouTube channel.", 3),
            ("tiktok", "tiktok", "reel", clip_id, f"{hook}\nFull release on YouTube: DEV Studios.", 4),
        ]
    offsets = proposals.get("offsets")
    offsets = offsets if isinstance(offsets, dict) else {}
    for key, platform, kind, media_id, fallback, hours in variants:
        existing = rows("SELECT id FROM posts WHERE release=? AND platform=? AND kind=? AND clip IS ?",
                        (release["id"], platform, kind, media_id))
        if not existing:
            offset = offsets.get(key)
            if isinstance(offset, (int, float)) and math.isfinite(offset) and 0 <= offset <= 72:
                hours = offset
            caption = proposals.get(key)
            make_post(release["id"], platform, kind, media_id,
                      caption.strip()[:2000] if isinstance(caption, str) and caption.strip() else fallback[:2000],
                      int(time.time() + hours * 3600))


def post_updates(form, identity, approve):
    post = one("posts", identity)
    require(post["state"] in {"draft", "failed", "approved"} and not post["stage"],
            "This job has an active or interrupted submission.")
    caption = form[f"caption_{identity}"].strip()
    require(0 < len(caption) <= 2000, "Enter a caption of up to 2,000 characters.")
    local = datetime.fromisoformat(form[f"due_{identity}"])
    require(local.tzinfo is None, "Enter the time shown in the dashboard timezone.")
    due = int(local.replace(tzinfo=TZ).timestamp())
    account = "Manual TikTok upload" if post["platform"] == "tiktok" else C.get(ACCOUNTS[post["platform"]], "")
    fingerprint = ""
    state = "draft"
    if approve:
        require(post["platform"] == "tiktok" or account, "Configure the destination account before approval.")
        if post["clip"]:
            clip = one("clips", post["clip"])
            require(clip["state"] == "ready", "Render the clip before approving.")
            fingerprint = clip_hash(clip["id"])
        if post["platform"] != "tiktok":
            require(due >= time.time() - 120, "Choose a current or future publish time.")
        state = "export_ready" if post["platform"] == "tiktok" else "approved"
    return (caption, due, account, fingerprint, state, identity)


def action(form):
    task, identity, release = form["action"], form.get("id", ""), form.get("release", "")
    if task in {"sync", "backfill"}:
        save_setting("sync", task)
    elif task == "pause":
        save_setting("media_paused", "0" if setting("media_paused") == "1" else "1")
    elif task == "add_lead":
        url = form["url"].strip()
        require(urlsplit(url).scheme == "https" and urlsplit(url).hostname, "Enter an HTTPS creator or conversation URL.")
        run("INSERT INTO leads(title,url,draft) VALUES(?,?,?)", (form["title"][:300], url, form["draft"][:2000]))
    elif task in {"suggest", "render"}:
        item = one("releases", release)
        filename = form["source"]
        source_path(filename)
        if task == "suggest":
            save_setting("cut_request", json.dumps({"release": release, "source": filename}))
            save_setting("cut:" + release, json.dumps({"status": "Finding a hook in " + filename}))
        else:
            start, duration, crop = cut_fields(form)
            run("INSERT INTO clips(id,release,source,start,duration,crop,hook) VALUES(?,?,?,?,?,?,?)",
                (new_id(), item["id"], filename, start, duration, crop, form["hook"][:300]))
    elif task == "drafts":
        drafts(one("releases", release), identity)
    elif task in {"save_posts", "approve_posts"}:
        selected = form.getlist("posts")
        require(selected, "Select at least one destination.")
        require(all(one("posts", job)["release"] == release for job in selected), "Review one campaign at a time.")
        changes = [post_updates(form, job, task == "approve_posts") for job in selected]
        with db() as conn:
            for change in changes:
                changed = conn.execute("UPDATE posts SET caption=?,due=?,account=?,approved_hash=?,state=?,error='' "
                                       "WHERE id=? AND state IN ('draft','failed','approved') AND stage=''", change).rowcount
                require(changed == 1, "A selected job changed while saving. Refresh and review.")
    elif task == "receipt":
        post = one("posts", identity)
        receipt = form["receipt"].strip()
        require(re.fullmatch(r"[\w.-]{1,120}", receipt) is not None, "Enter the confirmed platform ID.")
        link = form["link"].strip()
        require(not link or urlsplit(link).scheme == "https", "Enter an HTTPS post link.")
        run("UPDATE posts SET state='published',receipt=?,link=?,error='' WHERE id=? AND state='verify'",
            (receipt, link, post["id"]))
    elif task == "rearm":
        run("UPDATE posts SET state='draft',remote='',stage='',error='' WHERE id=? AND state IN ('verify','failed')", (identity,))
    elif task in {"reply_draft", "reply_save", "reply_approve", "reply_record"}:
        key = (form["platform"], identity)
        comments = rows("SELECT * FROM comments WHERE platform=? AND remote=?", key)
        require(bool(comments), "This comment is unavailable.")
        comment = comments[0]
        if task == "reply_record":
            receipt = form["receipt"].strip()
            require(bool(receipt), "Enter the confirmed reply ID.")
            run("UPDATE comments SET state='sent',receipt=?,error='' WHERE platform=? AND remote=? AND state='verify'",
                (receipt, *key))
            return
        require(comment["state"] in {"new", "draft"}, "This reply is already queued or was sent.")
        if task == "reply_draft":
            result = creative.ollama("Write one short studio reply. Return a reply key. If answering requires unpublished or unverified facts, offer a neutral acknowledgment.",
                                    {"comment": comment["body"], "release": one("releases", comment["release"])["title"]})
            reply = result["reply"]
        else:
            reply = form.get("reply", "").strip()
        require(isinstance(reply, str) and 0 < len(reply.strip()) <= 1000, "Enter a reply of up to 1,000 characters.")
        state = "approved" if task == "reply_approve" else "draft"
        run("UPDATE comments SET reply=?,state=?,error='' WHERE platform=? AND remote=? AND state IN ('new','draft')",
            (reply, state, *key))
    else:
        raise ValueError("Unknown dashboard action.")


@app.post("/action")
async def submit(request: Request):
    from starlette.concurrency import run_in_threadpool
    from urllib.parse import urlencode

    form = await request.form()
    params = {"view": form.get("view", "campaigns"), "release": form.get("release", "")}
    try:
        await run_in_threadpool(action, form)
    except (ValueError, KeyError, OSError, platforms.PlatformError) as exc:
        params["error"] = str(exc)[:500]
    return RedirectResponse("/?" + urlencode(params), status_code=303)
