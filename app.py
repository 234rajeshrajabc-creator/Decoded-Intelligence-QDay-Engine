from flask import Flask, render_template, request, jsonify
import requests, math, re, time

app = Flask(__name__)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"

HEADERS = {
    "User-Agent": "Decoded Intelligence Institutional Risk Research economics2decoded@gmail.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
}

def sec_get(url):
    h = dict(HEADERS)

    if "www.sec.gov/files" in url:
        h["Host"] = "www.sec.gov"

    r = requests.get(url, headers=h, timeout=20)
    r.raise_for_status()
    return r.json()


def identify(q):
    data = sec_get(SEC_TICKERS)
    items = list(data.values())
    ql = q.strip().lower()

    exact_t = next(
        (x for x in items if str(x.get("ticker", "")).lower() == ql),
        None
    )

    if exact_t:
        return exact_t

    exact_n = next(
        (x for x in items if str(x.get("title", "")).lower() == ql),
        None
    )

    if exact_n:
        return exact_n

    matches = [
        x for x in items
        if ql in str(x.get("title", "")).lower()
    ]

    if matches:
        return sorted(
            matches,
            key=lambda x: len(x.get("title", ""))
        )[0]

    return None


def latest_fact(facts, tags):
    for tag in tags:
        obj = facts.get(tag)

        if not obj:
            continue

        units = obj.get("units", {})
        candidates = []

        for unit, arr in units.items():
            for x in arr:
                if x.get("val") is not None:
                    candidates.append(x)

        candidates.sort(
            key=lambda x: (
                x.get("filed", ""),
                x.get("end", "")
            ),
            reverse=True
        )

        if candidates:
            x = candidates[0]

            v = x["val"]

            return {
                "value": v / 1_000_000,
                "unit": unit,
                "filed": x.get("filed"),
                "fy": x.get("fy"),
                "form": x.get("form")
            }

    return None


def get_company(q):
    ident = identify(q)

    if not ident:
        return {
            "ok": False,
            "error": "No reliable SEC public-company match found. Nothing has been invented."
        }

    cik = str(ident["cik_str"]).zfill(10)

    facts = sec_get(
        SEC_FACTS.format(cik)
    )

    us = facts.get("facts", {}).get("us-gaap", {})

    assets = latest_fact(
        us,
        ["Assets"]
    )

    revenue = latest_fact(
        us,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet"
        ]
    )

    income = latest_fact(
        us,
        [
            "ProfitLoss",
            "NetIncomeLoss"
        ]
    )

    equity = latest_fact(
        us,
        [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        ]
    )

    shares = latest_fact(
        us,
        [
            "EntityCommonStockSharesOutstanding"
        ]
    )

    return {
        "ok": True,

        "identity": {
            "name": ident.get("title"),
            "ticker": ident.get("ticker"),
            "cik": cik
        },

        "financial": {
            "assets": assets,
            "revenue": revenue,
            "net_income": income,
            "equity": equity,
            "shares": shares
        },

        "sources": [
            {
                "name": "SEC Company Tickers",
                "url": "https://www.sec.gov/files/company_tickers.json"
            },
            {
                "name": "SEC Company Facts",
                "url": SEC_FACTS.format(cik)
            }
        ]
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/company")
def company():

    q = request.json.get(
        "query",
        ""
    ).strip()

    if not q:
        return jsonify({
            "ok": False,
            "error": "Enter an institution name or ticker."
        }), 400

    try:

        return jsonify(
            get_company(q)
        )

    except requests.RequestException as e:

        return jsonify({
            "ok": False,
            "error": "Live public-data retrieval failed. No fallback values were generated.",
            "detail": str(e)
        }), 502

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": "Assessment could not be built. No fallback values were generated.",
            "detail": str(e)
        }), 500


@app.post("/api/calculate")
def calculate():

    d = request.json or {}

    required = [
        "legacy",
        "migration",
        "threat",
        "event_probability",
        "loss_rate"
    ]

    missing = [
        k for k in required
        if d.get(k) in (None, "")
    ]

    if missing:

        return jsonify({
            "ok": False,
            "error": "Required inputs missing",
            "missing": missing
        }), 400

    legacy = float(d["legacy"])
    mig = float(d["migration"])
    threat = float(d["threat"])
    event = float(d["event_probability"])
    lossrate = float(d["loss_rate"])

    if (
        legacy < 0
        or not 0 <= mig <= 100
        or not 0 <= threat <= 1
        or not 0 <= event <= 100
        or not 0 <= lossrate <= 100
    ):

        return jsonify({
            "ok": False,
            "error": "Input range invalid."
        }), 400

    capex = d.get("capex")
    emergency = d.get("emergency")
    shares = d.get("shares")
    tax = d.get("tax")

    capex = (
        float(capex)
        if capex not in (None, "")
        else None
    )

    emergency = (
        float(emergency)
        if emergency not in (None, "")
        else None
    )

    shares = (
        float(shares)
        if shares not in (None, "")
        else None
    )

    tax = (
        float(tax)
        if tax not in (None, "")
        else None
    )

    rows = []

    for year in range(2026, 2031):

        n = year - 2026

        residual = legacy * (
            (1 - mig / 100) ** n
        )

        expected_loss = (
            residual
            * threat
            * (event / 100)
            * (lossrate / 100)
        )

        risk = min(
            100,
            100
            * (residual / legacy)
            * threat
            * (1 + (event / 100) * n)
        )

        emergency_capex = (
            capex * emergency
            if capex is not None
            and emergency is not None
            else None
        )

        eps = (
            -(expected_loss * (1 - tax / 100)) / shares
            if shares
            and tax is not None
            else None
        )

        rows.append({
            "year": year,
            "residual": residual,
            "expected_loss": expected_loss,
            "risk": risk,
            "emergency_capex": emergency_capex,
            "eps": eps
        })

    return jsonify({
        "ok": True,
        "rows": rows
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=False
    )
