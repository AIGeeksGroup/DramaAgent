"""Validated, JSON-serializable story and reflection contracts."""
from dataclasses import asdict, dataclass, field
import math
import re

DIMENSIONS = ("identity", "semantic", "temporal", "audio_visual")


def text(value, name, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a {'possibly empty ' if allow_empty else ''}string")
    return value


def number(value, name, low=0, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError(f"Invalid identifier: {value!r}; use letters, digits, _ or -")
    return value


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    appearance: str
    voice: str = ""
    reference: str = ""

    def __post_init__(self):
        identifier(self.id)
        for name in ("name", "appearance", "voice", "reference"):
            text(getattr(self, name), name, name in ("voice", "reference"))


@dataclass(frozen=True)
class Dialogue:
    speaker: str
    text: str
    start: float
    end: float
    emotion: str = "neutral"
    delivery: str = "natural"

    def __post_init__(self):
        identifier(self.speaker)
        text(self.text, "dialogue text")
        text(self.emotion, "emotion")
        text(self.delivery, "delivery")
        number(self.start, "start")
        number(self.end, "end")
        if self.end <= self.start:
            raise ValueError("Dialogue end must be after start")


@dataclass(frozen=True)
class Scene:
    id: str
    description: str
    characters: list[str]
    environment: str
    emotion: str
    transition: str = ""
    duration: float = 5.0
    dialogue: list[Dialogue] = field(default_factory=list)
    ambience: str = ""
    music: str = ""

    def __post_init__(self):
        identifier(self.id)
        for name in ("description", "environment", "emotion", "transition", "ambience", "music"):
            text(getattr(self, name), name, name in ("transition", "ambience", "music"))
        number(self.duration, "duration", 0.1, 600)
        if not isinstance(self.characters, list) or len(self.characters) != len(set(self.characters)):
            raise ValueError("Scene characters must be a unique list")
        for character in self.characters:
            identifier(character)
        previous_end = 0
        for line in self.dialogue:
            if line.speaker not in self.characters:
                raise ValueError(f"Speaker {line.speaker} is not in scene {self.id}")
            if line.end > self.duration or line.start < previous_end:
                raise ValueError("Dialogue must be ordered, non-overlapping and fit the scene")
            previous_end = line.end


@dataclass(frozen=True)
class Story:
    title: str
    narrative: str
    characters: list[Character]
    scenes: list[Scene]

    def __post_init__(self):
        text(self.title, "title")
        text(self.narrative, "narrative")
        if not self.scenes:
            raise ValueError("Story must contain at least one scene")
        ids = [c.id for c in self.characters]
        scene_ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)) or len(scene_ids) != len(set(scene_ids)):
            raise ValueError("Duplicate character or scene identifiers")
        for scene in self.scenes:
            if set(scene.characters) - set(ids):
                raise ValueError(f"Unknown characters in scene {scene.id}")

    @classmethod
    def from_dict(cls, data):
        return cls(title=data["title"], narrative=data["narrative"],
                   characters=[Character(**c) for c in data["characters"]],
                   scenes=[Scene(**{**s, "dialogue": [Dialogue(**d) for d in s.get("dialogue", [])]}) for s in data["scenes"]])

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Assessment:
    scores: dict[str, float]
    issues: dict[str, str]
    summary: str
    evidence: str

    def __post_init__(self):
        if set(self.scores) != set(DIMENSIONS):
            raise ValueError(f"Scores must contain exactly {DIMENSIONS}")
        for key, value in self.scores.items():
            number(value, key, 0, 1)
        if set(self.issues) - set(DIMENSIONS):
            raise ValueError("Unknown issue dimension")
        for value in self.issues.values():
            text(value, "issue", True)
        text(self.summary, "observed summary")
        text(self.evidence, "evidence")

    def score(self, weights):
        return sum(weights[k] * self.scores[k] for k in DIMENSIONS)

    @property
    def weakest(self):
        return min(DIMENSIONS, key=lambda k: self.scores[k])
