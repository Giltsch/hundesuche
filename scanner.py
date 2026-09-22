#!/usr/bin/env python3
"""
HUNDESUCHE-SCANNER — sucht Kleinanzeigen/Marktplätze (DE + Nachbarländer) nach einem
gestohlenen Schäferhund-Husky-Rüden (3 J., 19.08.2026, Haag i. OB). Nur Python-Stdlib.

  python3 scanner.py            # ein Durchlauf, Report -> report.html
  python3 scanner.py --loop 15  # alle 15 Minuten, Benachrichtigung bei neuen Treffern

Privat/optional per Umgebungsvariable (in GitHub als Secret):
  DOG_NAME    Name des Hundes (Scoring + Suche; erscheint nirgends im Report)
  NTFY_TOPIC  ntfy.sh-Topic für Push-Nachrichten aufs Handy
  SITE_URL    Link zum Report (für die Push-Nachricht)
"""
import argparse, datetime as dt, html, json, os, random, re, subprocess, sys, time
import urllib.parse, urllib.request
from pathlib import Path

BASE = Path(__file__).parent
SEEN_FILE = BASE / "seen.json"
REPORT = Path(os.environ.get("REPORT_PATH", BASE / "report.html"))
DOG_NAME = os.environ.get("DOG_NAME", "").strip().lower()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SITE_URL = os.environ.get("SITE_URL", "").strip()
THEFT_DATE = dt.date(2026, 8, 19)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# --- Suchbegriffe -----------------------------------------------------------
# Verkäufer beschreiben gestohlene Hunde oft vage ("Mischling", "abzugeben"),
# daher bewusst breit suchen und danach scoren.
QUERIES = [
    "schäferhund husky", "husky schäferhund", "schäferhund mix", "husky mix",
    "schäferhundmix", "huskymix", "schäferhund mischling", "husky mischling",
    "schäferhund rüde", "husky rüde", "altdeutscher schäferhund", "wolfshund",
    "schäferhund abzugeben", "husky abzugeben", "rüde abzugeben", "hund abzugeben",
    "schäferhund zugelaufen", "hund zugelaufen", "hund gefunden",
    "schäferhund husky schutzgebühr", "husky mix tierschutz", "schäferhund mix tierschutz", "tierschutz bayern",
] + ([f"{DOG_NAME} rüde"] if DOG_NAME else [])
REGIONS = {"bayern": "l5510", "deutschland": ""}  # Bayern + bundesweit
PAGES = 5  # per --pages überschreibbar; tiefere Seiten = ältere Anzeigen
# Kleinanzeigen-Kategorie "Vermisste Tiere" (c283): Fund-/Zugelaufen-Meldungen
LOST_QUERIES = ["", "hund", "schäferhund", "husky", "rüde", "zugelaufen", "gefunden"]
# Websuche (DuckDuckGo HTML) – Foren, Facebook-Posts, Tierheim-Seiten, Zeitungen
WEB_QUERIES = [
    '"Schäferhund" "Husky" zugelaufen', '"Schäferhund Husky" gefunden Bayern',
    'Schäferhund Husky Mix Rüde abzugeben',
    'Hund gestohlen Haag Oberbayern', 'Schäferhund zugelaufen Mühldorf OR Rosenheim OR Ebersberg OR Erding OR Wasserburg',
    'Husky Mischling Fundhund Oberbayern', 'VW Caddy Hund gestohlen Oberbayern',
    'Schäferhund Husky Salzburg zugelaufen', 'willhaben Schäferhund Husky Rüde',
    'ovčák husky kříženec pes nalezen', 'owczarek husky mieszaniec znaleziony',
    '"Tierschutz Bayern" Hund abgeholt', 'falsche Tierschützer Hund Oberbayern', 'Schäferhund Husky Mischling Schutzgebühr Rüde',
] + ([f'"{DOG_NAME}" Schäferhund Husky'] if DOG_NAME else [])
WEB_PER_RUN = 4  # DuckDuckGo blockt schnell -> pro Lauf nur wenige, rotierend

