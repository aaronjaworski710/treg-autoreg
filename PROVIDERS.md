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
| B | WP Holding | wp.pl, o2.pl, tlen.pl, poczta.fm | ACCEPTED (probe 15.10) | API декодирован (см. WP_REGISTER_API.md), блокер 412 | Turnstile (sidecar 5с FREE) | imap.wp.pl:993 / imap.o2.pl:993 | высокий | один onelogin на все домены |
| B | Mail.ru Group | mail.ru, inbox.ru, list.ru, bk.ru | ACCEPTED (probe 15.10) | account.mail.ru/signup; референсы: Draugrol/MailRu-autoreg, entreee/MailRuAutoreg-python (forks у aaronjaworski710) | image/fun-captcha → qwen3-vl voting :16432 | imap.mail.ru:993 (включить в настройках) | высокий | risk-engine, иногда SMS |
| B | Interia | interia.pl + subdomeny | ACCEPTED (probe 15.10) | rejestracja poczta.interia.pl | image captcha | poczta.interia.pl:993 | средний | — |
| C | Onet | onet.pl, op.pl, vp.pl, buziaczek.pl, amorki.pl | ACCEPTED (probe 15.10) | konto.onet.pl | captcha | poczta.onet.pl:993 | средний | — |
| C | Seznam/Atlas SK | seznam.cz, email.cz, post.cz, atlas.sk, azet.sk, zoznam.sk | ACCEPTED (probe 15.10) | registrace.seznam.cz | captcha | imap.seznam.cz:993 | средний | — |
| C | GMX/United Internet | gmx.de, gmx.at, gmx.ch, web.de | ACCEPTED (probe 15.10) | signup-форма | captcha + часто phone | imap.gmx.net:993, imap.web.de:993 | средний | phone-гейт DE |
| C | inbox.lv | inbox.lv, mail.lv | probe | register/ | captcha | imap.inbox.lv:993 | средний | 403 из нашего IP на curl — нужен браузер |
| C | ABV Bulgaria | abv.bg, mail.bg, myabv.com | probe | reg.abv.bg | image captcha | imap.abv.bg:993 | средний | — |
| C | Ukr.net | ukr.net | ACCEPTED | passport.ukr.net (недоступен с нашего IP — через UA-прокси) | captcha | imap.ukr.net:993 | средний | geo |
| C | meta.ua / i.ua / email.ua / ua.fm | meta.ua, i.ua, email.ua, ua.fm | ACCEPTED (probe 15.10) | 403 с нашего IP — через UA-прокси | image captcha | mail.meta.ua:993 | средний | geo |
| D | Tutanota/Tuta | tutanota.com/de, tuta.io | ACCEPTED (probe 15.10) | POW-captcha, веб-only | POW | НЕТ | низкий | нет IMAP |
| D | Mailfence | mailfence.com | ACCEPTED (probe 15.10) | форма | captcha | IMAP платный | низкий | — |
| D | Yandex | yandex.ru/com | ACCEPTED (probe 15.10) | smart-captcha + phone | SmartCaptcha | imap.yandex.ru:993 | низкий | phone-гейт |
| D | Rambler | rambler.ru | ACCEPTED (probe 15.10) | — | captcha | imap.rambler.ru:993 | низкий | — |
| A | Outlook/Hotmail | outlook.com, hotmail.com, live.com | ACCEPTED (probe 15.10) | **daimon3332/OutlookRegister** (fork: aaronjaworski710/OutlookRegister, MIT, 1263★, жив 07.2026) — patchright + авто-press&hold Arkose, БЕЗ платных солверов; выход email----pass----client_id----refresh_token (Thunderbird client_id 9e5f94bc-..., ROPC-блок не влияет — authcode flow) | press&hold автоматом; FunCaptcha = плохой IP → смена IP | Graph API / OAuth RT | высокий | нужен резидентный/мобильный прокси (DC-IP → MS risk block) |
| F | Gmail | gmail.com | ACCEPTED $0 | — | — | — | 0 | promo_inbox дедуп |
| F | t-online.de | t-online.de | ACCEPTED $0 (наблюдение) | — | — | imap | ? | требует перепроверки: по коду домен не должен резаться |
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
