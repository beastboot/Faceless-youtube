"""Wan 2.2 image-to-video on Modal's serverless GPUs (free $30/month Starter credits).

Deploy once:   modal deploy src/wan_modal.py
The pipeline calls it through src/aimotion.py. Model weights are cached in a Modal Volume, so only
the first run downloads them."""
import io

import modal

MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"   # 5B text+image-to-video, fits a 24 GB GPU

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("torch==2.6.0", "diffusers==0.35.1", "transformers==4.56.1", "accelerate==1.10.1",
                 "ftfy==6.3.1", "sentencepiece==0.2.1", "imageio==2.37.0", "imageio-ffmpeg==0.6.0",
                 "Pillow==11.3.0", "huggingface_hub==0.35.0")
    .env({"HF_HOME": "/cache/hf", "HF_HUB_ENABLE_HF_TRANSFER": "0"})
)
cache = modal.Volume.from_name("odd-annals-wan-cache", create_if_missing=True)
app = modal.App("odd-annals-wan", image=image)


@app.cls(gpu="L4", volumes={"/cache": cache}, timeout=60 * 40, scaledown_window=90, max_containers=1)
class Wan:
    @modal.enter()
    def load(self):
        import torch
        from diffusers import WanImageToVideoPipeline
        self.pipe = WanImageToVideoPipeline.from_pretrained(MODEL, torch_dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()       # keeps peak VRAM within 24 GB
        self.pipe.vae.enable_tiling()
        cache.commit()

    @modal.method()
    def animate(self, image_bytes: bytes, prompt: str, width: int = 704, height: int = 1280,
                frames: int = 73, steps: int = 24, seed: int = 0) -> bytes:
        """Returns an mp4 (24 fps) of the picture coming to life as described by `prompt`."""
        import time
        import torch
        from diffusers.utils import export_to_video
        from PIL import Image, ImageOps
        t0 = time.time()
        img = ImageOps.fit(Image.open(io.BytesIO(image_bytes)).convert("RGB"), (width, height))
        out = self.pipe(image=img, prompt=prompt,
                        negative_prompt="blurry, distorted faces, extra limbs, text, watermark, flicker, static",
                        width=width, height=height, num_frames=frames, num_inference_steps=steps,
                        guidance_scale=5.0, generator=torch.Generator("cuda").manual_seed(seed)).frames[0]
        path = "/tmp/clip.mp4"
        export_to_video(out, path, fps=24)
        print(f"generated {frames} frames {width}x{height} in {time.time() - t0:.0f}s")
        return open(path, "rb").read()


@app.local_entrypoint()
def main(image: str, prompt: str = "gentle cinematic motion, hair and clothes moving in the wind, slow camera push-in",
         out: str = "wan_test.mp4", portrait: bool = False):
    import time
    w, h = (704, 1280) if portrait else (1280, 704)
    t0 = time.time()
    data = Wan().animate.remote(open(image, "rb").read(), prompt, width=w, height=h)
    open(out, "wb").write(data)
    print(f"saved {out} in {time.time() - t0:.0f}s (incl. cold start)")
