#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path

HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Redirecting…</title>
  <meta http-equiv="refresh" content="0; url=$REDIRECT_URL">
  <link rel="canonical" href="$REDIRECT_URL">
  <script>location.replace("$REDIRECT_URL");</script>
</head>
<body>
  <p>Redirecting to <a href="$REDIRECT_URL">$REDIRECT_URL</a>…</p>
</body>
</html>
"""


def render(
    issue_date: str = datetime.now().strftime("%d-%m-%Y"),
    download_dir_path: Path = Path("output_dir"),
):
    download_dir_path.mkdir(exist_ok=True, parents=True)
    (download_dir_path / "index.html").write_text(
        HTML.replace("$REDIRECT_URL", f"https://uttarbangasambad.in/uploadedfiles/UBS-SLG-{issue_date}.pdf")
    )
