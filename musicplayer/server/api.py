"""HTTP API of the library server: the catalogue, covers and the audio files for phones and other computers.  Qt-free.

Every device plays on its own (the files are streamed as they are, with HTTP Range for seeking).  Each listener has an
own key (see config.py), so likes and listening history are personal: the OWNER's likes are the desktop app's
«Избранное» and the owner's plays are the app's plays; a GUEST's likes and plays live in ``remote_favorites`` /
``remote_plays`` and never touch the owner's.  Labels (moods, genres, «Моя волна» 👍/👎 on the model's guesses) teach
the shared mood model, so they need ``can_label`` (always true for the owner).

Access: header ``Authorization: Bearer <key>`` (or ``X-Walle-Key``), the ``wp_k`` cookie (set by opening
``/?k=<key>`` once - the link in the app's QR code), or ``?k=<key>`` on any /api URL.
The API is documented in docs/remote-api.md (it is also the contract for the mobile app).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from musicplayer import __version__
from musicplayer.core import moods as moodvocab
from musicplayer.core.flags import FLAGS
from musicplayer.core.genres import ERAS, GENRE_TREE
from musicplayer.core.db import TRACK_COLS, Database, weighted_order
from musicplayer.core.wave import CHARACTERS, Wave

log = logging.getLogger(__name__)
API_VERSION = 1          # raised when the API changes so that older clients break; additions keep it
API_MIN = 1              # the oldest API version a client may speak: older clients are told to update
MOBILE_APK = re.compile(r"WallePlayer[-_ ]?v?(\d+(?:\.\d+){1,3})\.apk$", re.I)


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def latest_mobile(folder: Path) -> dict | None:
    """The newest mobile app the owner put into <data>/mobile (WallePlayer-1.2.0.apk): phones offer to update to it."""
    try:
        found = [(m.group(1), f) for f in folder.iterdir() if (m := MOBILE_APK.search(f.name))]
    except OSError:
        return None
    if not found:
        return None
    ver, f = max(found, key=lambda x: version_key(x[0]))
    return {"version": ver, "file": f.name, "url": f"/api/app/{f.name}", "size": f.stat().st_size}
COOKIE = "wp_k"
WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_LIMIT = 500
FAIL_WINDOW, FAIL_MAX = 600.0, 10          # wrong keys per address per 10 minutes before a ban (fail2ban-like)
BAN_BASE, BAN_FACTOR, BAN_MAX = 3600.0, 4, 7 * 86400   # 1 h, then 4 h, 16 h, ... at most a week
STRIKE_MEMORY = 30 * 86400                 # a repeat ban within 30 days is longer
CLIENT_TTL = 120.0                          # a device counts as connected this long after its last request
JOIN_TTL = 600.0                            # an access request waits this long for the owner's answer
JOIN_PER_ADDRESS, JOIN_MAX = 3, 20          # pending requests per address / in all (against spam)
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
               ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".wma": "audio/x-ms-wma"}
ORDERS = {"artist", "title", "album", "year", "play_count", "last_played", "added", "duration", "random", "rare_random"}
ALBUM_SEP = "\x1f"
# the desktop labelling chips (ui/pages/library.py): languages, vocals, genre family colours
LANGS = [("ru", "Русский"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어"), ("fr", "Français"), ("other", "Другой")]
VOCALS = [("voc", "С вокалом"), ("inst", "Инструментал")]
GENRE_COLOURS = {"Rock": "#E8A87C", "Punk": "#F28B82", "Metal": "#AEB8C6", "Electronic": "#7FD6E0", "Hip-hop": "#C5A8F5",
                 "Pop": "#F5A9C4", "Jazz": "#C9A27A", "Soundtrack": "#D9BE95", "Oriental": "#E0C27A"}
GUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_favorites(user TEXT, kind TEXT, key TEXT, added REAL, PRIMARY KEY(user, kind, key));
CREATE TABLE IF NOT EXISTS remote_plays(user TEXT, track_id INTEGER, played_at REAL, listened REAL);
CREATE INDEX IF NOT EXISTS remote_plays_user ON remote_plays(user, played_at);
CREATE TABLE IF NOT EXISTS remote_play_uids(user TEXT, uid TEXT, at REAL, PRIMARY KEY(user, uid));
"""
THUMB_SIZES = (96, 160, 320, 512)            # cover thumbnails are made in these sizes only (a small disk cache)


def rev(items) -> str:
    """A short fingerprint of a list: a client keeps it and downloads the list again only when it changes."""
    return hashlib.sha1(repr(list(items)).encode()).hexdigest()[:12]


def album_key(artist: str, album: str) -> str:
    """Favourites key of an album, the same as in the desktop app."""
    return f"{artist}{ALBUM_SEP}{album}"


