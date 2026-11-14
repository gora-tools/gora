"""The exceptions file of the image scan (ADR-011, amendment 2026-11-07).

``scripts/gate/scan-exceptions.yaml`` is Grype's own configuration, passed to the scanner as it
is. Grype ignores a key it does not know, so a misspelt ``expire:`` would be an inert line and an
entry without ``vulnerability`` would ignore every advisory of its package: this test holds each
entry to exactly ``vulnerability``, ``package`` with exactly ``name``, ``reason`` and
``expires``, all four required and no other key at any level. It also holds the dates: an entry
is not expired, and does not expire more than 90 days after the day the test runs (UTC).

The date logic is a pure function, ``exception_problems``, tested on fixed dates; only the test
of the real file reads today's date. The host-side refusal of an expired entry, before the
scanner runs, is ``scripts/gate/image-scan.sh``'s and is tested in ``test_image_scan.py``.
"""

import re
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS = ROOT / "scripts" / "gate" / "scan-exceptions.yaml"

MAX_DAYS = 90
ENTRY_KEYS = {"vulnerability", "package", "reason", "expires"}
# The advisory ids Grype's database uses: GitHub's, the CVE's.
ADVISORY = re.compile(r"^(?:GHSA(?:-[0-9a-z]{4}){3}|CVE-[0-9]{4}-[0-9]{4,})$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# The lines image-scan.sh reads the dates from (awk, by text): a list item, and these two keys.
EXPIRES_LINE = re.compile(r"^ {4}expires: [0-9]{4}-[0-9]{2}-[0-9]{2}$")
VULNERABILITY_LINE = re.compile(r"^ {2}- vulnerability: \S+$")
# A line that starts either key, in any spelling of the list item; a comment or a sentence of a
# `reason` that merely holds the word does not.
KEY_LINE = re.compile(r"^\s*(?:-\s*\{?\s*)?(?P<key>expires|vulnerability)\b")


def exception_problems(document: Any, today: date) -> list[str]:
    """Why the document is not a valid exceptions file on ``today``; empty when it is."""
    if not isinstance(document, dict) or set(document) != {"ignore"}:
        return ["the file is not a mapping whose only key is `ignore`"]
    entries = document["ignore"]
    if not isinstance(entries, list):
        return ["`ignore` is not a list"]
    problems: list[str] = []
    for number, entry in enumerate(entries, start=1):
        name = f"entry {number}"
        if not isinstance(entry, dict):
            problems.append(f"{name}: not a mapping")
            continue
        if isinstance(entry.get("vulnerability"), str):
            name = f"entry {number} ({entry['vulnerability']})"
        for key in sorted(ENTRY_KEYS - set(entry)):
            problems.append(f"{name}: no `{key}`")
        for key in sorted(set(entry) - ENTRY_KEYS):
            problems.append(f"{name}: unknown key `{key}`")
        problems += _field_problems(name, entry, today)
    return problems


def _field_problems(name: str, entry: dict[Any, Any], today: date) -> list[str]:
    problems: list[str] = []
    if "vulnerability" in entry and not (
        isinstance(entry["vulnerability"], str) and ADVISORY.match(entry["vulnerability"])
    ):
        problems.append(f"{name}: `vulnerability` is not a GHSA or CVE id")
    if "package" in entry:
        package = entry["package"]
        if not isinstance(package, dict):
            problems.append(f"{name}: `package` is not a mapping")
        else:
            for key in sorted(set(package) - {"name"}):
                problems.append(f"{name}: unknown key `package.{key}`")
            if not (isinstance(package.get("name"), str) and package["name"].strip()):
                problems.append(f"{name}: no `package.name`")
    if "reason" in entry:
        reason = entry["reason"]
        if not (isinstance(reason, str) and reason.strip() and "\n" not in reason.strip()):
            problems.append(f"{name}: `reason` is not one sentence on one line")
    if "expires" in entry:
        problems += _expiry_problems(name, entry["expires"], today)
    return problems


def _expiry_problems(name: str, expires: Any, today: date) -> list[str]:
    # YAML 1.1 reads an unquoted 2027-01-31 as a date, and a quoted one as a string.
    if isinstance(expires, datetime) or not isinstance(expires, date | str):
        return [f"{name}: `expires` is not an ISO date"]
    if isinstance(expires, str):
        if not ISO_DATE.match(expires):
            return [f"{name}: `expires` is not an ISO date"]
        try:
            expires = date.fromisoformat(expires)
        except ValueError:
            return [f"{name}: `expires` is not an ISO date"]
    if expires < today:
        return [f"{name}: expired on {expires}"]
    if expires > today + timedelta(days=MAX_DAYS):
        return [f"{name}: expires on {expires}, more than {MAX_DAYS} days after {today}"]
    return []


def line_problems(text: str) -> list[str]:
    """The form of the lines image-scan.sh reads by text: every ``expires`` is on its own line as
    ``    expires: YYYY-MM-DD`` and every entry starts ``  - vulnerability: ID``."""
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        key = KEY_LINE.match(line)
        if not key:
            continue
        if key["key"] == "expires" and not EXPIRES_LINE.match(line):
            problems.append(f"line {number}: `expires` is not `    expires: YYYY-MM-DD`")
        if key["key"] == "vulnerability" and not VULNERABILITY_LINE.match(line):
            problems.append(f"line {number}: the entry does not start `  - vulnerability: ID`")
    return problems


TODAY = date(2027, 1, 15)
ENTRY: dict[str, Any] = {
    "vulnerability": "GHSA-6q48-6c7w-9gw2",
    "package": {"name": "certifi"},
    "reason": "Not reachable: the image never verifies a certificate chain with it.",
    "expires": "2027-02-14",
}


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"ignore": [deepcopy(entry) for entry in entries]}


