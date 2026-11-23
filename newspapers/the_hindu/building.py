"""Building: structured data -> HTML strings. No network, no scraping.

Markup lives in static/*.html, styling in static/*.css, behaviour in static/page.js.
"""

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from string import Template
from urllib.parse import quote, urljoin

from .parsing import (
    GUTTER,
    PAGE_H,
    PAGE_W,
    STYLESHEET_RE,
    BuildContext,
    Card,
    FetchedArticle,
    css_local_path,
    find_div,
    is_absolute,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
COPYRIGHT_NOTE = "Personal archive; not for redistribution."


# ==========================================================================
# Loading files from static/
# ==========================================================================
@lru_cache(maxsize=None)
def _read(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


def static_css(name: str) -> str:
    return _read(f"{name}.css")


def static_js(name: str) -> str:
    return _read(f"{name}.js")


def render_template(name: str, **values) -> str:
    """Fill ${placeholders} in static/<name>.html. Values are inserted as-is,
    so callers escape user text before passing it in."""
    return Template(_read(f"{name}.html")).substitute(values).strip()


# ==========================================================================
# Article pages
# ==========================================================================
def remove_div(markup: str, open_pattern: str):
    """Remove the first balanced <div ...> matching open_pattern.
    Returns (new_markup, start_index) so a replacement can be inserted, or (markup, None)."""
    span = find_div(markup, open_pattern)
    if span is None or span[3] is None:  # no match, or unbalanced: leave untouched
        return markup, None
    start, _, _, outer_end = span
    return markup[:start] + markup[outer_end:], start


def _figure(img):
    return (
        f'<figure class="archive-fig"><img src="../images/{quote(img.local)}" '
        f'alt="Article image {img.n + 1}">'
        + (f"<figcaption>{img.caption}</figcaption>" if img.caption else "")
        + "</figure>"
    )


def _nav(record, idx, ordered, has_layout):
    prev_l = (
        f'<a href="{ordered[idx - 1]["file_name"]}">&larr; Previous</a>' if idx > 0 else ""
    )
    next_l = (
        f'<a href="{ordered[idx + 1]["file_name"]}">Next &rarr;</a>'
        if idx < len(ordered) - 1
        else ""
    )
    pno = record["page_no"]
    page_l = (
        f'<a href="../pages/page-{pno:02d}.html#a-{record["id"]}">Page {pno}</a>'
        if has_layout
        else f"<span>Page {pno}</span>"
    )
    return render_template(
        "article_nav", page_link=page_l, prev_link=prev_l, next_link=next_l
    )


def build_article_page(
    fetched: FetchedArticle, idx: int, ordered: list, ctx: BuildContext
) -> str:
    """Return the HTML to save for one article. `ordered` is the reading-order record list."""
    record, url, raw = fetched.record, fetched.url, fetched.raw

    # 1. stylesheets -> local assets
    def css_sub(m):
        href = m.group(1)
        if is_absolute(href):
            return m.group(0)
        rel = css_local_path(urljoin(url, href), ctx.base, href)
        return f'<link rel="stylesheet" href="../assets/{quote(rel)}">'

    page = STYLESHEET_RE.sub(css_sub, raw)

    # 2. swap the JS-driven gallery container for the static figures
    figs_html = "\n".join(_figure(img) for img in fetched.images)
    page, pos = remove_div(page, r'<div class="image-gallery-container"[^>]*>')
    if pos is not None:
        page = page[:pos] + figs_html + page[pos:]
    elif figs_html:  # no container found: put images after the headline block
        page = re.sub(
            r"(</h2>|</h1>)(?!.*</h[12]>)",
            lambda m: m.group(1) + figs_html,
            page,
            count=1,
            flags=re.S,
        )

    # 3. drop scripts and og: meta (they need the live site), add our style, nav + credit
    page = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)
    page = re.sub(r'<meta property="og:[^>]*>\s*', "", page)
    style = "<style>\n" + static_css("article") + "\n</style>"
    nav = _nav(record, idx, ordered, ctx.has_layout)
    credit = render_template("article_credit", label=html.escape(ctx.edition_label), fetched_url=url)
    page = page.replace("</head>", style + "</head>", 1)
    page = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + nav, page, count=1)
    page = page.replace("</body>", credit + "</body>", 1)
    return page


# ==========================================================================
# Reconstructed newspaper pages
# ==========================================================================
# ---- one article box -------------------------------------------------------
def _pct(value, whole, places=3):
    return f"{value / whole * 100:.{places}f}%"


