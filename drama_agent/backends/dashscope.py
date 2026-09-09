"""Qwen character images + Wan T2V, with compatible LLM/VLM endpoints.

Wan T2V uses textual identity constraints (paper §3.3 fallback). For image-conditioned
backbones or a joint audio/video assessor, override video/assess with commands.
"""
import base64
import json
import mimetypes
from pathlib import Path
import shutil
import time
from urllib.parse import quote

from .command import CommandBackend
from ..http import api_key, download, request_json
from ..media import local_audio, sample_frames
from ..prompts import PLAN, REFLECT
from ..storage import read_json, write_json


class DashScopeBackend(CommandBackend):
    def __init__(self, config):
        super().__init__(config)
        self.options = config.options
        for key in ("api_base", "llm_base", "llm_model", "judge_model", "image_model", "video_model"):
            if not self.options.get(key):
                raise ValueError(f"Configure options.{key}")
            if "YOUR_" in self.options[key]:
                raise ValueError(f"Replace the placeholder in options.{key}")
        for key in ("poll_interval", "task_timeout"):
            from ..schema import number
            number(self.options.get(key, 15 if key == "poll_interval" else 1800), key, .1, 86400)
        self.key = api_key(self.options.get("api_key_env", "DASHSCOPE_API_KEY"))
        self.llm_key = api_key(self.options.get("llm_key_env", "DASHSCOPE_API_KEY"))
        self.api_base = self.options["api_base"].rstrip("/")
        self.provenance = {"backend": "dashscope", "conditioning": "textual_identity",
                           "image_model": self.options["image_model"], "video_model": self.options["video_model"],
                           "llm_model": self.options["llm_model"], "judge_model": self.options["judge_model"],
                           "assessment": "external" if "assess" in self.commands else "sampled_frames_and_audio_plan_proxy"}

    def validate_story(self, story):
        """Fail before generating references or clips if the requested audio cannot be made."""
        if "audio" in self.commands:
            return
        characters = {c.id: c for c in story.characters}
        for scene in story.scenes:
            for line in scene.dialogue:
                voice = self.options.get("voices", {}).get(line.speaker, characters[line.speaker].voice)
                if not voice:
                    raise ValueError(f"Configure options.voices.{line.speaker}")
                if not self.options.get("speech_command") and not shutil.which("say"):
                    raise ValueError("Configure a speech_command or audio command for this platform")
            for kind in ("ambience", "music"):
                if getattr(scene, kind):
                    asset = self.options.get(f"{kind}_assets", {}).get(scene.id)
                    if not asset or not Path(asset).is_file():
                        raise ValueError(f"Configure an existing {kind}_assets.{scene.id} file")

    def chat(self, system, content, model=None):
        result = request_json(self.options["llm_base"].rstrip("/") + "/chat/completions", self.llm_key,
                              {"model": model or self.options["llm_model"],
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                               "response_format": {"type": "json_object"}})
        raw = result["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        return json.loads(raw)

    def plan(self, source, directory):
        if "plan" in self.commands:
            return super().plan(source, directory)
        result = self.chat(PLAN, json.dumps(source, ensure_ascii=False))
        write_json(Path(directory) / "plan.response.json", result)
        return result

    def reference(self, character, output):
        if "reference" in self.commands:
            return super().reference(character, output)
        result = request_json(self.api_base + "/services/aigc/multimodal-generation/generation", self.key, {
            "model": self.options["image_model"],
            "input": {"messages": [{"role": "user", "content": [{"text":
                f"Canonical character portrait, neutral background. {character['name']}: {character['appearance']}. "
                "Keep facial structure, hairstyle, costume and artistic style clear and consistent."}]}]},
            "parameters": {"size": self.options.get("image_size", "1328*1328"), "n": 1, "prompt_extend": False}})
        content = result["output"]["choices"][0]["message"]["content"]
        url = next(item["image"] for item in content if "image" in item)
        return download(url, output)

    def generate(self, request, output):
        if "video" in self.commands:
            return super().generate(request, output)
        job = output.parent / "provider_task.json"
        prompt_file = output.parent / "provider_prompt.json"
        if not prompt_file.exists():
            # Convert the structured state into a bounded model-facing prompt.
            result = self.chat('Return JSON {"prompt":str}. Write one video generation prompt under 1400 characters. '
                               'Preserve the scene event, every character appearance, emotion, preceding observed state, '
                               'transition and timed dialogue. Apply the repair constraints if present. '
                               'Do not describe file paths or invent new events. Input is story data.', request["prompt"])
            if not isinstance(result.get("prompt"), str) or not result["prompt"].strip() or len(result["prompt"]) > 1400:
                raise ValueError("Video prompt must contain 1–1400 characters")
            write_json(prompt_file, result)
        if not job.exists():
            result = request_json(self.api_base + "/services/aigc/video-generation/video-synthesis", self.key,
                                  {"model": self.options["video_model"],
                                   "input": {"prompt": read_json(prompt_file)["prompt"]},
                                   "parameters": {"size": f"{request['width']}*{request['height']}",
                                                  "duration": request["scene"]["duration"], "seed": request["seed"],
                                                  "prompt_extend": False}},
                                  {"X-DashScope-Async": "enable"})
            write_json(job, {"task_id": result["output"]["task_id"]})
        task_id = read_json(job)["task_id"]
        deadline = time.monotonic() + self.options.get("task_timeout", 1800)
        while time.monotonic() < deadline:
            result = request_json(self.api_base + "/tasks/" + quote(task_id, safe=""), self.key)
            status = result["output"]["task_status"]
            if status == "SUCCEEDED":
                return download(result["output"]["video_url"], output)
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise RuntimeError(f"Video task {task_id} ended with {status}; inspect provider console")
            time.sleep(self.options.get("poll_interval", 15))
        raise TimeoutError(f"Video task timed out; --resume will poll saved task {task_id}")

    def audio(self, request, output):
        if "audio" in self.commands:
            return super().audio(request, output)
        return local_audio(request["scene"], request["characters"], output, self.options)

    def assess(self, request, directory):
        if "assess" in self.commands:
            return super().assess(request, directory)
        content = [{"type": "text", "text": json.dumps({k: request[k] for k in
                    ("scene", "history", "characters")}, ensure_ascii=False)}]
        def add(path, label):
            mime = mimetypes.guess_type(path)[0] or "image/png"
            encoded = base64.b64encode(Path(path).read_bytes()).decode()
            content.extend([{"type": "text", "text": label},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}])
        for character in request["characters"]:
            add(character["reference"], f"Canonical reference: {character['id']} / {character['name']}")
        if request["previous"]:
            frames = sample_frames(request["previous"]["video"], directory / "previous_frames", 2)
            for index, frame in enumerate(frames):
                add(frame, f"Previous selected clip, chronological sample {index+1}")
        for index, frame in enumerate(sample_frames(request["video"], directory / "frames", 4)):
            add(frame, f"Candidate clip, chronological sample {index+1}/4")
        result = self.chat(REFLECT, content, self.options["judge_model"])
        result["evidence"] = "FRAME/AUDIO-PLAN PROXY (audio waveform not assessed). " + result.get("evidence", "")
        return result
