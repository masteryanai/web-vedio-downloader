"""Fetch the pinned media toolchain into this project, without installing system software."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"

def fetch(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()

def install(data, digest, select):
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError("Tool archive checksum mismatch")
    TOOLS.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            name = Path(entry.filename).name
            if not entry.is_dir() and select(entry.filename, name):
                target = TOOLS / name
                if not target.exists():
                    target.write_bytes(archive.read(entry))

if __name__ == "__main__":
    metadata = json.loads((ROOT / "toolchain.json").read_text(encoding="utf-8"))
    install(fetch(metadata["url"]), metadata["sha256"],
            lambda path, name: "/bin/" in path and (name in {"ffmpeg.exe", "ffprobe.exe"} or name.endswith(".dll")))
    base = "https://nodejs.org/dist/v24.14.1/"
    filename = "node-v24.14.1-win-x64.zip"
    lines = fetch(base + "SHASUMS256.txt").decode().splitlines()
    digest = next(line.split()[0] for line in lines if line.split()[-1] == filename)
    install(fetch(base + filename), digest, lambda path, name: name == "node.exe")
    print("Media tools ready in tools/.")
