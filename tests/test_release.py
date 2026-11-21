#!/usr/bin/env python3
import os
import re
import unittest

import test_align


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP_DIRS = {".git", "__pycache__", "dist", ".screenshot-home"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".wav", ".mp3", ".pyc"}


class ReleaseMetadataTest(unittest.TestCase):
    def test_version_strings_match_version_file(self):
        with open(os.path.join(ROOT, "VERSION")) as version_file:
            version = version_file.read().strip()

        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")

        with open(os.path.join(ROOT, "packaging", "changelog")) as changelog_file:
            changelog = changelog_file.read()

        self.assertTrue(changelog.startswith("atis-relay-host (%s)" % version))

        with open(os.path.join(ROOT, "README.md")) as readme_file:
            readme = readme_file.read()

        named = re.findall(r"atis-relay-host_([0-9.]+)_all\.deb", readme)
        self.assertGreaterEqual(len(named), 1)
        self.assertTrue(all(item == version for item in named))

        with open(os.path.join(ROOT, "packaging", "nfpm.yaml")) as nfpm_file:
            nfpm = nfpm_file.read()

        self.assertIn("version: ${VERSION}", nfpm)


class SourceHygieneTest(unittest.TestCase):
    def test_align_cli_takes_pairs_from_argv(self):
        self.assertIsNone(test_align.parse_pairs([]))
        self.assertIsNone(test_align.parse_pairs(["SAME", "a.mp3"]))
        self.assertEqual(
            test_align.parse_pairs(["SAME", "a.mp3", "b.mp3"]),
            [("SAME", "a.mp3", "b.mp3")],
        )

    def test_tree_has_no_personal_home_paths_or_gmail(self):
        home_needle = os.path.join(os.sep, "Users", "")
        mail_needle = "@" + "gmail.com"
        hits = []

        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]

            for name in filenames:
                path = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()

                if ext in SKIP_SUFFIXES:
                    continue

                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        text = handle.read()

                except (OSError, UnicodeDecodeError):
                    continue

                rel = os.path.relpath(path, ROOT)

                if home_needle in text or mail_needle in text:
                    hits.append(rel)

        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
