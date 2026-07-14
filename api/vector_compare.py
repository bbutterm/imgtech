#КОПИЯ core/vector_compare.py для деплоя на Vercel: serverless-функция должна быть
#самодостаточной. Правьте core/vector_compare.py и синхронизируйте этот файл.
"""Сравнение векторной графики двух страниц PDF.

Подход: векторные примитивы обеих страниц (get_drawings) сэмплируются в
облака точек. Такое представление инвариантно к порядку путей в файле, к
разбиению отрезков на части и к способу аппроксимации кривых — то есть к
«шуму экспорта» из CAD.

Дальше:
1. оценивается глобальный сдвиг между страницами (перебор по сетке
   заполненности + субпиксельное уточнение) и страница 1 приводится к
   координатам страницы 2;
2. каждая точка одной страницы ищется в облаке другой с допуском eps —
   несопоставившиеся точки и есть отличия;
3. несопоставившиеся точки кластеризуются в прямоугольники отличий.

Если доля несопоставившихся точек аномально высокая (representations
несовместимы, а не «всё изменилось»), вызывающий код должен откатиться
на растровое сравнение — порог отдаётся в результате (unmatched_share).

Горячие места (сопоставление точек, оценка сдвига, кластеризация)
векторизованы через numpy: клетки сетки кодируются целочисленными
ключами, поиск кандидатов — searchsorted по отсортированным ключам.
"""

import numpy as np

#смещение и разрядность упаковки индексов клетки в один int64-ключ:
#key = (cx + _OFF) << 21 | (cy + _OFF); хватает на ~10^6 клеток по каждой оси
_OFF = 1 << 16


def _keys(cells):
	return ((cells[:, 0] + _OFF) << 21) + (cells[:, 1] + _OFF)


def _ragged(lo, cnt):
	"""Индексы для развёртки диапазонов [lo[i], lo[i]+cnt[i]): (номер строки, индекс)."""
	total = int(cnt.sum())
	rep = np.repeat(np.arange(len(cnt)), cnt)
	offs = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
	return rep, lo[rep] + offs


def _sample_segments_np(segs, step):
	"""Сэмплирует пакет отрезков (N, 4: x0 y0 x1 y1) с шагом step."""
	a = segs[:, 0:2]
	b = segs[:, 2:4]
	n = np.maximum(1, (np.hypot(*(b - a).T) / step).astype(np.int64))
	cnt = n + 1
	rep = np.repeat(np.arange(len(segs)), cnt)
	t = ((np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)) / n[rep])[:, None]
	return a[rep] + (b[rep] - a[rep]) * t


def _sample_beziers_np(cur, step):
	"""Сэмплирует пакет кубических Безье (N, 8) с шагом step."""
	p0, p1, p2, p3 = cur[:, 0:2], cur[:, 2:4], cur[:, 4:6], cur[:, 6:8]
	#длина ломаной контрольных точек — верхняя оценка длины кривой
	approx = (np.hypot(*(p1 - p0).T) + np.hypot(*(p2 - p1).T) + np.hypot(*(p3 - p2).T))
	n = np.maximum(2, (approx / step).astype(np.int64))
	cnt = n + 1
	rep = np.repeat(np.arange(len(cur)), cnt)
	t = ((np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)) / n[rep])[:, None]
	mt = 1 - t
	return (mt**3 * p0[rep] + 3 * mt * mt * t * p1[rep] +
	        3 * mt * t * t * p2[rep] + t**3 * p3[rep])


def extract_points(page, step=2.0):
	"""Сэмплирует всю векторную графику страницы в облако точек (N, 2).

	Используется get_cdrawings (сырые кортежи из C) — get_drawings тратит
	в несколько раз больше времени на обёртку каждого пути в Point/Rect.
	"""
	segs = []
	curves = []
	for path in page.get_cdrawings():
		for item in path["items"]:
			kind = item[0]
			if kind == "l":
				a, b = item[1], item[2]
				segs.append((a[0], a[1], b[0], b[1]))
			elif kind == "c":
				p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
				curves.append((p0[0], p0[1], p1[0], p1[1],
				               p2[0], p2[1], p3[0], p3[1]))
			elif kind == "re":
				x0, y0, x1, y1 = item[1]
				segs.append((x0, y0, x1, y0))
				segs.append((x1, y0, x1, y1))
				segs.append((x1, y1, x0, y1))
				segs.append((x0, y1, x0, y0))
			elif kind == "qu":
				q = item[1]
				#порядок углов Quad: ul, ur, ll, lr — периметр обходим как ul-ur-lr-ll
				corners = (q[0], q[1], q[3], q[2])
				for a, b in zip(corners, corners[1:] + corners[:1]):
					segs.append((a[0], a[1], b[0], b[1]))
	parts = []
	if segs:
		parts.append(_sample_segments_np(np.asarray(segs), step))
	if curves:
		parts.append(_sample_beziers_np(np.asarray(curves), step))
	if not parts:
		return np.empty((0, 2))
	return np.concatenate(parts)


