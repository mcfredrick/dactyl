#!/usr/bin/env python3
"""Validates that a generated job digest post has the expected structure.

Exits 0 if valid, 1 if not.
"""

import re
import sys
from pathlib import Path

MIN_JOBS = 1


def validate(path: Path) -> list[str]:
    if not path.exists():
        return [f"Post file not found: {path}"]

    text = path.read_text()

    if text.startswith("---"):
        end = text.index("---", 3)
        body = text[end + 3:].lstrip("\n")
    else:
        body = text

    errors = []

    # Must have at least one job bullet with a markdown link
    job_bullets = re.findall(r'\*\*\[.+?\]\(https?://[^)]+\)\*\*', body)
    if len(job_bullets) < MIN_JOBS:
        errors.append(f"Only {len(job_bullets)} job bullets with links (minimum {MIN_JOBS})")

    # Must have a Today's Pick section
    if "## Today's Pick" not in body:
        errors.append("Missing ## Today's Pick section")
    else:
        pick_section = body[body.index("## Today's Pick"):]
        if not re.search(r'^>', pick_section, re.MULTILINE):
            errors.append("Today's Pick section is missing a blockquote (LinkedIn message)")

    # URLs must not contain spaces
    bad_urls = re.findall(r'\]\((https?://[^)]*\s[^)]*)\)', body)
    if bad_urls:
        errors.append(f"Links with spaces in URL (hallucinated): {bad_urls[:3]}")

    return errors


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: validate_post.py <post.md>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    errors = validate(path)

    if errors:
        print(f"Post validation failed: {path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Post valid: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
