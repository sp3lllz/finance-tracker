import datetime as dt
import math
import re

import pytest
from starlette.testclient import TestClient

from app import credit
from app.credit import Debt, project
from app.main import create_app
from tests.test_app import csrf_of, settings

START = dt.date(2027, 1, 1)


def debt(id=1, balance=1_000_000, apr="12", payment=20_000, **kw):
    return Debt(id=id, name=f"Debt {id}", kind="loan", balance_pence=balance,
                apr_bp=credit.parse_rate_bp(apr), payment_pence=payment, **kw)


def test_zero_interest_is_simple_division():
    r = project([debt(balance=100_000, apr="0", payment=10_000)], START).results[0]
    assert (r.months, r.interest_pence, r.paid_pence) == (10, 0, 100_000)


def test_matches_annuity_formula():
    # £10,000 at 12% APR, £200/month: n = -ln(1 - rB/P) / ln(1 + r)
    rate = 1.12 ** (1 / 12) - 1
    expected = math.ceil(-math.log(1 - rate * 10_000 / 200) / math.log(1 + rate))
    r = project([debt()], START).results[0]
    assert r.months == expected == 69
    assert r.paid_pence - r.interest_pence == 1_000_000  # principal fully repaid
    assert 360_000 < r.interest_pence < 370_000


def test_payment_below_interest_never_clears():
    p = project([debt(balance=1_000_000, apr="30", payment=1_000)], START)
    assert p.months is None and not p.results[0].cleared
    needed = credit.payment_to_clear(debt(balance=1_000_000, apr="30", payment=1_000),
                                     credit.MAX_MONTHS, START)
    assert needed > 2_200  # 30% APR on £10k is ~£221/month interest
    assert project([debt(balance=1_000_000, apr="30", payment=needed)], START).results[0].cleared


def test_promo_rate_applies_until_end_date():
    promo = debt(balance=120_000, apr="25", payment=10_000,
                 promo_apr_bp=0, promo_until=dt.date(2028, 1, 1))
    assert project([promo], START).results[0].interest_pence == 0  # cleared in 12 promo months
    late = debt(balance=120_000, apr="25", payment=10_000,
                promo_apr_bp=0, promo_until=dt.date(2027, 7, 1))
    assert project([late], START).results[0].interest_pence > 0


def test_avalanche_and_snowball_beat_current_payments():
    debts = [debt(1, balance=300_000, apr="29.9", payment=9_000),
             debt(2, balance=80_000, apr="6", payment=5_000)]
    current = project(debts, START)
    avalanche = project(debts, START, extra_pence=5_000, strategy="avalanche")
    snowball = project(debts, START, extra_pence=5_000, strategy="snowball")
    assert avalanche.months < current.months
    assert avalanche.interest_pence < snowball.interest_pence < current.interest_pence
    # Snowball clears the small balance first; avalanche the high-rate one.
    assert snowball.results[1].months < snowball.results[0].months
    assert avalanche.results[0].months < current.results[0].months


def test_rollover_without_extra_still_helps():
    debts = [debt(1, balance=50_000, apr="20", payment=10_000),
             debt(2, balance=500_000, apr="20", payment=10_000)]
    assert project(debts, START, strategy="avalanche").months < project(debts, START).months


@pytest.mark.parametrize("text,bp", [("24.9", 2490), ("3.95%", 395), ("", 0), ("0", 0)])
def test_parse_rate(text, bp):
    assert credit.parse_rate_bp(text) == bp
    assert credit.format_rate(bp) == (text.rstrip("%") or "0")


def test_describe_months():
    assert credit.describe_months(14) == "1 yr 2 mo"
    assert credit.describe_months(None) == "over 50 years"


# --- pages -------------------------------------------------------------------

def test_credit_pages(tmp_path):
    client = TestClient(create_app(settings(tmp_path, auth_disabled=True)))
    page = client.get("/credit")
    assert "Add a debt" in page.text and "Total owed" not in page.text
    token = csrf_of(page.text)

    bad = client.post("/credit", data={"csrf": token, "name": "Card", "balance": "lots"})
    assert bad.status_code == 400 and "Not an amount" in bad.text
    assert 'value="Card"' in bad.text  # keeps what was typed

    client.post("/credit", data={"csrf": token, "name": "Card", "kind": "credit_card",
                                 "balance": "2,000", "apr": "24.9", "payment": "100"})
    client.post("/credit", data={"csrf": token, "name": "Car", "kind": "car_finance",
                                 "balance": "6000", "apr": "7.9", "payment": "250"})
    page = client.get("/credit?extra=50&strategy=snowball")
    assert "£8,000.00" in page.text and "Interest saved" in page.text
    assert "<path class=\"line series-2\"" in page.text

    debts_csv = client.get("/data/debts.csv").text
    assert "Card,credit_card,2000.00,24.9,100.00" in debts_csv

    debt_id = re.search(r'href="/credit/(\d+)">Card', page.text).group(1)
    edit = client.get(f"/credit/{debt_id}")
    assert 'value="24.9"' in edit.text
    client.post(f"/credit/{debt_id}", data={"csrf": token, "name": "Card", "kind": "credit_card",
                                            "balance": "1500", "apr": "24.9", "payment": "10"})
    assert "doesn't cover the interest" in client.get("/credit").text

    client.post(f"/credit/{debt_id}", data={"csrf": token, "action": "delete"})
    assert "Card" not in client.get("/credit").text