def _match_mask(pts_a, pts_b, eps):
	"""Маска: для каждой точки a существует ли точка b на расстоянии <= eps.

	Клетка сетки равна eps, поэтому все кандидаты лежат в 3x3 соседних
	клетках; кандидаты находятся searchsorted'ом по ключам клеток, после
	чего проверяется точное расстояние.
	"""
	if len(pts_a) == 0:
		return np.zeros(0, dtype=bool)
	if len(pts_b) == 0:
		return np.zeros(len(pts_a), dtype=bool)

	cb = np.floor(pts_b / eps).astype(np.int64)
	order = np.argsort(_keys(cb), kind="stable")
	key_b = _keys(cb)[order]
	pts_b_sorted = pts_b[order]

	ca = np.floor(pts_a / eps).astype(np.int64)
	matched = np.zeros(len(pts_a), dtype=bool)
	eps2 = eps * eps

	for dx in (-1, 0, 1):
		for dy in (-1, 0, 1):
			todo = np.nonzero(~matched)[0]
			if len(todo) == 0:
				break
			ka = ((ca[todo, 0] + dx + _OFF) << 21) + (ca[todo, 1] + dy + _OFF)
			lo = np.searchsorted(key_b, ka, "left")
			hi = np.searchsorted(key_b, ka, "right")
			cnt = hi - lo
			has = cnt > 0
			if not has.any():
				continue
			rep, cand = _ragged(lo[has], cnt[has])
			src = todo[np.nonzero(has)[0][rep]]
			d2 = ((pts_a[src] - pts_b_sorted[cand]) ** 2).sum(axis=1)
			matched[src[d2 <= eps2]] = True
	return matched


