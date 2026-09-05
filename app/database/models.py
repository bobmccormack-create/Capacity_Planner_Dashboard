"""
Local cache models.

We don't mirror Zoho's full schema - just enough to (a) cache the last
known-good KPI counts so the dashboard has something to show if Zoho is
briefly unavailable, and (b) keep a history of snapshots for trend charts
later.
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from app.database.database import Base


class KpiSnapshot(Base):
    """One row per KPI refresh - lets us show trends and survive outages."""
    __tablename__ = "kpi_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    projects = Column(Integer, default=0)
    tasks = Column(Integer, default=0)
    cases = Column(Integer, default=0)
    users = Column(Integer, default=0)

    source = Column(String, default="zoho")  # 'zoho' or 'cache'/'fallback'


class Employee(Base):
    """
    Placeholder for future capacity-engine work: who's on the team and
    their available hours, used to compute utilization against task load.
    """
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    zoho_user_id = Column(String, nullable=True)
    weekly_capacity_hours = Column(Integer, default=40)
