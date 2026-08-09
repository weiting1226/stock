"""把錯誤訊息中的憑證遮蔽掉。

Finnhub 之類的 API 把金鑰放在 query string，requests 的 HTTPError 訊息會包含
完整 URL，直接寫進報告就等於把金鑰 commit 進公開 repo（2026-08 實際發生過）。
任何來自外部呼叫的例外訊息，一律先經過這裡才可以進入輸出或日誌。
"""
from __future__ import annotations

import re

# 常見的憑證參數名稱：token / apikey / api_key / key / apiKey / secret
_QUERY_SECRET_RE = re.compile(
    r"((?:token|api_?key|key|secret|password)=)[^&\s\"\']+",
    re.IGNORECASE,
)
# Bearer / Basic 授權標頭。
#
# 這裡要求 20 個字元以上，是因為原本只寫 `+` 會把普通英文吃掉：類股名稱
# 「Basic Materials」曾被改寫成「Basic ***REDACTED***」寫進資料檔。遮蔽做過頭
# 一樣是損壞資料，只是壞得比較安靜——外洩看得出來，被吃掉的字沒人會發現。
# 真正的 bearer token 遠長於 20 字元，因此這個門檻不會放過真的憑證；
# 另外接受帶有 Authorization 前綴的形式，那種情境即使值很短也一定是憑證。
_AUTH_HEADER_RE = re.compile(
    r"((?:bearer|basic)\s+)[A-Za-z0-9._\-+/=]{20,}",
    re.IGNORECASE,
)
_AUTH_LABELLED_RE = re.compile(
    r"((?:authorization|proxy-authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?)[^\s,;\"']+",
    re.IGNORECASE,
)


def redact_secrets(text) -> str:
    """把 URL query 或授權標頭中的憑證值換成 ***REDACTED***。"""
    s = str(text)
    s = _QUERY_SECRET_RE.sub(r"\1***REDACTED***", s)
    s = _AUTH_LABELLED_RE.sub(r"\1***REDACTED***", s)
    s = _AUTH_HEADER_RE.sub(r"\1***REDACTED***", s)
    return s
