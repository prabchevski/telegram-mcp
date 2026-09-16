"""Pinned TDLib runtime, independent of the obsolete Homebrew stable library."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import ctypes
import fcntl
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
    # The 0.6 installer allows only 30 seconds for this import. Build Intel
    # TDLib separately, retaining the working installation until a later retry.
    if platform.system() == "Darwin" and platform.machine() == "x86_64":
        version = Path(__file__).resolve().parents[2]
        if (version.parent / ".telegram-search-install-root").is_file() and not (version / "native/libtdjson.dylib").is_file():
            cached = version.parent / "native" / COMMIT / "libtdjson.dylib"
            if cached.is_file():
                prepare(version)
                return
            start_background_prepare(version.parent)
            raise RuntimeError("Pinned TDLib is being prepared in the background. The previous installation remains active; the next automatic update retries the upgrade.")


def start_background_prepare(root: Path) -> None:
    from .config_io import atomic_write, read_source
    from .installation import safe_environment
    from .launchers import current_version
    from .service import open_private_file
    current = current_version(root)
    directory = root / "native"
    directory.mkdir(mode=0o700, exist_ok=True)
    script = directory / ("prepare-" + COMMIT + ".py")
    # The old installer deletes a failed staging directory. Keep this reviewed,
    # stdlib-only helper outside it and execute the still-installed interpreter.
    content = Path(__file__).read_bytes()
    before = read_source(script)
    if before != content:
        atomic_write(script, content, expected=before)
    descriptor = open_private_file(directory / "prepare.log")
    try:
        os.lseek(descriptor, 0, os.SEEK_END)
        subprocess.Popen([str(current / ".venv/bin/python"), "-I", str(script), "--cache-only", str(root)],
                         env=safe_environment(), cwd=str(root), stdin=subprocess.DEVNULL,
                         stdout=descriptor, stderr=descriptor, close_fds=True, start_new_session=True)
    finally:
        os.close(descriptor)


def verify(path: Path) -> None:
    library = ctypes.CDLL(str(path))
    library.td_execute.argtypes = [ctypes.c_char_p]
    library.td_execute.restype = ctypes.c_char_p
    for name, expected in (("version", VERSION), ("commit_hash", COMMIT)):
        result = json.loads(library.td_execute(json.dumps({"@type": "getOption", "name": name}).encode()))
        if result.get("value") != expected:
            raise RuntimeError("TDLib runtime does not match the pinned version and commit")


def _build_cache(root: Path) -> Path:
    cache = root / "native" / COMMIT
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
    return cache


def prepare_cache(root: Path, *, background: bool = False) -> Path | None:
    from telegram_search_mcp.config_io import validate_path
    from telegram_search_mcp.launchers import validate_root
    from telegram_search_mcp.service import open_private_file
    validate_root(root)
    directory = root / "native"
    directory.mkdir(mode=0o700, exist_ok=True)
    validate_path(directory / "prepare.lock")
    descriptor = open_private_file(directory / "prepare.lock")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if background else 0))
        except BlockingIOError:
            return None
        if background and not (directory / COMMIT / "libtdjson.dylib").is_file():
            # The old 0.6 installer may only have installed the Homebrew TDLib
            # bottle, without the tools needed for this pinned source build.
            subprocess.run(["/usr/local/bin/brew", "install", "cmake", "gperf", "openssl@3"], check=True, timeout=1800)
        return _build_cache(root)
    finally:
        os.close(descriptor)


def prepare(version: Path) -> None:
    """Use locked wheels where available; compile the same official source on Intel Macs."""
    bundled = bundled_candidates()
    if bundled:
        verify(bundled[0])
        return
    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        raise RuntimeError("No supported pinned TDLib runtime is installed")
    cache = prepare_cache(version.parent)
    library = cache / "libtdjson.dylib"
    destination = version / "native"
    destination.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile(library, destination / library.name)
    shutil.copyfile(cache / "LICENSE_1_0.txt", destination / "LICENSE_1_0.txt")


if __name__ == "__main__":
    import sys
    if sys.argv[1] == "--cache-only":
        try:
            prepare_cache(Path(sys.argv[2]), background=True)
        except Exception:
            subprocess.run(["/usr/bin/osascript", "-e", 'display notification "Automatic update needs attention. Run the latest Telegram MCP installer; your old installation and login are preserved." with title "Telegram MCP update"'], capture_output=True, timeout=5, check=False)
            raise
    else:
        prepare(Path(sys.argv[1]))
