"""The ported layer must be exactly what the converter produces.

Two drifts this catches:

1. A hand edit to `.dsh/skills/**` that the converter would not reproduce — the next
   re-run would silently revert it, so the edit is a lie until the converter knows it.
2. An upstream merge that changed `.claude/skills/**` without re-running the converter,
   leaving the ported layer quietly behind the layer it derives from.

Both are invisible in a diff review and cheap to check mechanically.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / ".claude" / "skills"
DST = REPO / ".dsh" / "skills"

_spec = importlib.util.spec_from_file_location(
    "port_skills_to_dsh", REPO / "tools" / "port_skills_to_dsh.py"
)
assert _spec is not None and _spec.loader is not None, "converter is not importable"
_converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_converter)

# Tool vocabulary that belongs to Claude Code and has no place in the ported layer.
CLAUDE_ONLY = re.compile(r"\bWebSearch\b|\bWebFetch\b|\bAskUserQuestion\b|^allowed-tools:", re.M)


class PortedLayerIsReproducible(unittest.TestCase):
    def test_layers_cover_the_same_files(self):
        upstream = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.md")}
        ported = {p.relative_to(DST).as_posix() for p in DST.rglob("*.md")}
        self.assertTrue(ported, ".dsh/skills is empty — run tools/port_skills_to_dsh.py")
        self.assertEqual(upstream, ported)

    def test_every_ported_file_is_what_the_converter_produces(self):
        for src in sorted(SRC.rglob("*.md")):
            rel = src.relative_to(SRC).as_posix()
            expected, _ = _converter.convert(src.read_text(encoding="utf-8"), rel)
            on_disk = (DST / rel).read_text(encoding="utf-8")
            self.assertEqual(
                expected,
                on_disk,
                f".dsh/skills/{rel} differs from the converter output — re-run "
                f"tools/port_skills_to_dsh.py instead of editing it by hand",
            )

    def test_no_claude_tool_vocabulary_survives(self):
        for path in sorted(DST.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            # ported_from deliberately names the Claude path; the vocabulary does not.
            body = "\n".join(
                line for line in text.splitlines() if not line.startswith("ported_from:")
            )
            hit = CLAUDE_ONLY.search(body)
            if hit:
                self.fail(
                    f"{path.relative_to(REPO)} still carries Claude Code vocabulary: "
                    f"{hit.group(0)!r}"
                )

    def test_frontmatter_carries_provenance(self):
        for path in sorted(DST.rglob("SKILL.md")):
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                continue
            self.assertIn(
                "ported_from:",
                text[: text.find("\n---", 4)],
                f"{path.relative_to(REPO)} frontmatter is missing ported_from",
            )


if __name__ == "__main__":
    unittest.main()
