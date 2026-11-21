#!/usr/bin/env python3

"""Network + disk: download manifests, article pages and the files they reference.

Only resources under Public/ are requested. Never builds pages.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from .parsing import (
    ArticleImage,
    BuildContext,
    FetchedArticle,
    css_local_path,
    css_references,
    gallery_items,
    strip_query,
    stylesheet_hrefs,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (personal-archive script)"
REQUEST_DELAY = 0.05  # seconds between requests per worker


# ==========================================================================
# HTTP
# ==========================================================================
def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
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
    time.sleep(REQUEST_DELAY)
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


# ==========================================================================
# Downloads
# ==========================================================================
def fetch_default_manifest_url(issue_date: str = datetime.now().strftime("%Y-%m-%d")) -> str:
    url = "https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/"

    params = {
        "start": "",
        "limit": "",
        "epubName": "th_international_web",
        "fromDate": issue_date,
        "toDate": issue_date,
        "issueNameContains": "",
        "sortOrder": "desc",
        "draft": "true",
        "skipSections": "true",
        "submit": "Submit Query",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0",
        "Accept": "application/json, text/html, */*",
        "Referer": "https://epaper.thehindu.com/ccidist-ws/th/th_international/",
    }

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["issues"]["web"][0]["url"] + "OPS/articles.json"


# ---- manifests -------------------------------------------------------------
def fetch_manifests(url: str, base: str, out: Path):
    """Save and load articles.json (required) and cciobjects.json (optional -> None)."""
    manifest_bytes = http_get(url)
    (out / "articles.json").write_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes)

    cci = None
    try:
        cci_bytes = http_get(base + "cciobjects.json")
        (out / "cciobjects.json").write_bytes(cci_bytes)
        cci = json.loads(cci_bytes)
    except Exception as e:  # noqa: BLE001
        print(
            f"  ! cciobjects.json unavailable ({e}); skipping page layouts",
            file=sys.stderr,
        )
    return manifest, cci


# ---- articles --------------------------------------------------------------
def fetch_css_tree(css_url: str, assets: Path, seen: set, base: str):
    """Download a stylesheet plus anything it references via url()."""
    rel = css_local_path(css_url, base)
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
    for ref in css_references(css):
        sub = urljoin(css_url, ref)
        sub_rel = strip_query(sub[len(base) :]) if sub.startswith(base) else None
        if sub_rel and sub_rel not in seen:
            seen.add(sub_rel)
            download(sub, assets / sub_rel)


def fetch_article(record: dict, ctx: BuildContext):
    """Download one article page, its stylesheets and gallery images.
    Returns a FetchedArticle, or None if the page itself could not be fetched."""
    res = record["resource"]
    url = urljoin(ctx.base, res)
    try:
        raw = http_get(url).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"  ! article {res}: {e}", file=sys.stderr)
        return None

    for href in stylesheet_hrefs(raw):
        with ctx.lock:
            fetch_css_tree(
                urljoin(url, href), ctx.out / "assets", ctx.css_seen, ctx.base
            )

    images = []
    for item in gallery_items(raw, record):
        local = Path(strip_query(item.ref)).name
        if download(urljoin(ctx.base, item.ref), ctx.out / "images" / local):
            images.append(ArticleImage(n=item.n, local=local, caption=item.caption))
    return FetchedArticle(record=record, url=url, raw=raw, images=images)


# ---- page layout assets ----------------------------------------------------
def fetch_page_scan(page: dict, out: Path, base: str):
    """Only the page preview (a Public/ resource) is used. The page backdrop and PDF listed in
    cciobjects.json have no Public/ prefix, so they are not for public access and are never
    requested. Returns the local file name, or None."""
    for rk in page.get("resources", {}):
        if rk.startswith("Public/") and rk.endswith("_medium-900.jpg"):
            name = Path(rk).name.replace("+", "_")
            if download(urljoin(base, rk), out / "pages" / "img" / name):
                return name
            return None
    return None


def fetch_placed_photos(cci_pages: dict, out: Path, base: str, workers: int = 4):
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
                    urljoin(base, r), out / "images" / Path(strip_query(r)).name
                ),
                sorted(refs),
            )
        )
