"""Manifest schema checks."""

import unittest
from typing import NotRequired, TypedDict

from nix_adapter import Conflict, validate


class Manifest(TypedDict):
    user: str
    units: list[str]
    extra: NotRequired[int]


class ManifestTests(unittest.TestCase):
    def test_matching_keys_pass_with_or_without_optional_ones(self):
        data = {"user": "a", "units": []}
        self.assertIs(validate(Manifest, data), data)
        self.assertEqual(validate(Manifest, {**data, "extra": 1}).get("extra"), 1)

    def test_missing_unknown_and_non_object_manifests_are_rejected(self):
        for data, message in (
            ({"user": "a"}, "missing units"),
            ({"user": "a", "units": [], "typo": 1}, "unknown typo"),
            ([], "not a JSON object"),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(Conflict, message):
                validate(Manifest, data)


if __name__ == "__main__":
    unittest.main()
