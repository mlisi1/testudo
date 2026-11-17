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
