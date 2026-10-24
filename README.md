# Finance Tracker

A small self-hosted web app for a monthly household budget, plus a credit and loan
payoff planner. It runs as one Docker container with a SQLite database, and you sign in
with [Pocket ID](https://pocket-id.org) (or any other OpenID Connect provider).

It started as a spreadsheet with one tab per month, so each month follows the same layout:

- **Income**: salary and any extra money that month
- **Recurring outgoings**: bills grouped by category (overdraft, utilities, housing,
  finance & credit cards, other bills; add your own)
- **Planned spending**: one-off purchases for that month
- Totals for **funds after bills** and **funds after spending**

## Features

- **Year view** with income vs outgoings by month, plus a table you can click into
- **Month pages** where you edit, add and remove lines, with totals updating as you go
- **Copy last month** to start a new month with the previous month's income and bills
  (planned spending starts empty), or start from the blank template
- **Credit tracker** for cards, loans, mortgages, car finance and BNPL with balance,
  APR, monthly payment and an optional 0% promo period. For each one it estimates
  the payoff date and the interest you'll pay
- **Debt-free plan**: add an extra monthly amount and choose avalanche (highest APR
  first) or snowball (smallest balance first). It shows the new debt-free date,
  interest saved and the order debts get cleared
- **CSV import/export** for backups and for bringing in data from a spreadsheet
- Works on a phone, with light and dark mode

## Running it

### 1. Create a client in Pocket ID

In Pocket ID, go to **Administration → OIDC Clients → Add OIDC Client**:

| Setting | Value |
|---|---|
| Name | Finance Tracker |
| Callback URLs | `https://finance.example.com/auth/callback` |
| Logout callback URLs | `https://finance.example.com/` |
| Public client | Optional. If you turn it on, leave `OIDC_CLIENT_SECRET` empty (PKCE is always used) |

Copy the client ID (and secret, if any). To limit who can sign in, use the client's
**Allowed user groups** in Pocket ID, or set `OIDC_ALLOWED_GROUPS` / `OIDC_ALLOWED_EMAILS`.

### 2. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as SESSION_SECRET
```

Fill in `APP_URL`, `OIDC_ISSUER` (your Pocket ID URL, e.g. `https://id.example.com`),
`OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.

### 3. Start

```bash
docker compose up -d --build
```

The app listens on port 8000. Put it behind your reverse proxy (Caddy, Traefik, nginx…)
with HTTPS at the address in `APP_URL`. Session cookies are marked `Secure` when `APP_URL`
starts with `https://`.

Data is stored in the `finance-data` Docker volume (`/data/finance.db`). To back up,
use **Import / export** in the app or copy the database file:

```bash
docker compose cp finance:/data/finance.db ./finance-backup.db
```

## Importing from a spreadsheet

Make a CSV with these columns and upload it on the **Import / export** page:

```csv
year,month,kind,category,name,amount
2026,1,income,Income,Salary after tax,2000.00
2026,1,bill,Housing,Mortgage / rent,800.00
2026,1,bill,Utilities,Water,40.00
2026,1,planned,Planned spending,New tyres,180.00
```

`kind` is `income`, `bill` or `planned`. Each month in the file replaces that month
in the app. If any line is invalid, nothing is imported.

## About the payoff estimates

Interest is added once a month at the APR, treated as an effective annual rate
(monthly rate = (1 + APR)^(1/12) − 1), and then the payment is taken. Real lenders
charge interest daily and post it on their own statement dates, so expect small
differences. Update balances from your statements now and then to keep the
projections accurate.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
AUTH_DISABLED=true DATA_DIR=./data-dev SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))") \
  .venv/bin/uvicorn app.main:create_app --factory --reload --port 8077
```

`AUTH_DISABLED=true` skips sign-in, so only use it on your own machine.

Stack: Python 3.12, Starlette, Jinja2, Authlib, SQLite. No JavaScript build step. The
chart and tooltip scripts are small and served from `app/static`.