# --- Scoring ------------------------------------------------------------------
RULES = [  # (regex, punkte, label)
    # mehrsprachig: DE / CZ-SK / PL / NL / IT / HU
    (r"husk[yi]|hasky", 3, "Husky"),
    (r"sch(ä|ae|a)fer|ovč[áa]k|ovčiak|ovcak|owczar|herder|pastore|juhász|shepherd", 3, "Schäferhund"),
    (r"\brüde|\bruede|\bsamec|\bpsík|\bpies\b|\breu\b|maschio|\bkan\b", 1, "Rüde"),
    (r"\b[34]\s*(jahre|j\.|jährig|roky|roku|lata|jaar|anni|éves)|202[23]\s*geb|geb\w*\s*202[23]|dreijährig|vierjährig", 2, "~3-4 Jahre"),
    # Masche laut Presse: junges Pärchen gab sich als "Tierschutz Bayern" aus -> Weiterverkauf als "Tierschutzhund"
    (r"tierschutz bayern", 4, "»Tierschutz Bayern«"),
    (r"schutzgebühr|tierschutz|vermittlung|notfell|pflegestelle|vom tierschutz|adopt", 1.5, "Tierschutz-Vermittlung (Masche)"),
    (r"bernstein|amber|goldene augen|helle augen|jantar|bursztyn|ambra", 3, "Bernstein-Augen"),
    (r"pinsel|büschel|buschel|haarbüschel|štětec|pędzel", 3, "Pinsel/Büschel am Ohr"),
    (r"zugelaufen|gefunden|aufgefunden|streuner|nalezen|najden|znalezion|gevonden|trovato|talált", 3, "Zugelaufen/Gefunden"),
    (r"gestohlen|geklaut|entwendet|ukraden|skradzion|gestolen|rubato|lopott", 2, "Gestohlen erwähnt"),
    (r"kříženec|kríženec|mieszaniec|kruising|incrocio|keverék|mischling|\bmix\b", 1, "Mischling"),
    (r"dringend|schnell|sofort|heute noch", 1, "Eile"),
    (r"umzug|keine zeit|allergie|trennung|aus zeitgründen", 1, "Standard-Abgabegrund"),
    (r"ohne papiere|keine papiere|kein chip|nicht gechipt|ohne chip|kein impfpass", 2, "Keine Papiere/Chip"),
    (r"nur abholung|nur bar|barzahlung", 1, "Nur bar/Abholung"),
    (r"springt|verspielt|verschmust|lieb", 0.5, "Charakter passt"),
    (r"welpe|welpen|štěn|šteň|szczeni|pup(py|s)|cuccio|kölyök|kiskutya", -3, "Welpe"),
    (r"hündin|huendin|\bfen(a|ka|ku|ečka)\b|\bsuk[ai]\b|\bteef\b|femmina|szuka", -4, "Hündin"),
    (r"deckrüde|deckakt|zucht", -2, "Zucht"),
] + ([(rf"\b{re.escape(DOG_NAME)}\b", 5, "Name passt")] if DOG_NAME else []) + [
    (r"chihuahua|malteser|dackel|yorkshire|pudel|spitz|mops|bulldog|labrador|retriever|terrier|havaneser|shih|pomeranian|beagle|border collie|australi(an|en) shepherd|aussie|dobermann|rottweiler|cane corso|malinois|belgisch|belgick|belgijsk|mallorquin", -3, "andere Rasse"),
    (r"suche\b|gesucht", -1, "Suchanzeige"),
]
NEAR_PLZ = ("835", "834", "845", "84", "83", "85", "81", "80")  # Haag i.OB = 83527


COUNTRY_BONUS = {"AT": 1.5, "CZ": 1, "SK": 0.5, "PL": 0.5, "IT": 0.5, "HU": 0.5, "NL": 0}


def score(text, plz, posted, country="DE"):
    t = text.lower()
    pts, why = 0.0, []
    if COUNTRY_BONUS.get(country):
        pts += COUNTRY_BONUS[country]; why.append(f"Ausland {country} (+{COUNTRY_BONUS[country]:g})")
    if country != "DE":
        plz = None  # ausländische PLZ nicht mit Haag-Nähe verwechseln
    for rx, p, label in RULES:
        if re.search(rx, t):
            pts += p
            why.append(f"{label} ({p:+g})")
    if plz:
        for i, pre in enumerate(NEAR_PLZ):
            if plz.startswith(pre):
                bonus = 3 if i < 3 else 2
                pts += bonus; why.append(f"Nähe Haag PLZ {plz} (+{bonus})"); break
    if posted and posted < THEFT_DATE:
        pts -= 6; why.append("vor Diebstahl eingestellt (-6)")
    return pts, why


# --- HTTP ---------------------------------------------------------------------
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def polite():
    time.sleep(random.uniform(1.5, 3.5))


def parse_date(s):
    s = s.strip().lower()
    today = dt.date.today()
    if s.startswith("heute"): return today
    if s.startswith("gestern"): return today - dt.timedelta(days=1)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b", s)
    if not m: return None
    y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    try: return dt.date(y, int(m[2]), int(m[1]))
    except ValueError: return None


