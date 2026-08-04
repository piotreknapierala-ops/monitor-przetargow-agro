from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "config" / "sources.csv"
DATA_FILE = ROOT / "data" / "notices.json"
PUBLIC_FILE = ROOT / "docs" / "data.json"

REQUEST_TIMEOUT = 25
MAX_LISTING_PAGES_PER_SOURCE = 8
MAX_LINKS_PER_PAGE = 300
SLEEP_BETWEEN_REQUESTS = 0.6

PROCUREMENT_TERMS = [
    "przetarg", "zamowien", "postepowan", "zaproszenie do skladania ofert",
    "zapytanie ofertowe", "ogloszen", "swz", "specyfikacja warunkow",
    "roboty budowlane", "wykonawca", "ofert"
]

OBJECT_TERMS = {
    "obora": 40, "obor": 40, "chlewn": 40, "kurnik": 40, "indycz": 45,
    "stajni": 35, "stajnia": 35, "owczarni": 35, "cielec": 30,
    "cielętnik": 40, "tuczarn": 40, "porodow": 35, "ferma": 30,
    "budynek inwentarski": 40, "hala inwentarska": 40, "hala rolnicza": 35,
    "magazyn zboz": 40, "magazyn pasz": 35, "magazyn": 18,
    "silos": 30, "wiata": 22, "dojarni": 40, "dojarnia": 40,
    "hala udojowa": 40, "kuchnia pasz": 40, "mieszalnia pasz": 40,
    "gnojowic": 35, "plyta obornik": 35, "płyta obornik": 35,
    "zbiornik": 12, "budynek gospodarczy": 20
}

WORK_TERMS = {
    "budow": 20, "rozbudow": 20, "przebudow": 20, "moderniz": 18,
    "remont": 15, "zaprojektuj i wybuduj": 25, "doprojektuj i wybuduj": 25,
    "dokumentacj projekt": 12, "projekt budowlany": 12, "wykonanie robot": 18,
    "generalny wykonawca": 20, "konstrukcj stalow": 15, "konstrukcj zelbet": 15,
    "dach": 10, "posadzk": 10, "wentylac": 10
}

EXCLUDE_TERMS = [
    "nawoz", "material siewny", "nasion", "pasza", "mleko w proszku",
    "usluga kopania", "usluga zbioru", "sprzedaz zwierzat", "zakup zwierzat",
    "olej napedowy", "energia elektryczna", "ubezpieczen", "srodek ochrony roslin",
    "bakterie azotowe", "weterynaryjn", "dzierzawa grunt"
]

LISTING_HINTS = [
    "przetarg", "zamowien", "postepowan", "ogloszen", "zapytan-ofert",
    "zapytania-ofert", "bip", "platformazakupowa", "ezamowienia"
]

DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-2]?\d|3[01])\b"),
    re.compile(r"\b([0-2]?\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b"),
]

DEADLINE_HINTS = [
    "termin skladania ofert", "skladanie ofert do", "oferty nalezy skladac do",
    "termin zlozenia ofert", "termin nadsyłania ofert", "termin nadsyłania"
]

@dataclass
class Source:
    name: str
    url: str
    category: str
    region: str


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_text(value: str, limit: int = 1000) -> str:
    value = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    return value[:limit]


def normalize_url(base: str, href: str) -> str | None:
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    full = urljoin(base, href)
    full, _ = urldefrag(full)
    parsed = urlparse(full)
    if parsed.scheme not in {"http", "https"}:
        return None
    return full


def same_domain(url_a: str, url_b: str) -> bool:
    a = urlparse(url_a).netloc.lower().removeprefix("www.")
    b = urlparse(url_b).netloc.lower().removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def load_sources() -> list[Source]:
    rows: list[Source] = []
    with SOURCES_FILE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("active", "1")).strip() not in {"1", "true", "True", "TAK", "tak"}:
                continue
            url = (row.get("url") or "").strip()
            if not url:
                continue
            rows.append(Source(
                name=(row.get("name") or url).strip(),
                url=url,
                category=(row.get("category") or "inne").strip(),
                region=(row.get("region") or "").strip(),
            ))
    return rows


def load_existing() -> dict:
    if not DATA_FILE.exists():
        return {"generated_at": None, "sources_checked": 0, "errors": [], "items": []}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"generated_at": None, "sources_checked": 0, "errors": [], "items": []}


