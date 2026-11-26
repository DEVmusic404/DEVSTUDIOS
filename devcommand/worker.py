import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import creative, media, platforms
from .store import ACCOUNTS, C, MEDIA, initialize, rows, run, save_setting, setting

def clip_hash(clip):
    with (MEDIA / clip / "clip.mp4").open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def media_tick():
    if setting("media_paused") == "1":
        return
    pending = setting("cut_request")
    if pending and run("DELETE FROM settings WHERE key='cut_request' AND value=?", (pending,)):
        task = json.loads(pending)
        release = rows("SELECT * FROM releases WHERE id=?", (task["release"],))[0]
        try:
            result = creative.suggest_cut(release, task["source"])
            result["source"] = task["source"]
        except Exception as exc:
            result = {"status": safe_error(exc)}
        save_setting("cut:" + task["release"], json.dumps(result))
    jobs = rows("SELECT * FROM clips WHERE state='queued' ORDER BY rowid LIMIT 1")
    for job in jobs:
        if not run("UPDATE clips SET state='rendering',error='' WHERE id=? AND state='queued'", (job["id"],)):
            continue
        try:
            transcript = media.render(job)
            run("UPDATE clips SET state='ready',transcript=? WHERE id=?", (transcript, job["id"]))
        except Exception as exc:
            run("UPDATE clips SET state='failed',error=? WHERE id=?", (str(exc)[:1000], job["id"]))


def synchronize(backfill=False):
    operations = []
    if C.get("youtube_channel"):
        operations.append(("YouTube releases", lambda: platforms.releases(backfill)))
    for platform, key in ACCOUNTS.items():
        if C.get(key):
            operations.append((f"{platform} comments", lambda platform=platform: platforms.comments(platform)))
    if C.get("youtube_channel") and int(setting("analytics_at", "0")) < time.time() - 21600:
        def refresh_analytics():
            save_setting("analytics", json.dumps(platforms.analytics()))
            save_setting("analytics_at", int(time.time()))
        operations.append(("YouTube analytics", refresh_analytics))
    operations.append(("Meta engagement", refresh_metrics))
    for name, operation in operations:
        try:
            operation()
            save_setting(f"health:{name}", "Connected")
        except Exception as exc:
            save_setting(f"health:{name}", str(exc) if isinstance(exc, platforms.PlatformError) else type(exc).__name__)


def refresh_metrics():
    for post in rows("SELECT * FROM posts WHERE state='published' AND metrics_at<? ORDER BY metrics_at LIMIT 15",
                     (int(time.time()) - 21600,)):
        token = platforms.account_token(post["platform"], post["account"])
        root = f"{platforms.GRAPH}/{post['receipt']}"
        if post["platform"] == "instagram":
            result = platforms.request("GET", f"{root}/insights", token, params={"metric": "shares,saved,comments"})
            metrics = {m["name"]: m["values"][0]["value"] for m in result.get("data", []) if m.get("values")}
        else:
            result = platforms.request("GET", root, token, params={"fields": "shares,comments.limit(0).summary(true)"}
                                       if post["kind"] == "link" else {"fields": "comments.limit(0).summary(true)"})
            metrics = {"comments": result.get("comments", {}).get("summary", {}).get("total_count")}
            if "shares" in result:
                metrics["shares"] = result["shares"].get("count")
        run("UPDATE posts SET metrics=?,metrics_at=? WHERE id=?", (json.dumps(metrics), int(time.time()), post["id"]))


def mark_published(post, receipt, token):
    run("UPDATE posts SET receipt=?,state='published',error='' WHERE id=?", (receipt, post["id"]))
    try:
        link = platforms.permalink(post["platform"], receipt, token)
        run("UPDATE posts SET link=? WHERE id=?", (link, post["id"]))
    except platforms.PlatformError:
        pass


def reconcile(post):
    if not post["remote"]:
        run("UPDATE posts SET state='verify' WHERE id=?", (post["id"],))
        return
    token = platforms.account_token(post["platform"], post["account"])
    receipt = platforms.reconcile(post, token)
    if receipt:
        mark_published(post, receipt, token)
    elif post["platform"] == "instagram" or time.time() > post["due"] + 3600:
        run("UPDATE posts SET state='verify',error='Check the platform for a live post before closing this job.' WHERE id=?",
            (post["id"],))


