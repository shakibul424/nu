
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import unicodedata

from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.nu.ac.bd/examination-notice.php"
DATA_FILE = Path("data/degnot.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; NUDegreeNoticeBot/1.0)"
    )
}

TIMEOUT = 30

KEYWORDS = [
    "degree pass",
    "degree (pass)",
    "degree-pass",
    "degree pass course",
    "degree & certificate course",
    "degree and certificate course",
    "degree (pass) and certificate course",
    "degree (pass) & certificate course",
    "ডিগ্রি পাস",
    "ডিগ্রী পাস",
    "ডিগ্রি (পাস)",
    "ডিগ্রী (পাস)",
    "ডিগ্রি(পাস)",
    "ডিগ্রী(পাস)",
    "ডিগ্রি-পাস",
    "ডিগ্রী-পাস",
    "পাস কোর্স",
    "ডিগ্রী",
    "ডিগ্রি",
    "ডিগ্রি পাস কোর্স",
    "ডিগ্রী পাস কোর্স",
    "সার্টিফিকেট কোর্স",
    "ডিগ্রি ও সার্টিফিকেট কোর্স",
    "ডিগ্রী ও সার্টিফিকেট কোর্স",
    "স্নাতক পাস",
    "স্নাতক (পাস)",
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def normalize(text: str) -> str:
    """Normalize Unicode and whitespace for keyword matching."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    text = text.replace("\u200c", "").replace("\u200d", "")
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED_KEYWORDS = tuple(normalize(k) for k in KEYWORDS)


def is_degree_notice(title: str) -> bool:
    normalized = normalize(title)
    return any(k in normalized for k in NORMALIZED_KEYWORDS)


def parse_date(text: str) -> str:
    formats = (
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    )

    for fmt in formats:
        try:
            return datetime.strptime(
                text.strip(), fmt
            ).date().isoformat()
        except ValueError:
            continue

    return ""


def read_existing() -> dict:
    if not DATA_FILE.exists():
        return {}

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(
            data.get("notices"), list
        ):
            return data
    except (OSError, json.JSONDecodeError):
        logging.warning("Existing JSON cache could not be read.")

    return {}


def scrape() -> list[dict]:
    response = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding or "utf-8"
    )

    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.select_one("#nuRecentNoticeTable")

    if table is None:
        table = soup.select_one(
            ".nu-examination-notice-section table"
        )

    if table is None:
        raise RuntimeError(
            "Notice table not found. NU may have changed its HTML."
        )

    notices = []
    seen_urls = set()

    for row in table.select("tbody tr"):
        cells = row.find_all("td")

        if len(cells) < 2:
            continue

        link = cells[0].find("a", href=True)

        if not link:
            continue

        title = link.get_text(" ", strip=True)

        if not title or not is_degree_notice(title):
            continue

        pdf_url = urljoin(SOURCE_URL, link["href"])

        if pdf_url in seen_urls:
            continue

        seen_urls.add(pdf_url)

        raw_date = cells[1].get_text(" ", strip=True)

        notices.append({
            "title": title,
            "date": raw_date,
            "date_iso": parse_date(raw_date),
            "url": pdf_url
        })

    if not notices:
        raise RuntimeError(
            "No matching Degree Pass notices found. "
            "Refusing to overwrite existing data."
        )

    return notices


def atomic_write(data: dict) -> None:
    """Write JSON atomically to avoid partial/corrupt files."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        dir=DATA_FILE.parent,
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        os.replace(temp_name, DATA_FILE)

    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    existing = read_existing()

    # Preserve the existing cache if the source is unavailable.
    fresh_notices = scrape()

    old_by_url = {
        item.get("url"): item
        for item in existing.get("notices", [])
        if item.get("url")
    }

    # Keep the newest source data and retain older cached notices
    # that may have disappeared from the currently displayed page.
    fresh_urls = {item["url"] for item in fresh_notices}

    older_notices = [
        item
        for item in existing.get("notices", [])
        if item.get("url") not in fresh_urls
    ]

    merged = fresh_notices + older_notices

    # Report genuinely new notice URLs.
    new_notices = [
        item for item in fresh_notices
        if item["url"] not in old_by_url
    ]

    output = {
        "source": SOURCE_URL,
        "category": "degree_pass",
        "updated_at": int(time.time()),
        "total": len(merged),
        "new_count": len(new_notices),
        "notices": merged,
    }

    atomic_write(output)

    logging.info("Scraped: %d", len(fresh_notices))
    logging.info("New notices: %d", len(new_notices))
    logging.info("Total cached: %d", len(merged))


if __name__ == "__main__":
    main()