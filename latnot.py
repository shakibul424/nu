
import datetime
import requests
import json
import os
import time
import tempfile

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urldefrag
from html import escape
from datetime import date

URL = "https://www.nu.ac.bd/"
JSON_FILE = "data/latnot.json"

# GitHub Actions Secrets থেকে নেওয়া হবে
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

headers = {
    "User-Agent": "Mozilla/5.0 (compatible; EducationalResearch/1.0)"
}

MAX_RETRIES = 3


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
            return datetime.datetime.strptime(
                text.strip(), fmt
            ).date().isoformat()
        except ValueError:
            continue

    return ""


def normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def format_bengali_date(date_iso):
    if not date_iso:
        return "তারিখ পাওয়া যায়নি"

    try:
        d = date.fromisoformat(date_iso)
    except ValueError:
        return "তারিখ পাওয়া যায়নি"

    months = [
        "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল",
        "মে", "জুন", "জুলাই", "আগস্ট",
        "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"
    ]

    return f"{d.day} {months[d.month - 1]} {d.year}"


def create_notice_message(data):
    title = escape(data["title"])
    url = escape(data["pdf_url"], quote=True)

    published = format_bengali_date(data.get("date_iso", ""))

    return (
        "🎓 <b>জাতীয় বিশ্ববিদ্যালয় — নতুন বিজ্ঞপ্তি</b>\n\n"
        "<blockquote>"
        f"📌 <b>বিষয়:</b>\n{title}\n\n"
        f"📅 <b>প্রকাশকাল:</b> <code>{published}</code>"
        "</blockquote>\n\n"
        f'<a href="{url}"><b>📥 সম্পূর্ণ বিজ্ঞপ্তি দেখুন (PDF)</b></a>'
    )


def save_notices(notices):
    """নিরাপদে JSON ফাইল আপডেট করবে।"""
    directory = os.path.dirname(os.path.abspath(JSON_FILE))

    fd, temp_file = tempfile.mkstemp(
        suffix=".tmp",
        dir=directory
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                notices,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, JSON_FILE)

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def send_notice(notice):
    payload = {
        "chat_id": CHAT_ID,
        "text": create_notice_message(notice),
        "parse_mode": "HTML",
        "link_preview_options": {
            "is_disabled": True
        }
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                API_URL,
                json=payload,
                timeout=30
            )

            try:
                result = response.json()
            except ValueError:
                result = {}

            if response.ok and result.get("ok"):
                message_id = result["result"]["message_id"]

                print("✅ Telegram-এ পাঠানো হয়েছে!")
                print("Message ID:", message_id)

                return True

            description = result.get(
                "description",
                response.text[:300]
            )

            print(
                f"❌ Telegram Error ({attempt}/{MAX_RETRIES}):",
                description
            )

            # ভুল Token, Chat ID বা অন্য স্থায়ী API error হলে
            # অপ্রয়োজনীয় retry না করা
            if response.status_code in (400, 401, 403, 404):
                print("⚠️ স্থায়ী API error; পরে সেটিংস পরীক্ষা করুন।")
                return False

            # Rate limit হলে Telegram-এর নির্দেশিত সময় মানা
            if response.status_code == 429:
                retry_after = result.get(
                    "parameters", {}
                ).get("retry_after", 5)

                if attempt < MAX_RETRIES:
                    time.sleep(min(max(retry_after, 1), 60))
                    continue

            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

        except requests.RequestException as error:
            print(
                f"🌐 Network Error ({attempt}/{MAX_RETRIES}):",
                error
            )

            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

    return False


def load_notices():
    if not os.path.exists(JSON_FILE):
        return []

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as file:
            notices = json.load(file)

        if not isinstance(notices, list):
            raise ValueError("JSON ফাইলের format list নয়")

        return notices

    except (json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            f"আগের JSON পড়া যায়নি: {error}"
        ) from error


def main():
    print("🔍 জাতীয় বিশ্ববিদ্যালয়ের নোটিশ চেক করা হচ্ছে...")

    # ১. আগের নোটিশ লোড
    all_notices = load_notices()

    notice_by_url = {}

    for notice in all_notices:
        pdf_url = notice.get("pdf_url")

        if pdf_url:
            notice_by_url[normalize_url(pdf_url)] = notice

    print("📚 আগের সংরক্ষিত নোটিশ:", len(all_notices))

    # ২. ওয়েবসাইট থেকে নোটিশ সংগ্রহ
    response = requests.get(
        URL,
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    soup = BeautifulSoup(response.text, "html.parser")
    section = soup.select_one("#section1")

    if section is None:
        raise RuntimeError(
            "#section1 পাওয়া যায়নি। ওয়েবসাইটের HTML পরিবর্তিত হয়েছে কি না দেখুন।"
        )

    new_count = 0

    # ৩. নতুন নোটিশ শনাক্ত
    for row in section.select("table tr"):
        link = row.select_one("td a")
        date_cell = row.select_one("td:nth-of-type(2)")

        if not link or not date_cell:
            continue

        title = link.get_text(" ", strip=True)
        publish_date = date_cell.get_text(" ", strip=True)
        href = link.get("href", "").strip()

        if not title or not href:
            continue

        pdf_url = urljoin(URL, href)
        unique_url = normalize_url(pdf_url)

        if unique_url in notice_by_url:
            continue

        notice = {
            "title": title,
            "publish_date": publish_date,
            "date_iso": parse_date(publish_date),
            "pdf_url": pdf_url,
            "sent": False
        }

        notice_by_url[unique_url] = notice
        all_notices.append(notice)
        new_count += 1

    print(f"🆕 নতুন নোটিশ পাওয়া গেছে: {new_count}টি")

    # ৪. পাঠানোর আগে সব নতুন নোটিশ JSON-এ সেভ
    save_notices(all_notices)

    # ৫. সব pending নোটিশ পাঠানোর চেষ্টা
    pending = [
        notice for notice in all_notices
        if not notice.get("sent", False)
    ]

    print(f"📨 পাঠানোর অপেক্ষায়: {len(pending)}টি")

    success_count = 0
    failed_count = 0

    for notice in pending:
        print("\n📌", notice["title"])

        success = send_notice(notice)

        if success:
            notice["sent"] = True
            notice["sent_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()

            success_count += 1
        else:
            notice["sent"] = False
            failed_count += 1

        # প্রতিটি নোটিশের ফলাফল সঙ্গে সঙ্গে সেভ
        save_notices(all_notices)

        # একাধিক মেসেজ পাঠানোর মাঝে বিরতি
        time.sleep(1)

    print("\n" + "=" * 40)
    print("✅ Scraping সম্পন্ন")
    print("🆕 নতুন নোটিশ:", new_count)
    print("📨 সফলভাবে পাঠানো:", success_count)
    print("❌ ব্যর্থ / Pending:", failed_count)
    print("📚 মোট সংরক্ষিত:", len(all_notices))
    print("=" * 40)


if __name__ == "__main__":
    main()
