## Just a bunch of e-newspapers

Site live at: https://billydoesdev.github.io/e-newspapers/<br>
Daily builds at **07:30 IST**<br>

LICENSE: [MIT](LICENSE)

## Usage

First, clone and cd into the project

```sh
git clone https://github.com/BillyDoesDev/e-newspapers.git
cd e-newspapers
```

### Running locally

Recommended workflow: using [uv](https://docs.astral.sh/uv/)

```sh
uv sync
uv run main.py --help
```

### Using Docker

```
docker compose run --rm --build e_newspaper_builder [args...]
```
---

```sh
usage: main.py [-h] -p {times-of-india,...} [-d DATE] [--out OUT]

options:
  -h, --help            show this help message and exit
  -p, --paper {times-of-india,...}
                        Paper code to render
  -d, --date DATE       Edition date in the required format [default: today's date]
  --out OUT             download dir [default: 'output_dir']
```


## Deploy structure

The current day's paper gets built at **07:30 IST** and is live at:

https://billydoesdev.github.io/e-newspapers/<paper-code\>/

or, if rendered as a pdf, at:

https://billydoesdev.github.io/e-newspapers/<paper-code\>/paper.pdf

Where, `paper-code` refers to the keys of the `PAPERS` dict. in [`main.py`](https://github.com/BillyDoesDev/e-newspapers/blob/main/main.py).<br>
Eg: `hindu`, for The Hindu (https://billydoesdev.github.io/e-newspapers/hindu/), and so on...


## Disclaimer

All resources scraped are available publically; however, their respective rights belong to the respective owners. Please use this content responsibly and **FOR PERSONAL USE ONLY.**
