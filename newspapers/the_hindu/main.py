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
rendered locally from the rebuilt pages (--render).

Usage:
    python hindu_epaper_archive.py
    python hindu_epaper_archive.py --url <.../OPS/articles.json> --out edition_214446
    python hindu_epaper_archive.py --render       # render every page to PDF locally (Playwright)
    python hindu_epaper_archive.py --serve        # serve the result at http://127.0.0.1:8000/
    python hindu_epaper_archive.py --no-build --serve    # just serve a folder you already built
    python hindu_epaper_archive.py --no-header    # skip the drawn masthead / running header

Output layout:
    out/index.html            reading list: pages in order, articles grouped by page
    out/pages/page-NN.html    reconstructed page: articles at their printed positions
    out/pages/img/            Public page previews (for the "original scan" toggle)
    out/pdf/                  page-NN.pdf and edition.pdf, only with --render
    out/articles/*.html       each article, CSS/images rewritten to local copies
    out/assets/, out/images/  CSS and article images
    out/articles.json, out/cciobjects.json   raw manifests, kept for reference

Requires: pip install requests
Optional (--render): pip install playwright pypdf && playwright install chromium
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

from .building import build_article_page, build_index, build_page
from .fetching import (
    fetch_article,
    fetch_default_manifest_url,
    fetch_manifests,
    fetch_page_scan,
    fetch_placed_photos,
)
from .output import render_pdfs, serve
from .parsing import (
    ArticleResult,
    BuildContext,
    Edition,
    base_from_url,
    issue_from_url,
    page_section,
    parse_article,
    parse_card,
    parse_edition,
)

# DEFAULT_URL = (
#     "https://epaper.thehindu.com/ccidist-ws/th/th_international/"
#     "issues/214446/OPS/articles.json"
# )


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # ap.add_argument("--url", default=DEFAULT_URL, help="URL of articles.json for the edition")
    ap.add_argument(
        "--date",
        type=str,
        help="issue date; eg: 2026-11-20 [default: today's date]",
        default=datetime.now().strftime("%Y-%m-%d"),
    )
    ap.add_argument(
        "--out", default=None, help="output folder (default: derived from issue id)"
    )
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--no-header",
        action="store_true",
        help="don't draw the masthead / running header (page number, section, date)",
    )
    ap.add_argument(
        "--render",
        "--pdf",
        dest="render",
        action="store_true",
        help="render every page (and one merged edition.pdf) to PDF with Playwright; "
        "needs: pip install playwright pypdf && playwright install chromium",
    )
    ap.add_argument(
        "--serve",
        action="store_true",
        help="after building, serve the output folder over HTTP (Ctrl+C to stop)",
    )
    ap.add_argument(
        "--port", type=int, default=8000, help="port for --serve (default 8000)"
    )
    ap.add_argument(
        "--no-build",
        action="store_true",
        help="skip fetching/building and reuse the existing output folder "
        "(combine with --serve and/or --render)",
    )
    _ = ap.parse_args(argv)
    _.url = fetch_default_manifest_url(_.date)
    return _


# --------------------------------------------------------------------------
# Stage 1: articles
def save_article(record: dict, idx: int, edition: Edition, ctx: BuildContext):
    """Fetch one article, write its localised page. Returns ArticleResult or None."""
    fetched = fetch_article(record, ctx)
    if fetched is None:
        return None
    page_html = build_article_page(fetched, idx, edition.articles, ctx)
    (ctx.out / "articles" / record["file_name"]).write_text(page_html, encoding="utf-8")
    return ArticleResult(
        thumb=f"images/{quote(fetched.thumb)}" if fetched.thumb else None,
        parsed=parse_article(fetched.raw),
        figs={img.local: img.caption for img in fetched.images},
    )


def save_articles(edition: Edition, ctx: BuildContext, workers: int):
    """Process every article in a thread pool. Returns {article id: ArticleResult}."""
    results, records = {}, edition.articles

    def work(i):
        rec = records[i]
        r = save_article(rec, i, edition, ctx)
        if r is None:
            rec["failed"] = True
        else:
            results[rec["id"]] = r
            if r.thumb:
                rec["thumb_path"] = r.thumb
        done = i + 1
        if done % 10 == 0 or done == len(records):
            print(f"  {done}/{len(records)}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, range(len(records))))
    return results


# --------------------------------------------------------------------------
# Stage 2: reconstructed page layouts
def save_pages(
    edition: Edition,
    results: dict,
    out: Path,
    base: str,
    workers: int,
    header: bool,
    pdf_links: bool,
):
    fetch_placed_photos(edition.cci_pages, out, base, workers)
    print("Building page layouts")
    (out / "pages").mkdir(exist_ok=True)
    records = {a["id"]: a for a in edition.articles}
    for pid in edition.page_order:
        node, pno = edition.cci_pages[pid], edition.pno_by_pid[pid]
        scan = fetch_page_scan(node, out, base)
        cards = [
            card
            for child in node.get("children", [])
            if child.get("kind") == "Article"
            for card in [
                parse_card(
                    child,
                    records.get(child["id"]),
                    results.get(child["id"]),
                    out / "images",
                )
            ]
            if card
        ]
        page_html, placed = build_page(
            cards,
            pno,
            edition.npages,
            section=page_section(node["attributes"]),
            label=edition.label,
            scan=scan,
            header=header,
            meta=edition.meta,
            pdf_href=f"../pdf/page-{pno:02d}.pdf" if pdf_links else None,
        )
        (out / "pages" / f"page-{pno:02d}.html").write_text(page_html, encoding="utf-8")
        print(f"  page {pno}: {placed} articles placed")


# --------------------------------------------------------------------------
# Putting it together
def build_edition(
    issue_date: str = (datetime.now() - timedelta(days=datetime.now().time() < time(7, 30))).strftime("%Y-%m-%d"),
    n_workers: int = 8,
    no_header: bool = False,
    render_pdf: bool = False,
    download_dir_path: Path = Path("output_dir"),
):
    manifest_url = fetch_default_manifest_url(issue_date)
    base = base_from_url(manifest_url)
    for sub in ("articles", "assets", "images", "pages/img"):
        (download_dir_path / sub).mkdir(parents=True, exist_ok=True)

    print(f"Fetching manifest: {manifest_url}")
    manifest, cci = fetch_manifests(manifest_url, base, download_dir_path)
    edition = parse_edition(manifest_url, manifest, cci)
    print(f"{len(edition.pages)} pages, {len(edition.articles)} articles")

    ctx = BuildContext(
        base=base,
        out=download_dir_path,
        edition_label=edition.label,
        has_layout=edition.has_layout,
    )
    results = save_articles(edition, ctx, n_workers)

    merged_pdf = None
    if edition.has_layout:
        save_pages(
            edition,
            results,
            download_dir_path,
            base,
            n_workers,
            header=not no_header,
            pdf_links=render_pdf,
        )
        if render_pdf:
            print("Rendering PDFs locally")
            merged_pdf = render_pdfs(download_dir_path)

    index_html = build_index(
        edition.pages, edition.label, edition.has_layout, merged_pdf
    )
    (download_dir_path / "index.html").write_text(index_html, encoding="utf-8")

    failed = [a["resource"] for a in edition.articles if a.get("failed")]
    print(f"\nDone -> {download_dir_path / 'index.html'}")
    if failed:
        print(
            f"{len(failed)} article(s) failed (re-run to retry; finished files are skipped):"
        )
        for f in failed:
            print("  ", f)


def main(args) -> int:
    out = Path(args.out or f"hindu_{issue_from_url(args.url)}")

    if args.no_build:
        if not (out / "index.html").exists():
            print(f"  ! {out} has no index.html; run once without --no-build first")
            return 1
        if args.render:
            print("Rendering PDFs locally")
            render_pdfs(out)
    else:
        build_edition(args.date, args.workers, args.no_header, args.render, out)

    if args.serve:
        return serve(out, port=args.port)
    return 0


# if __name__ == "__main__":
#     import sys
#     sys.exit(main(parse_args()))
