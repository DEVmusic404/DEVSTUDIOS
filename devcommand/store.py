import os
import sqlite3
import tomllib
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.getenv("DEV_COMMAND_DATA", Path(os.getenv("LOCALAPPDATA", Path.home() / ".local/share")) / "DEV Command")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
MEDIA = ROOT / "media"
MEDIA.mkdir(exist_ok=True)
CONFIG = ROOT / "config.toml"
C = tomllib.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
MASTER = Path(C.get("master_folder", Path.home() / "Videos/DEV Studios/Ready to Promote")).expanduser().resolve()
ACCOUNTS = {"facebook": "facebook_page", "instagram": "instagram_account", "youtube": "youtube_channel"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, published TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, release TEXT NOT NULL REFERENCES releases(id), source TEXT NOT NULL,
 start REAL NOT NULL, duration REAL NOT NULL, crop REAL NOT NULL, hook TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', error TEXT DEFAULT '', transcript TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS posts (
 id TEXT PRIMARY KEY, release TEXT NOT NULL REFERENCES releases(id), platform TEXT NOT NULL,
 account TEXT NOT NULL, kind TEXT NOT NULL, target_url TEXT DEFAULT '', clip TEXT REFERENCES clips(id), caption TEXT NOT NULL,
 due INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'draft', remote TEXT DEFAULT '',
 stage TEXT DEFAULT '', link TEXT DEFAULT '', receipt TEXT DEFAULT '', error TEXT DEFAULT '',
 metrics TEXT DEFAULT '', metrics_at INTEGER DEFAULT 0, approved_hash TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_destination_draft ON posts(release,platform,kind,ifnull(clip,''));
CREATE TABLE IF NOT EXISTS comments (
 platform TEXT NOT NULL, remote TEXT NOT NULL, release TEXT NOT NULL REFERENCES releases(id),
 account TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL,
 reply TEXT DEFAULT '', state TEXT DEFAULT 'new', receipt TEXT DEFAULT '', error TEXT DEFAULT '',
 PRIMARY KEY(platform, remote)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, draft TEXT NOT NULL
);
"""


@contextmanager
def db():
    connection = sqlite3.connect(ROOT / "command.sqlite", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)


def rows(sql, args=()):
    with db() as connection:
        return connection.execute(sql, args).fetchall()


def run(sql, args=()):
    with db() as connection:
        return connection.execute(sql, args).rowcount


def setting(key, default=""):
    result = rows("SELECT value FROM settings WHERE key=?", (key,))
    return result[0]["value"] if result else default


def save_setting(key, value):
    run("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def source_path(name):
    candidate = (MASTER / name).resolve()
    if candidate.parent != MASTER or candidate.suffix.lower() not in {".mp4", ".mov", ".mkv", ".m4v", ".webm"} or not candidate.is_file():
        raise ValueError("Choose a video in the master folder.")
    return candidate


def masters():
    return sorted(p.name for p in MASTER.glob("*") if p.is_file() and p.resolve().parent == MASTER
                  and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".m4v", ".webm"})
