"""GORA_GIT names the git the Git adapter runs: an absolute path, /usr/bin/git unless set.

The release image installs git there (deployments/docker/Dockerfile, ADR-006); a name that is
not absolute would be looked up on PATH, which the adapter never does.
"""

import pytest
from pydantic import ValidationError

from gora.config.settings import Settings


def test_the_default_is_the_path_the_release_image_installs_git_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GORA_GIT", raising=False)
    assert Settings().git == "/usr/bin/git"


def test_an_absolute_path_is_taken_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GORA_GIT", "/opt/git/bin/git")
    assert Settings().git == "/opt/git/bin/git"


@pytest.mark.parametrize("name", ["git", "bin/git", ""])
def test_a_name_that_is_not_an_absolute_path_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GORA_GIT", name)
    with pytest.raises(ValidationError, match="GORA_GIT"):
        Settings()
