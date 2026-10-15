import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_upgrade_and_schema_match(migrated_db):
    for arguments in [("downgrade", "base"), ("upgrade", "head"), ("check",)]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
