"""Parsing: raw manifests / article HTML -> structured data.

Nothing here touches the network or builds pages.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path


# Geometry units: the page is 3,500,000 x 5,400,000. One text column is
# 368,750 wide with 50,000 gutters, so an article's column count can be derived.
PAGE_W, PAGE_H = 3_500_000, 5_400_000
COL_W, GUTTER = 368_750, 50_000

# Headline size (pt) by column span, used when the article has no DigitalStyle.
DEFAULT_HEAD_PT = {1: 13, 2: 17, 3: 24, 4: 28, 5: 34}

# Optional overrides: page-name prefix -> header label, e.g. {"SplSports": "Special Sports"}
SECTION_LABELS = {}


# ==========================================================================
# Data containers passed between the stages
# ==========================================================================
# ---- edition-level ---------------------------------------------------------
@dataclass
class Edition:
    """Everything we know about the edition after parsing both manifests."""

    issue: str
    label: str  # "Friday, 14 March 2025" (or "Edition 214446" if no date)
    meta: dict  # edition / date / weekday / dateshort / npages (for the masthead)
    has_layout: bool  # cciobjects.json was available
    page_order: list  # page ids in printed order
    pno_by_pid: dict  # page id -> printed page number
    cci_pages: dict  # page id -> page node from cciobjects.json
    pages: list  # [(page id, [article record, ...])] for the reading list
    articles: list  # flat list of article records, in reading order
    npages: int = 0


@dataclass
class BuildContext:
    """Shared, read-mostly state for the article workers."""

    base: str
    out: Path
    edition_label: str
    has_layout: bool
    lock: threading.Lock = field(default_factory=threading.Lock)
    css_seen: set = field(default_factory=set)


# ---- article-level ---------------------------------------------------------
@dataclass
class GalleryItem:
    n: int  # position in the article's gallery (used for alt text)
    ref: str  # image path relative to the OPS folder
    caption: str  # already-cleaned caption HTML ("" if none)


@dataclass
class ArticleImage:
    """A gallery image that was downloaded successfully."""

    n: int
    local: str  # file name under out/images/
    caption: str


@dataclass
class FetchedArticle:
    """An article page plus the local files it needs."""

    record: dict
    url: str
    raw: str
    images: list  # [ArticleImage]

    @property
    def thumb(self):
        return self.images[0].local if self.images else None


@dataclass
class ArticleResult:
    """What the page builder needs to know about an article after it was saved."""

    thumb: str | None  # "images/<file>" relative to the output root
    parsed: dict  # headline / deck / byline / body
    figs: dict  # local image name -> caption HTML


# ---- page layout -----------------------------------------------------------
@dataclass
class Photo:
    """A photo/graphic at its printed rectangle, relative to its article box."""

    local: str
    x: int
    y: int
    w: int
    h: int
    cap: str


@dataclass
class Panel:
    """Coloured text panel of a promo card, relative to its article box."""

    x: int
    y: int
    w: int
    h: int
    color: str
    rgb: tuple


@dataclass
class Card:
    """One article box on a printed page, ready to be turned into HTML."""

    id: str
    x: int
    y: int
    w: int
    h: int
    ncols: int
    headline_pt: int
    mode: str  # pcard | bleed | placed | simple
    promo: bool
    title_html: str
    title_text: str
    deck: str
    byline: str
    body_html: str
    photos: list  # [Photo]
    panel: Panel | None
    contour: list  # [(x%, y%)] polygon for non-rectangular outlines, else []
    promo_kicker: str
    promo_title: str
    promo_jump: str
    paragraphs: list  # [(css class, text)] source text for "placed" cards
    file_name: str  # saved article file, "" if unknown

    @property
    def area(self):
        return self.w * self.h


# ==========================================================================
# Small string helpers
# ==========================================================================
def strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def find_div(markup: str, open_pattern: str):
    """Locate the first <div ...> matching open_pattern together with its balanced close.

    Returns None if there is no match, else (start, inner_start, inner_end, outer_end);
    inner_end / outer_end are None when the divs are unbalanced."""
    m = re.search(open_pattern, markup)
    if not m:
        return None
    depth, pos = 1, m.end()
    for t in re.finditer(r"<div\b|</div\s*>", markup[pos:]):
        depth += 1 if t.group(0).startswith("<div") else -1
        if depth == 0:
            return m.start(), pos, pos + t.start(), pos + t.end()
    return m.start(), pos, None, None


def grab_div(markup: str, open_pattern: str) -> str:
    """Inner HTML of the first <div ...> matching open_pattern (balanced)."""
    span = find_div(markup, open_pattern)
    if span is None:
        return ""
    _, inner_start, inner_end, _ = span
    return markup[inner_start:] if inner_end is None else markup[inner_start:inner_end]


# ==========================================================================
# Article page: headline / body / gallery / stylesheets
# ==========================================================================
STYLESHEET_RE = re.compile(r'<link\s+rel="stylesheet"\s+href="([^"]+)"\s*/?>')
CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


def is_absolute(href: str) -> bool:
    return href.startswith(("http:", "https:", "//"))


def parse_article(raw: str) -> dict:
    """Pull headline / deck / byline / body out of an article page."""

    def unp(s):
        return re.sub(r"</?p\b[^>]*>", "", s or "").strip()

    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", raw, re.S)
    h2 = re.search(r"<h2\b[^>]*>(.*?)</h2>", raw, re.S)
    by = grab_div(raw, r'<div class="byline"[^>]*>')
    body = grab_div(raw, r'<div class="body[^"]*"[^>]*>')
    body = re.sub(r"<p>\s*</p>", "", body)
    body = re.sub(r'<span class="drop_initial"[^>]*></span>', "", body)
    return {
        "headline": unp(h1.group(1)) if h1 else "",
        "deck": unp(h2.group(1)) if h2 else "",
        "byline": " ".join(unp(p) for p in re.findall(r"<p>(.*?)</p>", by, re.S)),
        "body": body.strip(),
    }


# ---- stylesheets -----------------------------------------------------------
def stylesheet_hrefs(raw: str):
    """Relative stylesheet hrefs an article page links to (absolute ones are left alone)."""
    return [h for h in STYLESHEET_RE.findall(raw) if not is_absolute(h)]


def css_local_path(full_url: str, base: str, fallback_href: str = "") -> str:
    """Path of a stylesheet under out/assets/ (relative to the OPS folder if it lives there)."""
    if full_url.startswith(base):
        return strip_query(full_url[len(base):])
    return Path(strip_query(fallback_href or full_url)).name


def css_references(css_text: str):
    """Relative url(...) targets inside a stylesheet (fonts, images)."""
    return [
        ref
        for ref in CSS_URL_RE.findall(css_text)
        if not ref.startswith(("data:", "http:", "https:", "//", "#"))
    ]


# ---- gallery ---------------------------------------------------------------
def extract_gallery(raw: str):
    m = re.search(r"galleryItems\s*=\s*(\[.*?\])\s*</script>", raw, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def gallery_items(raw: str, record: dict):
    """Images of an article: the page's galleryItems, else Public/ children of the record."""
    items = extract_gallery(raw)
    if not items:  # fallback: JSON children that live under Public/
        for ch in record.get("children", []):
            r = ch.get("resource", "")
            if r.startswith("Public/"):
                items.append({"imageRef": r, "captionBody": ""})
    return [
        GalleryItem(n=n, ref=it["imageRef"], caption=clean_caption(it.get("captionBody", "")))
        for n, it in enumerate(items)
        if it.get("imageRef")
    ]


