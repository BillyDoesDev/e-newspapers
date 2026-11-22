#!/usr/bin/env python3

"""
IMPORTANT NOTE

Make sure all render functions have a signature similar to:
    f(..., issue_date: str = '...', ..., download_dir_path: Path = Path('output_dir'), ...)

with the appropriate page specific defaults. The default value for
`download_dir_path` should always be `./output_dir/`.

`issue_date` basically gives the current date, if it's after 7:30 AM;
else it gives the previous date, by default

For the basics of how these work, and where the information is scraped from,
check out `newspapers/hacking.md`
"""

import argparse
from pathlib import Path
from shutil import rmtree

from newspapers import (
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
    "abp": render_anandabazar_patrika,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--paper",
        choices=PAPERS,
        required=True,
        help="Paper code to render",
    )
    parser.add_argument(
        "-d",
        "--date",
        help="Edition date in the required format [default: today's date]",
    )
    parser.add_argument(
        "--out", help="download dir [default: 'output_dir']", default="output_dir"
    )
    args = parser.parse_args()

    rmtree(path=args.out, ignore_errors=True)

    if args.date:
        PAPERS[args.paper](issue_date=args.date, download_dir_path=Path(args.out))
    else:
        PAPERS[args.paper](download_dir_path=Path(args.out))


if __name__ == "__main__":
    main()
