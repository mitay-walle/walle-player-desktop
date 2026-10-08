"""CLAP as a ready-made second opinion for moods (no training needed) and as extra features for the trained model.

CLAP runs in a separate python process (tools/clap_worker.py) so the player stays light: any python that has
torch + transformers works, by default the one of the CLAP_GUI virtualenv.  Embeddings are stored in the
catalogue (``track_embeddings``, model name "clap"), which also gives audio-based *similar tracks*.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from .db import Database
from .jobs import JobContext, null_context

log = logging.getLogger(__name__)
EMB_MODEL = "clap"
DIM = 512
WORKER = Path(__file__).resolve().parent.parent / "tools" / "clap_worker.py"

from .moods import PROMPTS as DEFAULT_PROMPTS  # noqa: E402  (text description of every mood, see moods.py)


def find_clap_python() -> str | None:
    """Interpreter with torch + transformers (env MUSICPLAYER_CLAP_PYTHON, else the CLAP_GUI virtualenvs)."""
    cands = [os.environ.get("MUSICPLAYER_CLAP_PYTHON")]
    home = Path.home() / ".venvs"
    cands += [str(home / "clap_gui" / "Scripts" / "python.exe"), str(home / "clap_gui_cpu" / "Scripts" / "python.exe")]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


MODEL_NAME = os.environ.get("CLAP_MODEL", "laion/clap-htsat-fused")


def hf_roots() -> list[Path]:
    """HuggingFace homes that may hold the CLAP weights, most specific first: MUSICPLAYER_HF_HOME, HF_HOME, the CLAP_GUI
    workspace next to this project, the user's default cache."""
    import sys
    cands = [os.environ.get("MUSICPLAYER_HF_HOME"), os.environ.get("HF_HOME")]
    # the CLAP_GUI project next to this one: look upwards from the source file and from the built exe
    for start in (Path(__file__).resolve(), Path(sys.executable).resolve()):
        for up in start.parents:
            p = up / "CLAP_GUI" / "workspace" / "hf_cache"
            if p.is_dir():
                cands.append(str(p))
                break
    cands.append(str(Path.home() / ".cache" / "huggingface"))
    out = []
    for c in cands:
        if c and Path(c) not in out:
            out.append(Path(c))
    return out


def _has_weights(root: Path) -> bool:
    snaps = root / "hub" / ("models--" + MODEL_NAME.replace("/", "--")) / "snapshots"
    try:
        return any(any(s.iterdir()) for s in snaps.iterdir())
    except OSError:
        return False


def hf_home() -> Path | None:
    """The first HuggingFace home that actually contains the CLAP weights."""
    return next((r for r in hf_roots() if _has_weights(r)), None)


def model_cached() -> bool:
    """True when the CLAP weights are in a local HuggingFace cache (the worker runs offline)."""
    return hf_home() is not None


def available() -> bool:
    """CLAP can be run now: a python with torch+transformers, the worker script and the weights in the cache."""
    return find_clap_python() is not None and WORKER.exists() and model_cached()


def unavailable_reason() -> str:
    if find_clap_python() is None:
        return "нет python с torch и transformers (MUSICPLAYER_CLAP_PYTHON или окружение CLAP_GUI)"
    if not model_cached():
        return (f"модель {MODEL_NAME} не найдена ни в одном кэше HuggingFace ({', '.join(str(r) for r in hf_roots())}); "
                "нужно скачать (около 1.2 ГБ) или указать папку в MUSICPLAYER_HF_HOME")
    return ""


def prompt_for(name: str, prompt: str | None) -> str:
    return (prompt or "").strip() or DEFAULT_PROMPTS.get(name) or f"{name.lower()} music"