def fetch(session: requests.Session, url: str) -> tuple[str, str]:
    response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    ctype = response.headers.get("content-type", "").lower()
    if "text/html" not in ctype and "application/xhtml" not in ctype and not ctype.startswith("text/"):
        return response.url, ""
    response.encoding = response.apparent_encoding or response.encoding
    return response.url, response.text


def page_title(soup: BeautifulSoup, fallback: str) -> str:
    if soup.find("h1"):
        return clean_text(soup.find("h1").get_text(" ", strip=True), 300)
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True), 300)
    return fallback


def is_listing_link(anchor_text: str, url: str) -> bool:
    hay = normalize_text(anchor_text + " " + url)
    return any(term in hay for term in LISTING_HINTS)


def has_object_term(text: str) -> bool:
    return any(term in text for term in OBJECT_TERMS)


def score_candidate(text: str, source: Source) -> tuple[int, list[str]]:
    norm = normalize_text(text)
    matched: list[str] = []
    score = 0

    for term, points in OBJECT_TERMS.items():
        if term in norm:
            score += points
            matched.append(term)
    for term, points in WORK_TERMS.items():
        if term in norm:
            score += points
            matched.append(term)
    if any(term in norm for term in PROCUREMENT_TERMS):
        score += 8
    if source.category.lower() in {"ohz", "stadnina", "kowr", "uczelnia", "instytut"}:
        score += 5

    exclusions = [term for term in EXCLUDE_TERMS if term in norm]
    if exclusions and not has_object_term(norm):
        score -= 100
    return score, sorted(set(matched))


def parse_dates(text: str) -> list[str]:
    results: list[str] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            try:
                parsed = date_parser.parse(raw, dayfirst=not raw[:4].isdigit(), fuzzy=False).date()
                if date(2020, 1, 1) <= parsed <= date(2035, 12, 31):
                    iso = parsed.isoformat()
                    if iso not in results:
                        results.append(iso)
            except (ValueError, OverflowError):
                pass
    return results


def extract_deadline(text: str) -> str | None:
    norm = normalize_text(text)
    for hint in DEADLINE_HINTS:
        idx = norm.find(hint)
        if idx >= 0:
            segment = text[max(0, idx - 40): idx + 220]
            dates = parse_dates(segment)
            if dates:
                return dates[0]
    return None


def item_id(url: str, title: str) -> str:
    key = normalize_url(url, url) or url
    if not key:
        key = normalize_text(title)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def make_item(source: Source, title: str, url: str, source_page: str, context: str) -> dict | None:
    title = clean_text(title, 500)
    context = clean_text(context, 1600)
    combined = f"{title} {context}"
    score, matched = score_candidate(combined, source)
    if score < 30 or not matched:
        return None

    dates = parse_dates(combined)
    deadline = extract_deadline(combined)
    today = date.today().isoformat()
    status = "Nieustalony"
    if deadline:
        status = "Otwarty" if deadline >= today else "Termin minął"

    return {
        "id": item_id(url, title),
        "source_name": source.name,
        "source_category": source.category,
        "region": source.region,
        "title": title,
        "url": url,
        "source_page": source_page,
        "published_date": dates[0] if dates else None,
        "deadline": deadline,
        "status": status,
        "score": min(score, 100),
        "matched_keywords": matched[:12],
        "snippet": context[:700],
    }


