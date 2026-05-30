from dataclasses import dataclass, field

from nova.settings import MODEL_COSTS


@dataclass
class CostMeter:
    run_id: str
    stage_costs: dict[str, float] = field(default_factory=dict)

    def add_stage_cost(self, stage: str, cost_usd: float) -> None:
        self.stage_costs[stage] = self.stage_costs.get(stage, 0.0) + cost_usd

    @property
    def total_usd(self) -> float:
        return round(sum(self.stage_costs.values()), 6)


def calculate_model_cost_usd(*, model: str, input_tokens: int, output_tokens: int) -> float:
    costs = MODEL_COSTS.get(model)
    if costs is None:
        return 0.0

    input_cost = (input_tokens / 1_000_000) * costs["input_per_1m"]
    output_cost = (output_tokens / 1_000_000) * costs["output_per_1m"]
    return round(input_cost + output_cost, 6)
