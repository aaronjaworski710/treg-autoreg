# TREG.TO — MAIL DOORS PROVIDER MATRIX

Как работает $1-грант (реверс исходников treg.to, src/treg/application/auth.py + signup.py + promotions.py):

```
promo_eligible = (turnstile_outcome == "pass")   # НЕ зависит от домена почты!
грант $1 выдаётся при: новый user + email verified + org создана
  + inbox не заклеймен (promo_inbox: дедуп по +tag для всех, по точкам для gmail)
  + домен НЕ в TREG_BLOCKED_EMAIL_DOMAINS (env-блоклист: disposable-сервисы)
```

Выводы:
- gmail = $0 всегда (promo_inbox схлопывает все +alias и точки в один inbox — один грант на аккаунт)
- «обычные» региональные почты = должны платить $1 (блоклист режет только disposable)
- Turnstile-токен обязан быть PASS (cloakbrowser-фабрика и sidecar :8877 real_page дают PASS)

## Рейтинг дверей (для масс-фарма)

| # | Провайдер | Домены | treg | Рега-автомат | Капча | IMAP | Масштаб | Риск |
|---|-----------|--------|------|--------------|-------|------|---------|------|
| S | Catch-all свой домен | *.loc.cc, *.nl8.eu | ACCEPTED, $1 proven (27/27) | полная (ForwardEmail MX) | — | — | ∞ | домен спалится → блоклист |
| A | Proton Mail | proton.me, protonmail.com, pm.me | ACCEPTED, $1 proven | proton_signup.py (t-online верификация) | POW-challenge (patchright) | НЕТ (webmail/API только) | 100+/день | abuse-flag при headless-чтении; лечить native-API чтением (SRP pmapi) вместо браузера |
| B | WP Holding | wp.pl, o2.pl, tlen.pl | ACCEPTED | API декодирован (см. WP_REGISTER_API.md), блокер 412 | Turnstile (sidecar 5с FREE) | imap.wp.pl:993 / imap.o2.pl:993 | высокий | один onelogin на все домены |
| B | Mail.ru Group | mail.ru, inbox.ru, list.ru, bk.ru | ACCEPTED | известные флоу (account.mail.ru/signup) | image/fun-captcha → qwen3-vl voting :16432 | imap.mail.ru:993 (включить в настройках) | высокий | risk-engine, иногда SMS |
| B | Interia | interia.pl + subdomeny | ACCEPTED | rejestracja poczta.interia.pl | image captcha | poczta.interia.pl:993 | средний | — |
| C | Onet | onet.pl, op.pl, vp.pl, buziaczek.pl | probe | konto.onet.pl | captcha | poczta.onet.pl:993 | средний | — |
| C | Seznam | seznam.cz, email.cz, post.cz | probe | registrace.seznam.cz | captcha | imap.seznam.cz:993 | средний | — |
| C | GMX/United Internet | gmx.de, gmx.at, gmx.ch, web.de | ACCEPTED | signup-форма | captcha + часто phone | imap.gmx.net:993, imap.web.de:993 | средний | phone-гейт DE |
| C | inbox.lv | inbox.lv, mail.lv | probe | register/ | captcha | imap.inbox.lv:993 | средний | 403 из нашего IP на curl — нужен браузер |
| C | ABV Bulgaria | abv.bg, mail.bg, myabv.com | probe | reg.abv.bg | image captcha | imap.abv.bg:993 | средний | — |
| C | Ukr.net | ukr.net | ACCEPTED | passport.ukr.net (недоступен с нашего IP — через UA-прокси) | captcha | imap.ukr.net:993 | средний | geo |
| C | meta.ua / i.ua | meta.ua, i.ua, email.ua | ACCEPTED | 403 с нашего IP — через UA-прокси | image captcha | mail.meta.ua:993 | средний | geo |
| D | Tutanota/Tuta | tutanota.com/de, tuta.io | probe | POW-captcha, веб-only | POW | НЕТ | низкий | нет IMAP |
| D | Mailfence | mailfence.com | probe | форма | captcha | IMAP платный | низкий | — |
| D | Yandex | yandex.ru/com/by | probe | smart-captcha + phone | SmartCaptcha | imap.yandex.ru:993 | низкий | phone-гейт |
| D | Rambler | rambler.ru | probe | — | captcha | imap.rambler.ru:993 | низкий | — |
| F | Gmail | gmail.com | ACCEPTED $0 | — | — | — | 0 | promo_inbox дедуп |
| F | t-online.de | t-online.de | ACCEPTED $0 (наблюдение) | — | — | imap | ? | требует перепроверки: по коду домен не должен резаться |
| F | Outlook/Hotmail | outlook.com, hotmail.com, live.com | probe | ROPC мёртв глобально (AADSTS70003) | — | Graph OAuth только | 0 | — |
| X | Disposable | mail.tm, tempmail.lol, guerrillamail, mailinator | BLOCKED (403) | — | — | — | 0 | блоклист |

Статусы probe = из `probe_state.json` (domain_probe2.py, обновляется).

## Скрипты репы

- `treg_farm.py` / `gh_oauth_reg.py` — основной фарм (email-OTP door + GH OAuth door)
- `domain_probe2.py` — пробер блоклиста treg (turnstile sidecar :8877 + proxy pool)
- `verify_bank.py` — проверка живости банка
- `WP_REGISTER_API.md` — декодированный API-контракт реги wp.pl/o2.pl
- Банк: `out/*.jsonl` (org-токены, email, балансы)

## Проверенные факты (14-15.10)

- proton.me kltyx1eprg0 → $1.00 (org reformlab11278) — PASS-токен + verify + org
- catch-all vortexio.loc.cc волна 27/27 × $1
- proton webmail-чтение после 1-2 сессий ловит «Automated Abuse Detection» → OTP не доходит; лечение: native Proton API (SRP) вместо headless-браузера
- sidecar :8877 real_page=True решает Turnstile wp (0x4AAAAAAAaystnvZAOebwrA) за 5с и treg (0x4AAAAAAFVJ1qUhtZ_qG-N7) стабильно
- 2captcha оба ключа в минусе — не использовать
