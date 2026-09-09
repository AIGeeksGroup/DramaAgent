import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from drama_agent.config import Config
from drama_agent.pipeline import DramaAgent
from drama_agent.schema import Assessment, DIMENSIONS, Story
from drama_agent.storage import read_json
from drama_agent.legacy import import_shots

STORY = {"title": "Test", "narrative": "Find lamp. Return home.",
         "characters": [{"id": "c1", "name": "Ada", "appearance": "Blue coat"}],
         "scenes": [{"id": "s2", "description": "Find lamp", "characters": ["c1"], "environment": "Snow", "emotion": "hope", "duration": 1}]}


class FakeBackend:
    provenance = {"backend": "test"}

    def __init__(self, scores):
        self.scores, self.calls, self.requests = scores, [], []

    def reference(self, char, output):
        self.calls.append("reference")
        output.write_bytes(b"image")
        return str(output)

    def generate(self, request, output):
        self.calls.append("video")
        self.requests.append(copy.deepcopy(request))
        output.write_bytes(b"video")
        return str(output)

    def audio(self, request, output):
        self.calls.append("audio")
        output.write_bytes(b"audio")
        return str(output)

    def assess(self, request, directory):
        self.calls.append("assess")
        value = self.scores[len(self.requests)-1]
        scores = value if isinstance(value, dict) else dict.fromkeys(DIMENSIONS, value)
        return {"scores": scores, "issues": dict.fromkeys(DIMENSIONS, "missing evidence"),
                "summary": "OBSERVED lamp held in left hand", "evidence": "test fixture"}


def fake_compose(story, selected, target, config):
    target.mkdir(parents=True, exist_ok=True)
    output = target / "final.mp4"
    output.write_bytes(b"composed")
    return str(output)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "run"
        for name, value in (("check_tools", None), ("assert_stream", None), ("compose", fake_compose)):
            p = patch(f"drama_agent.pipeline.{name}", side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)

    def execute(self, scores, config=None, story=None):
        backend = FakeBackend(scores)
        agent = DramaAgent(config or Config(candidates=1), backend, lambda _: None)
        return agent.run(story or STORY, self.output), backend

    def test_default_paper_weights(self):
        a = Assessment(dict(zip(DIMENSIONS, [1, .5, .25, 0])), {}, "observed", "test")
        self.assertAlmostEqual(a.score(Config().weights), .55)

    def test_select_best_candidate(self):
        result, b = self.execute([.81, .95, .9], Config())
        self.assertEqual(result["selected"][0]["id"], "r00_c001")
        self.assertEqual(b.calls.count("video"), 3)

    def test_weakest_dimension_repaired_with_same_references(self):
        initial = dict(identity=.9, semantic=.2, temporal=.8, audio_visual=.8)
        result, b = self.execute([initial, .9])
        repair = json.loads(b.requests[1]["prompt"])["repair"]
        self.assertEqual(repair["dimension"], "semantic")
        self.assertEqual(b.requests[0]["characters"], b.requests[1]["characters"])
        self.assertTrue(result["selected"][0]["accepted"])

    def test_degrading_repair_keeps_original_and_stops(self):
        result, b = self.execute([.7, .4])
        self.assertEqual(result["selected"][0]["score"], .7)
        self.assertEqual(result["selected"][0]["stop_reason"], "min_improvement")
        self.assertEqual(len(b.requests), 2)
        self.assertEqual(result["below_threshold_scenes"], ["s2"])

    def test_repair_budget(self):
        result, b = self.execute([.5, .6, .7])
        self.assertEqual(len(b.requests), 3)
        self.assertEqual(result["selected"][0]["stop_reason"], "budget")

    def test_strict_policy_saves_failure(self):
        with self.assertRaises(RuntimeError):
            self.execute([.3], Config(candidates=1, repair_rounds=0, low_score_policy="fail"))
        self.assertEqual(read_json(self.output / "run.json")["status"], "quality_failed")
        self.assertTrue((self.output / "scenes/s2/selection.json").exists())

    def test_context_uses_observed_history_in_declared_scene_order(self):
        story = copy.deepcopy(STORY)
        story["scenes"].append({**story["scenes"][0], "id": "s10", "description": "Return home"})
        result, b = self.execute([.9, .9], story=story)
        self.assertEqual([s["scene_id"] for s in result["selected"]], ["s2", "s10"])
        self.assertIn("OBSERVED", b.requests[1]["history"][0]["observed_summary"])
        self.assertEqual(b.requests[1]["previous"]["scene_id"], "s2")

    def test_resume_makes_no_provider_calls(self):
        result, b = self.execute([.5, .9])
        before = b.calls[:]
        resumed = DramaAgent(Config(candidates=1), b, lambda _:None).run(STORY, self.output, resume=True)
        self.assertEqual(before, b.calls)
        self.assertEqual(result["selected"], resumed["selected"])

    def test_resume_rejects_changed_configuration(self):
        _, b = self.execute([.9])
        with self.assertRaisesRegex(ValueError, "changed"):
            DramaAgent(Config(candidates=2), b).run(STORY, self.output, resume=True)

    def test_no_overwrite_without_resume(self):
        _, b = self.execute([.9])
        with self.assertRaisesRegex(ValueError, "already"):
            DramaAgent(Config(candidates=1), b).run(STORY, self.output)

    def test_missing_cached_media_is_not_silently_accepted(self):
        result, b = self.execute([.9])
        Path(result["selected"][0]["video"]).unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            DramaAgent(Config(candidates=1), b).run(STORY, self.output, resume=True)

    def test_resume_after_audio_failure_reuses_video(self):
        b = FakeBackend([.9])
        original = b.audio
        def fail(*args):
            raise RuntimeError("interrupted")
        b.audio = fail
        agent = DramaAgent(Config(candidates=1), b, lambda _:None)
        with self.assertRaises(RuntimeError):
            agent.run(STORY, self.output)
        b.audio = original
        result = agent.run(STORY, self.output, resume=True)
        self.assertEqual(b.calls.count("video"), 1)
        self.assertEqual(result["status"], "completed")


