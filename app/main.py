import datetime as dt
import logging
from itertools import groupby
from pathlib import Path

from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import chart, credit, csvio
from .auth import Auth, RequireLogin, SecurityHeaders
from .config import Settings
from .db import Database, MonthTotals, totals_for
from .money import format_gbp, parse_pence, plain
from .structure import BILL, INCOME, KIND_LABELS, KINDS, MONTH_NAMES, PLANNED

HERE = Path(__file__).parent
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
log = logging.getLogger("finance")


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    if settings.auth_disabled:
        log.warning("AUTH_DISABLED is set: anyone who can reach this app can use it.")
    db = Database(settings.data_dir / "finance.db")
    auth = Auth(settings)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["gbp"] = format_gbp
    templates.env.filters["plain"] = plain
    templates.env.filters["rate"] = credit.format_rate
    templates.env.filters["duration"] = credit.describe_months
    templates.env.globals.update(MONTH_NAMES=MONTH_NAMES, KIND_LABELS=KIND_LABELS,
                                 DEBT_KINDS=credit.DEBT_KINDS, STRATEGIES=credit.STRATEGIES)

    def render(request: Request, name: str, **context) -> Response:
        context.update(user=request.session.get("user"), csrf=request.session.get("csrf"))
        return templates.TemplateResponse(request, name, context)

    async def checked_form(request: Request) -> FormData:
        form = await request.form(max_part_size=MAX_UPLOAD_BYTES)
        token = request.session.get("csrf")
        if not token or form.get("csrf") != token:
            raise HTTPException(403, "Form expired. Reload the page and try again.")
        return form

    def ym(request: Request) -> tuple[int, int]:
        year, month = request.path_params["year"], request.path_params["month"]
        if not (1 <= month <= 12 and 2000 <= year <= 2100):
            raise HTTPException(404)
        return year, month

    def month_url(year: int, month: int, anchor: str = "") -> str:
        return f"/month/{year}/{month}" + (f"#{anchor}" if anchor else "")

    # --- pages ------------------------------------------------------------

    async def home(request: Request):
        return RedirectResponse(f"/year/{dt.date.today().year}", status_code=303)

    async def year_view(request: Request):
        year = request.path_params["year"]
        totals = db.year_totals(year)
        overall = MonthTotals()
        for t in totals.values():
            overall.income += t.income
            overall.bills += t.bills
            overall.planned += t.planned
        years = sorted(set(db.years()) | {year, dt.date.today().year})
        return render(request, "year.html", year=year, totals=totals, overall=overall,
                      chart=chart.build(totals), years=years)

    async def month_view(request: Request):
        year, month = ym(request)
        prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
        next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
        nav = {"prev": month_url(prev_y, prev_m), "next": month_url(next_y, next_m)}
        if not db.month_exists(year, month):
            return render(request, "month_new.html", year=year, month=month, nav=nav,
                          source=db.previous_month(year, month))
        items = db.items(year, month)
        sections = {}
        for kind in KINDS:
            of_kind = [i for i in items if i.kind == kind]
            if kind == BILL:
                # Keep categories in first-seen order, like the sheet.
                order = list(dict.fromkeys(i.category for i in of_kind))
                of_kind.sort(key=lambda i: order.index(i.category))
                sections[kind] = [(cat, list(group)) for cat, group in
                                  groupby(of_kind, key=lambda i: i.category)]
            else:
                sections[kind] = of_kind
        bill_categories = [cat for cat, _ in sections[BILL]]
        return render(request, "month.html", year=year, month=month, nav=nav,
                      sections=sections, totals=totals_for(items),
                      bill_categories=bill_categories)

    # --- month actions ----------------------------------------------------

    async def start_month(request: Request):
        year, month = ym(request)
        form = await checked_form(request)
        if not db.month_exists(year, month):
            source = db.previous_month(year, month)
            if form.get("mode") == "copy" and source:
                db.copy_month(source, (year, month))
            else:
                db.create_blank_month(year, month)
        return RedirectResponse(month_url(year, month), status_code=303)

    async def delete_month(request: Request):
        year, month = ym(request)
        await checked_form(request)
        db.delete_month(year, month)
        return RedirectResponse(f"/year/{year}", status_code=303)

    async def add_item(request: Request):
        year, month = ym(request)
        form = await checked_form(request)
        kind = form.get("kind", "")
        if kind not in KINDS:
            raise HTTPException(400, "Unknown section")
        name = (form.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Give the line a name")
        category = (form.get("category") or "").strip()
        if kind == BILL:
            category = category or "Other bills"
        else:
            category = KIND_LABELS[kind] if kind == PLANNED else "Income"
        try:
            pence = parse_pence(form.get("amount"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        db.add_item(year, month, kind, category, name[:120], pence)
        return RedirectResponse(month_url(year, month, kind), status_code=303)

    async def update_item(request: Request):
        item = db.get_item(request.path_params["item_id"]) or _404()
        form = await checked_form(request)
        url = month_url(item.year, item.month, f"item-{item.id}")
        if form.get("action") == "delete":
            db.delete_item(item.id)
            return RedirectResponse(month_url(item.year, item.month, item.kind), status_code=303)
        try:
            pence = parse_pence(form.get("amount"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        name = (form.get("name") or "").strip() or item.name
        db.update_item(item.id, name[:120], pence)
        return RedirectResponse(url, status_code=303)

    # --- credit tracker ---------------------------------------------------

    def debt_values(form: FormData) -> dict:
        """Validate the debt form; raises ValueError with a readable message."""
        name = (form.get("name") or "").strip()[:120]
        if not name:
            raise ValueError("Give it a name.")
        kind = form.get("kind") if form.get("kind") in credit.DEBT_KINDS else "other"
        promo_text = (form.get("promo_until") or "").strip()
        try:
            promo_until = dt.date.fromisoformat(promo_text) if promo_text else None
        except ValueError:
            raise ValueError("Promo end date isn't a valid date.")
        promo_rate = (form.get("promo_apr") or "").strip()
        values = {
            "name": name,
            "kind": kind,
            "balance_pence": parse_pence(form.get("balance")),
            "apr_bp": credit.parse_rate_bp(form.get("apr")),
            "payment_pence": parse_pence(form.get("payment")),
            "promo_apr_bp": credit.parse_rate_bp(promo_rate) if promo_until else None,
            "promo_until": promo_until,
            "notes": (form.get("notes") or "").strip()[:500],
        }
        if promo_until and not promo_rate:
            values["promo_apr_bp"] = 0
        if values["balance_pence"] < 0 or values["payment_pence"] < 0:
            raise ValueError("Balance and payment can't be negative.")
        return values

    def credit_start() -> dt.date:
        return credit.add_months(dt.date.today().replace(day=1), 1)

    async def credit_view(request: Request, error: str | None = None, draft=None):
        debts = db.debts()
        start = credit_start()
        try:
            extra = max(0, parse_pence(request.query_params.get("extra")))
        except ValueError:
            extra = 0
        strategy = request.query_params.get("strategy")
        if strategy not in credit.STRATEGIES:
            strategy = "avalanche"
        current = credit.project(debts, start)
        plan = credit.project(debts, start, extra_pence=extra, strategy=strategy)
        stuck = {r.debt.id: credit.payment_to_clear(r.debt, credit.MAX_MONTHS, start)
                 for r in current.results if not r.cleared}
        total = sum(d.balance_pence for d in debts)
        balance_chart = None
        if total > 0:
            balance_chart = chart.build_balances(
                total,
                [("Current", "series-1", current.balances), ("Plan", "series-2", plan.balances)],
                start, credit.add_months)
        payoff_order = sorted((r for r in plan.results if r.cleared and r.months),
                              key=lambda r: r.months)
        return render(request, "credit.html", debts=debts, current=current, plan=plan,
                      extra=extra, strategy=strategy, stuck=stuck, total=total,
                      payments=sum(d.payment_pence for d in debts), chart=balance_chart,
                      payoff_order=payoff_order, error=error, draft=draft or {})

    async def add_debt(request: Request):
        form = await checked_form(request)
        try:
            db.save_debt(debt_values(form))
        except ValueError as exc:
            response = await credit_view(request, error=str(exc), draft=dict(form))
            response.status_code = 400
            return response
        return RedirectResponse("/credit", status_code=303)

    async def debt_view(request: Request):
        debt = db.get_debt(request.path_params["debt_id"]) or _404()
        return render(request, "debt.html", debt=debt, error=None)

    async def update_debt(request: Request):
        debt = db.get_debt(request.path_params["debt_id"]) or _404()
        form = await checked_form(request)
        if form.get("action") == "delete":
            db.delete_debt(debt.id)
            return RedirectResponse("/credit", status_code=303)
        try:
            db.save_debt(debt_values(form), debt.id)
        except ValueError as exc:
            response = render(request, "debt.html", debt=debt, error=str(exc))
            response.status_code = 400
            return response
        return RedirectResponse("/credit", status_code=303)

    # --- import / export --------------------------------------------------

    async def data_page(request: Request):
        return render(request, "data.html", message=None, error=None)

    async def export(request: Request):
        body = csvio.export_csv(db.all_items())
        filename = f"finance-export-{dt.date.today().isoformat()}.csv"
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    async def export_debts(request: Request):
        body = csvio.export_debts_csv(db.debts())
        filename = f"finance-debts-{dt.date.today().isoformat()}.csv"
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    async def import_csv(request: Request):
        form = await checked_form(request)
        upload = form.get("file")
        if not upload or not getattr(upload, "filename", ""):
            return render(request, "data.html", message=None, error="Choose a CSV file first.")
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return render(request, "data.html", message=None, error="That file is over 2 MB.")
        try:
            rows = csvio.parse_csv(raw.decode("utf-8"))
        except (UnicodeDecodeError, csvio.ImportError_) as exc:
            return render(request, "data.html", message=None, error=f"Nothing imported. {exc}")
        count = db.replace_months(rows)
        return render(request, "data.html", error=None,
                      message=f"Imported {len(rows)} lines across {count} month(s).")

    async def healthz(request: Request):
        return PlainTextResponse("ok")

    async def http_error(request: Request, exc: HTTPException):
        response = render(request, "error.html", status=exc.status_code, detail=exc.detail)
        response.status_code = exc.status_code
        return response

    routes = [
        Route("/", home),
        Route("/healthz", healthz),
        Route("/login", auth.login),
        Route("/auth/callback", auth.callback),
        Route("/logout", auth.logout, methods=["POST"]),
        Route("/year/{year:int}", year_view),
        Route("/month/{year:int}/{month:int}", month_view),
        Route("/month/{year:int}/{month:int}/start", start_month, methods=["POST"]),
        Route("/month/{year:int}/{month:int}/delete", delete_month, methods=["POST"]),
        Route("/month/{year:int}/{month:int}/items", add_item, methods=["POST"]),
        Route("/items/{item_id:int}", update_item, methods=["POST"]),
        Route("/credit", credit_view),
        Route("/credit", add_debt, methods=["POST"]),
        Route("/credit/{debt_id:int}", debt_view),
        Route("/credit/{debt_id:int}", update_debt, methods=["POST"]),
        Route("/data", data_page),
        Route("/data/export.csv", export),
        Route("/data/debts.csv", export_debts),
        Route("/data/import", import_csv, methods=["POST"]),
        Mount("/static", StaticFiles(directory=HERE / "static"), name="static"),
    ]
    middleware = [
        Middleware(SecurityHeaders, form_target=settings.oidc_issuer),
        Middleware(SessionMiddleware, secret_key=settings.session_secret,
                   session_cookie="finance_session", max_age=7 * 24 * 3600,
                   same_site="lax", https_only=settings.secure_cookies),
        Middleware(RequireLogin, settings=settings),
    ]
    app = Starlette(routes=routes, middleware=middleware,
                    exception_handlers={HTTPException: http_error})
    app.state.db = db

    return app


def _404():
    raise HTTPException(404)
