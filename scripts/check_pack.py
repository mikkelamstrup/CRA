#!/usr/bin/env python3
"""Read-only syntax/link checks for the plan pack; not a platform acceptance test."""
import ast
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote

root = Path(__file__).resolve().parent.parent
errors = []
excluded = {'.git', '.local', '.runtime', '.secrets', '.venv', 'node_modules', 'reports', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'dist'}
markdown = []
for directory, subdirs, files in os.walk(root):
    subdirs[:] = [name for name in subdirs if name not in excluded]
    markdown.extend(Path(directory) / name for name in files if name.endswith('.md'))
for path in markdown:
    text = path.read_text()
    fences = [line for line in text.splitlines() if line.startswith("```")]
    if len(fences) % 2:
        errors.append(f"{path.relative_to(root)}: unbalanced code fences")
    for ref in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        if ref.startswith(("https://", "http://", "#", "mailto:")):
            continue
        target = unquote(ref.split("#")[0])
        if not (path.parent / target).exists():
            errors.append(f"{path.relative_to(root)}: missing {target}")
for path in (root / "scripts").glob("*.py"):
    ast.parse(path.read_text(), filename=str(path.relative_to(root)))
fixture = json.loads((root / "examples/event.fixture.json").read_text())
if fixture.get("fixture") is not True:
    errors.append("event fixture must be explicitly synthetic")
if errors:
    raise SystemExit("FAIL\n" + "\n".join(errors))
print(f"PASS: {len(markdown)} Markdown files (local links/fences), Python syntax, synthetic JSON fixture")
print("NOT RUN: YAML schema/mission runtime, server/KVM/isolation/capacity")
