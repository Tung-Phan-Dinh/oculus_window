"""Real PDF rendering and Office-derived parse/embedding page attribution."""

import base64
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import fitz
import numpy as np
import torch

import embedder
import parser
from embed_contract import EMBED_DIM, embeddings_path, is_embedded


class EmbedderRenderTest(unittest.TestCase):
    def test_oversized_spreadsheet_keeps_one_bounded_image_per_sheet(self):
        with tempfile.TemporaryDirectory(prefix="oculus-sheet-render-") as temporary:
            path = Path(temporary) / "marks.xlsx.pdf"
            with fitz.open() as document:
                document.new_page(width=3418, height=3853)
                # Include a landscape sheet: the clamp must follow each page.
                document.new_page(width=3853, height=3418)
                document.save(path)

            rendered = embedder.render_pages(str(path))
            page_numbers = []
            for page_no, image in rendered:
                page_numbers.append(page_no)
                with image:
                    # MuPDF rounds each dimension up to an integer pixel.
                    rounding = image.width + image.height + 1
                    self.assertLessEqual(
                        image.width * image.height,
                        embedder.MAX_RENDER_PIXELS + rounding,
                    )
                    self.assertGreater(image.width * image.height, 7_900_000)
                    self.assertEqual(image.mode, "RGB")
                    expected_ratio = 3418 / 3853 if page_no == 1 else 3853 / 3418
                    self.assertAlmostEqual(image.width / image.height, expected_ratio, places=3)
            self.assertEqual(page_numbers, [1, 2])

    def test_ordinary_page_pixels_are_identical_to_the_previous_renderer(self):
        with tempfile.TemporaryDirectory(prefix="oculus-a4-render-") as temporary:
            path = Path(temporary) / "notes.pdf"
            with fitz.open() as document:
                page = document.new_page(width=595, height=842)
                page.insert_text((72, 72), "Ordinary A4 lecture slide")
                page.draw_rect(fitz.Rect(80, 150, 300, 270), fill=(0.2, 0.5, 0.8))
                document.save(path)

            with fitz.open(path) as document:
                previous = document[0].get_pixmap(
                    matrix=fitz.Matrix(embedder.RENDER_DPI / 72, embedder.RENDER_DPI / 72)
                )
                expected_size = (previous.width, previous.height)
                expected_pixels = previous.samples
            images = list(embedder.render_pages(str(path)))
            self.assertEqual(len(images), 1)
            page_no, image = images[0]
            with image:
                self.assertEqual(page_no, 1)
                self.assertEqual(image.size, expected_size)
                self.assertEqual(image.tobytes(), expected_pixels)

    def test_a_lower_requested_resolution_is_never_increased(self):
        with fitz.open() as document:
            page = document.new_page(width=3418, height=3853)
            zoom = 0.1
            self.assertEqual(embedder._page_zoom(page, zoom), (zoom, zoom))
            capped, _ = embedder._page_zoom(page, embedder.RENDER_DPI / 72)
            self.assertTrue(math.isfinite(capped))
            self.assertLess(capped, embedder.RENDER_DPI / 72)

    def test_office_pdf_artifacts_join_the_correct_page_without_name_collisions(self):
        with tempfile.TemporaryDirectory(prefix="oculus Office 测试 ") as temporary:
            root = Path(temporary)
            # A real PDF and two Office originals may all share the same stem.
            for filename in ("notes.pdf", "notes.pptx.pdf", "notes.xlsx.pdf"):
                with self.subTest(filename=filename):
                    path = root / filename
                    with fitz.open() as document:
                        for number, label in enumerate(("Algebra", "Geometry"), 1):
                            page = document.new_page(width=250, height=180)
                            page.insert_text((20, 35), f"{label} page {number}")
                            color = (1, 0, 0) if number == 1 else (0, 0, 1)
                            page.draw_rect(fitz.Rect(70, 70, 180, 130), fill=color)
                        document.save(path)

                    parser.parse_fast_isolated(str(path))
                    markdown = json.loads(parser.pages_path(path).read_text(encoding="utf-8"))

                    class ImageFingerprintModel:
                        def process(self, items):
                            image = items[0]["image"]
                            r, _, b = image.getpixel((image.width // 2, image.height // 2))
                            vector = torch.zeros((1, EMBED_DIM))
                            vector[0, 0 if r > b else 1] = 1
                            return vector

                    progress = []
                    with mock.patch.object(embedder, "get_embedder", return_value=ImageFingerprintModel()):
                        result = embedder.embed_pdf(str(path), on_progress=progress.append)
                    stored_path = embeddings_path(path)
                    stored = json.loads(stored_path.read_text(encoding="utf-8"))
                    self.assertEqual(stored_path.name, f"{path.stem}.emb.json")
                    self.assertEqual(parser.pages_path(path).name, f"{path.stem}.pages.json")
                    self.assertEqual(result["page_count"], 2)
                    self.assertTrue(is_embedded(str(path)))
                    self.assertEqual([item["pages_done"] for item in progress], [1, 2])
                    self.assertTrue(all(item["total_pages"] == 2 for item in progress))
                    self.assertEqual([item["page_no"] for item in stored["pages"]], [1, 2])
                    text_by_page = {item["page_no"]: item["markdown"] for item in markdown["pages"]}
                    for record in stored["pages"]:
                        vector = np.frombuffer(base64.b64decode(record["vector"]), dtype=np.float16)
                        self.assertEqual(vector.shape, (EMBED_DIM,))
                        self.assertEqual(int(np.argmax(vector)), record["page_no"] - 1)
                        label = "Algebra" if record["page_no"] == 1 else "Geometry"
                        self.assertIn(label, text_by_page[record["page_no"]])

            self.assertEqual(len(list(root.glob("*.emb.json"))), 3)
            self.assertEqual(len(list(root.glob("*.pages.json"))), 3)


if __name__ == "__main__":
    unittest.main()
