"""API сравнения PDF — serverless-функция для Vercel.

Поток для многостраничных документов разбит на шаги, чтобы укладываться
в таймаут функции и не отдавать бессмысленно огромные ответы:

1. /api/plan (или /api/plan-urls) — сопоставляет страницы двух документов
   по содержимому (а не по номеру): возвращает пары для сравнения и списки
   удалённых/добавленных листов.
2. /api/compare-batch (или /api/compare-batch-urls) — сравнивает указанную
   партию пар страниц; клиент вызывает партиями и показывает прогресс.

Одиночные вызовы /api/compare и /api/compare-urls сравнивают страницы 1:1
и остаются для простых случаев и ручной проверки.

Локальный запуск из корня репозитория:
    pip install -r requirements.txt uvicorn
    uvicorn api.index:app --port 8000
"""

import difflib as dl
import json
import time
import urllib.request

import fitz
from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
	from api.vector_compare import compare_page_vectors  #локальный запуск
	from api.supabase_auth import verify_token, SUPABASE_URL
except ImportError:
	from vector_compare import compare_page_vectors  #рантайм Vercel
	from supabase_auth import verify_token, SUPABASE_URL

app = FastAPI()

#лимит на скачивание одного файла из хранилища
MAX_DOWNLOAD = 60 * 1024 * 1024

#выше этой доли несовпавших точек страница считается «сильно изменённой»:
#рамки по ней — шум, а не информация, и не возвращаются
HEAVY_SHARE = 0.5

#потолок числа рамок на пару страниц; сверх него оставляем самые крупные
MAX_BOXES_PER_PAGE = 100

#максимум пар страниц в одной партии (страховка от таймаута)
MAX_BATCH = 12

#порог похожести страниц при сопоставлении и ширина окна поиска
MATCH_MIN_SIM = 0.5
MATCH_BAND = 10


def _check_auth(authorization):
	"""Возвращает JSONResponse с 401 либо None, если токен валиден."""
	if not authorization.startswith("Bearer "):
		return JSONResponse({"error": "Требуется авторизация"}, status_code=401)
	try:
		verify_token(authorization[7:])
	except Exception:
		return JSONResponse({"error": "Сессия недействительна — войдите заново"},
		                    status_code=401)
	return None


def _fetch_from_storage(url):
	"""Скачивает файл по подписанной ссылке нашего Supabase Storage."""
	#жёсткая проверка происхождения ссылки — API не должен скачивать
	#произвольные адреса, которые ему подсунули
	if not url.startswith(f"{SUPABASE_URL}/storage/v1/object/sign/uploads/"):
		raise ValueError("недопустимый источник файла")
	with urllib.request.urlopen(url, timeout=60) as resp:
		data = resp.read(MAX_DOWNLOAD + 1)
	if len(data) > MAX_DOWNLOAD:
		raise ValueError("файл слишком большой")
	return data


def _open_pdfs(data1, data2):
	try:
		return fitz.open(stream=data1, filetype="pdf"), fitz.open(stream=data2, filetype="pdf")
	except Exception:
		return None, None


def text_diff_boxes(page1, page2):
	"""bbox'ы слов, отличающихся между страницами (difflib по списку слов)."""
	words1 = page1.get_text("words")
	words2 = page2.get_text("words")
	list1 = [w[4] for w in words1]
	list2 = [w[4] for w in words2]
	if not list1 and not list2:
		return [], []
	diff1 = [d for d in dl.ndiff(list1, list2) if not d.startswith(("+", "?"))]
	diff2 = [d for d in dl.ndiff(list1, list2) if not d.startswith(("-", "?"))]
	boxes1 = [list(words1[i][:4]) for i, d in enumerate(diff1) if d.startswith("-")]
	boxes2 = [list(words2[i][:4]) for i, d in enumerate(diff2) if d.startswith("+")]
	return boxes1, boxes2


