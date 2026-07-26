import re
from dataclasses import dataclass

_RESET_PHRASES = {"new search", "start over", "reset", "begin again"}
_VALIDATION_RE = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class SelectionResult:
    positions: list[int] | None
    error: str | None
    is_reset: bool

    @property
    def is_selection(self) -> bool:
        return self.positions is not None


def parse_selection(text: str, max_position: int) -> SelectionResult:
    """Parse a WhatsApp reply such as ``1, 3, 4`` into a sorted list of positions.

    Returns a SelectionResult. ``positions`` is None when the message is not a
    numeric selection (either a reset command or an unrelated message). ``error``
    is set when digits were found but some were out of range.
    """
    normalized = text.strip().lower()

    if normalized in _RESET_PHRASES:
        return SelectionResult(positions=None, error=None, is_reset=True)

    tokens = _VALIDATION_RE.findall(normalized)
    if not tokens:
        return SelectionResult(positions=None, error=None, is_reset=False)

    numbers = sorted({int(token) for token in tokens})
    invalid = [n for n in numbers if n < 1 or n > max_position]
    if invalid:
        return SelectionResult(
            positions=None,
            error=(
                f"Invalid selection: {', '.join(str(n) for n in invalid)}. "
                f"Please reply with numbers between 1 and {max_position}."
            ),
            is_reset=False,
        )

    return SelectionResult(positions=numbers, error=None, is_reset=False)


def is_affirmative(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"yes", "y", "confirm", "approve", "ok", "okay", "confirmed"}


def is_negative(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"no", "n", "change", "edit", "cancel"}
