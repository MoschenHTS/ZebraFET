"""
test_version_consistency.py — The release version is declared in five places.

ZebraFET is archived on Zenodo and cited in the literature, so the version in
CITATION.cff is part of the published record rather than incidental packaging
metadata. A release where the installer, the package metadata and the citation
file disagree is a defect visible to anyone citing the software, and it has
happened before: v2.2.0 was initially tagged with constants.py and ZebraFET.iss
bumped while pyproject.toml, CITATION.cff and the README still read 2.1.4.

These tests pin all five to APP_VERSION so the next bump cannot go out partial.
"""
import re
from pathlib import Path

import pytest

from src.core.constants import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_app_version_is_a_semantic_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


@pytest.mark.parametrize(
    "filename, pattern, description",
    [
        ("pyproject.toml", r'^version\s*=\s*"([^"]+)"', "packaging metadata"),
        ("CITATION.cff", r'^version:\s*"([^"]+)"', "citation metadata"),
        ("ZebraFET.iss", r"^AppVersion=([0-9.]+)", "Windows installer"),
        # The build scripts name the artifacts users download, and were found
        # stranded at 2.1.2 — two releases behind — while the installer had
        # already been bumped.
        ("build_macos.sh", r'^VERSION="([^"]+)"', "macOS build script"),
        ("build_linux.sh", r'^VERSION="([^"]+)"', "Linux build script"),
    ],
)
def test_declared_version_matches_app_version(filename, pattern, description):
    match = re.search(pattern, _read(filename), re.MULTILINE)
    assert match, f"no version declaration found in {filename} ({description})"
    assert match.group(1) == APP_VERSION, (
        f"{filename} declares {match.group(1)}, but APP_VERSION is {APP_VERSION}"
    )


def test_readme_citation_states_the_current_version():
    """The README citation block carries the version users will copy into papers."""
    assert f"(v{APP_VERSION})" in _read("README.md"), (
        f"README citation does not mention v{APP_VERSION}"
    )


#: Files that state the Zenodo DOI. The two source files matter most: the About
#: tab shows it, and the report generator writes it into every exported report,
#: so a stale copy there is reproduced in documents that leave the lab.
DOI_SITES = (
    "README.md",
    "src/ui/dialogs/about_dialog.py",
    "src/export/report_generator.py",
)


def _declared_doi() -> str:
    doi = re.search(r'^doi:\s*"([^"]+)"', _read("CITATION.cff"), re.MULTILINE)
    assert doi, "CITATION.cff declares no DOI"
    return doi.group(1)


@pytest.mark.parametrize("name", DOI_SITES)
def test_every_stated_doi_matches_citation_cff(name):
    """A mismatched DOI would send citations to a different archived record."""
    assert _declared_doi() in _read(name), f"{name} states a different DOI"


def test_the_doi_is_the_concept_doi_not_a_version_doi():
    """Zenodo mints a DOI per release plus one concept DOI for the record itself.

    Only the concept DOI resolves to the current version; a version DOI freezes
    at the release that minted it. Citing a version DOI while naming a newer
    version sends the reader to the wrong software, which is what happened when
    every site above carried v2.1.1's DOI through the 2.2.0 bump.
    """
    assert _declared_doi() == "10.5281/zenodo.20183712", (
        "expected the concept DOI; a per-version DOI goes stale on the next release"
    )
