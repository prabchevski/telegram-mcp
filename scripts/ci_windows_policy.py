#!/usr/bin/env python3
"""Decide whether this revision needs fresh Windows 10/11 installer checks.

The baseline is a successful canonical main-push CI run, not the previous commit:
a failed or cancelled risky change must remain in the next run's comparison.
Only reviewed fast paths can skip desktop VMs. Missing evidence fails closed.
This script reads Git/GitHub metadata; it never fetches or changes a checkout.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib
from typing import NamedTuple


REPOSITORY = "prabchevski/telegram-mcp"
WORKFLOW = ".github/workflows/ci.yml"
SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PACKAGE = "src/telegram_search_mcp/"
DOC_FILES = frozenset({
    "AGENTS.md", "CHANGELOG.md", "README.md", "VERIFICATION.md", "LICENSE", "NOTICE",
    "INSTALL.md", "INSTALL_MACOS.md", "INSTALL_WINDOWS.md", "START_HERE.md",
    "UNINSTALL_MACOS.md", "UNINSTALL_WINDOWS.md",
})
# These modules implement OS-independent tool behavior. Windows Server plus the
# mandatory Linux/macOS checks still exercise them on every revision. Bootstrap,
# configuration, storage, credentials, transports, native loading and unknown new
# modules deliberately do not qualify for this allowlist.
COMMON_CORE = frozenset({
    "backend.py", "chat_drafts.py", "diagnostics.py", "editing.py", "editing_tools.py",
    "models.py", "navigation.py", "outgoing.py", "public_posts.py", "speech.py",
    "tool_catalog.py", "workflow_tools.py", "workflows.py",
})


class PolicyError(Exception):
    """A static, safe reason why skipping desktop checks cannot be justified."""


class Change(NamedTuple):
    path: str
    status: str
    old_mode: str
    new_mode: str


def git(repository: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(repository), *arguments],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PolicyError("git_unavailable") from exc
    if result.returncode:
        raise PolicyError("git_failed")
    return result.stdout


def require_commit(repository: Path, revision: str, label: str) -> None:
    if not isinstance(revision, str) or SHA.fullmatch(revision) is None:
        raise PolicyError("invalid_" + label)
    if git(repository, "cat-file", "-t", revision).strip() != b"commit":
        raise PolicyError("invalid_" + label)


def is_ancestor(repository: Path, baseline: str, head: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repository),
             "merge-base", "--is-ancestor", baseline, head],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PolicyError("git_unavailable") from exc
    if result.returncode not in (0, 1):
        raise PolicyError("baseline_history_unavailable")
    return result.returncode == 0


def github_runs(page: int) -> dict:
    endpoint = (f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs"
                f"?branch=main&event=push&status=success&per_page=100&page={page}")
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
            capture_output=True, timeout=45, check=False,
        )
        if result.returncode:
            raise PolicyError("baseline_api_unavailable")
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PolicyError("baseline_api_unavailable") from exc
    except (ValueError, UnicodeError) as exc:
        raise PolicyError("baseline_api_malformed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise PolicyError("baseline_api_malformed")
    return payload


def discover_baseline(repository: Path, head: str, current_run_id: str | None) -> tuple[str, int]:
    if current_run_id and (not current_run_id.isdecimal() or int(current_run_id) <= 0):
        raise PolicyError("invalid_current_run_id")
    excluded_id = int(current_run_id) if current_run_id else None
    # A bounded search can conservatively run VMs if old history is unavailable;
    # there is no safety reason to skip on an API outage or an unbounded history.
    for page in range(1, 4):
        runs = github_runs(page)["workflow_runs"]
        candidates = []
        for run in runs:
            if not isinstance(run, dict):
                raise PolicyError("baseline_api_malformed")
            if (not isinstance(run.get("repository"), dict)
                    or not isinstance(run.get("head_repository"), dict)):
                raise PolicyError("baseline_api_malformed")
            if (run.get("event") != "push" or run.get("head_branch") != "main"
                    or run.get("path") != WORKFLOW or run.get("status") != "completed"
                    or run.get("conclusion") != "success"
                    or run.get("repository", {}).get("full_name") != REPOSITORY
                    or run.get("head_repository", {}).get("full_name") != REPOSITORY):
                continue
            run_id, revision = run.get("id"), run.get("head_sha")
            if type(run_id) is not int or run_id <= 0 or not isinstance(revision, str) or not SHA.fullmatch(revision):
                raise PolicyError("baseline_api_malformed")
            if run_id == excluded_id:
                continue
            try:
                created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                if created.tzinfo is None:
                    raise ValueError("Missing timezone")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise PolicyError("baseline_api_malformed") from exc
            candidates.append((created, run_id, revision))
        for _, run_id, revision in sorted(candidates, reverse=True):
            require_commit(repository, revision, "baseline")
            if is_ancestor(repository, revision, head):
                return revision, run_id
        if len(runs) < 100:
            break
    raise PolicyError("no_successful_baseline")


def changed_files(repository: Path, baseline: str, head: str) -> list[Change]:
    raw = git(repository, "diff", "--raw", "--abbrev=40", "-z", "--no-renames",
              "--no-ext-diff", "--no-textconv", "--no-color", baseline, head, "--")
    if not raw:
        return []
    fields = raw.split(b"\0")
    if fields.pop() != b"" or len(fields) % 2:
        raise PolicyError("diff_malformed")
    changes = []
    for offset in range(0, len(fields), 2):
        try:
            header = fields[offset].decode("ascii").split()
            path = fields[offset + 1].decode("utf-8")
        except UnicodeError as exc:
            raise PolicyError("diff_malformed") from exc
        if (len(header) != 5 or not re.fullmatch(r":[0-7]{6}", header[0])
                or not re.fullmatch(r"[0-7]{6}", header[1])
                or not SHA.fullmatch(header[2]) or not SHA.fullmatch(header[3])
                or header[4] not in {"A", "D", "M", "T"}):
            raise PolicyError("diff_malformed")
        if (not re.fullmatch(r"[A-Za-z0-9._/-]+", path) or str(PurePosixPath(path)) != path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise PolicyError("unsupported_path")
        changes.append(Change(path, header[4], header[0][1:], header[1]))
    return changes


def blob(repository: Path, revision: str, path: str) -> str:
    data = git(repository, "cat-file", "blob", f"{revision}:{path}")
    if len(data) > 4 * 1024 * 1024:
        raise PolicyError("semantic_parse_failed")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise PolicyError("semantic_parse_failed") from exc


def version_metadata(path: str, source: str) -> dict:
    data = tomllib.loads(source)
    if path == "pyproject.toml":
        project = data.get("project")
        if (not isinstance(project, dict) or project.get("name") != "telegram-search-mcp"
                or not isinstance(project.get("version"), str) or not VERSION.fullmatch(project["version"])):
            raise ValueError("Invalid project version")
        del project["version"]
    else:
        packages = data.get("package")
        if not isinstance(packages, list) or any(not isinstance(p, dict) for p in packages):
            raise ValueError("Invalid lock packages")
        own = [p for p in packages if p.get("name") == "telegram-search-mcp"]
        if (len(own) != 1 or own[0].get("source") != {"editable": "."}
                or not isinstance(own[0].get("version"), str) or not VERSION.fullmatch(own[0]["version"])):
            raise ValueError("Invalid lock project version")
        del own[0]["version"]
    return data


class WithoutDocstrings(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        return self.without_docstring(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Module = visit_FunctionDef

    def without_docstring(self, node):
        self.generic_visit(node)
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body.pop(0)
        return node


def semantic_python(path: str, source: str) -> str:
    tree = ast.parse(source, type_comments=True)
    tree = WithoutDocstrings().visit(tree)
    if path == PACKAGE + "server.py":
        # Mask ONLY literal initialization instructions in the known initializer.
        # Calls, f-strings, variable assignments and arbitrary expressions remain
        # in the tree and require desktop checks if they change.
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name != "create_server":
                continue
            for statement in function.body:
                if (not isinstance(statement, ast.Assign) or len(statement.targets) != 1
                        or not isinstance(statement.targets[0], ast.Name) or statement.targets[0].id != "mcp"
                        or not isinstance(statement.value, ast.Call)
                        or not isinstance(statement.value.func, ast.Name) or statement.value.func.id != "MCPServer"):
                    continue
                for keyword in statement.value.keywords:
                    if (keyword.arg == "instructions" and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)):
                        keyword.value.value = "<literal MCP instructions>"
    elif path == PACKAGE + "__init__.py":
        versions = []
        for statement in tree.body:
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "__version__"):
                versions.append(statement)
        if (len(versions) != 1 or not isinstance(versions[0].value, ast.Constant)
                or not isinstance(versions[0].value.value, str) or not VERSION.fullmatch(versions[0].value.value)):
            raise ValueError("Invalid package version")
        versions[0].value.value = "<package version>"
    return ast.dump(tree, include_attributes=False)


def fast_change(repository: Path, baseline: str, head: str, change: Change) -> bool:
    path = change.path
    if change.status == "T" or any(mode not in {"000000", "100644", "100755"} for mode in change[2:]):
        return False
    if change.status == "M" and change.old_mode != change.new_mode:
        return False
    if path in DOC_FILES or (path.startswith("docs/") and path.endswith((".md", ".rst", ".txt"))) or path.startswith("tests/"):
        return True
    if change.status != "M":
        return False
    if path in {PACKAGE + name for name in COMMON_CORE}:
        return True
    if path not in {"pyproject.toml", "uv.lock", PACKAGE + "server.py", PACKAGE + "__init__.py"}:
        return False
    before, after = blob(repository, baseline, path), blob(repository, head, path)
    try:
        if path in {"pyproject.toml", "uv.lock"}:
            return version_metadata(path, before) == version_metadata(path, after)
        return semantic_python(path, before) == semantic_python(path, after)
    except (ValueError, SyntaxError, TypeError, KeyError) as exc:
        raise PolicyError("semantic_parse_failed") from exc


def initial_report(head: str | None, reason: str = "policy_error") -> dict:
    return {"schema_version": 1, "required": True, "reason": reason,
            "baseline": None, "head": head if isinstance(head, str) and SHA.fullmatch(head) else None,
            "baseline_run_id": None, "changed_paths": [], "risky_paths": []}


def classify(repository: Path, head: str | None, *, base: str | None = None,
             force: bool = False, current_run_id: str | None = None) -> dict:
    report = initial_report(head)
    try:
        require_commit(repository, head, "head")
        if force:
            report["reason"] = "forced"
            return report
        if base is None:
            base, run_id = discover_baseline(repository, head, current_run_id)
            report["baseline_run_id"] = run_id
        require_commit(repository, base, "baseline")
        if not is_ancestor(repository, base, head):
            raise PolicyError("baseline_not_ancestor")
        report["baseline"] = base
        changes = changed_files(repository, base, head)
        report["changed_paths"] = [change.path for change in changes]
        report["risky_paths"] = [change.path for change in changes if not fast_change(repository, base, head, change)]
        report["required"] = bool(report["risky_paths"])
        report["reason"] = "desktop_sensitive_changes" if report["required"] else "reviewed_fast_paths" if changes else "no_changes"
    except PolicyError as exc:
        report["required"], report["reason"] = True, str(exc)
    except Exception:
        # Unexpected metadata shapes or local environment problems cannot turn a
        # mandatory installer check into an approved skip.
        report["required"], report["reason"] = True, "policy_error"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--base")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parent.parent
    report = initial_report(args.head)
    try:
        require_commit(repository, args.head, "head")
        actual_head = git(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
        if actual_head != args.head:
            report["reason"] = "head_checkout_mismatch"
        else:
            report = classify(repository, args.head, base=args.base, force=args.force,
                              current_run_id=os.environ.get("GITHUB_RUN_ID"))
    except PolicyError as exc:
        report["reason"] = str(exc)
    except (UnicodeError, ValueError):
        report["reason"] = "checkout_head_unavailable"
    output = os.environ.get("GITHUB_OUTPUT")
    exit_code = 0
    if output:
        try:
            # Only generated reasons and validated SHA strings enter this file;
            # filenames and API error text cannot inject extra output lines.
            with open(output, "a", encoding="utf-8") as stream:
                stream.write(f"required={str(report['required']).lower()}\n")
                stream.write(f"baseline={report['baseline'] or ''}\nreason={report['reason']}\n")
        except OSError:
            report["required"], report["reason"] = True, "github_output_unavailable"
            exit_code = 1
    print(json.dumps(report, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
