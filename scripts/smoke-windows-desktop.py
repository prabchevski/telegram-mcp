#!/usr/bin/env python3
"""Run the real Windows installer in fresh Windows 10/11 x64 evaluation VMs.

This orchestration is intentionally restricted to disposable GitHub-hosted Linux
runners. It never installs an OS or deletes SDKs on a developer workstation.
Only Microsoft-hosted, SHA-256-pinned evaluation ISOs are used. They are neither
committed nor uploaded. The guest runs the smoke check as a disposable standard
user and never authorizes Telegram or opens a real profile.

Primary references:
https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise
https://download.microsoft.com/download/c/1/1/c11d2ca5-967c-45c0-bc7d-2d9ca3f1fe07/Windows10Enterprise22H2HashValues.pdf
https://go.microsoft.com/fwlink/?linkid=2334901
https://github.com/dockur/windows/tree/efe47da76d49c9d77c0a26799c70315fa4d91055
https://github.blog/changelog/2024-04-02-github-actions-hardware-accelerated-android-virtualization-now-available/

The VM harness configures unattended setup, networking and virtio drivers; it is
not a test of every OEM Windows image or the graphical Codex/ChatGPT application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


# Docker Hub's OCI index records this exact source revision and version 6.05.
VM_SOURCE = "efe47da76d49c9d77c0a26799c70315fa4d91055"
VM_IMAGE = "docker.io/dockurr/windows@sha256:0cff9eb0e7aee9953e55bc682852ca4fdca233145a58ae1ec94f0b0c01a2ed30"
WINDOWS_IMAGES = {
    "10": {
        "name": "Windows 10 Enterprise Evaluation 22H2 x64, build 19045.2006",
        "url": "https://software-static.download.prss.microsoft.com/dbazure/988969d5-f34g-4e03-ac9d-1f9786c66750/19045.2006.220908-0225.22h2_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso",
        "sha256": "ef7312733a9f5d7d51cfa04ac497671995674ca5e1058d5164d6028f0938d668",
        "bytes": 5550497792,
    },
    "11": {
        "name": "Windows 11 Enterprise Evaluation 25H2 x64, build 26200.6584",
        "url": "https://software-static.download.prss.microsoft.com/dbazure/888969d5-f34g-4e03-ac9d-1f9786c66749/26200.6584.250915-1905.25h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso",
        "sha256": "a61adeab895ef5a4db436e0a7011c92a2ff17bb0357f58b13bbc4062e535e7b9",
        "bytes": 7092807680,
    },
}
UV_URL = "https://github.com/astral-sh/uv/releases/download/0.11.15/uv-x86_64-pc-windows-msvc.zip"
UV_SHA256 = "04b98d414a9000e25e5e0e7c9f53749e66b790cdaffc582829e6f58c544ee11c"

# Generated in the ephemeral OEM folder, not a distributed installer entrypoint.
# The installer is executed by a standard user, without membership of Administrators.
GUEST_BOOTSTRAP = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = 'C:\DesktopTest'
$results = Join-Path $root 'results'
$shared = '\\host.lan\Data'
New-Item -ItemType Directory -Force -Path $results | Out-Null
Start-Transcript -Path (Join-Path $results 'bootstrap.log') -Force | Out-Null
$exitCode = 1
try {
    Copy-Item -LiteralPath 'C:\OEM\package.zip' -Destination $root
    Copy-Item -LiteralPath 'C:\OEM\guest-test.ps1' -Destination $root
    Copy-Item -LiteralPath 'C:\OEM\uv.exe' -Destination $root
    Expand-Archive -LiteralPath (Join-Path $root 'package.zip') -DestinationPath (Join-Path $root 'source')
    & net.exe user DesktopTestRunner '@TEST_PASSWORD@' /add /expires:never
    if ($LASTEXITCODE -ne 0) { throw 'Could not create disposable standard test user' }
    $account = "$env:COMPUTERNAME\DesktopTestRunner"
    & icacls.exe $root /grant "${account}:(OI)(CI)M" /T /Q
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant the standard user access to test files' }
    $secure = ConvertTo-SecureString '@TEST_PASSWORD@' -AsPlainText -Force
    $credential = New-Object System.Management.Automation.PSCredential($account, $secure)
    $process = Start-Process -FilePath "$PSHOME\powershell.exe" -Credential $credential -LoadUserProfile -Wait -PassThru -WorkingDirectory $root -ArgumentList '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\DesktopTest\guest-test.ps1' -RedirectStandardOutput (Join-Path $results 'guest.stdout.log') -RedirectStandardError (Join-Path $results 'guest.stderr.log')
    $exitCode = $process.ExitCode
    if ($exitCode -ne 0) { throw "The standard-user acceptance process failed: $exitCode" }
} catch {
    $_ | Out-String | Write-Output
    $exitCode = 1
} finally {
    [ordered]@{ exit_code = $exitCode; expected_windows = '@EXPECTED_WINDOWS@' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $results 'guest-exit.json') -Encoding UTF8
    Stop-Transcript | Out-Null
    # Wait for the private VM-to-container SMB connection; never publish a host port.
    $copied = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Copy-Item -Path (Join-Path $results '*') -Destination $shared -Force -ErrorAction Stop
            Set-Content -LiteralPath (Join-Path $shared 'complete.txt') -Value $exitCode -Encoding ASCII
            $copied = $true
            break
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $copied) { Write-Output 'Could not copy guest evidence to the host share' }
}
exit $exitCode
"""

