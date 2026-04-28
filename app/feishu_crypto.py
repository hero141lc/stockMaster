from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


def decrypt_feishu_event(encrypt_key: str, encrypt_b64: str) -> dict[str, Any]:
    """
    飞书事件订阅 Encrypt Key 解密（AES-256-CBC，密钥为 SHA256(encrypt_key)）。
    """
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypt_b64)
    iv = raw[:16]
    ciphertext = raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plain = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(plain.decode("utf-8"))
