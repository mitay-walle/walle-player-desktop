"""Pack the Linux library server: dist/linux/walle-server-<version>.tar.gz  (see linux/README.md)

    python scripts/build_linux_server.py

Pure Python, so it is packed on any system: the server and core modules (no desktop window, no Qt), the web player,
install.sh (venv + systemd service) and the server requirements.  Linux line endings and modes are set in the archive.
"""
import io
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from musicplayer import __version__  # noqa: E402

OUT = ROOT / "dist" / "linux"
SKIP_DIRS = {"__pycache__"}
TEXT = {".py", ".sh", ".txt", ".md", ".json", ".js", ".css", ".html", ".svg", ".webmanifest"}


def files():
    pkg = ROOT / "musicplayer"
    for p in sorted(pkg.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and not SKIP_DIRS.intersection(rel.parts) and rel.parts[1] != "ui":   # no desktop window
            yield p, rel.as_posix()
    yield ROOT / "linux" / "install.sh", "install.sh"
    yield ROOT / "linux" / "requirements-server.txt", "requirements-server.txt"
    yield ROOT / "linux" / "requirements-autotag.txt", "requirements-autotag.txt"
    yield ROOT / "linux" / "README.md", "README.md"
    yield ROOT / "LICENSE", "LICENSE"


def main() -> int:
    name = f"walle-server-{__version__}"
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name}.tar.gz"
    n = 0
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT) as t:
        def add(arc: str, data: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(f"{name}/{arc}")
            info.size, info.mode, info.mtime = len(data), mode, time.time()
            t.addfile(info, io.BytesIO(data))

        for p, arc in files():
            data = p.read_bytes()
            if p.suffix in TEXT:
                data = data.replace(b"\r\n", b"\n")          # bash refuses CRLF scripts
            add(arc, data, 0o755 if arc.endswith(".sh") else 0o644)
            n += 1
        add("VERSION", f"{__version__}\n".encode())
    print(f"{out}  ({n} files, {out.stat().st_size / 2**20:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
