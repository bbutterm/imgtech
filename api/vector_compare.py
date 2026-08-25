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

import math

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


def _build_index(pts_b, eps):
	"""Сортированный индекс клеток облака b — переиспользуется при переборе."""
	cb = np.floor(pts_b / eps).astype(np.int64)
	order = np.argsort(_keys(cb), kind="stable")
	return _keys(cb)[order], pts_b[order]


def _match_mask(pts_a, pts_b, eps, index=None):
	"""Маска: для каждой точки a существует ли точка b на расстоянии <= eps.

	Клетка сетки равна eps, поэтому все кандидаты лежат в 3x3 соседних
	клетках; кандидаты находятся searchsorted'ом по ключам клеток, после
	чего проверяется точное расстояние. Готовый индекс b можно передать
	в index — при переборе вариантов совмещения он строится один раз.
	"""
	if len(pts_a) == 0:
		return np.zeros(0, dtype=bool)
	if len(pts_b) == 0:
		return np.zeros(len(pts_a), dtype=bool)

	key_b, pts_b_sorted = index if index is not None else _build_index(pts_b, eps)

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


def _nearest(pts_a, key_b, pts_b_sorted, cell, tol=None):
	"""Индекс ближайшей точки b для каждой точки a (или -1, если дальше tol).

	Отличается от _match_mask тем, что возвращает саму пару, а не факт
	совпадения: по парам точек оценивается преобразование между листами.
	Индекс b построен с клеткой cell, а искать можно с допуском tol больше
	клетки — тогда просматривается соответственно больше соседних клеток.
	"""
	best = np.full(len(pts_a), -1, dtype=np.int64)
	if len(pts_a) == 0 or len(pts_b_sorted) == 0:
		return best
	if tol is None:
		tol = cell

	reach = int(np.ceil(tol / cell))
	ca = np.floor(pts_a / cell).astype(np.int64)
	srcs, cands, dists = [], [], []
	for dx in range(-reach, reach + 1):
		for dy in range(-reach, reach + 1):
			ka = ((ca[:, 0] + dx + _OFF) << 21) + (ca[:, 1] + dy + _OFF)
			lo = np.searchsorted(key_b, ka, "left")
			hi = np.searchsorted(key_b, ka, "right")
			cnt = hi - lo
			has = cnt > 0
			if not has.any():
				continue
			rep, cand = _ragged(lo[has], cnt[has])
			src = np.nonzero(has)[0][rep]
			srcs.append(src)
			cands.append(cand)
			dists.append(((pts_a[src] - pts_b_sorted[cand]) ** 2).sum(axis=1))
	if not srcs:
		return best

	src = np.concatenate(srcs)
	cand = np.concatenate(cands)
	d2 = np.concatenate(dists)
	keep = d2 <= tol * tol
	src, cand, d2 = src[keep], cand[keep], d2[keep]
	if not len(src):
		return best
	#из нескольких кандидатов на точку берём ближайшего: сортировка по
	#(точка, расстояние) и первый элемент каждой группы
	order = np.lexsort((d2, src))
	src, cand = src[order], cand[order]
	first = np.unique(src, return_index=True)[1]
	best[src[first]] = cand[first]
	return best


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


#--- совмещение листов с поворотом -------------------------------------------
#estimate_shift ищет только параллельный перенос и только в пределах ±8pt.
#Реальные комплекты дают и больший сдвиг, и поворот листа (90/180/270 при
#пересборке комплекта, доли градуса — при перекосе скана). Для них грубое
#смещение оценивается кросс-корреляцией сеток заполненности через ФФТ
#(диапазон не ограничен), при необходимости — с перебором угла, а результат
#доводится estimate_shift.

#размер сетки ФФТ: клетка подбирается под размер листа, сетка фиксирована
_GRID_N = 1024
#точек, по которым считается качество совмещения варианта
_SCORE_SAMPLES = 4000
#точек, по которым считается кросс-корреляция при переборе углов
_SCAN_SAMPLES = 60000
#сколько пиков корреляции проверять: на чертеже с регулярной сеткой осей или
#штриховкой наибольший пик часто приходится на кратный шаг сетки, а не на
#истинное смещение, поэтому кандидаты сверяются по числу совпавших точек
_FFT_PEAKS = 6
#доля совпавших точек, выше которой лист считается совмещённым и перебор
#вариантов не запускается вовсе (обычный случай — ни сдвига, ни поворота)
_ALIGNED_SHARE = 0.9
#углы-кандидаты: перекос скана (доли градуса) и повороты комплекта.
#Точное значение угла ICP находит сам, сетке достаточно попасть в его
#область сходимости — примерно ±0.5°
_SEED_ANGLES = tuple(round(x * 0.5, 1) for x in range(-6, 7)) + (90.0, 180.0, 270.0)
#сколько лучших углов и сколько пиков сдвига для каждого доводить ICP
_SEED_TRIALS = 4
_START_TRIALS = 2
#меньший угол считаем нулевым: это шум подгонки, а не поворот листа
_MIN_ANGLE = 0.01
#поворот принимается, только если даёт заметно больше совпадений, чем вариант
#без поворота: иначе на честно переработанном листе можно «поймать»
#несуществующий поворот и спрятать за ним настоящие отличия
_ANGLE_GAIN = 1.05


