from functools import cache
from pathlib import Path
from string import Template


@cache
def _template(name: str) -> Template:
    return Template((Path(__file__).parent / f"{name}.md").read_text())


def render(name: str, **values: object) -> str:
    """Fill a prompt file. Uses $placeholders so SQL braces in the content are left alone."""
    return _template(name).substitute({k: str(v) for k, v in values.items()})
