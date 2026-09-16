"""Pinned TDLib runtime, independent of the obsolete Homebrew stable library."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import ctypes
import json
import os
import platform
import shutil
import subprocess
import tempfile

VERSION = "1.8.67"
COMMIT = "d1085f9cebc5a62379991ae1652673954f229c1f"


def bundled_candidates() -> list[Path]:
    try:
        package = distribution("tdjson")
    except PackageNotFoundError:
        return []
    if package.version != VERSION:
        raise RuntimeError("Unexpected bundled TDLib package version")
    return [Path(package.locate_file(item)) for item in package.files or ()
            if item.name.startswith("libtdjson") and (".so" in item.name or item.name.endswith(".dylib"))]


def candidates() -> list[Path]:
    return bundled_candidates() + [Path(__file__).resolve().parents[2] / "native/libtdjson.dylib"]


def check_staged_runtime() -> None:
    # The 0.6 updater stages new Python code using its old installer. On Intel
    # it cannot build TDLib. Fail its import preflight before it activates an
    # unusable version. The 0.7 installer prepares native code before this check.
    if platform.system() == "Darwin" and platform.machine() == "x86_64":
        version = Path(__file__).resolve().parents[2]
        if (version.parent / ".telegram-search-install-root").is_file() and not (version / "native/libtdjson.dylib").is_file():
            raise RuntimeError("Run the 0.7 macOS installer once to upgrade TDLib on this Intel Mac; the previous installation remains available")


def verify(path: Path) -> None:
    library = ctypes.CDLL(str(path))
    library.td_execute.argtypes = [ctypes.c_char_p]
    library.td_execute.restype = ctypes.c_char_p
    for name, expected in (("version", VERSION), ("commit_hash", COMMIT)):
        result = json.loads(library.td_execute(json.dumps({"@type": "getOption", "name": name}).encode()))
        if result.get("value") != expected:
            raise RuntimeError("TDLib runtime does not match the pinned version and commit")


def prepare(version: Path) -> None:
    """Use locked wheels where available; compile the same official source on Intel Macs."""
    bundled = bundled_candidates()
    if bundled:
        verify(bundled[0])
        return
    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        raise RuntimeError("No supported pinned TDLib runtime is installed")
    cache = version.parent / "native" / COMMIT
    library = cache / "libtdjson.dylib"
    if not library.exists():
        cache.parent.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tdlib-build-", dir=cache.parent) as directory:
            temporary = Path(directory)
            source, build = temporary / "source", temporary / "build"
            environment = {**os.environ, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
            subprocess.run(["git", "init", str(source)], check=True, env=environment)
            subprocess.run(["git", "-C", str(source), "fetch", "--depth", "1", "https://github.com/tdlib/td.git", COMMIT], check=True, env=environment, timeout=300)
            subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], check=True, env=environment)
            brew = "/usr/local/bin/brew"
            openssl = subprocess.check_output([brew, "--prefix", "openssl@3"], text=True).strip()
            subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release", "-DTD_ENABLE_JNI=OFF", "-DTD_ENABLE_DOTNET=OFF", "-DOPENSSL_ROOT_DIR=" + openssl], check=True, env=environment, timeout=300)
            subprocess.run(["cmake", "--build", str(build), "--target", "tdjson", "--parallel", str(min(os.cpu_count() or 2, 4))], check=True, env=environment, timeout=3600)
            compiled = build / "libtdjson.dylib"
            verify(compiled)
            staged = temporary / "runtime"
            staged.mkdir(mode=0o700)
            shutil.copyfile(compiled, staged / library.name)
            shutil.copyfile(source / "LICENSE_1_0.txt", staged / "LICENSE_1_0.txt")
            staged.rename(cache)
    verify(library)
    destination = version / "native"
    destination.mkdir(mode=0o700)
    shutil.copyfile(library, destination / library.name)
    shutil.copyfile(cache / "LICENSE_1_0.txt", destination / "LICENSE_1_0.txt")


if __name__ == "__main__":
    import sys
    prepare(Path(sys.argv[1]))
