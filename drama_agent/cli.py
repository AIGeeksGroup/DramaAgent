import argparse
import json
from pathlib import Path
import sys
from .backends import create_backend
from .config import Config
from .pipeline import DramaAgent
from .schema import Story
from .storage import read_json, write_json


def load_source(path):
    path = Path(path).resolve()
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix.lower() == ".json" else raw
    if isinstance(data, dict) and "scenes" in data:
        for character in data.get("characters", []):
            if character.get("reference"):
                reference = Path(character["reference"])
                character["reference"] = str((path.parent / reference).resolve())
        Story.from_dict(data)
    elif not isinstance(data, (str, dict)):
        raise ValueError("Input must be text, a MA2 synopsis object, or a structured story")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="DramaAgent: story planning, persistent characters and targeted repair")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "plan", "validate"):
        command = sub.add_parser(name)
        command.add_argument("--input", required=True, help="Text, MA2 MovieScript/Character JSON, or structured story JSON")
        if name != "validate":
            command.add_argument("--config", help="Configuration JSON; defaults to synthetic demo")
            command.add_argument("--output", required=True)
        if name == "run":
            command.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        source = load_source(args.input)
        if args.command == "validate":
            story = Story.from_dict(source)
            print(f"Valid: {len(story.characters)} characters, {len(story.scenes)} scenes, {sum(s.duration for s in story.scenes):g}s")
            return 0
        config = Config.load(args.config)
        backend = create_backend(config)
        if config.backend == "demo":
            print("DEMO MODE: synthetic video, test-tone audio and fixture scores; no AI story generation.")
        if args.command == "plan":
            output = Path(args.output).resolve()
            if output.exists():
                raise ValueError("Plan output already exists; choose another path")
            planned = source if isinstance(source, dict) and "scenes" in source else backend.plan(source, output.parent / (output.stem + "_planning"))
            story = Story.from_dict(planned)
            write_json(output, story.to_dict())
            print(output)
        else:
            state = DramaAgent(config, backend).run(source, args.output, args.resume)
            print(f"Video: {state['final_video']}")
            print(f"Subtitles: {state['subtitles']}")
            if state["below_threshold_scenes"]:
                print("Below threshold: " + ", ".join(state["below_threshold_scenes"]))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(f"DramaAgent error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
