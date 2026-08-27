from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from scripts.check_commit_messages import invalid_subjects, main


class CommitMessageTests(unittest.TestCase):
    def test_accepts_conventional_subjects(self):
        self.assertEqual(
            invalid_subjects(
                [
                    "feat: add hosted viewer",
                    "fix(api)!: reject cross-tenant import",
                    "ci(release/v2): attest immutable image",
                ]
            ),
            [],
        )

    def test_accepts_subject_at_length_limit(self):
        message = "docs: " + "x" * 66
        self.assertEqual(len(message), 72)
        self.assertEqual(invalid_subjects([message]), [])

    def test_rejects_unscoped_prose_and_overlong_subjects(self):
        self.assertEqual(invalid_subjects(["update things"]), ["update things"])
        message = "docs: " + "x" * 67
        self.assertEqual(len(message), 73)
        self.assertEqual(invalid_subjects([message]), [message])

    def test_rejects_malformed_scope_and_whitespace(self):
        messages = ["fix(API): reject request", "fix(api/): reject request", "fix: trailing "]
        self.assertEqual(invalid_subjects(messages), messages)

    def test_subject_environment_mode_reports_invalid_title(self):
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"PR_TITLE": "update everything"}),
            mock.patch.object(sys, "argv", ["check_commit_messages.py", "--subject-env", "PR_TITLE"]),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)
        self.assertIn("update everything", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
