"""Contract tests for PROD-029 / F-20 dependency reproducibility.

The production install path must stay deterministic:

* ``requirements.txt`` is the authoritative generated set and every entry is
  fully pinned (``name==version``), including transitive dependencies;
* ``requirements.in`` is only the direct-dependency source and is never used
  by the production Docker image or by CI;
* ``Dockerfile.backend.prod`` and ``.github/workflows/ci.yml`` install from
  ``requirements.txt``.

These tests fail when dependency resolution is allowed to become floating
again in a production path.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]

# name==version, with an optional extras marker before == (e.g. celery[redis]).
_PINNED_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)"
    r"==(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+*-]*)$"
)
# Leading dependency name of a direct requirement line (before any specifier).
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _pinned_entries() -> dict[str, str]:
    """Return {base package name: version} for every pinned requirement."""
    entries: dict[str, str] = {}
    for raw_line in _read("requirements.txt").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PINNED_RE.fullmatch(line)
        if match is None:
            raise AssertionError(
                f"Non-pinned requirement in requirements.txt: {line!r}"
            )
        extra = match.group("name")
        base = extra.split("[", 1)[0].lower()
        entries[base] = match.group("version")
    return entries


class DependencyContractTests(SimpleTestCase):
    maxDiff = None

    def test_requirements_txt_is_fully_pinned(self):
        entries = _pinned_entries()
        # At least every direct dependency plus resolved transitive packages.
        self.assertGreaterEqual(len(entries), 13)

    def test_all_direct_dependencies_are_pinned(self):
        pinned = _pinned_entries()
        direct_names = []
        for raw_line in _read("requirements.in").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name_match = _NAME_RE.match(line)
            self.assertIsNotNone(name_match, f"Unparseable direct dep: {line!r}")
            direct_names.append(name_match.group(0).lower())
        self.assertGreaterEqual(len(direct_names), 13)
        for name in direct_names:
            self.assertIn(
                name,
                pinned,
                f"Direct dependency {name!r} is not pinned in requirements.txt",
            )

    def test_production_dockerfile_installs_pinned_requirements_only(self):
        dockerfile = _read("Dockerfile.backend.prod")
        self.assertIn("-r requirements.txt", dockerfile)
        # The floating direct-dependency source must never be installed.
        self.assertNotIn("requirements.in", dockerfile)

    def test_ci_installs_pinned_requirements_only(self):
        ci_workflow = _read(".github/workflows/ci.yml")
        self.assertIn("pip install -r requirements.txt", ci_workflow)
        self.assertNotIn("requirements.in", ci_workflow)

    def test_only_authoritative_dependency_files_exist(self):
        root_requirement_files = sorted(
            path.name
            for path in REPO_ROOT.glob("requirements*")
            if path.suffix in {".txt", ".in"}
        )
        self.assertEqual(root_requirement_files, ["requirements.in", "requirements.txt"])
