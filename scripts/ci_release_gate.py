"""Require successful release checks and an explicitly allowed desktop decision."""
from __future__ import annotations

import json
import os
import re


MANDATORY_JOBS = ('desktop-policy', 'tests', 'release-artifacts', 'windows-install')


class ReleaseGateError(ValueError):
    """The workflow has not established that publication is allowed."""


def release_ready(needs: object) -> dict:
    if not isinstance(needs, dict):
        raise ReleaseGateError('Missing prerequisite results')
    for job in MANDATORY_JOBS:
        value = needs.get(job)
        if not isinstance(value, dict) or value.get('result') != 'success':
            raise ReleaseGateError(f'Required job did not succeed: {job}')
    policy = needs['desktop-policy'].get('outputs')
    if not isinstance(policy, dict) or policy.get('required') not in ('true', 'false'):
        raise ReleaseGateError('Missing or invalid Windows desktop decision')
    required = policy['required'] == 'true'
    if not required and not re.fullmatch(r'[0-9a-f]{40}', str(policy.get('baseline', ''))):
        raise ReleaseGateError('A desktop skip requires a validated baseline')
    desktop = needs.get('windows-desktop')
    result = desktop.get('result') if isinstance(desktop, dict) else None
    allowed = ('success',) if required else ('success', 'skipped')
    if result not in allowed:
        raise ReleaseGateError('Windows desktop checks did not meet the policy decision')
    return {'ready': True, 'desktop_required': required, 'desktop_result': result}


def main() -> int:
    try:
        result = release_ready(json.loads(os.environ.get('NEEDS_JSON', '')))
    except (ValueError, TypeError) as error:
        message = str(error) if isinstance(error, ReleaseGateError) else 'Invalid prerequisite JSON'
        print(json.dumps({'ready': False, 'reason': message}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
