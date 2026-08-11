"""
讀取並驗證搶位設定。

分成兩個來源，刻意分開是為了不讓個資進 git 歷史紀錄：
  - config.json（可以安全提交進repo，docs/fumee-booking.html 網頁表單編輯的就是這個檔）：
    只有 booking_url / attempts（日期時段人數偏好）/ 逾時參數，不含任何個資。
  - 環境變數（GitHub Secrets，只在跑Actions時注入，不進repo）：
    BOOKING_NAME / BOOKING_PHONE / BOOKING_EMAIL / NOTIFY_EMAIL_TO。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
EXAMPLE_PATH = Path(__file__).parent / "config.example.json"


@dataclass
class Attempt:
    label: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    party_size: int


@dataclass
class Contact:
    name: str
    phone: str
    email: str


@dataclass
class BookingConfig:
    contact: Contact
    notify_email_to: str
    booking_url: str
    attempts: list[Attempt]
    max_attempt_seconds: int
    retry_interval_ms: int


def _load_contact_from_env() -> Contact:
    name = os.environ.get("BOOKING_NAME")
    phone = os.environ.get("BOOKING_PHONE")
    email = os.environ.get("BOOKING_EMAIL")
    missing = [k for k, v in {"BOOKING_NAME": name, "BOOKING_PHONE": phone, "BOOKING_EMAIL": email}.items() if not v]
    if missing:
        raise ValueError(
            f"缺少環境變數：{', '.join(missing)}。這些是個資，故意不放in config.json，"
            "本機測試請 export，GitHub Actions 請設成 repo Secrets（見 fumee_booking/README.md）。"
        )
    return Contact(name=name, phone=phone, email=email)


def load_config(path: Path = CONFIG_PATH) -> BookingConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"找不到 {path}。請先複製 {EXAMPLE_PATH.name} 為 config.json 並填入你的資料，"
            "或透過 docs/fumee-booking.html 網頁表單建立/更新它。"
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    contact = _load_contact_from_env()

    attempts_raw = raw.get("attempts") or []
    if not attempts_raw:
        raise ValueError("config.json 的 attempts 至少要有一組（日期/時段/人數）")

    attempts = []
    for i, a in enumerate(attempts_raw):
        for field in ("date", "time", "party_size"):
            if a.get(field) in (None, ""):
                raise ValueError(f"config.json 的 attempts[{i}].{field} 不可為空")
        attempts.append(
            Attempt(
                label=a.get("label") or f"{a['date']} {a['time']} {a['party_size']}人",
                date=a["date"],
                time=a["time"],
                party_size=int(a["party_size"]),
            )
        )

    booking_url = raw.get("booking_url")
    if not booking_url:
        raise ValueError("config.json 缺少 booking_url")

    return BookingConfig(
        contact=contact,
        notify_email_to=os.environ.get("NOTIFY_EMAIL_TO") or contact.email,
        booking_url=booking_url,
        attempts=attempts,
        max_attempt_seconds=int(raw.get("max_attempt_seconds", 90)),
        retry_interval_ms=int(raw.get("retry_interval_ms", 500)),
    )
