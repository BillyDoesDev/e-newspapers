Make sure you have your `wrangler.jsonc` and an appropriate `.dev.vars` ready. </br>
Project files should be structured under `src/`.

Then:

```sh
docker compose build --no-cache # later, docker compose build

# now we have built the image, and are good to go
# start a shell with [uses .env vars]:
docker exec -it pywrangler-shell bash
# then, inside the shell
pywrangler secret bulk .dev.vars && pywrangler dev --ip 0.0.0.0 # start dev server
pywrangler secret bulk .dev.vars && pywrangler deploy           # non-interactively deploy
```

Sample `.env`:

```sh
CLOUDFLARE_API_TOKEN=xxx
CLOUDFLARE_ACCOUNT_ID=xxx
DO_NOT_TRACK=1
WRANGLER_SEND_METRICS=false
```

### Notes

As of writing, node v26 seems to be buggy with pywrangler -- which happens to be the main motivation behind this docker based setup.
