# PyInstaller spec: build with  .venv\Scripts\pyinstaller musicplayer.spec --noconfirm
# Output: dist\WallePlayer\WallePlayer.exe (one-folder build, windowed)
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = [("musicplayer/resources", "musicplayer/resources"), ("musicplayer/tools", "musicplayer/tools"),
         ("musicplayer/server/web", "musicplayer/server/web")]
binaries = []
hidden = []
for pkg in ("librosa", "numba", "llvmlite", "soundfile", "sklearn", "scipy", "uvicorn", "anyio",
            "starlette", "sse_starlette", "pydantic", "pydantic_core", "httpx", "httpcore", "h11", "lazy_loader",
            "mcp_types", "segno"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception as e:  # optional packages
        print("collect_all skipped", pkg, e)
hidden += collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
datas += collect_data_files("mcp")
hidden += collect_submodules("musicplayer")
import os
if os.path.exists("third_party/mpv/libmpv-2.dll"):
    binaries += [("third_party/mpv/libmpv-2.dll", ".")]
if os.path.exists("third_party/cloudflared/cloudflared.exe"):   # optional: internet access for other devices
    binaries += [("third_party/cloudflared/cloudflared.exe", "third_party/cloudflared")]
hidden += ["mpv", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtSvg"]

a = Analysis(
    ["scripts/launch.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WallePlayer", console=False,
          icon="musicplayer/resources/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="WallePlayer")
