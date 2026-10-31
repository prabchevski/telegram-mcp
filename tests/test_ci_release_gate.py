"""Publication cannot bypass failed checks or an unvalidated desktop skip."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ci_release_gate', ROOT / 'scripts/ci_release_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def checks(required='false', desktop='skipped'):
    needs = {name: {'result': 'success'} for name in gate.MANDATORY_JOBS}
    needs['desktop-policy']['outputs'] = {'required': required, 'baseline': 'a' * 40}
    needs['windows-desktop'] = {'result': desktop}
    return needs


@pytest.mark.parametrize('required,desktop', [('false', 'skipped'), ('false', 'success'), ('true', 'success')])
def test_valid_desktop_decisions_allow_publication(required, desktop):
    result = gate.release_ready(checks(required, desktop))
    assert result == {'ready': True, 'desktop_required': required == 'true', 'desktop_result': desktop}


@pytest.mark.parametrize('job', gate.MANDATORY_JOBS)
@pytest.mark.parametrize('result', ['failure', 'cancelled', 'skipped', 'in_progress', '', None])
def test_every_mandatory_job_must_succeed(job, result):
    needs = checks()
    needs[job]['result'] = result
    with pytest.raises(gate.ReleaseGateError, match='Required job'):
        gate.release_ready(needs)


@pytest.mark.parametrize('required', ['true', 'false'])
@pytest.mark.parametrize('desktop', ['failure', 'cancelled', 'in_progress', '', None])
def test_desktop_failure_or_cancellation_never_becomes_a_skip(required, desktop):
    with pytest.raises(gate.ReleaseGateError, match='Windows desktop checks'):
        gate.release_ready(checks(required, desktop))


def test_required_desktops_cannot_be_skipped():
    with pytest.raises(gate.ReleaseGateError, match='Windows desktop checks'):
        gate.release_ready(checks('true', 'skipped'))


@pytest.mark.parametrize('decision', ['', 'False', '0', None, False, True])
def test_missing_or_ambiguous_decisions_fail_closed(decision):
    with pytest.raises(gate.ReleaseGateError, match='decision'):
        gate.release_ready(checks(decision))


@pytest.mark.parametrize('baseline', ['', None, 'main', 'a' * 39, 'b' * 41])
def test_fast_path_requires_a_validated_baseline(baseline):
    needs = checks()
    needs['desktop-policy']['outputs']['baseline'] = baseline
    with pytest.raises(gate.ReleaseGateError, match='baseline'):
        gate.release_ready(needs)


@pytest.mark.parametrize('missing', [*gate.MANDATORY_JOBS, 'windows-desktop'])
def test_missing_prerequisites_block_publication(missing):
    needs = checks()
    del needs[missing]
    with pytest.raises(gate.ReleaseGateError):
        gate.release_ready(needs)


def test_cli_fails_on_malformed_results_without_echoing_input(monkeypatch, capsys):
    monkeypatch.setenv('NEEDS_JSON', 'invalid private input')
    assert gate.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {'ready': False, 'reason': 'Invalid prerequisite JSON'}


def test_cli_reports_an_allowed_skip(monkeypatch, capsys):
    monkeypatch.setenv('NEEDS_JSON', json.dumps(checks()))
    assert gate.main() == 0
    assert json.loads(capsys.readouterr().out)['desktop_result'] == 'skipped'
