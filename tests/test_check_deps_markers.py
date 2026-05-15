import sys

import check_deps


def test_marker_matches_current_platform():
    marker = f'sys_platform == "{sys.platform}"'
    assert check_deps._marker_matches(marker) is True


def test_marker_skips_other_platform():
    other = "win32" if sys.platform != "win32" else "linux"
    marker = f'sys_platform == "{other}"'
    assert check_deps._marker_matches(marker) is False


def test_parse_requirement_name_ignores_versions_and_extras():
    assert check_deps._parse_requirement_name("uvicorn[standard]>=0.23.0,<0.30.0") == "uvicorn"
