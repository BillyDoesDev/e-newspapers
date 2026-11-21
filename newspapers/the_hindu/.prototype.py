#!/usr/bin/env python3
"""
Rebuild a single edition of The Hindu e-paper as an offline-browsable folder.

Personal-archive use only: the content is The Hindu's copyright, not public domain.
Don't republish it. Every saved article/page carries a source credit.

Inputs (both live next to each other in the edition's OPS folder):
    articles.json   -> article text/metadata + resource names
    cciobjects.json -> page geometry: x/y/width/height of every article and photo

Only resources under Public/ are requested. The per-page backdrop image and PDF listed in
cciobjects.json have no Public/ prefix, so they are never fetched: the masthead / running
header (page number, section, date) are drawn from the page data instead, and PDFs are
rendered locally from the rebuilt pages (--pdf).

Usage:
    python hindu_epaper_archive.py
    python hindu_epaper_archive.py --url <.../OPS/articles.json> --out edition_214446
    python hindu_epaper_archive.py --pdf          # render PDFs locally (needs playwright; pypdf merges)
    python hindu_epaper_archive.py --no-header    # skip the drawn masthead / running header

Output layout:
    out/index.html            reading list: pages in order, articles grouped by page
    out/pages/page-NN.html    reconstructed page: articles at their printed positions
    out/pages/img/            Public page previews (for the "original scan" toggle)
    out/pdf/                  page-NN.pdf and edition.pdf, only with --pdf
    out/articles/*.html       each article, CSS/images rewritten to local copies
    out/assets/, out/images/  CSS and article images
    out/articles.json, out/cciobjects.json   raw manifests, kept for reference

Requires: pip install requests
Optional (--pdf): pip install playwright pypdf && playwright install chromium
"""

import argparse
import datetime
import html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_URL = (
    "https://epaper.thehindu.com/ccidist-ws/th/th_international/"
    "issues/214446/OPS/articles.json"
)
UA = "Mozilla/5.0 (personal-archive script)"
DELAY = 0.15  # seconds between requests per worker, be polite

# Geometry units: the page is 3,500,000 x 5,400,000. One text column is
# 368,750 wide with 50,000 gutters, so an article's column count can be derived.
PAGE_W, PAGE_H = 3_500_000, 5_400_000
COL_W, GUTTER = 368_750, 50_000
PT = 3528  # 1 pt in page units (1 unit = 0.0001 mm)


# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------
def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


SESSION = make_session()


def http_get(url, timeout=30):
    """GET and return bytes. Retries/backoff are handled by the session adapter;
    4xx (e.g. 404) raise immediately via raise_for_status()."""
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    time.sleep(DELAY)
    return r.content


