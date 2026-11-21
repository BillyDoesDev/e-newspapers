#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path
from shutil import rmtree
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup
from pypdf import PdfWriter

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/",
}


def fetch_default_manifest_url(issue_date: str) -> str:
    url = "https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/issues/"

    params = {
        "start": "",
        "limit": "",
        "epubName": "toidelhibs_delhi_web",
        "fromDate": issue_date,
        "toDate": issue_date,
        "issueNameContains": "",
        "sortOrder": "desc",
        "draft": "true",
        "skipSections": "true",
        "submit": "Submit Query",
    }

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["issues"]["web"][0]["url"] + "content.epub"


def render(
    issue_date: str = datetime.now().strftime("%Y-%m-%d"),
    download_dir_path: Path = Path("output_dir"),
    no_download: bool = False,
):
    download_dir_path.mkdir(parents=True, exist_ok=True)
    epub_path = download_dir_path / "download.epub"
    extract_dir = download_dir_path / "extracted"

    epub_url = fetch_default_manifest_url(issue_date)
    print("[*] fetched epub url:", epub_url)

    if not no_download:
        print("[*] downloading epub...")
        r = requests.get(epub_url, headers=headers, timeout=60)
        r.raise_for_status()
        epub_path.write_bytes(r.content)

    print(f"[*] extracting to {extract_dir}")
    extract_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(epub_path) as epub:
        root = extract_dir.resolve()
        for member in epub.infolist():
            target = (extract_dir / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path in EPUB archive: {member.filename}")
            epub.extract(member, extract_dir)

    # build pdf merge order
    opf_path = extract_dir / "OPS" / "package.opf"
    print(f"[*] reading {opf_path}")

    if not opf_path.is_file():
        raise FileNotFoundError(f"Could not find {opf_path}")

    pdf_paths = []
    soup = BeautifulSoup(opf_path.read_text(), features="xml")
    for i in soup.find_all("item"):
        if i.get("href").endswith(".pdf"):
            pdf_paths.append(extract_dir / "OPS" / i.get("href"))

    if not pdf_paths:
        raise FileNotFoundError("No existing PDF files were referenced in package.opf")

    # Merge in OPF order.
    output_path = download_dir_path / "paper.pdf"
    writer = PdfWriter()

    for pdf_path in pdf_paths:
        # print(f"[*] adding: {pdf_path.relative_to(extract_dir)}")
        writer.append(str(pdf_path))

    with output_path.open("wb") as output_file:
        writer.write(output_file)

    writer.close()
    print(f"[*] Done! Merged {len(pdf_paths)} PDFs into {output_path}")

    # cleanup
    rmtree(extract_dir, ignore_errors=True)


if __name__ == "__main__":
    render(no_download=True)