class Worker:
    """A running clap_worker process in 'audio' or 'text' mode."""

    def __init__(self, mode: str) -> None:
        py = find_clap_python()
        if py is None:
            raise RuntimeError("Не найден python с torch и transformers для CLAP "
                               "(задайте MUSICPLAYER_CLAP_PYTHON или создайте окружение CLAP_GUI).")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        home = hf_home()
        if home is not None:
            env["HF_HOME"] = str(home)           # the weights may live outside the default cache
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen([py, str(WORKER), mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, creationflags=flags, bufsize=0)
        first = self._read()
        if not first or not first.get("ready"):
            self.close()
            raise RuntimeError("CLAP не запустился (модель laion/clap-htsat-fused должна быть в кэше HuggingFace).")
        self.info = first

    def _read(self) -> dict | None:
        line = self.proc.stdout.readline()
        while line and not line.strip().startswith(b"{"):    # skip library noise on stdout
            line = self.proc.stdout.readline()
        return json.loads(line.decode("utf-8")) if line else None

    def send(self, items: list[dict]) -> None:
        def feed() -> None:
            try:
                for it in items:
                    self.proc.stdin.write((json.dumps(it, ensure_ascii=False) + "\n").encode("utf-8"))
                self.proc.stdin.close()
            except OSError:
                pass
        threading.Thread(target=feed, daemon=True).start()

    def results(self, n: int):
        for _ in range(n):
            r = self._read()
            if r is None:
                return
            yield r

    def close(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass


def decode(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=np.float32).copy()


def embed_tracks(db: Database, ids: list[int] | None = None, ctx: JobContext | None = None) -> dict:
    """CLAP embeddings for tracks that have none (default: all)."""
    ctx = ctx or null_context()
    have = {r[0] for r in db.conn.execute("SELECT track_id FROM track_embeddings WHERE model=?", (EMB_MODEL,))}
    rows = db.conn.execute("SELECT id,path FROM tracks WHERE missing=0").fetchall()
    want = set(ids) if ids is not None else None
    todo = [{"id": r["id"], "path": r["path"]} for r in rows
            if r["id"] not in have and (want is None or r["id"] in want)]
    ctx.log(f"CLAP: {len(todo)} треков без эмбеддингов (модель грузится ~10 с)")
    ctx.progress(0, len(todo), "загрузка модели")
    if not todo:
        return {"embedded": 0, "failed": []}
    w = Worker("audio")
    ctx.log(f"CLAP на устройстве: {w.info.get('device')}")
    done, failed = 0, []
    try:
        w.send(todo)
        for r in w.results(len(todo)):
            ctx.check()
            if "emb" in r:
                db.set_embedding(r["id"], EMB_MODEL, base64.b64decode(r["emb"]))
            else:
                failed.append((r.get("id"), r.get("error", "")))
            done += 1
            ctx.progress(done, len(todo), f"{done}/{len(todo)}")
    finally:
        w.close()
    ctx.log(f"CLAP готов: {done - len(failed)} эмбеддингов, ошибок {len(failed)}")
    return {"embedded": done - len(failed), "failed": failed}


def text_embeddings(db: Database, cache_dir: str | Path, names: list[str] | None = None) -> dict[str, np.ndarray]:
    """Embeddings of the mood prompts (cached on disk by prompt text)."""
    cache_file = Path(cache_dir) / "clap_text.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    moods = {m["name"]: prompt_for(m["name"], m["prompt"]) for m in db.list_moods() if names is None or m["name"] in names}
    missing = {n: p for n, p in moods.items() if p not in cache}
    if missing:
        w = Worker("text")
        try:
            w.send([{"id": n, "text": p} for n, p in missing.items()])
            for r in w.results(len(missing)):
                if "emb" in r:
                    cache[missing[r["id"]]] = r["emb"]
        finally:
            w.close()
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {n: decode(cache[p]) for n, p in moods.items() if p in cache}


def _scores(db: Database, cache_dir: str | Path, ids: list[int]) -> tuple[list[int], list[str], np.ndarray]:
    """Probabilities over moods for tracks that have embeddings (CLAP logit scale 100).

    Calibrated: each mood's mean similarity over the whole library is subtracted first, otherwise a prompt
    that is generally "closer" to music (e.g. 'aggressive heavy') wins for most tracks."""
    t = text_embeddings(db, cache_dir)
    names = sorted(t)
    T = np.vstack([t[n] for n in names])
    allrows = db.conn.execute("SELECT track_id,vec FROM track_embeddings WHERE model=?", (EMB_MODEL,)).fetchall()
    if not allrows:
        return [], names, np.zeros((0, len(names)))
    index = {r[0]: i for i, r in enumerate(allrows)}
    E = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in allrows])
    C = E @ T.T
    C = C - C.mean(axis=0, keepdims=True)
    got = [i for i in ids if i in index]
    if not got:
        return [], names, np.zeros((0, len(names)))
    logits = 100.0 * C[[index[i] for i in got]]
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return got, names, p / p.sum(axis=1, keepdims=True)


