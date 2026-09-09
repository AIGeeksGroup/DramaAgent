from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from .schema import DIMENSIONS, number


@dataclass(frozen=True)
class Config:
    backend: str = "demo"
    candidates: int = 3
    repair_rounds: int = 2
    threshold: float = 0.80
    min_improvement: float = 0.02
    weights: dict = field(default_factory=lambda: dict(zip(DIMENSIONS, (0.35, 0.30, 0.20, 0.15))))
    seed: int = 42
    width: int = 1280
    height: int = 720
    fps: int = 24
    transition_seconds: float = 0.12
    low_score_policy: str = "keep_best"
    options: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.backend not in ("demo", "command", "dashscope"):
            raise ValueError("backend must be demo, command or dashscope")
        for key, low in (("candidates", 1), ("repair_rounds", 0), ("seed", 0), ("width", 16), ("height", 16), ("fps", 1)):
            value = getattr(self, key)
            if type(value) is not int or value < low:
                raise ValueError(f"{key} must be an integer >= {low}")
        if self.width % 2 or self.height % 2:
            raise ValueError("width and height must be even for H.264")
        for key in ("threshold", "min_improvement", "transition_seconds"):
            number(getattr(self, key), key, 0, 1)
        if set(self.weights) != set(DIMENSIONS):
            raise ValueError("weights must specify all four reflection dimensions")
        for value in self.weights.values():
            number(value, "weight", 0, 1)
        if abs(sum(self.weights.values()) - 1) > 1e-8:
            raise ValueError("weights must sum to 1")
        if self.low_score_policy not in ("keep_best", "fail"):
            raise ValueError("low_score_policy must be keep_best or fail")
        if not isinstance(self.options, dict):
            raise ValueError("options must be an object")

    @classmethod
    def load(cls, path=None):
        return cls(**json.loads(Path(path).read_text())) if path else cls()

    def to_dict(self):
        return asdict(self)