def ka_search_url(query, region, page, cat=("hunde", "134")):
    reg = region if region else ""
    loc = "bayern/" if region else ""
    seite = f"seite:{page}/" if page > 1 else ""
    if not query:  # ganze Kategorie
        return f"https://www.kleinanzeigen.de/s-{cat[0]}/{loc}{seite}c{cat[1]}{reg}"
    slug = urllib.parse.quote(query.replace(" ", "-"))
    return f"https://www.kleinanzeigen.de/s-{cat[0]}/{loc}{seite}{slug}/k0c{cat[1]}{reg}"


def web_search(q):
    """DuckDuckGo-HTML: liefert Ergebnisse als Pseudo-Anzeigen."""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q, "kl": "de-de", "df": "m"})  # df=m: letzter Monat
    h = fetch(url)
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', h, re.S):
        href = html.unescape(m.group(1))
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        strip = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        out.append(dict(id="web:" + u, url=u, title=strip(m.group(2)), desc=strip(snip.group(1)) if snip else "",
                        img="", loc=urllib.parse.urlparse(u).hostname or "", plz=None, date="", price="",
                        source="web", country="DE", web_query=q))
    return out


def run_js_scraper(pages):
    js = BASE / "scraper.js"
    if not js.exists():
        return []
    print(f"  JS-Scraper (Playwright, {pages} Seiten/Quelle) läuft …")
    try:
        r = subprocess.run(["node", str(js), "--pages", str(pages)], cwd=BASE, capture_output=True, text=True, timeout=3600)
        print("  " + "\n  ".join(r.stdout.strip().splitlines()[-3:]))
    except Exception as e:
        print(f"  ! JS-Scraper: {e}", file=sys.stderr)
    try:
        return json.loads((BASE / "js_results.json").read_text())["ads"]
    except Exception:
        return []


def parse_ka_list(page_html):
    out = []
    for m in re.finditer(r'<article[^>]*data-adid="(\d+)"[^>]*data-href="([^"]+)"', page_html):
        adid, href = m.group(1), m.group(2)
        end = page_html.find("</article>", m.end())
        chunk = page_html[m.end():end]
        ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', chunk, re.S)
        title = desc = img = ""
        if ld:
            try:
                j = json.loads(ld.group(1))
                title, desc = j.get("title", ""), j.get("description", "")
                img = j.get("contentUrl", "")
            except json.JSONDecodeError:
                pass
        spans = [html.unescape(s).strip() for s in re.findall(r"<span>([^<]{2,80})</span>", chunk)]
        loc = spans[0] if spans else ""
        date_s = next((s for s in spans if re.search(r"heute|gestern|\d{2}\.\d{2}\.\d{4}", s, re.I)), "")
        price = re.search(r"(\d[\d.]*\s*€[^<]*|VB|Zu verschenken)", chunk)
        plz = (re.match(r"(\d{5})", loc) or [None, None])[1]
        out.append(dict(id=adid, url="https://www.kleinanzeigen.de" + href, title=html.unescape(title),
                        desc=html.unescape(desc), img=img, loc=loc, plz=plz, date=date_s,
                        price=price.group(1).strip() if price else ""))
    return out


def enrich_detail(ad):
    """Volltext + alle Bilder der Anzeige holen (nur für Kandidaten)."""
    try:
        h = fetch(ad["url"]); polite()
    except Exception as e:
        ad["detail_err"] = str(e); return
    d = re.search(r'id="viewad-description-text"[^>]*>(.*?)</p>', h, re.S)
    if d:
        ad["desc"] = html.unescape(re.sub(r"<[^>]+>", " ", d.group(1))).strip()
    ad["images"] = list(dict.fromkeys(re.findall(r'https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^"?\s]+', h)))[:8]
    details = re.findall(r'<li class="addetailslist--detail">\s*([^<]+?)\s*<span[^>]*>\s*([^<]+?)\s*</span>', h)
    ad["details"] = {html.unescape(k).strip(): html.unescape(v).strip() for k, v in details}


# --- State / Report -----------------------------------------------------------
def load_seen():
    try: return json.loads(SEEN_FILE.read_text())
    except Exception: return {}


def notify(title, msg, url=""):
    if NTFY_TOPIC:  # Push aufs Handy (ntfy-App, Topic abonnieren)
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode(), method="POST",
                                         headers={"Title": title.encode("ascii", "ignore").decode().strip() or "Treffer",
                                                  "Tags": "dog", "Priority": "high", **({"Click": url} if url else {})})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print(f"  ! ntfy: {e}", file=sys.stderr)
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title {json.dumps(title)} sound name "Glass"'], timeout=5)
        except Exception:
            pass