def estimate_shift(pts_a, pts_b, search_range=8, max_samples=800):
	"""Оценка глобального сдвига a -> b.

	Медиана смещений до ближайших соседей здесь не годится: для страницы из
	длинных линий ближайший сосед лежит перпендикулярно линии, и компоненты
	сдвига вдоль преобладающего направления линий систематически занижаются.
	Вместо этого целочисленный сдвиг ищется перебором по сетке заполненности
	(максимум совпавших точек), а затем уточняется субпиксельно медианой
	остаточных смещений до ближайших соседей.
	"""
	if len(pts_a) == 0 or len(pts_b) == 0:
		return 0.0, 0.0

	#заполненность страницы 2 клетками 1pt, растянутая на соседние клетки,
	#чтобы покрыть зазоры между сэмплами (шаг сэмплирования > 1pt)
	cb = np.round(pts_b).astype(np.int64)
	d = np.array([-1, 0, 1], dtype=np.int64)
	gx = cb[:, 0, None, None] + d[None, :, None]
	gy = cb[:, 1, None, None] + d[None, None, :]
	occ = np.unique(((gx + _OFF) << 21) + (gy + _OFF))

	stride = max(1, len(pts_a) // max_samples)
	sample = pts_a[::stride]
	cs = np.round(sample).astype(np.int64)

	#все смещения одним вызовом; argmax берёт первый максимум — как и цикл
	offs = np.array([(dx, dy)
	                 for dx in range(-search_range, search_range + 1)
	                 for dy in range(-search_range, search_range + 1)], dtype=np.int64)
	ks = (((cs[None, :, 0] + offs[:, 0, None] + _OFF) << 21) +
	      (cs[None, :, 1] + offs[:, 1, None] + _OFF))
	pos = np.searchsorted(occ, ks.ravel()).clip(max=len(occ) - 1)
	scores = (occ[pos] == ks.ravel()).reshape(len(offs), -1).sum(axis=1)
	best = tuple(offs[int(np.argmax(scores))])

	#субпиксельное уточнение: локальный перебор с шагом 0.25pt, счёт — число
	#точек выборки, у которых есть сосед в радиусе 1pt. Медиана смещений до
	#«ближайшего» соседа здесь не годится: на осевых линиях ближайший сосед
	#неоднозначен (равные расстояния влево/вправо вдоль линии), и порядок
	#разрешения ничьих систематически сдвигает медиану. Перебор симметричен.
	cell = 1.0
	cb2 = np.floor(pts_b / cell).astype(np.int64)
	order = np.argsort(_keys(cb2), kind="stable")
	key_b = _keys(cb2)[order]
	pts_b_sorted = pts_b[order]

	def fine_score(off):
		shifted = sample + off
		ca = np.floor(shifted / cell).astype(np.int64)
		hit = np.zeros(len(shifted), dtype=bool)
		for dx in (-1, 0, 1):
			for dy in (-1, 0, 1):
				ka = ((ca[:, 0] + dx + _OFF) << 21) + (ca[:, 1] + dy + _OFF)
				lo = np.searchsorted(key_b, ka, "left")
				hi = np.searchsorted(key_b, ka, "right")
				cnt = hi - lo
				has = cnt > 0
				if not has.any():
					continue
				rep, cand = _ragged(lo[has], cnt[has])
				src = np.nonzero(has)[0][rep]
				d2 = ((shifted[src] - pts_b_sorted[cand]) ** 2).sum(axis=1)
				hit[src[d2 <= cell * cell]] = True
		return int(hit.sum())

	#порядок от нулевого смещения наружу: при равном счёте выигрывает
	#наименьшая поправка
	steps = np.arange(-1.25, 1.26, 0.25)
	offsets = sorted(((dx, dy) for dx in steps for dy in steps),
	                 key=lambda o: o[0] * o[0] + o[1] * o[1])
	base_off = np.asarray(best, dtype=float)
	best_fine = (0.0, 0.0)
	best_fs = -1
	for off in offsets:
		score = fine_score(base_off + np.asarray(off))
		if score > best_fs:
			best_fs = score
			best_fine = off
	return best[0] + best_fine[0], best[1] + best_fine[1]


def cluster_boxes(points, cell=10.0, min_pts=6, pad=3.0):
	"""Группирует точки в прямоугольники через связные компоненты клеток сетки."""
	if len(points) == 0:
		return []
	c = np.floor(np.asarray(points) / cell).astype(np.int64)
	keys = _keys(c)
	uniq, inverse = np.unique(keys, return_inverse=True)

	#BFS по занятым клеткам (их немного — ограничено площадью страницы)
	index_of = {int(k): i for i, k in enumerate(uniq)}
	comp = np.full(len(uniq), -1, dtype=np.int64)
	n_comp = 0
	neighbor_offsets = [(dx << 21) + dy for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
	for start in range(len(uniq)):
		if comp[start] != -1:
			continue
		comp[start] = n_comp
		stack = [int(uniq[start])]
		while stack:
			k = stack.pop()
			for off in neighbor_offsets:
				j = index_of.get(k + off)
				if j is not None and comp[j] == -1:
					comp[j] = n_comp
					stack.append(int(uniq[j]))
		n_comp += 1

	comp_pts = comp[inverse]
	counts = np.bincount(comp_pts, minlength=n_comp)

	pts = np.asarray(points)
	mins = np.full((n_comp, 2), np.inf)
	maxs = np.full((n_comp, 2), -np.inf)
	np.minimum.at(mins, comp_pts, pts)
	np.maximum.at(maxs, comp_pts, pts)

	boxes = []
	for i in range(n_comp):
		if counts[i] >= min_pts:
			boxes.append((mins[i, 0] - pad, mins[i, 1] - pad,
			              maxs[i, 0] + pad, maxs[i, 1] + pad))
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
	if register and len(pts1) and len(pts2):
		sx, sy = estimate_shift(pts1, pts2)
		pts1 = pts1 + np.array([sx, sy])

	un1 = pts1[~_match_mask(pts1, pts2, eps)]
	un2 = pts2[~_match_mask(pts2, pts1, eps)]

	total = len(pts1) + len(pts2)
	share = (len(un1) + len(un2)) / total if total else 0.0

	#координаты отличий страницы 1 возвращаем в её исходной системе
	boxes1 = cluster_boxes(un1 - np.array([sx, sy]) if len(un1) else un1,
	                       cell=cluster_cell, min_pts=cluster_min_pts)
	boxes2 = cluster_boxes(un2, cell=cluster_cell, min_pts=cluster_min_pts)

	return {
		"boxes1": boxes1,
		"boxes2": boxes2,
		"unmatched_share": share,
		"shift": (sx, sy),
		"points": (len(pts1), len(pts2)),
	}
