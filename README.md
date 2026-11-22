The current day's paper gets built at **02:00 UTC** and is live at:

https://billydoesdev.github.io/e-newspapers/<paper-code\>/

or, if rendered as a pdf, at:

https://billydoesdev.github.io/e-newspapers/<paper-code\>/paper.pdf

Where, `paper-code` refers to the keys of the `PAPERS` dict. in [`main.py`](https://github.com/BillyDoesDev/e-newspapers/blob/main/main.py).<br>
Eg: `hindu`, for The Hindu (https://billydoesdev.github.io/e-newspapers/hindu/), and so on...


## Usage

```sh
usage: main.py [-h] -p {...} [-d DATE] [--out OUT]

options:
  -h, --help            show this help message and exit
  -p, --paper {...}
                        Paper code to render
  -d, --date DATE       Edition date in the required format [default: today's date]
  --out OUT             download dir [default: 'output_dir']
```


## Disclaimer

All resources scraped are available publically; however, their respective rights belong to the respective owners. Please use this content responsibly and FOR PERSONAL USE ONLY.
