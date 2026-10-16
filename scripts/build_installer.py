"""Build the one-file installer: dist\\installer\\WallePlayer-Setup-<version>.exe

    .venv\\Scripts\\pyinstaller musicplayer.spec --noconfirm     # the app first
    python scripts/build_installer.py [--app data\\next\\WallePlayer]   # another app folder: the running one is locked
    python scripts/build_installer.py --sign none                   # nothing signed here (signed elsewhere)

--sign self (the default): WallePlayer.exe and the installer are signed with the own self-signed certificate
(scripts/sign.py) when it is on this computer; dist\\installer\\WallePlayer.cer is the public part for the other computers.
--sign none: left unsigned for an outside signing service — the release workflow (.github/workflows/release.yml) has
SignPath sign WallePlayer.exe first, puts it back into the app folder, packs the installer with --sign none and has
SignPath sign the installer as well (docs/code-signing.md).
Both executables carry the version resource (scripts/versioninfo.py) the signing service checks.
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from musicplayer import __version__  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import sign  # noqa: E402
import versioninfo  # noqa: E402

APP_DIR = ROOT / "dist" / "WallePlayer"
WORK = ROOT / "build" / "installer"
OUT = ROOT / "dist" / "installer"


def main() -> int:
    global APP_DIR
    if "--app" in sys.argv:
        APP_DIR = Path(sys.argv[sys.argv.index("--app") + 1]).resolve()
    mode = sys.argv[sys.argv.index("--sign") + 1] if "--sign" in sys.argv else "self"
    if mode not in ("self", "none"):
        print("--sign self | none")
        return 2
    if not (APP_DIR / "WallePlayer.exe").exists():
        print("build the app first: pyinstaller musicplayer.spec --noconfirm")
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    signed = mode == "self" and sign.sign([APP_DIR / "WallePlayer.exe"])   # before packing: the program is signed too
    payload = WORK / "app.zip"
    files = [p for p in APP_DIR.rglob("*") if p.is_file()]
    print(f"packing {len(files)} files ...", flush=True)
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(APP_DIR))
    print(f"payload {payload.stat().st_size / 2**20:.0f} MB", flush=True)
    res = ROOT / "musicplayer" / "resources"
    src = (ROOT / "installer" / "installer.py").read_text(encoding="utf-8")
    script = WORK / "installer.py"
    script.write_text(src.replace('VERSION = "0.2.0"', f'VERSION = "{__version__}"'), encoding="utf-8")
    name = f"WallePlayer-Setup-{__version__}"
    version_file = versioninfo.write(WORK / "version_installer.txt", __version__, "Walle Player Setup", f"{name}.exe")
    sep = ";"
    cmd = [str(ROOT / ".venv" / "Scripts" / "pyinstaller.exe"), "--noconfirm", "--onefile", "--windowed", "--name", name,
           "--icon", str(res / "icon.ico"), "--distpath", str(OUT), "--workpath", str(WORK / "build"),
           "--specpath", str(WORK), "--version-file", str(version_file),
           "--add-data", f"{payload}{sep}.", "--add-data", f"{res / 'icon.ico'}{sep}.",
           "--add-data", f"{res / 'wordmark.png'}{sep}.",
           "--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "sklearn",
           "--exclude-module", "librosa", "--exclude-module", "numba", "--exclude-module", "llvmlite",
           "--exclude-module", "PySide6.QtMultimedia", "--exclude-module", "PySide6.QtCharts",
           "--exclude-module", "PySide6.QtWebEngineCore", str(script)]
    subprocess.run(cmd, check=True)
    exe = OUT / f"{name}.exe"
    if signed:
        sign.sign([exe])
        sign.export(OUT / "WallePlayer.cer")
    print(f"installer: {exe}  ({exe.stat().st_size / 2**20:.0f} MB)" + ("" if signed else "  — not signed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
