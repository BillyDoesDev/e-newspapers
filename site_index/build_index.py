#!/usr/bin/env python3
"""Fill site_index/index.html with links to every paper under the site dir."""

import argparse
import datetime
import html
import pathlib
from urllib.parse import quote

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def collect_rows(root: pathlib.Path) -> list[str]:
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        dir_label = html.escape(d.name.replace("-", " ").title())

        if (d / "index.html").exists():
            rows.append(_row(f"{quote(d.name)}/", "site", dir_label))
            continue

        pdfs = sorted(
            f for f in d.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"
        )
        for pdf in pdfs:
            if len(pdfs) == 1:
                label = dir_label
            else:
                stem = (
                    pdf.stem.removeprefix("paper_").replace("_", " ").replace("-", " ")
                )
                label = f"{dir_label}: {html.escape(stem.title())}"
            rows.append(_row(f"{quote(d.name)}/{quote(pdf.name)}", "pdf", label))
    return rows


def _row(href: str, kind: str, label: str) -> str:
    return (
        f'<li class="paper {kind}"><a href="{href}">{label}</a> '
        f'<span class="kind">{kind}</span></li>'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--site", default="site", help="dir containing one folder per paper"
    )
    ap.add_argument("--template", default="site_index/index.html")
    ap.add_argument("--out", default=None, help="default: <site>/index.html")
    args = ap.parse_args()

    root = pathlib.Path(args.site)
    out = pathlib.Path(args.out) if args.out else root / "index.html"

    built = datetime.datetime.now(IST).strftime("%a %d %b %Y, %H:%M IST")
    page = pathlib.Path(args.template).read_text(encoding="utf-8")
    page = page.replace("{{LINKS}}", "\n    ".join(collect_rows(root)))
    page = page.replace("{{BUILT}}", built)
    out.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
