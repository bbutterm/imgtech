#КОПИЯ core/vector_compare.py для деплоя на Vercel: serverless-функция должна быть
#самодостаточной. Правьте core/vector_compare.py и синхронизируйте этот файл.
"""Сравнение векторной графики двух страниц PDF.

Подход: векторные примитивы обеих страниц (get_drawings) сэмплируются в
облака точек с фиксированным шагом. Такое представление инвариантно к
порядку путей в файле, к разбиению отрезков на части и к способу
аппроксимации кривых — то есть к «шуму экспорта» из CAD.

Дальше:
1. оценивается глобальный сдвиг между страницами (медиана смещений до
   ближайших соседей) и страница 1 приводится к координатам страницы 2;
2. каждая точка одной страницы ищется в облаке другой с допуском eps —
   несопоставившиеся точки и есть отличия;
3. несопоставившиеся точки кластеризуются в прямоугольники отличий.

Если доля несопоставившихся точек аномально высокая (representations
несовместимы, а не «всё изменилось»), вызывающий код должен откатиться
на растровое сравнение — порог отдаётся в результате (unmatched_share).
"""

import math
import statistics
from collections import defaultdict


def _point(p):
	return (float(p[0]), float(p[1]))


def _sample_segment(a, b, step):
	ax, ay = a
	bx, by = b
	n = max(1, int(math.hypot(bx - ax, by - ay) / step))
	return [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n + 1)]


def _sample_bezier(p0, p1, p2, p3, step):
	#длина ломаной контрольных точек — верхняя оценка длины кривой
	approx_len = (math.hypot(p1[0] - p0[0], p1[1] - p0[1]) +
	              math.hypot(p2[0] - p1[0], p2[1] - p1[1]) +
	              math.hypot(p3[0] - p2[0], p3[1] - p2[1]))
	n = max(2, int(approx_len / step))
	pts = []
	for i in range(n + 1):
		t = i / n
		mt = 1 - t
		x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
		y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
		pts.append((x, y))
	return pts


def extract_points(page, step=2.0):
	"""Сэмплирует всю векторную графику страницы в облако точек."""
	pts = []
	for path in page.get_drawings():
		for item in path["items"]:
			kind = item[0]
			if kind == "l":
				pts += _sample_segment(_point(item[1]), _point(item[2]), step)
			elif kind == "c":
				pts += _sample_bezier(_point(item[1]), _point(item[2]),
				                      _point(item[3]), _point(item[4]), step)
			elif kind == "re":
				r = item[1]
				corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
				for a, b in zip(corners, corners[1:] + corners[:1]):
					pts += _sample_segment(a, b, step)
			elif kind == "qu":
				q = item[1]
				corners = [_point(q.ul), _point(q.ur), _point(q.lr), _point(q.ll)]
				for a, b in zip(corners, corners[1:] + corners[:1]):
					pts += _sample_segment(a, b, step)
	return pts


