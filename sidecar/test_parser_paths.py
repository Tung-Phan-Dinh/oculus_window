"""Real fast parsing through Unicode/spaced paths and image staging."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import fitz
from PIL import Image

import parser


class ParserPathsTest(unittest.TestCase):
    def make_pdf(self, root):
        path = root / "讲义 (week 1).pdf"
        image = root / "chart.png"
        Image.new("RGB", (300, 200), "green").save(image)
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Page one: vectors and matrices")
            page.insert_image(fitz.Rect(72, 110, 372, 310), filename=str(image))
            pdf.new_page().insert_text((72, 72), "Page two: integrals")
            pdf.save(path)
        return path

    def assert_artifacts(self, path, result):
        self.assertEqual(result["page_count"], 2)
        record = json.loads(parser.pages_path(path).read_text(encoding="utf-8"))
        self.assertEqual([page["page_no"] for page in record["pages"]], [1, 2])
        self.assertIn("vectors", record["pages"][0]["markdown"])
        self.assertIn("integrals", record["pages"][1]["markdown"])
        self.assertEqual(record["mode"], "fast")
        self.assertTrue(any(parser.images_dir_for(path).iterdir()))
        self.assertEqual(parser.parse_mode(path), "fast")

    def test_isolated_parse_unicode_path_and_page_attribution(self):
        with tempfile.TemporaryDirectory(prefix="oculus 测试 (files) ") as temporary:
            path = self.make_pdf(Path(temporary))
            self.assert_artifacts(path, parser.parse_fast_isolated(str(path)))

    def test_spaced_scratch_without_short_paths(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="oculus unsafe (temp) ") as temporary:
            path = self.make_pdf(Path(temporary))
            with mock.patch.object(parser, "_scratch_root", return_value=temporary):
                self.assert_artifacts(path, parser.parse_fast(str(path)))
        self.assertEqual(os.getcwd(), original_cwd)


if __name__ == "__main__":
    unittest.main()