def _inside(text_box, graphics_boxes, min_overlap=0.5):
	"""Текстовая рамка по большей части лежит внутри одной из графических?"""
	tx0, ty0, tx1, ty1 = text_box
	area = max((tx1 - tx0) * (ty1 - ty0), 1e-6)
	for gx0, gy0, gx1, gy1 in graphics_boxes:
		ix = max(0.0, min(tx1, gx1) - max(tx0, gx0))
		iy = max(0.0, min(ty1, gy1) - max(ty0, gy0))
		if ix * iy / area >= min_overlap:
			return True
	return False


def build_plan(doc1, doc2):
	"""Сопоставляет страницы документов по текстовому содержимому.

	Похожесть — difflib по спискам слов в окне ±MATCH_BAND от диагонали;
	затем жадное связывание лучших пар. Страницы без пары — удалённые
	(в док.1) или добавленные (в док.2) листы.
	"""
	words1 = [[w[4] for w in doc1[i].get_text("words")] for i in range(doc1.page_count)]
	words2 = [[w[4] for w in doc2[j].get_text("words")] for j in range(doc2.page_count)]

	candidates = []
	for i in range(len(words1)):
		lo = max(0, i - MATCH_BAND)
		hi = min(len(words2), i + MATCH_BAND + 1)
		for j in range(lo, hi):
			sm = dl.SequenceMatcher(None, words1[i], words2[j])
			if sm.quick_ratio() < MATCH_MIN_SIM:
				continue
			ratio = sm.ratio()
			if ratio >= MATCH_MIN_SIM:
				candidates.append((ratio, i, j))

	#при равной похожести предпочитаем пару, ближайшую к диагонали
	candidates.sort(key=lambda c: (-c[0], abs(c[1] - c[2]), c[1]))
	used1, used2 = set(), set()
	pairs = []
	for ratio, i, j in candidates:
		if i in used1 or j in used2:
			continue
		used1.add(i)
		used2.add(j)
		pairs.append([i, j])
	pairs.sort()

	removed = [i for i in range(len(words1)) if i not in used1]
	added = [j for j in range(len(words2)) if j not in used2]
	return {
		"pairs": pairs,
		"removed": removed,
		"added": added,
		"numPages1": doc1.page_count,
		"numPages2": doc2.page_count,
	}


def _box_area(b):
	return (b[2] - b[0]) * (b[3] - b[1])


