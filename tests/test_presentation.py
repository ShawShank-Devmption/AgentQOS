"""Structural checks for the P5.5 presentation artifact."""

from pathlib import Path
from zipfile import BadZipFile, ZipFile

DECK_PATH = Path("docs/agent_aware_networking_demo.pptx")
REHEARSAL_PATH = Path("docs/demo_rehearsal.md")


def test_demo_deck_is_a_ten_slide_powerpoint() -> None:
    """Require the checked-in presentation to remain a valid ten-slide deck."""
    assert DECK_PATH.is_file()

    try:
        with ZipFile(DECK_PATH) as archive:
            slide_names = {
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            }
            presentation = archive.read("ppt/presentation.xml")
    except BadZipFile as exc:
        raise AssertionError(f"invalid PowerPoint package: {DECK_PATH}") from exc

    assert len(slide_names) == 10
    assert b"p:sldIdLst" in presentation


def test_rehearsal_runbook_names_the_presentation() -> None:
    """Keep the live-demo runbook connected to its presentation artifact."""
    assert DECK_PATH.name in REHEARSAL_PATH.read_text(encoding="utf-8")
