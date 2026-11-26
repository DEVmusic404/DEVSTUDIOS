import json
import subprocess
import tempfile
from pathlib import Path

from .store import C, MEDIA, source_path

_whisper = None


def transcribe(path):
    from faster_whisper import WhisperModel

    global _whisper
    if _whisper is None:
        device = C.get("whisper_device", "cpu")
        _whisper = WhisperModel(C.get("whisper_model", "small"), device=device,
                                compute_type="int8" if device == "cpu" else "float16")
    segments, _ = _whisper.transcribe(str(path), vad_filter=True, beam_size=3)
    return [(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


def srt_time(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def ffmpeg(args, directory):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                            cwd=directory, capture_output=True, timeout=1800)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace")[-800:] or "FFmpeg failed.")


def inspect(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(path)], capture_output=True, check=True, timeout=30)
    report = json.loads(result.stdout)
    return float(report["format"]["duration"]), any(s["codec_type"] == "audio" for s in report["streams"])


def render(clip):
    source = source_path(clip["source"])
    duration, audio = inspect(source)
    start, length = clip["start"], clip["duration"]
    if start + length > duration + 0.1:
        raise ValueError(f"The requested cut ends after the {duration:.1f}-second master.")
    location = MEDIA / clip["id"]
    location.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=location) as temp:
        work = Path(temp)
        cut = ["-ss", str(start), "-i", str(source), "-t", str(length)]
        segments = []
        if audio:
            ffmpeg([*cut, "-vn", "-ac", "1", "-ar", "16000", "audio.wav"], work)
            segments = transcribe(work / "audio.wav")
        srt = "\n\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}" for i, (a, b, text) in enumerate(segments, 1))
        (work / "captions.srt").write_text(srt, encoding="utf-8")
        filters = (f"scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,"
                   f"crop=1080:1920:(iw-ow)*{clip['crop']}:(ih-oh)/2,setsar=1")
        if segments:
            filters += ",subtitles=captions.srt:force_style='FontSize=18,Outline=2,MarginV=40,Alignment=2'"
        ffmpeg([*cut, "-map", "0:v:0", "-map", "0:a:0?", "-vf", filters, "-r", "30",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "clip.mp4"], work)
        (work / "captions.srt").replace(location / "captions.srt")
        (work / "clip.mp4").replace(location / "clip.mp4")
    return " ".join(text for _, _, text in segments)
