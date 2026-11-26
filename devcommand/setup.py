"""Local setup and dependency checks. Credentials are managed by connect.py."""

import argparse
import importlib.util
import re
import shutil
from pathlib import Path

import httpx
import tomli_w

from .store import C, CONFIG, MASTER


def ollama_check():
    model = C.get("ollama_model")
    if not model:
        return False, "Optional. Enter the model name in setup to enable AI drafts."
    try:
        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.0)
        response.raise_for_status()
        names = {item["name"] for item in response.json()["models"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False, "Start Ollama at 127.0.0.1:11434 and refresh."
    if model in names or f"{model}:latest" in names:
        return True, model
    return False, f"Run ollama pull {model} to install the configured model."


def local_checks():
    return [
        ("Master folder", MASTER.is_dir(), str(MASTER)),
        ("FFmpeg and FFprobe", bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
         "Install FFmpeg and put both commands on PATH."),
        ("Whisper", importlib.util.find_spec("faster_whisper") is not None,
         "Install requirements-media.txt for transcription."),
        ("Ollama model", *ollama_check()),
    ]


def update_config(**values):
    # Write in the same directory so replacement stays atomic on Windows.
    current = {**C, **values}
    temp = CONFIG.with_suffix(".toml.tmp")
    temp.write_bytes(tomli_w.dumps(current).encode("utf-8"))
    temp.replace(CONFIG)
    C.update(values)


def configure():
    print("\nRelay / Local studio setup")
    print("Press Enter to keep the displayed value.")
    name = input(f"Master folder [{MASTER}]: ").strip().strip('"')
    source = Path(name).expanduser().resolve() if name else MASTER
    source.mkdir(parents=True, exist_ok=True)
    model = input(f"Ollama model [{C.get('ollama_model') or 'optional'}]: ").strip()
    graph_version = input(f"Meta Graph version [{C.get('graph_version', 'v25.0')}]: ").strip()
    values = {"master_folder": str(source)}
    if model:
        values["ollama_model"] = model
    if graph_version:
        if not re.fullmatch(r"v\d+\.\d+", graph_version):
            raise ValueError("Use a Meta Graph version such as v25.0.")
        values["graph_version"] = graph_version
    update_config(**values)
    print(f"Saved local settings to {CONFIG}.")


def main():
    parser = argparse.ArgumentParser(description="Configure DEV Command or inspect local prerequisites.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        for name, ready, details in local_checks():
            print(f"{name} — {'Ready' if ready else 'Check setup'}. {details}")
        return
    try:
        configure()
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Setup: {exc}\n")


if __name__ == "__main__":
    main()
