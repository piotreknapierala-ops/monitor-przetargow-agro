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
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "config" / "sources.csv"
DATA_FILE = ROOT / "data" / "notices.json"
PUBLIC_FILE = ROOT / "docs" / "data.json"

REQUEST_TIMEOUT = 18
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MAX_LISTING_PAGES_PER_SOURCE = 5
MAX_LINKS_PER_PAGE = 250
SLEEP_BETWEEN_REQUESTS = 0.25
MAX_AGE_DAYS = 730

PROCUREMENT_TERMS = [
    "przetarg", "zamowien", "postepowan", "zaproszenie do skladania ofert",
    "zapytanie ofertowe", "ogloszen", "roboty budowlane", "wykonawca",
    "skladania ofert", "specyfikacja warunkow", "swz"
]

OBJECT_TERMS = {
    "obora": 45, "obor": 45, "chlewn": 45, "kurnik": 45, "indycz": 50,
    "stajni": 42, "stajnia": 42, "owczarni": 40, "cielec": 32,
    "cielętnik": 45, "tuczarn": 45, "porodow": 38, "ferma": 35,
    "budynek inwentarski": 48, "hala inwentarska": 48, "hala rolnicza": 38,
    "magazyn zboz": 45, "magazyn pasz": 42, "magazyn": 18,
    "silos": 32, "wiata": 24, "dojarni": 45, "dojarnia": 45,
    "hala udojowa": 45, "kuchnia pasz": 45, "mieszalnia pasz": 45,
    "gnojowic": 40, "plyta obornik": 40, "płyta obornik": 40,
    "zbiornik": 14, "budynek gospodarczy": 25
}

STRONG_OBJECT_TERMS = {
    "obora", "obor", "chlewn", "kurnik", "indycz", "stajni", "stajnia",
    "owczarni", "cielętnik", "tuczarn", "porodow", "ferma",
    "budynek inwentarski", "hala inwentarska", "magazyn zboz",
    "magazyn pasz", "dojarni", "dojarnia", "hala udojowa",
    "kuchnia pasz", "mieszalnia pasz", "gnojowic", "plyta obornik"
}

WORK_TERMS = {
    "budow": 24, "rozbudow": 24, "przebudow": 24, "moderniz": 20,
    "remont": 18, "zaprojektuj i wybuduj": 30, "doprojektuj i wybuduj": 30,
    "dokumentacj projekt": 15, "projekt budowlany": 15, "wykonanie robot": 22,
    "roboty budowlane": 22, "generalny wykonawca": 22,
    "konstrukcj stalow": 18, "konstrukcj zelbet": 18,
    "dach": 10, "posadzk": 10, "wentylac": 10
}

EXCLUDE_TERMS = [
    "nawoz", "material siewny", "nasion", "pasza", "mleko w proszku",
    "usluga kopania", "usluga zbioru", "sprzedaz zwierzat", "zakup zwierzat",
    "olej napedowy", "energia elektryczna", "ubezpieczen", "srodek ochrony roslin",
    "bakterie azotowe", "weterynaryjn", "dzierzawa grunt", "sprzedaz zboza",
    "sprzedaż zboża", "sprzedaz nawozu", "zakup nawozu"
]

LISTING_HINTS = [
    "przetarg", "zamowien", "postepowan", "ogloszen", "zapytan-ofert",
    "zapytania-ofert", "bip", "platformazakupowa", "ezamowienia",
    "smartpzp", "logintrade", "eb2b", "bazakonkurencyjnosci"
]

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z",
    ".jpg", ".jpeg", ".png", ".odt", ".ods"
}

DOCUMENT_NOISE = [
    "formularz", "zalacznik", "załącznik", "regulamin", "oswiadczenie",
    "oświadczenie", "warunki udzialu", "warunków udziału", "swz", "wzor umowy",
    "wzór umowy", "odpowiedzi", "wyjasnienia", "wyjaśnienia", "pytania",
    "protokol", "protokół", "informacja z otwarcia", "ogloszenie o wyniku",
    "ogłoszenie o wyniku", "modyfikacja", "zmiana terminu", "pobierz"
]

GENERIC_TITLES = {
    "przetargi", "przetargi/ogloszenia", "przetargi i ogloszenia", "ogloszenia",
    "ogloszenie przetargu", "zamowienia publiczne", "postepowania", "czytaj wiecej",
    "wiecej", "szczegoly", "menu", "strona glowna"
}

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


