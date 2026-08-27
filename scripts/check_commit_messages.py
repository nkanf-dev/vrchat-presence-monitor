#!/usr/bin/env python3
"""Validate Conventional Commit subjects in a Git revision range."""
from __future__ import annotations

import os
import re
import subprocess
import sys


SUBJECT = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([a-z0-9](?:[a-z0-9._/-]*[a-z0-9])?\))?!?: "
    r"[^\s](?:.*[^\s])?$"
)
MAX_SUBJECT_LENGTH = 72
ZERO = "0" * 40


def subjects(revision_range: str) -> list[str]:
    if not revision_range or revision_range.startswith(f"{ZERO}.."):
        revision_range = "HEAD"
    result = subprocess.run(
        ["git", "log", "--format=%s", "--no-merges", revision_range],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def invalid_subjects(messages: list[str]) -> list[str]:
    return [
        message
        for message in messages
        if len(message) > MAX_SUBJECT_LENGTH or not SUBJECT.fullmatch(message)
    ]


def report_invalid_subjects(messages: list[str]) -> int:
    invalid = invalid_subjects(messages)
    if not invalid:
        return 0
    print("Invalid Conventional Commit subjects:", file=sys.stderr)
    for message in invalid:
        print(f"  - {message}", file=sys.stderr)
    return 1


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == "--subject-env":
        variable = arguments[1]
        if variable not in os.environ:
            print(f"Missing environment variable: {variable}", file=sys.stderr)
            return 2
        return report_invalid_subjects([os.environ[variable]])
    if len(arguments) > 1 or (arguments and arguments[0].startswith("--")):
        print("Usage: check_commit_messages.py [REVISION_RANGE]", file=sys.stderr)
        print("   or: check_commit_messages.py --subject-env VARIABLE", file=sys.stderr)
        return 2
    revision_range = arguments[0] if arguments else "HEAD^..HEAD"
    return report_invalid_subjects(subjects(revision_range))


if __name__ == "__main__":
    raise SystemExit(main())
