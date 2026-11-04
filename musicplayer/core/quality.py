"""Sound quality of the files: what to delete or download again.  Qt-free.

A few seconds from the middle of every track are decoded (ffmpeg) and looked at:

* the spectrum's cut-off: a lossy encoder drops the highs (128 kbit/s at ~16 kHz, 192 at ~19, 320 at ~20); a file
  that claims 320 kbit/s but stops at 16 kHz was re-encoded from a poor copy ("пережатый");
* the bitrate (mutagen); clipping (a share of samples at full scale - loud masters have some, a broken rip a lot);
* whether the file decodes at all, and to its end (a cut download).

Results go to ``track_quality`` (synced like the other analysis); ``check`` takes the tracks not yet looked at, so the
nightly run (ui) goes on where it stopped.  Tracks that only the server has are fetched for it (core/remotefile.py).
"""

from __future__ import annotations

import time

import numpy as np

from .db import Database

SCHEMA = ("CREATE TABLE IF NOT EXISTS track_quality(track_id INTEGER PRIMARY KEY, bitrate INTEGER, cutoff INTEGER, "
          "clip REAL, verdict TEXT, note TEXT, checked REAL)")
VERDICTS = {"ok": "Хорошее", "fake": "Пережатый", "low": "Низкое качество", "cut": "Обрезан", "broken": "Битый файл"}
BAD = ("broken", "cut", "fake", "low")
SR = 44100
N_FFT = 4096


def ensure(db: Database) -> None:
    db.conn.execute(SCHEMA)