def _grid(points, cell):
	g = defaultdict(list)
	for p in points:
		g[(int(p[0] // cell), int(p[1] // cell))].append(p)
	return g


def _nearest_in_grid(x, y, grid, cell, max_d2):
	best_d2 = max_d2
	best = None
	cx, cy = int(x // cell), int(y // cell)
	for dx in (-1, 0, 1):
		for dy in (-1, 0, 1):
			for qx, qy in grid.get((cx + dx, cy + dy), ()):
				d2 = (qx - x) ** 2 + (qy - y) ** 2
				if d2 <= best_d2:
					best_d2 = d2
					best = (qx, qy)
	return best


def estimate_shift(pts_a, pts_b, search_range=8, max_samples=800):
	"""Оценка глобального сдвига a -> b.

	Медиана смещений до ближайших соседей здесь не годится: для страницы из
	длинных линий ближайший сосед лежит перпендикулярно линии, и компоненты
	сдвига вдоль преобладающего направления линий систематически занижаются.
	Вместо этого целочисленный сдвиг ищется перебором по сетке заполненности
	(максимум совпавших точек), а затем уточняется субпиксельно медианой
	остаточных смещений — при остатке меньше клетки перпендикулярный
	эффект уже не искажает результат.
	"""
	if not pts_a or not pts_b:
		return 0.0, 0.0

	#заполненность страницы 2 клетками 1pt, растянутая на соседние клетки,
	#чтобы покрыть зазоры между сэмплами (шаг сэмплирования > 1pt)
	occ = set()
	for x, y in pts_b:
		cx, cy = int(round(x)), int(round(y))
		for dx in (-1, 0, 1):
			for dy in (-1, 0, 1):
				occ.add((cx + dx, cy + dy))

	stride = max(1, len(pts_a) // max_samples)
	sample = pts_a[::stride]

	best = (0, 0)
	best_score = -1
	for dx in range(-search_range, search_range + 1):
		for dy in range(-search_range, search_range + 1):
			score = sum(1 for x, y in sample
			            if (int(round(x + dx)), int(round(y + dy))) in occ)
			if score > best_score:
				best_score = score
				best = (dx, dy)

	#субпиксельное уточнение
	grid_b = _grid(pts_b, 2.0)
	dxs, dys = [], []
	for x, y in sample:
		nb = _nearest_in_grid(x + best[0], y + best[1], grid_b, 2.0, 4.0)
		if nb is not None:
			dxs.append(nb[0] - (x + best[0]))
			dys.append(nb[1] - (y + best[1]))
	if dxs:
		return best[0] + statistics.median(dxs), best[1] + statistics.median(dys)
	return float(best[0]), float(best[1])


def _unmatched(points, other_grid, eps):
	out = []
	eps2 = eps * eps
	for x, y in points:
		if _nearest_in_grid(x, y, other_grid, eps, eps2) is None:
			out.append((x, y))
	return out


def cluster_boxes(points, cell=10.0, min_pts=6, pad=3.0):
	"""Группирует точки в прямоугольники через связные компоненты клеток сетки."""
	occ = _grid(points, cell)
	seen = set()
	boxes = []
	for start in occ:
		if start in seen:
			continue
		seen.add(start)
		stack = [start]
		comp = []
		while stack:
			c = stack.pop()
			comp += occ[c]
			for dx in (-1, 0, 1):
				for dy in (-1, 0, 1):
					nb = (c[0] + dx, c[1] + dy)
					if nb in occ and nb not in seen:
						seen.add(nb)
						stack.append(nb)
		if len(comp) >= min_pts:
			xs = [p[0] for p in comp]
			ys = [p[1] for p in comp]
			boxes.append((min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad))
	return boxes


def compare_page_vectors(page1, page2, eps=1.5, step=2.0, register=True,
                         cluster_cell=10.0, cluster_min_pts=6):
	"""Сравнивает векторную графику двух страниц.

	Возвращает словарь:
	  boxes1 — прямоугольники (x0, y0, x1, y1) отличий в координатах страницы 1
	  boxes2 — то же для страницы 2
	  unmatched_share — доля несопоставившихся точек; если она высока
	      (например > 0.6), представления несовместимы и нужен растровый fallback
	  shift — оценённый глобальный сдвиг страницы 1 относительно страницы 2
	  points — размеры облаков точек (диагностика)
	"""
	pts1 = extract_points(page1, step)
	pts2 = extract_points(page2, step)

	sx = sy = 0.0
	if register and pts1 and pts2:
		sx, sy = estimate_shift(pts1, pts2)
		pts1 = [(x + sx, y + sy) for x, y in pts1]

	grid1 = _grid(pts1, eps)
	grid2 = _grid(pts2, eps)
	un1 = _unmatched(pts1, grid2, eps)
	un2 = _unmatched(pts2, grid1, eps)

	total = len(pts1) + len(pts2)
	share = (len(un1) + len(un2)) / total if total else 0.0

	#координаты отличий страницы 1 возвращаем в её исходной системе
	boxes1 = cluster_boxes([(x - sx, y - sy) for x, y in un1],
	                       cell=cluster_cell, min_pts=cluster_min_pts)
	boxes2 = cluster_boxes(un2, cell=cluster_cell, min_pts=cluster_min_pts)

	return {
		"boxes1": boxes1,
		"boxes2": boxes2,
		"unmatched_share": share,
		"shift": (sx, sy),
		"points": (len(pts1), len(pts2)),
	}
