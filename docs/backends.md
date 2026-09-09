# Model Adapter Protocol

`backend=command` exchanges JSON files using protocol version 1. Commands run as argument arrays without a shell. `{request}` and `{response}` are replaced with absolute paths at invocation time; both placeholders are required.

```json
{
  "backend": "command",
  "options": {
    "command_timeout": 1800,
    "commands": {
      "plan": ["/path/to/python", "/path/to/worker.py", "--request", "{request}", "--response", "{response}"],
      "reference": ["/path/to/python", "/path/to/worker.py", "--request", "{request}", "--response", "{response}"],
      "video": ["/path/to/python", "/path/to/worker.py", "--request", "{request}", "--response", "{response}"],
      "audio": ["/path/to/python", "/path/to/worker.py", "--request", "{request}", "--response", "{response}"],
      "assess": ["/path/to/python", "/path/to/worker.py", "--request", "{request}", "--response", "{response}"]
    }
  }
}
```

Every operation includes `protocol_version` and `operation`. A complete structured story skips `plan`; a character with an existing reference skips its `reference` operation.

| Operation | Request fields | Response |
|---|---|---|
| `plan` | `source`: raw text or MA2 synopsis | Complete Story object; see the example story |
| `reference` | `character`, `output` | `{"path":"/absolute/reference.png"}` |
| `video` | Scene request fields below, `output` | `{"path":"/absolute/video.mp4"}` |
| `audio` | Scene request, `video`, `output` | `{"path":"/absolute/audio.wav"}` |
| `assess` | Scene request, `video`, `audio` | Assessment object |

Scene requests contain:

- `scene`: Description, environment, emotion, transition, character IDs, duration, dialogue, and ambience/music intent.
- `characters`: Attributes and absolute canonical reference paths for characters in the current scene.
- `history`: Observed summaries of previously selected scenes, scores, and whether they met the threshold.
- `previous`: The preceding scene's selection record, including video and audio paths; `null` for the first scene.
- `prompt`: Complete generation conditions encoded as a JSON string. During repair, it includes the failure dimension, constraints, and previous prompt.
- `seed`, `round`, `width`, `height`, and `fps`.

Audio must align with the video and dialogue time windows. The command returns a mixed, decodable audio track. To preserve a video's native audio, the audio adapter can extract it from the video. Reference and output paths must be accessible to the worker. For remote services, the adapter should upload inputs and download results to local storage.

Example assessment response:

```json
{
  "scores": {"identity": 0.9, "semantic": 0.7, "temporal": 0.85, "audio_visual": 0.8},
  "issues": {"semantic": "The required lamp is missing."},
  "summary": "The character reaches the forest entrance with an empty right hand.",
  "evidence": "Describe the frames, clips, and audio tracks actually examined, including unobservable or uncertain details."
}
```

All four scores are required and must be finite values in `[0,1]`. The `summary` should describe actual observations rather than repeat the intended plot. Missing media or assessment results cause an error; adapters must not fabricate passing scores.

With `backend=dashscope`, supply `options.commands` to override individual built-in operations. For example, override `video` to connect an existing GPU worker, or override `assess` to use a complete audiovisual evaluator.

Example CogVideoX override:

```json
{"video":["/path/to/gpu/python","scripts/cogvideox_backend.py","--request","{request}","--response","{response}","--keyframes","/path/to/keyframes.json"]}
```

The keyframe mapping has the form `{"s001":"frames/scene_1.png","s002":"frames/scene_2.png"}`. Relative paths are resolved against the mapping file. Real workers must be tested in their target model environment. The adapter example defines the interface; importing the core package does not download or load models.
