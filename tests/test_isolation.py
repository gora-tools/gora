"""Tests never touch the real home (ADR-0016 rule 7, TESTING rule 6).

GORA writes an audit journal, so a test that ran a mutating command without isolation would
write one to the developer's real ``~/.local/state``. The conftest fixture points HOME, the
XDG directories and GORA_AUDIT_LOG under the test's tmp_path, in this process and, since it
sets ``os.environ``, in every child.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import gora

NAMES = ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "GORA_AUDIT_LOG")
SRC = Path(gora.__file__).parent.parent


def under(path: str, root: Path) -> bool:
    return Path(path).is_relative_to(root)


def test_the_environment_points_under_tmp_path(tmp_path: Path) -> None:
    for name in NAMES:
        assert under(os.environ[name], tmp_path), name
    assert under(str(Path.home()), tmp_path)
    assert under(os.path.expanduser("~"), tmp_path)


def test_a_child_process_sees_the_same_isolation(tmp_path: Path) -> None:
    code = f"import os, json; print(json.dumps({{n: os.environ.get(n) for n in {NAMES!r}}}))"

    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert json.loads(run.stdout) == {name: os.environ[name] for name in NAMES}


def test_a_child_process_that_writes_the_journal_writes_it_under_tmp_path(tmp_path: Path) -> None:
    child = (
        "import os\n"
        "from gora.audit.logger import AuditLogger\n"
        "from gora.audit.sink import attach, resolve_journal\n"
        "attached = attach(resolve_journal(os.environ))\n"
        "AuditLogger(actor='test').record('isolation.check')\n"
        "attached.detach()\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}

    subprocess.run([sys.executable, "-c", child], env=env, check=True, capture_output=True)

    journal = Path(os.environ["GORA_AUDIT_LOG"])
    assert [json.loads(line)["action"] for line in journal.read_text().splitlines()] == [
        "isolation.check"
    ]
