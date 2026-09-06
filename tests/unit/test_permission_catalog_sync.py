"""Drift guard: the generated permission-catalog artifacts
(back/policies/data/permissions.json, front/src/permissions.generated.js)
must always match what `scripts/gen_permissions.py` would produce from the
canonical `app/core/permissions_catalog.json`. Fails with a diff, not just a
boolean, so drift is diagnosable from CI output alone.
"""

import subprocess
import sys
from pathlib import Path

BACK_DIR = Path(__file__).resolve().parent.parent.parent


def test_generated_permission_files_are_up_to_date():
    result = subprocess.run(
        [sys.executable, "scripts/gen_permissions.py", "--check"],
        cwd=BACK_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Generated permission-catalog files are stale -- run "
        "`python scripts/gen_permissions.py` and commit the result.\n"
        f"{result.stdout}\n{result.stderr}"
    )
