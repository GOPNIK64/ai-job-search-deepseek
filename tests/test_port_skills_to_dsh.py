"""The ported layer must be exactly what the converter produces.

Three drifts this catches:

1. A hand edit to `.dsh/skills/**` that the converter would not reproduce — the next
   re-run would silently revert it, so the edit is a lie until the converter knows it.
   Judgement edits belong in `tools/port_skills_to_dsh.py`'s `OVERRIDES`, not in `.dsh/`.
2. An upstream merge that changed `.claude/skills/**` or `.claude/commands/**` without
   re-running the converter, leaving the ported layer quietly behind its source.
3. A file in `.dsh/` whose source no longer exists — a command upstream deleted, or a
   skill renamed there, both of which leave a dead skill in dsh's catalog.

All three are invisible in a diff review and cheap to check mechanically.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / ".claude" / "skills"
CMDS = REPO / ".claude" / "commands"
DST = REPO / ".dsh" / "skills"

_spec = importlib.util.spec_from_file_location(
    "port_skills_to_dsh", REPO / "tools" / "port_skills_to_dsh.py"
)
assert _spec is not None and _spec.loader is not None, "converter is not importable"
_converter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_converter)

# Tool vocabulary that belongs to Claude Code and has no place in the ported layer.
CLAUDE_ONLY = re.compile(r"\bWebSearch\b|\bWebFetch\b|\bAskUserQuestion\b|^allowed-tools:", re.M)

# The commands that must exist as user-invocable skills, and the two layers that must
# stay model-invocable instead (upstream's skills are model-triggered; its commands are
# not, and the port has to preserve that split or the model can start /apply by itself).
MODEL_INVOCABLE = {"job-application-assistant", "job-scraper", "upskill"}


def expected_outputs() -> dict[str, str]:
    """Mirror main(): every file the converter would write, with its exact contents."""
    out: dict[str, str] = {}
    for src in sorted(SRC.rglob("SKILL.md")):
        rel = src.relative_to(SRC).as_posix()
        text, counts = _converter.convert(src.read_text(encoding="utf-8"), rel)
        out[rel] = _converter.apply_overrides(text, rel, counts)
    for src in sorted(CMDS.glob("*.md")):
        name = src.stem
        text, counts = _converter.convert_command(src.read_text(encoding="utf-8"), name)
        rel = f"{name}/SKILL.md"
        out[rel] = _converter.apply_overrides(text, rel, counts)
    return out


def frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[:end] if end != -1 else ""


class PortedLayerIsReproducible(unittest.TestCase):
    def setUp(self):
        self.expected = expected_outputs()

    def test_layers_cover_the_same_files(self):
        on_disk = {p.relative_to(DST).as_posix() for p in DST.rglob("*.md")}
        self.assertTrue(on_disk, ".dsh/skills is empty — run tools/port_skills_to_dsh.py")
        self.assertEqual(set(self.expected), on_disk)

    def test_every_ported_file_is_what_the_converter_produces(self):
        for rel, expected in self.expected.items():
            on_disk = (DST / rel).read_text(encoding="utf-8")
            self.assertEqual(
                expected,
                on_disk,
                f".dsh/skills/{rel} differs from the converter output — re-run "
                f"tools/port_skills_to_dsh.py instead of editing it by hand",
            )

    def test_no_claude_tool_vocabulary_survives(self):
        """No Claude Code tool vocabulary, except where a line is *about* upstream.

        A line that names the upstream runtime is contrasting with it — the ported
        escalation rule does exactly that when it explains that dsh's fetch never reads
        robots.txt. That is documentation, not a tool call under the wrong name, so the
        exemption is gated on the contrast marker rather than on a path allowlist.
        """
        for path in sorted(DST.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                if line.startswith("ported_from:"):
                    continue  # provenance deliberately names the Claude path
                if "Claude Code" in line:
                    continue  # a contrastive mention, not a tool call
                hit = CLAUDE_ONLY.search(line)
                if hit:
                    self.fail(
                        f"{path.relative_to(REPO)}:{n} still carries Claude Code "
                        f"vocabulary: {hit.group(0)!r}"
                    )

    def test_frontmatter_carries_provenance(self):
        for path in sorted(DST.rglob("SKILL.md")):
            fm = frontmatter(path.read_text(encoding="utf-8"))
            if not fm:
                continue
            self.assertIn(
                "ported_from:",
                fm,
                f"{path.relative_to(REPO)} frontmatter is missing ported_from",
            )

    def test_commands_are_user_invocable_and_never_model_invoked(self):
        """Upstream's commands are user-triggered only, and the port has to keep that.

        A command the model can fire on its own turns /apply into something that starts
        itself, which is the opposite of what the pipeline's review gate assumes.
        """
        names = sorted(p.stem for p in CMDS.glob("*.md"))
        self.assertTrue(names, "no upstream commands found")
        for name in names:
            fm = frontmatter((DST / name / "SKILL.md").read_text(encoding="utf-8"))
            self.assertIn(f"name: {name}", fm)
            self.assertIn("user-invocable: true", fm)
            self.assertIn("disable-model-invocation: true", fm)
            self.assertRegex(fm, r"(?m)^description: \S")

    def test_ported_skills_stay_model_invocable(self):
        for name in sorted(MODEL_INVOCABLE):
            fm = frontmatter((DST / name / "SKILL.md").read_text(encoding="utf-8"))
            self.assertNotIn(
                "disable-model-invocation: true",
                fm,
                f"{name} is a model-triggered skill upstream; making it user-only would "
                f"leave it unreachable from a normal conversation",
            )

    def test_no_upstream_arguments_token_survives(self):
        for path in sorted(DST.rglob("*.md")):
            self.assertNotIn(
                "$ARGUMENTS",
                path.read_text(encoding="utf-8"),
                f"{path.relative_to(REPO)} still carries Claude Code's $ARGUMENTS, which "
                f"dsh has no substitution for",
            )


class LayerBoundaryTests(unittest.TestCase):
    """Dispatchers are generated; content is not.

    This is the invariant that keeps /setup's output alive. /setup writes the candidate
    profile into `.claude/skills/job-application-assistant/01-`, `02-` and `04-` through
    `07-`, and every command body writes newly confirmed facts back to `01-` as it runs.
    If any of those lived in the generated layer, the next converter run would delete
    them - silently, and with no copy anywhere else.
    """

    def test_the_generated_layer_holds_only_dispatchers(self):
        for path in sorted(DST.rglob("*.md")):
            self.assertEqual(
                path.name,
                "SKILL.md",
                f"{path.relative_to(REPO)} is content in the generated layer; "
                f"content belongs in .claude/, where the workflow writes it",
            )

    def test_setup_writes_outside_the_generated_layer(self):
        setup = (DST / "setup" / "SKILL.md").read_text(encoding="utf-8")

        self.assertNotIn(
            ".dsh/",
            setup,
            "the setup flow must not read or write inside the generated layer",
        )
        self.assertIn("search-queries.md", setup)

    def test_command_references_still_resolve_after_the_un_remap(self):
        """Paths that point at command dispatchers get remapped; paths that point at
        content must not, or the reference dangles."""
        for name in ("apply", "rank", "interview"):
            text = (DST / name / "SKILL.md").read_text(encoding="utf-8")
            for target in re.findall(r"`\.dsh/skills/([a-z-]+)/SKILL\.md`", text):
                self.assertTrue(
                    (DST / target).is_dir(),
                    f"{name}/SKILL.md points at .dsh/skills/{target}/, which does not exist",
                )


if __name__ == "__main__":
    unittest.main()