def rotate_points(pts, deg, center):
	"""Поворот облака точек вокруг center на deg градусов."""
	if not deg:
		return pts
	a = np.radians(deg)
	ca, sa = np.cos(a), np.sin(a)
	d = pts - center
	return np.column_stack((center[0] + d[:, 0] * ca - d[:, 1] * sa,
	                        center[1] + d[:, 0] * sa + d[:, 1] * ca))


def _grid_params(pts_a, pts_b):
	"""Начало координат и размер клетки сетки, общие для обоих облаков."""
	lo = np.minimum(pts_a.min(axis=0), pts_b.min(axis=0))
	hi = np.maximum(pts_a.max(axis=0), pts_b.max(axis=0))
	#запас 1.6 расширяет поле так, чтобы сдвиг не заворачивался по кругу
	cell = max(float((hi - lo).max()) * 1.6 / _GRID_N, 0.5)
	return lo, cell


def _occupancy(pts, origin, cell):
	g = np.zeros((_GRID_N, _GRID_N), dtype=np.float32)
	idx = ((pts - origin) / cell).astype(np.int64)
	np.clip(idx, 0, _GRID_N - 1, out=idx)
	g[idx[:, 0], idx[:, 1]] = 1.0
	return g


def _fft_offsets(pts_a, origin, cell, spectrum_b, peaks=_FFT_PEAKS):
	"""Кандидаты грубого сдвига a -> b по пикам кросс-корреляции.

	Возвращает список (dx, dy, высота пика). Диапазон ограничен только
	размером сетки, а не окном перебора, поэтому отрабатываются переносы в
	сотни пунктов и повороты листа. Пиков берётся несколько: на чертеже с
	регулярной сеткой осей или штриховкой наибольший из них часто приходится
	на кратный шаг сетки, а не на истинное смещение.
	"""
	fa = np.fft.rfft2(_occupancy(pts_a, origin, cell))
	cc = np.fft.irfft2(spectrum_b * np.conj(fa), s=(_GRID_N, _GRID_N)).ravel()
	top = np.argpartition(cc, -peaks)[-peaks:]
	out = []
	for flat in top[np.argsort(cc[top])[::-1]]:
		kx, ky = divmod(int(flat), _GRID_N)
		dx = kx if kx <= _GRID_N // 2 else kx - _GRID_N
		dy = ky if ky <= _GRID_N // 2 else ky - _GRID_N
		out.append((dx * cell, dy * cell, float(cc[flat])))
	return out


