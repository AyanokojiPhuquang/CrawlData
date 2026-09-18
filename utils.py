"""Tiện ích dùng chung."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


def sanitize_folder_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150].strip() or "unknown_goi_thau"


def extract_notify_id(href: str) -> str | None:
    """Trích notify_id (ID duy nhất gói thầu) từ URL chi tiết."""
    try:
        qs = parse_qs(urlparse(href).query)
    except Exception:
        return None
    for key in ("notifyId", "id"):
        vals = qs.get(key) or qs.get(
            f"_egpportalcontractorselectionv2_WAR_egpportalcontractorselectionv2_{key}"
        )
        if vals and vals[0] and vals[0] != "undefined":
            return vals[0]
    # fallback: mã TBMT trong query
    no = qs.get("notifyNo")
    if no and no[0]:
        return no[0]
    return None


def extract_notify_no(href: str) -> str:
    try:
        qs = parse_qs(urlparse(href).query)
        v = qs.get("notifyNo")
        return v[0] if v else ""
    except Exception:
        return ""
