"""Тесты совмещения листов: сдвиг, поворот, перекос.

Проверяют ровно те случаи, на которых прежняя регистрация (перебор
±8pt без поворота) разваливалась и выдавала десятки ложных рамок:
сдвиг больше диапазона перебора, поворот комплекта на 90/180/270,
перекос скана в доли градуса.

Запуск:  python3 -m core.tests.test_registration
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from core.vector_compare import compare_page_vectors

W, H = 842.0, 595.0
#отличие, которое обязано находиться при любом совмещении
DIFF_RECT = (200.0, 200.0, 260.0, 240.0)


def _draw(page, tx=0.0, ty=0.0, rot=0.0, diff=False):
	"""«CAD-подобный» лист: рамка, штамп, сетка осей, штриховка, окружности."""
	cx, cy = W / 2, H / 2

	def t(p):
		x, y = p
		if rot:
			a = math.radians(rot)
			dx, dy = x - cx, y - cy
			x = cx + dx * math.cos(a) - dy * math.sin(a)
			y = cy + dx * math.sin(a) + dy * math.cos(a)
		return fitz.Point(x + tx, y + ty)

	def line(a, b):
		page.draw_line(t(a), t(b))

	for a, b in (((20, 20), (822, 20)), ((822, 20), (822, 575)),
	             ((822, 575), (20, 575)), ((20, 575), (20, 20))):
		line(a, b)
	for yy in (500, 525, 550):
		line((550, yy), (822, yy))
	for xx in (550, 640, 730):
		line((xx, 500), (xx, 575))
	for i in range(12):
		line((60 + i * 40, 60), (60 + i * 40, 460))
	for j in range(9):
		line((60, 60 + j * 50), (500, 60 + j * 50))
	for i in range(40):
		line((560 + i * 6, 60), (560 + i * 6 + 30, 160))
	for k in range(6):
		page.draw_circle(t((600 + (k % 3) * 60, 250 + (k // 3) * 80)), 22)
	if diff:
		x0, y0, x1, y1 = DIFF_RECT
		for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
		             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
			line(a, b)


def _page(tmp, name, **kw):
	path = f"{tmp}/{name}.pdf"
	doc = fitz.open()
	_draw(doc.new_page(width=W, height=H), **kw)
	doc.save(path)
	doc.close()
	return fitz.open(path)[0]


def _covers(boxes, rect, tol=15.0):
	x0, y0, x1, y1 = rect
	return any(not (b[2] < x0 - tol or b[0] > x1 + tol or
	                b[3] < y0 - tol or b[1] > y1 + tol) for b in boxes)


class RegistrationTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tmp = tempfile.mkdtemp(prefix="registration_")
		cls.base = _page(cls.tmp, "base")

	def _assert_aligned(self, page2, name):
		r = compare_page_vectors(self.base, page2)
		self.assertLess(r["unmatched_share"], 0.02,
		                f"{name}: share={r['unmatched_share']:.3f}")
		self.assertEqual(r["boxes1"], [], f"{name}: ложные рамки на стороне 1")
		self.assertEqual(r["boxes2"], [], f"{name}: ложные рамки на стороне 2")

	def test_identical(self):
		self._assert_aligned(_page(self.tmp, "same"), "идентичные")

	def test_shift_within_old_range(self):
		self._assert_aligned(_page(self.tmp, "s5", tx=5, ty=-3), "сдвиг 5pt")

	def test_shift_beyond_old_range(self):
		self._assert_aligned(_page(self.tmp, "s20", tx=20, ty=20), "сдвиг 20pt")

	def test_shift_far(self):
		self._assert_aligned(_page(self.tmp, "s120", tx=120, ty=-60), "сдвиг 120pt")

	def test_rotated_90(self):
		self._assert_aligned(_page(self.tmp, "r90", rot=90), "поворот 90°")

	def test_rotated_180(self):
		self._assert_aligned(_page(self.tmp, "r180", rot=180), "поворот 180°")

	def test_rotated_270(self):
		self._assert_aligned(_page(self.tmp, "r270", rot=270), "поворот 270°")

	def test_skew_one_degree(self):
		self._assert_aligned(_page(self.tmp, "sk1", rot=1), "перекос 1°")

	def test_skew_two_degrees(self):
		self._assert_aligned(_page(self.tmp, "sk2", rot=2), "перекос 2°")

	def test_difference_found_without_transform(self):
		r = compare_page_vectors(self.base, _page(self.tmp, "d0", diff=True))
		self.assertTrue(_covers(r["boxes2"], DIFF_RECT), "отличие не найдено")
		self.assertEqual(r["boxes1"], [], f"лишние рамки: {r['boxes1']}")

	def test_difference_found_under_rotation(self):
		#поворот компенсируется, но реальное отличие обязано остаться видимым
		page2 = _page(self.tmp, "d90", rot=90, diff=True)
		r = compare_page_vectors(self.base, page2)
		self.assertLess(r["unmatched_share"], 0.05,
		                f"share={r['unmatched_share']:.3f}")
		self.assertTrue(r["boxes2"], "отличие на повёрнутом листе не найдено")

	def test_rotation_not_invented_on_reworked_sheet(self):
		#честно переработанный лист не должен «совмещаться» выдуманным поворотом
		doc = fitz.open()
		page = doc.new_page(width=W, height=H)
		for i in range(60):
			page.draw_line(fitz.Point(30 + i * 13, 40), fitz.Point(60 + i * 11, 540))
		doc.save(f"{self.tmp}/other.pdf")
		doc.close()
		r = compare_page_vectors(self.base, fitz.open(f"{self.tmp}/other.pdf")[0])
		self.assertGreater(r["unmatched_share"], 0.5,
		                   "переработанный лист не должен выглядеть совпавшим")


if __name__ == "__main__":
	unittest.main(verbosity=2)
