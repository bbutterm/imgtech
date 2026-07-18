import sys
import unittest
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.index import _empty_compare_pages, _parse_pairs, _non_text_similarity


class CompareRegressionTests(unittest.TestCase):
    def test_identical_pages_have_empty_result(self):
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 20), "same")

        result = _empty_compare_pages(doc, doc, [(0, 0)])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["boxes1"], [])
        self.assertEqual(result[0]["boxes2"], [])
        self.assertEqual(result[0]["textBoxes1"], [])
        self.assertEqual(result[0]["textBoxes2"], [])
        self.assertEqual(result[0]["unmatchedShare"], 0.0)
        self.assertFalse(result[0]["heavilyChanged"])
        self.assertFalse(result[0]["truncated"])

    def test_parse_pairs_rejects_malformed_pair(self):
        with self.assertRaises(ValueError):
            _parse_pairs([[0]])

    def test_parse_pairs_rejects_negative_index(self):
        with self.assertRaises(ValueError):
            _parse_pairs([[-1, 0]])

    def test_non_text_similarity_prefers_same_geometry(self):
        def make_page(width, height, rect):
            doc = fitz.open()
            page = doc.new_page(width=width, height=height)
            page.draw_rect(rect)
            return doc, page

        doc_a, page_a = make_page(200, 100, fitz.Rect(10, 10, 50, 50))
        doc_b, page_b = make_page(200, 100, fitz.Rect(20, 20, 60, 60))
        doc_c, page_c = make_page(400, 100, fitz.Rect(10, 10, 50, 50))
        self.assertGreater(_non_text_similarity(page_a, page_b),
                           _non_text_similarity(page_a, page_c))
        doc_a.close()
        doc_b.close()
        doc_c.close()


if __name__ == "__main__":
    unittest.main()
