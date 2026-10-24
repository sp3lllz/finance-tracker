"""The blank month template, mirroring the layout of the original spreadsheet.

Each month in the sheet had income lines, an overdraft line, recurring bills
grouped by category, and a planned-spending column. Names here are generic
placeholders; edit them freely in the app.
"""

INCOME = "income"
BILL = "bill"
PLANNED = "planned"
KINDS = (INCOME, BILL, PLANNED)

KIND_LABELS = {
    INCOME: "Income",
    BILL: "Recurring outgoings",
    PLANNED: "Planned spending",
}

# (kind, category, name) — amounts start at £0.
BLANK_MONTH: list[tuple[str, str, str]] = [
    (INCOME, "Income", "Salary after tax"),
    (BILL, "Overdraft", "Overdraft repayment"),
    (BILL, "Utilities", "Electricity & gas"),
    (BILL, "Utilities", "Water"),
    (BILL, "Utilities", "Phone"),
    (BILL, "Utilities", "Internet"),
    (BILL, "Housing", "Mortgage / rent"),
    (BILL, "Housing", "Contents insurance"),
    (BILL, "Housing", "Council tax"),
    (BILL, "Housing", "Home maintenance"),
    (BILL, "Finance & credit cards", "Loan"),
    (BILL, "Finance & credit cards", "Credit card"),
    (BILL, "Other bills", "Subscriptions"),
    (BILL, "Other bills", "Insurance"),
]

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
