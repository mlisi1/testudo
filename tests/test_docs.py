"""The documentation stays true to the code: every code documented, every generated table current."""
from __future__ import annotations

import importlib.util
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_reference", DOCS / "gen" / "gen_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _generator()
SOURCE_CODE = re.compile(rf"\b(?:{'|'.join(GEN.CODE_PAGES)})-\d{{3}}\b")


def _source_codes() -> set[str]:
    return {code for path in (ROOT / "testudo").rglob("*.py") for code in SOURCE_CODE.findall(path.read_text(encoding="utf-8"))}


def _documented() -> list[tuple[str, str, str]]:
    """(code, page, anchor) for every code entry in the docs."""
    entries = []
    for page in GEN.CODE_PAGES.values():
        text = (DOCS / page).read_text(encoding="utf-8")
        entries += [(code, page, anchor) for code, _title, anchor in GEN.CODE_HEADING.findall(text)]
    return entries


def test_every_code_in_the_source_has_exactly_one_entry_and_every_entry_a_code() -> None:
    counts = Counter(code for code, _page, _anchor in _documented())
    assert set(counts) == _source_codes()
    assert max(counts.values()) == 1


def test_a_code_is_documented_on_its_prefix_page_under_its_own_anchor() -> None:
    for code, page, anchor in _documented():
        assert page == GEN.CODE_PAGES[code.split("-")[0]]
        assert anchor == code.lower()


def test_every_builtin_plugin_has_a_page_in_the_navigation() -> None:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for page in GEN.PLUGIN_PAGES:
        assert (DOCS / "plugins" / f"{page}.md").is_file()
        assert f"plugins/{page}.md" in config


@pytest.mark.parametrize("path", sorted(GEN.generated_files()), ids=lambda path: path.name)
def test_generated_reference_is_current(path: Path) -> None:
    assert path.read_text(encoding="utf-8") == GEN.generated_files()[path], "run docs/gen/gen_reference.py"


def test_every_mark_on_the_dashboard_page_stays_on_screen_and_links_to_a_real_page() -> None:
    dashboard_map = GEN.dashboard_map
    for crops in dashboard_map.SECTIONS.values():
        for crop in crops:
            for item in crop.items:
                for spot in item.spots:
                    assert crop.col <= spot.col and spot.col + spot.width <= crop.col + crop.width, item.name
                    assert crop.row <= spot.row and spot.row + spot.height <= crop.row + crop.height, item.name
                assert item.target is None or (DOCS / item.target.partition("#")[0]).is_file(), item.target


def test_every_code_in_the_explore_pages_sample_data_is_documented() -> None:
    sample = (DOCS / "assets" / "js" / "explorer-data.js").read_text(encoding="utf-8")
    documented = {code for code, _page, _anchor in _documented()}
    assert set(SOURCE_CODE.findall(sample)) <= documented


def test_every_page_the_explore_page_links_to_exists() -> None:
    sample = (DOCS / "assets" / "js" / "explorer-data.js").read_text(encoding="utf-8")
    for page in re.findall(r'page: "([^"#]+?)/?(?:#[^"]*)?"', sample):
        assert (DOCS / f"{page}.md").is_file() or (DOCS / page / "index.md").is_file(), page


def test_example_config_is_valid_yaml() -> None:
    assert yaml.safe_load((ROOT / "config" / "example_config.yaml").read_text(encoding="utf-8"))


def test_dashboard_marks_stay_on_the_page() -> None:
    """Only the overview links, and only to sections of the same page: the tables hold the links elsewhere."""
    for path, content in GEN.dashboard_map.generated_files(DOCS / "includes").items():
        figures = content.split("</figure>")[0]
        hrefs = re.findall(r'<a [^>]*href="([^"]*)"', figures)
        assert all(href.startswith("#") for href in hrefs), path
        assert path.name == "overview.md" or not hrefs, path
