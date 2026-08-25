import sys
import unittest
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.index import (_empty_compare_pages, _parse_pairs, _non_text_similarity,
                       _to_page_coords, compare_pairs)


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
        self.assertTrue(result[0]["comparable"])
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


class RotatedPageTests(unittest.TestCase):
    """Рамки на листе с /Rotate должны попадать в отображаемую страницу.

    get_cdrawings и get_text отдают координаты без учёта поворота, а
    width/height уходят клиенту из page.rect — то есть уже с учётом.
    Без перевода координат рамка уезжает мимо отличия или за край листа.
    """

    @staticmethod
    def _doc(rotation=0, extra=False):
        doc = fitz.open()
        page = doc.new_page(width=800, height=400)
        for i in range(30):
            page.draw_line(fitz.Point(40 + i * 25, 40), fitz.Point(60 + i * 24, 360))
        if extra:
            page.draw_rect(fitz.Rect(60, 60, 140, 120))
        if rotation:
            page.set_rotation(rotation)
        return doc

    def test_boxes_stay_inside_rotated_page(self):
        doc1 = self._doc()
        doc2 = self._doc(rotation=90, extra=True)
        page2 = doc2[0]

        pages = compare_pairs(doc1, doc2, [(0, 0)])
        boxes = pages[0]["boxes2"]

        self.assertTrue(boxes, "отличие на повёрнутом листе не найдено")
        self.assertEqual(pages[0]["width2"], page2.rect.width)
        for x0, y0, x1, y1 in boxes:
            self.assertGreaterEqual(x0, -1)
            self.assertGreaterEqual(y0, -1)
            self.assertLessEqual(x1, page2.rect.width + 1,
                                 "рамка вышла за ширину повёрнутого листа")
            self.assertLessEqual(y1, page2.rect.height + 1,
                                 "рамка вышла за высоту повёрнутого листа")

    def test_unrotated_boxes_untouched(self):
        doc = self._doc()
        boxes = [[10.0, 20.0, 30.0, 40.0]]
        self.assertEqual(_to_page_coords(boxes, doc[0]), boxes)


class UncomparablePairTests(unittest.TestCase):
    def test_completely_different_sheets_are_marked_uncomparable(self):
        """Несопоставимые листы должны честно помечаться, а не выдавать шум."""
        doc1 = fitz.open()
        page1 = doc1.new_page(width=400, height=400)
        for i in range(40):
            page1.draw_line(fitz.Point(10, 10 + i * 9), fitz.Point(390, 10 + i * 9))

        doc2 = fitz.open()
        page2 = doc2.new_page(width=400, height=400)
        for i in range(40):
            page2.draw_circle(fitz.Point(30 + (i % 8) * 45, 30 + (i // 8) * 45), 18)

        page = compare_pairs(doc1, doc2, [(0, 0)])[0]
        self.assertGreater(page["unmatchedShare"], 0.6)
        self.assertFalse(page["comparable"])


if __name__ == "__main__":
    unittest.main()
