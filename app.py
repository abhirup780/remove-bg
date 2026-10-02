"""BG Remove - offline background remover with bulk queue (pywebview UI + ONNX Runtime)."""
import base64
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from itertools import count
from pathlib import Path

import webview
from webview.dom import DOMEventHandler
from PIL import Image, ImageGrab, ImageOps

from segment import Segmenter

# bundled resources (ui/, models/, icon) live next to app.py, or in _MEIPASS when frozen
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BG Remove"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tif", ".tiff", ".jfif"}
SETTINGS_FILE = DATA_DIR / "settings.json"
INBOX = Path.home() / "Pictures" / "bgremove"
DEFAULTS = {
    "model": "fast", "bg": "transparent", "color": "#ffffff", "format": "png",
    "dest": "beside", "custom_dir": "", "trim": False, "recursive": True,
}
THUMB = 300


def data_url(img, fmt):
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=80)
    else:
        img.save(buf, "PNG", compress_level=1)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in count(2):
        q = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not q.exists():
            return q


def open_image(path, draft=None):
    img = Image.open(path)
    if draft and img.format == "JPEG":
        img.draft("RGB", draft)  # fast DCT downscale for previews
    img.load()
    return ImageOps.exif_transpose(img)


class Job:
    _ids = count(1)

    def __init__(self, src: Path):
        self.id = next(self._ids)
        self.src = src
        self.status = "queued"  # queued | working | done | error | stopped
        self.out = None
        self.err = ""
        self.ms = 0
        self.size = None
        self.thumb = None
        self.result = None
        self.sent = set()

    def payload(self):
        d = {"id": self.id, "name": self.src.name, "status": self.status, "err": self.err,
             "ms": self.ms, "out": str(self.out) if self.out else "", "size": self.size}
        for k in ("thumb", "result"):  # send heavy data URLs only once
            v = getattr(self, k)
            if v and k not in self.sent:
                d[k] = v
                self.sent.add(k)
        return d