def compare_pairs(doc1, doc2, pairs):
	"""Сравнивает заданные пары страниц, ограничивая объём выдачи."""
	pages = []
	for i, j in pairs:
		if not (0 <= i < doc1.page_count and 0 <= j < doc2.page_count):
			continue
		p1, p2 = doc1[i], doc2[j]
		r = compare_page_vectors(p1, p2)
		heavy = r["unmatched_share"] > HEAVY_SHARE

		boxes1, boxes2, tb1, tb2 = [], [], [], []
		truncated = False
		if not heavy:
			boxes1 = [list(b) for b in r["boxes1"]]
			boxes2 = [list(b) for b in r["boxes2"]]
			tb1, tb2 = text_diff_boxes(p1, p2)
			#подписи, исчезнувшие вместе с графическим фрагментом, уже накрыты
			#его рамкой — отдельными отличиями их не считаем
			tb1 = [b for b in tb1 if not _inside(b, r["boxes1"])]
			tb2 = [b for b in tb2 if not _inside(b, r["boxes2"])]
			if len(boxes1) + len(boxes2) + len(tb1) + len(tb2) > MAX_BOXES_PER_PAGE:
				boxes1 = sorted(boxes1, key=_box_area, reverse=True)[:MAX_BOXES_PER_PAGE // 2]
				boxes2 = sorted(boxes2, key=_box_area, reverse=True)[:MAX_BOXES_PER_PAGE // 2]
				tb1, tb2 = [], []
				truncated = True

		pages.append({
			"index1": i,
			"index2": j,
			"boxes1": boxes1,
			"boxes2": boxes2,
			"textBoxes1": tb1,
			"textBoxes2": tb2,
			"width1": p1.rect.width, "height1": p1.rect.height,
			"width2": p2.rect.width, "height2": p2.rect.height,
			"unmatchedShare": round(r["unmatched_share"], 4),
			"heavilyChanged": heavy,
			"truncated": truncated,
		})
	return pages


class UrlsIn(BaseModel):
	url1: str
	url2: str


class BatchUrlsIn(BaseModel):
	url1: str
	url2: str
	pairs: list[list[int]]


def _parse_pairs(raw):
	pairs = json.loads(raw) if isinstance(raw, str) else raw
	if not isinstance(pairs, list) or len(pairs) > MAX_BATCH:
		raise ValueError("недопустимый список пар")
	return [(int(p[0]), int(p[1])) for p in pairs]


@app.post("/api/plan")
async def plan(file1: UploadFile = File(...), file2: UploadFile = File(...),
               authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	doc1, doc2 = _open_pdfs(await file1.read(), await file2.read())
	if doc1 is None:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)
	return build_plan(doc1, doc2)


@app.post("/api/plan-urls")
async def plan_urls(payload: UrlsIn, authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	try:
		data1 = _fetch_from_storage(payload.url1)
		data2 = _fetch_from_storage(payload.url2)
	except ValueError as e:
		return JSONResponse({"error": str(e)}, status_code=400)
	except Exception:
		return JSONResponse({"error": "Не удалось получить файлы из хранилища"},
		                    status_code=502)
	doc1, doc2 = _open_pdfs(data1, data2)
	if doc1 is None:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)
	return build_plan(doc1, doc2)


@app.post("/api/compare-batch")
async def compare_batch(file1: UploadFile = File(...), file2: UploadFile = File(...),
                        pairs: str = Form(...),
                        authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	try:
		pair_list = _parse_pairs(pairs)
	except Exception:
		return JSONResponse({"error": "Недопустимый список пар"}, status_code=400)
	doc1, doc2 = _open_pdfs(await file1.read(), await file2.read())
	if doc1 is None:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)
	started = time.time()
	pages = compare_pairs(doc1, doc2, pair_list)
	return {"pages": pages, "elapsed": round(time.time() - started, 2)}


@app.post("/api/compare-batch-urls")
async def compare_batch_urls(payload: BatchUrlsIn,
                             authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	try:
		pair_list = _parse_pairs(payload.pairs)
	except Exception:
		return JSONResponse({"error": "Недопустимый список пар"}, status_code=400)
	try:
		data1 = _fetch_from_storage(payload.url1)
		data2 = _fetch_from_storage(payload.url2)
	except ValueError as e:
		return JSONResponse({"error": str(e)}, status_code=400)
	except Exception:
		return JSONResponse({"error": "Не удалось получить файлы из хранилища"},
		                    status_code=502)
	doc1, doc2 = _open_pdfs(data1, data2)
	if doc1 is None:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)
	started = time.time()
	pages = compare_pairs(doc1, doc2, pair_list)
	return {"pages": pages, "elapsed": round(time.time() - started, 2)}


def _compare_documents(data1, data2):
	"""Одиночный вызов: страницы 1:1, для простых случаев и ручной проверки."""
	started = time.time()
	doc1, doc2 = _open_pdfs(data1, data2)
	if doc1 is None:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)
	pair_list = [(i, i) for i in range(min(doc1.page_count, doc2.page_count))]
	pages = compare_pairs(doc1, doc2, pair_list)
	return {
		"pages": pages,
		"numPages1": doc1.page_count,
		"numPages2": doc2.page_count,
		"elapsed": round(time.time() - started, 2),
	}


@app.post("/api/compare")
async def compare(file1: UploadFile = File(...), file2: UploadFile = File(...),
                  authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	return _compare_documents(await file1.read(), await file2.read())


@app.post("/api/compare-urls")
async def compare_urls(payload: UrlsIn, authorization: str = Header(default="")):
	denied = _check_auth(authorization)
	if denied:
		return denied
	try:
		data1 = _fetch_from_storage(payload.url1)
		data2 = _fetch_from_storage(payload.url2)
	except ValueError as e:
		return JSONResponse({"error": str(e)}, status_code=400)
	except Exception:
		return JSONResponse({"error": "Не удалось получить файлы из хранилища"},
		                    status_code=502)
	return _compare_documents(data1, data2)