def zero_shot(db: Database, cache_dir: str | Path, ids: list[int] | None = None, threshold: float = 0.3,
              only_unlabelled: bool = True, top_k: int = 2, ctx: JobContext | None = None) -> dict:
    """Mood suggestions straight from CLAP (source 'clap'); no training and no labels needed."""
    ctx = ctx or null_context()
    rows = db.search(limit=10**9, no_mood=only_unlabelled) if ids is None else [db.get_track(i) for i in ids]
    cand = [r["id"] for r in rows if r]
    got, names, P = _scores(db, cache_dir, cand)
    ctx.progress(0, len(got), "zero-shot")
    items = []
    for tid, p in zip(got, P):
        db.conn.execute("DELETE FROM track_moods WHERE track_id=? AND source='clap'", (tid,))
        order = np.argsort(-p)[:top_k]
        chosen = [j for j in order if p[j] >= threshold] or [int(order[0])]
        items += [(tid, names[j], float(p[j])) for j in chosen]
    db.suggest_moods(items, "clap")
    for tid in got:
        db.refresh_fts(tid)
    ctx.progress(len(got), len(got), "готово")
    ctx.log(f"CLAP zero-shot: {len(got)} треков, без эмбеддингов пропущено {len(cand) - len(got)}")
    return {"predicted": len(got), "suggestions": len(items), "skipped_no_embedding": len(cand) - len(got)}


def agreement(db: Database, cache_dir: str | Path) -> dict:
    """How often CLAP's top mood is among the moods YOU confirmed (zero-shot quality on your own labels).

    ``accuracy`` is the fair number: CLAP chooses only among the moods you actually use.
    ``accuracy_all_moods`` lets it choose among every mood in the list (near-synonyms such as calm / ambient /
    sleep then compete).  ``confusion`` maps your mood -> what CLAP said (restricted)."""
    labelled = db.mood_training_set()
    got, names, P = _scores(db, cache_dir, [tid for tid, _ in labelled])
    truth = dict(labelled)
    used = sorted({m for _, ms in labelled for m in ms if m in names})
    cols = [names.index(m) for m in used]
    n = len(got)
    hits_all = hits = 0
    per: dict[str, list[int]] = {}
    confusion: dict[str, dict[str, int]] = {}
    for tid, p in zip(got, P):
        hits_all += names[int(np.argmax(p))] in truth[tid]
        q = p[cols] / max(p[cols].sum(), 1e-9)
        top = used[int(np.argmax(q))]
        hits += top in truth[tid]
        for m in truth[tid]:
            d = per.setdefault(m, [0, 0])
            d[1] += 1
            d[0] += top == m
            confusion.setdefault(m, {}).setdefault(top, 0)
            confusion[m][top] += 1
    return {"tracks": n, "accuracy": round(hits / n, 3) if n else None,
            "accuracy_all_moods": round(hits_all / n, 3) if n else None, "chance": round(1 / len(used), 3) if used else None,
            "per_mood_recall": {m: round(a / b, 2) for m, (a, b) in per.items()}, "confusion": confusion,
            "moods_used": used}
