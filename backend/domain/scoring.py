"""Pure arithmetic: no HTTP, persistence, Pydantic or criterion-specific rules."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Mapping, Protocol

ENGINE_VERSION = "decimal-v1.0"


@dataclass(frozen=True)
class CriterionDefinition:
    id: str
    kind: str
    weight: Decimal
    maximum: int
    allowed_values: tuple[int, ...]
    allow_na: bool
    section: str


class Strategy(Protocol):
    def normalized(self, criterion: CriterionDefinition, value: int) -> Decimal: ...
    def weighted(self) -> bool: ...


class BinaryStrategy:
    def normalized(self, criterion: CriterionDefinition, value: int) -> Decimal:
        if value not in (0, 1):
            raise ValueError("Binary values must be 0 or 1")
        return Decimal(value)

    def weighted(self) -> bool:
        return True


class ScaleStrategy:
    def normalized(self, criterion: CriterionDefinition, value: int) -> Decimal:
        return Decimal(value) / Decimal(criterion.maximum)

    def weighted(self) -> bool:
        return True


class FlagStrategy(BinaryStrategy):
    def weighted(self) -> bool:
        return False


@dataclass(frozen=True)
class Contribution:
    criterion_id: str
    effective_weight: Decimal | None
    contribution: Decimal | None


@dataclass(frozen=True)
class Calculation:
    total: Decimal | None
    earned: Decimal
    possible: Decimal
    contributions: tuple[Contribution, ...]
    sections: tuple[tuple[str, Decimal | None], ...]


class ScoringEngine:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {
            "binary": BinaryStrategy(), "scale": ScaleStrategy(), "flag": FlagStrategy(),
        }

    def register(self, kind: str, strategy: Strategy) -> None:
        if kind in self._strategies:
            raise ValueError("A strategy is already registered for this type")
        self._strategies[kind] = strategy

    def calculate(self, criteria: tuple[CriterionDefinition, ...], answers: Mapping[str, int | None]) -> Calculation:
        ordered = sorted(criteria, key=lambda criterion: criterion.id)
        if len({c.id for c in ordered}) != len(ordered) or set(answers) != {c.id for c in ordered}:
            raise ValueError("Expected exactly one answer per criterion")
        with localcontext() as context:
            # Sorting makes Decimal accumulation independent of UI/LLM ordering.
            context.prec = max([50, *[len(c.weight.as_tuple().digits) + 50 for c in ordered]])
            earned = Decimal(0)
            possible = Decimal(0)
            sections: dict[str, tuple[Decimal, Decimal]] = {}
            raw: dict[str, tuple[Decimal, Decimal] | None] = {}
            for criterion in ordered:
                value = answers[criterion.id]
                strategy = self._strategies[criterion.kind]
                if not criterion.weight.is_finite() or criterion.weight < 0 or criterion.maximum <= 0:
                    raise ValueError("Invalid weight or maximum")
                if value is None and not criterion.allow_na:
                    raise ValueError("N/A is forbidden")
                if value is not None and (type(value) is not int or value not in criterion.allowed_values):
                    raise ValueError("Value is not a configured step")
                sections.setdefault(criterion.section, (Decimal(0), Decimal(0)))
                if value is None:
                    raw[criterion.id] = None
                    continue
                if not strategy.weighted() or criterion.weight == 0:
                    raw[criterion.id] = (Decimal(0), Decimal(0))
                    continue
                points = strategy.normalized(criterion, value) * criterion.weight
                earned += points
                possible += criterion.weight
                section_points, section_weight = sections[criterion.section]
                sections[criterion.section] = (section_points + points, section_weight + criterion.weight)
                raw[criterion.id] = (criterion.weight, points)
            contributions = tuple(
                Contribution(c.id, None, None) if raw[c.id] is None else
                Contribution(c.id, Decimal(100) * raw[c.id][0] / possible if possible else Decimal(0),
                             Decimal(100) * raw[c.id][1] / possible if possible else Decimal(0))
                for c in ordered
            )
            return Calculation(
                total=rounded_total(earned, possible), earned=earned, possible=possible,
                contributions=contributions,
                sections=tuple((section, rounded_total(*values)) for section, values in sorted(sections.items())),
            )


def rounded_total(earned: Decimal, possible: Decimal) -> Decimal | None:
    if not possible:
        return None
    return (Decimal(100) * earned / possible).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