def device_name(ua: str) -> str:
    ua = ua or ""
    for key, name in (("WallePlayerApp", "Приложение"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                      ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux"), ("Dart", "Приложение")):
        if key in ua:
            return name
    return "Устройство"


class Library:
    """The server's own connection to the catalogue (WAL: the desktop app keeps writing) behind a lock, because
    Starlette runs the request handlers on a thread pool.  Personal data (likes, plays) is looked up per listener."""

    def __init__(self, db_path: str | Path, covers: str | Path, on_change: Callable[[list[int]], None] | None = None):
        self.db = Database(db_path)
        self.db.conn.executescript(GUEST_SCHEMA)
        self.covers = Path(covers)
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda ids: None)
        self.models = Path(db_path).parent / "models"
        self._model, self._model_mtime = None, None

    def __call__(self, fn: Callable[[Database], Any]) -> Any:
        with self.lock:
            return fn(self.db)

    def changed(self, ids: list[int]) -> None:
        try:
            self.on_change(ids)
        except Exception:  # noqa: BLE001 - the desktop app's refresh must never break a request
            log.exception("on_change failed")

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def mood_probs(self, tid: int) -> dict[str, float]:
        """The current mood model's probability for every mood of a track (empty without a model or analysis)."""
        from musicplayer.core import mood
        p = mood.model_path(self.models)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return {}
        with self.lock:
            try:
                if self._model is None or mtime != self._model_mtime:    # retrained in the app: reload
                    self._model, self._model_mtime = mood.load_model(self.models), mtime
                return mood.track_probs(self.db, self._model, tid) if self._model is not None else {}
            except Exception:  # noqa: BLE001 - a model from another version must not break the player
                log.exception("mood probabilities failed")
                return {}

    # ---- personal data ------------------------------------------------------
    def liked(self, user: dict, kind: str = "track") -> list[str]:
        """Keys the listener liked, newest first."""
        with self.lock:
            if user.get("owner"):
                return self.db.favorites(kind)
            return [r[0] for r in self.db.conn.execute(
                "SELECT key FROM remote_favorites WHERE user=? AND kind=? ORDER BY added DESC", (user["id"], kind))]

    def set_like(self, user: dict, kind: str, key: str, on: bool) -> None:
        with self.lock:
            if user.get("owner"):
                self.db.set_favorite(kind, key, on)
            elif on:
                self.db.conn.execute("INSERT OR IGNORE INTO remote_favorites VALUES(?,?,?,?)",
                                     (user["id"], kind, key, time.time()))
            else:
                self.db.conn.execute("DELETE FROM remote_favorites WHERE user=? AND kind=? AND key=?",
                                     (user["id"], kind, key))

    def record_play(self, user: dict, tid: int, listened: float, when: float | None, uid: str | None = None) -> bool:
        """False when ``uid`` was already recorded (the client re-sent a play whose answer got lost)."""
        with self.lock:
            if uid:
                cur = self.db.conn.execute("INSERT OR IGNORE INTO remote_play_uids VALUES(?,?,?)",
                                           (user["id"], uid, time.time()))
                if cur.rowcount == 0:
                    return False
                if secrets.randbelow(200) == 0:                       # now and then forget uids older than 60 days
                    self.db.conn.execute("DELETE FROM remote_play_uids WHERE at<?", (time.time() - 60 * 86400,))
            if user.get("owner"):
                self.db.record_play(tid, listened, when)
            else:
                self.db.conn.execute("INSERT INTO remote_plays VALUES(?,?,?,?)",
                                     (user["id"], tid, when or time.time(), listened))
            return True

    def recent_ids(self, user: dict, limit: int) -> list[int]:
        with self.lock:
            if user.get("owner"):
                q, args = "SELECT track_id FROM plays GROUP BY track_id ORDER BY MAX(played_at) DESC LIMIT ?", (limit,)
            else:
                q, args = ("SELECT track_id FROM remote_plays WHERE user=? GROUP BY track_id ORDER BY MAX(played_at) "
                           "DESC LIMIT ?", (user["id"], limit))
            return [r[0] for r in self.db.conn.execute(q, args)]

    def guest_plays(self, user: dict) -> dict[int, tuple[int, float]] | None:
        """{track id: (count, last played)} of a guest; None for the owner (his plays are the catalogue's)."""
        if user.get("owner"):
            return None
        with self.lock:
            return {r[0]: (r[1], r[2]) for r in self.db.conn.execute(
                "SELECT track_id, COUNT(*), MAX(played_at) FROM remote_plays WHERE user=? GROUP BY track_id", (user["id"],))}

    def personal(self, user: dict):
        """A function track -> JSON with this listener's likes (and, for a guest, own play counts)."""
        liked = set(self.liked(user))
        plays: dict[int, tuple[int, float]] | None = None
        if not user.get("owner"):
            with self.lock:
                plays = {r[0]: (r[1], r[2]) for r in self.db.conn.execute(
                    "SELECT track_id, COUNT(*), MAX(played_at) FROM remote_plays WHERE user=? GROUP BY track_id",
                    (user["id"],))}

        def to_json(t: dict) -> dict:
            j = track_json(t)
            j["liked"] = str(t["id"]) in liked
            if plays is not None:
                j["plays"], j["last_played"] = plays.get(t["id"], (0, None))
            if "wave_kind" in t:
                j["wave"] = {"kind": t["wave_kind"], "moods": t.get("wave_moods", []), "conf": t.get("wave_conf")}
            return j
        return to_json


def track_json(t: dict) -> dict:
    key = t.get("cover_key")
    return {"id": t["id"], "title": t["title"] or Path(t["path"]).stem, "artist": t["artist"] or "",
            "albumartist": t.get("albumartist") or "", "album": t["album"] or "", "year": t["year"],
            "track_no": t.get("track_no"), "duration": t["duration"] or 0, "genres": t.get("genres", []),
            "moods": t.get("moods", []), "feat": t.get("feat", []),
            # moods the model assigned by itself (source 'auto') with its confidence; the others are the user's
            "mood_conf": {m: c for m, c in (t.get("mood_conf") or {}).items()
                          if (t.get("mood_sources") or {}).get(m) == "auto" and c is not None},
            "suggested": [{"mood": s["mood"], "conf": s["confidence"], "source": s["source"]}
                          for s in t.get("mood_suggestions", [])],
            "flags": t.get("flags", []), "lang": t.get("lang"), "vocal": t.get("vocal"), "era": t.get("era"),
            "plays": t.get("play_count") or 0, "last_played": t.get("last_played"), "added": t.get("added"),
            "format": Path(t["path"]).suffix.lower().lstrip("."), "liked": False,
            "cover": f"/api/cover/{key}" if key else None, "stream": f"/api/stream/{t['id']}"}


class Access:
    """Keys of the listeners, brute-force throttle and the list of connected devices (shown in the app's dialog)."""

    def __init__(self, users: list[dict], save: Callable[[], None] | None = None,
                 on_join: Callable[[dict], None] | None = None, ban_file: Path | None = None,
                 on_ban: Callable[[dict], None] | None = None) -> None:
        """``save`` persists the listeners after a change made through the API (a listener renamed himself).
        ``on_join(request)``: a new device asks for access (the desktop app shows it to the owner); None - requests
        are not accepted (the headless server has nobody to answer them)."""
        self.users = users
        self.save = save or (lambda: None)
        self.on_join = on_join
        self.fails: dict[str, list[float]] = {}
        self.clients: dict[str, dict] = {}
        self.joins: dict[str, dict] = {}     # id -> {id, secret, name, device, address, created, status, user}
        self.lock = threading.Lock()
        self.loopback_key: str | None = None # set while the VPS tunnel runs: its visitors arrive from localhost
        self.addresses: Callable[[], dict] = lambda: {}   # {"lan": [...], "internet": [...]} - set by the app
        self.ban_file = Path(ban_file) if ban_file else None
        self.on_ban = on_ban
        self.bans: dict[str, dict] = {}      # ban key -> {until, strikes, address, since}
        if self.ban_file is not None:
            try:
                self.bans = json.loads(self.ban_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.bans = {}

    # ---- fail2ban: wrong keys from an address get it banned for a while, longer each time ------------------------
    @staticmethod
    def ban_key(addr: str) -> str | None:
        """What is banned: the IPv4 address, or the whole IPv6 /64 (a home network hands out many addresses from it).
        None for the computer itself (it is never banned)."""
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return None
        if ip.is_loopback:
            return None
        if ip.version == 6:
            if ip.ipv4_mapped:
                return str(ip.ipv4_mapped)
            return str(ipaddress.ip_network(f"{ip}/64", strict=False))
        return str(ip)

    def _key(self, addr: str) -> str | None:
        """The ban key of a request's address; visitors through the VPS tunnel share one key (their real address
        does not reach the computer) and are banned only briefly, so one guesser cannot lock everyone out for long."""
        key = self.ban_key(addr)
        if key is None and self.loopback_key:
            try:
                if ipaddress.ip_address(addr).is_loopback:
                    return self.loopback_key
            except ValueError:
                pass
        return key

    def _save_bans(self) -> None:
        if self.ban_file is None:
            return
        try:
            tmp = self.ban_file.with_name(self.ban_file.name + ".tmp")
            tmp.write_text(json.dumps(self.bans, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.ban_file)
        except OSError:
            log.exception("bans not saved")

    def banned(self) -> list[dict]:
        """Active bans for the app's dialog, the newest first."""
        now = time.time()
        with self.lock:
            return sorted(({"key": k, **b} for k, b in self.bans.items() if b["until"] > now), key=lambda b: -b["since"])

    def unban(self, key: str | None = None) -> None:
        """Lifts one ban (or all): the owner who mistyped his key lets himself back in."""
        with self.lock:
            for k in ([key] if key else list(self.bans)):
                if k in self.bans:
                    self.bans[k]["until"] = 0
                self.fails.pop(k, None)
            self._save_bans()

    # ---- access requests from new devices: the owner answers in the desktop app ---------------------------------
    def request_join(self, name: str, device: str, address: str) -> dict | str:
        """A new pending request, or the reason why not (str)."""
        now = time.time()
        with self.lock:
            for k in [k for k, j in self.joins.items() if now - j["created"] > JOIN_TTL]:
                del self.joins[k]
            pending = [j for j in self.joins.values() if j["status"] == "pending"]
            if len(pending) >= JOIN_MAX or sum(j["address"] == address for j in pending) >= JOIN_PER_ADDRESS:
                return "too many requests, try again later"
            req = {"id": secrets.token_hex(6), "secret": secrets.token_urlsafe(16), "name": name, "device": device,
                   "address": address, "created": now, "status": "pending", "user": None}
            self.joins[req["id"]] = req
            return dict(req)

    def answer_join(self, req_id: str, user: dict | None) -> bool:
        """The owner's answer: the listener the device becomes (a new one or an existing one), or None - refused."""
        with self.lock:
            req = self.joins.get(req_id)
            if req is None or req["status"] != "pending":
                return False
            req["status"], req["user"] = ("approved", user) if user is not None else ("denied", None)
            return True

    def join_state(self, req_id: str, secret: str) -> dict | None:
        with self.lock:
            req = self.joins.get(req_id)
            if req is None or not hmac.compare_digest(secret.encode(), req["secret"].encode()):
                return None
            if req["status"] == "pending" and time.time() - req["created"] > JOIN_TTL:
                req["status"] = "expired"
            if req["status"] in ("approved", "denied", "expired"):
                self.joins.pop(req_id, None)                  # the key is handed out once
            return dict(req)

    @staticmethod
    def address(request: Request) -> str:
        host = request.client.host if request.client else "?"
        if host in ("127.0.0.1", "::1"):        # behind the tunnel every request comes from localhost
            host = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", host).split(",")[0].strip()
        return host

    @staticmethod
    def presented(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (request.headers.get("x-walle-key") or request.cookies.get(COOKIE)
                or request.query_params.get("k") or "")

    def user_for(self, key: str) -> dict | None:
        if not key:
            return None
        found = None
        for u in list(self.users):              # compare with every key: the time does not tell which one matched
            if hmac.compare_digest(key.encode(), str(u["token"]).encode()):
                found = u
        return found

    def blocked(self, addr: str) -> bool:
        key = self._key(addr)
        if key is None:
            return False
        with self.lock:
            b = self.bans.get(key)
            return bool(b and b["until"] > time.time())

    def failed(self, addr: str) -> None:
        """A wrong key (or another failed attempt): FAIL_MAX of them within FAIL_WINDOW ban the address."""
        key = self._key(addr)
        if key is None:
            return
        now = time.time()
        info = None
        with self.lock:
            recent = [t for t in self.fails.get(key, []) if now - t < FAIL_WINDOW] + [now]
            self.fails[key] = recent
            if len(recent) >= FAIL_MAX:
                old = self.bans.get(key)
                strikes = (old["strikes"] if old and now - old.get("since", 0) < STRIKE_MEMORY else 0) + 1
                until = now + min(BAN_BASE * BAN_FACTOR ** (strikes - 1), BAN_MAX)
                if key == self.loopback_key:                 # everybody through the VPS: only ten minutes
                    until = now + 600
                self.bans[key] = {"until": until, "strikes": strikes, "address": addr, "since": now}
                self.fails.pop(key, None)
                self._save_bans()
                info = {"key": key, **self.bans[key]}
        if info:
            log.warning("banned %s until %s (strike %d): too many wrong keys", key,
                        time.strftime("%d.%m %H:%M", time.localtime(info["until"])), info["strikes"])
            if self.on_ban:
                try:
                    self.on_ban(info)
                except Exception:  # noqa: BLE001
                    log.exception("on_ban failed")

    def seen(self, request: Request, addr: str, user: dict, track: dict | None = None) -> None:
        ua = request.headers.get("user-agent", "")
        key = f"{user['id']}|{addr}|{ua}"
        with self.lock:
            c = self.clients.setdefault(key, {"user": user["name"], "address": addr, "device": device_name(ua),
                                              "track": None})
            c["last"] = time.time()
            c["user"] = user["name"]
            if track is not None:
                c["track"] = f"{track['artist']} — {track['title']}" if track.get("artist") else track["title"]

    def connected(self) -> list[dict]:
        now = time.time()
        with self.lock:
            return sorted((dict(c) for c in self.clients.values() if now - c["last"] < CLIENT_TTL),
                          key=lambda c: -c["last"])


def _int(v: Any, default: int | None = None, lo: int | None = None, hi: int | None = None) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


class WebFiles(StaticFiles):
    """The web client's files: always revalidated (ETag), so a phone never keeps an old app.js after an update."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _set_key_cookie(request: Request, resp: Response, key: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(COOKIE, key, max_age=400 * 86400, httponly=True, samesite="lax", secure=secure)


def build_app(lib: Library, access: Access, info: dict | None = None) -> Starlette:
    """``info``: extra fields for /api/info (server name...)."""
    info = info or {}

    def threaded(fn, label: bool = False):
        """Key check, then the blocking handler fn(request, user, body) on the thread pool.
        ``label``: the handler changes labels (the mood model's training data) - needs ``can_label``."""
        from starlette.concurrency import run_in_threadpool

        async def handler(request: Request):
            addr = Access.address(request)
            if access.blocked(addr):
                return _err(429, "too many wrong keys, try again later")
            user = access.user_for(Access.presented(request))
            if user is None:
                access.failed(addr)
                return _err(401, "access key required")
            access.seen(request, addr, user)
            if label and not user.get("can_label"):
                return _err(403, "labelling is not allowed for this listener")
            body = None
            if request.method == "POST":
                try:
                    body = await request.json()
                except ValueError:
                    return _err(400, "JSON body expected")
                if not isinstance(body, dict):
                    return _err(400, "JSON object expected")
            return await run_in_threadpool(fn, request, user, body)
        return handler

    # ---- no key needed ------------------------------------------------------
    def versions() -> dict:
        """What clients compare: the server's version and API range, the newest mobile app it can hand out."""
        return {"version": __version__, "api": API_VERSION, "api_min": API_MIN,
                "mobile": latest_mobile(lib.models.parent / "mobile")}

    async def ping(request: Request):
        user = access.user_for(Access.presented(request))
        return JSONResponse({"app": "WallePlayer", **versions(), "name": info.get("name", ""),
                             "library": info.get("library", ""),
                             "authorized": user is not None, "user": user["name"] if user else None})

    async def index(request: Request):
        key = request.query_params.get("k")
        if key is not None:                    # the QR link: remember the key in a cookie, drop it from the URL
            addr = Access.address(request)
            if not access.blocked(addr) and access.user_for(key):
                resp = RedirectResponse("/", status_code=303)
                _set_key_cookie(request, resp, key)
                return resp
            access.failed(addr)
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def login(request: Request):
        addr = Access.address(request)
        if access.blocked(addr):
            return _err(429, "too many wrong keys, try again later")
        try:
            key = str((await request.json()).get("key", "")).strip()
        except (ValueError, AttributeError):
            key = ""
        user = access.user_for(key)
        if user is None:
            access.failed(addr)
            return _err(401, "wrong key")
        resp = JSONResponse({"ok": True, "user": user["name"]})
        _set_key_cookie(request, resp, key)
        return resp

    async def join(request: Request):
        """A device without a key asks for access under a name; the owner answers in the desktop app."""
        if access.on_join is None:
            return _err(403, "access requests are off: ask the owner for a QR code")
        try:
            body = await request.json()
            name = " ".join(str(body.get("name") or "").split())[:40]
        except (ValueError, AttributeError):
            name = ""
        if not name:
            return _err(400, "name required")
        addr = Access.address(request)
        req = access.request_join(name, device_name(request.headers.get("user-agent", "")), addr)
        if isinstance(req, str):
            access.failed(addr)                                       # request spam counts as a failed attempt
            return _err(429, req)
        try:
            access.on_join({k: v for k, v in req.items() if k != "secret"})
        except Exception:  # noqa: BLE001
            log.exception("join request not shown")
        return JSONResponse({"id": req["id"], "secret": req["secret"], "wait": JOIN_TTL})

    async def join_state(request: Request):
        st = access.join_state(request.path_params["rid"], request.query_params.get("s", ""))
        if st is None:
            access.failed(Access.address(request))                   # guessing request secrets counts too
            return _err(404, "no such request")
        out: dict[str, Any] = {"status": st["status"]}
        if st["status"] != "approved":
            return JSONResponse(out)
        user = st["user"]
        out |= {"key": user["token"], "user": user["name"]}           # the app keeps the key; the browser gets a cookie
        resp = JSONResponse(out)
        _set_key_cookie(request, resp, user["token"])
        return resp

    async def logout(request: Request):
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ---- catalogue ----------------------------------------------------------
    def me(user: dict) -> dict:
        return {"name": user["name"], "owner": bool(user.get("owner")), "can_label": bool(user.get("can_label"))}

    def api_me(request: Request, user: dict, _b):
        return JSONResponse(me(user))

    def api_rename(request: Request, user: dict, body: dict):
        """The listener changes his own name (shown in the app's dialog and on his devices)."""
        name = " ".join(str(body.get("name") or "").split())[:40]
        if not name:
            return _err(400, "name required")
        user["name"] = name
        try:
            access.save()
        except Exception:  # noqa: BLE001 - the name is changed in memory anyway
            log.exception("listeners not saved")
        return JSONResponse(me(user))

    def api_info(request: Request, user: dict, _b):
        def go(db: Database):
            counts = {m["name"]: m["n"] for m in db.list_moods()}
            moods = [{"name": n, "n": counts.get(n, 0), "group": moodvocab.group_of(n),
                      "color": moodvocab.style_of(n)[0], "glyph": moodvocab.style_of(n)[1]}
                     for n in moodvocab.ordered(list(counts))]
            total = db.conn.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
            return {"app": "WallePlayer", **versions(), **info, "tracks": total,
                    "user": me(user), "addresses": access.addresses(), "moods": moods, "mood_groups": moodvocab.GROUPS,
                    "characters": [{"key": k, "name": v} for k, v in CHARACTERS.items()],
                    "genres": [{"name": g["name"], "n": g["n"]} for g in db.list_values("genre")[:60]],
                    "labels": {"genre_tree": [{"name": f, "color": GENRE_COLOURS.get(f, "#C8CDD3"), "subs": subs}
                                              for f, subs in GENRE_TREE.items()],
                               "eras": ERAS, "flags": [{"key": k, "name": v} for k, v in FLAGS.items()],
                               "langs": [{"key": k, "name": v} for k, v in LANGS],
                               "vocals": [{"key": k, "name": v} for k, v in VOCALS]},
                    "playlists": len(db.list_playlists())}
        return JSONResponse(lib(go))

    def api_tracks(request: Request, user: dict, _b):
        q = request.query_params
        order = q.get("order", "artist")
        kw: dict[str, Any] = {
            "text": q.get("q", ""), "genre": q.get("genre") or None, "mood": q.get("mood") or None,
            "artist": q.get("artist") or None, "album": q.get("album") or None,
            "year_from": _int(q.get("year")), "year_to": _int(q.get("year")),
            "order": order if order in ORDERS else "artist", "desc": q.get("desc") in ("1", "true"),
            "limit": _int(q.get("limit"), 100, 1, MAX_LIMIT), "offset": _int(q.get("offset"), 0, 0)}
        if q.get("mood_status") in ("confirmed", "suggested", "any"):
            kw["mood_status"] = q["mood_status"]
        if q.get("not_played_days"):
            kw["not_played_days"] = _int(q["not_played_days"], None, 0)
        plays = lib.guest_plays(user)
        if plays is not None and (kw["order"] in ("play_count", "last_played", "rare_random") or "not_played_days" in kw):
            rows = guest_order(plays, kw)                     # a guest: by HIS plays, not the catalogue's
        else:
            rows = lib(lambda db: db.search(**kw))
        to_json = lib.personal(user)
        return JSONResponse({"items": [to_json(t) for t in rows], "offset": kw["offset"],
                             "more": len(rows) == kw["limit"]})

    def guest_order(plays: dict, kw: dict) -> list[dict]:
        k = {key: v for key, v in kw.items() if key not in ("order", "desc", "limit", "offset", "not_played_days")}
        rows = lib(lambda db: db.search(**k, order="artist", limit=10**9))
        for r in rows:
            r["play_count"], r["last_played"] = plays.get(r["id"], (0, None))
        if kw.get("not_played_days") is not None:
            edge = time.time() - kw["not_played_days"] * 86400
            rows = [r for r in rows if not r["last_played"] or r["last_played"] < edge]
        if kw["order"] == "rare_random":
            rows = weighted_order(rows)
        elif kw["order"] == "play_count":
            rows.sort(key=lambda r: r["play_count"], reverse=kw["desc"])
        elif kw["order"] == "last_played":
            rows.sort(key=lambda r: r["last_played"] or 0, reverse=kw["desc"])
        return rows[kw["offset"]:kw["offset"] + kw["limit"]]

    def api_lookup(request: Request, user: dict, body: dict):
        """Fresh data of many tracks at once (moods, genres, likes...): a client refreshes the tracks it saved
        on the device without downloading the audio again.  Unknown or missing ids are left out."""
        ids = [i for i in (_int(x) for x in (body.get("ids") or [])[:2000]) if i is not None]

        def go(db: Database):
            rows = []
            for k in range(0, len(ids), 500):
                chunk = ids[k:k + 500]
                rows += [dict(r) for r in db.conn.execute(
                    f"SELECT {TRACK_COLS} FROM tracks WHERE missing=0 AND id IN ({','.join('?' * len(chunk))})", chunk)]
            return db._enrich(rows)
        to_json = lib.personal(user)
        return JSONResponse({"items": [to_json(t) for t in lib(go)]})

    def api_track(request: Request, user: dict, _b):
        t = lib(lambda db: db.get_track(_int(request.path_params["tid"], 0)))
        return JSONResponse(lib.personal(user)(t)) if t else _err(404, "no such track")

    def api_probs(request: Request, user: dict, _b):
        tid = _int(request.path_params["tid"], 0)
        if not lib(lambda db: db.get_track(tid)):
            return _err(404, "no such track")
        return JSONResponse({"probs": {m: round(p, 3) for m, p in lib.mood_probs(tid).items()}})

    def api_artists(request: Request, user: dict, _b):
        liked = set(lib.liked(user, "artist"))
        return JSONResponse({"items": [r | {"liked": r["name"] in liked} for r in lib(lambda db: db.list_values("artist"))]})

    def api_albums(request: Request, user: dict, _b):
        rows = lib(lambda db: db.list_values("album"))
        liked = set(lib.liked(user, "album"))
        return JSONResponse({"items": [{"name": r["name"], "artist": r["artist"], "year": r["year"], "n": r["n"],
                                        "liked": album_key(r["artist"] or "", r["name"]) in liked,
                                        "cover": f"/api/cover/{r['cover_key']}" if r["cover_key"] else None}
                                       for r in rows]})

    def api_genres(request: Request, user: dict, _b):
        return JSONResponse({"items": lib(lambda db: db.list_values("genre"))})

    def api_playlists(request: Request, user: dict, _b):
        rows = lib(lambda db: db.list_playlists())
        return JSONResponse({"items": [{"id": p["id"], "name": p["name"], "smart": bool(p["rules"]), "n": p["count"]}
                                       for p in rows]})

    def api_playlist(request: Request, user: dict, _b):
        pid = _int(request.path_params["pid"], 0)
        p = lib(lambda db: db.playlist_row(pid))
        if p is None:
            return _err(404, "no such playlist")
        rows = lib(lambda db: db.playlist_tracks(pid))
        to_json = lib.personal(user)
        return JSONResponse({"id": pid, "name": p["name"], "smart": bool(p["rules"]),
                             "items": [to_json(t) for t in rows], "rev": rev([p["name"], [t["id"] for t in rows]])})

    def api_recent(request: Request, user: dict, _b):
        ids = lib.recent_ids(user, _int(request.query_params.get("limit"), 50, 1, MAX_LIMIT))
        rows = {t["id"]: t for t in lib(lambda db: [db.get_track(i) for i in ids]) if t}
        to_json = lib.personal(user)
        return JSONResponse({"items": [to_json(rows[i]) for i in ids if i in rows]})

    # ---- files --------------------------------------------------------------
    def api_stream(request: Request, user: dict, _b):
        t = lib(lambda db: db.get_track(_int(request.path_params["tid"], 0)))
        if not t:
            return _err(404, "no such track")
        path = Path(t["path"])
        if not path.is_file():
            return _err(410, "the file is not available")
        if request.headers.get("range", "bytes=0-").startswith("bytes=0-"):   # a new play, not a seek
            access.seen(request, Access.address(request), user, t)
        return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                            headers={"Cache-Control": "private, max-age=86400"})

    def api_cover(request: Request, user: dict, _b):
        key = request.path_params["key"]
        if not re.fullmatch(r"[0-9a-fA-F]{6,64}", key):
            return _err(404, "no such cover")
        f = lib.covers / f"{key}.jpg"
        if not f.is_file():
            return _err(404, "no such cover")
        want = _int(request.query_params.get("size"))
        if want:                                                    # a thumbnail for lists: less traffic and memory
            size = next((s for s in THUMB_SIZES if s >= want), THUMB_SIZES[-1])
            thumb = lib.covers / "thumbs" / f"{key}_{size}.jpg"
            if not thumb.is_file():
                try:
                    from PIL import Image
                    thumb.parent.mkdir(exist_ok=True)
                    with Image.open(f) as im:
                        im = im.convert("RGB")
                        im.thumbnail((size, size), Image.LANCZOS)
                        im.save(thumb, "JPEG", quality=85)
                except Exception:  # noqa: BLE001 - a broken picture: give the original
                    log.exception("cover thumbnail failed")
                    thumb = f
            f = thumb
        return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=2592000, immutable"})

    # ---- personal: plays, likes --------------------------------------------------
    def api_plays(request: Request, user: dict, body: dict):
        tid = _int(body.get("id"))
        listened = float(body.get("listened") or 0)
        when = body.get("at")
        when = float(when) if isinstance(when, (int, float)) and when <= time.time() + 60 else None
        uid = str(body.get("uid") or "")[:64] or None             # repeats of the same play are ignored
        if not lib(lambda db: db.get_track(tid or 0)):
            return _err(404, "no such track")
        if not lib.record_play(user, tid, listened, when, uid):
            return JSONResponse({"ok": True, "duplicate": True})
        if user.get("owner"):
            lib.changed([tid])
        return JSONResponse({"ok": True})

    def api_set_labels(request: Request, user: dict, body: dict):
        """All labels of a track at once, as the desktop labelling feed; only the keys present are changed:
        moods (+ reject), genres, flags, lang, vocal ('voc' | 'inst'), era (decade).  null clears a single value."""
        tid = _int(request.path_params["tid"], 0)
        clean = lambda xs: list(dict.fromkeys(str(x).strip() for x in xs or [] if str(x).strip()))  # noqa: E731
        if "vocal" in body and body["vocal"] not in (None, "voc", "inst"):
            return _err(400, "vocal must be voc, inst or null")
        if "flags" in body and not set(clean(body["flags"])) <= set(FLAGS):
            return _err(400, "unknown flag")

        def go(db: Database):
            if not db.get_track(tid):
                return None
            if "moods" in body:
                db.set_moods([tid], clean(body["moods"]), "set")
            if body.get("reject"):
                db.reject_moods(tid, [m for m in clean(body["reject"]) if m not in clean(body.get("moods"))])
            if "genres" in body:
                db.set_genres([tid], clean(body["genres"]), "set")
            if "flags" in body:
                db.set_flags([tid], clean(body["flags"]), "set")
            if "lang" in body:
                db.set_language([tid], str(body["lang"]) if body["lang"] else None)
            if "vocal" in body:
                db.set_vocal([tid], body["vocal"])
            if "era" in body:
                db.set_era([tid], _int(body["era"]))
            return db.get_track(tid)
        t = lib(go)
        if t is None:
            return _err(404, "no such track")
        lib.changed([tid])
        return JSONResponse(lib.personal(user)(t))

    def api_favorites(request: Request, user: dict, _b):
        """The listener's liked tracks (newest first), albums and artists."""
        keys = lib.liked(user, "track")
        tracks = [t for t in lib(lambda db: [db.get_track(int(k)) for k in keys if k.isdigit()]) if t]
        albums = [dict(zip(("artist", "name"), k.split(ALBUM_SEP, 1))) for k in lib.liked(user, "album") if ALBUM_SEP in k]
        to_json = lib.personal(user)
        artists = lib.liked(user, "artist")
        return JSONResponse({"tracks": [to_json(t) for t in tracks], "albums": albums, "artists": artists,
                             "rev": rev([keys, [list(a.values()) for a in albums], artists])})

    def api_like(request: Request, user: dict, body: dict):
        """{kind: track|album|artist, id (track) | artist+album (album) | name (artist), on: bool}"""
        kind = body.get("kind", "track")
        if kind == "track":
            tid = _int(body.get("id"))
            key = str(tid) if tid is not None else ""
        elif kind == "album":
            key = album_key(str(body.get("artist") or ""), str(body.get("album") or "")) if body.get("album") else ""
        elif kind == "artist":
            key = str(body.get("name") or "")
        else:
            return _err(400, "kind must be track, album or artist")
        if not key:
            return _err(400, "what to like is missing")
        on = bool(body.get("on", True))
        lib.set_like(user, kind, key, on)
        if kind == "track" and user.get("owner"):
            lib.changed([int(key)])
        return JSONResponse({"kind": kind, "key": key, "liked": on})

    # ---- «Моя волна» and labels ------------------------------------------------------
    def api_wave_next(request: Request, user: dict, body: dict):
        moods = [str(m) for m in body.get("moods") or []]
        genres = [str(g) for g in body.get("genres") or []]
        character = str(body.get("character") or "mixed")
        exclude = {i for i in (_int(x) for x in body.get("exclude") or []) if i is not None}
        n = _int(body.get("n"), 10, 1, 50)
        plays = lib.guest_plays(user)                          # a guest's wave follows his own listening
        rows = lib(lambda db: Wave(db, moods, character, genres=genres, plays=plays).next_batch(n, exclude))
        to_json = lib.personal(user)
        return JSONResponse({"items": [to_json(t) for t in rows]})

    def api_wave_rate(request: Request, user: dict, body: dict):
        tid = _int(body.get("id"))
        verdict = body.get("verdict")
        if tid is None or verdict not in ("like", "dislike"):
            return _err(400, "id and verdict (like|dislike) required")
        t = {"id": tid, "wave_kind": str(body.get("kind") or "known"), "wave_moods": [str(m) for m in body.get("moods") or []]}

        def go(db: Database):
            w = Wave(db)
            return w.like(t) if verdict == "like" else w.dislike(t)
        res = lib(go)
        if res["moods"]:
            lib.changed([tid])
        return JSONResponse(res)

    def api_set_moods(request: Request, user: dict, body: dict):
        """The listener's own moods for a track (manual labels); ``reject`` - guessed moods that do not fit."""
        tid = _int(request.path_params["tid"], 0)
        moods = [str(m) for m in body.get("moods") or [] if str(m).strip()]
        reject = [str(m) for m in body.get("reject") or [] if str(m) not in moods]

        def go(db: Database):
            if not db.get_track(tid):
                return None
            if moods:
                db.set_moods([tid], moods, "set")
            if reject:
                db.reject_moods(tid, reject)
            return db.get_track(tid)
        t = lib(go)
        if t is None:
            return _err(404, "no such track")
        lib.changed([tid])
        return JSONResponse(lib.personal(user)(t))

    def api_set_genres(request: Request, user: dict, body: dict):
        """The track's genres (replaces them, as editing in the desktop app; files are not touched)."""
        tid = _int(request.path_params["tid"], 0)
        genres = list(dict.fromkeys(str(g).strip() for g in body.get("genres") or [] if str(g).strip()))

        def go(db: Database):
            if not db.get_track(tid):
                return None
            db.set_genres([tid], genres, "set")
            return db.get_track(tid)
        t = lib(go)
        if t is None:
            return _err(404, "no such track")
        lib.changed([tid])
        return JSONResponse(lib.personal(user)(t))

    def api_app(request: Request, user: dict, _b):
        """The mobile app's installer from <data>/mobile (only the newest one is offered)."""
        m = latest_mobile(lib.models.parent / "mobile")
        if m is None or request.path_params["name"] != m["file"]:
            return _err(404, "no such app file")
        return FileResponse(lib.models.parent / "mobile" / m["file"], media_type="application/vnd.android.package-archive",
                            filename=m["file"])

    def asset(name: str, media: str):
        async def handler(request: Request):
            return FileResponse(WEB_DIR / name, media_type=media, headers={"Cache-Control": "no-cache"})
        return handler

    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return _err(404, "not found")
        return Response("Not found", status_code=404)

    routes = [
        Route("/", index),
        Route("/manifest.webmanifest", asset("manifest.webmanifest", "application/manifest+json")),
        Route("/sw.js", asset("sw.js", "text/javascript")),          # at the root: its scope is the whole player
        Route("/api/ping", ping),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/join", join, methods=["POST"]),
        Route("/api/join/{rid}", join_state),
        Route("/api/me", threaded(api_me)),
        Route("/api/me", threaded(api_rename), methods=["POST"]),
        Route("/api/info", threaded(api_info)),
        Route("/api/tracks", threaded(api_tracks)),
        Route("/api/tracks/lookup", threaded(api_lookup), methods=["POST"]),
        Route("/api/tracks/{tid:int}", threaded(api_track)),
        Route("/api/tracks/{tid:int}/moods", threaded(api_set_moods, label=True), methods=["POST"]),
        Route("/api/tracks/{tid:int}/probs", threaded(api_probs)),
        Route("/api/tracks/{tid:int}/labels", threaded(api_set_labels, label=True), methods=["POST"]),

        Route("/api/tracks/{tid:int}/genres", threaded(api_set_genres, label=True), methods=["POST"]),
        Route("/api/artists", threaded(api_artists)),
        Route("/api/albums", threaded(api_albums)),
        Route("/api/genres", threaded(api_genres)),
        Route("/api/playlists", threaded(api_playlists)),
        Route("/api/playlists/{pid:int}", threaded(api_playlist)),
        Route("/api/recent", threaded(api_recent)),
        Route("/api/favorites", threaded(api_favorites)),
        Route("/api/favorites", threaded(api_like), methods=["POST"]),
        Route("/api/stream/{tid:int}", threaded(api_stream), methods=["GET", "HEAD"]),
        Route("/api/cover/{key}", threaded(api_cover)),
        Route("/api/plays", threaded(api_plays), methods=["POST"]),
        Route("/api/app/{name}", threaded(api_app)),
        Route("/api/wave/next", threaded(api_wave_next), methods=["POST"]),
        Route("/api/wave/rate", threaded(api_wave_rate, label=True), methods=["POST"]),
        Mount("/static", WebFiles(directory=WEB_DIR), name="static"),
    ]
    return BanGuard(Starlette(routes=routes, exception_handlers={404: not_found}), access)


class BanGuard:
    """A banned address gets nothing at all - no page, no API, no access requests (answer 429 with Retry-After)."""

    def __init__(self, app, access: Access) -> None:
        self.app, self.access = app, access

    def __getattr__(self, name):                 # url_path_for, routes... of the wrapped app
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            addr = Access.address(Request(scope))
            if self.access.blocked(addr):
                b = self.access.bans.get(self.access._key(addr) or "", {})
                wait = max(1, int(b.get("until", 0) - time.time()))
                resp = JSONResponse({"error": "too many wrong keys: this address is blocked for a while"},
                                    status_code=429, headers={"Retry-After": str(wait)})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
