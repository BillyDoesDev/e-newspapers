## The Hindu

can effectively scrape the entire newspaper using https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214446 (can be obtained by inspecting the network flow of the main epaper page: https://epaper.thehindu.com/reader/)<br>
check for all site assets at: https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214446/META-INF/container.xml<br>
also worth checking: https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214587/OPS/package.opf ****(contains content placement metadata)****<br>
and: https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214446/OPS/cciobjects.json (specifically under `resources`)<br>

****the main scraping is from `articles.json`: https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214446/OPS/articles.json****<br>
^ you can map each entry in the newspaper to these<br>
the html and css entries can be loaded as is, and the images need the /Public/ prefic, where mentioned<br>
<br>
sample article: https://epaper.thehindu.com/ccidist-ws/th/th_international/issues/214446/OPS/GQLGPIHTC.1+GCMGQNPE3.1.html<br>

****to query all publication, you could head over to: https://epaper.thehindu.com/ccidist-ws/th/th_international/****<br>
<br>

## TOI
****check this: https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/****<br>
They seem to use the same mechanism, lol<br>

*Turns out, this approach works for most CCI Distribution Servers...* (https://github.com/mashiuruab/ccidist/tree/master)
<br>

This is very easy: get the epub (yes, they let you download it):<br>
https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/issues/157956/?json=true > https://embed-epaper.indiatimes.com/ccidist-ws/toi/toidelhibs_delhi/issues/157956/content.epub<br>
<br>
extract the epub: you'll find the pdfs of all pages over there (wow, I know)<br>
run `grep -oE "href=.*\.pdf\"" ./OPS/package.opf | cut -d\" -f2` in the extracted folder, and you'll get how to arrange them in order<br>
merge them, and you're good to go!<br>

## RSS FEEDS

Applications: Feeder/NewsFlash (thunderbird kinda sucks in this regard, sadly) 

- https://timesofindia.indiatimes.com/rss.cms
- https://www.thehindu.com/rssfeeds/ > default feed: https://www.thehindu.com/feeder/default.rss


---

finally, in the future, to mess with auth (The Hindu), you could tamper with:

```
GET https://id.tinypass.com/id/api/v1/identity/token/verify?browser_id=mwnybta5jbl515i6&page_view_id=mwnybta5vrno39d5&page_title=The+Hindu+ePaper+%7C+Daily+News+and+Current+Affairs&callback=jsonp9836&client_id=zUShAI0Opu&site=https%3A%2F%2Fepaper.thehindu.com&user_state=anon HTTP/2.0
user-agent: Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0
accept: */*
accept-language: en-US,en;q=0.9
accept-encoding: gzip, deflate, br, zstd
sec-fetch-storage-access: none
sec-gpc: 1
referer: https://epaper.thehindu.com/
sec-fetch-dest: script
sec-fetch-mode: no-cors
sec-fetch-site: cross-site
dnt: 1
pragma: no-cache
cache-control: no-cache
te: trailers
```

```
HTTP/2.0 200 
date: Thu, 19 Nov 2026 19:37:06 GMT
content-type: application/javascript
server: cloudflare
cache-control: no-cache, no-store, must-revalidate
pragma: no-cache
expires: Thu, 01 Jan 1970 00:00:00 GMT
access-control-allow-origin: *
access-control-allow-headers: origin, content-type, accept, authorization, pn-consents, user-state
access-control-allow-credentials: true
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS, HEAD
p3p: CP="ALL ADM DEV PSAi COM OUR OTRo STP IND ONL"
x-request-id: Mu5lrotSx18
cf-cache-status: DYNAMIC
vary: accept-encoding
strict-transport-security: max-age=86400; includeSubDomains
set-cookie: __cf_bm=QC8_6oghYp5QmBfrHLV4yZcAb7xG8NGJZjeWz5mxDbw-1795117025.9485536-1.0.1.1-BaYbxj22Vgzt1P4PP9Cjr8z6REmvDQOCnmPviPzD6E4T3C56uAp11y5N4zPk3Qo4nL9JrQjF8tqzjAtIc8nVFbwKgEJvQuCrRuljssk1t04kZNDxPFEoLEQ3wvzw81U6; HttpOnly; SameSite=None; Partitioned; Secure; Path=/; Domain=tinypass.com; Expires=Thu, 19 Nov 2026 20:07:06 GMT
cf-ray: a5d1aae42fbca7b7-BOM
alt-svc: h3=":443"; ma=86400
content-length: 247

jsonp9836({
  "error" : "access_denied",
  "site_cookie_domain" : ".thehindu.com",
  "email_confirmation_required" : null,
  "extend_expired_access_enabled" : false,
  "lwt_enabled" : false,
  "authorized_by_sso" : false,
  "global_mode" : true
})
```
