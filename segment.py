"""Minimal salient-object segmentation on ONNX Runtime (same pre/post-processing as rembg)."""
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

# file name, input size, mean, std, apply sigmoid to logits
MODELS = {
    "fast": ("isnet-general-use.onnx", 1024, (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), False),
    "best": ("birefnet-general-lite.onnx", 1024, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225), True),
}


class Segmenter:
    def __init__(self, key: str, model_dir: Path):
        fname, self.size, mean, std, self.sigmoid = MODELS[key]
        self.mean = np.array(mean, np.float32)
        self.std = np.array(std, np.float32)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(Path(model_dir) / fname), opts,
                                            providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def mask(self, img: Image.Image) -> Image.Image:
        """Return an 8-bit alpha mask the same size as `img`."""
        x = np.asarray(img.convert("RGB").resize((self.size, self.size), Image.LANCZOS), np.float32)
        x /= max(float(x.max()), 1e-6)
        x = ((x - self.mean) / self.std).transpose(2, 0, 1)[None]
        pred = self.session.run(None, {self.input: x})[0][0, 0]
        if self.sigmoid:
            pred = 1 / (1 + np.exp(-pred))
        lo, hi = float(pred.min()), float(pred.max())
        pred = (pred - lo) / max(hi - lo, 1e-6)
        m = Image.fromarray((pred * 255).astype(np.uint8), "L")
        return m.resize(img.size, Image.LANCZOS)
