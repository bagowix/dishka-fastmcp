import re

import dishka_fastmcp


def test_version_is_pep440_string() -> None:
    assert isinstance(dishka_fastmcp.__version__, str)
    assert re.match(r'^\d+\.\d+\.\d+', dishka_fastmcp.__version__)