def clean_caption(cap):
    """Gallery caption HTML -> '<p>text <span class=cr>CREDIT / NAME</span></p>'."""
    if not cap:
        return ""
    credits = [
        strip_tags(x)
        for x in re.findall(
            r'<span class="cutline_(?:credit|name)"[^>]*>(.*?)</span>', cap, re.S
        )
    ]
    body = re.sub(
        r'<span class="cutline_(?:credit|name)"[^>]*>.*?</span>', "", cap, flags=re.S
    )
    text = re.sub(
        r"\s+", " ", strip_tags(re.sub(r"</p>\s*<p[^>]*>", " ", body))
    ).strip()
    cr = " / ".join(c for c in credits if c)
    if not text and not cr:
        return ""
    return (
        "<p>"
        + html.escape(text)
        + (f' <span class="cr">{html.escape(cr)}</span>' if cr else "")
        + "</p>"
    )


# ==========================================================================
# Edition: articles.json + cciobjects.json
# ==========================================================================
def issue_from_url(url: str) -> str:
    m = re.search(r"/issues/(\d+)/", url)
    return m.group(1) if m else "edition"


def base_from_url(url: str) -> str:
    """Folder that holds articles.json (every resource path is relative to it)."""
    return url.rsplit("/", 1)[0] + "/"


def edition_name_from_url(url: str) -> str:
    m = re.search(r"/th/th_([a-z0-9_]+)/", url)
    return m.group(1).replace("_", " ").title() + " Edition" if m else ""