def _photo_figures(card: Card) -> str:
    return "".join(
        f'<figure class="ph" style="left:{_pct(p.x, card.w)};top:{_pct(p.y, card.h)};'
        f'width:{_pct(p.w, card.w)}"><img src="../images/{quote(p.local)}" alt="" '
        f'style="aspect-ratio:{p.w}/{p.h}">'
        + (f"<figcaption>{p.cap}</figcaption>" if p.cap else "")
        + "</figure>"
        for p in card.photos
    )


def _head_style(card: Card) -> str:
    """Photo hugging the top: keep the headline out of its way."""
    if not card.photos:
        return ""
    p = card.photos[0]
    if p.y < 0.12 * card.h and p.w <= 0.65 * card.w:
        if p.x + p.w / 2 < card.w / 2:
            return f"margin-left:{(p.x + p.w + GUTTER) / card.w * 100:.3f}%;"
        return f"width:{max(10, (p.x - GUTTER) / card.w * 100):.3f}%;"
    return ""


def _head_div(card: Card) -> str:
    return (
        f'<div class="head" style="{_head_style(card)}"><h2 class="hl">{card.title_html}</h2>'
        + (f'<div class="deck">{card.deck}</div>' if card.deck and not card.promo else "")
        + (f'<div class="by">{card.byline}</div>' if card.byline and not card.promo else "")
        + "</div>"
    )


def _promo_panel(card: Card, figures: str) -> str:
    panel = card.panel
    r, g, b = panel.rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    fg = "#fff" if lum < 0.55 else "#111"
    kick = (
        f'<div class="kick">{html.escape(card.promo_kicker)}</div>'
        if card.promo_kicker
        else ""
    )
    jmp = (
        f'<div class="jump">{html.escape(card.promo_jump)}</div>' if card.promo_jump else ""
    )
    return (
        figures
        + f'<div class="pp" style="left:{_pct(panel.x, card.w)};top:{_pct(panel.y, card.h)};'
        f"width:{_pct(panel.w, card.w)};height:{_pct(panel.h, card.h)};"
        f'background:{html.escape(panel.color, quote=True)};color:{fg}">'
        f'<div class="pin">{kick}<div class="tt">{html.escape(card.promo_title)}</div>{jmp}</div></div>'
    )


def card_html(card: Card) -> str:
    figures = _photo_figures(card)
    if card.mode == "bleed":
        inner = figures
    elif card.mode == "pcard":
        inner = _promo_panel(card, figures)
    elif card.mode == "placed":
        src = "".join(
            f'<p class="{html.escape(cl, quote=True)}">{html.escape(t)}</p>'
            for cl, t in card.paragraphs
        )
        inner = _head_div(card) + figures + '<div class="flow"></div>' + f'<div class="src">{src}</div>'
    else:
        inner = (
            f'<div class="inner">{_head_div(card)}'
            + (f'<div class="txt">{card.body_html}</div>' if card.body_html else "")
            + "</div>"
        )

    href = f"../articles/{quote(card.file_name)}" if card.file_name else "#"
    clip = ""
    if card.contour:
        coords = ", ".join(f"{x:.3f}% {y:.3f}%" for x, y in card.contour)
        clip = f"clip-path:polygon({coords});"
    style = (
        f"left:{_pct(card.x, PAGE_W, 4)};top:{_pct(card.y, PAGE_H, 4)};"
        f"width:{_pct(card.w, PAGE_W, 4)};height:{_pct(card.h, PAGE_H, 4)};"
        f"--nc:{card.ncols};--hp:{card.headline_pt};" + clip
    )
    title = html.escape(card.title_text, quote=True)
    return (
        f'<article class="art {card.mode}{" promo" if card.promo else ""}" id="a-{html.escape(card.id)}" '
        f'data-nc="{card.ncols}" data-gut="{GUTTER / card.w:.5f}" style="{style}">'
        f'<a class="hot" href="{href}" title="{title}">'
        f'<span class="sr">{html.escape(card.title_text)}</span></a>' + inner + "</article>"
    )


# ---- masthead / running header --------------------------------------------
def masthead_html(pno, section, meta, first):
    e = html.escape
    if first:
        return render_template(
            "masthead_front",
            weekday=e(meta.get("weekday", "").upper()),
            dateshort=e(meta.get("dateshort", "")),
            edition=e(meta.get("edition", "").upper()).replace(" EDITION", "<br>EDITION"),
            npages=meta.get("npages", ""),
        )
    return render_template(
        "masthead_running",
        pno=pno,
        section=e(section),
        date=e(meta.get("date", "")),
        edition=e(meta.get("edition", "")),
    )


