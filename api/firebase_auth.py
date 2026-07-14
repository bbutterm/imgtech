"""Проверка Firebase ID-токенов без service-account-ключа.

Подпись токена проверяется по публичным X.509-сертификатам Google
(эндпоинт securetoken), поэтому серверу не нужны никакие секреты —
достаточно ID проекта. Сертификаты кэшируются в памяти процесса.
"""

import json
import time
import urllib.request

import jwt
from cryptography.x509 import load_pem_x509_certificate

_CERTS_URL = ("https://www.googleapis.com/robot/v1/metadata/x509/"
              "securetoken@system.gserviceaccount.com")
_cache = {"expires": 0.0, "keys": {}}


def _public_key(kid):
	now = time.time()
	if now >= _cache["expires"] or kid not in _cache["keys"]:
		with urllib.request.urlopen(_CERTS_URL, timeout=10) as resp:
			certs = json.loads(resp.read())
		_cache["keys"] = {
			k: load_pem_x509_certificate(v.encode()).public_key()
			for k, v in certs.items()
		}
		_cache["expires"] = now + 3600
	return _cache["keys"].get(kid)


def verify_id_token(token, project_id):
	"""Возвращает claims валидного токена, иначе бросает исключение."""
	header = jwt.get_unverified_header(token)
	key = _public_key(header.get("kid", ""))
	if key is None:
		raise ValueError("unknown key id")
	claims = jwt.decode(
		token, key, algorithms=["RS256"],
		audience=project_id,
		issuer=f"https://securetoken.google.com/{project_id}",
	)
	if not claims.get("sub"):
		raise ValueError("empty subject")
	return claims