def parse_edition(url: str, manifest: list, cci) -> Edition:
    """Group articles by page and work out dates / page numbers.

    `manifest` is articles.json, `cci` is cciobjects.json (or None if unavailable).
    Article records are annotated in place with page_no, body_len and file_name."""
    issue = issue_from_url(url)
    label = f"Edition {issue}"
    meta = {
        "edition": edition_name_from_url(url),
        "date": "",
        "weekday": "",
        "dateshort": "",
        "npages": "",
    }

    page_order, pno_by_pid, cci_pages = [], {}, {}
    if cci:
        d = cci.get("attributes", {}).get("PubDateDate", "")
        try:
            dt = datetime.date.fromisoformat(d)
            label = dt.strftime("%A, %d %B %Y")
            meta.update(
                weekday=dt.strftime("%A"),
                dateshort=f"{dt.strftime('%B')} {dt.day}, {dt.year}",
                date=f"{dt.strftime('%A, %B')} {dt.day}, {dt.year}",
            )
        except ValueError:
            pass
        for i, pg in enumerate(cci.get("children", []), 1):
            if pg.get("kind") != "Page":
                continue
            page_order.append(pg["id"])
            cci_pages[pg["id"]] = pg
            p = pg.get("attributes", {}).get("Page", "")
            pno_by_pid[pg["id"]] = int(p) if p.isdigit() else i

    # group articles by page
    page_map = {}
    for a in manifest:
        if a.get("kind") != "Article" or not a.get("resource"):
            continue
        page_map.setdefault(a["pageId"], []).append(a)
    ordered_pids = [p for p in page_order if p in page_map] + [
        p for p in page_map if p not in page_order
    ]
    pages = [(pid, page_map[pid]) for pid in ordered_pids]

    flat = []
    for n, (pid, arts) in enumerate(pages, 1):
        pno = pno_by_pid.get(pid, n)
        for a in arts:
            a["page_no"] = pno
            a["body_len"] = len(a.get("bodyText") or "")
            a["file_name"] = a["resource"].replace("+", "_")  # '+' is awkward in filenames
            flat.append(a)

    npages = max(pno_by_pid.values()) if pno_by_pid else len(page_order)
    if cci:
        meta["npages"] = npages

    return Edition(
        issue=issue,
        label=label,
        meta=meta,
        has_layout=bool(cci),
        page_order=page_order,
        pno_by_pid=pno_by_pid,
        cci_pages=cci_pages,
        pages=pages,
        articles=flat,
        npages=npages,
    )


# ==========================================================================
# Page geometry: positions, outlines, colours
# ==========================================================================
def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def geom(attrs):
    """(x, y, width, height) of an object in page units."""
    return (
        to_int(attrs.get("ObjectXPos")),
        to_int(attrs.get("ObjectYPos")),
        to_int(attrs.get("ObjectWidth")),
        to_int(attrs.get("ObjectHeight")),
    )


def contour_polygon(attrs, ax, ay, aw, ah):
    """Outline of a non-rectangular article (L-shaped stories etc.) as
    [(x%, y%)] relative to its box, or [] for a plain rectangle."""
    try:
        nums = [int(float(v)) for v in attrs.get("Contour", "").split(",") if v.strip()]
    except ValueError:
        return []
    if len(nums) < 8 or len(nums) % 2:
        return []
    pts = list(zip(nums[0::2], nums[1::2]))
    simp = []
    for p in pts:
        if not simp or simp[-1] != p:
            simp.append(p)
    if len(simp) > 1 and simp[0] == simp[-1]:
        simp.pop()
    poly = []
    n = len(simp)
    for i, p in enumerate(simp):  # drop collinear mid-points
        a, b = simp[i - 1], simp[(i + 1) % n]
        if (a[0] == p[0] == b[0]) or (a[1] == p[1] == b[1]):
            continue
        poly.append(p)
    if len(poly) < 3:
        return []
    if len({p[0] for p in poly}) <= 2 and len({p[1] for p in poly}) <= 2:
        return []  # plain rectangle
    return [((x - ax) / aw * 100, (y - ay) / ah * 100) for x, y in poly]


def headline_pt(attrs, ncols):
    m = re.search(r"(\d+)\s*pt", attrs.get("DigitalStyle", ""))
    if m:
        return max(9, min(int(m.group(1)), 60))
    return DEFAULT_HEAD_PT.get(ncols, 40)


def parse_rgb(s):
    m = re.match(r"\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s or "")
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.match(r"\s*#([0-9a-fA-F]{6})\s*$", s or "")
    if m:
        v = m.group(1)
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))
    return None


# ==========================================================================
# Page layout: one printed article box -> Card
# ==========================================================================
# ---- page header -----------------------------------------------------------
def page_desk(attrs):
    return attrs.get("LayoutDeskCont", "").replace("TH_", "")


def page_section(attrs):
    """Label for the running header: page-name prefix (with overrides), else the desk."""
    name = (attrs.get("PageNameCont") or attrs.get("Name") or "").split("_")[0]
    return SECTION_LABELS.get(name, name) or page_desk(attrs)


