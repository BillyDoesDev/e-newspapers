# The Hindu e-paper archiver

Run `python hindu_epaper_archive.py --help`. Data flows one way:

    fetching.py  ->  parsing.py  ->  building.py  ->  output.py
    (download)       (understand)    (write HTML)     (--render / --serve)

| File | What lives there |
|---|---|
| `hindu_epaper_archive.py` | CLI flags and the step-by-step pipeline (read this first) |
| `fetching.py` | HTTP session, downloads. Never builds pages |
| `parsing.py` | Raw JSON / HTML -> plain data (dataclasses at the top). No network, no HTML output |
| `building.py` | Plain data -> HTML strings. No network, no scraping |
| `output.py` | `--render` (PDF via Playwright) and `--serve` (local HTTP) |
| `static/` | Our CSS, JS and HTML snippets. Edit these to change the look |
