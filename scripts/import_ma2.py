"""Run from the repository root: python scripts/import_ma2.py --help."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drama_agent.legacy import import_shots
from drama_agent.storage import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="Import MA2 Step_3 shots with an explicit character sheet")
    parser.add_argument("--shots", required=True)
    parser.add_argument("--characters", required=True, help="JSON object with a characters list, or a complete story")
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=5)
    args = parser.parse_args()
    source = Path(args.characters).resolve()
    characters = read_json(source)["characters"]
    for char in characters:
        if char.get("reference"):
            char["reference"] = str((source.parent / char["reference"]).resolve())
    output = Path(args.output)
    if output.exists():
        parser.error("Output exists; choose a new path")
    story = import_shots(read_json(args.shots), characters, duration=args.duration)
    write_json(output, story.to_dict())
    print(f"Imported {len(story.scenes)} shots. Review allocated subtitle timings before generation.")


if __name__ == "__main__":
    main()
