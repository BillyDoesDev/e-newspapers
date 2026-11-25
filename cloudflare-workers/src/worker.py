#!/usr/bin/env python3

from datetime import datetime

import requests
from fastapi import FastAPI
from fastapi import Response as fastapi_Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@app.get("/")
def index():
    return {"message": "go to /docs for more"}


def _fetch_default_ccidist_manifest_url(
    base_url: str, epub_name: str, issue_date: str
) -> str:
    params = {
        "start": "",
        "limit": "100",
        "epubName": epub_name,
        "fromDate": issue_date,
        "toDate": issue_date,
        "issueNameContains": "",
        "sortOrder": "desc",
        "draft": "true",
        "skipSections": "true",
        "submit": "Submit Query",
    }

    response = requests.get(
        base_url, params=params, headers={**HEADERS, "Referer": base_url}
    )
    # print(response.headers)
    if response.status_code == 403:
        print(response.text)

    response.raise_for_status()

    data = response.json()
    return data["issues"]["web"][0]["url"] + "content.epub"


class Epub(BaseModel):
    base_url: str
    name: str
    issue_date: str | None = datetime.now().strftime("%Y-%m-%d")


@app.post("/epub")
def get_epub_binary(e: Epub):

    # this basically only runs when run through normal uvicorn, not via workers
    # in this case, we are of the assumption that CPU time is unlimited
    epub_url = _fetch_default_ccidist_manifest_url(e.base_url, e.name, e.issue_date)
    r = requests.get(epub_url, headers={**HEADERS, "Referer": e.base_url}, timeout=6000)
    r.raise_for_status()
    return fastapi_Response(content=r.content, media_type="application/octet-stream")


##############################################################
# workers specific
try:
    import json

    import asgi
    from workers import Request, WorkerEntrypoint, fetch

    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request):
            # intercept start
            print(">>> Traffic intercepted!")
            if request.method == "POST" and "epub" in request.url:
                e: dict = await request.json()
                epub_url = _fetch_default_ccidist_manifest_url(
                    e["base_url"], e["name"], e["issue_date"]
                )

                # this bit is run natively on the worker's runtime (JS), not python
                # this saves on python's CPU time, allowing us to run stuff for cheap,
                # without hitting limits, like in the case earlier, where the CPU
                # effectively waits the entire time for the request to load
                upstream = await fetch(
                    epub_url, headers={**HEADERS, "Referer": e["base_url"]}
                )
                return upstream  # body is passed through by the runtime
            # intercept end

            return await asgi.fetch(app, request, self.env)

        async def scheduled(self, controller, env, ctx):
            print("[*] preparing GH dispatch...")
            env = self.env
            # https://docs.github.com/en/rest/repos/repos?apiVersion=2026-03-10#create-a-repository-dispatch-event
            resp = await fetch(
                f"https://api.github.com/repos/{env.GH_REPO}/dispatches",
                method="POST",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {env.GH_TOKEN}",
                    "X-GitHub-Api-Version": "2026-03-10",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0",
                    "Content-Type": "application/json",
                },
                body=json.dumps({"event_type": "on_demand_build"}),
            )
            print("[*] ret status:", resp.status)
            if resp.status != 204:
                raise RuntimeError(
                    f"Dispatch failed: {resp.status} {await resp.text()}"
                )

except ModuleNotFoundError:
    pass
