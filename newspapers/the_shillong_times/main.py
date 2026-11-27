#!/usr/bin/env python3
# import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import img2pdf

# import jwt
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

MAX_WORKERS = 8

headers = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0"
}


def get_issue_url(issue_date: str) -> str:
    url = "https://epaper.theshillongtimes.com/wp-admin/admin-ajax.php"
    params = {
        "action": "find_issue",
        "edition": "16",
        "date": issue_date,
    }

    r = requests.get(
        url,
        params=params,
        headers=headers,
    )

    r.raise_for_status()

    print(f"[*] Issue URL for {issue_date}: {r.text}")
    return r.text


def render(
    issue_date: str = datetime.now().strftime("%Y-%m-%d"),
    download_dir_path: Path = Path("output_dir"),
) -> None:
    with requests.Session() as s:
        # Make the connection pool big enough for all worker threads
        s.mount("https://", HTTPAdapter(pool_maxsize=MAX_WORKERS))

        r = s.get(get_issue_url(issue_date), headers=headers)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "lxml")
        pages = soup.find_all("img", class_="w-100")
        print(f"[*] {len(pages)} pages found")

        download_dir = Path(download_dir_path)
        download_dir.mkdir(exist_ok=True, parents=True)

        def download_page(item: tuple[int, BeautifulSoup]) -> Path:
            idx, page = item
            _ = page.get("data-src")
            # token = _.split("token=")[-1]
            # if re.match(r"https:\/\/.*\.(png|jpg|jpeg)", token):
            #     img_url = token
            # else:
            #     img_url = jwt.decode(token.split("&")[0], options={"verify_signature": False})["img"]

            img_url = _

            print(f"[{idx:>02}/{len(pages)}] {img_url[:50]} ...")
            resp = s.get(img_url, timeout=30)
            resp.raise_for_status()

            path = download_dir / f"p-{idx}.jpg"
            path.write_bytes(resp.content)
            return path

        print("[*] Downloading images")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            # map() preserves input order, so paths stay in page order
            paths = list(pool.map(download_page, enumerate(pages, start=1)))

        print("[*] Building PDF")
        (download_dir / "paper.pdf").write_bytes(
            img2pdf.convert([str(p) for p in paths])
        )

        print("[*] Cleaning up images")
        for p in paths:
            p.unlink()

        print(f"[*] Done! PDF saved at {(download_dir / 'paper.pdf')}")


# To convert from images to pdf (imagemagick):
# convert "*.{png,jpg}" outfile.pdf
# Then, you could run `ocrmypdf` on this

# ToDo:
# Per section extraction for higher resolution images, eg:
# https://epaper.theshillongtimes.com/imgsrc/59515/1/410-481-1975-1641
# You can obtain the coordinates (410-481-1975-1641 in this case) from
# the same `data-src` extracted earlier, from the <map> tag
