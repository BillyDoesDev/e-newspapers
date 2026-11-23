#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path
from shutil import rmtree
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup
from pypdf import PdfWriter


def _build_pdf_from_ccidist_epub(
    epub_path: Path, download_dir_path: Path, paper_name: str = "paper"
) -> None:
    extract_dir = download_dir_path / "extracted"
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
    output_path = download_dir_path / f"{paper_name}.pdf"
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


def _download_epub(epub_dl_path: Path, base_url: str, name: str, issue_date: str):
    print(f"[*] downloading epub: {name} ...")
    # backend logic comes from `worker.py` in the project root
    # check it out! :D

    r = requests.post(
        "https://e-paper-services.octopus-lure-shiny.workers.dev/epub",
        json={
            "base_url": base_url,
            "name": name,
            "issue_date": issue_date,
        },
        headers={"accept": "application/json"},
    )
    r.raise_for_status()
    epub_dl_path.write_bytes(r.content)


def render(
    issue_date: str = datetime.now().strftime("%Y-%m-%d"),
    download_dir_path: Path = Path("output_dir"),
):
    download_dir_path.mkdir(parents=True, exist_ok=True)
    epub_path = download_dir_path / "download.epub"

    editions = ["toidelhibs_delhi", "toikolkatabs_kolkatacity"]

    for e in editions:
        _download_epub(
            epub_path,
            f"https://embed-epaper.indiatimes.com/ccidist-ws/toi/{e}/issues/",
            f"{e}_web",
            issue_date,
        )
        _build_pdf_from_ccidist_epub(epub_path, download_dir_path, e.split("_")[1])
