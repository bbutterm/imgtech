"""Тесты движка векторного сравнения на синтетических «CAD-подобных» PDF.

Запуск из корня репозитория:  python3 -m core.tests.run_tests

Проверяются главные риски векторного подхода:
1. та же геометрия, но другое представление (разбитые отрезки, другой
   порядок путей, круг полилинией вместо кривых Безье, джиттер координат)
   — отличий быть не должно;
2. та же геометрия с глобальным сдвигом страницы — отличий быть не должно
   (работает регистрация);
3. реальные изменения (сдвинутая линия, удалённый круг, добавленный
   прямоугольник) — каждое должно быть найдено, лишних рамок быть не должно.
"""

import math
import random
import sys
import tempfile

import fitz

from core.vector_compare import compare_page_vectors


def _circle_polyline(page, center, r, n=72, jitter=0.0, shift=(0.0, 0.0)):
	rnd = random.Random(42)
	pts = []
	for i in range(n + 1):
		a = 2 * math.pi * i / n
		x = center[0] + r * math.cos(a) + shift[0] + rnd.uniform(-jitter, jitter)
		y = center[1] + r * math.sin(a) + shift[1] + rnd.uniform(-jitter, jitter)
		pts.append(fitz.Point(x, y))
	for a, b in zip(pts, pts[1:]):
		page.draw_line(a, b)


def _split_line(page, a, b, parts, shift=(0.0, 0.0)):
	#рисует один отрезок как несколько коллинеарных кусков (другое представление)
	ax, ay = a
	bx, by = b
	knots = [(ax + (bx - ax) * i / parts + shift[0], ay + (by - ay) * i / parts + shift[1])
	         for i in range(parts + 1)]
	segments = list(zip(knots, knots[1:]))
	segments.reverse()  #и в другом порядке
	for p, q in segments:
		page.draw_line(fitz.Point(*p), fitz.Point(*q))


def _bezier_polyline(page, p0, p1, p2, p3, n=60, shift=(0.0, 0.0)):
	pts = []
	for i in range(n + 1):
		t = i / n
		mt = 1 - t
		x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
		y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
		pts.append(fitz.Point(x + shift[0], y + shift[1]))
	for a, b in zip(pts, pts[1:]):
		page.draw_line(a, b)


LINES = [((100, 100), (400, 100)), ((100, 100), (100, 300)), ((100, 300), (400, 300))]
CIRCLE_CENTER, CIRCLE_R = (600, 200), 60
BEZIER = ((150, 450), (250, 380), (350, 520), (450, 450))
FRAME = (20, 20, 822, 575)


def make_base(path):
	doc = fitz.open()
	page = doc.new_page(width=842, height=595)
	page.draw_rect(fitz.Rect(*FRAME))
	for a, b in LINES:
		page.draw_line(fitz.Point(*a), fitz.Point(*b))
	page.draw_circle(fitz.Point(*CIRCLE_CENTER), CIRCLE_R)
	page.draw_bezier(*[fitz.Point(*p) for p in BEZIER])
	doc.save(path)
	doc.close()


def make_same_geometry_other_repr(path, shift=(0.0, 0.0)):
	#та же картинка: линии разбиты на куски и нарисованы в другом порядке,
	#круг — полилиния вместо Безье, кривая — полилиния, джиттер 0.05pt
	doc = fitz.open()
	page = doc.new_page(width=842, height=595)
	_bezier_polyline(page, *BEZIER, shift=shift)
	_circle_polyline(page, CIRCLE_CENTER, CIRCLE_R, jitter=0.05, shift=shift)
	for a, b in reversed(LINES):
		_split_line(page, a, b, parts=3, shift=shift)
	x0, y0, x1, y1 = FRAME
	x0, y0, x1, y1 = x0 + shift[0], y0 + shift[1], x1 + shift[0], y1 + shift[1]
	for a, b in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
	             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
		_split_line(page, a, b, parts=2)
	doc.save(path)
	doc.close()


MOVED_LINE_OLD = LINES[0]                      #была y=100
MOVED_LINE_NEW = ((100, 115), (400, 115))      #стала y=115
ADDED_RECT = (650, 400, 750, 470)