def test_the_exceptions_file_holds_today() -> None:
    """The one test that reads today's date (UTC): the file as it is."""
    text = EXCEPTIONS.read_text(encoding="utf-8")
    today = datetime.now(UTC).date()
    assert exception_problems(yaml.safe_load(text), today) == []
    assert line_problems(text) == []


def test_the_exceptions_file_is_found_at_all() -> None:
    document = yaml.safe_load(EXCEPTIONS.read_text(encoding="utf-8"))
    assert isinstance(document, dict) and isinstance(document.get("ignore"), list)


def test_an_empty_list_and_a_valid_entry_pass() -> None:
    assert exception_problems({"ignore": []}, TODAY) == []
    assert exception_problems(_document(ENTRY), TODAY) == []


@pytest.mark.parametrize(
    ("expires", "passes"),
    [
        ("2027-01-14", False),
        ("2027-01-15", True),
        ("2027-04-15", True),
        ("2027-04-16", False),
        (date(2027, 1, 15), True),
        (date(2027, 4, 16), False),
        ("2027-1-15", False),
        ("2027-02-30", False),
        ("tomorrow", False),
        (20270215, False),
    ],
)
def test_an_entry_holds_through_its_date_and_for_90_days_at_most(
    expires: Any, passes: bool
) -> None:
    entry = {**ENTRY, "expires": expires}
    assert (exception_problems(_document(entry), TODAY) == []) is passes


def _without(path: str) -> dict[str, Any]:
    document = _document(ENTRY)
    target = document["ignore"][0]
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]
    return document


def _with(path: str, value: Any) -> dict[str, Any]:
    document = _document(ENTRY)
    target = document["ignore"][0]
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    return document


@pytest.mark.parametrize(
    ("document", "found"),
    [
        (_without("vulnerability"), "no `vulnerability`"),
        (_without("package"), "no `package`"),
        (_without("package.name"), "no `package.name`"),
        (_without("reason"), "no `reason`"),
        (_without("expires"), "no `expires`"),
        (_with("expire", "2027-02-14"), "unknown key `expire`"),
        (_with("package.version", "2026.6.17"), "unknown key `package.version`"),
        (_with("fix-state", "fixed"), "unknown key `fix-state`"),
        (_with("expires", "2027-01-14"), "expired on 2027-01-14"),
        (_with("expires", "2027-04-16"), "more than 90 days after 2027-01-15"),
        (_with("vulnerability", "*"), "is not a GHSA or CVE id"),
        (_with("package.name", " "), "no `package.name`"),
        (_with("reason", "One. Two\nthree"), "`reason` is not one sentence on one line"),
    ],
)
def test_an_entry_that_is_not_exactly_the_form_is_found(
    document: dict[str, Any], found: str
) -> None:
    assert any(found in problem for problem in exception_problems(document, TODAY)), found


@pytest.mark.parametrize(
    "document",
    [None, [], {}, {"ignore": {}}, {"ignore": [], "extra": 1}, {"ignore": ["GHSA-x"]}],
)
def test_a_file_that_is_not_one_list_of_entries_is_found(document: Any) -> None:
    assert exception_problems(document, TODAY) != []


def test_the_lines_the_scan_reads_by_text_are_found_when_they_differ() -> None:
    entry = (
        "ignore:\n  - vulnerability: GHSA-6q48-6c7w-9gw2\n    package:\n      name: certifi\n"
        "    reason: x\n    expires: 2027-02-14\n"
    )
    assert line_problems(entry) == []
    assert line_problems("# expires: any text in a comment\nignore: []\n") == []
    assert line_problems(entry.replace("expires: 2027", 'expires: "2027')) != []
    assert line_problems(entry.replace("    expires", "  expires")) != []
    assert line_problems(entry.replace("  - vulnerability", "  - { vulnerability")) != []