@dataclass
class Candidate:
    title: str
    url: str
    listing_url: str
    context: str
    score: int
    matched: list[str]
    published_date: str | None
    deadline: str | None


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


def is_document_url(url: str) -> bool:
    return Path(urlparse(url).path.lower()).suffix in DOCUMENT_EXTENSIONS


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
        return {"items": []}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": []}


def candidate_url_variants(url: str) -> list[str]:
    """Zwraca warianty adresu dla starych serwisów z błędnym SSL lub www."""
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    hosts = [host]
    if host.startswith("www."):
        hosts.append(host[4:])
    elif host:
        hosts.append("www." + host)

    variants: list[str] = []
    for scheme in [parsed.scheme or "https", "https", "http"]:
        for candidate_host in hosts:
            candidate = f"{scheme}://{candidate_host}{path}{query}"
            if candidate not in variants:
                variants.append(candidate)
    return variants


def fetch(session: requests.Session, url: str) -> tuple[str, str]:
    """Pobiera HTML; przy wadliwym certyfikacie próbuje bez jego weryfikacji."""
    last_exc: Exception | None = None
    for candidate in candidate_url_variants(url):
        for verify in (True, False):
            try:
                response = session.get(
                    candidate,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                    verify=verify,
                )
                response.raise_for_status()
                ctype = response.headers.get("content-type", "").lower()
                if (
                    "text/html" not in ctype
                    and "application/xhtml" not in ctype
                    and not ctype.startswith("text/")
                ):
                    return response.url, ""
                response.encoding = response.apparent_encoding or response.encoding
                return response.url, response.text
            except (
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError,
            ) as exc:
                last_exc = exc
                continue
    if last_exc:
        raise last_exc
    raise requests.RequestException(f"Nie udało się pobrać {url}")


def is_listing_link(anchor_text: str, url: str) -> bool:
    hay = normalize_text(anchor_text + " " + url)
    return any(term in hay for term in LISTING_HINTS)


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
            # Indeksy tekstu znormalizowanego i oryginalnego mogą się lekko różnić,
            # dlatego sprawdzamy krótki fragment oraz cały blok jako plan awaryjny.
            segment = text[max(0, idx - 60): idx + 260]
            dates = parse_dates(segment) or parse_dates(text)
            if dates:
                return dates[0]
    return None


def score_candidate(text: str, listing_url: str) -> tuple[int, list[str]]:
    norm = normalize_text(text)
    object_matches = [term for term in OBJECT_TERMS if term in norm]
    work_matches = [term for term in WORK_TERMS if term in norm]
    procurement_match = any(term in norm for term in PROCUREMENT_TERMS) or is_listing_link("", listing_url)

    # Musi to być jednocześnie inwestycja i właściwy typ obiektu.
    if not object_matches or not work_matches or not procurement_match:
        return 0, []

    exclusions = [term for term in EXCLUDE_TERMS if term in norm]
    strong_object = any(term in norm for term in STRONG_OBJECT_TERMS)
    if exclusions and not strong_object:
        return 0, []

    score = sum(OBJECT_TERMS[t] for t in object_matches)
    score += sum(WORK_TERMS[t] for t in work_matches)
    score += 10
    if exclusions:
        score -= 30
    return min(score, 100), sorted(set(object_matches + work_matches))


def meaningful_label(value: str) -> bool:
    norm = normalize_text(value)
    if len(norm) < 12 or norm in GENERIC_TITLES:
        return False
    if any(noise in norm for noise in DOCUMENT_NOISE):
        return False
    return any(term in norm for term in OBJECT_TERMS) and any(term in norm for term in WORK_TERMS)


def sentence_title(context: str) -> str | None:
    sentences = re.split(r"(?<=[.!?;])\s+|\s{2,}|\n+", context)
    choices: list[str] = []
    for sentence in sentences:
        sentence = clean_text(sentence, 320)
        norm = normalize_text(sentence)
        if 25 <= len(sentence) <= 320 and any(t in norm for t in OBJECT_TERMS) and any(t in norm for t in WORK_TERMS):
            if not any(noise in norm for noise in DOCUMENT_NOISE):
                choices.append(sentence)
    if not choices:
        return None
    choices.sort(key=lambda x: (len(x) > 220, -len(x)))
    return choices[0]


