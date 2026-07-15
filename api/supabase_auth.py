"""Проверка Supabase-токенов без секретов на сервере.

Токен пользователя проверяется запросом к /auth/v1/user самого Supabase:
валиден — вернётся профиль, нет — ошибка. Так серверу не нужны ни JWT-секрет
проекта, ни service-role-ключ; anon-ключ публичный.
"""

import json
import urllib.request

SUPABASE_URL = "https://ajltzwomvyvntvtjnrrf.supabase.co"
ANON_KEY = "sb_publishable_avfKy6YbDfLLYmjiUF7zcw_a3tLPdR6"


def verify_token(token):
	"""Возвращает профиль пользователя валидного токена, иначе бросает исключение."""
	req = urllib.request.Request(
		f"{SUPABASE_URL}/auth/v1/user",
		headers={"apikey": ANON_KEY, "Authorization": f"Bearer {token}"},
	)
	with urllib.request.urlopen(req, timeout=10) as resp:
		user = json.loads(resp.read())
	if not user.get("id"):
		raise ValueError("no user in token")
	return user