def make_changed(path):
	#реальные изменения: первая линия сдвинута, круг удалён, добавлен прямоугольник
	doc = fitz.open()
	page = doc.new_page(width=842, height=595)
	page.draw_rect(fitz.Rect(*FRAME))
	page.draw_line(fitz.Point(*MOVED_LINE_NEW[0]), fitz.Point(*MOVED_LINE_NEW[1]))
	for a, b in LINES[1:]:
		page.draw_line(fitz.Point(*a), fitz.Point(*b))
	page.draw_bezier(*[fitz.Point(*p) for p in BEZIER])
	page.draw_rect(fitz.Rect(*ADDED_RECT))
	doc.save(path)
	doc.close()


def _box_near(box, region, tol=15.0):
	bx0, by0, bx1, by1 = box
	rx0, ry0, rx1, ry1 = region
	return not (bx1 < rx0 - tol or bx0 > rx1 + tol or by1 < ry0 - tol or by0 > ry1 + tol)


def _covered(region, boxes):
	return any(_box_near(b, region) for b in boxes)


FAILURES = []


def check(name, cond, details=""):
	status = "ok" if cond else "FAIL"
	print(f"  [{status}] {name}" + (f" — {details}" if details and not cond else ""))
	if not cond:
		FAILURES.append(name)


def main():
	tmp = tempfile.mkdtemp(prefix="veccmp_")
	base = f"{tmp}/base.pdf"
	same = f"{tmp}/same_repr.pdf"
	shifted = f"{tmp}/shifted.pdf"
	changed = f"{tmp}/changed.pdf"
	make_base(base)
	make_same_geometry_other_repr(same)
	make_same_geometry_other_repr(shifted, shift=(4.0, -3.0))
	make_changed(changed)

	d_base = fitz.open(base)
	d_same = fitz.open(same)
	d_shifted = fitz.open(shifted)
	d_changed = fitz.open(changed)

	print("1. Та же геометрия, другое представление:")
	r = compare_page_vectors(d_base[0], d_same[0])
	check("нет рамок на стороне 1", r["boxes1"] == [], str(r["boxes1"]))
	check("нет рамок на стороне 2", r["boxes2"] == [], str(r["boxes2"]))
	check("доля несовпадений < 2%", r["unmatched_share"] < 0.02,
	      f"share={r['unmatched_share']:.4f}")

	print("2. Та же геометрия, страница сдвинута на (4, -3):")
	r = compare_page_vectors(d_base[0], d_shifted[0])
	sx, sy = r["shift"]
	check("сдвиг оценён верно", abs(sx - 4.0) < 0.5 and abs(sy + 3.0) < 0.5,
	      f"shift=({sx:.2f}, {sy:.2f})")
	check("нет рамок на стороне 1", r["boxes1"] == [], str(r["boxes1"]))
	check("нет рамок на стороне 2", r["boxes2"] == [], str(r["boxes2"]))

	print("3. Реальные изменения:")
	r = compare_page_vectors(d_base[0], d_changed[0])
	old_line = (*MOVED_LINE_OLD[0], *MOVED_LINE_OLD[1])
	new_line = (*MOVED_LINE_NEW[0], *MOVED_LINE_NEW[1])
	circle = (CIRCLE_CENTER[0] - CIRCLE_R, CIRCLE_CENTER[1] - CIRCLE_R,
	          CIRCLE_CENTER[0] + CIRCLE_R, CIRCLE_CENTER[1] + CIRCLE_R)
	check("старое положение линии найдено (стр. 1)", _covered(old_line, r["boxes1"]))
	check("удалённый круг найден (стр. 1)", _covered(circle, r["boxes1"]))
	check("новое положение линии найдено (стр. 2)", _covered(new_line, r["boxes2"]))
	check("добавленный прямоугольник найден (стр. 2)", _covered(ADDED_RECT, r["boxes2"]))
	expected1 = [old_line, new_line, circle]
	expected2 = [old_line, new_line, ADDED_RECT]
	extras1 = [b for b in r["boxes1"] if not any(_box_near(b, e) for e in expected1)]
	extras2 = [b for b in r["boxes2"] if not any(_box_near(b, e) for e in expected2)]
	check("нет ложных рамок (стр. 1)", extras1 == [], str(extras1))
	check("нет ложных рамок (стр. 2)", extras2 == [], str(extras2))
	check("доля несовпадений умеренная (не fallback)", r["unmatched_share"] < 0.6,
	      f"share={r['unmatched_share']:.4f}")

	print()
	if FAILURES:
		print(f"ПРОВАЛЕНО: {len(FAILURES)} проверок: {FAILURES}")
		sys.exit(1)
	print("Все проверки пройдены.")


if __name__ == "__main__":
	main()
