# Capacity Planner

A Streamlit dashboard showing live project/task/case/user counts pulled from
Zoho Projects and Zoho CRM, with a foundation for team capacity/utilization
calculations.

## Project layout

```
capacity_planner/
├── main.py                        # Streamlit entrypoint
├── pages/
│   └── dashboard.py                # KPI dashboard page
├── app/
│   ├── config/settings.py          # env-driven configuration
│   ├── api/
│   │   ├── zoho_auth.py            # OAuth token refresh
│   │   └── zoho_projects.py        # Zoho Projects + CRM API client
│   ├── database/
│   │   ├── database.py             # SQLAlchemy engine/session
│   │   └── models.py               # KpiSnapshot, Employee
│   ├── services/
│   │   ├── dashboard_service.py    # fetches + shapes KPIs for the page
│   │   └── capacity_engine.py      # utilization math (WIP)
│   └── utils/logger.py
├── requirements.txt
└── .env.example
```

## Setup

1. Create a virtual environment and install dependencies:
   ```
   python -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your Zoho credentials:
   ```
   cp .env.example .env
   ```
   You'll need a Zoho API console app (https://api-console.zoho.com) with a
   refresh token scoped for Zoho Projects and Zoho CRM reads. See comments
   in `.env.example` for the exact scopes.

3. Run the app:
   ```
   streamlit run main.py
   ```

## Notes

- If Zoho is unreachable or `.env` isn't configured, the dashboard falls
  back to the last cached KPI snapshot (stored in a local SQLite DB) rather
  than crashing.
- `capacity_engine.py` has the first utilization calculation
  (assigned hours vs. available hours per person) — it isn't wired into the
  dashboard page yet since it needs task-hour and assignee data decisions
  specific to your Zoho setup.

## Next steps

- Decide how "capacity" is defined for your team (hours/week per person,
  by role, etc.) and populate the `Employee` table
- Wire `CapacityEngine.calculate_utilization` into a new dashboard page
- Add a scheduled job (the `schedule` package is already in
  requirements.txt) to refresh the KPI snapshot on a timer instead of only
  on page load
