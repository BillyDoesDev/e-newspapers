#!/usr/bin/env python3

"""
IMPORTANT NOTE

MAKE SURE all render functions have a signature similar to:
    f(..., issue_date: str = '...', ..., download_dir_path: Path = Path('output_dir'), ...)

with the appropriate page specific defaults. The default value for
`download_dir_path` should always be `./output_dir/`.

`issue_date` basically gives the current date, if it's after 7:30 AM;
else it gives the previous date, by default

For the basics of how these work, and where the information is scraped from,
check out `newspapers/hacking.md`
"""

import argparse
import sys
from pathlib import Path
from shutil import copy2, rmtree

from newspapers import (
    _build_pdf_from_ccidist_epub,
    render_anandabazar_patrika,
    render_the_hindu,
    render_the_shillong_times,
    render_times_of_india,
    render_uttarbangasambad,
)

PAPERS = {
    "hindu": render_the_hindu,
    "shillong-times": render_the_shillong_times,
    "times-of-india": render_times_of_india,
    "uttarbangasambad": render_uttarbangasambad,
    "anandabazar-patrika": render_anandabazar_patrika,
}


def _failsafe(download_dir_path: Path = Path("output_dir")):
    print("[X] The scripts failed :/")

    # remove any previous outputs
    rmtree(path=download_dir_path, ignore_errors=True)
    download_dir_path.mkdir(exist_ok=True, parents=True)

    copy2(Path("newspapers/assets/failsafe.html"), download_dir_path / "index.html")
    copy2(Path("newspapers/assets/failsafe.pdf"), download_dir_path / "paper.pdf")

    print(f"[*] Default failsafe outputs generated at {download_dir_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--paper",
        choices=PAPERS,
        help="Paper code to render",
    )
    parser.add_argument(
        "-d",
        "--date",
        help="Edition date in the required format [default: today's date]",
    )
    parser.add_argument(
        "-o",
        "--out",
        help="download dir [default: 'output_dir']",
        default=Path("output_dir"),
    )
    parser.add_argument("-x", "--x-ccidist-epub", help="path to epub")
    args = parser.parse_args()

    if not (args.paper or args.x_ccidist_epub):
        print("Error: need to specify either --paper or --x-ccidist-epub")
        sys.exit(1)

    rmtree(path=args.out, ignore_errors=True)

    if args.x_ccidist_epub:
        _build_pdf_from_ccidist_epub(args.x_ccidist_epub, args.out)
        return

    try:
        if args.date:
            PAPERS[args.paper](issue_date=args.date, download_dir_path=args.out)
        else:  # let the respective functions handle the default dates
            PAPERS[args.paper](download_dir_path=args.out)
    except Exception as e:  # noqa: BLE001
        print("[X] Exception:", e)
        _failsafe(download_dir_path=args.out)


if __name__ == "__main__":
    main()