# ---- the page --------------------------------------------------------------
def _toolbar(pno, npages, section, prev_l, next_l, has_scan, pdf_href):
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
    # NOT EXACTLY FUNCTIONAL AT THE MOMENT
    
    # if has_scan:
    #     bar.append('<button id="mode">Show original scan</button>')
    # bar.append('<button id="print" title="Print or save this page as PDF">Print / PDF</button>')
    # if pdf_href:
    #     bar.append(f'<a href="{pdf_href}">PDF</a>')
    return "".join(bar)


def build_page(
    cards,
    pno: int,
    npages: int,
    section: str,
    label: str,
    scan: str = None,
    header: bool = True,
    meta: dict = None,
    pdf_href: str = None,
):
    """Return (html, number_of_cards) for one printed page."""
    meta = meta or {}
    cards = sorted(cards, key=lambda c: -c.area)  # big first, small on top
    prev_l = f"page-{pno - 1:02d}.html" if pno > 1 else ""
    next_l = f"page-{pno + 1:02d}.html" if pno < npages else ""

    sheet = []
    if scan:
        sheet.append(f'<img class="scan" src="img/{quote(scan)}" alt="Original page {pno}">')
    if header:
        sheet.append(masthead_html(pno, section, meta, pno == 1))
    sheet.extend(card_html(c) for c in cards)

    doc = render_template(
        "page",
        pno=pno,
        label=html.escape(label),
        css=static_css("page"),
        js=static_js("page"),
        bar=_toolbar(pno, npages, section, prev_l, next_l, bool(scan), pdf_href),
        sheet="\n".join(sheet),
        prev_json=json.dumps(prev_l),
        next_json=json.dumps(next_l),
        copyright_note=COPYRIGHT_NOTE,
    )
    return doc, len(cards)


# ==========================================================================
# Reading-list index
# ==========================================================================
def _card_classes(a, live, lead_done):
    """Grid size class for one story. Returns (classes, lead_done)."""
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
    return classes, lead_done


def _snippet(a, classes):
    dek = a.get("subHead") or ""
    has_head = bool(a.get("headline"))
    by = a.get("author") or ""
    if not dek and has_head:
        body = a.get("bodyText") or ""
        if by and body.startswith(by):
            body = body[len(by) :].lstrip()
        dek = (body[:200].rsplit(" ", 1)[0] + "…") if len(body) > 200 else body
    if "brief" in classes and not dek:
        body = a.get("bodyText") or ""
        dek = (body[:160].rsplit(" ", 1)[0] + "…") if len(body) > 160 else body
    return dek


def _story_html(a, classes):
    title = a.get("headline") or a.get("teaserText") or "(untitled)"
    by = a.get("author") or ""
    dek = _snippet(a, classes)
    img = (
        f'<img loading="lazy" src="{a["thumb_path"]}" alt="">' if a.get("thumb_path") else ""
    )
    href = "articles/" + quote(a["file_name"])
    return (
        f'<article class="{" ".join(classes)}"><a href="{href}">{img}'
        + (f'<div class="by">{html.escape(by)}</div>' if by else "")
        + f"<h3>{html.escape(title)}</h3>"
        + (f"<p>{html.escape(dek)}</p>" if dek else "")
        + "</a></article>"
    )


def build_index(pages, label: str, has_layout: bool, pdf_href: str = None) -> str:
    first = pages[0][1][0]["page_no"] if pages else 1
    parts = []
    if has_layout:
        parts.append(
            f'<p class="toc"><a href="pages/page-{first:02d}.html"><b>Read as newspaper &rarr;</b></a></p>'
        )
    if pdf_href:
        parts.append(f'<p class="toc"><a href="{pdf_href}">Download the full edition (PDF)</a></p>')

    parts.append('<nav class="toc">')
    for _, arts in pages:
        pno = arts[0]["page_no"]
        desk = arts[0].get("layoutDeskCont", "").replace("TH_", "")
        parts.append(f'<a href="#p{pno}">{pno}. {html.escape(desk)}</a>')
    parts.append("</nav>")

    for _, arts in pages:
        pno = arts[0]["page_no"]
        desk = arts[0].get("layoutDeskCont", "").replace("TH_", "")
        live = [a for a in arts if not a.get("failed")]
        link = f'<a href="pages/page-{pno:02d}.html">Open page layout</a>' if has_layout else ""
        parts.append(
            f'<div class="page-head" id="p{pno}"><div>Page {pno}'
            f"<span> {html.escape(desk)}</span>{link}</div></div><div class=\"grid\">"
        )
        lead_done = False
        for a in live:
            classes, lead_done = _card_classes(a, live, lead_done)
            parts.append(_story_html(a, classes))
        parts.append("</div>")

    return render_template(
        "index",
        label=html.escape(label),
        css=static_css("index"),
        body="\n".join(parts),
        copyright_note=COPYRIGHT_NOTE,
    )