def cutoff_hz(x: np.ndarray, sr: int = SR) -> int:
    """Highest frequency band still carrying sound: 250 Hz bands from 8 kHz up, louder than the mids - 55 dB."""
    if x.size < N_FFT * 4:
        return 0
    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::N_FFT // 2]
    loud = frames[np.sqrt((frames ** 2).mean(axis=1)) > 0.01]    # silence says nothing about the highs
    if len(loud) >= 4:
        frames = loud
    spec = (np.abs(np.fft.rfft(frames * np.hanning(N_FFT), axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    level = 10 * np.log10(spec + 1e-20)
    ref = np.median(level[(freqs > 500) & (freqs < 4000)])
    edges = np.arange(8000, sr / 2 - 250, 250)
    band = np.array([level[(freqs >= a) & (freqs < a + 250)].mean() for a in edges])
    above = np.flatnonzero(band > ref - 55)
    return int(edges[above[-1]] + 250) if above.size else 8000


def measure(path: str) -> dict:
    """{bitrate (kbit/s, 0 = unknown), duration, cutoff (Hz), clip (share), error (str or None), cut (bool)}."""
    import mutagen

    from .audio import decode_ffmpeg
    out = {"bitrate": 0, "duration": 0.0, "cutoff": 0, "clip": 0.0, "error": None, "cut": False}
    try:
        f = mutagen.File(path)
        if f is not None and f.info is not None:
            out["duration"] = float(getattr(f.info, "length", 0) or 0)
            out["bitrate"] = int(getattr(f.info, "bitrate", 0) or 0) // 1000
    except Exception as e:  # noqa: BLE001 - a file mutagen cannot read is what we are looking for
        out["error"] = f"теги не читаются: {e}"[:200]
    dur = out["duration"]
    parts = []
    for frac in (0.3, 0.6):
        try:
            got = decode_ffmpeg(path, SR, start=max(0.0, dur * frac - 4), seconds=8)
        except Exception as e:  # noqa: BLE001 - the first part decodes, a later one not: the download was cut
            out["error"] = str(e)[:200]
            got = None
        if got is None or got.size < SR:
            out["cut"] = bool(parts)
            break
        parts.append(got)
    if not parts:
        return out
    x = np.concatenate(parts)
    out["cutoff"] = cutoff_hz(x)
    out["clip"] = round(float((np.abs(x) > 0.995).mean()), 4)
    if dur > 20 and not out["cut"]:                        # the last seconds decode: the download is whole
        try:
            tail = decode_ffmpeg(path, SR, start=dur - 6, seconds=4)
            out["cut"] = tail.size < SR
        except Exception:  # noqa: BLE001
            out["cut"] = True
    return out


def judge(m: dict) -> tuple[str, str]:
    """(verdict, a note for the person) from ``measure``."""
    if m.get("error") and not m.get("cutoff"):
        return "broken", m["error"]
    if m.get("cut"):
        return "cut", "конец файла не читается — загрузка оборвалась"
    br, cut = m.get("bitrate") or 0, m.get("cutoff") or 0
    khz = f"{cut / 1000:.1f} кГц"
    if br >= 256 and cut and cut <= 16500:
        return "fake", f"{br} кбит/с, а звук обрезан на {khz} — пережат из плохой копии"
    if (br and br < 160) or (cut and cut <= 15500):
        return "low", f"{br or '?'} кбит/с, звук до {khz}"
    note = f"{br or '?'} кбит/с, звук до {khz}"
    if m.get("clip", 0) > 0.15:
        note += f", перегружен ({m['clip'] * 100:.0f}% отсчётов на пределе)"
    return "ok", note


def pending(db: Database, limit: int | None = None) -> list[dict]:
    """Tracks not checked yet, the newest first (new downloads matter most)."""
    ensure(db)
    sql = ("SELECT t.id, t.path FROM tracks t LEFT JOIN track_quality q ON q.track_id=t.id "
           "WHERE t.missing=0 AND q.track_id IS NULL ORDER BY t.added DESC, t.id DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.conn.execute(sql)]


def save(db: Database, tid: int, m: dict) -> str:
    verdict, note = judge(m)
    db.conn.execute("INSERT OR REPLACE INTO track_quality VALUES(?,?,?,?,?,?,?)",
                    (tid, m.get("bitrate") or 0, m.get("cutoff") or 0, m.get("clip") or 0.0, verdict, note, time.time()))
    return verdict


def check(db: Database, ctx=None, limit: int | None = None, until: float | None = None) -> dict:
    """Look at the tracks not checked yet (up to ``limit``, or until the time ``until``); returns counts per verdict."""
    from .remotefile import Paused, local_copy
    todo = pending(db, limit)
    counts = {k: 0 for k in VERDICTS}
    for i, t in enumerate(todo, 1):
        if ctx is not None:
            ctx.check()
            ctx.progress(i, len(todo), f"Качество звука: {i} из {len(todo)}")
        if until is not None and time.time() > until:
            break
        try:
            with local_copy(t["id"], t["path"]) as p:
                m = measure(p)
        except Paused:
            break
        except Exception as e:  # noqa: BLE001 - the server could not hand the file out: try again next night
            if ctx is not None:
                ctx.log(f"{t['path']}: {e}")
            continue
        counts[save(db, t["id"], m)] += 1
        db.conn.commit()
    counts["left"] = len(pending(db))
    return counts


def listing(db: Database, verdicts=BAD) -> list[dict]:
    """Tracks with a bad verdict, the worst first: track fields + bitrate, cutoff, clip, verdict, note."""
    ensure(db)
    q = ",".join("?" * len(verdicts))
    order = "CASE q.verdict " + " ".join(f"WHEN '{v}' THEN {i}" for i, v in enumerate(BAD)) + " ELSE 9 END"
    rows = db.conn.execute(
        f"SELECT q.track_id AS id, q.bitrate, q.cutoff, q.clip, q.verdict, q.note FROM track_quality q "
        f"JOIN tracks t ON t.id=q.track_id WHERE t.missing=0 AND q.verdict IN ({q}) "
        f"ORDER BY {order}, t.artist COLLATE NOCASE, t.album COLLATE NOCASE", list(verdicts)).fetchall()
    out = []
    for r in rows:
        t = db.get_track(r["id"])
        if t:
            out.append({**t, **{k: r[k] for k in ("bitrate", "cutoff", "clip", "verdict", "note")}})
    return out


def summary(db: Database) -> dict:
    ensure(db)
    got = dict(db.conn.execute("SELECT q.verdict, COUNT(*) FROM track_quality q JOIN tracks t ON t.id=q.track_id "
                               "WHERE t.missing=0 GROUP BY q.verdict").fetchall())
    return {"checked": sum(got.values()), "left": len(pending(db)), **{k: got.get(k, 0) for k in VERDICTS}}
