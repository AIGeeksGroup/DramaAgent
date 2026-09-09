"""Contract tests only: all service calls and downloads are mocked."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from drama_agent.backends.command import CommandBackend
from drama_agent.backends.dashscope import DashScopeBackend
from drama_agent.config import Config
from drama_agent.storage import write_json


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def cloud(self):
        options = {"api_base":"https://test.invalid/api/v1", "llm_base":"https://test.invalid/v1",
                   "llm_model":"test", "judge_model":"test", "image_model":"test", "video_model":"test"}
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY":"dummy-test-key"}):
            return DashScopeBackend(Config(backend="dashscope", options=options))

    def test_command_protocol_and_argv(self):
        backend = CommandBackend(Config(backend="command", options={"commands":{
            "assess":["worker","{request}","{response}"]}}))
        def worker(args, **kwargs):
            request = json.loads(Path(args[1]).read_text())
            self.assertEqual(request["protocol_version"], 1)
            self.assertEqual(request["prompt"], "literal $(not-a-command)")
            self.assertNotIn("shell", kwargs)
            Path(args[2]).write_text('{"ok":true}')
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("drama_agent.backends.command.subprocess.run", side_effect=worker):
            self.assertEqual(backend.assess({"prompt":"literal $(not-a-command)"},self.directory), {"ok":True})

    def test_command_failure_does_not_use_stale_response(self):
        backend = CommandBackend(Config(backend="command", options={"commands":{
            "assess":["worker","{request}","{response}"]}}))
        response = self.directory / "assess.response.json"
        response.write_text('{"stale":true}')
        with patch("drama_agent.backends.command.subprocess.run", return_value=SimpleNamespace(returncode=1,stdout="",stderr="failed")):
            with self.assertRaises(RuntimeError):
                backend.assess({}, self.directory)
        self.assertFalse(response.exists())

    def test_cloud_task_resume_does_not_submit_again(self):
        backend = self.cloud()
        write_json(self.directory / "provider_task.json", {"task_id":"saved-id"})
        write_json(self.directory / "provider_prompt.json", {"prompt":"A scene"})
        success = {"output":{"task_status":"SUCCEEDED","video_url":"https://test.invalid/video.mp4"}}
        with patch("drama_agent.backends.dashscope.request_json",return_value=success) as http, \
             patch("drama_agent.backends.dashscope.download",return_value="video.mp4"):
            backend.generate({}, self.directory / "video.mp4")
            http.assert_called_once_with("https://test.invalid/api/v1/tasks/saved-id", "dummy-test-key")

    def test_cloud_failed_task_is_reported(self):
        backend = self.cloud()
        write_json(self.directory / "provider_task.json", {"task_id":"saved-id"})
        write_json(self.directory / "provider_prompt.json", {"prompt":"A scene"})
        with patch("drama_agent.backends.dashscope.request_json",return_value={"output":{"task_status":"FAILED"}}):
            with self.assertRaisesRegex(RuntimeError, "FAILED"):
                backend.generate({}, self.directory / "video.mp4")

    def test_reference_response_contract(self):
        backend = self.cloud()
        response = {"output":{"choices":[{"message":{"content":[{"image":"https://test.invalid/ref.png"}]}}]}}
        with patch("drama_agent.backends.dashscope.request_json",return_value=response) as http, \
             patch("drama_agent.backends.dashscope.download",return_value="reference.png"):
            backend.reference({"name":"Ada","appearance":"Blue coat"},self.directory/"reference.png")
            self.assertEqual(http.call_args.args[2]["parameters"]["n"], 1)
            self.assertIn("Blue coat",http.call_args.args[2]["input"]["messages"][0]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
