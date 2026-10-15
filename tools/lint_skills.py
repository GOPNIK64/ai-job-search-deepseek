#!/usr/bin/env python3
"""Lint the repo's skill, command, and settings files.

Run from anywhere: python tools/lint_skills.py

Checks:
- Every SKILL.md (.claude/skills/*, .agents/skills/*, .dsh/skills/*) has YAML
  frontmatter that parses, with non-empty `name` and `description` keys
- `allowed-tools` entries of the form `Bash(bun run <path> *)` point at files
  that exist (skill paths resolve relative to the repo root and to .agents/)
- Every .claude/commands/*.md starts with a `# /<name>` title
- .claude/settings.json is valid JSON with a permissions.allow list
- The ported dsh layer follows dsh's discovery rules: `<name>/SKILL.md` with a
  kebab-case directory name, and nothing nested deeper (see check_dsh_skill_tree)

Exit code 0 on success, 1 with a failure list otherwise.
"""

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("lint_skills.py requires PyYAML: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []

# dsh's documented skill identity: ^[a-z0-9]+(?:-[a-z0-9]+)*$
DSH_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check_skill(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(f"{rel(path)}: missing YAML frontmatter (file must start with ---)")
        return
    end = text.find("\n---", 4)
    if end == -1:
        errors.append(f"{rel(path)}: unterminated YAML frontmatter")
        return
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        errors.append(f"{rel(path)}: frontmatter is not valid YAML: {exc}")
        return
    if not isinstance(data, dict):
        errors.append(f"{rel(path)}: frontmatter did not parse to a mapping")
        return
    for key in ("name", "description"):
        if not data.get(key):
            errors.append(f"{rel(path)}: frontmatter missing required key '{key}'")

    allowed = data.get("allowed-tools", "")
    if isinstance(allowed, str):
        for match in re.finditer(r"bun run ([^\s)]+)", allowed):
            target = match.group(1).rstrip("*")
            if not target or target.endswith("/"):
                continue
            # Targets may contain globs (e.g. .agents/skills/*/cli/src/cli.ts);
            # require at least one existing file to match.
            if "*" in target:
                if not list(ROOT.glob(target)) and not list((ROOT / ".agents").glob(target)):
                    errors.append(f"{rel(path)}: allowed-tools glob matches no files: {target}")
            else:
                candidates = [ROOT / target, ROOT / ".agents" / target]
                if not any(c.is_file() for c in candidates):
                    errors.append(f"{rel(path)}: allowed-tools references a missing file: {target}")


def check_command(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").lstrip().splitlines()
    first = lines[0] if lines else ""
    if not first.startswith("# /"):
        errors.append(f"{rel(path)}: command file must start with a '# /<name>' title (found: {first[:50]!r})")


def check_dsh_skill_tree() -> int:
    """Lint the ported layer against dsh's own discovery rules.

    dsh discovers `<name>/SKILL.md` one level under a skill root and walks no deeper, so
    two failure modes are silent in the app and loud here:

    - a directory name that is not kebab-case, which is what dsh documents as skill
      identity (^[a-z0-9]+(?:-[a-z0-9]+)*$)
    - a SKILL.md nested two or more levels down, which dsh's provider never reaches: the
      skill simply does not exist as far as the catalog is concerned

    Returns the number of skills found, for the summary line.
    """
    root = ROOT / ".dsh" / "skills"
    if not root.is_dir():
        return 0
    found = 0
    for skill in sorted(root.glob("*/SKILL.md")):
        found += 1
        if not DSH_SKILL_NAME.match(skill.parent.name):
            errors.append(
                f"{rel(skill)}: directory name {skill.parent.name!r} is not kebab-case, "
                f"which dsh requires for skill identity"
            )
        check_skill(skill)
    for stray in sorted(root.glob("*/*/SKILL.md")):
        errors.append(
            f"{rel(stray)}: dsh does not discover a SKILL.md nested more than one level "
            f"under .dsh/skills/ - this skill would never load"
        )
    return found


def check_settings() -> None:
    path = ROOT / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f".claude/settings.json: {exc}")
        return
    if not isinstance(data, dict):
        errors.append(".claude/settings.json: expected top-level JSON value to be an object")
        return
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        errors.append(".claude/settings.json: expected permissions to be an object")
        return
    if not isinstance(permissions.get("allow"), list):
        errors.append(".claude/settings.json: expected permissions.allow to be a list")


def main() -> int:
    skills = sorted(ROOT.glob(".claude/skills/*/SKILL.md")) + sorted(ROOT.glob(".agents/skills/*/SKILL.md"))
    commands = sorted((ROOT / ".claude" / "commands").glob("*.md"))
    if not skills:
        errors.append("no SKILL.md files found - glob roots are wrong or the tree moved")
    if not commands:
        errors.append("no command files found under .claude/commands/")

    for skill in skills:
        check_skill(skill)
    for command in commands:
        check_command(command)
    dsh_skills = check_dsh_skill_tree()
    check_settings()

    if errors:
        print(f"lint_skills: {len(errors)} failure(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(
        f"lint_skills: OK ({len(skills)} skills, {dsh_skills} dsh skills, "
        f"{len(commands)} commands, settings.json)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