def scan_source(session: requests.Session, source: Source) -> list[dict]:
    final_url, text = fetch(session, source.url)
    if not text:
        return []
    soup = BeautifulSoup(text, "lxml")

    listing_urls = [final_url]
    for anchor in soup.find_all("a", href=True)[:MAX_LINKS_PER_PAGE]:
        href = normalize_url(final_url, anchor.get("href"))
        label = clean_text(anchor.get_text(" ", strip=True), 300)
        if href and same_domain(final_url, href) and is_listing_link(label, href):
            if href not in listing_urls:
                listing_urls.append(href)
        if len(listing_urls) >= MAX_LISTING_PAGES_PER_SOURCE:
            break

    items: list[dict] = []
    seen_urls: set[str] = set()

    for listing_url in listing_urls:
        if listing_url == final_url:
            page_html = text
            real_url = final_url
        else:
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            real_url, page_html = fetch(session, listing_url)
        if not page_html:
            continue

        page = BeautifulSoup(page_html, "lxml")
        page_text = clean_text(page.get_text(" ", strip=True), 8000)
        own = make_item(source, page_title(page, source.name), real_url, real_url, page_text)
        if own and own["url"] not in seen_urls:
            items.append(own)
            seen_urls.add(own["url"])

        for anchor in page.find_all("a", href=True)[:MAX_LINKS_PER_PAGE]:
            href = normalize_url(real_url, anchor.get("href"))
            if not href or href in seen_urls:
                continue
            label = clean_text(anchor.get_text(" ", strip=True), 500)
            title_attr = clean_text(anchor.get("title", ""), 300)
            nearby = ""
            parent = anchor.find_parent(["article", "li", "tr", "div", "p"])
            if parent:
                nearby = clean_text(parent.get_text(" ", strip=True), 1800)
            candidate_title = label or title_attr or Path(urlparse(href).path).name
            item = make_item(source, candidate_title, href, real_url, nearby)
            if item:
                items.append(item)
                seen_urls.add(href)

    return items


def merge_items(existing: dict, found: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    old_map = {item.get("id"): item for item in existing.get("items", []) if item.get("id")}
    merged: dict[str, dict] = dict(old_map)
    alerts: list[dict] = []

    for item in found:
        old = old_map.get(item["id"])
        fingerprint = hashlib.sha256(json.dumps({
            "title": item.get("title"), "deadline": item.get("deadline"),
            "status": item.get("status"), "snippet": item.get("snippet")
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

        if old:
            old_fingerprint = old.get("fingerprint")
            item["date_found"] = old.get("date_found", now)
            item["change_type"] = "Zmienione" if old_fingerprint and old_fingerprint != fingerprint else "Bez zmian"
            if item["change_type"] == "Zmienione":
                alerts.append(item)
        else:
            item["date_found"] = now
            item["change_type"] = "Nowe"
            alerts.append(item)

        item["last_seen"] = now
        item["fingerprint"] = fingerprint
        merged[item["id"]] = item

    values = list(merged.values())
    values.sort(key=lambda x: (
        x.get("status") == "Otwarty",
        x.get("change_type") == "Nowe",
        x.get("date_found") or "",
        x.get("score") or 0,
    ), reverse=True)
    return values[:1000], alerts


def send_email(alerts: list[dict]) -> None:
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    recipient = os.getenv("ALERT_TO", "").strip()
    if not alerts or not user or not password or not recipient:
        return

    lines = ["Nowe lub zmienione postępowania:\n"]
    for item in alerts[:25]:
        lines.append(
            f"- [{item.get('change_type')}] {item.get('source_name')}: {item.get('title')}\n"
            f"  Termin: {item.get('deadline') or 'nieustalony'} | Dopasowanie: {item.get('score')}/100\n"
            f"  {item.get('url')}\n"
        )

    message = EmailMessage()
    message["Subject"] = f"Monitor przetargów Agro: {len(alerts)} nowych/zmienionych"
    message["From"] = user
    message["To"] = recipient
    message.set_content("\n".join(lines))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(user, password)
        server.send_message(message)


def main() -> int:
    sources = load_sources()
    existing = load_existing()
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; AgroTenderMonitor/1.0; +https://github.com/)",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
    })

    all_found: list[dict] = []
    errors: list[dict] = []
    for source in sources:
        try:
            print(f"Sprawdzam: {source.name} -> {source.url}")
            all_found.extend(scan_source(session, source))
        except Exception as exc:  # noqa: BLE001 - one broken site must not stop the whole monitor
            print(f"BŁĄD {source.name}: {exc}", file=sys.stderr)
            errors.append({"source": source.name, "url": source.url, "error": str(exc)[:500]})
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    items, alerts = merge_items(existing, all_found)
    output = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources_checked": len(sources),
        "found_this_run": len(all_found),
        "new_or_changed": len(alerts),
        "errors": errors,
        "items": items,
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2)
    DATA_FILE.write_text(encoded, encoding="utf-8")
    PUBLIC_FILE.write_text(encoded, encoding="utf-8")
    send_email(alerts)
    print(f"Gotowe. Trafienia: {len(items)}, nowe/zmienione: {len(alerts)}, błędy: {len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
