"""Routing rules: which model transcribes which page, and what a rerun costs.

Run: .venv/bin/python -m pytest tests/
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipeline  # noqa: E402
from parse_pages import header_page_type  # noqa: E402


@pytest.mark.parametrize("header, page_type", [
    ("168 ADA CROCKER-LANGLEY DIRECTORY. ADE", "alphabetical"),
    ("SAC CROCKER-LANGLEY DIRECTORY. SAH 1615", "alphabetical"),
    ("STREET AND AVENUE GUIDE. 75", "street_guide"),
    ("38 Art Societies. CROCKER-LANGLEY DIRECTORY. Banks.", "classified"),
    ("SEE PAGE 3 TRANSACTS A GENERAL BANKING BUSINESS", None),  # ad cropped as header
    ("", None),
])
def test_header_page_type(header, page_type):
    assert header_page_type(header) == page_type


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Fake prep and transcripts folders: page A alphabetical, page B classified, page C unknown."""
    prep, transcripts = tmp_path / "prep", tmp_path / "transcripts"
    for page in ("A", "B", "C"):
        for part in ("col1_part1", "col1_part2", "col2_part1"):
            (prep / page).mkdir(parents=True, exist_ok=True)
            (prep / page / f"{part}.png").write_bytes(b"")
    monkeypatch.setattr(pipeline, "PREP", prep)
    monkeypatch.setattr(pipeline, "TRANSCRIPTS", transcripts)
    return transcripts


HEADERS = {"A": "168 ADA CROCKER-LANGLEY DIRECTORY. ADE",
           "B": "38 Art Societies. CROCKER-LANGLEY DIRECTORY. Banks.",
           "C": "unreadable"}


def test_routes_by_page_type(workspace):
    routes, _ = pipeline.plan(["A", "B", "C"], HEADERS, force=False)
    assert routes["A"]["model"] == pipeline.HAIKU
    assert routes["B"]["model"] == pipeline.SONNET
    assert routes["C"]["model"] == pipeline.HAIKU and routes["C"]["page_type"] is None


def test_cost_estimate_counts_only_missing_work(workspace):
    _, cost = pipeline.plan(["A", "B"], HEADERS, force=False)
    full = 3 * pipeline.COST_PER_CHUNK[pipeline.HAIKU] + 3 * pipeline.COST_PER_CHUNK[pipeline.SONNET] \
        + pipeline.COST_PER_EXTRACTION
    assert cost == pytest.approx(full)

    # Transcribe A fully and extract B: only B's columns are left to pay for.
    done = workspace / pipeline.HAIKU / "A"
    done.mkdir(parents=True)
    for part in ("col1_part1", "col1_part2", "col2_part1"):
        (done / f"{part}.txt").write_text("x")
    extracted = workspace / pipeline.SONNET / "B"
    extracted.mkdir(parents=True)
    (extracted / "classified.json").write_text("{}")
    _, cost = pipeline.plan(["A", "B"], HEADERS, force=False)
    assert cost == pytest.approx(3 * pipeline.COST_PER_CHUNK[pipeline.SONNET])

    _, forced = pipeline.plan(["A", "B"], HEADERS, force=True)
    assert forced == pytest.approx(full)