class SchemaTests(unittest.TestCase):
    def test_unknown_character(self):
        story = copy.deepcopy(STORY)
        story["scenes"][0]["characters"] = ["missing"]
        with self.assertRaises(ValueError):
            Story.from_dict(story)

    def test_path_traversal_id(self):
        story = copy.deepcopy(STORY)
        story["scenes"][0]["id"] = "../escape"
        with self.assertRaises(ValueError):
            Story.from_dict(story)

    def test_bad_scores_and_config(self):
        for value in (float("nan"), 1.1, -.1, True):
            with self.assertRaises(ValueError):
                Assessment(dict.fromkeys(DIMENSIONS, value), {}, "observed", "test")
        for kwargs in ({"candidates":0}, {"repair_rounds":-1}, {"threshold":float("nan")}, {"width":1279}, {"weights":{}}):
            with self.assertRaises(ValueError):
                Config(**kwargs)

    def test_dialogue_timing_and_speaker(self):
        for line in ({"speaker":"c1","text":"hello","start":.2,"end":2},
                     {"speaker":"absent","text":"hello","start":.2,"end":.8}):
            story = copy.deepcopy(STORY)
            story["scenes"][0]["dialogue"] = [line]
            with self.assertRaises(ValueError):
                Story.from_dict(story)

    def test_legacy_numeric_order(self):
        shot = {"Involving Characters":{"Ada":[0,0,1,1]}, "Plot/Visual Description":"Walk", "Subtitles":{"Ada":"Go"}}
        parent = {"Scene Description":"Snow", "Emotional Tone":"hope", "Shot Annotation":{"Shot":{"Shot 10":{**shot,"Plot/Visual Description":"Last"},"Shot 2":shot}}}
        data = {"Sub-Script":{"Sub-Script 1":{"Plot":"Walk home", "Scene Annotation":{"Scene":{"Scene 1":parent}}}}}
        story = import_shots(data, STORY["characters"])
        self.assertEqual([s.description for s in story.scenes], ["Walk","Last"])
        self.assertEqual(story.scenes[0].dialogue[0].speaker, "c1")


if __name__ == "__main__":
    unittest.main()
