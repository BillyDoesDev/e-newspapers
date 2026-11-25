#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import img2pdf
import requests
from bs4 import BeautifulSoup

MAX_WORKERS = 12

headers = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0"
}


def get_page_urls(issue_date: str = datetime.now().strftime("%Y-%m-%d")) -> list[str]:
    idx = 1
    pages = []

    print("[*] Looking for pages...")
    while True:
        r = requests.get(
            f"https://epaper.anandabazar.com/api/v1/next-page/kolkata/15/{issue_date}/page-{idx}.html",
            headers=headers,
        )
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError:
            break
        idx += 1

        img_url = (
            "https"
            + BeautifulSoup(r.json()["data"]["epaper_page_data"], features="lxml")
            .find("img", class_="panzoom-image")
            .get("data-full")
            .split("https")[-1]
        )
        pages.append(img_url)

    print(f"[*] {len(pages)} pages found")
    return pages


def render(
    issue_date: str = datetime.now().strftime("%Y-%m-%d"),
    download_dir_path: Path = Path("output_dir"),
):
    pages = get_page_urls(issue_date=issue_date)

    download_dir = Path(download_dir_path)
    download_dir.mkdir(exist_ok=True, parents=True)

    def download_page(item: tuple[int, str]) -> Path:
        idx, url = item
        print(f"[{idx:>02}/{len(pages)}] {url} ...")
        r = requests.get(url, headers=headers, timeout=3000)
        r.raise_for_status()

        path = download_dir / f"p-{idx}.jpg"
        path.write_bytes(r.content)
        return path

    print("[*] Downloading pages")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        paths = list(pool.map(download_page, enumerate(pages, start=1)))

    print("[*] Building PDF")
    (download_dir / "paper.pdf").write_bytes(img2pdf.convert([str(p) for p in paths]))

    print("[*] Cleaning up images")
    for p in paths:
        p.unlink()

    print(f"[*] Done! PDF saved at {(download_dir / 'paper.pdf')}")
