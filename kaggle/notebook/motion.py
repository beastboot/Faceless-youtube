# Odd Annals: free AI motion on Kaggle's GPU. Animates each (image, prompt) job from the input
# dataset with LTX-Video (open source) and writes mp4 clips + timings to /kaggle/working/out.
import glob, json, os, subprocess, sys, time

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers==0.35.1", "transformers==4.56.1",
                "accelerate==1.10.1", "sentencepiece==0.2.1", "imageio==2.37.0", "imageio-ffmpeg==0.6.0"], check=True)

import torch
from diffusers import LTXImageToVideoPipeline
from diffusers.utils import export_to_video
from PIL import Image, ImageOps

src = next(iter(glob.glob("/kaggle/input/*/jobs.json")), None)
if not src:
    raise SystemExit("no jobs.json in the input dataset")
root = os.path.dirname(src)
jobs = json.load(open(src))
os.makedirs("/kaggle/working/out", exist_ok=True)

gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
dtype = torch.bfloat16 if bf16 else torch.float16
print("GPU:", gpu, "dtype:", dtype, flush=True)

t0 = time.time()
pipe = LTXImageToVideoPipeline.from_pretrained("Lightricks/LTX-Video-0.9.5", torch_dtype=dtype)
pipe.enable_model_cpu_offload()
pipe.vae.enable_tiling()
load_s = time.time() - t0
print(f"model loaded in {load_s:.0f}s", flush=True)

budget_s = float(os.environ.get("BUDGET_SECONDS", jobs[0].get("budget_seconds", 9000) if jobs else 9000))
timings = {"gpu": gpu, "load_s": round(load_s), "clips": []}
for j in jobs:
    if time.time() - t0 > budget_s:
        print("time budget reached; stopping", flush=True)
        break
    s = time.time()
    try:
        w, h = j.get("width", 704), j.get("height", 480)
        img = ImageOps.fit(Image.open(os.path.join(root, j["image"])).convert("RGB"), (w, h))
        frames = pipe(image=img, prompt=j["prompt"],
                      negative_prompt="worst quality, blurry, distorted, deformed faces, text, watermark, jittery",
                      width=w, height=h, num_frames=j.get("frames", 97), num_inference_steps=j.get("steps", 30),
                      guidance_scale=3.0, generator=torch.Generator("cuda").manual_seed(j.get("seed", 0))).frames[0]
        export_to_video(frames, f"/kaggle/working/out/{j['id']}.mp4", fps=24)
        timings["clips"].append({"id": j["id"], "seconds": round(time.time() - s)})
        print(f"clip {j['id']}: {time.time() - s:.0f}s", flush=True)
    except Exception as e:
        timings["clips"].append({"id": j["id"], "error": str(e)[:300]})
        print(f"clip {j['id']} failed: {e}", flush=True)
    json.dump(timings, open("/kaggle/working/out/timings.json", "w"))
print("done", json.dumps(timings), flush=True)
