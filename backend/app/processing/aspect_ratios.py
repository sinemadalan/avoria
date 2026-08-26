import math
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class OutputAspectRatio(str, Enum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    SOCIAL_PORTRAIT = "4:5"

    @property
    def ratio(self) -> float:
        width, height = self.value.split(":", maxsplit=1)
        return int(width) / int(height)


OUTPUT_DIMENSIONS: Mapping[OutputAspectRatio, tuple[int, int]] = MappingProxyType(
    {
        OutputAspectRatio.LANDSCAPE: (1920, 1080),
        OutputAspectRatio.PORTRAIT: (1080, 1920),
        OutputAspectRatio.SQUARE: (1080, 1080),
        OutputAspectRatio.SOCIAL_PORTRAIT: (1080, 1350),
    }
)


def nearest_output_aspect_ratio(width: int, height: int) -> OutputAspectRatio:
    """Map arbitrary dimensions to the closest supported ratio deterministically."""
    if width <= 0 or height <= 0:
        raise ValueError("Video dimensions must be positive")
    source_ratio = width / height
    # Log distance treats reciprocal landscape/portrait deviations symmetrically.
    return min(
        OutputAspectRatio,
        key=lambda candidate: abs(math.log(source_ratio / candidate.ratio)),
    )
