# app/navigation.py
MENU = [
    {
        "label": "Main",
        "section": True,  # non-clickable section header
    },
    {
        "label": "Dashboard",
        "icon": "bi bi-speedometer2",
        "endpoint": "main.index",
    },
{
    "label": "Payments",
    "icon": "bi bi-cash-coin",
    "endpoint": "#",   # parent itself doesn’t point anywhere
    "children": [
        {
            "label": "Payments",
            "icon": "bi bi-arrow-left-right",
            "endpoint": "main.payments_page",
        },
        {
            "label": "Transfers",
            "icon": "bi bi-wallet2",
            "endpoint": "main.transfers_page",
        },
        {
            "label": "Recurring",
            "icon": "bi bi-repeat",
            "endpoint": "main.recurring_page",
        },
    ],
},
{
    "label": "Credit Cards",
    "icon": "bi bi-credit-card-2-front",
    "endpoint": "main.credit_cards_page",
},

    {
        "label": "Transactions",
        "icon": "bi bi-journal-text",
        "endpoint": "main.transactions_page",
    },
    {
    "label": "Budget",
    "icon": "bi bi-pie-chart",
    "endpoint": "main.budget_page",
},
    {
        "label": "Debt Tracker",
        "icon": "bi bi-cash-coin",
        "endpoint": "main.debts_page",
    },
{
    "label": "Salary",
    "icon": "bi bi-currency-exchange",
    "endpoint": "#",  # parent itself doesn’t point anywhere
    "children": [
        {
            "label": "Salary Tracker",
            "icon": "bi bi-calendar2-check",
            "endpoint": "main.salary_page",
        },
        {
            "label": "Holidays",
            "icon": "bi bi-calendar-event",
            "endpoint": "main.holidays_page",
        },
        {
            "label": "Settings",
            "icon": "bi bi-sliders",
            "endpoint": "main.salary_settings_page",  # or modal later; page is easiest first
        },
    ],
},

]
