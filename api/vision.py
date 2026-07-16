"""Сравнение изображений через мультимодальную LLM (Qwen-VL).

Ключ QWEN_API_KEY живёт ТОЛЬКО в переменных окружения Vercel — это первый
настоящий секрет проекта, в репозиторий и на фронт он попадать не должен.
Эндпоинт OpenAI-совместимый; модель и адрес меняются через env без правок кода.
"""

import base64
import json
import os
import urllib.request

QWEN_BASE_URL = os.environ.get(
	"QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-vl-plus")

PROMPT = (
	"Перед тобой две версии одного изображения (интерьерная визуализация, "
	"рендер или чертёж): первое изображение — версия 1, второе — версия 2. "
	"Найди предметные отличия версии 2 от версии 1: что добавлено, удалено "
	"или изменено (мебель, конструкции, отделка, оборудование, расстановка). "
	"Игнорируй шум рендера, артефакты сжатия и мелкие отличия освещения, "
	"если они не вызваны изменением объектов. Если ракурсы камер различаются "
	"— сравнивай состав сцены, а не положение пикселей. "
	"Для каждого отличия укажи, где оно расположено на каждой версии: "
	"прямоугольник [x0, y0, x1, y1] в тысячных долях ширины и высоты "
	"соответствующего изображения (числа 0–1000). Если объект есть только "
	"в одной версии, для другой укажи область, где он был/должен был быть; "
	"если место указать нельзя — null. "
	"Ответь строго JSON без пояснений: "
	'{"summary": "одно предложение об общем характере изменений", '
	'"differences": [{"what": "краткое описание", '
	'"box1": [x0, y0, x1, y1], "box2": [x0, y0, x1, y1]}]}. '
	"Если предметных отличий нет — differences: []."
)


def compare_images(images):
	"""images: список из двух элементов {"url": …} или {"b64": …, "mime": …}."""
	key = os.environ.get("QWEN_API_KEY")
	if not key:
		raise RuntimeError("Режим изображений не настроен: нет QWEN_API_KEY")

	content = [{"type": "text", "text": PROMPT}]
	for im in images:
		url = im.get("url") or f"data:{im['mime']};base64,{im['b64']}"
		content.append({"type": "image_url", "image_url": {"url": url}})

	payload = {
		"model": QWEN_MODEL,
		"messages": [{"role": "user", "content": content}],
		"temperature": 0.2,
	}
	req = urllib.request.Request(
		f"{QWEN_BASE_URL}/chat/completions",
		data=json.dumps(payload).encode(),
		headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
	)
	try:
		with urllib.request.urlopen(req, timeout=120) as resp:
			out = json.loads(resp.read())
	except urllib.error.HTTPError as e:
		#диагностика настройки: 401 — ключ, 404 — модель/URL, 429 — лимиты
		hint = {401: "провайдер отклонил ключ (проверьте QWEN_API_KEY)",
		        403: "доступ запрещён (проверьте ключ и площадку)",
		        404: "модель или адрес не найдены (проверьте QWEN_MODEL/QWEN_BASE_URL)",
		        429: "исчерпан лимит запросов у провайдера"}
		raise RuntimeError(hint.get(e.code, f"ошибка провайдера модели ({e.code})"))
	return _parse(out["choices"][0]["message"]["content"])


def _norm_box(b):
	"""Валидный прямоугольник в тысячных долях (0–1000) либо None."""
	try:
		x0, y0, x1, y1 = (max(0.0, min(1000.0, float(v))) for v in b)
	except Exception:
		return None
	if x1 - x0 < 5 or y1 - y0 < 5:
		return None
	return [x0, y0, x1, y1]


def _norm_diff(item):
	if isinstance(item, str):
		return {"what": item, "box1": None, "box2": None}
	if isinstance(item, dict):
		what = str(item.get("what") or item.get("label") or
		           " — ".join(str(v) for k, v in item.items()
		                      if k not in ("box1", "box2") and v))
		return {"what": what,
		        "box1": _norm_box(item.get("box1")),
		        "box2": _norm_box(item.get("box2"))}
	return {"what": str(item), "box1": None, "box2": None}


def _parse(text):
	"""Модель просят вернуть JSON, но страхуемся от оград ```json и болтовни."""
	start = text.find("{")
	end = text.rfind("}")
	if start != -1 and end > start:
		try:
			data = json.loads(text[start:end + 1])
			return {
				"summary": str(data.get("summary", "")),
				"differences": [_norm_diff(d) for d in data.get("differences", [])][:30],
			}
		except Exception:
			pass
	lines = [l.strip("-•*# \t") for l in text.splitlines() if l.strip()]
	return {"summary": "", "differences": [_norm_diff(l) for l in lines][:30]}
