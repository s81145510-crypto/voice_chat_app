"""Static syntax checks for inline JavaScript shipped in the Mini App template."""
from html.parser import HTMLParser
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class InlineScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            attrs = dict(attrs)
            # External vendor scripts are not part of our inline application code.
            self._current = [] if not attrs.get("src") else None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._current is not None:
            source = "".join(self._current).strip()
            if source:
                self.scripts.append(source)
            self._current = None


def test_inline_javascript_parses_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; inline JavaScript syntax check unavailable")

    parser = InlineScriptParser()
    parser.feed(TEMPLATE.read_text(encoding="utf-8"))
    assert parser.scripts, "No inline JavaScript blocks found in the Mini App template"

    for index, source in enumerate(parser.scripts, start=1):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", encoding="utf-8", delete=False
        ) as handle:
            handle.write(source)
            path = handle.name
        try:
            result = subprocess.run(
                [node, "--check", path],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, (
                f"Inline JavaScript block {index} has a syntax error:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        finally:
            Path(path).unlink(missing_ok=True)