def _sub(pts, limit):
	return pts[::max(1, len(pts) // limit)] if len(pts) > limit else pts


def _fit_rigid(src, dst):
	"""Поворот и перенос, совмещающие пары точек src -> dst (2D Прокруст)."""
	cs, cd = src.mean(axis=0), dst.mean(axis=0)
	a, b = src - cs, dst - cd
	num = float((a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum())
	den = float((a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1]).sum())
	return math.degrees(math.atan2(num, den)), cs, cd


def _icp(pts, pts2, index, eps, angle, shift, center, iters=6):
	"""Уточняет (угол, сдвиг) по совпавшим парам точек: (угол, сдвиг, счёт).

	Перебор угла сеткой здесь не годится: шаг 0.25° на листе A1 — это
	несколько пунктов на краю, то есть больше eps, а более мелкий шаг
	превращает перебор в десятки лишних проходов. Вместо этого поворот
	считается сразу по парам точек, а допуск поиска пар сжимается от
	грубого к точному.

	Возвращается лучшая итерация, а не последняя: на чертеже с регулярной
	штриховкой пары могут «съехать» на соседнюю линию, и тогда следующий шаг
	уводит совмещение в сторону. С отбором по счёту результат не может
	оказаться хуже стартового приближения.
	"""
	key_b, pts_b_sorted = index
	moved = rotate_points(pts, angle, center) + np.asarray(shift)
	rotated = rotate_points(pts, angle, center)

	def commit(cur, ang):
		return ang, tuple((cur - rotate_points(pts, ang, center)).mean(axis=0)), \
		       int(_match_mask(cur, pts2, eps, index).sum())

	best = commit(moved, angle)
	for k in range(iters):
		#допуск от 4*eps к eps: шире — и пары цепляются за соседнюю линию
		tol = eps * max(1.0, 4.0 / (2 ** k))
		j = _nearest(moved, key_b, pts_b_sorted, eps, tol)
		hit = j >= 0
		if hit.sum() < 20:
			break
		d_angle, cs, cd = _fit_rigid(moved[hit], pts_b_sorted[j[hit]])
		moved = rotate_points(moved, d_angle, cs) + (cd - cs)
		angle += d_angle
		cand = commit(moved, angle)
		if cand[2] > best[2]:
			best = cand
	return best


def estimate_transform(pts1, pts2, eps=1.5):
	"""Оценивает поворот и перенос pts1 -> pts2: (угол в градусах, (dx, dy)).

	Порядок: сначала прежнее поведение (estimate_shift, только перенос и
	только ±8pt) — на обычном листе им всё и заканчивается. Если лист так не
	совмещается, перебираются углы (перекос скана и повороты комплекта), для
	каждого — сдвиг по пикам кросс-корреляции, лучшие варианты доводятся ICP.

	Итоговый вариант принимается, только если он строго лучше прежнего,
	поэтому совмещение не может стать хуже, чем было до появления перебора.
	"""
	if len(pts1) == 0 or len(pts2) == 0:
		return 0.0, (0.0, 0.0)

	index = _build_index(pts2, eps)
	probe = _sub(pts1, _SCORE_SAMPLES)
	center = pts1.mean(axis=0)
	aligned = _ALIGNED_SHARE * len(probe)

	def score(angle, shift):
		moved = rotate_points(probe, angle, center) + np.asarray(shift)
		return int(_match_mask(moved, pts2, eps, index).sum())

	zero_shift = estimate_shift(pts1, pts2)
	zero_score = score(0.0, zero_shift)
	if zero_score >= aligned:
		return 0.0, zero_shift

	origin, cell = _grid_params(pts1, pts2)
	scan1 = _sub(pts1, _SCAN_SAMPLES)
	spectrum_b = np.fft.rfft2(_occupancy(pts2, origin, cell))

	#грубый отбор углов: высота пика корреляции — дешёвая мера похожести,
	#её хватает, чтобы отсеять заведомо неподходящие углы без ICP
	seeds = []
	for angle in _SEED_ANGLES:
		offsets = _fft_offsets(rotate_points(scan1, angle, center),
		                       origin, cell, spectrum_b)
		seeds.append((offsets[0][2], angle, offsets))
	seeds.sort(key=lambda seed: (-seed[0], abs(seed[1])))

	best_angle, best_shift, best_score = 0.0, zero_shift, zero_score
	for _, angle, offsets in seeds[:_SEED_TRIALS]:
		starts = [(dx, dy) for dx, dy, _ in offsets[:_START_TRIALS]]
		if angle == 0.0:
			starts.insert(0, zero_shift)
		for start in starts:
			cand_angle, cand_shift, s = _icp(probe, pts2, index, eps,
			                                 angle, start, center)
			if s > best_score:
				best_angle, best_shift, best_score = cand_angle, cand_shift, s
		if best_score >= aligned and abs(best_angle) < _MIN_ANGLE:
			return best_angle, best_shift

	#поворот меньше сотой доли градуса — это шум подгонки, а не поворот листа
	if abs(best_angle) < _MIN_ANGLE:
		return 0.0, best_shift
	#поворот принимается, только если он заметно лучше варианта без поворота
	if best_score <= zero_score * _ANGLE_GAIN:
		return 0.0, zero_shift
	return best_angle, best_shift


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
	  shift — оценённый перенос страницы 1 относительно страницы 2
	  angle — оценённый поворот страницы 1 относительно страницы 2, градусы
	  points — размеры облаков точек (диагностика)
	"""
	pts1 = extract_points(page1, step)
	pts2 = extract_points(page2, step)

	angle = 0.0
	sx = sy = 0.0
	center = pts1.mean(axis=0) if len(pts1) else np.zeros(2)
	if register and len(pts1) and len(pts2):
		angle, (sx, sy) = estimate_transform(pts1, pts2, eps)
		pts1 = rotate_points(pts1, angle, center) + np.array([sx, sy])

	un1 = pts1[~_match_mask(pts1, pts2, eps)]
	un2 = pts2[~_match_mask(pts2, pts1, eps)]

	total = len(pts1) + len(pts2)
	share = (len(un1) + len(un2)) / total if total else 0.0

	#координаты отличий страницы 1 возвращаем в её исходной системе
	if len(un1):
		un1 = rotate_points(un1 - np.array([sx, sy]), -angle, center)
	boxes1 = cluster_boxes(un1, cell=cluster_cell, min_pts=cluster_min_pts)
	boxes2 = cluster_boxes(un2, cell=cluster_cell, min_pts=cluster_min_pts)

	return {
		"boxes1": boxes1,
		"boxes2": boxes2,
		"unmatched_share": share,
		"shift": (sx, sy),
		"angle": angle,
		"points": (len(pts1), len(pts2)),
	}
