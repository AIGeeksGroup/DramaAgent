"""Bring any local or hosted generator through a versioned JSON subprocess protocol."""
import json
from pathlib import Path
import subprocess
from ..storage import write_json


class CommandBackend:
    def __init__(self, config):
        self.config = config
        self.commands = config.options.get("commands", {})
        self.timeout = config.options.get("command_timeout", 1800)
        self.provenance = {"backend": "command", "commands": self.commands}

    def call(self, operation, payload, directory):
        command = self.commands.get(operation)
        if not isinstance(command, list) or not command or not all(isinstance(t, str) for t in command):
            raise ValueError(f"Configure options.commands.{operation} as a nonempty argv array")
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        request = directory / f"{operation}.request.json"
        response = directory / f"{operation}.response.json"
        response.unlink(missing_ok=True)
        write_json(request, {"protocol_version": 1, "operation": operation, **payload})
        args = [token.replace("{request}", str(request)).replace("{response}", str(response)) for token in command]
        if not any("{request}" in t for t in command) or not any("{response}" in t for t in command):
            raise ValueError("Command must include {request} and {response} placeholders")
        result = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
        # Provider logs may contain signed URLs; retain them locally, never print them automatically.
        (directory / f"{operation}.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{operation} command failed with exit {result.returncode}; see {directory / (operation+'.log')}")
        value = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{operation} must return a JSON object")
        return value

    def plan(self, source, directory):
        return self.call("plan", {"source": source}, directory)

    def reference(self, character, output):
        return self.call("reference", {"character": character, "output": str(output)}, output.parent)["path"]

    def generate(self, request, output):
        return self.call("video", {**request, "output": str(output)}, output.parent)["path"]

    def audio(self, request, output):
        return self.call("audio", {**request, "output": str(output)}, output.parent)["path"]

    def assess(self, request, directory):
        return self.call("assess", request, directory)
