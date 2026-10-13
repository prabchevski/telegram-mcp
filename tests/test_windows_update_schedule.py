"""Task Scheduler is mocked here; native archive CI exercises the Windows COM API."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_search_mcp import windows_update_schedule as schedule
from telegram_search_mcp.platform_support import ensure_private_dir
from telegram_search_mcp.windows_install import windows_launcher_text

SID = "S-1-5-21-111-222-333-1001"


class MissingTaskError(Exception):
    hresult = -2147024894


class WrappedTaskError(Exception):
    hresult = -2147352567
    def __init__(self, inner, *, expose_attribute=True):
        info = (0, None, None, None, 0, inner)
        if expose_attribute:
            self.excepinfo = info
        super().__init__(self.hresult, 'Exception occurred.', info, None)


@pytest.mark.parametrize('expose_attribute', [False, True])
def test_native_dispatch_missing_task_is_absent(expose_attribute):
    def missing(_name):
        raise WrappedTaskError(-2147024894, expose_attribute=expose_attribute)
    folder = SimpleNamespace(GetTask=missing)
    assert schedule._get_task(folder, 'only-this-task') is None


@pytest.mark.parametrize('inner', [-2147024891, -2147467259])
def test_native_dispatch_access_denied_and_other_errors_fail_closed(inner):
    def failed(_name):
        raise WrappedTaskError(inner)
    folder = SimpleNamespace(GetTask=failed)
    with pytest.raises(RuntimeError, match='Unable to inspect') as result:
        schedule._get_task(folder, 'only-this-task')
    assert isinstance(result.value.__cause__, WrappedTaskError)


def test_nested_not_found_does_not_override_an_unrelated_outer_error():
    error = WrappedTaskError(-2147024894)
    error.hresult = -2147024891
    def failed(_name):
        raise error
    with pytest.raises(RuntimeError, match='Unable to inspect'):
        schedule._get_task(SimpleNamespace(GetTask=failed), 'only-this-task')


class FakeTask:
    def __init__(self, xml):
        self.Xml = xml

    @property
    def Enabled(self):
        return schedule._xml_tree(self.Xml).findtext("Settings/Enabled", "true") == "true"

    def Run(self, *_):
        pytest.fail("Setup must never run an updater task")


class FakeFolder:
    def __init__(self):
        self.tasks = {}
        self.registered = []
        self.deleted = []
        self.normalize = lambda xml: xml

    def GetTask(self, name):
        if name not in self.tasks:
            raise MissingTaskError()
        return self.tasks[name]

    def RegisterTaskDefinition(self, name, definition, flags, user, password, logon, sddl):
        assert name not in self.tasks, "TASK_CREATE must not replace existing tasks"
        self.registered.append((name, flags, user, password, logon, sddl))
        task = FakeTask(self.normalize(definition.XmlText))
        self.tasks[name] = task
        return task

    def DeleteTask(self, name, flags):
        assert flags == 0
        self.deleted.append(name)
        del self.tasks[name]


@pytest.fixture
def native_mock(tmp_path, monkeypatch):
    root = tmp_path / "Телеграм with spaces"
    ensure_private_dir(root)
    (root / "update.ps1").write_bytes(windows_launcher_text(root, "telegram_search_mcp.windows_updates").encode())
    folder = FakeFolder()
    service = SimpleNamespace(NewTask=lambda _: SimpleNamespace(XmlText=""))
    @contextmanager
    def connected():
        yield service, folder, SID
    monkeypatch.setattr(schedule, "_scheduler", connected)
    monkeypatch.setattr(schedule, "validate_root", lambda _: None)
    monkeypatch.setattr(schedule, "current_version", lambda _: root / "version-0.11.0-abcdef012345")
    powershell = Path(tmp_path.anchor) / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    monkeypatch.setattr(schedule, "_powershell", lambda: powershell)
    return root, folder


def test_per_user_schedule_has_only_daily_logon_and_owned_system_action(native_mock):
    root, folder = native_mock
    result = schedule.configure_schedule(root, True)
    assert result == schedule.schedule_status(root)
    assert result["enabled"] is True and result["owned"] is True
    assert len(folder.registered) == 1
    name, flags, user, password, logon, sddl = folder.registered[0]
    assert name == schedule.task_name(root)
    assert flags == schedule.TASK_CREATE | schedule.TASK_IGNORE_REGISTRATION_TRIGGERS
    assert user == SID and password is None and logon == 3
    assert sddl == f"O:{SID}D:P(A;;FA;;;{SID})(A;;FA;;;SY)"
    tree = schedule._xml_tree(folder.tasks[name].Xml)
    assert tree.findtext("Principals/Principal/RunLevel") == "LeastPrivilege"
    assert tree.findtext("Settings/AllowStartOnDemand") == "false"
    assert tree.findtext("Settings/StartWhenAvailable") == "false"
    assert tree.findtext("Settings/AllowHardTerminate") == "false"
    assert tree.findtext("Settings/ExecutionTimeLimit") == "PT0S"
    assert {item.tag for item in tree.findall("Triggers/*")} == {"CalendarTrigger", "LogonTrigger"}
    assert tree.findtext("Actions/Exec/Arguments") == schedule.task_arguments(root)
    assert "--scheduled" in schedule.task_arguments(root)
    assert "-Command" not in schedule.task_arguments(root)
    schedule.configure_schedule(root, True)
    assert len(folder.registered) == 1
    schedule.configure_schedule(root, False)
    assert folder.deleted == [name]
    assert schedule.schedule_status(root)["state"] == "absent"
    assert not (root / schedule.SNAPSHOT).exists()


@pytest.mark.parametrize("old,new", [
    ("LeastPrivilege", "HighestAvailable"), ("InteractiveToken", "Password"),
    ("--scheduled", "--different"), ("<DaysInterval>1</DaysInterval>", "<DaysInterval>2</DaysInterval>"),
    ("<Enabled>true</Enabled>", "<Enabled>false</Enabled>"),
])
def test_owner_task_changes_are_not_replaced_or_deleted(native_mock, old, new):
    root, folder = native_mock
    schedule.configure_schedule(root, True)
    task = folder.tasks[schedule.task_name(root)]
    task.Xml = task.Xml.replace(old, new)
    assert schedule.schedule_status(root)["state"] == "modified_by_owner"
    for enabled in (False, True):
        with pytest.raises(RuntimeError, match="preserved"):
            schedule.configure_schedule(root, enabled)
    assert folder.deleted == [] and len(folder.registered) == 1


def test_unknown_colliding_task_and_unrelated_tasks_are_preserved(native_mock):
    root, folder = native_mock
    folder.tasks["unrelated"] = FakeTask("unrelated task")
    folder.tasks[schedule.task_name(root)] = FakeTask(schedule.task_definition(root, SID, schedule._powershell()))
    with pytest.raises(RuntimeError, match="not owned"):
        schedule.configure_schedule(root, True)
    with pytest.raises(RuntimeError, match="not owned"):
        schedule.configure_schedule(root, False)
    assert folder.deleted == [] and folder.registered == []
    assert "unrelated" in folder.tasks


def test_owner_removal_is_not_restored_by_an_update(native_mock):
    root, folder = native_mock
    schedule.configure_schedule(root, True)
    del folder.tasks[schedule.task_name(root)]
    assert schedule.schedule_status(root)["state"] == "removed_by_owner"
    with pytest.raises(RuntimeError, match="removed by its owner"):
        schedule.configure_schedule(root, True)
    schedule.configure_schedule(root, False)
    schedule.configure_schedule(root, True)
    assert len(folder.registered) == 2


def test_concurrent_owner_task_removal_is_reported_without_an_unscoped_delete(native_mock, monkeypatch):
    root, folder = native_mock
    schedule.configure_schedule(root, True)
    original = folder.GetTask
    reads = []
    def remove_during_recheck(name):
        reads.append(name)
        if len(reads) == 2:
            del folder.tasks[name]
        return original(name)
    monkeypatch.setattr(folder, 'GetTask', remove_during_recheck)
    with pytest.raises(RuntimeError, match='changed concurrently'):
        schedule.configure_schedule(root, False)
    assert folder.deleted == []


def test_schedule_creation_failure_removes_only_its_unmodified_new_task(native_mock, monkeypatch):
    root, folder = native_mock
    folder.tasks["unrelated"] = FakeTask("unrelated")
    def fail(*_, **__):
        raise RuntimeError("synthetic metadata persistence failure")
    monkeypatch.setattr(schedule, "atomic_write", fail)
    with pytest.raises(RuntimeError, match="persistence failure"):
        schedule.configure_schedule(root, True)
    assert folder.deleted == [schedule.task_name(root)]
    assert set(folder.tasks) == {"unrelated"}
    assert not (root / schedule.SNAPSHOT).exists()


def test_unexpected_registered_definition_is_removed_before_reporting_failure(native_mock):
    root, folder = native_mock
    folder.normalize = lambda xml: xml.replace("--scheduled", "--other")
    with pytest.raises(RuntimeError, match="unexpected updater"):
        schedule.configure_schedule(root, True)
    assert folder.deleted == [schedule.task_name(root)]


def test_task_mismatch_diagnostics_report_only_guard_names(native_mock):
    root, _ = native_mock
    xml = schedule.task_definition(root, SID, schedule._powershell())
    assert schedule._task_mismatches(xml, root, SID, schedule._powershell()) == []
    normalized = xml.replace(SID, 'HOSTNAME\\account-name')
    result = schedule._task_mismatches(normalized, root, SID, schedule._powershell())
    assert result == ['Principal.UserId', 'LogonTrigger.UserId']
    assert 'account-name' not in str(result)


def native_exported_xml(xml, account='HOSTNAME\\owner'):
    import xml.etree.ElementTree as ET
    tree = schedule._xml_tree(xml)
    principal = tree.find('Principals/Principal')
    principal.remove(principal.find('RunLevel'))
    for trigger in tree.findall('Triggers/*'):
        trigger.remove(trigger.find('Enabled'))
    settings = tree.find('Settings')
    settings.remove(settings.find('Enabled'))
    tree.find('Triggers/LogonTrigger/UserId').text = account
    return ET.tostring(tree, encoding='unicode')


def test_actual_windows_export_defaults_and_current_account_name_are_owned(native_mock, monkeypatch):
    root, folder = native_mock
    monkeypatch.setattr(schedule, '_resolve_account_sid', lambda name: SID if name == 'HOSTNAME\\owner' else None)
    folder.normalize = native_exported_xml
    assert schedule.configure_schedule(root, True)['owned'] is True
    assert schedule.schedule_status(root)['enabled'] is True
    assert schedule.configure_schedule(root, True)['owned'] is True
    assert len(folder.registered) == 1
    schedule.configure_schedule(root, False)
    assert folder.deleted == [schedule.task_name(root)]


@pytest.mark.parametrize('element,value,guard', [
    ('Principals/Principal/RunLevel', 'HighestAvailable', 'Principal.RunLevel'),
    ('Triggers/CalendarTrigger/Enabled', 'false', 'Triggers.Enabled'),
    ('Triggers/LogonTrigger/Enabled', 'false', 'Triggers.Enabled'),
    ('Settings/Enabled', 'false', 'Settings.Enabled'),
])
def test_missing_native_defaults_do_not_accept_explicit_elevation_or_disabled_fields(
    native_mock, monkeypatch, element, value, guard,
):
    import xml.etree.ElementTree as ET
    root, _ = native_mock
    monkeypatch.setattr(schedule, '_resolve_account_sid', lambda _: SID)
    tree = schedule._xml_tree(native_exported_xml(schedule.task_definition(root, SID, schedule._powershell())))
    parent_name, field = element.rsplit('/', 1)
    ET.SubElement(tree.find(parent_name), field).text = value
    mismatches = schedule._task_mismatches(ET.tostring(tree, encoding='unicode'), root, SID, schedule._powershell())
    assert guard in mismatches


@pytest.mark.parametrize('resolved', [None, 'S-1-5-21-111-222-333-1002'])
def test_unknown_or_other_account_name_never_matches_current_user(native_mock, monkeypatch, resolved):
    root, _ = native_mock
    monkeypatch.setattr(schedule, '_resolve_account_sid', lambda _: resolved)
    xml = native_exported_xml(schedule.task_definition(root, SID, schedule._powershell()), 'HOSTNAME\\other')
    assert schedule._task_mismatches(xml, root, SID, schedule._powershell()) == ['LogonTrigger.UserId']


def test_direct_current_sid_requires_no_lookup_and_other_sid_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(schedule, '_resolve_account_sid', lambda _: pytest.fail('A SID must not be reinterpreted as an account name'))
    assert schedule._same_user(SID, SID) is True
    assert schedule._same_user('S-1-5-21-111-222-333-1002', SID) is False


def test_denied_native_account_lookup_fails_closed(monkeypatch):
    import sys
    def denied(*_):
        raise PermissionError('Synthetic lookup access denied')
    monkeypatch.setattr(schedule, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setitem(sys.modules, 'win32security', SimpleNamespace(LookupAccountName=denied))
    assert schedule._resolve_account_sid('HOSTNAME\\owner') is None


def test_exact_saved_native_xml_still_rejects_owner_edits(native_mock, monkeypatch):
    root, folder = native_mock
    monkeypatch.setattr(schedule, '_resolve_account_sid', lambda _: SID)
    folder.normalize = native_exported_xml
    schedule.configure_schedule(root, True)
    task = folder.tasks[schedule.task_name(root)]
    # The account name remains the same SID, but changing the saved definition
    # is still an owner edit and must not be silently restored or removed.
    task.Xml = task.Xml.replace('HOSTNAME\\owner', 'HOSTNAME\\renamed-owner')
    assert schedule.schedule_status(root)['state'] == 'modified_by_owner'
    with pytest.raises(RuntimeError, match='preserved'):
        schedule.configure_schedule(root, False)
    assert folder.deleted == []


def test_edited_launcher_is_preserved_and_never_registered(native_mock):
    root, folder = native_mock
    (root / "update.ps1").write_text("owner launcher", encoding="utf-8")
    with pytest.raises(RuntimeError, match="launcher.*edited"):
        schedule.configure_schedule(root, True)
    assert folder.registered == []


def test_saved_task_definition_hash_ignores_only_xml_formatting(native_mock):
    root, folder = native_mock
    schedule.configure_schedule(root, True)
    task = folder.tasks[schedule.task_name(root)]
    original = schedule._xml_digest(task.Xml)
    task.Xml = task.Xml.replace("><", ">\n  <")
    assert schedule._xml_digest(task.Xml) == original
    assert schedule.schedule_status(root)["owned"] is True
    assert json.loads((root / schedule.SNAPSHOT).read_bytes())["xml_sha256"] == original


def test_native_com_is_not_imported_by_bootstrap_module():
    # Running in a fresh isolated process catches eager optional imports even
    # when another Windows test already imported pywin32 in this pytest process.
    import subprocess
    import sys
    subprocess.run([sys.executable, "-I", "-c",
                    "import sys; import telegram_search_mcp.windows_update_schedule; "
                    "assert 'win32com.client' not in sys.modules; assert 'pythoncom' not in sys.modules"],
                   check=True, capture_output=True)
