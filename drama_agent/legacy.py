"""Convert MA2 Step_3_shot_results.json without loading any GPU dependencies."""
import re
from .schema import Story


def natural_key(value):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", value)]


def import_shots(data, characters, title="Imported MA2 story", duration=5):
    """Preserve numeric sub-script/scene/shot order and convert speaker-name dictionaries.

    `characters` is an explicit canonical sheet; never invent appearances from names.
    Old subtitle dictionaries have no timestamps, so allocate equal non-overlapping slots.
    These initial timings should be reviewed before paid generation.
    """
    names = {c["name"]: c["id"] for c in characters}
    scenes, plots = [], []
    for sub_name in sorted(data["Sub-Script"], key=natural_key):
        sub = data["Sub-Script"][sub_name]
        plots.append(sub["Plot"])
        annotated = sub["Scene Annotation"]["Scene"]
        for scene_name in sorted(annotated, key=natural_key):
            parent = annotated[scene_name]
            shots = parent["Shot Annotation"]["Shot"]
            for shot_name in sorted(shots, key=natural_key):
                shot = shots[shot_name]
                involved = list(shot["Involving Characters"])
                lines = [(n, t) for n, t in shot.get("Subtitles", {}).items() if t and t.strip()]
                # Some old annotations omit an off-screen speaker from the character boxes.
                involved = list(dict.fromkeys(involved + [n for n, _ in lines]))
                missing = set(involved) - set(names)
                if missing:
                    raise ValueError(f"Character sheet missing MA2 names: {', '.join(sorted(missing))}")
                slot = (duration - .4) / max(1, len(lines))
                dialogue = [{"speaker": names[n], "text": t, "start": round(.2 + i*slot, 6),
                             "end": round(.2 + (i+1)*slot, 6)} for i, (n, t) in enumerate(lines)]
                scenes.append({"id": f"s{len(scenes)+1:04d}",
                               "description": shot.get("Plot/Visual Description") or shot["Coarse Plot"],
                               "characters": [names[n] for n in involved],
                               "environment": parent["Scene Description"],
                               "emotion": parent.get("Emotional Tone", "neutral"),
                               "transition": "Continue from the preceding accepted shot" if scenes else "",
                               "duration": duration, "dialogue": dialogue})
    return Story.from_dict({"title": title, "narrative": "\n".join(plots), "characters": characters, "scenes": scenes})
