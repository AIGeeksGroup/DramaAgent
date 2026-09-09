"""FFmpeg composition, timing, sampling and speaker-specific local TTS."""
import json
from pathlib import Path
import shutil
import subprocess


def run(args, timeout=600):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-2500:]}")
    return result.stdout


def check_tools():
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise RuntimeError(f"{name} is required; install FFmpeg and put it on PATH")


def ffmpeg(*args):
    return run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args])


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))


def duration(path):
    value = float(probe(path)["format"]["duration"])
    if value <= 0:
        raise ValueError(f"Invalid media duration: {path}")
    return value


def assert_stream(path, kind):
    info = probe(path)
    if not any(s.get("codec_type") == kind for s in info["streams"]):
        raise ValueError(f"No {kind} stream in {path}")


def sample_frames(path, target, count=4):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    length = duration(path)
    frames = []
    for i in range(count):
        output = target / f"{i:02d}.jpg"
        ffmpeg("-ss", str(length * (i + 0.5) / count), "-i", path,
               "-frames:v", "1", "-vf", "scale=640:-2", output)
        frames.append(str(output.resolve()))
    return frames


def _tempo(factor):
    filters = []
    while factor > 2:
        filters.append("atempo=2")
        factor /= 2
    while factor < .5:
        filters.append("atempo=0.5")
        factor /= .5
    return ",".join(filters + [f"atempo={factor:.8f}"])


def local_audio(scene, characters, output, options):
    """Native speech with explicit voices, and optionally retrieved ambience/music assets."""
    output = Path(output)
    directory = output.parent / (output.stem + "_tracks")
    directory.mkdir(parents=True, exist_ok=True)
    tracks = []
    commands = options.get("speech_command")
    for index, line in enumerate(scene["dialogue"]):
        char = next(c for c in characters if c["id"] == line["speaker"])
        voice = options.get("voices", {}).get(char["id"], char.get("voice"))
        if not voice:
            raise ValueError(f"Configure options.voices.{char['id']} for local speech")
        raw = directory / f"{index:03d}.aiff"
        if commands:
            # Token substitution, not shell execution. Text may contain arbitrary punctuation.
            args = [token.replace("{text}", line["text"]).replace("{voice}", voice).replace("{output}", str(raw)) for token in commands]
            run(args)
        elif shutil.which("say"):
            run(["say", "-v", voice, "-o", raw, "--", line["text"]])
        else:
            raise ValueError("Configure speech_command or use an audio command backend; macOS say is unavailable")
        available = line["end"] - line["start"]
        speed = max(1, duration(raw) / available)
        if speed > 1.5:
            raise ValueError(f"Dialogue {index} needs {speed:.2f}x speech speed; shorten text or widen timing")
        fitted = directory / f"{index:03d}.wav"
        ffmpeg("-i", raw, "-af", f"{_tempo(speed)},apad,atrim=duration={available}",
               "-ar", "48000", "-ac", "2", fitted)
        tracks.append((fitted, line["start"], 1.0, False))
    for kind in ("ambience", "music"):
        if scene.get(kind):
            asset = options.get(f"{kind}_assets", {}).get(scene["id"])
            if not asset:
                raise ValueError(f"Scene requests {kind}; configure {kind}_assets.{scene['id']} or use an audio backend")
            tracks.append((Path(asset).resolve(), 0, .20 if kind == "ambience" else .12, True))
    args = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={scene['duration']}"]
    filters = []
    labels = ["[0:a]"]
    for index, (path, start, gain, loop) in enumerate(tracks, 1):
        if loop:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(path)]
        delay = round(start * 1000)
        filters.append(f"[{index}:a]aresample=48000,volume={gain},adelay={delay}|{delay}[a{index}]")
        labels.append(f"[a{index}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95[a]")
    ffmpeg(*args, "-filter_complex", ";".join(filters), "-map", "[a]", "-t", scene["duration"], output)
    return str(output.resolve())


def compose(story, selected, target, config):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    normalized = []
    for index, (scene, candidate) in enumerate(zip(story.scenes, selected)):
        output = target / f"segment_{index:04d}.mp4"
        fade = min(config.transition_seconds, scene.duration / 4)
        video_filter = (f"scale={config.width}:{config.height}:force_original_aspect_ratio=decrease,"
                        f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={config.fps},"
                        f"tpad=stop_mode=clone:stop_duration={scene.duration},trim=duration={scene.duration},setpts=PTS-STARTPTS")
        audio_filter = f"apad,atrim=duration={scene.duration},asetpts=PTS-STARTPTS"
        if fade:
            video_filter += f",fade=t=in:d={fade},fade=t=out:st={scene.duration-fade}:d={fade}"
            audio_filter += f",afade=t=in:d={fade},afade=t=out:st={scene.duration-fade}:d={fade}"
        ffmpeg("-i", candidate["video"], "-i", candidate["audio"], "-map", "0:v:0", "-map", "1:a:0",
               "-vf", video_filter, "-af", audio_filter, "-t", scene.duration,
               "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-ar", "48000", "-ac", "2", "-video_track_timescale", "12288", output)
        normalized.append(output)
    listing = target / "concat.txt"
    # Use controlled relative names; never interpolate story titles into concat syntax.
    listing.write_text("".join(f"file '{p.name}'\n" for p in normalized))
    output = target / "final.mp4"
    ffmpeg("-f", "concat", "-safe", "1", "-i", listing, "-c", "copy", "-movflags", "+faststart", output)
    write_subtitles(story, target / "subtitles.srt")
    return str(output.resolve())


def write_subtitles(story, path):
    def stamp(seconds):
        ms = round(seconds * 1000)
        hours, ms = divmod(ms, 3600000)
        minutes, ms = divmod(ms, 60000)
        seconds, ms = divmod(ms, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"
    names = {c.id: c.name for c in story.characters}
    offset, rows = 0, []
    for scene in story.scenes:
        for line in scene.dialogue:
            rows.append(f"{len(rows)+1}\n{stamp(offset+line.start)} --> {stamp(offset+line.end)}\n{names[line.speaker]}: {line.text}\n")
        offset += scene.duration
    Path(path).write_text("\n".join(rows), encoding="utf-8")
