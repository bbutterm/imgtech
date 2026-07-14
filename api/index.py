"""API сравнения PDF — serverless-функция для Vercel.

Локальный запуск из корня репозитория:
    pip install -r requirements.txt uvicorn
    uvicorn api.index:app --port 8000
"""

import time

import fitz
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

try:
	from api.vector_compare import compare_page_vectors  #локальный запуск
except ImportError:
	from vector_compare import compare_page_vectors  #рантайм Vercel

app = FastAPI()

#выше этой доли несовпавших точек считаем, что векторные представления
#несовместимы и геометрическим рамкам доверять нельзя
FALLBACK_SHARE = 0.6


def text_diff_boxes(page1, page2):
	"""bbox'ы слов, отличающихся между страницами (difflib по списку слов)."""
	import difflib as dl
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


@app.post("/api/compare")
async def compare(file1: UploadFile = File(...), file2: UploadFile = File(...)):
	started = time.time()
	data1 = await file1.read()
	data2 = await file2.read()
	try:
		doc1 = fitz.open(stream=data1, filetype="pdf")
		doc2 = fitz.open(stream=data2, filetype="pdf")
	except Exception:
		return JSONResponse({"error": "Не удалось открыть один из файлов как PDF"},
		                    status_code=400)

	pages = []
	for i in range(min(doc1.page_count, doc2.page_count)):
		p1, p2 = doc1[i], doc2[i]
		r = compare_page_vectors(p1, p2)
		tb1, tb2 = text_diff_boxes(p1, p2)
		#подписи, исчезнувшие вместе с графическим фрагментом, уже накрыты его
		#рамкой — отдельными отличиями их не считаем
		tb1 = [b for b in tb1 if not _inside(b, r["boxes1"])]
		tb2 = [b for b in tb2 if not _inside(b, r["boxes2"])]
		pages.append({
			"index": i,
			"boxes1": [list(b) for b in r["boxes1"]],
			"boxes2": [list(b) for b in r["boxes2"]],
			"textBoxes1": tb1,
			"textBoxes2": tb2,
			"width1": p1.rect.width, "height1": p1.rect.height,
			"width2": p2.rect.width, "height2": p2.rect.height,
			"unmatchedShare": round(r["unmatched_share"], 4),
			"lowConfidence": r["unmatched_share"] > FALLBACK_SHARE,
		})

	return {
		"pages": pages,
		"numPages1": doc1.page_count,
		"numPages2": doc2.page_count,
		"elapsed": round(time.time() - started, 2),
	}
