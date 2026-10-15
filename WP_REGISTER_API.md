# WP onelogin Register API — декодировано из poczta.o2.pl/rejestracja (14.10)

Backend всех WP Holding доменов (wp.pl / o2.pl / tlen.pl / interia-нет):
`https://konto.wp.pl/api/v1/public/wpk-identity-provider`

Fetch-wrapper (module ZP): `credentials:"include"`, `Content-Type: application/json`,
header `Init-Timestamp: <initTimestamp из register/init>`.

## Флоу

1. `GET /register/init` → `{"initTimestamp": "<107 chars>"}` (+ cookies сессии)
2. Turnstile solve: sitekey `0x4AAAAAAAaystnvZAOebwrA`, page `https://poczta.o2.pl/rejestracja`
   → sidecar :8877 `{"type":"turnstile","sitekey":...,"url":...,"real_page":true}` = 5с FREE
3. `PUT /recaptcha/token` + header `X-Recaptcha: <turnstile-token>` + `Init-Timestamp`
   → 200, response header `X-Recaptcha-Mu` (78 chars)
4. `POST /register/available?login_challenge=<challenge>` body `{"login":"<user>"}`
   + headers `X-Recaptcha-Mu`, `Init-Timestamp` → **412 (блокер, см. ниже)**
5. `POST /register/submit_v2?login_challenge=...` body
   `{"login","firstName","lastName","birthdate","password","gender","marketing":[...],"agreements":{"<id>":true,...},"initTimestamp","token","loginChallenge"}`
6. `POST /verify` — PIN на recovery email (submit принимает `recovery` = email для PIN)

Agreement IDs и brand (o2/tlen/wp) — из init/config ответа (brand=o2).
reCAPTCHA v3 sitekey wp: `6LdH36IZAAAAAGgJDr06cH1m9hZG7B6GSpq4u5Ve` (legacy, сейчас Turnstile).

## Блокер 412 на /register/available

Проверено: без challenge / `login_challenge=undefined` / с Init-Timestamp / с cookies —
всё 412, пустое тело. Wrapper ничего не добавляет (чистый fetch).
Гипотезы (не проверены):
- `login_challenge` — реальный ID из другого источника (hydra/ory-flow: challenge создаётся
  при входе на /zaloz-konto через redirect-цепочку с client_id)
- требуется заголовок brand (`X-Brand: o2`?) или session cookie от первой загрузки SPA
- 412 = precondition failed по ихнему WAF (проверить ответ с браузерными заголовками
  sec-ch-ua/sec-fetch-* — curl-заголовки могут резаться)

Следующий шаг: пройти реальный флоу в браузере с network-логом (browser_exec +
performance entries / CDP Network) и снять точные заголовки запроса available.
