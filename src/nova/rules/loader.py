from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nova.schemas.rules import CustomerRuleSet

RULES_ROOT = Path(__file__).resolve().parent / "customers"


class RuleLoadError(Exception):
    pass


def load_rules(customer_id: str, *, rules_root: Path = RULES_ROOT) -> CustomerRuleSet:
    if not customer_id.strip():
        raise RuleLoadError("customer_id must be non-empty")

    rule_path = rules_root / f"{customer_id}.yaml"
    try:
        raw = rule_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleLoadError(f"Could not read rule file for customer {customer_id}: {exc}") from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"Invalid YAML in {rule_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuleLoadError(f"Rule file {rule_path} must contain a YAML mapping")

    try:
        rules = CustomerRuleSet.model_validate(data)
    except ValidationError as exc:
        raise RuleLoadError(f"Invalid rule schema in {rule_path}: {exc}") from exc

    if rules.customer_id != customer_id:
        raise RuleLoadError(
            f"Rule file customer_id {rules.customer_id!r} does not match requested {customer_id!r}"
        )

    return rules
