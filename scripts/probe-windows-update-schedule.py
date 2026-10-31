#!/usr/bin/env python3
"""Print native Task Scheduler normalization using one inert disposable task.

The probe does not run the task, touch a Telegram profile, or inspect other tasks.
Both triggers start tomorrow and its temporary PowerShell script only exits.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET

from telegram_search_mcp import windows_update_schedule as schedule
from telegram_search_mcp.windows_install import powershell_path


def main() -> None:
    if sys.platform != "win32":
        raise RuntimeError("This isolated probe requires native Windows")
    with tempfile.TemporaryDirectory(prefix="telegram schedule probe ") as temporary:
        root = Path(temporary).resolve()
        (root / "update.ps1").write_text("exit 0\n", encoding="ascii")
        name, powershell = schedule.task_name(root), powershell_path()
        with schedule._scheduler() as (service, folder, sid):
            if schedule._get_task(folder, name) is not None:
                raise RuntimeError("Probe name already exists; it was preserved")
            generated = schedule.task_definition(root, sid, powershell)
            tree = ET.fromstring(generated)
            namespace = "{" + schedule.TASK_NAMESPACE + "}"
            logon = tree.find(namespace + "Triggers/" + namespace + "LogonTrigger")
            boundary = ET.Element(namespace + "StartBoundary")
            boundary.text = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
            logon.insert(0, boundary)
            generated = ET.tostring(tree, encoding="unicode")
            definition = service.NewTask(0)
            definition.XmlText = generated
            registered_xml = None
            try:
                task = folder.RegisterTaskDefinition(
                    name, definition, schedule.TASK_CREATE | schedule.TASK_IGNORE_REGISTRATION_TRIGGERS,
                    sid, None, schedule.TASK_LOGON_INTERACTIVE_TOKEN,
                    f"O:{sid}D:P(A;;FA;;;{sid})(A;;FA;;;SY)",
                )
                registered_xml = task.Xml
                print(json.dumps({
                    "probe": "task_scheduler_normalization", "task_name": name,
                    "generated_xml": generated, "registered_xml": registered_xml,
                    "mismatches": schedule._task_mismatches(registered_xml, root, sid, powershell),
                    "native_principal_run_level": int(task.Definition.Principal.RunLevel),
                    "native_enabled": bool(task.Enabled),
                    "native_trigger_enabled": [bool(task.Definition.Triggers.Item(index).Enabled)
                                               for index in range(1, task.Definition.Triggers.Count + 1)],
                    "task_run_called": False, "telegram_profile_accessed": False,
                }, ensure_ascii=True), flush=True)
            finally:
                if registered_xml is not None:
                    task = schedule._get_task(folder, name)
                    if task is not None:
                        if schedule._xml_digest(task.Xml) != schedule._xml_digest(registered_xml):
                            raise RuntimeError("Probe task changed concurrently; it was preserved")
                        folder.DeleteTask(name, 0)
                    print("PASS: only the unchanged disposable probe task was removed; no task run or Telegram profile access", flush=True)


if __name__ == "__main__":
    main()
