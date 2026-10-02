#!/usr/bin/env python3
"""Cut a Termux PL release.

    python scripts/release.py patch          # 1.0.0 -> 1.0.1  (bug fixes)
    python scripts/release.py minor          # 1.0.0 -> 1.1.0  (new features)
    python scripts/release.py major          # 1.0.0 -> 2.0.0  (breaking changes)
    python scripts/release.py 1.2.3          # explicit version
    python scripts/release.py minor --push   # also push the commit + tag (starts the GitHub release)
    python scripts/release.py minor --dry-run

What it does:
  1. checks the working tree is clean and that CHANGELOG "Unreleased" has entries
  2. bumps __version__ in termuxpl/__init__.py
  3. renames "## [Unreleased]" to "## [X.Y.Z] - today", adds a fresh Unreleased section,
     and updates the compare links at the bottom
  4. commits "Release vX.Y.Z" and creates an annotated tag vX.Y.Z

Pushing the tag triggers .github/workflows/release.yml, which builds the
packages and publishes a GitHub Release using that changelog section as notes.

    python scripts/release.py links OWNER/REPO   # only rewrite changelog links
    python scripts/release.py notes 1.2.3        # print that version's changelog section
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "termuxpl" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def current_version() -> str:
    m = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"Could not find __version__ in {INIT}")
    return ".".join(m.groups())


def bump(version: str, part: str) -> str:
    if SEMVER.match(part):
        return part
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    sys.exit(f"Unknown bump '{part}'. Use major, minor, patch or X.Y.Z")


def unreleased_body(text: str) -> str:
    m = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|^\[Unreleased\]:|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def version_notes(text: str, version: str) -> str:
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[Unreleased\]:|\Z)",
                  text, re.M | re.S)
    return m.group(1).strip() if m else ""


def repo_slug() -> str | None:
    url = git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def rewrite_links(text: str, slug: str | None) -> str:
    """Rebuild the compare links at the bottom from the version headings."""
    slug = slug or "OWNER/REPO"
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
    text = re.sub(r"\n(\[[^\]]+\]: https?://\S+\n?)+\s*\Z", "\n", text).rstrip() + "\n\n"
    base = f"https://github.com/{slug}"
    lines = []
    if versions:
        lines.append(f"[Unreleased]: {base}/compare/v{versions[0]}...HEAD")
    for i, v in enumerate(versions):
        if i + 1 < len(versions):
            lines.append(f"[{v}]: {base}/compare/v{versions[i + 1]}...v{v}")
        else:
            lines.append(f"[{v}]: {base}/releases/tag/v{v}")
    return text + "\n".join(lines) + "\n"


def update_changelog(text: str, new: str, date: str) -> str:
    text = re.sub(r"^## \[Unreleased\][^\n]*\n",
                  f"## [Unreleased]\n\n## [{new}] - {date}\n", text, count=1, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bump", help="major | minor | patch | X.Y.Z | links | notes")
    ap.add_argument("arg", nargs="?", help="OWNER/REPO for 'links', X.Y.Z for 'notes'")
    ap.add_argument("--push", action="store_true", help="push the commit and tag to origin")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, touch nothing")
    ap.add_argument("--allow-empty", action="store_true", help="release even if Unreleased is empty")
    args = ap.parse_args()

    log = CHANGELOG.read_text(encoding="utf-8")

    if args.bump == "links":
        slug = args.arg or repo_slug()
        if not slug:
            sys.exit("Give OWNER/REPO, or add a GitHub 'origin' remote first.")
        CHANGELOG.write_text(rewrite_links(log, slug), encoding="utf-8")
        py = ROOT / "pyproject.toml"
        py.write_text(re.sub(r"github\.com/[^/\s\"]+/[^/\s\"]+?(?=[/\"])", f"github.com/{slug}",
                             py.read_text(encoding="utf-8")), encoding="utf-8")
        rd = ROOT / "README.md"
        rd.write_text(rd.read_text(encoding="utf-8").replace("OWNER/REPO", slug), encoding="utf-8")
        print(f"Links now point to github.com/{slug}")
        return
    if args.bump == "notes":
        print(version_notes(log, (args.arg or current_version()).lstrip("v")) or "No release notes.")
        return

    old = current_version()
    new = bump(old, args.bump)
    tag = f"v{new}"
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        sys.exit(f"New version {new} must be greater than {old}")

    if git("status", "--porcelain"):
        sys.exit("Working tree has uncommitted changes. Commit or stash them first.")
    if git("tag", "--list", tag):
        sys.exit(f"Tag {tag} already exists.")
    notes = unreleased_body(log)
    if not notes and not args.allow_empty:
        sys.exit("CHANGELOG.md has nothing under [Unreleased]. Describe the changes first "
                 "(or pass --allow-empty).")

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"Releasing {old} -> {new} on branch '{branch}'\n")
    print(notes or "(no changelog entries)")
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return

    INIT.write_text(VERSION_RE.sub(f'__version__ = "{new}"', INIT.read_text(encoding="utf-8")),
                    encoding="utf-8")
    today = dt.date.today().isoformat()
    CHANGELOG.write_text(rewrite_links(update_changelog(log, new, today), repo_slug()),
                         encoding="utf-8")

    git("add", str(INIT.relative_to(ROOT)), str(CHANGELOG.relative_to(ROOT)))
    git("commit", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"Termux PL {tag}\n\n{notes}")
    print(f"\nCommitted and tagged {tag}.")

    if args.push:
        git("push", "origin", branch)
        git("push", "origin", tag)
        print(f"Pushed. GitHub Actions is now building the {tag} release.")
    else:
        print(f"Publish it with:\n  git push origin {branch}\n  git push origin {tag}")


if __name__ == "__main__":
    main()