def outbound_tick():
    now = int(time.time())
    if now < int(setting("outbound_retry", "0")):
        return
    cutoff = now - C.get("stale_hours", 12) * 3600
    run("UPDATE posts SET state='draft',stage='',remote='',error='Schedule expired while the worker was away. Review and reschedule.' "
        "WHERE state='approved' AND stage!='submitted' AND due<?", (cutoff,))
    for post in rows("SELECT * FROM posts WHERE (state='approved' AND due<=?) OR state='checking' ORDER BY due", (now,)):
        if post["state"] == "checking":
            try:
                reconcile(post)
            except platforms.ConnectionInterrupted:
                save_setting("outbound_retry", now + 60)
                return
            except Exception as exc:
                run("UPDATE posts SET state='verify',error=? WHERE id=?", (safe_error(exc), post["id"]))
            continue
        if not run("UPDATE posts SET state='sending' WHERE id=? AND state='approved' AND due<=?", (post["id"], now)):
            continue
        post = rows("SELECT * FROM posts WHERE id=?", (post["id"],))[0]
        try:
            if post["clip"] and clip_hash(post["clip"]) != post["approved_hash"]:
                raise ValueError("The approved clip changed or is unavailable.")
            token = platforms.account_token(post["platform"], post["account"])
            receipt = platforms.publish(post, token)
            if receipt:
                mark_published(post, receipt, token)
            else:
                run("UPDATE posts SET state=CASE WHEN stage='submitted' THEN 'checking' ELSE 'approved' END WHERE id=?",
                    (post["id"],))
        except platforms.ConnectionInterrupted as exc:
            run("UPDATE posts SET state=CASE WHEN stage='submitted' THEN 'checking' ELSE 'approved' END,error=? WHERE id=?",
                (safe_error(exc), post["id"]))
            save_setting("outbound_retry", now + 60)
            return
        except Exception as exc:
            run("UPDATE posts SET state=CASE WHEN stage='submitted' THEN 'checking' ELSE 'failed' END,error=? WHERE id=?",
                (safe_error(exc), post["id"]))
    for comment in rows("SELECT * FROM comments WHERE state='approved'"):
        key = (comment["platform"], comment["remote"])
        try:
            token = platforms.account_token(comment["platform"], comment["account"])
        except platforms.ConnectionInterrupted:
            save_setting("outbound_retry", now + 60)
            return
        except Exception as exc:
            run("UPDATE comments SET state='draft',error=? WHERE platform=? AND remote=? AND state='approved'",
                (safe_error(exc), *key))
            continue
        if not run("UPDATE comments SET state='sending' WHERE platform=? AND remote=? AND state='approved'", key):
            continue
        comment = rows("SELECT * FROM comments WHERE platform=? AND remote=?", key)[0]
        try:
            receipt = platforms.reply(comment, token)
            run("UPDATE comments SET state='sent',receipt=?,error='' WHERE platform=? AND remote=?", (receipt, *key))
        except Exception as exc:
            run("UPDATE comments SET state='verify',error=? WHERE platform=? AND remote=?", (safe_error(exc), *key))


def safe_error(exc):
    return str(exc) if isinstance(exc, (platforms.PlatformError, ValueError)) else type(exc).__name__


def recover():
    run("UPDATE clips SET state='queued' WHERE state='rendering'")
    run("UPDATE posts SET state=CASE WHEN stage='submitted' THEN 'checking' ELSE 'approved' END WHERE state='sending'")
    run("UPDATE comments SET state='verify',error='Check the platform for this reply before closing the job.' WHERE state='sending'")


def main():
    initialize()
    guard = (MEDIA.parent / "worker.lock").open("a+b")
    if os.name == "nt":
        import msvcrt
        guard.seek(0)
        msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    recover()
    next_poll = 0
    with guard, ThreadPoolExecutor(max_workers=1) as render, ThreadPoolExecutor(max_workers=1) as sync:
        media_job = sync_job = None
        while True:
            save_setting("worker_seen", int(time.time()))
            if media_job is None or media_job.done():
                media_job = render.submit(media_tick)
            pending = setting("sync")
            if (sync_job is None or sync_job.done()) and (pending or time.monotonic() >= next_poll):
                run("DELETE FROM settings WHERE key='sync' AND value=?", (pending,))
                sync_job = sync.submit(synchronize, pending == "backfill")
                next_poll = time.monotonic() + C.get("poll_seconds", 300)
            outbound_tick()
            time.sleep(5)


if __name__ == "__main__":
    main()
