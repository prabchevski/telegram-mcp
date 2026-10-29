from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_windows_policy", ROOT / "scripts/ci_windows_policy.py")
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)

PYPROJECT = '''[project]
name = "telegram-search-mcp"
version = "0.11.1"
requires-python = ">=3.12,<3.15"
dependencies = ["mcp==2.0.0"]
[project.scripts]
tgsearch = "telegram_search_mcp.cli:main"
[build-system]
requires = ["hatchling==1.31.0"]
build-backend = "hatchling.build"
'''
LOCK = '''version = 1
revision = 3
requires-python = ">=3.12,<3.15"
[[package]]
name = "mcp"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "telegram-search-mcp"
version = "0.11.1"
source = { editable = "." }
dependencies = [{name = "mcp"}]
'''
INIT = '__version__ = "0.11.1"\n'
SERVER = '''from somewhere import MCPServer

def create_server(backend):
    """Create the server."""
    mcp = MCPServer("telegram-search", instructions=("Read history first. " "Ask for public-search consent."))
    async def transcribe():
        """Only use on a separate request."""
        return backend
    return mcp
'''


class Repository:
    def __init__(self, root):
        self.root = root
        self.run("init", "--initial-branch=main")
        self.run("config", "user.name", "CI policy test")
        self.run("config", "user.email", "ci-policy@example.invalid")
        self.run("config", "commit.gpgsign", "false")
        self.run("config", "core.autocrlf", "false")

    def run(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout.decode().strip()

    def commit(self, changes):
        for path, content in changes.items():
            target = self.root / path
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        self.run("add", "-A")
        self.run("commit", "-m", "Policy fixture")
        return self.run("rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    repo = Repository(tmp_path)
    repo.base = repo.commit({
        "README.md": "Base documentation.\n",
        "pyproject.toml": PYPROJECT,
        "uv.lock": LOCK,
        policy.PACKAGE + "__init__.py": INIT,
        policy.PACKAGE + "server.py": SERVER,
        policy.PACKAGE + "windows_install.py": "VALUE = 1\n",
        policy.PACKAGE + "speech.py": "VALUE = 1\n",
    })
    return repo


def run_record(revision, run_id=10, **changes):
    row = {
        "id": run_id, "head_sha": revision, "event": "push", "head_branch": "main",
        "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success",
        "created_at": f"2026-10-29T10:{run_id % 60:02}:00Z",
        "repository": {"full_name": "prabchevski/telegram-mcp"},
        "head_repository": {"full_name": "prabchevski/telegram-mcp"},
    }
    return row | changes


def baseline_api(monkeypatch, *runs):
    monkeypatch.setattr(policy, "github_runs", lambda page: {"workflow_runs": list(runs) if page == 1 else []})


def decision(repository, head, **options):
    return policy.classify(repository.root, head, **options)


@pytest.mark.parametrize("prior_conclusion", ["failure", "cancelled", "timed_out"])
def test_failed_risky_change_is_not_hidden_by_later_docs_push(repository, monkeypatch, prior_conclusion):
    risky = repository.commit({policy.PACKAGE + "windows_install.py": "VALUE = 2\n"})
    head = repository.commit({"README.md": "Later docs-only push.\n"})
    baseline_api(monkeypatch, run_record(risky, 20, conclusion=prior_conclusion), run_record(repository.base))

    result = decision(repository, head)

    assert result["required"] is True
    assert result["baseline"] == repository.base
    assert result["risky_paths"] == [policy.PACKAGE + "windows_install.py"]
    # The previous-commit shortcut would incorrectly allow this skip.
    assert decision(repository, head, base=risky)["required"] is False


def test_newest_successful_canonical_ancestor_is_used_not_current_or_other_runs(repository, monkeypatch):
    fast = repository.commit({"README.md": "A verified change.\n"})
    risky = repository.commit({policy.PACKAGE + "windows_install.py": "VALUE = 2\n"})
    baseline_api(monkeypatch,
                 run_record(repository.base, 10),
                 run_record(risky, 40),  # Current rerun must not approve itself.
                 run_record(fast, 20),
                 run_record(risky, 30, event="pull_request"))
    result = decision(repository, risky, current_run_id="40")
    assert result["required"] is True
    assert result["baseline"] == fast
    assert result["baseline_run_id"] == 20


@pytest.mark.parametrize("changed", [
    {"event": "workflow_dispatch"}, {"head_branch": "other"},
    {"path": ".github/workflows/other.yml"}, {"status": "in_progress"},
    {"conclusion": "cancelled"}, {"repository": {"full_name": "someone/fork"}},
    {"head_repository": {"full_name": "someone/fork"}},
])
def test_wrong_workflow_account_or_event_cannot_supply_baseline(repository, monkeypatch, changed):
    baseline_api(monkeypatch, run_record(repository.base, **changed))
    result = decision(repository, repository.base)
    assert result["required"] is True
    assert result["reason"] == "no_successful_baseline"


def test_success_on_nonancestor_branch_does_not_hide_main_changes(repository, monkeypatch):
    repository.run("checkout", "-b", "unrelated")
    other = repository.commit({"README.md": "Branch-only docs.\n"})
    repository.run("checkout", "main")
    head = repository.commit({"README.md": "Main docs.\n"})
    baseline_api(monkeypatch, run_record(other, 30), run_record(repository.base))
    result = decision(repository, head)
    assert result["required"] is False
    assert result["baseline"] == repository.base
    assert decision(repository, head, base=other)["reason"] == "baseline_not_ancestor"


def test_unknown_first_run_and_api_errors_require_desktops(repository, monkeypatch):
    baseline_api(monkeypatch)
    assert decision(repository, repository.base)["reason"] == "no_successful_baseline"
    monkeypatch.setattr(policy, "github_runs", lambda page: (_ for _ in ()).throw(policy.PolicyError("baseline_api_unavailable")))
    result = decision(repository, repository.base)
    assert result["required"] is True
    assert result["reason"] == "baseline_api_unavailable"


@pytest.mark.parametrize("field,value", [
    ("repository", None), ("repository", []), ("head_repository", None),
    ("head_repository", "prabchevski/telegram-mcp"), ("head_sha", "main"),
    ("id", "10"), ("created_at", "not-a-date"),
])
def test_malformed_nested_run_metadata_requires_desktops(repository, monkeypatch, field, value):
    baseline_api(monkeypatch, run_record(repository.base, **{field: value}))
    result = decision(repository, repository.base)
    assert result["required"] is True
    assert result["reason"] == "baseline_api_malformed"


@pytest.mark.parametrize("bad", [None, "HEAD", "-n", "a" * 39, "A" * 40, "0" * 40 + "\nrequired=false", "$(touch bad)"])
def test_malicious_or_ambiguous_head_never_reaches_git_or_api(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(policy, "git", lambda *a: pytest.fail("Unsafe head reached git"))
    monkeypatch.setattr(policy, "github_runs", lambda *a: pytest.fail("Unsafe head reached API"))
    result = policy.classify(tmp_path, bad)
    assert result["required"] is True
    assert result["reason"] == "invalid_head"


def test_unknown_commit_and_bad_explicit_base_fail_closed(repository):
    assert decision(repository, "f" * 40)["required"] is True
    assert decision(repository, repository.base, base="main")["reason"] == "invalid_baseline"


@pytest.mark.parametrize("malformed", [b"unparseable", b":100644 100644 bad bad M\0README.md\0"])
def test_malformed_git_diff_cannot_approve_skip(repository, monkeypatch, malformed):
    original = policy.git
    def corrupt_diff(root, *arguments):
        return malformed if arguments[0] == "diff" else original(root, *arguments)
    monkeypatch.setattr(policy, "git", corrupt_diff)
    result = decision(repository, repository.base, base=repository.base)
    assert result["required"] is True
    assert result["reason"] == "diff_malformed"


def test_force_validates_head_but_needs_no_api(repository, monkeypatch):
    monkeypatch.setattr(policy, "github_runs", lambda *a: pytest.fail("Force should need no API"))
    result = decision(repository, repository.base, force=True)
    assert result["required"] is True and result["reason"] == "forced"
    assert result["baseline"] is None
    assert decision(repository, "-bad", force=True)["reason"] == "invalid_head"


def test_version_bump_alone_does_not_schedule_desktop_vms(repository):
    head = repository.commit({"pyproject.toml": PYPROJECT.replace("0.11.1", "0.11.2"),
                              "uv.lock": LOCK.replace("0.11.1", "0.11.2"),
                              policy.PACKAGE + "__init__.py": INIT.replace("0.11.1", "0.11.2")})
    result = decision(repository, head, base=repository.base)
    assert result["required"] is False
    assert len(result["changed_paths"]) == 3


@pytest.mark.parametrize("path,before,after", [
    ("pyproject.toml", "mcp==2.0.0", "mcp==2.1.0"),
    ("pyproject.toml", ">=3.12,<3.15", ">=3.13,<3.15"),
    ("pyproject.toml", "hatchling==1.31.0", "hatchling==1.32.0"),
    ("pyproject.toml", "telegram_search_mcp.cli:main", "telegram_search_mcp.cli:other"),
    ("uv.lock", 'version = "2.0.0"', 'version = "2.1.0"'),
    ("uv.lock", "revision = 3", "revision = 4"),
    (policy.PACKAGE + "__init__.py", INIT, INIT + "import os\n"),
])
def test_dependency_runtime_build_and_entrypoint_changes_are_not_version_only(repository, path, before, after):
    original = (repository.root / path).read_text()
    head = repository.commit({path: original.replace(before, after)})
    result = decision(repository, head, base=repository.base)
    assert result["required"] is True
    assert result["risky_paths"] == [path]


@pytest.mark.parametrize("path,source", [
    ("pyproject.toml", "not valid TOML = ["), ("uv.lock", "package = false\n"),
    ("uv.lock", LOCK + '[[package]]\nname="telegram-search-mcp"\nversion="0.11.2"\n'),
    (policy.PACKAGE + "__init__.py", '__version__ = get_version()\n'),
    (policy.PACKAGE + "server.py", "def broken("),
])
def test_malformed_semantic_inputs_require_desktops(repository, path, source):
    head = repository.commit({path: source})
    result = decision(repository, head, base=repository.base)
    assert result["required"] is True
    assert result["reason"] == "semantic_parse_failed"


def test_prompt_only_server_change_is_fast(repository):
    changed = SERVER.replace("Read history first.", "Read the selected messages.").replace(
        "Only use on a separate request.", "A reading request includes relevant transcription.")
    head = repository.commit({policy.PACKAGE + "server.py": changed + "\n# Clarification only.\n"})
    assert decision(repository, head, base=repository.base)["required"] is False


@pytest.mark.parametrize("before,after", [
    ('instructions=("Read history first. " "Ask for public-search consent.")', 'instructions=do_side_effect()'),
    ('instructions=("Read history first. " "Ask for public-search consent.")', 'instructions=f"Read {backend}"'),
    ('instructions=("Read history first. " "Ask for public-search consent.")', 'instructions="Read" + backend'),
    ("return mcp", "return None"),
    ("mcp = MCPServer", "mcp = AnotherServer"),
    ("    mcp = MCPServer", "    instructions = mutate()\n    mcp = MCPServer"),
])
def test_executable_server_changes_are_not_hidden_as_prompt_changes(repository, before, after):
    head = repository.commit({policy.PACKAGE + "server.py": SERVER.replace(before, after)})
    assert decision(repository, head, base=repository.base)["required"] is True


def test_literal_in_unrelated_instructions_assignment_is_not_masked(repository):
    base = repository.commit({policy.PACKAGE + "server.py": SERVER + '\ninstructions = "old"\n'})
    head = repository.commit({policy.PACKAGE + "server.py": SERVER + '\ninstructions = "new"\n'})
    assert decision(repository, head, base=base)["required"] is True


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", "scripts/release.py", "scripts/windows_assets.py",
    "src/telegram_search_mcp/windows_install.py", "src/telegram_search_mcp/wire.py",
    "src/telegram_search_mcp/platform_support.py", "src/telegram_search_mcp/native_runtime.py",
    "src/telegram_search_mcp/codex_config.py", "src/telegram_search_mcp/paths.py",
    "src/telegram_search_mcp/keychain.py", "src/telegram_search_mcp/service.py",
    "unexpected/new-file.txt",
])
def test_sensitive_and_unknown_paths_require_desktops(repository, path):
    head = repository.commit({path: "NEW = 3\n"})
    assert decision(repository, head, base=repository.base)["required"] is True


def test_reviewed_common_core_and_test_docs_changes_are_fast(repository):
    head = repository.commit({policy.PACKAGE + "speech.py": "VALUE = 2\n",
                              "tests/test_new.py": "def test_new(): assert True\n",
                              "docs/notes.md": "New documentation.\n", "README.md": None})
    assert decision(repository, head, base=repository.base)["required"] is False


@pytest.mark.parametrize("remove", [False, True])
def test_executable_addition_or_deletion_is_not_a_reviewed_modification(repository, remove):
    path = policy.PACKAGE + ("speech.py" if remove else "models.py")
    head = repository.commit({path: None if remove else "VALUE = 1\n"})
    assert decision(repository, head, base=repository.base)["required"] is True


def test_rename_out_of_sensitive_path_is_seen_as_delete_and_add(repository):
    source = policy.PACKAGE + "windows_install.py"
    target = "tests/old_installer.py"
    content = (repository.root / source).read_text()
    head = repository.commit({source: None, target: content})
    result = decision(repository, head, base=repository.base)
    assert result["required"] is True
    assert {source, target}.issubset(result["changed_paths"])


@pytest.mark.skipif(sys.platform == "win32", reason="Git tracks executable bits/symlinks natively on POSIX")
def test_mode_or_symlink_change_cannot_hide_under_document_path(repository):
    (repository.root / "README.md").unlink()
    (repository.root / "README.md").symlink_to("elsewhere")
    head = repository.commit({"docs/notes.md": "A harmless companion change.\n"})
    assert decision(repository, head, base=repository.base)["required"] is True


@pytest.mark.parametrize("output", [b"not-json", b"[]", b'{"workflow_runs": false}'])
def test_malformed_gh_api_responses_fail_closed(monkeypatch, output):
    monkeypatch.setattr(policy.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, output, b""))
    with pytest.raises(policy.PolicyError, match="baseline_api_malformed"):
        policy.github_runs(1)