# ---- text pieces -----------------------------------------------------------
def split_promo(rec):
    """Promo teasers store 'KICKER Headline ... SECTIONPAGE n' run together; pull them apart."""
    teaser = (rec.get("teaserText") or "").strip()
    body = (rec.get("bodyText") or "").strip()
    jump = ""
    m = re.search(r"([A-Z]{2,})\s*PAG\w*\s*(\d+)\s*$", body)
    if m:
        jump = f"{m.group(1)} » PAGE {m.group(2)}"
        body = body[: m.start()].strip()
    if teaser and body.startswith(teaser):
        body = body[len(teaser) :].strip()
    return teaser, body, jump


def body_paragraphs(body_html):
    """[(css_class, plain_text)] for each non-empty <p> of an article body."""
    paras = []
    for attrs, inner in re.findall(r"<p\b([^>]*)>(.*?)</p>", body_html or "", re.S):
        t = re.sub(r"\s+", " ", strip_tags(inner)).strip()
        if t:
            m = re.search(r'class="([^"]*)"', attrs)
            paras.append((m.group(1) if m else "", t))
    return paras


# ---- photos / panels -------------------------------------------------------
def collect_photos(c, ax, ay, aw, ah, figs, images_dir: Path):
    """Photos/graphics of an article at their exact printed rectangles (clamped to the article box)."""
    items = []
    for ch in c.get("children", []):
        if ch.get("kind") not in ("Photo", "Graphics"):
            continue
        ref = next(
            (
                x["reference"]
                for x in ch.get("content", [])
                if x.get("reference", "").startswith("Public/")
            ),
            None,
        )
        if not ref:
            continue
        local = Path(strip_query(ref)).name
        if not (images_dir / local).exists():
            continue
        px, py, pw, ph = geom(ch["attributes"])
        x0, y0 = max(px, ax), max(py, ay)
        x1, y1 = min(px + pw, ax + aw), min(py + ph, ay + ah)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        items.append(
            Photo(local=local, x=x0 - ax, y=y0 - ay, w=x1 - x0, h=y1 - y0, cap=figs.get(local, ""))
        )
    items.sort(key=lambda p: (p.y, p.x))
    return items


def find_panel(c, ax, ay):
    """Coloured text panel of a promo card: a Text child that carries a PageColor."""
    for ch in c.get("children", []):
        if ch.get("kind") != "Text":
            continue
        color = ch["attributes"].get("PageColor", "")
        rgb = parse_rgb(color)
        if not rgb:
            continue
        px, py, pw, ph = geom(ch["attributes"])
        if pw > 0 and ph > 0:
            return Panel(x=px - ax, y=py - ay, w=pw, h=ph, color=color, rgb=rgb)
    return None


# ---- the card --------------------------------------------------------------
def parse_card(c, rec, res, images_dir: Path):
    """c: Article node of cciobjects.json; rec: its articles.json record;
    res: ArticleResult from saving the article page. Returns a Card or None (zero-size)."""
    a = c["attributes"]
    ax, ay, aw, ah = geom(a)
    if aw <= 0 or ah <= 0:
        return None
    parsed = res.parsed if res else {}
    figs = res.figs if res else {}
    rec = rec or {}

    title_html = parsed.get("headline") or html.escape(
        rec.get("headline") or rec.get("teaserText") or a.get("Name", "")
    )
    promo = not parsed.get("headline")
    body = parsed.get("body", "")
    if (
        not body
        and promo
        and rec.get("bodyText")
        and rec.get("bodyText") != rec.get("teaserText")
    ):
        body = "<p>" + html.escape(rec["bodyText"]) + "</p>"

    ncols = max(1, round((aw + GUTTER) / (COL_W + GUTTER)))
    photos = collect_photos(c, ax, ay, aw, ah, figs, images_dir)
    panel = find_panel(c, ax, ay)

    if promo and panel:
        mode = "pcard"
    elif promo and photos and photos[0].h >= 0.8 * ah:
        mode = "bleed"
    elif photos:
        mode = "placed"
    else:
        mode = "simple"

    kicker = ptitle = jump = ""
    if mode == "pcard":
        teaser, ptxt, jump = split_promo(rec)
        ptitle = ptxt or teaser
        kicker = teaser if ptxt and teaser else ""

    return Card(
        id=c["id"],
        x=ax,
        y=ay,
        w=aw,
        h=ah,
        ncols=ncols,
        headline_pt=11 if promo else headline_pt(a, ncols),
        mode=mode,
        promo=promo,
        title_html=title_html,
        title_text=strip_tags(title_html),
        deck=parsed.get("deck", ""),
        byline=parsed.get("byline", ""),
        body_html=body,
        photos=photos,
        panel=panel,
        contour=contour_polygon(a, ax, ay, aw, ah),
        promo_kicker=kicker,
        promo_title=ptitle,
        promo_jump=jump,
        paragraphs=body_paragraphs(body) if mode == "placed" else [],
        file_name=rec.get("file_name", ""),
    )