MANUAL_LINKS = [
    ("Kleinanzeigen – Vermisste/Entlaufene Tiere Bayern", "https://www.kleinanzeigen.de/s-bayern/entlaufen-zugelaufen/k0l5510"),
    ("Deine Tierwelt – Entlaufen/Zugelaufen/Gestohlen", "https://www.deine-tierwelt.de/kleinanzeigen/entlaufen-zugelaufen-gestohlen-c4126/"),
    ("Deine Tierwelt – Hunde 'Schäferhund Husky'", "https://www.deine-tierwelt.de/kleinanzeigen/hunde-c1/q-sch%C3%A4ferhund+husky/"),
    ("markt.de – Hunde Schäferhund Husky", "https://www.markt.de/tiermarkt/hunde/suche/?keywords=sch%C3%A4ferhund+husky"),
    ("quoka – Hunde Schäferhund Husky", "https://www.quoka.de/tiermarkt/hunde/?search1=sch%C3%A4ferhund+husky"),
    ("hundeanzeigen.de", "https://www.hundeanzeigen.de/"),
    ("Facebook Marketplace (Hunde, München)", "https://www.facebook.com/marketplace/munich/search?query=sch%C3%A4ferhund%20husky"),
    ("Facebook: Vermisste & gefundene Hunde", "https://de-de.facebook.com/vermisste.gefundene.Hunde/"),
    ("Tierschutzverein Rosenheim – Fundhunde", "https://www.tierschutzverein-rosenheim.de/index.php/tiere/vermisste-tiere/hunde"),
    ("TASSO – Suchmeldung / Fundtiere", "https://www.tasso.net/"),
    ("FINDEFIX (Dt. Tierschutzbund)", "https://www.findefix.com/"),
    ("🇦🇹 willhaben – Tiere (manuell, Filter Salzburg/OÖ)", "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=sch%C3%A4ferhund%20husky"),
    ("🇦🇹 Tierschutz Austria / Tierheim Salzburg Fundtiere", "https://www.tierheim-salzburg.at/"),
    ("🇨🇭 tutti.ch (blockt Bots – manuell)", "https://www.tutti.ch/de/q/suche?query=husky%20sch%C3%A4ferhund"),
    ("🇨🇭 anibis.ch (blockt Bots – manuell)", "https://www.anibis.ch/de/q/alle-kategorien?fts=husky"),
    ("🇨🇿 sbazar.cz", "https://www.sbazar.cz/hledej/husky%20ov%C4%8D%C3%A1k"),
    ("🇫🇷 leboncoin (blockt Bots – manuell)", "https://www.leboncoin.fr/recherche?category=28&text=husky%20berger"),
    ("Google Lens (Foto des Hundes hochladen)", "https://lens.google.com/"),
    ("Bing Visual Search", "https://www.bing.com/visualsearch"),
    ("Yandex Bildersuche (gut bei Tierfotos)", "https://yandex.com/images/"),
]


FLAGS = {'DE': '🇩🇪', 'AT': '🇦🇹', 'CZ': '🇨🇿', 'SK': '🇸🇰', 'PL': '🇵🇱', 'NL': '🇳🇱', 'IT': '🇮🇹', 'HU': '🇭🇺', 'CH': '🇨🇭'}

# --- Entfernung ab Haag i. OB --------------------------------------------------
HOME = (48.1617, 12.1797)
COUNTRY_CENTER = {"AT": (47.6, 14.1), "CZ": (49.8, 15.5), "SK": (48.7, 19.7), "PL": (52.1, 19.4),
                  "NL": (52.2, 5.5), "IT": (43.0, 12.5), "HU": (47.2, 19.4), "CH": (46.8, 8.2)}
_GEO = None


def _norm(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).strip()


def _geo():
    global _GEO
    if _GEO is None:
        try: _GEO = json.loads((BASE / "geo.json").read_text())
        except Exception: _GEO = {"plz": {}, "places": {}}
    return _GEO