def block_for_anchor(anchor: Tag) -> Tag | None:
    block = anchor.find_parent(["article", "li", "tr"])
    if block is not None:
        return block

    parent = anchor.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            break
        classes = " ".join(parent.get("class", []))
        ident = f"{classes} {parent.get('id', '')}"
        text_len = len(clean_text(parent.get_text(" ", strip=True), 4000))
        if re.search(r"post|entry|item|tender|przetarg|offer|oglosz|zamow|aktual", ident, re.I) and 30 <= text_len <= 2600:
            return parent
        parent = parent.parent

    fallback = anchor.find_parent(["p", "div"])
    if fallback is not None:
        text_len = len(clean_text(fallback.get_text(" ", strip=True), 4000))
        if 30 <= text_len <= 1800:
            return fallback
    return None


def choose_title_and_url(block: Tag, listing_url: str) -> tuple[str | None, str]:
    labels: list[tuple[str, str]] = []

    for heading in block.find_all(["h2", "h3", "h4", "h5"], limit=8):
        label = clean_text(heading.get_text(" ", strip=True), 320)
        link = heading.find("a", href=True)
        href = normalize_url(listing_url, link.get("href")) if link else None
        if meaningful_label(label):
            labels.append((label, href or listing_url))

    for anchor in block.find_all("a", href=True, limit=30):
        label = clean_text(anchor.get_text(" ", strip=True), 320)
        href = normalize_url(listing_url, anchor.get("href"))
        if href and meaningful_label(label) and not is_document_url(href):
            labels.append((label, href))

    if labels:
        labels.sort(key=lambda x: len(x[0]), reverse=True)
        return labels[0]

    context = clean_text(block.get_text(" ", strip=True), 2200)
    title = sentence_title(context)
    if not title:
        return None, listing_url

    # Dokumenty są tylko załącznikami. Gdy brak właściwego linku do wpisu,
    # kierujemy użytkownika do strony z listą postępowań.
    for anchor in block.find_all("a", href=True, limit=30):
        href = normalize_url(listing_url, anchor.get("href"))
        label = clean_text(anchor.get_text(" ", strip=True), 200)
        if href and not is_document_url(href) and label and normalize_text(label) not in GENERIC_TITLES:
            return title, href
    return title, listing_url


def candidate_from_block(block: Tag, listing_url: str) -> Candidate | None:
    context = clean_text(block.get_text(" ", strip=True), 2400)
    if len(context) < 30:
        return None

    score, matched = score_candidate(context, listing_url)
    if score < 60:
        return None

    dates = parse_dates(context)
    deadline = extract_deadline(context)
    today = date.today()
    future_deadline = bool(deadline and deadline >= today.isoformat())
    parsed_dates = [date.fromisoformat(x) for x in dates]
    if parsed_dates and max(parsed_dates) < today - timedelta(days=MAX_AGE_DAYS) and not future_deadline:
        return None

    title, url = choose_title_and_url(block, listing_url)
    if not title:
        return None

    past_dates = [d for d in dates if d <= today.isoformat()]
    published = max(past_dates) if past_dates else (dates[0] if dates else None)
    return Candidate(
        title=clean_text(title, 320),
        url=url,
        listing_url=listing_url,
        context=context,
        score=score,
        matched=matched,
        published_date=published,
        deadline=deadline,
    )


