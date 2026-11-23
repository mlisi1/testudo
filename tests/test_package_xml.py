"""package.xml must stay valid XML: otherwise colcon silently builds Testudo as a plain
`python` package (no ament index entry, so `ros2 run testudo testudo` can't find it)."""
from __future__ import annotations

import xml.dom.minidom
from pathlib import Path

PACKAGE_XML = Path(__file__).resolve().parent.parent / "package.xml"


def test_package_xml_is_well_formed() -> None:
    # A `--` inside an XML comment is the usual way it breaks.
    document = xml.dom.minidom.parse(str(PACKAGE_XML))
    assert document.getElementsByTagName("build_type")[0].firstChild.data == "ament_python"


def test_every_version_agrees() -> None:
    """bloom releases package.xml's version: setup.py and both changelogs must say the same."""
    root = PACKAGE_XML.parent
    version = xml.dom.minidom.parse(str(PACKAGE_XML)).getElementsByTagName("version")[0].firstChild.data
    assert f'version="{version}"' in (root / "setup.py").read_text(encoding="utf-8")
    # The newest released section first, unless a "Forthcoming" one (unreleased
    # changes, which catkin_prepare_release turns into the next version) precedes it.
    rst = (root / "CHANGELOG.rst").read_text(encoding="utf-8").splitlines()
    released = [index for index, line in enumerate(rst) if line and set(line) == {"-"}]
    titles = [rst[index - 1] for index in released]
    if titles and titles[0] == "Forthcoming":
        titles = titles[1:]
    assert titles and titles[0].startswith(f"{version} (")
    assert all(len(rst[index]) == len(rst[index - 1]) for index in released)
    assert f"## {version} (" in (root / "CHANGELOG.md").read_text(encoding="utf-8")


def test_no_dependency_bloom_cannot_resolve_everywhere() -> None:
    """nav2_msgs isn't released into every distro, and bloom needs every declared key."""
    text = PACKAGE_XML.read_text(encoding="utf-8")
    assert "<test_depend>nav2_msgs</test_depend>" not in text
    assert "<url type=\"repository\">" in text
