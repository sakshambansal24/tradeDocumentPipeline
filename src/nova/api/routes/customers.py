from pathlib import Path

import yaml
from fastapi import APIRouter

from nova.rules.loader import RULES_ROOT
from nova.schemas.api import CustomerSummary

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerSummary])
def list_customers() -> list[CustomerSummary]:
    customers: list[CustomerSummary] = []
    for path in sorted(Path(RULES_ROOT).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        customer_id = data.get("customer_id", path.stem)
        customers.append(
            CustomerSummary(
                customer_id=customer_id,
                name=customer_id.replace("_", " ").title(),
                rule_set_path=str(path),
                version=str(data.get("version", "unknown")),
            )
        )
    return customers