def download(url, dest: Path):
    """Download to dest unless it already exists. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        data = http_get(url)
    except Exception as e:  # noqa: BLE001
        print(f"  ! failed {url}: {e}", file=sys.stderr)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------
def remove_div(markup: str, open_pattern: str):
    """Remove the first <div ...> matching open_pattern, with its balanced
    contents. Returns (new_markup, start_index) so a replacement can be inserted."""
    m = re.search(open_pattern, markup)
    if not m:
        return markup, None
    depth, pos = 1, m.end()
    for t in re.finditer(r"<div\b|</div\s*>", markup[pos:]):
        depth += 1 if t.group(0).startswith("<div") else -1
        if depth == 0:
            end = pos + t.end()
            return markup[: m.start()] + markup[end:], m.start()
    return markup, None  # unbalanced; leave untouched


def grab_div(markup: str, open_pattern: str) -> str:
    """Inner HTML of the first <div ...> matching open_pattern (balanced)."""
    m = re.search(open_pattern, markup)
    if not m:
        return ""
    depth, pos = 1, m.end()
    for t in re.finditer(r"<div\b|</div\s*>", markup[pos:]):
        depth += 1 if t.group(0).startswith("<div") else -1
        if depth == 0:
            return markup[pos : pos + t.start()]
    return markup[pos:]


def extract_gallery(markup: str):
    m = re.search(r"galleryItems\s*=\s*(\[.*?\])\s*</script>", markup, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_article(raw: str) -> dict:
    """Pull headline / deck / byline / body out of an article page."""

    def unp(s):
        return re.sub(r"</?p\b[^>]*>", "", s or "").strip()

    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", raw, re.DOTALL)
    h2 = re.search(r"<h2\b[^>]*>(.*?)</h2>", raw, re.DOTALL)
    by = grab_div(raw, r'<div class="byline"[^>]*>')
    body = grab_div(raw, r'<div class="body[^"]*"[^>]*>')
    body = re.sub(r"<p>\s*</p>", "", body)
    body = re.sub(r'<span class="drop_initial"[^>]*></span>', "", body)
    return {
        "headline": unp(h1.group(1)) if h1 else "",
        "deck": unp(h2.group(1)) if h2 else "",
        "byline": " ".join(unp(p) for p in re.findall(r"<p>(.*?)</p>", by, re.DOTALL)),
        "body": body.strip(),
    }


CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


def fetch_css_tree(css_url: str, assets: Path, seen: set):
    """Download a stylesheet plus anything it references via url()."""
    rel = (
        strip_query(css_url[len(BASE) :])
        if css_url.startswith(BASE)
        else Path(strip_query(css_url)).name
    )
    if rel in seen:
        return
    seen.add(rel)
    dest = assets / rel
    if not download(css_url, dest):
        return
    try:
        css = dest.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return
    for ref in CSS_URL_RE.findall(css):
        if ref.startswith(("data:", "http:", "https:", "//", "#")):
            continue
        sub = urljoin(css_url, ref)
        sub_rel = strip_query(sub[len(BASE) :]) if sub.startswith(BASE) else None
        if sub_rel and sub_rel not in seen:
            seen.add(sub_rel)
            download(sub, assets / sub_rel)


# --------------------------------------------------------------------------
# Article processing
# --------------------------------------------------------------------------
FIG_STYLE = """
<style>
 .archive-nav{font:14px/1.4 system-ui,sans-serif;padding:8px 12px;margin:0 0 12px;
   background:#f3f3f3;border-bottom:1px solid #ddd;display:flex;gap:16px;flex-wrap:wrap}
 .archive-nav a{text-decoration:none;color:#0b4f8a}
 .archive-fig{margin:1em 0}
 .archive-fig img{max-width:100%;height:auto;display:block}
 .archive-fig figcaption{font:13px/1.4 system-ui,sans-serif;color:#444;margin-top:4px}
 .archive-credit{font:12px/1.4 system-ui,sans-serif;color:#777;margin:2em 0 1em;
   border-top:1px solid #ddd;padding-top:8px}
</style>
"""


def process_article(art, idx, ordered, ctx):
    """Download one article + its images and write a localised copy.
    Returns {"thumb", "parsed", "figs"} or None on failure."""
    base, out = ctx["base"], ctx["out"]
    res = art["resource"]
    url = urljoin(base, res)
    try:
        raw = http_get(url).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  ! article {res}: {e}", file=sys.stderr)
        return None

    # 1. stylesheets -> local assets, rewrite hrefs
    def css_sub(m):
        href = m.group(1)
        if href.startswith(("http:", "https:", "//")):
            return m.group(0)
        full = urljoin(url, href)
        with ctx["lock"]:
            fetch_css_tree(full, out / "assets", ctx["css_seen"])
        rel = (
            strip_query(full[len(base) :])
            if full.startswith(base)
            else Path(strip_query(href)).name
        )
        return f'<link rel="stylesheet" href="../assets/{quote(rel)}">'

    page = re.sub(r'<link\s+rel="stylesheet"\s+href="([^"]+)"\s*/?>', css_sub, raw)

    # 2. gallery images -> local files + static <figure>s
    items = extract_gallery(raw)
    figures, thumb, figs = [], None, {}
    if not items:  # fallback: JSON children that live under Public/
        for ch in art.get("children", []):
            r = ch.get("resource", "")
            if r.startswith("Public/"):
                items.append({"imageRef": r, "captionBody": ""})
    for n, it in enumerate(items):
        ref = it.get("imageRef", "")
        if not ref:
            continue
        local = Path(strip_query(ref)).name
        if download(urljoin(base, ref), out / "images" / local):
            if thumb is None:
                thumb = f"images/{quote(local)}"
            cap = clean_caption(it.get("captionBody", ""))
            figs[local] = cap
            figures.append(
                f'<figure class="archive-fig"><img src="../images/{quote(local)}" '
                f'alt="Article image {n + 1}">'
                + (f"<figcaption>{cap}</figcaption>" if cap else "")
                + "</figure>"
            )
    figs_html = "\n".join(figures)

    # 3. swap the JS-driven gallery container for the static figures
    page, pos = remove_div(page, r'<div class="image-gallery-container"[^>]*>')
    if pos is not None:
        page = page[:pos] + figs_html + page[pos:]
    elif figs_html:  # no container found: put images after the headline block
        page = re.sub(
            r"(</h2>|</h1>)(?!.*</h[12]>)",
            r"\1" + figs_html.replace("\\", "\\\\"),
            page,
            count=1,
            flags=re.DOTALL,
        )

    # 4. drop scripts and og: meta (they need the live site), add nav + credit
    page = re.sub(r"<script\b.*?</script>", "", page, flags=re.DOTALL)
    page = re.sub(r'<meta property="og:[^>]*>\s*', "", page)
    prev_l = (
        f'<a href="{ordered[idx - 1]["file_name"]}">&larr; Previous</a>'
        if idx > 0
        else ""
    )
    next_l = (
        f'<a href="{ordered[idx + 1]["file_name"]}">Next &rarr;</a>'
        if idx < len(ordered) - 1
        else ""
    )
    page_l = (
        f'<a href="../pages/page-{art["page_no"]:02d}.html#a-{art["id"]}">Page {art["page_no"]}</a>'
        if ctx["layout"]
        else f"<span>Page {art['page_no']}</span>"
    )
    nav = (
        f'<div class="archive-nav"><a href="../index.html">Index</a>'
        f"{page_l}{prev_l}{next_l}</div>"
    )
    credit = (
        '<div class="archive-credit">Source: The Hindu e-paper '
        f"({html.escape(ctx['edition_label'])}). &copy; The Hindu. "
        "Archived copy for personal use.</div>"
    )
    page = page.replace("</head>", FIG_STYLE + "</head>", 1)
    page = re.sub(r"(<body[^>]*>)", r"\1" + nav, page, count=1)
    page = page.replace("</body>", credit + "</body>", 1)

    (out / "articles" / art["file_name"]).write_text(page, encoding="utf-8")
    return {"thumb": thumb, "parsed": parse_article(raw), "figs": figs}


# --------------------------------------------------------------------------
# Page layout (uses cciobjects.json geometry)
# --------------------------------------------------------------------------
def to_int(v, default=0):
    try:
        return int(float(v))
    except TypeError, ValueError:
        return default


def geom(attrs):
    return (
        to_int(attrs.get("ObjectXPos")),
        to_int(attrs.get("ObjectYPos")),
        to_int(attrs.get("ObjectWidth")),
        to_int(attrs.get("ObjectHeight")),
    )


def contour_clip(attrs, ax, ay, aw, ah):
    """CSS clip-path for non-rectangular article outlines (L-shaped stories etc.)."""
    try:
        nums = [int(float(v)) for v in attrs.get("Contour", "").split(",") if v.strip()]
    except ValueError:
        return ""
    if len(nums) < 8 or len(nums) % 2:
        return ""
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
        return ""
    if len({p[0] for p in poly}) <= 2 and len({p[1] for p in poly}) <= 2:
        return ""  # plain rectangle
    coords = ", ".join(
        f"{(x - ax) / aw * 100:.3f}% {(y - ay) / ah * 100:.3f}%" for x, y in poly
    )
    return f"clip-path:polygon({coords});"


DEFAULT_HEAD_PT = {1: 13, 2: 17, 3: 24, 4: 28, 5: 34}


def headline_pt(attrs, ncols):
    m = re.search(r"(\d+)\s*pt", attrs.get("DigitalStyle", ""))
    if m:
        return max(9, min(int(m.group(1)), 60))
    return DEFAULT_HEAD_PT.get(ncols, 40)


SECTION_LABELS = {}  # optional overrides: page-name prefix -> header label, e.g. {"SplSports": "Special Sports"}
PAGE_MM = (350, 540)  # printed page size: 3,500,000 x 5,400,000 units of 0.0001 mm


def parse_rgb(s):
    m = re.match(r"\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s or "")
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.match(r"\s*#([0-9a-fA-F]{6})\s*$", s or "")
    if m:
        v = m.group(1)
        return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))
    return None


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
    for attrs, inner in re.findall(
        r"<p\b([^>]*)>(.*?)</p>", body_html or "", re.DOTALL
    ):
        t = re.sub(r"\s+", " ", strip_tags(inner)).strip()
        if t:
            m = re.search(r'class="([^"]*)"', attrs)
            paras.append((m.group(1) if m else "", t))
    return paras


def clean_caption(cap):
    """Gallery caption HTML -> '<p>text <span class=cr>CREDIT / NAME</span></p>'."""
    if not cap:
        return ""
    credits = [
        strip_tags(x)
        for x in re.findall(
            r'<span class="cutline_(?:credit|name)"[^>]*>(.*?)</span>', cap, re.DOTALL
        )
    ]
    body = re.sub(
        r'<span class="cutline_(?:credit|name)"[^>]*>.*?</span>',
        "",
        cap,
        flags=re.DOTALL,
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


def collect_photos(c, ax, ay, aw, ah, figs, out):
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
        if not (out / "images" / local).exists():
            continue
        px, py, pw, ph = geom(ch["attributes"])
        x0, y0 = max(px, ax), max(py, ay)
        x1, y1 = min(px + pw, ax + aw), min(py + ph, ay + ah)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        items.append(
            {
                "local": local,
                "x": x0 - ax,
                "y": y0 - ay,
                "w": x1 - x0,
                "h": y1 - y0,
                "cap": figs.get(local, ""),
            }
        )
    items.sort(key=lambda d: (d["y"], d["x"]))
    return items


def card_html(c, rec, res, out):
    a = c["attributes"]
    ax, ay, aw, ah = geom(a)
    if aw <= 0 or ah <= 0:
        return "", 0
    aid = c["id"]
    parsed = (res or {}).get("parsed") or {}
    figs = (res or {}).get("figs") or {}
    rec = rec or {}

    title_html = parsed.get("headline") or html.escape(
        rec.get("headline") or rec.get("teaserText") or a.get("Name", "")
    )
    promo = not parsed.get("headline")
    deck = parsed.get("deck", "")
    byline = parsed.get("byline", "")
    body = parsed.get("body", "")
    if (
        not body
        and promo
        and rec.get("bodyText")
        and rec.get("bodyText") != rec.get("teaserText")
    ):
        body = "<p>" + html.escape(rec["bodyText"]) + "</p>"

    ncols = max(1, round((aw + GUTTER) / (COL_W + GUTTER)))
    hp = 11 if promo else headline_pt(a, ncols)
    photos = collect_photos(c, ax, ay, aw, ah, figs, out)

    # coloured text panel (promo cards): a Text child that carries a PageColor
    panel = None
    for ch in c.get("children", []):
        if ch.get("kind") == "Text" and parse_rgb(
            ch["attributes"].get("PageColor", "")
        ):
            px, py, pw, ph = geom(ch["attributes"])
            if pw > 0 and ph > 0:
                panel = (px - ax, py - ay, pw, ph, ch["attributes"]["PageColor"])
                break

    if promo and panel:
        mode = "pcard"
    elif promo and photos and photos[0]["h"] >= 0.8 * ah:
        mode = "bleed"
    elif photos:
        mode = "placed"
    else:
        mode = "simple"

    # every photo sits at its real printed rectangle; img is sized to exactly that box
    fig_html = "".join(
        f'<figure class="ph" style="left:{p["x"] / aw * 100:.3f}%;top:{p["y"] / ah * 100:.3f}%;'
        f'width:{p["w"] / aw * 100:.3f}%"><img src="../images/{quote(p["local"])}" alt="" '
        f'style="aspect-ratio:{p["w"]}/{p["h"]}">'
        + (f"<figcaption>{p['cap']}</figcaption>" if p["cap"] else "")
        + "</figure>"
        for p in photos
    )

    head_style = ""
    if photos:  # photo hugging the top: keep the headline out of its way
        p = photos[0]
        if p["y"] < 0.12 * ah and p["w"] <= 0.65 * aw:
            if p["x"] + p["w"] / 2 < aw / 2:
                head_style = (
                    f"margin-left:{(p['x'] + p['w'] + GUTTER) / aw * 100:.3f}%;"
                )
            else:
                head_style = f"width:{max(10, (p['x'] - GUTTER) / aw * 100):.3f}%;"
    head_div = (
        f'<div class="head" style="{head_style}"><h2 class="hl">{title_html}</h2>'
        + (f'<div class="deck">{deck}</div>' if deck and not promo else "")
        + (f'<div class="by">{byline}</div>' if byline and not promo else "")
        + "</div>"
    )

    if mode == "bleed":
        inner = fig_html
    elif mode == "pcard":
        teaser, ptxt, jump = split_promo(rec)
        rgb = parse_rgb(panel[4])
        lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
        fg = "#fff" if lum < 0.55 else "#111"
        title = ptxt or teaser
        kick = (
            f'<div class="kick">{html.escape(teaser)}</div>' if ptxt and teaser else ""
        )
        jmp = f'<div class="jump">{html.escape(jump)}</div>' if jump else ""
        inner = (
            fig_html
            + f'<div class="pp" style="left:{panel[0] / aw * 100:.3f}%;top:{panel[1] / ah * 100:.3f}%;'
            f"width:{panel[2] / aw * 100:.3f}%;height:{panel[3] / ah * 100:.3f}%;"
            f'background:{html.escape(panel[4], quote=True)};color:{fg}">'
            f'<div class="pin">{kick}<div class="tt">{html.escape(title)}</div>{jmp}</div></div>'
        )
    elif mode == "placed":
        src = "".join(
            f'<p class="{html.escape(cl, quote=True)}">{html.escape(t)}</p>'
            for cl, t in body_paragraphs(body)
        )
        inner = (
            head_div
            + fig_html
            + '<div class="flow"></div>'
            + f'<div class="src">{src}</div>'
        )
    else:
        inner = (
            f'<div class="inner">{head_div}'
            + (f'<div class="txt">{body}</div>' if body else "")
            + "</div>"
        )

    href = (
        f"../articles/{quote(rec.get('file_name', ''))}"
        if rec.get("file_name")
        else "#"
    )
    title_txt = strip_tags(title_html)
    style = (
        f"left:{ax / PAGE_W * 100:.4f}%;top:{ay / PAGE_H * 100:.4f}%;"
        f"width:{aw / PAGE_W * 100:.4f}%;height:{ah / PAGE_H * 100:.4f}%;--nc:{ncols};--hp:{hp};"
        + contour_clip(a, ax, ay, aw, ah)
    )
    card = (
        f'<article class="art {mode}{" promo" if promo else ""}" id="a-{html.escape(aid)}" '
        f'data-nc="{ncols}" data-gut="{GUTTER / aw:.5f}" style="{style}">'
        f'<a class="hot" href="{href}" title="{html.escape(title_txt, quote=True)}">'
        f'<span class="sr">{html.escape(title_txt)}</span></a>' + inner + "</article>"
    )
    return card, aw * ah


PAGE_CSS = r"""
:root{--paper:#fff;--ink:#111}
*{box-sizing:border-box}
body{margin:0;background:#d8d6d0;font:15px system-ui,sans-serif;color:var(--ink)}
.bar{position:sticky;top:0;z-index:10;display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  padding:8px 14px;background:#222;color:#eee;font-size:14px}
.bar a,.bar button{color:#fff;background:#3a3a3a;border:1px solid #555;border-radius:4px;
  padding:3px 10px;text-decoration:none;font:inherit;cursor:pointer}
.bar a:hover,.bar button:hover{background:#505050}
.bar .sp{flex:1}.bar .t{opacity:.8}
.wrap{overflow-x:auto;padding:14px}
.sheet{position:relative;margin:0 auto;width:var(--w,1200px);aspect-ratio:3500000/5400000;
  container-type:inline-size;background:var(--paper);box-shadow:0 2px 14px rgba(0,0,0,.35)}
.sheet .scan{position:absolute;inset:0;width:100%;height:100%;display:none;z-index:1}

/* ---- running header (pages 2+) and front-page masthead, drawn from page data ---- */
.mh{position:absolute;z-index:3;left:2.857%;width:94.286%;top:1.85%;height:3.6%;font-family:Georgia,'Times New Roman',serif}
.mh-top{position:absolute;left:0;right:0;top:0;border-top:.12cqw solid var(--ink);height:0}
.mh-top span{position:absolute;left:50%;top:-.55cqw;transform:translateX(-50%);background:var(--paper);
  padding:0 1cqw;font:700 .8cqw/1 Georgia,serif;letter-spacing:.22em;white-space:nowrap}
.mh-row{position:absolute;left:0;right:0;top:.5cqw;bottom:.5cqw;display:grid;grid-template-columns:1fr auto 1fr;align-items:center}
.mh-no b{display:inline-block;font:900 2.1cqw/1 Georgia,serif;padding:.25cqw .8cqw;border:.12cqw solid var(--ink)}
.mh-sec{font:900 3.2cqw/1 Georgia,serif;text-align:center;letter-spacing:.01em}
.mh-date{justify-self:end;text-align:right;font:.9cqw/1.4 Georgia,serif}
.mh-date small{display:block;font-size:.72cqw;letter-spacing:.1em;text-transform:uppercase;color:#444}
.mh-bot{position:absolute;left:0;right:0;bottom:0;height:.35cqw;border-top:.12cqw solid var(--ink);border-bottom:.04cqw solid var(--ink)}
.mh1{position:absolute;z-index:3;left:2.857%;top:1.85%;width:83%;height:7.2%;display:grid;
  grid-template-columns:13cqw 1fr;grid-template-rows:1fr auto;column-gap:1cqw;font-family:Georgia,'Times New Roman',serif}
.mh1-l{grid-row:1/3;align-self:center;text-align:right;font:.85cqw/1.55 Georgia,serif}
.mh1-l b{display:block;letter-spacing:.04em}.mh1-l b+b{margin-top:.5cqw}
.wm{align-self:center;text-align:center;white-space:nowrap;font:900 9.2cqw/1 Georgia,'Times New Roman',serif;letter-spacing:.03em}
.tag{text-align:center;font:.72cqw/1 Georgia,serif;letter-spacing:.16em;text-transform:uppercase;color:#333}
.mh1-rule{position:absolute;z-index:3;left:2.857%;width:94.286%;top:9.2%;height:.35cqw;
  border-top:.12cqw solid var(--ink);border-bottom:.04cqw solid var(--ink)}

/* ---- articles ---- */
.art{position:absolute;z-index:2;overflow:hidden;background:var(--paper);
  font:calc(.907cqw * var(--k,1))/1.22 Georgia,'Times New Roman',serif;color:var(--ink)}
.art:hover{outline:2px solid rgba(11,79,138,.55);outline-offset:-2px}
.hot{position:absolute;inset:0;z-index:5}
.sr{position:absolute;left:-9999px}
.hl{margin:0 0 .22em;font-size:calc(var(--hp) / 9 * 1em * var(--hs,1));line-height:1.06;font-weight:700;text-wrap:balance}
.promo .hl{font-size:1.15em;text-transform:uppercase;letter-spacing:.01em}
.deck{font-weight:700;font-size:.95em;line-height:1.2;margin:0 0 .35em;text-wrap:pretty}
.deck .leadin{font-weight:700}
.by{font:.78em/1.2 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.04em;
  margin:0 0 .5em;padding-bottom:.35em;border-bottom:.03cqw solid #bbb;color:#333}
.by .byline_name{font-weight:700}.by .dateline{margin-left:.5em;color:#666}
.art.placed .head{position:relative;z-index:2}
.ph{position:absolute;z-index:1;margin:0}
.ph img{display:block;width:100%;height:auto;object-fit:cover;background:#ddd}
.ph figcaption{font:.72em/1.2 system-ui,sans-serif;color:#444;margin-top:.25em}
.ph figcaption p{margin:0}
.ph .cr{color:#777;text-transform:uppercase;letter-spacing:.03em;font-size:.9em}
.flow{position:absolute;inset:0;z-index:0;pointer-events:none}
.slot{position:absolute;overflow:hidden;text-align:justify;hyphens:auto}
.slot p{margin:0 0 .35em}.slot p:last-child{margin-bottom:0}
.crule{position:absolute;width:0;border-left:.03cqw solid #c9c9c9}
.src{display:none}
.txt{column-count:var(--nc);column-gap:1.4286cqw;column-rule:.03cqw solid #c9c9c9;
  text-align:justify;hyphens:auto;orphans:2;widows:2}
.txt p{margin:0 0 .35em}
.promo .txt{text-align:left;hyphens:manual}
.art.bleed .ph{left:0!important;top:0!important;width:100%!important;height:100%}
.art.bleed .ph img{height:100%;aspect-ratio:auto!important}
.art.bleed figcaption,.art.pcard figcaption{display:none}
.pp{position:absolute;z-index:1;overflow:hidden;padding:.9cqw 1.1cqw;display:flex;align-items:center}
.pin{width:100%}
.pp .kick{font:700 .7em/1.2 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.07em;margin-bottom:.35em}
.pp .tt{font-size:1.5em;line-height:1.1;font-weight:600;text-wrap:balance}
.pp .jump{font:700 .65em/1 system-ui,sans-serif;margin-top:.7em;text-transform:uppercase;letter-spacing:.06em}
.sheet.scan .scan{display:block}
.sheet.scan .art>*:not(.hot),.sheet.scan .mh,.sheet.scan .mh1,.sheet.scan .mh1-rule{visibility:hidden}
.sheet.scan .art{background:transparent}
.sheet.scan .art:hover{background:rgba(11,79,138,.12)}
footer{text-align:center;font-size:12px;color:#555;padding:12px 0 24px}

@page{size:350mm 540mm;margin:0}
@media print{
 html,body{margin:0;background:#fff;height:540mm;overflow:hidden}
 .bar,footer{display:none!important}
 .wrap{padding:0;overflow:visible}
 .sheet{width:350mm!important;margin:0;box-shadow:none}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
 .art:hover{outline:none}
}
"""

PAGE_JS = r"""
(function(){
 var sheet=document.getElementById('sheet'),widths=[700,900,1100,1300,1600,2000,2600],zi=0;
 function setW(i){zi=Math.max(0,Math.min(widths.length-1,i));sheet.style.setProperty('--w',widths[zi]+'px');
   requestAnimationFrame(fitAll);}
 var vw=Math.min(window.innerWidth-28,1300);widths.forEach(function(w,i){if(w<=vw)zi=i;});
 sheet.style.setProperty('--w',widths[zi]+'px');
 document.getElementById('zin').onclick=function(){setW(zi+1)};
 document.getElementById('zout').onclick=function(){setW(zi-1)};
 var mode=document.getElementById('mode');
 if(mode)mode.onclick=function(){var on=sheet.classList.toggle('scan');
   mode.textContent=on?'Show reconstructed layout':'Show original scan';};
 var pb=document.getElementById('print');if(pb)pb.onclick=function(){window.print();};

 function K(a,v){a.style.setProperty('--k',v);}
 /* plain articles: headline + balanced columns, grow text until the box is full */
 function fitSimple(art){
   var inner=art.querySelector('.inner'),H=art.clientHeight;if(!inner||!H)return;
   var lo=.4,hi=1.15,best=lo;
   for(var i=0;i<8;i++){var m=(lo+hi)/2;K(art,m);if(inner.offsetHeight<=H){best=m;lo=m;}else{hi=m;}}
   K(art,best);
 }
 /* promo cards: text sized to the coloured panel */
 function fitPanel(art){
   var pp=art.querySelector('.pp'),pin=pp&&pp.querySelector('.pin');if(!pin)return;
   var cs=getComputedStyle(pp),H=pp.clientHeight-parseFloat(cs.paddingTop)-parseFloat(cs.paddingBottom);
   var lo=.3,hi=2.4,best=lo;
   for(var i=0;i<8;i++){var m=(lo+hi)/2;K(art,m);
     if(pin.offsetHeight<=H&&pp.scrollWidth<=pp.clientWidth+1){best=m;lo=m;}else{hi=m;}}
   K(art,best);
 }
 function subtract(ivs,a,b){var r=[];ivs.forEach(function(v){
   if(b<=v[0]||a>=v[1]){r.push(v);return;}
   if(a>v[0])r.push([v[0],a]);if(b<v[1])r.push([b,v[1]]);});return r;}
 /* articles with photos: photos stay at their printed rectangles, text flows column by column around them */
 function layout(art,k){
   K(art,k);
   var W=art.clientWidth,H=art.clientHeight,head=art.querySelector('.head'),
       flow=art.querySelector('.flow'),src=art.querySelector('.src');
   var fs=parseFloat(getComputedStyle(art).fontSize),lh=fs*1.22,
       nc=+art.dataset.nc||1,gut=W*(+art.dataset.gut||0),cw=(W-(nc-1)*gut)/nc;
   if(!art._p)art._p=Array.prototype.map.call(src.children,function(p){return{t:p.textContent,c:p.className};});
   var figEls=Array.prototype.slice.call(art.querySelectorAll('.ph'));
   figEls.forEach(function(f){if(!f.dataset.t)f.dataset.t=f.style.top;f.style.top=f.dataset.t;});
   var rects=figEls.map(function(f){
     return{x:f.offsetLeft,y:f.offsetTop,w:f.offsetWidth,h:f.offsetHeight};});
   var headB=0;
   if(head){
     /* a wide photo at the very top: the headline goes underneath it */
     var topFig=0;rects.forEach(function(r){if(r.w>.65*W&&r.y<.035*H)topFig=Math.max(topFig,r.y+r.h+fs*.3);});
     head.style.marginTop=topFig?topFig+'px':'';art.style.setProperty('--hs',1);
     var bottom=function(){return head.offsetTop+head.offsetHeight+fs*.25;};
     var clash=function(){var hl=head.offsetLeft,hr=hl+head.offsetWidth;
       return rects.some(function(r){return r.y>=.035*H&&r.y<headB-1&&r.x<hr&&r.x+r.w>hl;});};
     headB=bottom();
     /* a photo meant to sit below the headline: shrink the headline (our font is wider than print) */
     var steps=[.9,.8,.7,.62,.55];
     for(var si=0;si<steps.length&&clash();si++){art.style.setProperty('--hs',steps[si]);headB=bottom();}
     /* still touching: nudge the photo down by the shortfall (at most 10% of the box) */
     var hl0=head.offsetLeft,hr0=hl0+head.offsetWidth;
     rects.forEach(function(r,i){if(r.y>=.035*H&&r.y<headB-1&&r.x<hr0&&r.x+r.w>hl0){
       var d=Math.min(headB-r.y,.1*H);r.y+=d;figEls[i].style.top=r.y+'px';}});
   }
   var slots=[];
   for(var c=0;c<nc;c++){
     var x0=c*(cw+gut),x1=x0+cw,
         under=!head||(x1>head.offsetLeft+2&&x0<head.offsetLeft+head.offsetWidth-2),  /* column sits below the headline? */
         ivs=[[under?headB:0,H]];
     rects.forEach(function(r){if(r.x<x1-2&&r.x+r.w>x0+2)ivs=subtract(ivs,r.y-fs*.3,r.y+r.h+fs*.3);});
     ivs.forEach(function(v){if(v[1]-v[0]>=lh*2)slots.push({x:x0,y:v[0],w:cw,h:v[1]-v[0]});});
   }
   flow.innerHTML='';
   var pi=0,rest=null;
   for(var s=0;s<slots.length&&pi<art._p.length;s++){
     var S=slots[s],el=document.createElement('div');el.className='slot';
     el.style.cssText='left:'+S.x+'px;top:'+S.y+'px;width:'+S.w+'px;height:'+S.h+'px';
     flow.appendChild(el);
     if(S.x>0){var rl=document.createElement('div');rl.className='crule';
       rl.style.cssText='left:'+(S.x-gut/2)+'px;top:'+S.y+'px;height:'+S.h+'px';flow.appendChild(rl);}
     while(pi<art._p.length){
       var words=rest||art._p[pi].t.split(' ');rest=null;
       var p=document.createElement('p');p.className=art._p[pi].c;el.appendChild(p);
       p.textContent=words.join(' ');
       if(el.scrollHeight<=el.clientHeight+.5){pi++;continue;}
       var lo=0,hi=words.length-1;
       while(lo<hi){var m=(lo+hi+1)>>1;p.textContent=words.slice(0,m).join(' ');
         if(el.scrollHeight<=el.clientHeight+.5)lo=m;else hi=m-1;}
       if(lo===0){el.removeChild(p);}else{p.textContent=words.slice(0,lo).join(' ');}
       rest=words.slice(lo);break;
     }
   }
   var ok=pi>=art._p.length;art.dataset.over=ok?'0':'1';return ok;
 }
 function fitPlaced(art){
   if(layout(art,1.15))return;
   var lo=.45,hi=1.15,best=0;
   for(var i=0;i<7;i++){var m=(lo+hi)/2;if(layout(art,m)){best=m;lo=m;}else{hi=m;}}
   layout(art,best||.45);
 }
 function fitOne(art){
   if(art.classList.contains('bleed'))return;
   if(art.classList.contains('pcard'))fitPanel(art);
   else if(art.classList.contains('placed'))fitPlaced(art);
   else fitSimple(art);
 }
 function fitAll(){var a=document.querySelectorAll('.art');for(var i=0;i<a.length;i++)fitOne(a[i]);
   document.documentElement.setAttribute('data-fit','1');}
 window.__fit=fitAll;
 window.addEventListener('beforeprint',fitAll);
 if(document.fonts&&document.fonts.ready)document.fonts.ready.then(fitAll);else window.addEventListener('load',fitAll);
 document.addEventListener('keydown',function(e){
   if(e.key==='ArrowLeft'&&window.PREV)location.href=window.PREV;
   if(e.key==='ArrowRight'&&window.NEXT)location.href=window.NEXT;});
})();
"""


def section_label(attrs):
    name = (attrs.get("PageNameCont") or attrs.get("Name") or "").split("_")[0]
    return SECTION_LABELS.get(name, name)


def masthead_html(pno, section, meta, first):
    e = html.escape
    date, ed = e(meta.get("date", "")), e(meta.get("edition", ""))
    if first:
        return (
            '<header class="mh1"><div class="mh1-l">'
            f'<b>{e(meta.get("weekday", "").upper())}<br><span style="font-weight:400">{e(meta.get("dateshort", ""))}</span></b>'
            f"<b>{ed.upper().replace(' EDITION', '<br>EDITION')}</b>"
            f"<span>{meta.get('npages', '')} Pages</span></div>"
            '<div class="wm">THE HINDU</div>'
            '<div class="tag">India’s national newspaper since 1878</div></header>'
            '<div class="mh1-rule"></div>'
        )
    return (
        '<header class="mh"><div class="mh-top"><span>THE HINDU</span></div>'
        f'<div class="mh-row"><div class="mh-no"><b>{pno}</b></div>'
        f'<div class="mh-sec">{e(section)}</div>'
        f'<div class="mh-date">{date}<small>{ed}</small></div></div>'
        '<div class="mh-bot"></div></header>'
    )


def build_page(
    page,
    pno,
    npages,
    records,
    results,
    out: Path,
    label: str,
    files: dict,
    header: bool = True,
    meta: dict | None = None,
    pdf_href: str | None = None,
):
    attrs = page["attributes"]
    desk = attrs.get("LayoutDeskCont", "").replace("TH_", "")
    section = section_label(attrs) or desk
    meta = meta or {}
    cards = []
    for c in page.get("children", []):
        if c.get("kind") != "Article":
            continue
        card, area = card_html(c, records.get(c["id"]), results.get(c["id"]), out)
        if card:
            cards.append((area, card))
    cards.sort(key=lambda t: -t[0])  # big first, small on top

    scan = files.get("scan")
    prev_l = f"page-{pno - 1:02d}.html" if pno > 1 else ""
    next_l = f"page-{pno + 1:02d}.html" if pno < npages else ""
    bar = [
        '<a href="../index.html">Index</a>',
        f'<span class="t">Page {pno} of {npages} &middot; {html.escape(section)}</span>',
    ]
    if prev_l:
        bar.append(f'<a href="{prev_l}">&larr; Prev</a>')
    if next_l:
        bar.append(f'<a href="{next_l}">Next &rarr;</a>')
    bar.append('<span class="sp"></span>')
    bar.append(
        '<button id="zout" title="Smaller">A&minus;</button><button id="zin" title="Larger">A+</button>'
    )
    if scan:
        bar.append('<button id="mode">Show original scan</button>')
    bar.append(
        '<button id="print" title="Print or save this page as PDF">Print / PDF</button>'
    )
    if pdf_href:
        bar.append(f'<a href="{pdf_href}">PDF</a>')

    doc = [
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page {pno} &ndash; The Hindu {html.escape(label)}</title><style>{PAGE_CSS}</style></head><body>
<div class="bar">{"".join(bar)}</div>
<div class="wrap"><div class="sheet" id="sheet">"""
    ]
    if scan:
        doc.append(
            f'<img class="scan" src="img/{quote(scan)}" alt="Original page {pno}">'
        )
    if header:
        doc.append(masthead_html(pno, section, meta, pno == 1))
    doc.extend(c for _, c in cards)
    doc.append(f"""</div></div>
<footer>Source: The Hindu e-paper ({html.escape(label)}). &copy; The Hindu. Personal archive; not for redistribution.</footer>
<script>window.PREV={json.dumps(prev_l)};window.NEXT={json.dumps(next_l)};{PAGE_JS}</script></body></html>""")
    (out / "pages" / f"page-{pno:02d}.html").write_text(
        "\n".join(doc), encoding="utf-8"
    )
    return len(cards)


def fetch_page_assets(page, out: Path):
    """Only the page preview (a Public/ resource) is used. The page backdrop and PDF listed in
    cciobjects.json have no Public/ prefix, so they are not for public access and are never requested."""
    got = {}
    for rk in page.get("resources", {}):
        if rk.startswith("Public/") and rk.endswith("_medium-900.jpg"):
            name = Path(rk).name.replace("+", "_")
            if download(urljoin(BASE, rk), out / "pages" / "img" / name):
                got["scan"] = name
            break
    return got


def fetch_cci_photos(cci_pages, out: Path, workers: int = 4):
    """Make sure every photo that cciobjects.json places on a page is downloaded (Public/ only)."""
    refs = set()
    for pg in cci_pages.values():
        for art in pg.get("children", []):
            if art.get("kind") != "Article":
                continue
            for ch in art.get("children", []):
                if ch.get("kind") in ("Photo", "Graphics"):
                    for x in ch.get("content", []):
                        r = x.get("reference", "")
                        if (
                            r.startswith("Public/")
                            and not (
                                out / "images" / Path(strip_query(r)).name
                            ).exists()
                        ):
                            refs.add(r)
    if refs:
        print(f"Fetching {len(refs)} more placed photo(s)")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(
            ex.map(
                lambda r: download(
                    urljoin(BASE, r), out / "images" / Path(strip_query(r)).name
                ),
                sorted(refs),
            )
        )


READY_JS = """async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images).map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
  if (window.__fit) window.__fit();
}"""


def render_pdfs(out: Path, pnos):
    """Render each rebuilt page to a PDF with a local headless Chromium (nothing is downloaded).
    Returns the merged file name, or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "  ! --pdf needs Playwright:  pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return None
    pdf_dir = out / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    made = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page(viewport={"width": 1323, "height": 2041})
            for pno in pnos:
                src = (out / "pages" / f"page-{pno:02d}.html").resolve()
                dst = pdf_dir / f"page-{pno:02d}.pdf"
                pg.goto(src.as_uri())
                pg.emulate_media(media="print")
                pg.evaluate(READY_JS)
                pg.pdf(
                    path=str(dst),
                    width=f"{PAGE_MM[0]}mm",
                    height=f"{PAGE_MM[1]}mm",
                    print_background=True,
                    prefer_css_page_size=True,
                )
                made.append(dst)
                print(f"  pdf page {pno}")
            browser.close()
    except Exception as e:  # noqa: BLE001
        print(
            f"  ! PDF rendering failed: {e}\n    (run: playwright install chromium)",
            file=sys.stderr,
        )
        return None
    try:
        from pypdf import PdfWriter
    except ImportError:
        print("  (pip install pypdf to also get one merged edition.pdf)")
        return None
    w = PdfWriter()
    for f in made:
        w.append(str(f))
    with open(out / "edition.pdf", "wb") as fh:
        w.write(fh)
    return "edition.pdf"


# --------------------------------------------------------------------------
# Index page
# --------------------------------------------------------------------------
def build_index(
    pages, out: Path, label: str, layout: bool, pdf_href: str | None = None
):
    """Reading-list index: stories grouped by page, sized by text length."""
    css = """
 :root{--rule:#222;--soft:#bbb}
 body{font:16px/1.45 Georgia,'Times New Roman',serif;max-width:1180px;margin:0 auto;
   padding:12px 16px;color:#1a1a1a;background:#fbfaf6}
 .masthead{text-align:center;border-top:4px solid var(--rule);border-bottom:1px solid var(--rule);
   padding:10px 0;margin-bottom:6px}
 .masthead h1{margin:0;font:700 2.6em/1.1 'Old English Text MT',Georgia,serif;letter-spacing:.02em}
 .masthead p{margin:4px 0 0;font:12px system-ui,sans-serif;color:#555;text-transform:uppercase;letter-spacing:.12em}
 .toc{display:flex;gap:6px 14px;flex-wrap:wrap;font:13px system-ui,sans-serif;padding:8px 0;
   border-bottom:1px solid var(--soft);margin-bottom:8px}
 .toc a{color:#0b4f8a;text-decoration:none}
 .page-head{display:flex;justify-content:space-between;align-items:baseline;margin:30px 0 8px;
   border-top:3px double var(--rule);padding-top:6px;font:700 13px system-ui,sans-serif;
   text-transform:uppercase;letter-spacing:.1em}
 .page-head span{font-weight:400;color:#666}
 .page-head a{font-weight:400;color:#0b4f8a;text-transform:none;letter-spacing:0;margin-left:12px}
 .grid{display:grid;grid-template-columns:repeat(6,1fr);grid-auto-flow:dense;gap:0}
 .card{padding:10px 14px;border-left:1px solid var(--soft);border-bottom:1px solid var(--soft);
   min-width:0;overflow:hidden}
 .card a{color:inherit;text-decoration:none}
 .card a:hover h3{text-decoration:underline}
 .card h3{margin:6px 0 4px;font-size:1.12em;line-height:1.2}
 .card .by{font:11px system-ui,sans-serif;color:#777;text-transform:uppercase;letter-spacing:.05em}
 .card p{margin:0;color:#444;font-size:.88em}
 .card img{width:100%;height:auto;display:block;background:#eee}
 .s2{grid-column:span 2} .s3{grid-column:span 3} .s4{grid-column:span 4} .s6{grid-column:span 6}
 .lead h3{font-size:2em;line-height:1.1}
 .lead p{font-size:1em}
 .card.small img{max-height:150px;object-fit:cover}
 .brief h3{font-size:.98em;font-weight:600}
 footer{margin-top:3em;font:12px system-ui,sans-serif;color:#777;text-align:center}
 @media(max-width:800px){.grid{grid-template-columns:1fr 1fr}
   .s3,.s4,.s6,.lead{grid-column:span 2}.s2{grid-column:span 1}.lead h3{font-size:1.5em}}
 @media(max-width:480px){.grid{grid-template-columns:1fr}.s2,.s3,.s4,.s6{grid-column:span 1}}
"""
    first = pages[0][1][0]["page_no"] if pages else 1
    parts = [
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Hindu &ndash; {html.escape(label)}</title><style>{css}</style></head><body>
<div class="masthead"><h1>The Hindu</h1><p>{html.escape(label)} &middot; personal archive</p></div>
"""
    ]
    if layout:
        parts.append(
            f'<p class="toc"><a href="pages/page-{first:02d}.html"><b>Read as newspaper &rarr;</b></a></p>'
        )
    if pdf_href:
        parts.append(
            f'<p class="toc"><a href="{pdf_href}">Download the full edition (PDF)</a></p>'
        )
    parts.append('<nav class="toc">')
    for pid, arts in pages:
        pno = arts[0]["page_no"]
        desk = arts[0].get("layoutDeskCont", "").replace("TH_", "")
        parts.append(f'<a href="#p{pno}">{pno}. {html.escape(desk)}</a>')
    parts.append("</nav>")

    for pid, arts in pages:
        pno = arts[0]["page_no"]
        desk = arts[0].get("layoutDeskCont", "").replace("TH_", "")
        live = [a for a in arts if not a.get("failed")]
        link = (
            f'<a href="pages/page-{pno:02d}.html">Open page layout</a>'
            if layout
            else ""
        )
        parts.append(
            f'<div class="page-head" id="p{pno}"><div>Page {pno}'
            f'<span> {html.escape(desk)}</span>{link}</div></div><div class="grid">'
        )
        lead_done = False
        for a in live:
            n = a.get("body_len", 0)
            has_head = bool(a.get("headline"))
            classes = ["card"]
            if has_head and not lead_done and n > 600:
                lead_done = True
                classes += ["lead", "s4" if len(live) > 3 else "s6"]
            elif n > 2500:
                classes.append("s3")
            elif n > 800 or has_head:
                classes += ["s2", "small"]
            else:
                classes += ["s2", "small", "brief"]
            title = a.get("headline") or a.get("teaserText") or "(untitled)"
            by = a.get("author") or ""
            dek = a.get("subHead") or ""
            if not dek and has_head:
                body = a.get("bodyText") or ""
                if by and body.startswith(by):
                    body = body[len(by) :].lstrip()
                dek = (body[:200].rsplit(" ", 1)[0] + "…") if len(body) > 200 else body
            if "brief" in classes and not dek:
                body = a.get("bodyText") or ""
                dek = (body[:160].rsplit(" ", 1)[0] + "…") if len(body) > 160 else body
            img = (
                f'<img loading="lazy" src="{a["thumb_path"]}" alt="">'
                if a.get("thumb_path")
                else ""
            )
            href = f"articles/{quote(a['file_name'])}"
            parts.append(
                f'<article class="{" ".join(classes)}"><a href="{href}">{img}'
                + (f'<div class="by">{html.escape(by)}</div>' if by else "")
                + f"<h3>{html.escape(title)}</h3>"
                + (f"<p>{html.escape(dek)}</p>" if dek else "")
                + "</a></article>"
            )
        parts.append("</div>")
    parts.append(
        "<footer>Source: The Hindu e-paper. &copy; The Hindu. "
        "Personal archive; not for redistribution.</footer></body></html>"
    )
    (out / "index.html").write_text("\n".join(parts), encoding="utf-8")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    global BASE
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--url", default=DEFAULT_URL, help="URL of articles.json for the edition"
    )
    ap.add_argument(
        "--out", default=None, help="output folder (default: derived from issue id)"
    )
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--pdf",
        action="store_true",
        help="render each page (and one merged edition.pdf) to PDF locally; "
        "needs: pip install playwright pypdf && playwright install chromium",
    )
    ap.add_argument(
        "--no-header",
        action="store_true",
        help="don't draw the masthead / running header (page number, section, date)",
    )
    args = ap.parse_args()

    BASE = args.url.rsplit("/", 1)[0] + "/"
    m = re.search(r"/issues/(\d+)/", args.url)
    issue = m.group(1) if m else "edition"
    out = Path(args.out or f"hindu_{issue}")
    for sub in ("articles", "assets", "images", "pages/img"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    print(f"Fetching manifest: {args.url}")
    manifest_bytes = http_get(args.url)
    (out / "articles.json").write_bytes(manifest_bytes)
    articles = json.loads(manifest_bytes)

    # page geometry (optional: without it we still build the reading list)
    cci = None
    try:
        cci_bytes = http_get(BASE + "cciobjects.json")
        (out / "cciobjects.json").write_bytes(cci_bytes)
        cci = json.loads(cci_bytes)
    except Exception as e:  # noqa: BLE001
        print(
            f"  ! cciobjects.json unavailable ({e}); skipping page layouts",
            file=sys.stderr,
        )

    label = f"Edition {issue}"
    meta = {"edition": "", "date": "", "weekday": "", "dateshort": "", "npages": ""}
    em = re.search(r"/th/th_([a-z0-9_]+)/", args.url)
    if em:
        meta["edition"] = em.group(1).replace("_", " ").title() + " Edition"
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
    for a in articles:
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
            a["file_name"] = a["resource"].replace(
                "+", "_"
            )  # '+' is awkward in filenames
            flat.append(a)
    print(f"{len(pages)} pages, {len(flat)} articles")

    ctx = {
        "base": BASE,
        "out": out,
        "lock": threading.Lock(),
        "css_seen": set(),
        "edition_label": label,
        "layout": bool(cci),
    }
    results = {}

    def work(i):
        a = flat[i]
        r = process_article(a, i, flat, ctx)
        if r is None:
            a["failed"] = True
        else:
            results[a["id"]] = r
            if r["thumb"]:
                a["thumb_path"] = r["thumb"]
        done = i + 1
        if done % 10 == 0 or done == len(flat):
            print(f"  {done}/{len(flat)}")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, range(len(flat))))

    pdf_all = None
    if cci:
        fetch_cci_photos(cci_pages, out, args.workers)
        print("Building page layouts")
        (out / "pages").mkdir(exist_ok=True)
        records = {a["id"]: a for a in flat}
        npages = max(pno_by_pid.values()) if pno_by_pid else len(page_order)
        meta["npages"] = npages
        for pid in page_order:
            pg, pno = cci_pages[pid], pno_by_pid[pid]
            files = fetch_page_assets(pg, out)
            n = build_page(
                pg,
                pno,
                npages,
                records,
                results,
                out,
                label,
                files,
                header=not args.no_header,
                meta=meta,
                pdf_href=f"../pdf/page-{pno:02d}.pdf" if args.pdf else None,
            )
            print(f"  page {pno}: {n} articles placed")
        if args.pdf:
            print("Rendering PDFs locally")
            pdf_all = render_pdfs(out, [pno_by_pid[pid] for pid in page_order])

    build_index(pages, out, label, bool(cci), pdf_all)
    failed = [a["resource"] for a in flat if a.get("failed")]
    print(f"\nDone -> {out / 'index.html'}")
    if failed:
        print(
            f"{len(failed)} article(s) failed (re-run to retry; finished files are skipped):"
        )
        for f in failed:
            print("  ", f)


BASE = ""
if __name__ == "__main__":
    main()
