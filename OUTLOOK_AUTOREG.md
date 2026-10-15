# OUTLOOK AUTOREG — лучшие «обычные» почты для treg $1

Outlook/Hotmail/Live = ACCEPTED на treg.to (probe 15.10) и это ЕДИНСТВЕННАЯ
«обычная почта» с живым масс-авторегом без платных капчи-сервисов.

## Тул: aaronjaworski710/OutlookRegister (fork daimon3332/OutlookRegister, MIT, 1263★)

- patchright (stealth Chromium), Windows/Linux
- captcha_strategy=0: full-auto press&hold Arkose (human-like Bezier mouse, double-tap, hold) — БЕЗ 2captcha/CapSolver
- FunCaptcha вместо press&hold = IP плохой → ротация прокси (residential/mobile обязателен; DC-IP → «IP已被微软风控拦截»)
- multi-stage login challenges, recovery-email binding (опц., temp-mail API), batching, interrupt-safe
- Выход: `Results/oauth2.txt` = `email----password----client_id----refresh_token`
- client_id `9e5f94bc-e8a4-4e73-b8be-63364c29d753` (Thunderbird) — authcode flow, ROPC-блок Microsoft (AADSTS70003) НЕ влияет
- OAuth2 scopes: `offline_access` + `https://graph.microsoft.com/.default` → полный Graph (чтение почты через refresh_token)

### Запуск

```bash
git clone https://github.com/aaronjaworski710/OutlookRegister && cd OutlookRegister
pip install -r requirements.txt && patchright install chromium
cp config.example.json config.json   # настроить proxy (residential!), tasks, concurrent_flows
python main.py
```

config.json минимум: `proxy.mode=single/multiple` + host/port.
`captcha_strategy`: 0=авто-пресс (дефолт), 1=ручной пресс, 2=авто до капчи + ручной финал.

## Интеграция с treg: `treg_outlook.py`

Читает `oauth2.txt` → на каждый аккаунт:
1. `/auth/email/start` (TokenFactory PASS-токен из `treg_farm_v3.py`)
2. OTP через **Graph API** (`/me/messages`, без IMAP — у MS consumer IMAP выключен по умолчанию)
3. `/auth/email/verify` → org create → balance → `out/outlook_bank.jsonl`

```bash
TREG_LIB=/path/to/treg_farm_v3_dir python treg_outlook.py Results/oauth2.txt --pace 25
```

Резюме: outlook-ящик = уникальный promo_inbox → грант $1 (как proton, но без abuse-detection).

## Компаньоны (форкнуты у aaronjaworski710)

- MailRu-autoreg (Draugrol) — mail.ru door референс (2024, может требовать апдейт под текущую captcha)
- daimon3332/easy-proxies — локальный прокси-пул для multi-port mode
- daimon3332/OutlookManage — менеджер аккаунтов/токенов

## Статус (15.10)

- [x] forks созданы (202 accepted)
- [x] treg_outlook.py адаптер написан (compile OK)
- [ ] live-тест: нужен residential proxy (free-пул → FunCaptcha/блок)
- Кандидаты прокси: ZTE mobile farm (мёртв сейчас), bpproxy (мёртв), 1024proxy DE (баланс $0) → покупка резиков НЕ подтверждена Владом
