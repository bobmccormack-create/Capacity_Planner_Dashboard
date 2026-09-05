"""
Capacity calculations.

This is intentionally separate from DashboardService: dashboard_service.py
is about *fetching and summarizing* KPI counts, while this module is where
actual capacity/utilization math lives - team load vs. available hours.

Started with the fundamentals; extend as your data model (assignees, task
estimates, working hours) firms up.
"""
from dataclasses import dataclass
from typing import List


@dataclass
class UtilizationResult:
    person: str
    assigned_hours: float
    capacity_hours: float

    @property
    def utilization_pct(self) -> float:
        if self.capacity_hours <= 0:
            return 0.0
        return round((self.assigned_hours / self.capacity_hours) * 100, 1)

    @property
    def is_overallocated(self) -> bool:
        return self.assigned_hours > self.capacity_hours


class CapacityEngine:
    @staticmethod
    def calculate_utilization(
        tasks: List[dict],
        capacity_by_person: dict,
        hours_field: str = "estimated_hours",
        assignee_field: str = "assignee_name",
    ) -> List[UtilizationResult]:
        """
        tasks: list of task dicts (e.g. from ZohoClient.get_tasks())
        capacity_by_person: {"Jane Doe": 40, "John Smith": 35, ...}

        Sums assigned hours per person and compares against their
        available capacity for the period.
        """
        assigned_hours: dict = {person: 0.0 for person in capacity_by_person}

        for task in tasks:
            person = task.get(assignee_field)
            if not person:
                continue
            hours = float(task.get(hours_field) or 0)
            assigned_hours[person] = assigned_hours.get(person, 0.0) + hours

        results = []
        for person, capacity in capacity_by_person.items():
            results.append(
                UtilizationResult(
                    person=person,
                    assigned_hours=assigned_hours.get(person, 0.0),
                    capacity_hours=capacity,
                )
            )
        return sorted(results, key=lambda r: r.utilization_pct, reverse=True)

    @staticmethod
    def team_summary(results: List[UtilizationResult]) -> dict:
        if not results:
            return {"avg_utilization_pct": 0.0, "overallocated_count": 0}

        avg = sum(r.utilization_pct for r in results) / len(results)
        overallocated = sum(1 for r in results if r.is_overallocated)
        return {
            "avg_utilization_pct": round(avg, 1),
            "overallocated_count": overallocated,
        }
