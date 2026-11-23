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
        if (d / "index.html").exists():
            href, kind = f"{quote(d.name)}/", "site"
        elif (d / "paper.pdf").exists():
            href, kind = f"{quote(d.name)}/paper.pdf", "pdf"
        else:
            continue  # nothing usable in this folder
        label = html.escape(d.name.replace("-", " ").title())
        rows.append(
            f'<li class="paper {kind}"><a href="{href}">{label}</a> '
            f'<span class="kind">{kind}</span></li>'
        )
    return rows


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