GUEST_TEST = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = '1'
$env:UV_NO_PROGRESS = '1'
$root = 'C:\DesktopTest'
$env:PATH = "$root;$env:PATH"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'The installer acceptance test must run as a standard user'
}
[ordered]@{
    name = $identity.Name
    is_administrator = $false
    profile = $env:USERPROFILE
    uac_enabled = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System').EnableLUA
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'results\test-user.json') -Encoding UTF8
Set-Location (Join-Path $root 'source\telegram-mcp-macos')
& "$root\uv.exe" sync --locked --python 3.13
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency bootstrap failed' }
& '.\.venv\Scripts\python.exe' -I scripts/smoke-install-windows.py "$root\package.zip" --expected-windows '@EXPECTED_WINDOWS@' --report-path "$root\results\acceptance.json"
if ($LASTEXITCODE -ne 0) { throw 'The Windows archive installer smoke test failed' }
exit 0
"""

QMP_SCREENSHOT = r"""
import json, socket
connection = socket.socket(socket.AF_UNIX)
connection.settimeout(10)
connection.connect('/debug/qmp.sock')
stream = connection.makefile('rwb')
def request(command):
    stream.write((json.dumps(command) + '\n').encode()); stream.flush()
    while True:
        response = json.loads(stream.readline())
        if 'return' in response or 'error' in response:
            return response
