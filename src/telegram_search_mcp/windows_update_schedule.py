"""Owned, per-user Windows Task Scheduler updates without elevation or passwords.

Only this installation's exact saved task definition may be removed. A task
changed or removed in Task Scheduler is preserved and reported to the owner.
COM and pywin32 are imported only when native scheduling is requested.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from .config_io import atomic_write, read_source
from .launchers import current_version, validate_root

SNAPSHOT = "windows-update-schedule.json"
NAME_PREFIX = "TelegramMCPUpdate-"
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
TASK_CREATE = 2
TASK_IGNORE_REGISTRATION_TRIGGERS = 0x20
TASK_LOGON_INTERACTIVE_TOKEN = 3


def task_name(root: Path) -> str:
    return NAME_PREFIX + hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]


def _powershell() -> Path:
    from .windows_install import powershell_path
    return powershell_path()


def task_arguments(root: Path) -> str:
    # Exec actions pass arguments directly to the system executable, not cmd.exe.
    return subprocess.list2cmdline([
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(root / "update.ps1"), "--scheduled",
    ])


def task_definition(root: Path, user_sid: str, powershell: Path) -> str:
    if not re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+)+", user_sid):
        raise RuntimeError("Invalid current Windows user SID")
    if powershell.name.lower() != "powershell.exe" or not powershell.is_absolute():
        raise RuntimeError("Updater requires the system Windows PowerShell executable")
    ET.register_namespace("", TASK_NAMESPACE)
    task = ET.Element("Task", {"xmlns": TASK_NAMESPACE, "version": "1.2"})
    def add(parent, name, text=None, **attributes):
        item = ET.SubElement(parent, name, attributes)
        if text is not None:
            item.text = str(text)
        return item
    registration = add(task, "RegistrationInfo")
    add(registration, "Author", user_sid)
    add(registration, "Description", "Telegram MCP verified release updates for this installation")
    add(registration, "URI", "\\" + task_name(root))
    triggers = add(task, "Triggers")
    daily = add(triggers, "CalendarTrigger", id="daily")
    # A future first boundary and no registration trigger prevent setup from
    # immediately running an updater or opening a Telegram profile.
    add(daily, "StartBoundary", (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat())
    add(daily, "Enabled", "true")
    add(add(daily, "ScheduleByDay"), "DaysInterval", "1")
    logon = add(triggers, "LogonTrigger", id="logon")
    add(logon, "Enabled", "true")
    add(logon, "UserId", user_sid)
    add(logon, "Delay", "PT5M")
    principal = add(add(task, "Principals"), "Principal", id="owner")
    add(principal, "UserId", user_sid)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(task, "Settings")
    for name, value in {
        "MultipleInstancesPolicy": "IgnoreNew", "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false", "AllowHardTerminate": "false",
        "StartWhenAvailable": "false", "RunOnlyIfNetworkAvailable": "false",
        "AllowStartOnDemand": "false", "Enabled": "true", "Hidden": "false",
        "RunOnlyIfIdle": "false", "WakeToRun": "false", "ExecutionTimeLimit": "PT0S",
        "Priority": "7",
    }.items():
        add(settings, name, value)
    action = add(add(task, "Actions", Context="owner"), "Exec")
    add(action, "Command", str(powershell))
    add(action, "Arguments", task_arguments(root))
    add(action, "WorkingDirectory", str(root))
    return ET.tostring(task, encoding="unicode")


def _xml_tree(xml: str):
    if not isinstance(xml, str) or len(xml) > 256 * 1024 or "<!" in xml:
        raise RuntimeError("Invalid updater task definition")
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError("Invalid updater task definition") from exc
    for element in tree.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return tree


def _xml_digest(xml: str) -> str:
    def normalized(element):
        return [element.tag, sorted(element.attrib.items()), (element.text or "").strip(),
                [normalized(child) for child in element]]
    return hashlib.sha256(json.dumps(normalized(_xml_tree(xml)), ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _resolve_account_sid(name: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import win32security
        sid, _domain, _kind = win32security.LookupAccountName(None, name)
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return None


def _same_user(value: str | None, user_sid: str) -> bool:
    if value == user_sid:
        return True
    if (not isinstance(value, str) or not value or len(value) > 256
            or value.startswith("S-") or any(char in value for char in "\x00\r\n")):
        return False
    # Task Scheduler exports a logon trigger's SID as a qualified account name.
    # Resolve that name through Windows; textual similarity is never ownership.
    return _resolve_account_sid(value) == user_sid


def _task_mismatches(xml: str, root: Path, user_sid: str, powershell: Path) -> list[str]:
    """Return guard names only; diagnostics never expose task values or content."""
    tree = _xml_tree(xml)
    principals = tree.findall("Principals/Principal")
    actions = tree.findall("Actions/*")
    triggers = tree.findall("Triggers/*")
    checks = [
        ("Task", tree.tag == "Task"),
        ("RegistrationInfo.URI", tree.findtext("RegistrationInfo/URI") == "\\" + task_name(root)),
        ("Principals.count", len(principals) == 1),
        ("Principal.UserId", len(principals) == 1 and principals[0].findtext("UserId") == user_sid),
        ("Principal.LogonType", len(principals) == 1 and principals[0].findtext("LogonType") == "InteractiveToken"),
        ("Principal.RunLevel", len(principals) == 1 and principals[0].findtext("RunLevel", "LeastPrivilege") == "LeastPrivilege"),
        ("Actions.count", len(actions) == 1),
        ("Action.type", len(actions) == 1 and actions[0].tag == "Exec"),
        ("Action.Command", len(actions) == 1 and actions[0].findtext("Command") == str(powershell)),
        ("Action.Arguments", len(actions) == 1 and actions[0].findtext("Arguments") == task_arguments(root)),
        ("Action.WorkingDirectory", len(actions) == 1 and actions[0].findtext("WorkingDirectory") == str(root)),
        ("Triggers.count", len(triggers) == 2),
        ("Triggers.types", {trigger.tag for trigger in triggers} == {"CalendarTrigger", "LogonTrigger"}),
        ("CalendarTrigger.DaysInterval", tree.findtext("Triggers/CalendarTrigger/ScheduleByDay/DaysInterval") == "1"),
        ("LogonTrigger.UserId", _same_user(tree.findtext("Triggers/LogonTrigger/UserId"), user_sid)),
        ("LogonTrigger.Delay", tree.findtext("Triggers/LogonTrigger/Delay") == "PT5M"),
        ("Triggers.Enabled", all(trigger.findtext("Enabled", "true") == "true" for trigger in triggers)),
        ("Settings.Enabled", tree.findtext("Settings/Enabled", "true") == "true"),
    ]
    return [name for name, matches in checks if not matches]


def _expected_task(xml: str, root: Path, user_sid: str, powershell: Path) -> bool:
    return not _task_mismatches(xml, root, user_sid, powershell)


@contextmanager
def _scheduler():
    if sys.platform != "win32":
        raise RuntimeError("Automatic updates require native Windows Task Scheduler")
    import pythoncom
    import win32com.client
    import win32security
    from .platform_support import _user_sid
    pythoncom.CoInitialize()
    try:
        user_sid = win32security.ConvertSidToStringSid(_user_sid())
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        yield service, service.GetFolder("\\"), user_sid
    finally:
        pythoncom.CoUninitialize()


def _get_task(folder, name: str):
    try:
        return folder.GetTask(name)
    except Exception as exc:
        code = getattr(exc, "hresult", None)
        if code is None and exc.args and isinstance(exc.args[0], int):
            code = exc.args[0]
        if isinstance(code, int) and code & 0xFFFFFFFF == 0x80020009:  # DISP_E_EXCEPTION
            # IDispatch wraps Task Scheduler's actual HRESULT in EXCEPINFO.
            # Only its exact FILE_NOT_FOUND value means this task is absent.
            info = getattr(exc, "excepinfo", None)
            if info is None and len(exc.args) >= 3:
                info = exc.args[2]
            if isinstance(info, (tuple, list)) and len(info) == 6 and type(info[5]) is int:
                code = info[5]
        if type(code) is int and code & 0xFFFFFFFF == 0x80070002:
            return None
        raise RuntimeError("Unable to inspect this installation's updater task") from exc


def _snapshot(root: Path) -> tuple[bytes | None, dict | None]:
    content = read_source(root / SNAPSHOT)
    if content is None:
        return None, None
    value = json.loads(content)
    if (not isinstance(value, dict) or set(value) != {"name", "xml_sha256", "user_sid"}
            or value["name"] != task_name(root)
            or not isinstance(value["xml_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["xml_sha256"])):
        raise RuntimeError("Invalid saved Windows updater schedule")
    return content, value


def _owned(task, snapshot: dict | None, root: Path, sid: str, powershell: Path) -> bool:
    return bool(task is not None and snapshot and snapshot["user_sid"] == sid
                and snapshot["xml_sha256"] == _xml_digest(task.Xml)
                and _expected_task(task.Xml, root, sid, powershell))


def schedule_status(root: Path) -> dict:
    validate_root(root)
    _, snapshot = _snapshot(root)
    with _scheduler() as (_, folder, sid):
        task = _get_task(folder, task_name(root))
        if task is None:
            return {"task_name": task_name(root), "enabled": False, "owned": False,
                    "state": "removed_by_owner" if snapshot else "absent"}
        owned = _owned(task, snapshot, root, sid, _powershell())
        return {"task_name": task_name(root), "enabled": bool(task.Enabled) if owned else False,
                "owned": owned, "state": "enabled" if owned and task.Enabled else "modified_by_owner"}


def configure_schedule(root: Path, enabled: bool) -> dict:
    validate_root(root)
    current_version(root)
    before, snapshot = _snapshot(root)
    name, powershell = task_name(root), _powershell()
    with _scheduler() as (service, folder, sid):
        task = _get_task(folder, name)
        if task is not None and not _owned(task, snapshot, root, sid, powershell):
            raise RuntimeError("The Windows updater task was edited or is not owned; it was preserved")
        if not enabled:
            if task is not None:
                # Recheck the exact saved XML immediately before scoped deletion.
                if not _owned(_get_task(folder, name), snapshot, root, sid, powershell):
                    raise RuntimeError("Windows updater task changed concurrently; it was preserved")
                folder.DeleteTask(name, 0)
            if before is not None:
                if read_source(root / SNAPSHOT) != before:
                    raise RuntimeError("Windows updater schedule changed concurrently")
                (root / SNAPSHOT).unlink()
            return {"task_name": name, "enabled": False, "owned": False, "state": "absent"}
        if task is not None:
            return {"task_name": name, "enabled": True, "owned": True, "state": "enabled"}
        if snapshot is not None:
            raise RuntimeError("The Windows updater task was removed by its owner; run updates off then on to restore it")
        from .windows_install import windows_launcher_text
        if read_source(root / "update.ps1") != windows_launcher_text(root, "telegram_search_mcp.windows_updates").encode():
            raise RuntimeError("The stable Windows updater launcher is missing or was edited; it was preserved")
        definition = service.NewTask(0)
        definition.XmlText = task_definition(root, sid, powershell)
        # No password, service account, elevation, registration trigger or Run().
        task = folder.RegisterTaskDefinition(
            name, definition, TASK_CREATE | TASK_IGNORE_REGISTRATION_TRIGGERS,
            sid, None, TASK_LOGON_INTERACTIVE_TOKEN,
            f"O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)",
        )
        xml = task.Xml
        value = {"name": name, "user_sid": sid, "xml_sha256": _xml_digest(xml)}
        try:
            mismatches = _task_mismatches(xml, root, sid, powershell)
            if mismatches:
                raise RuntimeError("Task Scheduler returned an unexpected updater task: " + ", ".join(mismatches))
            atomic_write(root / SNAPSHOT, (json.dumps(value, indent=2) + "\n").encode(), expected=before)
        except BaseException:
            current = _get_task(folder, name)
            if current is not None and _xml_digest(current.Xml) == value["xml_sha256"]:
                folder.DeleteTask(name, 0)
            raise
        return {"task_name": name, "enabled": True, "owned": True, "state": "enabled"}
