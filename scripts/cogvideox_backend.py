"""Optional CUDA worker adapted from MA2's CogVideoX image-to-video interface.

Uses a scene keyframe, never an arbitrary first character portrait. Install the separate
GPU requirements in the worker environment; no GPU packages are required by the controller.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--keyframes", required=True, help="JSON mapping scene IDs to scene image paths")
    parser.add_argument("--model", default="THUDM/CogVideoX-5b-I2V")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    if request["operation"] != "video":
        raise ValueError("This worker only handles video generation")
    mapping_path = Path(args.keyframes).resolve()
    mapping = json.loads(mapping_path.read_text())
    keyframe = (mapping_path.parent / mapping[request["scene"]["id"]]).resolve()
    if not keyframe.is_file():
        raise ValueError(f"Missing scene keyframe: {keyframe}")
    import torch
    from diffusers import CogVideoXImageToVideoPipeline
    from diffusers.utils import export_to_video, load_image
    if not torch.cuda.is_available():
        raise RuntimeError("The CogVideoX worker requires a CUDA GPU")
    pipeline = CogVideoXImageToVideoPipeline.from_pretrained(args.model, torch_dtype=torch.bfloat16)
    pipeline.enable_model_cpu_offload()
    pipeline.vae.enable_tiling()
    scene = request["scene"]
    identities = "; ".join(f"{c['name']}: {c['appearance']}" for c in request["characters"])
    # Preserve concise control instructions within the backbone tokenizer's real limit.
    condition = json.loads(request["prompt"])
    repair = condition.get("repair", {})
    prompt = f"{scene['description']} {scene['environment']}. {scene['emotion']}. {scene['transition']}. {identities}. "
    if repair:
        prompt += f"{repair['constraint']} {repair['observed_failure']}"
    if len(pipeline.tokenizer(prompt).input_ids) > 226:
        raise ValueError("Scene prompt exceeds CogVideoX's 226-token limit; shorten the scene/appearance text")
    frames = pipeline(image=load_image(str(keyframe)).resize((720,480)), prompt=prompt,
                      width=720, height=480, num_frames=49, num_inference_steps=50, guidance_scale=6,
                      generator=torch.Generator(device="cuda").manual_seed(request["seed"])).frames[0]
    # Match the scene's requested duration without silently truncating its event.
    export_to_video(frames, request["output"], fps=len(frames)/scene["duration"])
    Path(args.response).write_text(json.dumps({"path": request["output"]}))


if __name__ == "__main__":
    main()
