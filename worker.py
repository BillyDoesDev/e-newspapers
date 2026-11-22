#!/usr/bin/env python3

"""
Refer to cloudflare's pywrangler docs for more
tree:
.
├── .env
├── pyproject.toml
├── src
│  └── worker.py
└── wrangler.jsonc

dependencies:
"fastapi[standard]",
"requests",
"workers-py",
"workers-runtime-sdk"

wrangler.jsonc:
{
        "name": "e-paper-services",
        "main": "src/worker.py",
        "compatibility_date": "2026-08-01",
        "compatibility_flags": [
                "python_workers"
        ],
        "observability": {
                "enabled": true
        }
}
"""

"""
This file is src/worker.py
author: BillyDoesDev
"""

import requests
from fastapi import FastAPI
from fastapi import Response as fastapi_Response
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="e-papers")

# Allow every origin, method and header.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,  # must be False when allow_origins is "*"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@app.get("/")
def index():
    return {"message": "go to /docs for more"}


def _fetch_default_manifest_url(issue_date: str) -> str:
    url = "https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/issues/"

    params = {
        "start": "",
        "limit": "100",
        "epubName": "toidelhibs_delhi_web",
        "fromDate": issue_date,
        "toDate": issue_date,
        "issueNameContains": "",
        "sortOrder": "desc",
        "draft": "true",
        "skipSections": "true",
        "submit": "Submit Query",
    }

    response = requests.get(url, params=params, headers=HEADERS)
    print(response.headers)
    if response.status_code == 403:
        print(response.text)

    response.raise_for_status()

    data = response.json()
    return data["issues"]["web"][0]["url"] + "content.epub"


@app.get("/issue/{issue_date}")
def get_epub_url(issue_date: str):
    return _fetch_default_manifest_url(issue_date)


@app.get("/epub/{issue_date}")
def get_epub_binary(issue_date: str):

    # this basically only runs when run through normal uvicorn, not via workers
    # in this case, we are of the assumption that CPU time is unlimited
    epub_url = _fetch_default_manifest_url(issue_date)
    r = requests.get(epub_url, headers=HEADERS, timeout=6000)
    r.raise_for_status()
    return fastapi_Response(content=r.content, media_type="application/octet-stream")


##############################################################
# workers specific
try:
    import asgi
    from workers import WorkerEntrypoint, fetch

    class Default(WorkerEntrypoint):
        async def fetch(self, request):
            # intercept start
            path = request.url.split("?")[0].rstrip("/")
            if "/epub/" in path:
                print("intercepted")
                issue_date = path.split("/")[-1]
                epub_url = _fetch_default_manifest_url(issue_date)

                # this bit is run natively on the worker's runtime (JS), not python
                # this saves on python's CPU time, allowing us to run stuff for cheap,
                # without hitting limits, like in the case earlier, where the CPU
                # effectively waits the entire time for the request to load

                upstream = await fetch(epub_url, headers=HEADERS)
                return upstream  # body is passed through by the runtime
            # intercept end

            return await asgi.fetch(app, request, self.env)

except ModuleNotFoundError:
    pass
