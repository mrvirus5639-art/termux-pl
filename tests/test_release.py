import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parent.parent / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

LOG = """# Changelog

## [Unreleased]

### Fixed
- a bug

## [1.0.0] - 2026-10-02

### Added
- everything

[Unreleased]: https://github.com/OWNER/REPO/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/OWNER/REPO/releases/tag/v1.0.0
"""


def test_bump():
    assert release.bump("1.2.3", "patch") == "1.2.4"
    assert release.bump("1.2.3", "minor") == "1.3.0"
    assert release.bump("1.2.3", "major") == "2.0.0"
    assert release.bump("1.2.3", "4.0.0") == "4.0.0"


def test_unreleased_and_version_notes():
    assert release.unreleased_body(LOG) == "### Fixed\n- a bug"
    assert release.version_notes(LOG, "1.0.0") == "### Added\n- everything"


def test_changelog_update_and_links():
    out = release.rewrite_links(release.update_changelog(LOG, "1.0.1", "2026-11-01"), "ben/termuxpl")
    assert "## [Unreleased]\n\n## [1.0.1] - 2026-11-01\n\n### Fixed\n- a bug" in out
    assert release.unreleased_body(out) == ""
    assert out.rstrip().endswith(
        "[Unreleased]: https://github.com/ben/termuxpl/compare/v1.0.1...HEAD\n"
        "[1.0.1]: https://github.com/ben/termuxpl/compare/v1.0.0...v1.0.1\n"
        "[1.0.0]: https://github.com/ben/termuxpl/releases/tag/v1.0.0")