def km_between(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def locate(a):
    """-> (km, exakt?) oder (None, False). DE/AT/CH über PLZ, sonst Ortsname im Text/URL, sonst Landesmitte."""
    g, cc = _geo(), a.get("country", "DE")
    if a.get("source") == "web":
        return None, False
    text = f"{a.get('loc', '')} {a.get('title', '')} {a.get('desc', '')[:400]}"
    if cc in ("DE", "AT", "CH"):
        for m in re.finditer(r"\b(\d{5})\b" if cc == "DE" else r"\b(\d{4})\b", a.get("loc") or text):
            p = g["plz"].get(f"{cc}:{m.group(1)}")
            if p: return round(km_between(HOME, p)), True
    # Ortsnamen: n-Gramme aus Ort, URL-Slug und Text (längste zuerst)
    slug = re.sub(r"[-_/]+", " ", urllib.parse.unquote(urllib.parse.urlparse(a.get("url", "")).path))
    words = re.findall(r"[^\W\d_][^\W\d_'.-]*", f"{a.get('loc', '')} {slug} {text}")
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            key = _norm(" ".join(words[i:i + n]))
            if len(key) < 4: continue
            p = g["places"].get(f"{cc}:{key}")
            if p and (n > 1 or words[i][0].isupper() or cc != "DE"):
                return round(km_between(HOME, p)), True
    if cc in COUNTRY_CENTER:
        return round(km_between(HOME, COUNTRY_CENTER[cc])), False
    return None, False


# --- Kategorie: Angebot / Fund / Vermisst / Presse ---------------------------------
RX_LOST = re.compile(r"vermisst|entlaufen|weggelaufen|ausgebüxt|gestohlen|geklaut|entwendet|belohnung|wer .{0,40}gesehen|gesehen hat|kontakt besitzer|"
                     r"such(e|en) (unseren|unsere|meinen|meine)|missing|pohřeš|ztracen|zaginą|zaginął|zaginęła|vermist|"
                     r"smarrit|scompars|elveszett|eltűnt|ukraden|skradzion", re.I)
RX_FOUND = re.compile(r"zugelaufen|aufgefunden|fundhund|fundtier|\bgefunden\b|streuner|nalezen|najden|"
                      r"znalezion|gevonden|trovato|talált|besitzer gesucht|halter gesucht", re.I)
CATS = {"angebot": "Angebote", "fund": "Fundtiere", "vermisst": "Vermisst-Meldungen", "presse": "Presse/Web"}


RX_OTHER_ANIMAL = re.compile(r"katze|kater\b|kätzchen|kitten|mieze|kaninchen|\bhase\b|meerschwein|vogel|papagei|sittich|"
                             r"pferd|pony|schildkröte|frettchen|hamster|kočk|kocour|\bkot(ek|ka)?\b|gatt[oi]|gattino|macska|\bpoes\b|\bkat\b", re.I)
RX_DOG = re.compile(r"hund|rüde|husky|schäfer|schaefer|welpe|\bdog\b|\bpes\b|psík|\bpies\b|psa\b|owczar|ovč|\bcane\b|kutya|\bhond|herder|pastore", re.I)


def is_other_animal(a):
    """Katzen & Co. aussortieren; Hundeanzeigen mit 'katzenverträglich' bleiben drin."""
    if a.get("source") == "web":
        return False
    t, text = a.get("title", ""), f"{a.get('title', '')} {a.get('desc', '')}"
    if RX_OTHER_ANIMAL.search(t) and not RX_DOG.search(t):
        return True
    return not RX_DOG.search(text) and bool(RX_OTHER_ANIMAL.search(text))


def classify(a):
    if a.get("source") == "web":
        return "presse"
    t, d = a.get("title", ""), a.get("desc", "")
    for txt in (t, d[:300]):
        f, l = RX_FOUND.search(txt), RX_LOST.search(txt)
        if f and (not l or f.start() < l.start()): return "fund"
        if l: return "vermisst"
    return "vermisst" if a.get("lost") else "angebot"


def mask(t):
    """Hundename nie im öffentlichen Report zeigen (auch nicht aus gescrapten Artikeln)."""
    return re.sub(rf"\b{re.escape(DOG_NAME)}\b", "•••", t or "", flags=re.I) if DOG_NAME else (t or "")


def write_report(ads, run_time, total=0):
    rows, counts, countries = [], {k: 0 for k in CATS}, set()
    for a in ads:
        a = {**a, "title": mask(a["title"]), "desc": mask(a["desc"])}
        counts[a["cat"]] += 1; countries.add(a.get("country", "DE"))
        imgs = a.get("images") or ([a["img"]] if a.get("img") else [])
        thumbs = "".join(f'<a href="{i}{"?rule=$_59.AUTO" if "kleinanzeigen" in i else ""}" target=_blank><img src="{i}{"?rule=$_2.AUTO" if "kleinanzeigen" in i else ""}" loading=lazy alt=""></a>'
                         for i in [re.sub(r"\?.*", "", x) if "kleinanzeigen" in x else x for x in imgs[:6]])
        cls = "hot" if a["score"] >= 10 else "warm" if a["score"] >= 7 else ""
        km = a.get("km")
        kmtxt = "" if km is None else (f"{km} km" if a.get("km_exact") else f"≈ {km} km")
        posted = parse_date(a.get("date") or "")
        rows.append(f"""<div class="card {cls}" data-cat="{a['cat']}" data-km="{'' if km is None else km}" data-score="{a['score']:.1f}" data-date="{posted.isoformat() if posted else ''}" data-cc="{a.get('country', 'DE')}" data-new="{1 if a.get('is_new') else 0}"><div class=s>{a['score']:.0f}</div><div class=body>
<h3>{'<b class=new>NEU</b> ' if a.get('is_new') else ''}<span class=src>{FLAGS.get(a.get('country', 'DE'), '')} {html.escape(a.get('source', ''))}</span> <a href="{html.escape(a['url'])}" target=_blank rel=noopener>{html.escape(a['title'] or '(ohne Titel)')}</a></h3>
<p class=meta>{f'<b class=km>{kmtxt}</b> · ' if kmtxt else ''}📍 {html.escape(mask(a['loc']))} · 📅 {html.escape(a['date'])} · 💶 {html.escape(a['price'])} · gesehen {a.get('first_seen', '')}</p>
<p class=why>{' · '.join(html.escape(w) for w in a['why'])}</p>
<p class=desc>{html.escape(a['desc'][:600])}</p><div class=th>{thumbs}</div></div></div>""")
    links = "".join(f'<li><a href="{u}" target=_blank rel=noopener>{html.escape(n)}</a></li>' for n, u in MANUAL_LINKS)
    tabs = "".join(f'<button class=tab data-cat="{k}">{v} <span>{counts[k]}</span></button>' for k, v in CATS.items())
    ccs = "".join(f'<label class=chip><input type=checkbox value="{c}" checked> {FLAGS.get(c, "")} {c}</label>' for c in sorted(countries, key=lambda c: (c != "DE", c)))
    REPORT.write_text(f"""<!doctype html><html lang=de><meta charset=utf-8><title>Hundesuche</title><meta name=robots content="noindex,nofollow">
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#222;--mute:#666;--line:#ddd;--acc:#2a6;--hot:#d33;--warm:#e9a200;--chip:#eee}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181b;--card:#22252a;--ink:#e8e6e3;--mute:#9aa0a6;--line:#3a3d42;--acc:#5c9;--chip:#2e3238}}}}
*{{box-sizing:border-box}}body{{font:15px system-ui;margin:0 auto;max-width:1000px;padding:16px;background:var(--bg);color:var(--ink)}}
a{{color:inherit}}.ref{{display:flex;gap:8px;overflow-x:auto}}.ref img{{height:160px;border-radius:8px}}
@media (min-width:760px){{.bar{{position:sticky;top:0;z-index:5}}}}
.bar{{background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.tab{{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:20px;padding:6px 12px;font:inherit;cursor:pointer}}
.tab.on{{background:var(--ink);color:var(--bg)}}.tab span{{opacity:.6;font-size:12px}}
select,input[type=search]{{font:inherit;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}}
.chip{{font-size:13px;background:var(--chip);border-radius:14px;padding:3px 8px;white-space:nowrap}}
.card{{display:flex;gap:12px;background:var(--card);border-radius:10px;padding:12px;margin:10px 0;border-left:6px solid var(--line)}}
.card[hidden]{{display:none!important}}.hot{{border-color:var(--hot)}}.warm{{border-color:var(--warm)}}.s{{font-size:22px;font-weight:700;min-width:36px}}
.body{{flex:1;min-width:0;overflow-wrap:anywhere}}h3{{margin:0 0 4px;font-size:16px}}.meta,.why{{color:var(--mute);font-size:13px;margin:2px 0}}.why{{color:var(--acc)}}
.km{{color:var(--ink)}}.desc{{white-space:pre-wrap;font-size:13px}}.th{{display:flex;flex-wrap:wrap}}.th img{{height:100px;margin:2px;border-radius:6px}}
.src{{font-size:12px;background:var(--chip);padding:1px 6px;border-radius:4px;color:var(--mute)}}.new{{color:#fff;background:var(--hot);padding:1px 6px;border-radius:4px}}
#count{{color:var(--mute);font-size:13px}}details{{margin:8px 0}}
</style>
<h1>🐕 Hundesuche</h1><p>Stand: {run_time} · {total} Anzeigen gescannt · {len(ads)} Kandidaten · gestohlen 19.08.2026, Haag i. OB (Entfernungen ab dort)</p>
<div class=ref><img src="reference/dog_1.jpg" alt="Foto des Hundes"><img src="reference/dog_2.jpg" alt="Portrait"><img src="reference/dog_3.jpg" alt="Portrait im Auto"></div>
<p><b>Merkmale:</b> Schäferhund-Husky-Rüde, 3–4 J., Bernstein-Augen, kleines pinselförmiges Haarbüschel im <b>rechten</b> Ohr, springt gern hoch, sieht jung aus.
<b>Masche:</b> junges Pärchen gab sich als „Tierschutz Bayern“ aus; brauner VW Caddy.</p>
<details><summary><b>Weitere Quellen (manuell prüfen)</b></summary><ul>{links}</ul></details>
<div class=bar>{tabs}
<select id=dist><option value=0>alle Entfernungen</option><option value=25>≤ 25 km</option><option value=50>≤ 50 km</option><option value=100>≤ 100 km</option><option value=200>≤ 200 km</option><option value=400>≤ 400 km</option></select>
<select id=sort><option value=score>Sortierung: Score</option><option value=km>Entfernung</option><option value=date>Neueste</option></select>
<label class=chip><input type=checkbox id=onlynew> nur neue</label>
<input type=search id=q placeholder="Suche im Text …" size=14>
<span>{ccs}</span><span id=count></span></div>
<div id=list>{''.join(rows)}</div>
<script>
(()=>{{
 const $=s=>document.querySelector(s),list=$('#list'),cards=[...list.children];
 let st={{cat:'angebot',dist:0,sort:'score',onlynew:false,q:''}};
 try{{Object.assign(st,JSON.parse(localStorage.getItem('hs')||'{{}}'))}}catch(e){{}}
 const save=()=>{{try{{localStorage.setItem('hs',JSON.stringify({{cat:st.cat,dist:st.dist,sort:st.sort}}))}}catch(e){{}}}};
 function render(){{
  const off=new Set([...document.querySelectorAll('.chip input[value]')].filter(i=>!i.checked).map(i=>i.value));
  const q=st.q.toLowerCase();
  const vis=cards.filter(c=>c.dataset.cat===st.cat&&!off.has(c.dataset.cc)&&(!st.onlynew||c.dataset.new==='1')
    &&(!st.dist||(c.dataset.km!==''&&+c.dataset.km<=st.dist))&&(!q||c.textContent.toLowerCase().includes(q)));
  const key={{score:c=>-c.dataset.score,km:c=>c.dataset.km===''?1e9:+c.dataset.km,date:c=>-(Date.parse(c.dataset.date)||0)}}[st.sort];
  vis.sort((a,b)=>key(a)-key(b)||b.dataset.score-a.dataset.score);
  cards.forEach(c=>c.hidden=true);vis.forEach(c=>{{c.hidden=false;list.appendChild(c)}});
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.cat===st.cat));
  $('#count').textContent=vis.length+' angezeigt';$('#dist').value=st.dist;$('#sort').value=st.sort;save();
 }}
 document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{{st.cat=t.dataset.cat;render()}});
 $('#dist').onchange=e=>{{st.dist=+e.target.value;render()}};$('#sort').onchange=e=>{{st.sort=e.target.value;render()}};
 $('#onlynew').onchange=e=>{{st.onlynew=e.target.checked;render()}};$('#q').oninput=e=>{{st.q=e.target.value;render()}};
 document.querySelectorAll('.chip input[value]').forEach(i=>i.onchange=render);
 render();
}})();
</script></html>""", encoding="utf-8")


# --- Main ---------------------------------------------------------------------
def scan_kleinanzeigen(pages, found):
    jobs = []
    for q in QUERIES:
        for rname, reg in REGIONS.items():
            if rname == "deutschland" and not re.search(r"husky|zugelaufen|gefunden|rüde", q):
                continue  # bundesweit nur die spezifischen Suchen
            jobs.append((q, rname, reg, ("hunde", "134"), pages))
    for q in LOST_QUERIES:  # Vermisste Tiere: Bayern tief, bundesweit mit Suchwort
        jobs.append((q, "bayern", "l5510", ("vermisste-tiere", "283"), pages * 2))
        if q: jobs.append((q, "deutschland", "", ("vermisste-tiere", "283"), pages))
    for q, rname, reg, cat, maxp in jobs:
        for p in range(1, maxp + 1):
            url = ka_search_url(q, reg, p, cat)
            try:
                ads = parse_ka_list(fetch(url))
            except Exception as e:
                print(f"  ! {q} [{rname}] S.{p}: {e}", file=sys.stderr); polite(); break
            print(f"  KA {cat[0][:8]:8} {q or '(alle)':26} [{rname:11}] S.{p}: {len(ads)}")
            new = 0
            for a in ads:
                a.update(source="kleinanzeigen", country="DE", lost=cat[1] == "283")
                if a["id"] not in found: new += 1
                found.setdefault(a["id"], a)
            polite()
            if len(ads) < 20 or new == 0: break


def run_once(min_score, detail_score, pages=PAGES, use_js=True, use_web=True):
    seen = load_seen()
    found = {}
    scan_kleinanzeigen(pages, found)
    if use_web:
        k = int(time.time() // 3600) % len(WEB_QUERIES)  # rotierend: pro Lauf andere Suchen
        for q in (WEB_QUERIES * 2)[k:k + WEB_PER_RUN]:
            try:
                res = web_search(q); print(f"  WEB {q[:60]:60} {len(res)}")
                for a in res: found.setdefault(a["id"], a)
                if not res: break  # vermutlich Captcha -> nächster Lauf
            except Exception as e:
                print(f"  ! web {q}: {e}", file=sys.stderr); break
            time.sleep(random.uniform(12, 25))
    if use_js:
        for a in run_js_scraper(pages):
            found.setdefault(a["id"], a)
    print(f"→ {len(found)} unique Anzeigen/Treffer, scoren …")

    cands = []
    for a in found.values():
        a.setdefault("source", "kleinanzeigen"); a.setdefault("country", "DE")
        posted = parse_date(a.get("date") or "")
        ctry = a["country"]
        extra = " zugelaufen" if a.get("lost") and re.search(r"hund|husky|sch.fer|rüde", (a["title"] + a["desc"]).lower()) else ""
        a["score"], a["why"] = score(a["title"] + " " + a["desc"] + extra, a.get("plz"), posted, ctry)
        prev = seen.get(a["id"])
        if a["source"] == "kleinanzeigen":
            if prev and prev.get("detail"):  # Detaildaten aus Cache übernehmen
                for k in ("desc", "images", "details"): a[k] = prev.get(k, a.get(k))
                a["score"], a["why"] = score(a["title"] + " " + a["desc"] + extra, a["plz"], posted)
            elif a["score"] >= detail_score:
                enrich_detail(a); a["detail"] = True
                a["score"], a["why"] = score(a["title"] + " " + a["desc"] + extra + " " + " ".join(f"{k} {v}" for k, v in a.get("details", {}).items()), a["plz"], posted)
        if is_other_animal(a):
            seen[a["id"]] = {"first_seen": prev["first_seen"] if prev else dt.datetime.now().strftime("%d.%m. %H:%M"), "title": a["title"], "score": -99}
            continue
        a["cat"] = classify(a)
        a["km"], a["km_exact"] = locate(a)
        a["is_new"] = a["id"] not in seen
        a["first_seen"] = prev["first_seen"] if prev else dt.datetime.now().strftime("%d.%m. %H:%M")
        if a["score"] >= min_score: cands.append(a)
        seen[a["id"]] = {k: a.get(k) for k in ("first_seen", "desc", "images", "details", "detail", "title", "score")}

    cands.sort(key=lambda a: (-a["score"], not a["is_new"]))
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False))
    now = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    write_report(cands, now, len(found))
    hot_new = [a for a in cands if a["is_new"] and a["score"] >= 8 and a["cat"] in ("angebot", "fund")]
    print(f"→ {len(cands)} Kandidaten ≥ {min_score}, davon {len(hot_new)} neue heiße. Report: {REPORT}")
    for a in cands[:25]:
        print(f"  {a['score']:5.1f} {a['cat'][:8]:8} {str(a['km'] or '?'):>5}km {'NEU ' if a['is_new'] else '    '}{a['source'][:13]:13} {mask(a['title'])[:55]:55} {a['loc'][:22]:22} {a['date'][:10]:10} {mask(a['url'])}")
    return hot_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="Minuten zwischen Durchläufen (0 = einmal)")
    ap.add_argument("--min-score", type=float, default=4)
    ap.add_argument("--detail-score", type=float, default=6, help="ab diesem Score Detailseite laden")
    ap.add_argument("--pages", type=int, default=PAGES, help="Seiten pro Suche (mehr = ältere Anzeigen)")
    ap.add_argument("--deep", action="store_true", help="Tiefenscan: 25 Seiten pro Suche, alles seit dem Diebstahl")
    ap.add_argument("--no-js", action="store_true", help="ohne Playwright-Scraper")
    ap.add_argument("--no-web", action="store_true", help="ohne Websuche")
    args = ap.parse_args()
    if args.deep: args.pages = 25
    first = not SEEN_FILE.exists()
    while True:
        print(f"\n=== Scan {dt.datetime.now():%H:%M:%S} ===")
        hot = run_once(args.min_score, args.detail_score, args.pages, not args.no_js, not args.no_web)
        if hot and not first:
            notify(f"Hundesuche: {len(hot)} neue(r) Treffer", "\n".join(f"[{a['score']:.0f}] {mask(a['title'])[:70]} – {a['loc']}" for a in hot[:5]), SITE_URL)
        first = False
        if not args.loop: break
        time.sleep(args.loop * 60 + random.randint(0, 90))


if __name__ == "__main__":
    main()
