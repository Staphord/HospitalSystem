"""The assistant must never name a screen the asking role cannot open.

The content pack tells staff where to go ("Reports, then Patient reports
(/admin/reports/patients)"). Those screens live in another repo, behind a role
check the content pack knows nothing about. When the two drift, a receptionist is
told to open a page that is not in their menu - which is exactly the bug these
tests exist to prevent.

The bridge is `frontend-hospital/nav-manifest.json`, generated from `HOSPITAL_NAV`
by `npm run nav:manifest` and committed. It maps every menu path to the roles whose
sidebar shows it.

These tests drive the real retrieval path - `build_retrieval_context` and
`visible_entries` - rather than re-reading `entry.roles`, so they check what a role
would actually be shown, department and approval filters included.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from app.assistant.content import ROLE_DEPARTMENT
from app.assistant.retrieval import build_retrieval_context, visible_entries

# Any absolute app path mentioned in a content entry, e.g. "/admin/reports/patients".
_PATH = re.compile(r"(?<![\w/])(/[a-z][a-z0-9]*(?:/[a-z0-9:-]+)*)")

TENANT = "hosp-navcheck"

# Paths a content entry may legitimately mention even though they are not menu
# entries. Keep this aligned with INTENTIONALLY_UNLINKED in navContract.test.tsx.
NON_MENU_PATHS = frozenset({"/profile", "/notifications", "/billing", "/unauthorized"})


def _manifest_path() -> Path | None:
    override = os.environ.get("NAV_MANIFEST_PATH")
    if override:
        return Path(override)

    # services/report-service/tests/unit/<this file> -> repo parent -> sibling checkout
    sibling = Path(__file__).resolve().parents[5] / "frontend-hospital" / "nav-manifest.json"
    return sibling if sibling.exists() else None


@pytest.fixture(scope="module")
def nav_manifest() -> dict:
    path = _manifest_path()
    if path is None or not path.exists():
        pytest.skip(
            "frontend-hospital/nav-manifest.json not found - check out the frontend "
            "alongside this repo, or set NAV_MANIFEST_PATH, to validate assistant "
            "screen references against the real navigation"
        )

    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema"] == 1, (
        f"nav-manifest.json is schema {manifest['schema']}, this test understands 1 - "
        "update the test alongside the generator"
    )
    return manifest


def _mentioned_paths(text: str | None) -> set[str]:
    if not text:
        return set()
    return {match for match in _PATH.findall(text)} - NON_MENU_PATHS


def _entry_paths(entry) -> set[str]:
    return _mentioned_paths(entry.location) | _mentioned_paths(entry.body)


@pytest.mark.parametrize("role", sorted(ROLE_DEPARTMENT))
def test_every_screen_the_assistant_names_exists(role: str, nav_manifest: dict) -> None:
    """A path in the content pack must be a real screen in the frontend."""
    context = build_retrieval_context(TENANT, [role], today=date(2099, 1, 1))
    assert context is not None

    screens = nav_manifest["screens"]
    unknown: list[str] = []

    for entry in visible_entries(context):
        for path in sorted(_entry_paths(entry)):
            if path not in screens:
                unknown.append(f"{entry.entry_id} -> {path}")

    assert not unknown, (
        "the assistant names screens that are not in the frontend navigation "
        f"(shown to {role}): {unknown}. Either the path is wrong, or the page needs "
        "a HOSPITAL_NAV entry and `npm run nav:manifest` rerun."
    )


@pytest.mark.parametrize("role", sorted(ROLE_DEPARTMENT))
def test_no_role_is_sent_to_a_screen_it_cannot_see(role: str, nav_manifest: dict) -> None:
    """A role must not be shown content pointing at a screen missing from its menu."""
    context = build_retrieval_context(TENANT, [role], today=date(2099, 1, 1))
    assert context is not None

    screens = nav_manifest["screens"]
    unreachable: list[str] = []

    for entry in visible_entries(context):
        for path in sorted(_entry_paths(entry)):
            screen = screens.get(path)
            if screen is None:
                continue  # reported by the test above
            if role not in screen["roles"]:
                unreachable.append(f"{entry.entry_id} -> {path} ({screen['label']})")

    assert not unreachable, (
        f"a {role} can be shown content pointing at screens their sidebar does not "
        f"list: {unreachable}. Either narrow the entry's roles in entries.py, or add "
        "the role to the HOSPITAL_NAV item and rerun `npm run nav:manifest`."
    )


def test_report_catalog_entries_say_where_to_go(nav_manifest: dict) -> None:
    """Every report entry carries a location, and it points at a real screen."""
    context = build_retrieval_context(TENANT, ["hospital_admin"], today=date(2099, 1, 1))
    assert context is not None

    from app.assistant.content import ContentKind

    catalog = [e for e in visible_entries(context) if e.kind is ContentKind.REPORT_CATALOG]
    assert catalog, "no report catalog entries are visible to hospital_admin"

    for entry in catalog:
        assert entry.location, f"{entry.entry_id} has no location - staff are not told where to go"
        paths = _mentioned_paths(entry.location)
        assert paths, f"{entry.entry_id} location names no path: {entry.location!r}"
        for path in paths:
            assert path in nav_manifest["screens"], f"{entry.entry_id} points at unknown {path}"