def test_gh_api_uses_fixed_read_only_canonical_endpoint(monkeypatch):
    captured = []
    def response(command, **kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, b'{"workflow_runs": []}', b"")
    monkeypatch.setattr(policy.subprocess, "run", response)
    assert policy.github_runs(2) == {"workflow_runs": []}
    assert captured == [["gh", "api", "--hostname", "github.com", "--method", "GET",
                         "repos/prabchevski/telegram-mcp/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=100&page=2"]]


def test_cli_has_stable_json_and_cannot_inject_github_outputs(repository, monkeypatch, capsys, tmp_path):
    script = repository.root / "scripts/ci_windows_policy.py"
    monkeypatch.setattr(policy, "__file__", str(script))
    monkeypatch.setenv("GITHUB_SHA", repository.base)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert policy.main(["--base", repository.base]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1 and result["required"] is False
    assert output.read_text().splitlines() == ["required=false", "baseline=" + repository.base, "reason=no_changes"]

    assert policy.main(["--head=bad\nrequired=false"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["required"] is True
    assert output.read_text().splitlines()[-3:] == ["required=true", "baseline=", "reason=invalid_head"]


def test_cli_output_write_failure_cannot_report_a_successful_skip(repository, monkeypatch, capsys):
    monkeypatch.setattr(policy, "__file__", str(repository.root / "scripts/policy.py"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(repository.root / "missing/output"))
    assert policy.main(["--head", repository.base, "--base", repository.base]) == 1
    assert json.loads(capsys.readouterr().out)["required"] is True


def test_cli_cannot_classify_a_different_commit_than_checked_out_source(repository, monkeypatch, capsys):
    repository.commit({policy.PACKAGE + "windows_install.py": "VALUE = 2\n"})
    monkeypatch.setattr(policy, "__file__", str(repository.root / "scripts/policy.py"))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(policy, "github_runs", lambda *a: pytest.fail("Mismatched checkout must not query baseline"))
    assert policy.main(["--head", repository.base, "--base", repository.base]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["required"] is True
    assert result["reason"] == "head_checkout_mismatch"
