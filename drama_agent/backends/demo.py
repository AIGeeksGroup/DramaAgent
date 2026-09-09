"""Deterministic synthetic test provider, never a real quality evaluator."""
from pathlib import Path
import re
from ..media import ffmpeg


class DemoBackend:
    def __init__(self, config):
        self.config = config
        self.provenance = {"backend": "demo", "synthetic": True}

    def plan(self, source, directory):
        if isinstance(source, dict):
            narrative = source.get("MovieScript", source.get("prompt", ""))
            names = source.get("Character", ["Traveler"])
        else:
            narrative, names = source, ["Traveler"]
        if not isinstance(narrative, str) or not narrative.strip():
            raise ValueError("Input must contain a nonempty prompt or MovieScript")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("MA2 Character must be a list of names")
        characters = [{"id": f"c{i+1}", "name": n, "appearance": f"DEMO placeholder for {n}; blue coat"} for i,n in enumerate(names)]
        beats = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s*|\n+", narrative) if p.strip()]
        return {"title": "DramaAgent pipeline demo", "narrative": narrative, "characters": characters,
                "scenes": [{"id": f"s{i+1:03}", "description": beat, "characters": [c["id"] for c in characters],
                            "environment": "Synthetic test stage", "emotion": "neutral", "duration": 2,
                            "transition": "Continue the preceding event" if i else "", "dialogue": []} for i,beat in enumerate(beats)]}

    def reference(self, character, output):
        ffmpeg("-f", "lavfi", "-i", "color=c=steelblue:s=256x256", "-frames:v", "1", output)
        return str(output)

    def generate(self, request, output):
        c = self.config
        colors = ["0x1e3555", "0x28556b", "0x47617a"]
        color = colors[request["seed"] % len(colors)]
        label = Path(output).parent / "label.txt"
        label.write_text(f"DRAMAAGENT DEMO - SYNTHETIC\n{request['scene']['id']} / repair {request['round']}\nNot generated story footage")
        # Controlled basename avoids quoting arbitrary paths in filter expressions.
        filters = "drawbox=x=mod(t*100\\,iw):y=ih/2:w=100:h=80:color=orange:t=fill"
        # drawtext is optional in FFmpeg builds; a colored moving test pattern remains a demo.
        from ..media import run
        if "drawtext" in run(["ffmpeg", "-hide_banner", "-filters"]):
            escaped = str(label).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
            filters += f",drawtext=textfile='{escaped}':fontcolor=white:fontsize=24:x=20:y=30"
        ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s={c.width}x{c.height}:r={c.fps}",
               "-vf", filters, "-t", request["scene"]["duration"], "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", output)
        return str(output)

    def audio(self, request, output):
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-af", "volume=0.05",
               "-t", request["scene"]["duration"], "-ac", "2", output)
        return str(output)

    def assess(self, request, directory):
        value = min(.94, .60 + request["round"] * .17 + request["seed"] % 3 * .01)
        return {"scores": {"identity": value-.04, "semantic": value, "temporal": value+.01, "audio_visual": value},
                "issues": {"identity": "Synthetic identity failure to exercise repair"},
                "summary": f"Synthetic demo clip for {request['scene']['id']}; no observed story action.",
                "evidence": "SYNTHETIC fixture scores, not measured video quality; audio is a test tone."}
