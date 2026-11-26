import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

temp = tempfile.TemporaryDirectory()
os.environ["DEV_COMMAND_DATA"] = temp.name

from devcommand import media, platforms, store, worker
from devcommand.app import app, TZ
from datetime import datetime


class CampaignTests(unittest.TestCase):
    def setUp(self):
        store.initialize()
        with store.db() as conn:
            for table in ("comments", "posts", "clips", "releases", "settings"):
                conn.execute(f"DELETE FROM {table}")
        self.release = "A_Release01"
        store.run("INSERT INTO releases VALUES(?,?,?,?)", (self.release, "DEVPOOL Test", "Original release", "2026-11-25"))
        self.client = TestClient(app, base_url="http://127.0.0.1:8765")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Origin": "http://127.0.0.1:8765"}

    def submit(self, action, **params):
        return self.client.post("/action", headers=self.headers, follow_redirects=False,
                                data={"release": self.release, "action": action, **params})

    def get_post(self):
        return store.rows("SELECT * FROM posts")[0]

    def test_link_campaign_approval_and_receipt(self):
        with patch.dict(store.C, {"facebook_page": "123"}):
            self.assertEqual(self.submit("drafts").status_code, 303)
            self.submit("drafts")
            post = self.get_post()
            self.assertEqual(len(store.rows("SELECT id FROM posts")), 1)
            self.assertIn("Facebook link", self.client.get("/").text)
            self.assertEqual(self.client.post("/action", data={"action": "pause"}).status_code, 403)
            due = datetime.fromtimestamp(time.time() + 60, TZ).strftime("%Y-%m-%dT%H:%M")
            result = self.submit("approve_posts", posts=post["id"], **{
                f"caption_{post['id']}": "See the full episode", f"due_{post['id']}": due})
            self.assertNotIn("error=", result.headers["location"])
            self.assertEqual(self.get_post()["state"], "approved")
            store.run("UPDATE posts SET due=?", (int(time.time()) - 1,))
            calls = []

            def fake_request(method, url, token, **kwargs):
                calls.append((method, url))
                return {"id": "123_456", "permalink_url": "https://www.facebook.com/123_456"}

            with patch.object(platforms, "account_token", return_value="token"), patch.object(platforms, "request", fake_request):
                worker.outbound_tick()
                worker.recover()
                worker.outbound_tick()
            self.assertEqual(self.get_post()["receipt"], "123_456")
            self.assertEqual(sum(method == "POST" for method, _ in calls), 1)

    def test_restart_checks_known_reel_without_submitting(self):
        self.submit("drafts")
        store.run("UPDATE posts SET state='sending', kind='reel', stage='submitted', remote='999', account='123'")
        worker.recover()
        with patch.object(platforms, "account_token", return_value="token"), \
             patch.object(platforms, "reconcile", return_value="999") as status, \
             patch.object(platforms, "permalink", return_value=""), \
             patch.object(platforms, "publish") as publish:
            worker.outbound_tick()
            status.assert_called_once()
            publish.assert_not_called()
        self.assertEqual(self.get_post()["receipt"], "999")

    def test_ambiguous_feed_submission_requires_verification(self):
        self.submit("drafts")
        store.run("UPDATE posts SET state='sending', stage='submitted'")
        worker.recover()
        with patch.object(platforms, "publish") as publish:
            worker.outbound_tick()
            publish.assert_not_called()
        self.assertEqual(self.get_post()["state"], "verify")

    def test_instagram_reel_checks_processing_before_publication(self):
        clip_id = "clip"
        store.run("INSERT INTO clips(id,release,source,start,duration,crop,hook,state) VALUES(?,?,?,?,?,?,?,?)",
                  (clip_id, self.release, "master.mp4", 0, 20, 0.5, "Hook", "ready"))
        self.submit("drafts", id=clip_id)
        post = store.rows("SELECT * FROM posts WHERE platform='instagram'")[0]
        store.run("UPDATE posts SET state='approved',due=?,account='123',approved_hash='hash' WHERE id=?",
                  (int(time.time()) - 1, post["id"]))
        with patch.object(platforms, "account_token", return_value="token"), \
             patch.object(platforms, "upload") as upload, \
             patch.object(worker, "clip_hash", return_value="hash"), \
             patch.object(platforms, "request") as api:
            api.return_value = {"id": "container", "uri": "https://rupload.facebook.com/path"}
            worker.outbound_tick()
            upload.assert_called_once()
            self.assertEqual(api.call_args.args[1].rsplit("/", 1)[-1], "media")
            api.return_value = {"status_code": "IN_PROGRESS"}
            worker.outbound_tick()
            self.assertEqual(api.call_args.args[0], "GET")
            api.side_effect = [{"status_code": "FINISHED"}, {"id": "reel"},
                               {"permalink": "https://www.instagram.com/reel/example/"}]
            worker.outbound_tick()
        result = store.rows("SELECT * FROM posts WHERE id=?", (post["id"],))[0]
        self.assertEqual(result["receipt"], "reel")
        self.assertEqual(result["state"], "published")

    def test_import_and_reply_from_inbox(self):
        with patch.dict(store.C, {"youtube_channel": "studio"}), \
             patch.object(platforms, "account_token", return_value="token"), \
             patch.object(platforms, "request") as api:
            api.return_value = {"items": [{"snippet": {"topLevelComment": {"id": "abc", "snippet": {
                "authorDisplayName": "Viewer", "textDisplay": "Loved it", "publishedAt": "2026-11-25"}}}}]}
            platforms.comments("youtube")
            self.assertIn("Loved it", self.client.get("/?view=inbox").text)
            self.submit("reply_approve", id="abc", platform="youtube", reply="Thanks for watching!")
            api.return_value = {"id": "reply123"}
            worker.outbound_tick()
            self.assertEqual(api.call_args.kwargs["json"]["snippet"]["parentId"], "abc")
        self.assertEqual(store.rows("SELECT receipt FROM comments")[0]["receipt"], "reply123")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_vertical_render_and_tiktok_export(self):
        with tempfile.TemporaryDirectory() as directory:
            master = store.Path(directory) / "master.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=320x180:r=24",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "5", "-c:v", "libx264",
                            "-c:a", "aac", str(master)], check=True)
            with patch.object(store, "MASTER", master.parent), patch.object(media, "transcribe", return_value=[(0, 2, "Hello")]):
                self.submit("render", source=master.name, start="0", duration="5", crop="0.5", hook="A hook")
                worker.media_tick()
        clip = store.rows("SELECT * FROM clips")[0]
        self.assertEqual(clip["state"], "ready", clip["error"])
        self.assertIn(b"Hello", self.client.get(f"/clips/{clip['id']}/captions.srt").content)
        self.submit("drafts", id=clip["id"])
        tiktok = store.rows("SELECT * FROM posts WHERE platform='tiktok'")[0]
        self.submit("approve_posts", posts=tiktok["id"], **{
            f"caption_{tiktok['id']}": "Watch this", f"due_{tiktok['id']}": "2026-11-25T20:00"})
        self.assertEqual(self.client.get(f"/export/{tiktok['id']}/caption.txt").text, "Watch this")
        self.assertEqual(self.client.get(f"/export/{tiktok['id']}/clip.mp4").status_code, 200)


if __name__ == "__main__":
    unittest.main()
