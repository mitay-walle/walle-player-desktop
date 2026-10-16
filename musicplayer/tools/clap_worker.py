"""CLAP embedding worker.  Runs with ANY python that has torch + transformers + soundfile + scipy (e.g. the
CLAP_GUI venv), so the player itself stays light.  Protocol (JSON lines):

    python clap_worker.py audio   <- stdin: {"id": 1, "path": "..."}   -> stdout: {"id": 1, "emb": "<b64 float32>"}
    python clap_worker.py text    <- stdin: {"id": "Грустное", "text": "sad music"} -> same shape
    first stdout line: {"ready": true, "device": "cuda", "model": "..."}; errors: {"id":..., "error": "..."}

Models come from the local HuggingFace cache only (offline).  Audio: up to three 10 s windows (25/50/75 %),
mono, 48 kHz; the embedding is the L2-normalised mean of the window embeddings (512-d).
"""

from __future__ import annotations

import base64
import json
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MODEL = os.environ.get("CLAP_MODEL", "laion/clap-htsat-fused")
SR = 48000
WIN = 10 * SR


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def windows(path: str):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd

    try:
        f = sf.SoundFile(path)
    except sf.LibsndfileError:                          # AAC (.m4a): ffmpeg decodes the whole file at 48 kHz
        return _windows_ffmpeg(path)
    with f:
        sr, frames = int(f.samplerate), int(f.frames)
        span = 10 * sr
        starts = [0] if frames <= span else [max(0, int(frames * q) - span // 2) for q in (0.25, 0.5, 0.75)]
        res = []
        for s in starts:
            f.seek(min(s, max(0, frames - 1)))
            x = f.read(min(span, frames - s), dtype="float32", always_2d=True).mean(axis=1)
            if sr != SR:
                g = gcd(sr, SR)
                x = resample_poly(x, SR // g, sr // g).astype("float32")
            res.append(x[:WIN])
    return res


def _windows_ffmpeg(path: str):
    import shutil
    import subprocess
    import numpy as np
    exe = shutil.which("ffmpeg") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links",
                                                 "ffmpeg.exe")
    r = subprocess.run([exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le",
                        "-"], capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    x = np.frombuffer(r.stdout, dtype="<f4").astype("float32")
    if not x.size:
        raise RuntimeError("ffmpeg: " + r.stderr.decode("utf-8", "replace")[:200])
    frames, span = x.size, 10 * SR
    starts = [0] if frames <= span else [max(0, int(frames * q) - span // 2) for q in (0.25, 0.5, 0.75)]
    return [x[s:s + span][:WIN] for s in starts]


def main() -> int:
    import numpy as np
    import torch
    from transformers import ClapModel, ClapProcessor

    mode = sys.argv[1] if len(sys.argv) > 1 else "audio"
    device = "cuda" if torch.cuda.is_available() and os.environ.get("CLAP_DEVICE", "cuda") == "cuda" else "cpu"
    model = ClapModel.from_pretrained(MODEL).to(device).eval()
    proc = ClapProcessor.from_pretrained(MODEL)
    out({"ready": True, "device": device, "model": MODEL})

    def norm(v: "np.ndarray") -> "np.ndarray":
        return v / (np.linalg.norm(v) + 1e-9)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        try:
            with torch.no_grad():
                if mode == "text":
                    inp = proc(text=[item["text"]], return_tensors="pt", padding=True).to(device)
                    emb = model.get_text_features(**inp)
                else:
                    wins = windows(item["path"])
                    try:
                        inp = proc(audio=wins, sampling_rate=SR, return_tensors="pt")
                    except TypeError:
                        inp = proc(audios=wins, sampling_rate=SR, return_tensors="pt")
                    inp = {k: v.to(device) for k, v in inp.items()}
                    emb = model.get_audio_features(**inp)
                emb = emb.pooler_output if hasattr(emb, "pooler_output") else emb
                v = emb.float().cpu().numpy()
                v = np.mean([norm(r) for r in v], axis=0) if mode == "audio" else v[0]
            out({"id": item["id"], "emb": base64.b64encode(norm(v).astype("float32").tobytes()).decode()})
        except Exception as e:  # one bad file must not stop the batch
            out({"id": item.get("id"), "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