class Engine:
    def __init__(self):
        self.settings = {**DEFAULTS, **self._load()}
        self.jobs: dict[int, Job] = {}
        self.todo = queue.Queue()
        self.thumbq = queue.Queue()
        self.dirty = set()
        self.lock = threading.RLock()
        self.infer_lock = threading.Lock()
        self.running = threading.Event()
        self.running.set()
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.model_state = "loading"
        self.window = None
        self.last_out = None
        self.batch_start = None
        self.busy_ms = 0
        for _ in range(2):  # 2 workers: one decodes/encodes while the other runs the model
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._thumber, daemon=True).start()
        threading.Thread(target=self._pusher, daemon=True).start()
        self.preload()

    # ---- settings -------------------------------------------------------
    def _load(self):
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ---- model ----------------------------------------------------------
    def session(self, key):
        with self.session_lock:
            if key not in self.sessions:
                self.sessions[key] = Segmenter(key, RES_DIR / "models")
            return self.sessions[key]

    def preload(self):
        key = self.settings["model"]

        def run():
            if key not in self.sessions:
                self.model_state = "loading"
                self.mark()
            try:
                self.session(key)
                self.model_state = "ready"
            except Exception as e:
                self.model_state = f"error: {e}"
            self.mark()
        threading.Thread(target=run, daemon=True).start()

    # ---- queue ----------------------------------------------------------
    def scan(self, paths):
        files = []
        for p in map(Path, paths):
            if p.is_dir():
                walker = os.walk(p) if self.settings["recursive"] else [(str(p), [], os.listdir(p))]
                for root, dirs, names in walker:
                    dirs[:] = sorted(d for d in dirs if d != "no-bg" and not d.startswith("."))
                    for n in sorted(names):
                        f = Path(root) / n
                        if f.suffix.lower() in EXTS and not f.stem.endswith("_nobg"):
                            files.append(f)
            elif p.is_file() and p.suffix.lower() in EXTS:
                files.append(p)
        return files

    def add(self, paths):
        files = self.scan(paths)
        added = 0
        with self.lock:
            active = {j.src for j in self.jobs.values() if j.status in ("queued", "working")}
            if not any(j.status in ("queued", "working") for j in self.jobs.values()):
                self.batch_start, self.busy_ms = time.time(), 0
            for f in files:
                if f in active:
                    continue
                j = Job(f)
                self.jobs[j.id] = j
                self.todo.put(j.id)
                self.thumbq.put(j.id)
                self.dirty.add(j.id)
                added += 1
        return {"added": added, "found": len(files)}

    def requeue(self, statuses):
        with self.lock:
            for j in self.jobs.values():
                if j.status in statuses:
                    j.status, j.err = "queued", ""
                    self.todo.put(j.id)
                    self.dirty.add(j.id)

    def stop(self):
        with self.lock:
            for j in self.jobs.values():
                if j.status == "queued":
                    j.status = "stopped"
                    self.dirty.add(j.id)
        self.running.set()

    def remove(self, ids):
        with self.lock:
            for i in ids:
                j = self.jobs.get(i)
                if j and j.status != "working":
                    del self.jobs[i]
        self.mark()

    def mark(self, *ids):
        with self.lock:
            self.dirty.update(ids or {0})

    # ---- processing -----------------------------------------------------
    def out_path(self, src: Path, ext):
        dest = self.settings["dest"]
        if dest == "custom" and self.settings["custom_dir"]:
            folder, name = Path(self.settings["custom_dir"]), f"{src.stem}{ext}"
        elif dest == "subfolder":
            folder, name = src.parent / "no-bg", f"{src.stem}{ext}"
        else:
            folder, name = src.parent, f"{src.stem}_nobg{ext}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not os.access(folder, os.W_OK):
                raise PermissionError
        except Exception:
            folder = INBOX
            folder.mkdir(parents=True, exist_ok=True)
        return unique_path(folder / name)

    def _thumber(self):
        while True:
            jid = self.thumbq.get()
            j = self.jobs.get(jid)
            if not j or j.thumb:
                continue
            try:
                img = open_image(j.src, draft=(THUMB * 2, THUMB * 2))
                img.thumbnail((THUMB, THUMB))
                j.thumb = data_url(img, "JPEG")
            except Exception:
                pass
            self.mark(jid)

    def _worker(self):
        while True:
            jid = self.todo.get()
            self.running.wait()
            with self.lock:
                j = self.jobs.get(jid)
                if not j or j.status != "queued":
                    continue
                j.status = "working"
                self.dirty.add(jid)
            s = dict(self.settings)
            t0 = time.perf_counter()
            try:
                img = open_image(j.src)
                rgb = img.convert("RGB")
                j.size = list(rgb.size)
                if not j.thumb:
                    t = rgb.copy(); t.thumbnail((THUMB, THUMB)); j.thumb = data_url(t, "JPEG")
                sess = self.session(s["model"])
                with self.infer_lock:
                    mask = sess.mask(rgb)
                cut = rgb.convert("RGBA")
                cut.putalpha(mask)
                if s["trim"]:
                    box = mask.point(lambda v: 255 if v > 10 else 0).getbbox()
                    if box:
                        pad = int(max(rgb.size) * 0.02)
                        box = (max(0, box[0] - pad), max(0, box[1] - pad),
                               min(rgb.width, box[2] + pad), min(rgb.height, box[3] + pad))
                        cut = cut.crop(box)
                fmt = s["format"]
                bg = s["bg"]
                if fmt == "jpg" and bg == "transparent":
                    bg = "white"
                if bg != "transparent":
                    color = {"white": "#ffffff", "black": "#000000"}.get(bg, s["color"])
                    flat = Image.new("RGB", cut.size, color)
                    flat.paste(cut, mask=cut.getchannel("A"))
                    cut = flat
                ext = {"png": ".png", "webp": ".webp", "jpg": ".jpg"}[fmt]
                dst = self.out_path(j.src, ext)
                if fmt == "png":
                    cut.save(dst, "PNG", compress_level=3)
                elif fmt == "webp":
                    cut.save(dst, "WEBP", quality=92, method=4)
                else:
                    cut.save(dst, "JPEG", quality=93, subsampling=0)
                prev = cut.copy()
                prev.thumbnail((THUMB, THUMB))
                j.result = data_url(prev, "PNG")
                j.out, j.status = dst, "done"
                self.last_out = dst
            except Exception as e:
                j.status, j.err = "error", str(e)[:300]
            j.ms = int((time.perf_counter() - t0) * 1000)
            self.busy_ms += j.ms
            self.mark(jid)

    # ---- UI push --------------------------------------------------------
    def stats(self):
        js = list(self.jobs.values())
        c = {k: sum(1 for j in js if j.status == k) for k in ("queued", "working", "done", "error", "stopped")}
        done_ms = [j.ms for j in js if j.status == "done"]
        return {**c, "total": len(js), "avg_ms": int(sum(done_ms) / len(done_ms)) if done_ms else 0,
                "elapsed": int(time.time() - self.batch_start) if self.batch_start else 0,
                "paused": not self.running.is_set(), "model": self.model_state,
                "has_output": bool(self.last_out)}

    def _pusher(self):
        while True:
            time.sleep(0.15)
            if not self.window or not self.dirty:
                continue
            with self.lock:
                ids, self.dirty = self.dirty, set()
                jobs = [self.jobs[i].payload() for i in ids if i in self.jobs]
                payload = {"jobs": jobs, "ids": list(self.jobs), "stats": self.stats()}
            try:
                self.window.evaluate_js(f"window.onUpdate && window.onUpdate({json.dumps(payload)})")
            except Exception:
                with self.lock:
                    self.dirty.update(ids)