stream.readline()
request({'execute': 'qmp_capabilities'})
print(request({'execute': 'screendump', 'arguments': {'filename': '/debug/screen.ppm'}}))
"""


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, path: Path, expected: str, size: int | None = None) -> None:
    budget = 12 * 60 if size is not None else 2 * 60
    print(f"Downloading {path.name} (total retry budget: {budget}s)", flush=True)
    command = ["curl", "--fail", "--location", "--retry", "2", "--retry-max-time", str(budget),
               "--connect-timeout", "30", "--max-time", str(budget),
               "--silent", "--show-error", url, "--output", str(path)]
    # curl's retry-max-time does not bound a final in-progress attempt. Enforce
    # one wall-clock budget around the entire process, including every retry.
    with subprocess.Popen(command) as process:
        deadline = time.monotonic() + budget
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Download exceeded its total {budget}s budget: {path.name}")
                try:
                    result = process.wait(timeout=min(30, remaining))
                except subprocess.TimeoutExpired:
                    received = path.stat().st_size if path.exists() else 0
                    total = f" / {size / 1024**2:.0f} MiB" if size is not None else ""
                    print(f"Downloading {path.name}: {received / 1024**2:.0f} MiB{total}", flush=True)
                    continue
                if result != 0:
                    raise subprocess.CalledProcessError(result, command)
                break
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    if size is not None and path.stat().st_size != size:
        raise RuntimeError(f"Unexpected downloaded size: {path.name}")
    if sha256(path) != expected:
        raise RuntimeError(f"SHA-256 verification failed: {path.name}")
    print(f"Verified {path.name}: SHA-256 {expected}", flush=True)


def prepare_runner() -> None:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or platform.system() != "Linux"
            or platform.machine() != "x86_64"):
        raise RuntimeError("This script only runs on disposable GitHub-hosted Linux x64 runners")
    if not Path("/dev/kvm").exists() or not Path("/dev/net/tun").exists():
        raise RuntimeError("This runner lacks /dev/kvm or /dev/net/tun; refusing software emulation")
    run(["sudo", "-n", "chmod", "666", "/dev/kvm"])
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("KVM is not accessible")
    # Standard runners ship SDKs this VM job does not use. Remove only this fixed
    # allowlist, and only after the disposable-host checks above have succeeded.
    if shutil.disk_usage(os.environ["RUNNER_TEMP"]).free < 45 * 1024**3:
        run(["sudo", "-n", "rm", "-rf", "--", "/usr/share/dotnet", "/usr/local/lib/android",
             "/opt/ghc", "/usr/share/swift", "/usr/local/share/powershell",
             "/opt/hostedtoolcache/CodeQL"])
    if shutil.disk_usage(os.environ["RUNNER_TEMP"]).free < 40 * 1024**3:
        raise RuntimeError("Less than 40 GiB free after removing unused ephemeral runner SDKs")
    run(["docker", "version"], stdout=subprocess.DEVNULL)


def write_guest(oem: Path, windows: str, archive: Path, uv_zip: Path) -> None:
    oem.mkdir()
    shutil.copyfile(archive, oem / "package.zip")
    with zipfile.ZipFile(uv_zip) as bundle:
        entries = [item for item in bundle.infolist() if Path(item.filename).name == "uv.exe"]
        if len(entries) != 1:
            raise RuntimeError("Expected one uv.exe in the verified bootstrap archive")
        (oem / "uv.exe").write_bytes(bundle.read(entries[0]))
    # net.exe asks an interactive compatibility question above 14 characters.
    # This short-lived account exists only inside an unexposed disposable VM.
    password = "T9a-" + secrets.token_hex(5)
    bootstrap = GUEST_BOOTSTRAP.replace("@EXPECTED_WINDOWS@", windows).replace("@TEST_PASSWORD@", password)
    guest = GUEST_TEST.replace("@EXPECTED_WINDOWS@", windows)
    # Windows PowerShell 5.1 recognizes UTF-8 with BOM reliably.
    (oem / "bootstrap.ps1").write_text(bootstrap, encoding="utf-8-sig")
    (oem / "guest-test.ps1").write_text(guest, encoding="utf-8-sig")
    (oem / "install.bat").write_text(
        '@echo off\r\npowershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\OEM\\bootstrap.ps1\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="ascii")


def capture(container: str, output: Path, debug: Path) -> None:
    with (output / "vm.log").open("w") as stream:
        subprocess.run(["docker", "logs", container], stdout=stream, stderr=subprocess.STDOUT, timeout=30)
    with (output / "vm-state.json").open("w") as stream:
        subprocess.run(["docker", "inspect", "--format", "{{json .State}}", container], stdout=stream, stderr=subprocess.STDOUT, timeout=30)
    subprocess.run(["docker", "exec", container, "python3", "-c", QMP_SCREENSHOT],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    screen = debug / "screen.ppm"
    if screen.is_file():
        shutil.copyfile(screen, output / "screen.ppm")


def execute(args: argparse.Namespace, output: Path, provenance: dict, overall_deadline: float) -> None:
    repository = Path(__file__).resolve().parent.parent
    image = WINDOWS_IMAGES[args.windows]
    # VM disks and ISO images are deliberately outside the uploaded evidence tree.
    with tempfile.TemporaryDirectory(prefix="telegram-desktop-", dir=os.environ["RUNNER_TEMP"],
                                     ignore_cleanup_errors=True) as temporary:
        temporary_root = Path(temporary)
        storage = temporary_root / "storage"
        storage.mkdir()
        shared = temporary_root / "shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        debug = temporary_root / "debug"
        debug.mkdir(mode=0o777)
        debug.chmod(0o777)
        build = temporary_root / "build"
        run([sys.executable, "-I", str(repository / "scripts/release.py"), "build", "--output", str(build)], cwd=repository)
        archives = list(build.glob("*.zip"))
        if len(archives) != 1:
            raise RuntimeError("Expected one built source archive")
        archive = archives[0]
        provenance["archive_sha256"] = sha256(archive)
        iso = temporary_root / "windows.iso"
        uv_zip = temporary_root / "uv.zip"
        download(image["url"], iso, image["sha256"], image["bytes"])
        download(UV_URL, uv_zip, UV_SHA256)
        oem = temporary_root / "oem"
        write_guest(oem, args.windows, archive, uv_zip)
        container = "telegram-desktop-" + args.windows
        started = False
        try:
            run(["docker", "pull", "--platform", "linux/amd64", VM_IMAGE], timeout=300)
            run(["docker", "run", "--detach", "--name", container,
                 "--platform", "linux/amd64", "--device=/dev/kvm", "--device=/dev/net/tun",
                 "--cap-add", "NET_ADMIN", "--stop-timeout", "30",
                 "--env", "RAM_SIZE=8G", "--env", "CPU_CORES=4", "--env", "DISK_SIZE=64G",
                 "--env", "DISK_FMT=qcow2", "--env", "ALLOCATE=N",
                 "--env", "BOOT_MODE=windows_secure", "--env", "TPM=Y",
                 "--env", "MIDO=N", "--env", "ESD=N", "--env", "LOG=Y",
                 "--env", "QMP=unix:/debug/qmp.sock,server=on,wait=off",
                 "--volume", f"{iso}:/custom.iso:ro", "--volume", f"{storage}:/storage",
                 "--volume", f"{oem}:/oem:ro", "--volume", f"{shared}:/shared",
                 "--volume", f"{debug}:/debug", VM_IMAGE])
            started = True
            deadline = min(time.monotonic() + 30 * 60, overall_deadline)
            last_progress = ""
            while time.monotonic() < deadline:
                if (shared / "complete.txt").is_file():
                    break
                state = run(["docker", "inspect", "--format", "{{.State.Running}}", container], capture_output=True, text=True).stdout.strip()
                if state != "true":
                    raise RuntimeError("The Windows VM container exited before producing acceptance evidence")
                progress = run(["docker", "logs", "--tail", "4", container], capture_output=True, text=True)
                recent = (progress.stdout + progress.stderr).strip()
                if recent != last_progress:
                    print(recent, flush=True)
                    last_progress = recent
                time.sleep(20)
            else:
                raise RuntimeError("Windows acceptance exceeded the 30 minute VM or 40 minute total run budget")
            for item in shared.iterdir():
                if item.is_file() and item.suffix in {".json", ".log", ".txt"}:
                    shutil.copyfile(item, output / item.name)
            if (shared / "complete.txt").read_text(encoding="ascii").strip() != "0":
                raise RuntimeError("The guest bootstrap or installer smoke check failed; see guest logs")
            report = json.loads((shared / "acceptance.json").read_text(encoding="utf-8-sig"))
            if report.get("status") != "passed":
                raise RuntimeError("The guest did not report a passed installation")
            if (report.get("expected_windows_version") != args.windows
                    or report.get("detected_desktop_windows_version") != args.windows
                    or report.get("windows_family") != "desktop"
                    or report.get("windows", {}).get("ProductType") != 1):
                raise RuntimeError("The guest evidence does not identify the expected desktop Windows version")
            if report.get("archive", {}).get("sha256") != provenance["archive_sha256"]:
                raise RuntimeError("The tested archive differs from the source archive built by this job")
            checks = report.get("checks", [])
            if len(checks) != 17 or any(check.get("status") != "passed" for check in checks):
                raise RuntimeError("The guest did not complete all 17 installer acceptance checks")
            user = json.loads((shared / "test-user.json").read_text(encoding="utf-8-sig"))
            if user.get("is_administrator") is not False:
                raise RuntimeError("The guest test did not verify a standard user")
            provenance["status"] = "passed"
            print("PASS: actual Windows " + args.windows + " x64 VM installation", flush=True)
            print(json.dumps(report, indent=2), flush=True)
        except BaseException as error:
            provenance["status"] = "failed"
            provenance["error"] = str(error)
            raise
        finally:
            if started:
                # Diagnostic/cleanup errors must not replace the test failure.
                try:
                    capture(container, output, debug)
                except Exception as error:
                    provenance.setdefault("diagnostic_errors", []).append(str(error))
                try:
                    run(["docker", "rm", "--force", container], timeout=60)
                except Exception as error:
                    provenance.setdefault("cleanup_errors", []).append(str(error))
            # The container writes root-owned VM files. Cleanup stays confined to
            # our freshly allocated directory on the guarded ephemeral runner.
            try:
                run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(temporary_root)], timeout=60)
            except Exception as error:
                provenance.setdefault("cleanup_errors", []).append(str(error))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", choices=WINDOWS_IMAGES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    provenance = {"status": "running", "expected_windows": args.windows,
                  "github_sha": os.environ.get("GITHUB_SHA"), "evaluation_image": WINDOWS_IMAGES[args.windows],
                  "vm_image": VM_IMAGE, "vm_source": VM_SOURCE,
                  "host_architecture": platform.machine(), "kvm": False,
                  "limitations": "Unattended evaluation VM; no Codex/ChatGPT graphical UI or Telegram authorization"}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    overall_deadline = time.monotonic() + 40 * 60
    try:
        prepare_runner()
        provenance["kvm"] = True
        execute(args, output, provenance, overall_deadline)
    except BaseException as error:
        provenance["status"] = "failed"
        provenance["error"] = str(error)
        raise
    finally:
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
