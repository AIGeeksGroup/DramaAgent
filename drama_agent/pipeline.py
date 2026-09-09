"""Sequential story state, candidate selection and bounded targeted repair."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import shutil

from . import __version__
from .media import assert_stream, check_tools, compose
from .prompts import scene_prompt
from .schema import Assessment, Story
from .storage import fingerprint, read_json, require_file, run_lock, write_json


class DramaAgent:
    def __init__(self, config, backend, progress=print):
        self.config, self.backend, self.progress = config, backend, progress

    def run(self, source, output, resume=False):
        check_tools()
        output = Path(output).resolve()
        with run_lock(output):
            return self._run(source, output, resume)

    def _run(self, source, output, resume):
        config = self.config
        run_path = output / "run.json"
        digest = fingerprint({"source": source, "config": config.to_dict(), "version": __version__})
        if run_path.exists():
            if not resume:
                raise ValueError("Output already contains a run; use --resume or choose a new directory")
            state = read_json(run_path)
            if state["fingerprint"] != digest:
                raise ValueError("Input/configuration changed; use a new output directory")
        else:
            state = {"fingerprint": digest, "version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
                     "config": config.to_dict(), "provider": self.backend.provenance, "status": "running", "selected": []}
            write_json(run_path, state)
        plan_path = output / "story.json"
        if plan_path.exists():
            story = Story.from_dict(read_json(plan_path))
        else:
            self.progress("Planning story and ordered scenes")
            planned = source if isinstance(source, dict) and "scenes" in source else self.backend.plan(source, output / "planning")
            story = Story.from_dict(planned)
            write_json(plan_path, story.to_dict())
        if hasattr(self.backend, "validate_story"):
            self.backend.validate_story(story)
        # A frozen canonical bank is shared by generation, evaluation, and every repair.
        refs_path = output / "characters.json"
        if refs_path.exists():
            characters = read_json(refs_path)
            for char in characters:
                require_file(char["reference"])
        else:
            characters = []
            for char in story.characters:
                directory = output / "characters" / char.id
                directory.mkdir(parents=True, exist_ok=True)
                canonical = directory / "reference.png"
                record = directory / "character.json"
                if record.exists():
                    entry = read_json(record)
                    require_file(entry["reference"])
                else:
                    entry = asdict(char)
                    if char.reference:
                        source_path = Path(require_file(char.reference))
                        canonical = canonical.with_suffix(source_path.suffix)
                        shutil.copy2(source_path, canonical)
                        entry["reference"] = str(canonical)
                    else:
                        self.progress(f"Creating canonical reference: {char.name}")
                        entry["reference"] = require_file(self.backend.reference(entry, canonical))
                    write_json(record, entry)
                characters.append(entry)
            write_json(refs_path, characters)
        selected, history = [], []
        for scene_index, scene in enumerate(story.scenes):
            scene_data = asdict(scene)
            scene_characters = [c for c in characters if c["id"] in scene.characters]
            prompt = scene_prompt(scene_data, scene_characters, history)
            scene_dir = output / "scenes" / scene.id
            candidates = []
            previous = selected[-1] if selected else None
            for index in range(config.candidates):
                candidates.append(self._candidate(scene_data, scene_characters, history, previous, prompt,
                                                  scene_index, 0, index, scene_dir))
            best = max(candidates, key=lambda item: item["score"])
            stop_reason = "threshold" if best["score"] >= config.threshold else "budget"
            for round_number in range(1, config.repair_rounds + 1):
                if best["score"] >= config.threshold:
                    break
                old_score = best["score"]
                assessment = Assessment(**best["assessment"])
                self.progress(f"{scene.id}: repairing {assessment.weakest}, score={old_score:.3f}")
                prompt = scene_prompt(scene_data, scene_characters, history, assessment, best["prompt"])
                # Paper repair budget: one targeted regeneration per round, not a fresh K-set.
                repaired = self._candidate(scene_data, scene_characters, history, previous, prompt,
                                           scene_index, round_number, 0, scene_dir)
                candidates.append(repaired)
                if repaired["score"] > best["score"]:
                    best = repaired
                if best["score"] >= config.threshold:
                    stop_reason = "threshold"
                    break
                if best["score"] - old_score < config.min_improvement - 1e-12:
                    stop_reason = "min_improvement"
                    break
            accepted = best["score"] >= config.threshold
            selection = {**best, "accepted": accepted, "stop_reason": stop_reason}
            write_json(scene_dir / "selection.json", selection)
            if not accepted and config.low_score_policy == "fail":
                state.update(status="quality_failed", failed_scene=scene.id, selected=selected)
                write_json(run_path, state)
                raise RuntimeError(f"{scene.id} remains below threshold ({best['score']:.3f}); best candidate saved")
            if not accepted:
                self.progress(f"{scene.id}: retaining best below threshold ({best['score']:.3f})")
            selected.append(selection)
            # Observations, not the desired script, drive the accepted story state.
            history.append({"scene_id": scene.id, "observed_summary": best["assessment"]["summary"],
                            "characters": scene.characters, "score": best["score"], "accepted": accepted})
            state.update(status="running", selected=selected, history=history)
            write_json(run_path, state)
        self.progress("Composing selected video, audio and subtitle tracks")
        final = compose(story, selected, output / "composition", config)
        state.update(status="completed", final_video=final,
                     subtitles=str(output / "composition" / "subtitles.srt"),
                     below_threshold_scenes=[s["scene_id"] for s in selected if not s["accepted"]],
                     completed_at=datetime.now(timezone.utc).isoformat())
        write_json(run_path, state)
        return state

    def _candidate(self, scene, characters, history, previous, prompt, scene_index, round_number, index, scene_dir):
        candidate_id = f"r{round_number:02d}_c{index:03d}"
        directory = scene_dir / candidate_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "candidate.json"
        seed = (self.config.seed + scene_index * 10000 + round_number * 100 + index) % 2147483647
        request = {"scene": scene, "characters": characters, "history": history,
                   "previous": previous, "prompt": prompt, "seed": seed, "round": round_number,
                   "width": self.config.width, "height": self.config.height, "fps": self.config.fps}
        digest = fingerprint(request)
        if path.exists():
            candidate = read_json(path)
            if candidate["request_fingerprint"] != digest:
                raise ValueError(f"Cached candidate context changed: {directory}")
        else:
            candidate = {"id": candidate_id, "scene_id": scene["id"], "seed": seed, "round": round_number,
                         "prompt": prompt, "request_fingerprint": digest}
            write_json(directory / "request.json", request)
        self.progress(f"{scene['id']}: candidate {candidate_id}")
        if "video" not in candidate:
            candidate["video"] = require_file(self.backend.generate(request, directory / "video.mp4"))
            assert_stream(candidate["video"], "video")
            write_json(path, candidate)
        else:
            require_file(candidate["video"])
        if "audio" not in candidate:
            candidate["audio"] = require_file(self.backend.audio({**request, "video": candidate["video"]}, directory / "audio.wav"))
            assert_stream(candidate["audio"], "audio")
            write_json(path, candidate)
        else:
            require_file(candidate["audio"])
        if "assessment" not in candidate:
            assessment = Assessment(**self.backend.assess({**request, "video": candidate["video"], "audio": candidate["audio"]}, directory))
            candidate["assessment"] = asdict(assessment)
            candidate["score"] = assessment.score(self.config.weights)
            write_json(path, candidate)
        else:
            assessment = Assessment(**candidate["assessment"])
            if abs(candidate["score"] - assessment.score(self.config.weights)) > 1e-9:
                raise ValueError(f"Cached score is inconsistent: {path}")
        return candidate
