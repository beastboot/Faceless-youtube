# Odd Annals: free AI motion on Kaggle's GPU. Animates each (image, prompt) job from the input
# dataset with Wan 2.2 TI2V-5B (open source, Apache-2.0) and writes mp4 clips + timings to
# /kaggle/working/out.
import glob, json, os, subprocess, sys, time

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers==0.35.1", "transformers==4.56.1",
                "accelerate==1.10.1", "sentencepiece==0.2.1", "ftfy==6.3.1", "imageio==2.37.0",
                "imageio-ffmpeg==0.6.0"], check=True)

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import torch
from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
from diffusers.utils import export_to_video
from PIL import Image, ImageOps

MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
src = next(iter(glob.glob("/kaggle/input/**/jobs.json", recursive=True)), None)
if not src:
    raise SystemExit("no jobs.json in the input dataset")
root = os.path.dirname(src)
jobs = json.load(open(src))
os.makedirs("/kaggle/working/out", exist_ok=True)

gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported(including_emulation=False)
dtype = torch.bfloat16 if bf16 else torch.float16
print("GPU:", gpu, "dtype:", dtype, flush=True)

t0 = time.time()
vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)  # fp32 VAE: no NaN frames
pipe = WanImageToVideoPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=dtype)
# A 16 GB T4 can't hold the 5B transformer plus its working memory: stream the weights layer by layer.
pipe.enable_sequential_cpu_offload()
load_s = time.time() - t0
print(f"model loaded in {load_s:.0f}s", flush=True)

budget_s = float(jobs[0].get("budget_seconds", 9000)) if jobs else 9000
timings = {"gpu": gpu, "dtype": str(dtype), "load_s": round(load_s), "clips": []}
for j in jobs:
    if time.time() - t0 > budget_s:
        print("time budget reached; stopping", flush=True)
        break
    s = time.time()
    try:
        w, h = j.get("width", 480), j.get("height", 832)
        img = ImageOps.fit(Image.open(os.path.join(root, j["image"])).convert("RGB"), (w, h))
        frames = pipe(image=img, prompt=j["prompt"],
                      negative_prompt="blurry, distorted, deformed faces, extra limbs, text, watermark, flicker, static, cartoon",
                      width=w, height=h, num_frames=j.get("frames", 49), num_inference_steps=j.get("steps", 24),
                      guidance_scale=5.0, generator=torch.Generator("cuda").manual_seed(j.get("seed", 0))).frames[0]
        export_to_video(frames, f"/kaggle/working/out/{j['id']}.mp4", fps=24)
        timings["clips"].append({"id": j["id"], "seconds": round(time.time() - s)})
        print(f"clip {j['id']}: {time.time() - s:.0f}s", flush=True)
    except Exception as e:
        timings["clips"].append({"id": j["id"], "error": str(e)[:300]})
        import traceback
        print(f"clip {j['id']} failed: {e}\n{traceback.format_exc()[-1500:]}", flush=True)
    json.dump(timings, open("/kaggle/working/out/timings.json", "w"))
print("done", json.dumps(timings), flush=True)
