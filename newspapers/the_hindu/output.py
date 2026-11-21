"""Things to do with a finished folder: render pages to PDF, serve over HTTP.
"""

import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PAGE_MM = (350, 540)  # printed page size in mm
RENDER_VIEWPORT = {"width": 1323, "height": 2041}


# ==========================================================================
# Render to PDF (Playwright)
# ==========================================================================
READY_JS = """async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images).map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
  if (window.__fit) window.__fit();
}"""

INSTALL_HINT = "pip install playwright pypdf && playwright install chromium"


def page_files(out: Path):
    """out/pages/page-NN.html in page order."""
    def number(p):
        m = re.search(r"page-(\d+)\.html$", p.name)
        return int(m.group(1)) if m else 0

    return sorted((out / "pages").glob("page-*.html"), key=number)


def render_pdfs(out: Path):
    """Render every page in out/pages/ to out/pdf/page-NN.pdf and merge them into
    out/edition.pdf (needs pypdf). Returns the merged file name, or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"  ! --render needs Playwright:  {INSTALL_HINT}", file=sys.stderr)
        return None

    sources = page_files(out)
    if not sources:
        print(f"  ! no pages found in {out / 'pages'}; nothing to render", file=sys.stderr)
        return None

    pdf_dir = out / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    made = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page(viewport=RENDER_VIEWPORT)
            for src in sources:
                dst = pdf_dir / src.with_suffix(".pdf").name
                pg.goto(src.resolve().as_uri())
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
                print(f"  pdf {src.stem}")
            browser.close()
    except Exception as e:
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


# ==========================================================================
# Serve over HTTP
# ==========================================================================
def serve(out: Path, port: int = 8000, host: str = "127.0.0.1") -> int:
    """Block serving `out` until Ctrl+C. Returns a process exit code."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(out))
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        print(f"  ! cannot listen on {host}:{port}: {e}  (try --port)")
        return 1
    print(f"\nServing {out} at http://{host}:{httpd.server_address[1]}/   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