class Api:
    """Methods callable from the page as window.pywebview.api.<name>()."""

    def __init__(self, engine: Engine):
        self._e = engine

    def init(self):
        e = self._e
        with e.lock:
            for j in e.jobs.values():
                j.sent.clear()
            e.dirty.update(e.jobs or {0})
        return {"settings": e.settings, "stats": e.stats()}

    def set_settings(self, patch):
        e = self._e
        old_model = e.settings["model"]
        e.settings.update(patch)
        e.save()
        if e.settings["model"] != old_model:
            e.preload()
        return e.settings

    def add_paths(self, paths):
        return self._e.add(paths)

    def pick_files(self):
        exts = ";".join(f"*{x}" for x in sorted(EXTS))
        r = self._e.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                              file_types=(f"Images ({exts})", "All files (*.*)"))
        return self._e.add(r) if r else None

    def pick_folder(self):
        r = self._e.window.create_file_dialog(webview.FileDialog.FOLDER)
        return self._e.add(r) if r else None

    def pick_output(self):
        r = self._e.window.create_file_dialog(webview.FileDialog.FOLDER)
        if r:
            return self.set_settings({"custom_dir": r[0], "dest": "custom"})
        return None

    def paste(self):
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            data = None
        if isinstance(data, list):
            return self._e.add(data)
        if isinstance(data, Image.Image):
            INBOX.mkdir(parents=True, exist_ok=True)
            p = unique_path(INBOX / time.strftime("clipboard_%Y%m%d_%H%M%S.png"))
            data.save(p)
            return self._e.add([p])
        return {"added": 0, "found": 0, "clipboard": False}

    def pause(self):
        self._e.running.clear(); self._e.mark()

    def resume(self):
        self._e.running.set(); self._e.mark()

    def stop(self):
        self._e.stop(); self._e.mark()

    def retry(self):
        self._e.requeue({"error", "stopped"}); self._e.running.set()

    def rerun(self, ids):
        e = self._e
        with e.lock:
            for i in ids:
                j = e.jobs.get(i)
                if j and j.status in ("done", "error", "stopped"):
                    j.status, j.err, j.result = "queued", "", None
                    j.sent.discard("result")
                    e.todo.put(i)
                    e.dirty.add(i)
        e.running.set()

    def clear(self, which):
        e = self._e
        keep = {"done": ("queued", "working", "error", "stopped"), "all": ("working",)}[which]
        e.remove([i for i, j in list(e.jobs.items()) if j.status not in keep])

    def remove(self, jid):
        self._e.remove([jid])

    def preview(self, jid):
        j = self._e.jobs.get(jid)
        if not j:
            return None
        before = open_image(j.src, draft=(1800, 1800)).convert("RGB")
        before.thumbnail((1600, 1600))
        res = {"before": data_url(before, "JPEG"), "name": j.src.name, "out": str(j.out or "")}
        if j.out and Path(j.out).exists():
            after = Image.open(j.out)
            after.thumbnail((1600, 1600))
            res["after"] = data_url(after, "PNG" if after.mode == "RGBA" else "JPEG")
        return res

    def open_file(self, jid):
        j = self._e.jobs.get(jid)
        if j and j.out:
            os.startfile(j.out)

    def reveal(self, jid=None):
        e = self._e
        j = e.jobs.get(jid) if jid else None
        target = (j.out if j and j.out else None) or e.last_out
        if target and Path(target).exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif e.settings["dest"] == "custom" and e.settings["custom_dir"]:
            os.startfile(e.settings["custom_dir"])
        else:
            INBOX.mkdir(parents=True, exist_ok=True)
            os.startfile(INBOX)


def set_window_icon(title):
    """Give the window (and its taskbar button) the app icon instead of Python's."""
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    ico = str(RES_DIR / "bgremove.ico")
    for which, size in ((1, 0), (0, 32)):  # ICON_BIG (default size), ICON_SMALL
        h = u.LoadImageW(None, ico, 1, size, size, 0x10 | (0x40 if not size else 0))
        if h:
            u.SendMessageW(hwnd, 0x80, which, h)


def main():
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BGRemove.App")
    engine = Engine()
    api = Api(engine)
    win = webview.create_window(
        "BG Remove", str(RES_DIR / "ui" / "index.html"), js_api=api,
        width=1160, height=700, min_size=(860, 560), background_color="#0e0f12")
    engine.window = win

    def on_drop(e):
        paths = [f.get("pywebviewFullPath") for f in e.get("dataTransfer", {}).get("files", [])]
        paths = [p for p in paths if p]
        if paths:
            r = engine.add(paths)
            win.evaluate_js(f"window.onDropped && window.onDropped({json.dumps(r)})")

    def on_loaded():
        try:
            set_window_icon("BG Remove")
        except Exception:
            pass
        doc = win.dom.document
        doc.events.dragover += DOMEventHandler(lambda e: None, True, True)
        doc.events.drop += DOMEventHandler(on_drop, True, True)
        files = [a for a in sys.argv[1:] if not a.startswith("--")]
        if files:
            engine.add(files)

    win.events.loaded += on_loaded
    webview.start(debug="--debug" in sys.argv, private_mode=False,
                  storage_path=str(DATA_DIR / "webview"))


if __name__ == "__main__":
    main()
