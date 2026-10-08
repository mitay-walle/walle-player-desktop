"""Sign Walle Player's executables with the own code-signing certificate ("CN=Walle Player").

    python scripts/sign.py FILE.exe [...]          # sign
    python scripts/sign.py --export OUT.cer         # the public certificate for other computers (no private key)

The certificate is self-signed and lives in the current user's store (Cert:\\CurrentUser\\My), made once with
``New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Walle Player" ...``.  Windows trusts the signature
only where WallePlayer.cer was installed into «Trusted Root Certification Authorities» and «Trusted Publishers».
No certificate on this computer: signing is skipped with a note, the build goes on.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

SUBJECT = "CN=Walle Player"
TIMESTAMP = ["http://timestamp.digicert.com", "http://timestamp.sectigo.com", "http://timestamp.globalsign.com/tsa/r6advanced1"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout.strip()


def thumbprint() -> str | None:
    """The newest valid code-signing certificate of Walle Player in the user's store."""
    out = _ps(f"Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | Where-Object {{ $_.Subject -eq '{SUBJECT}' "
              "-and $_.NotAfter -gt (Get-Date) -and $_.HasPrivateKey } | Sort-Object NotAfter -Descending | "
              "Select-Object -First 1 -ExpandProperty Thumbprint")
    return out or None


def signtool() -> str | None:
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10", "bin")
    found = sorted(glob.glob(os.path.join(kits, "*", "x64", "signtool.exe")))
    return found[-1] if found else None


def sign(paths: list[Path]) -> bool:
    """Signs every file (SHA-256, with a timestamp when a timestamp server answers).  False = not signed."""
    tool, thumb = signtool(), thumbprint()
    if tool is None or thumb is None:
        print("signing skipped: " + ("no signtool (Windows SDK)" if tool is None else f"no certificate {SUBJECT}"))
        return False
    for p in paths:
        base = [tool, "sign", "/sha1", thumb, "/s", "My", "/fd", "sha256", "/d", "Walle Player", "/q"]
        for ts in TIMESTAMP + [None]:          # the timestamp keeps the signature valid after the certificate expires
            cmd = base + (["/tr", ts, "/td", "sha256"] if ts else []) + [str(p)]
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                print(f"signed: {p.name}" + ("" if ts else "  (without a timestamp: no timestamp server answered)"))
                break
        else:
            print(f"NOT signed: {p}\n{r.stdout}{r.stderr}")
            return False
    return True


def export(out: Path) -> bool:
    thumb = thumbprint()
    if thumb is None:
        print(f"no certificate {SUBJECT}")
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    _ps(f"Export-Certificate -Cert Cert:\\CurrentUser\\My\\{thumb} -FilePath '{out}' -Type CERT | Out-Null")
    print(f"certificate: {out}")
    return out.exists()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--export"] and len(args) == 2:
        raise SystemExit(0 if export(Path(args[1])) else 1)
    if not args:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(0 if sign([Path(a) for a in args]) else 1)
