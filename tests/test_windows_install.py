"""Registration edits must preserve other plugins and user customizations."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
from telegram_search_mcp import windows_install as installer
from telegram_search_mcp.config_io import atomic_write, read_source
from telegram_search_mcp.platform_support import assert_private_path, ensure_private_dir


@pytest.fixture
def windows_smoke():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'smoke-install-windows.py'
    spec = importlib.util.spec_from_file_location('windows_smoke', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_identity(build='19045', product_type=1):
    return {
        'ProductName': 'Windows 10 Pro', 'DisplayVersion': '22H2',
        'CurrentBuild': build, 'UBR': 1, 'Version': f'10.0.{build}',
        'Caption': 'Microsoft Windows', 'OSArchitecture': '64-bit',
        'ProductType': product_type, 'PowerShellVersion': '5.1',
    }


@pytest.mark.parametrize(('build', 'expected'), [('10240', '10'), ('19045', '10'), ('22000', '11'), ('26100', '11')])
def test_acceptance_uses_desktop_kernel_build_not_registry_product_name(windows_smoke, build, expected):
    # Windows 11 commonly retains "Windows 10" in the registry ProductName.
    assert windows_smoke.require_windows_version(windows_identity(build), expected) == ('desktop', expected)


@pytest.mark.parametrize('product_type', [2, 3])
@pytest.mark.parametrize('expected', ['10', '11'])
def test_acceptance_never_substitutes_windows_server_for_desktop(windows_smoke, product_type, expected):
    identity = windows_identity('26100', product_type)
    assert windows_smoke.require_windows_version(identity, None) == ('server', None)
    with pytest.raises(RuntimeError, match='actual OS is Windows Server'):
        windows_smoke.require_windows_version(identity, expected)


@pytest.mark.parametrize(('build', 'expected'), [('19045', '11'), ('26100', '10')])
def test_acceptance_rejects_the_other_desktop_version(windows_smoke, build, expected):
    with pytest.raises(RuntimeError, match='Expected desktop Windows'):
        windows_smoke.require_windows_version(windows_identity(build), expected)


@pytest.mark.parametrize('invalid_type', [None, True, '1', 0, 4])
def test_acceptance_rejects_missing_or_invalid_product_type(windows_smoke, invalid_type):
    with pytest.raises(RuntimeError, match='ProductType'):
        windows_smoke.require_windows_version(windows_identity(product_type=invalid_type), '10')


def test_acceptance_rejects_conflicting_os_evidence(windows_smoke):
    identity = windows_identity()
    identity['Version'] = '10.0.26100'
    with pytest.raises(RuntimeError, match='evidence disagree'):
        windows_smoke.require_windows_version(identity, '10')


def test_failed_desktop_acceptance_replaces_old_success_report_before_installation(
    windows_smoke, monkeypatch, tmp_path,
):
    powershell = tmp_path / 'powershell.exe'
    powershell.touch()
    report = tmp_path / 'acceptance.json'
    report.write_text(json.dumps({'status': 'passed'}), encoding='utf-8')
    missing_archive = tmp_path / 'must-not-be-read.zip'
    monkeypatch.setattr(sys, 'argv', ['smoke', str(missing_archive), '--expected-windows', '11',
                                    '--report-path', str(report)])
    monkeypatch.setattr(windows_smoke.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(windows_smoke.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(windows_smoke, 'system_powershell', lambda: powershell)
    monkeypatch.setattr(windows_smoke, 'read_windows_identity', lambda _: windows_identity('26100', 3))
    monkeypatch.setattr(windows_smoke, 'exercise_archive', lambda *_: pytest.fail('Server must not run desktop checks'))
    with pytest.raises(RuntimeError, match='actual OS is Windows Server'):
        windows_smoke.main()
    evidence = json.loads(report.read_text(encoding='utf-8'))
    assert evidence['status'] == 'failed'
    assert evidence['windows']['ProductType'] == 3
    assert evidence['windows_family'] == 'server'
    assert evidence['detected_desktop_windows_version'] is None
    assert evidence['expected_windows_version'] == '11'
    assert evidence['checks'] == []
    assert evidence['client_ui_tested'] is False
    assert evidence['telegram_authorization_performed'] is False
    assert evidence['error']['type'] == 'RuntimeError'


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows DLL mapping lifetime')
def test_smoke_powershell_lookup_does_not_pin_pywin32_dll_in_verifier():
    # A mapped cached pywin32 DLL prevents deleting uv's hardlinks in disposable
    # installations. Use a fresh process: other pytest tests may map it normally.
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'smoke-install-windows.py'
    check = '''
import ctypes
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('windows_smoke', sys.argv[1])
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
dll = f'pywintypes{sys.version_info.major}{sys.version_info.minor}.dll'
assert not kernel32.GetModuleHandleW(dll), 'Test process must initially have no pywin32 DLL mapping'
assert smoke.system_powershell().is_file()
assert 'win32api' not in sys.modules and 'pywintypes' not in sys.modules
assert not kernel32.GetModuleHandleW(dll), 'Verifier must not pin the DLL while cleaning hardlinked environments'
'''
    subprocess.run([sys.executable, '-I', '-c', check, str(script)],
                   check=True, capture_output=True, timeout=60)


def test_new_marketplace_and_existing_settings_are_preserved():
    created = json.loads(installer.merge_marketplace(None))
    assert created['name'] == 'personal'
    entry = installer.marketplace_entry()
    assert created['plugins'] == [entry]
    before = json.dumps({'name': 'my_plugins', 'interface': {'displayName': 'Keep me'},
                         'plugins': [{'name': 'unrelated', 'source': './plugins/other'}]}).encode()
    after = json.loads(installer.merge_marketplace(before))
    assert after['interface'] == {'displayName': 'Keep me'}
    assert after['plugins'][0] == json.loads(before)['plugins'][0]
    assert after['plugins'][1] == entry


def test_unchanged_owned_entry_retains_exact_marketplace_bytes():
    before = installer.merge_marketplace(None)
    assert installer.merge_marketplace(before, owned_entry=installer.marketplace_entry()) is before


@pytest.mark.parametrize('change', ['unknown_owner', 'edited', 'duplicate'])
def test_never_overwrite_colliding_or_edited_registration(change):
    entry = installer.marketplace_entry()
    data = {'name': 'personal', 'plugins': [entry]}
    owned = installer.marketplace_entry()
    if change == 'unknown_owner':
        owned = None
    elif change == 'edited':
        data['plugins'][0]['source']['path'] = './plugins/custom'
    else:
        data['plugins'].append(entry)
    with pytest.raises(RuntimeError, match='not overwritten'):
        installer.merge_marketplace(json.dumps(data).encode(), owned_entry=owned)


@pytest.mark.parametrize('value', [[], {}, {'name': '../bad', 'plugins': []}, {'name': 'ok', 'plugins': {}}])
def test_invalid_marketplace_rejected(value):
    with pytest.raises(RuntimeError, match='Invalid'):
        installer.merge_marketplace(json.dumps(value).encode())


def test_launcher_does_not_interpolate_install_path_as_powershell_code():
    dangerous = Path("a'; Start-Process bad; #")
    text = installer.windows_launcher_text(dangerous, 'telegram_search_mcp.server')
    assert str(dangerous) not in text
    assert '$PSScriptRoot' in text
    assert '-I -X utf8 -m telegram_search_mcp.server @args' in text
    assert 'ReparsePoint' in text
    with pytest.raises(ValueError):
        installer.windows_launcher_text(dangerous, 'bad;code')


def test_update_launcher_uses_isolated_current_environment():
    text = installer.windows_launcher_text(Path("owner path"), 'telegram_search_mcp.windows_updates')
    assert '-I -X utf8 -m telegram_search_mcp.windows_updates @args' in text
    assert '$PSScriptRoot' in text
    assert 'current.json' in text


@pytest.fixture
def isolated_installer(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nname="telegram-search-mcp"\nversion="9.8.7"\n', encoding='utf-8')
    (source / 'SOURCE_REVISION').write_text('a' * 40 + '\n', encoding='utf-8')
    root = tmp_path / 'installed'
    marketplace = tmp_path / 'owner/.agents/plugins/marketplace.json'
    ensure_private_dir(marketplace.parent)
    plugin = tmp_path / 'owner/plugins/telegram-mcp-work/.mcp.json'
    uv = tmp_path / 'bootstrap/uv.exe'
    calls, scheduled = [], []
    monkeypatch.setattr(installer, 'sys', SimpleNamespace(platform='win32'))
    import platform
    monkeypatch.setattr(platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(installer, '_plugin_files', lambda *_: {plugin: b'{"generated":"plugin"}\n'})
    monkeypatch.setattr(installer, '_copy_source', lambda *_: None)
    monkeypatch.setattr(installer, '_stable_uv', lambda *_: {
        'path': str(root / 'tools/uv-0.11.15-abcdef012345/uv.exe'), 'sha256': 'b' * 64, 'version': '0.11.15'})
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(installer.subprocess, 'run', run)
    def configure(path, enabled):
        assert read_source(path / 'current.json') is not None, 'Activate installed dependencies before COM'
        scheduled.append(enabled)
        return {'enabled': enabled}
    monkeypatch.setattr(installer, '_configure_schedule', configure)
    return source, root, marketplace, uv, plugin, calls, scheduled


def perform_install(fixture, **kwargs):
    source, root, marketplace, uv, *_ = fixture
    return installer.install(source, root, marketplace, uv, **kwargs)


def edit_receipt(root, change):
    path = root / installer.RECEIPT
    before = read_source(path)
    receipt = json.loads(before)
    change(receipt)
    atomic_write(path, json.dumps(receipt).encode(), expected=before)


def test_install_records_source_identity_stable_uv_and_default_updates(isolated_installer):
    result = perform_install(isolated_installer)
    _, root, _, uv, _, calls, scheduled = isolated_installer
    receipt = installer.read_receipt(root)
    assert result['version'] == receipt['version'] == '9.8.7'
    assert receipt['revision'] == 'a' * 40
    assert receipt['auto_update'] is True and receipt['updates_configured'] is True
    assert json.loads((root / 'current.json').read_bytes())['version'].startswith('version-9.8.7-')
    assert calls[0][0] == receipt['uv']['path'] and calls[0][0] != str(uv)
    assert scheduled == [True]
    assert (root / 'update.ps1').read_bytes() == installer.windows_launcher_text(root, 'telegram_search_mcp.windows_updates').encode()
    assert all('--no-config' in call for call in calls if 'sync' in call)


@pytest.mark.parametrize('preference,expected', [('keep', False), ('on', True), ('off', False)])
def test_explicit_opt_out_survives_update_and_can_be_changed(isolated_installer, preference, expected):
    perform_install(isolated_installer, auto_update='off')
    _, root, *_ = isolated_installer
    result = perform_install(isolated_installer, auto_update=preference, revision='c' * 40)
    assert result['auto_update'] is expected
    assert installer.read_receipt(root)['revision'] == 'c' * 40


def test_legacy_false_receipt_gets_default_updates_without_losing_unknown_preferences(isolated_installer):
    perform_install(isolated_installer)
    _, root, *_ = isolated_installer
    def legacy(receipt):
        receipt.pop('updates_configured')
        receipt.pop('auto_update')
        receipt['automatic_updates'] = False
        receipt['owner_extra'] = {'parallel': True}
    edit_receipt(root, legacy)
    perform_install(isolated_installer)
    receipt = installer.read_receipt(root)
    assert receipt['auto_update'] is True
    assert receipt['owner_extra'] == {'parallel': True}


@pytest.mark.parametrize('change', ['removed_entry', 'edited_entry', 'duplicate_entry', 'deleted_plugin', 'edited_plugin'])
def test_update_never_restores_removed_edited_or_duplicated_plugin(isolated_installer, change):
    perform_install(isolated_installer)
    _, root, marketplace, _, plugin, *_ = isolated_installer
    sending = root / 'sending.json'
    atomic_write(sending, b'{"enabled":true}\n', expected=None)
    if change == 'deleted_plugin':
        plugin.unlink()
    elif change == 'edited_plugin':
        atomic_write(plugin, b'owner config\n', expected=read_source(plugin))
    else:
        before = read_source(marketplace)
        data = json.loads(before)
        if change == 'removed_entry':
            data['plugins'] = []
        elif change == 'edited_entry':
            data['plugins'][0]['policy']['installation'] = 'DISABLED'
        else:
            data['plugins'].append(data['plugins'][0])
        atomic_write(marketplace, json.dumps(data).encode(), expected=before)
    market_before, plugin_before = read_source(marketplace), read_source(plugin)
    previous = installer.read_receipt(root)
    result = perform_install(isolated_installer)
    assert result['plugin_registration'] == 'preserved_user_changes'
    assert read_source(marketplace) == market_before and read_source(plugin) == plugin_before
    assert read_source(sending) == b'{"enabled":true}\n'
    assert installer.read_receipt(root)['plugin_files'] == previous['plugin_files']


def test_update_receipt_compare_and_swap_fails_before_dependency_work(isolated_installer):
    perform_install(isolated_installer)
    _, root, _, _, _, calls, _ = isolated_installer
    before = read_source(root / installer.RECEIPT)
    edit_receipt(root, lambda receipt: receipt.update(auto_update=False))
    original_calls = len(calls)
    with pytest.raises(RuntimeError, match='receipt changed'):
        perform_install(isolated_installer, expected_receipt=before)
    assert len(calls) == original_calls


def test_plugin_snapshot_blocks_concurrent_owner_edit_during_staging(isolated_installer, monkeypatch):
    perform_install(isolated_installer)
    _, root, marketplace, _, plugin, _, _ = isolated_installer
    receipt_before = read_source(root / installer.RECEIPT)
    pointer_before = read_source(root / 'current.json')
    snapshot = installer.registration_snapshot(installer.read_receipt(root), marketplace)
    original = installer.subprocess.run
    def edit_during_sync(command, **kwargs):
        if 'sync' in command:
            atomic_write(plugin, b'owner concurrent edit\n', expected=read_source(plugin))
        return original(command, **kwargs)
    monkeypatch.setattr(installer.subprocess, 'run', edit_during_sync)
    with pytest.raises(RuntimeError, match='registration changed'):
        perform_install(isolated_installer, expected_receipt=receipt_before, registration_snapshot=snapshot)
    assert read_source(root / 'current.json') == pointer_before
    assert read_source(root / installer.RECEIPT) == receipt_before
    assert read_source(plugin) == b'owner concurrent edit\n'


def test_install_snapshot_is_captured_before_generating_plugin_assets(isolated_installer, monkeypatch):
    perform_install(isolated_installer)
    _, root, _, _, plugin, *_ = isolated_installer
    pointer = read_source(root / 'current.json')
    original = installer._plugin_files
    def edit_during_assets(*args):
        result = original(*args)
        result[plugin] = b'new generated plugin version\n'
        atomic_write(plugin, b'owner edited while assets were generated\n', expected=read_source(plugin))
        return result
    monkeypatch.setattr(installer, '_plugin_files', edit_during_assets)
    with pytest.raises(RuntimeError, match='Configuration changed'):
        perform_install(isolated_installer)
    assert read_source(plugin) == b'owner edited while assets were generated\n'
    assert read_source(root / 'current.json') == pointer


def test_failed_schedule_rolls_back_activation_receipt_and_registration(isolated_installer, monkeypatch):
    perform_install(isolated_installer, auto_update='off')
    _, root, marketplace, _, plugin, *_ = isolated_installer
    paths = [root / 'current.json', root / installer.RECEIPT, marketplace, plugin]
    before = {path: read_source(path) for path in paths}
    def fail(*_):
        raise RuntimeError('owner edited schedule preserved')
    monkeypatch.setattr(installer, '_configure_schedule', fail)
    with pytest.raises(RuntimeError, match='schedule preserved'):
        perform_install(isolated_installer, auto_update='on', revision='c' * 40)
    assert {path: read_source(path) for path in paths} == before


def test_failed_schedule_rollback_keeps_owner_concurrent_plugin_edit(isolated_installer, monkeypatch):
    perform_install(isolated_installer)
    *_, plugin, _, _ = isolated_installer
    def edit_and_fail(*_):
        atomic_write(plugin, b'owner latest edit\n', expected=read_source(plugin))
        raise RuntimeError('synthetic schedule failure')
    monkeypatch.setattr(installer, '_configure_schedule', edit_and_fail)
    with pytest.raises(RuntimeError, match='schedule failure'):
        perform_install(isolated_installer)
    assert read_source(plugin) == b'owner latest edit\n'


def test_explicit_off_remains_disabled_when_schedule_removal_fails(isolated_installer, monkeypatch):
    perform_install(isolated_installer)
    _, root, marketplace, _, plugin, *_ = isolated_installer
    pointer = read_source(root / 'current.json')
    previous = installer.read_receipt(root)
    registration = {path: read_source(path) for path in (marketplace, plugin)}
    def fail_removal(_root, enabled):
        assert enabled is False
        raise RuntimeError('Owner edited task; it was preserved')
    monkeypatch.setattr(installer, '_configure_schedule', fail_removal)
    with pytest.raises(RuntimeError, match='Owner edited task'):
        perform_install(isolated_installer, auto_update='off', revision='c' * 40)
    receipt = installer.read_receipt(root)
    assert receipt['auto_update'] is False and receipt['automatic_updates'] is False
    assert receipt['updates_configured'] is True
    assert receipt['revision'] == previous['revision']
    assert read_source(root / 'current.json') == pointer
    assert {path: read_source(path) for path in registration} == registration


def test_explicit_off_schedule_failure_preserves_concurrent_owner_receipt_changes(isolated_installer, monkeypatch):
    perform_install(isolated_installer)
    _, root, *_ = isolated_installer
    owner_receipt = []
    def edit_and_fail(_root, enabled):
        assert enabled is False
        edit_receipt(root, lambda receipt: receipt.update(owner_extra='preserve exact bytes'))
        owner_receipt.append(read_source(root / installer.RECEIPT))
        raise RuntimeError('Owner edited task; it was preserved')
    monkeypatch.setattr(installer, '_configure_schedule', edit_and_fail)
    with pytest.raises(RuntimeError, match='Owner edited task'):
        perform_install(isolated_installer, auto_update='off')
    assert read_source(root / installer.RECEIPT) == owner_receipt[0]
    assert installer.read_receipt(root)['auto_update'] is False
    assert installer.read_receipt(root)['owner_extra'] == 'preserve exact bytes'


def test_stable_uv_is_verified_private_and_survives_bootstrap_deletion(tmp_path, monkeypatch):
    import hashlib
    root, bootstrap = tmp_path / 'installed', tmp_path / 'bootstrap'
    ensure_private_dir(root)
    ensure_private_dir(bootstrap)
    uv = bootstrap / 'uv.exe'
    payload = b'reviewed synthetic Windows uv binary'
    atomic_write(uv, payload, expected=None)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(installer, 'UV_SHA256', digest)
    receipt = installer._stable_uv(root, uv)
    target = Path(receipt['path'])
    assert target.is_relative_to(root / 'tools') and target.read_bytes() == payload
    assert_private_path(target)
    uv.unlink()
    bootstrap.rmdir()
    assert target.read_bytes() == payload
    assert installer._stable_uv(root, target) == receipt
    atomic_write(target, b'owner edit', expected=read_source(target))
    source = tmp_path / 'another-uv.exe'
    atomic_write(source, payload, expected=None)
    with pytest.raises(RuntimeError, match='was edited'):
        installer._stable_uv(root, source)
    assert target.read_bytes() == b'owner edit'


def test_unverified_uv_is_not_copied(tmp_path):
    uv = tmp_path / 'unverified.exe'
    uv.write_bytes(b'unverified executable')
    root = tmp_path / 'install'
    ensure_private_dir(root)
    with pytest.raises(RuntimeError, match='pinned verified build'):
        installer._stable_uv(root, uv)
    assert not (root / 'tools').exists()


def make_update_state(fixture):
    import base64
    _, root, marketplace, *_ = fixture
    receipt = read_source(root / installer.RECEIPT)
    snapshot = installer.registration_snapshot(installer.read_receipt(root), marketplace)
    snapshot.update({str(root / name): read_source(root / name)
                     for name in ('current.json', 'launch-mcp.ps1', 'tgsearch.ps1', 'update.ps1')})
    state = {'schema': 1, 'receipt': base64.b64encode(receipt).decode('ascii'),
             'registration': {name: base64.b64encode(content).decode('ascii') if content is not None else None
                              for name, content in snapshot.items()}}
    path = root / ('update-state-' + '1' * 32 + '.json')
    atomic_write(path, json.dumps(state).encode(), expected=None)
    return path, receipt, snapshot


def test_candidate_update_state_contains_only_private_bounded_compare_and_swap_data(isolated_installer):
    perform_install(isolated_installer)
    _, root, marketplace, *_ = isolated_installer
    state, expected_receipt, snapshot = make_update_state(isolated_installer)
    assert_private_path(state)
    assert installer._update_state(state, root, marketplace) == (expected_receipt, snapshot)


@pytest.mark.parametrize('change', ['extra_command', 'other_path', 'missing_current', 'bad_base64', 'boolean_schema', 'other_marketplace'])
def test_candidate_state_cannot_redirect_or_omit_destination_guards(isolated_installer, change):
    perform_install(isolated_installer)
    _, root, marketplace, *_ = isolated_installer
    path, *_ = make_update_state(isolated_installer)
    before = read_source(path)
    state = json.loads(before)
    selected_marketplace = marketplace
    if change == 'extra_command':
        state['command'] = ['arbitrary executable']
    elif change == 'other_path':
        state['registration'][str(root / 'not-owned.json')] = None
    elif change == 'missing_current':
        state['registration'].pop(str(root / 'current.json'))
    elif change == 'bad_base64':
        state['receipt'] = '***'
    elif change == 'boolean_schema':
        state['schema'] = True
    else:
        selected_marketplace = marketplace.parent / 'another-marketplace.json'
    atomic_write(path, json.dumps(state).encode(), expected=before)
    with pytest.raises(RuntimeError):
        installer._update_state(path, root, selected_marketplace)


def test_candidate_state_rejects_changed_receipt_and_oversize_payload(isolated_installer):
    perform_install(isolated_installer)
    _, root, marketplace, *_ = isolated_installer
    path, *_ = make_update_state(isolated_installer)
    edit_receipt(root, lambda receipt: receipt.update(owner_change=True))
    with pytest.raises(RuntimeError, match='receipt changed'):
        installer._update_state(path, root, marketplace)
    atomic_write(path, b'x' * (256 * 1024 + 1), expected=read_source(path))
    with pytest.raises(RuntimeError, match='size limit'):
        installer._update_state(path, root, marketplace)


def test_plugin_template_is_fail_closed_until_configured():
    source = Path(__file__).resolve().parents[1]
    release = installer._release_module(source)
    assets = release.windows_assets(release.inventory(source))
    manifest = json.loads(assets['plugins/telegram-mcp-work/.codex-plugin/plugin.json'])
    config = json.loads(assets['plugins/telegram-mcp-work/.mcp.json'])
    assert manifest['name'] == installer.PLUGIN_NAME
    assert manifest['mcpServers'] == './.mcp.json'
    assert 'exit 1' in config['mcpServers']['telegram']['args'][-1]


@pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows PowerShell pipe behavior')
@pytest.mark.parametrize('hidden_console', [False, True])
def test_native_launcher_preserves_unicode_json_stdio(tmp_path, hidden_console):
    """A disposable fake MCP server exercises the real launcher without a profile."""
    root = tmp_path / 'Телеграм install'
    version = root / 'version-0.9.0-abcdef012345'
    version.mkdir(parents=True)
    (version / installer.VERSION_MARKER).write_bytes(b'telegram-search-mcp\n')
    (root / 'current.json').write_text(json.dumps({'version': version.name}), encoding='utf-8')
    environment = version / '.venv'
    subprocess.run([sys.executable, '-I', '-m', 'venv', '--without-pip', str(environment)],
                   check=True, capture_output=True, timeout=60)
    package = environment / 'Lib/site-packages/telegram_search_mcp'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'server.py').write_text('''import json
import sys
assert sys.flags.isolated and sys.flags.utf8_mode
# MCP uses UTF-8 text wrappers around these binary pipes.
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    if request['method'] == 'initialize':
        result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'Тест 🌍', 'version': '1'}}
    elif request['method'] == 'tools/call':
        result = {'content': [{'type': 'text', 'text': request['params']['arguments']['text']}]}
    else:
        raise AssertionError(request)
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result},
                     ensure_ascii=False), flush=True)
''', encoding='utf-8')
    launcher = root / 'launch-mcp.ps1'
    launcher.write_text(installer.windows_launcher_text(root, 'telegram_search_mcp.server'),
                        encoding='utf-8', newline='\n')
    unicode_text = 'Привет, мир — café 🌍 中文'
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
         'params': {'name': 'echo', 'arguments': {'text': unicode_text}}},
    ]
    wire = ''.join(json.dumps(request, ensure_ascii=False) + '\n' for request in requests).encode('utf-8')
    # Reproduce an app started from another PowerShell version: its inherited
    # module path does not contain this runtime's compatible built-in modules.
    external_modules = tmp_path / 'external modules'
    external_modules.mkdir()
    child_environment = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
    child_environment['PSModulePath'] = str(external_modules)
    result = subprocess.run(
        [str(installer.powershell_path()), '-NoLogo', '-NoProfile', '-NonInteractive',
         '-ExecutionPolicy', 'Bypass', '-File', str(launcher)],
        input=wire, capture_output=True, timeout=30, check=True, env=child_environment,
        creationflags=subprocess.CREATE_NO_WINDOW if hidden_console else 0,
    )
    responses = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines()]
    assert len(responses) == 2, 'Launcher emitted extra stdout or lost a protocol response'
    assert responses[0]['result']['serverInfo']['name'] == 'Тест 🌍'
    assert responses[1]['result']['content'][0]['text'] == unicode_text
    assert result.stderr == b''