def candidate_key(candidate: Candidate) -> str:
    norm = normalize_text(candidate.title)
    norm = re.sub(r"\b(20\d{2}|\d{1,2})\b", "", norm)
    norm = re.sub(r"\b(przetarg|ogloszenie|zaproszenie|postepowanie|oferta|roboty budowlane)\b", "", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    words = norm.split()[:24]
    return " ".join(words) or normalize_text(candidate.listing_url)


def discover_listing_urls(soup: BeautifulSoup, final_url: str) -> list[str]:
    urls: list[str] = []
    if is_listing_link("", final_url):
        urls.append(final_url)

    for anchor in soup.find_all("a", href=True)[:MAX_LINKS_PER_PAGE]:
        href = normalize_url(final_url, anchor.get("href"))
        label = clean_text(anchor.get_text(" ", strip=True), 250)
        if not href or not is_listing_link(label, href):
            continue
        if same_domain(final_url, href) or any(host in href for host in (
            "platformazakupowa.pl", "ezamowienia.gov.pl", "smartpzp.pl",
            "logintrade.net", "eb2b.com.pl", "bazakonkurencyjnosci.funduszeeuropejskie.gov.pl"
        )):
            if href not in urls:
                urls.append(href)
        if len(urls) >= MAX_LISTING_PAGES_PER_SOURCE:
            break

    # Jeśli adres z CSV był bezpośrednią zakładką, nie dodajemy strony głównej drugi raz.
    if not urls:
        urls.append(final_url)
    return urls[:MAX_LISTING_PAGES_PER_SOURCE]


def scan_source(session: requests.Session, source: Source) -> list[dict]:
    """
    Zwraca osobny rekord dla każdego rzeczywistego postępowania.
    Załączniki, formularze, SWZ i odpowiedzi pozostają zgrupowane pod postępowaniem.
    """
    final_url, home_html = fetch(session, source.url)
    if not home_html:
        return []
    home = BeautifulSoup(home_html, "lxml")
    listing_urls = discover_listing_urls(home, final_url)

    candidates: list[Candidate] = []
    seen_blocks: set[str] = set()

    for listing_url in listing_urls:
        if listing_url == final_url:
            real_url, page_html = final_url, home_html
        else:
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            real_url, page_html = fetch(session, listing_url)
        if not page_html:
            continue

        page = BeautifulSoup(page_html, "lxml")
        for unwanted in page.find_all(["nav", "header", "footer", "script", "style", "noscript", "aside"]):
            unwanted.decompose()

        main = page.find("main") or page.find(id=re.compile(r"content|main", re.I)) or page.body or page

        # Najpierw czytamy bezpośrednio nagłówki postępowań. To zabezpiecza strony,
        # na których jeden wpis ma bardzo dużo załączników (np. OHZ Lubiana),
        # przez co cały kontener wpisu jest zbyt obszerny dla zwykłej analizy bloków.
        for heading in page.find_all(["h1", "h2", "h3", "h4", "h5"], limit=180):
            label = clean_text(heading.get_text(" ", strip=True), 320)
            if not meaningful_label(label):
                continue

            score, matched = score_candidate(label, real_url)
            if score < 60:
                continue

            link = heading.find("a", href=True)
            href = normalize_url(real_url, link.get("href")) if link else real_url
            if href and is_document_url(href):
                href = real_url

            # Data zwykle znajduje się tuż obok nagłówka. Pobieramy tylko krótki
            # fragment otoczenia, aby nie domieszać nazw dziesiątek załączników.
            context_node = heading.find_parent("article") or heading.parent
            context = clean_text(
                context_node.get_text(" ", strip=True) if isinstance(context_node, Tag) else label,
                900,
            )
            dates = parse_dates(context)
            deadline = extract_deadline(context)
            today = date.today()
            parsed_dates = [date.fromisoformat(x) for x in dates]
            future_deadline = bool(deadline and deadline >= today.isoformat())
            if parsed_dates and max(parsed_dates) < today - timedelta(days=MAX_AGE_DAYS) and not future_deadline:
                continue
            past_dates = [d for d in dates if d <= today.isoformat()]
            published = max(past_dates) if past_dates else (dates[0] if dates else None)

            candidates.append(Candidate(
                title=label,
                url=href or real_url,
                listing_url=real_url,
                context=context,
                score=score,
                matched=matched,
                published_date=published,
                deadline=deadline,
            ))

        for anchor in main.find_all("a", href=True)[:MAX_LINKS_PER_PAGE]:
            block = block_for_anchor(anchor)
            if block is None:
                continue
            block_text = clean_text(block.get_text(" ", strip=True), 2600)
            block_hash = hashlib.sha1(normalize_text(block_text).encode("utf-8")).hexdigest()
            if block_hash in seen_blocks:
                continue
            seen_blocks.add(block_hash)
            candidate = candidate_from_block(block, real_url)
            if candidate:
                candidates.append(candidate)

    if not candidates:
        return []

    deduped: dict[str, Candidate] = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        current = deduped.get(key)
        if current is None:
            deduped[key] = candidate
        else:
            current_specific = current.url != current.listing_url
            candidate_specific = candidate.url != candidate.listing_url
            if (
                candidate.score > current.score
                or (candidate.score == current.score and candidate_specific and not current_specific)
                or (
                    candidate.score == current.score
                    and candidate.published_date
                    and (not current.published_date or candidate.published_date > current.published_date)
                )
            ):
                deduped[key] = candidate

    unique = list(deduped.values())
    unique.sort(
        key=lambda c: (
            bool(c.deadline and c.deadline >= date.today().isoformat()),
            c.published_date or "",
            c.score,
        ),
        reverse=True,
    )

    source_id = hashlib.sha256(
        normalize_text(source.name + " " + source.url).encode("utf-8")
    ).hexdigest()[:16]

    items: list[dict] = []
    for candidate in unique:
        proceeding_key = candidate_key(candidate)
        proceeding_id = hashlib.sha256(
            f"{source_id}|{proceeding_key}".encode("utf-8")
        ).hexdigest()[:24]
        status = (
            "Otwarty"
            if candidate.deadline and candidate.deadline >= date.today().isoformat()
            else "Do sprawdzenia"
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "deadline": candidate.deadline,
                    "published_date": candidate.published_date,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        items.append(
            {
                "id": proceeding_id,
                "source_name": source.name,
                "source_category": source.category,
                "region": source.region,
                "title": candidate.title,
                "url": candidate.url or candidate.listing_url,
                "source_page": candidate.listing_url,
                "published_date": candidate.published_date,
                "deadline": candidate.deadline,
                "status": status,
                "score": candidate.score,
                "matched_keywords": candidate.matched[:12],
                "snippet": "Załączniki i dokumenty tego postępowania zostały zgrupowane.",
                "fingerprint": fingerprint,
            }
        )
    return items


def merge_items(existing: dict, found: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    old_map = {item.get("id"): item for item in existing.get("items", []) if item.get("id")}
    current_items: list[dict] = []
    alerts: list[dict] = []

    for item in found:
        old = old_map.get(item["id"])
        if old:
            item["date_found"] = old.get("date_found", now)
            item["change_type"] = "Zmienione" if old.get("fingerprint") != item.get("fingerprint") else "Bez zmian"
        else:
            item["date_found"] = now
            item["change_type"] = "Nowe"

        item["last_seen"] = now
        if item["change_type"] in {"Nowe", "Zmienione"}:
            alerts.append(item)
        current_items.append(item)

    current_items.sort(key=lambda x: (
        x.get("status") == "Otwarty",
        x.get("change_type") in {"Nowe", "Zmienione"},
        x.get("published_date") or "",
        x.get("score") or 0,
    ), reverse=True)
    return current_items, alerts


def send_email(alerts: list[dict]) -> None:
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    recipient = os.getenv("ALERT_TO", "").strip()
    if not alerts or not user or not password or not recipient:
        return

    lines = ["Nowe lub zmienione postępowania pasujące do profilu Agro:\n"]
    for item in alerts[:25]:
        lines.append(
            f"- [{item.get('change_type')}] {item.get('source_name')}: {item.get('title')}\n"
            f"  Termin: {item.get('deadline') or 'do sprawdzenia'}\n"
            f"  {item.get('url')}\n"
        )

    message = EmailMessage()
    message["Subject"] = f"Monitor przetargów Agro: {len(alerts)} nowych/zmienionych postępowań"
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
        "User-Agent": "Mozilla/5.0 (compatible; AgroTenderMonitor/3.0; +https://github.com/)",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
    })

    found_items: list[dict] = []
    errors: list[dict] = []
    for source in sources:
        try:
            print(f"Sprawdzam: {source.name} -> {source.url}")
            source_items = scan_source(session, source)
            found_items.extend(source_items)
        except Exception as exc:  # jeden niedziałający serwis nie zatrzymuje monitora
            print(f"BŁĄD {source.name}: {exc}", file=sys.stderr)
            errors.append({"source": source.name, "url": source.url, "error": str(exc)[:260]})
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    items, alerts = merge_items(existing, found_items)
    units_with_hits = len({item.get("source_name") for item in items})
    output = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources_checked": len(sources),
        "found_this_run": len(items),
        "units_with_hits": units_with_hits,
        "new_or_changed": len(alerts),
        "errors": errors,
        "items": items,
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2)
    DATA_FILE.write_text(encoded, encoding="utf-8")
    PUBLIC_FILE.write_text(encoded, encoding="utf-8")
    send_email(alerts)
    print(f"Gotowe. Postępowania: {len(items)}, jednostki: {units_with_hits}, nowe/zmienione: {len(alerts)}, błędy: {len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
