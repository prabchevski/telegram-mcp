from contextlib import nullcontext
import fcntl
import json
import os
from pathlib import Path

import pytest

from telegram_search_mcp import profile_binding as binding
from telegram_search_mcp import paths, service


@pytest.fixture
def profile_area(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    monkeypatch.setattr(binding, "shared_root", lambda: base / "TelegramSearchMCPShared")
    monkeypatch.setattr(service, "profile_exclusive", lambda: nullcontext())
    return base


def legacy(base, source, user=123):
    root = base / binding.LEGACY[source][0]
    profile = root / "profiles/default"
    for directory in (root, root / "profiles", profile, profile / "db"):
        directory.mkdir(mode=0o700)
    (profile / "policy.json").write_text(json.dumps({"version": 2, "search_scope": "global_cloud_chats", "api_id": 321, "expected_user_id": user}))
    (profile / "policy.json").chmod(0o600)
    (profile / "db/session.data").write_bytes(b"synthetic session: must remain at its original path")
    return profile


def test_no_legacy_profile_keeps_fresh_setup(profile_area):
    assert binding.plan_adoption() is None
    assert binding.credential_services() == (binding.SHARED_SERVICE,)


@pytest.mark.parametrize("source", ["codex", "gemini"])
def test_adoption_reuses_data_and_matching_keychain_without_copying(profile_area, source):
    profile = legacy(profile_area, source)
    before = (profile / "db/session.data").read_bytes()
    assert binding.plan_adoption() == source
    binding.adopt(source)
    assert paths.profile_root() == profile
    assert binding.credential_services() == binding.LEGACY[source][1]
    assert (profile / "db/session.data").read_bytes() == before
    assert not (binding.shared_root() / "profiles").exists()
    assert (binding.shared_root() / "profile-source.json").stat().st_mode & 0o777 == 0o600
    assert binding.plan_adoption() is None


def test_two_different_accounts_need_a_selection(profile_area):
    legacy(profile_area, "codex", 123)
    legacy(profile_area, "gemini", 456)
    with pytest.raises(RuntimeError, match="Choose"):
        binding.plan_adoption()
    assert binding.plan_adoption("gemini") == "gemini"
    assert not binding.shared_root().exists()


def test_two_sessions_of_same_account_have_a_deterministic_default(profile_area):
    legacy(profile_area, "codex", 123)
    legacy(profile_area, "gemini", 123)
    assert binding.plan_adoption() == "codex"


def test_existing_shared_profile_is_never_replaced(profile_area):
    legacy(profile_area, "codex")
    target = binding.shared_root() / "profiles/default"
    target.mkdir(parents=True)
    (target / "policy.json").write_text("existing owner setup")
    assert binding.plan_adoption() is None
    with pytest.raises(RuntimeError, match="already exists"):
        binding.plan_adoption("codex")


def test_busy_legacy_database_is_not_migrated(profile_area):
    profile = legacy(profile_area, "codex")
    descriptor = os.open(profile / "tdlib.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="in use"):
            binding.adopt("codex")
        assert binding.read_binding() is None
    finally:
        os.close(descriptor)


def test_symlinked_or_public_legacy_policy_is_refused(profile_area):
    profile = legacy(profile_area, "codex")
    policy = profile / "policy.json"
    policy.chmod(0o644)
    with pytest.raises(RuntimeError, match="private"):
        binding.plan_adoption()
    policy.chmod(0o600)
    original = profile / "original.json"
    policy.rename(original)
    policy.symlink_to(original)
    with pytest.raises(RuntimeError, match="symlink"):
        binding.plan_adoption()


def test_binding_cannot_select_arbitrary_files_or_another_account(profile_area):
    binding.shared_root().mkdir(mode=0o700)
    path = binding.shared_root() / "profile-source.json"
    path.write_text(json.dumps({"schema": 1, "source": "../../other-person"}))
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="Unsupported"):
        binding.read_binding()
