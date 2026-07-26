from app.services.selection import is_affirmative, is_negative, parse_selection


def test_parses_comma_separated():
    assert parse_selection("1,3,4", 5).positions == [1, 3, 4]


def test_parses_space_separated():
    assert parse_selection("1 3 4", 5).positions == [1, 3, 4]


def test_parses_and_connector():
    assert parse_selection("1 and 3", 5).positions == [1, 3]


def test_parses_mixed_separators():
    assert parse_selection("1, 3 and 4", 5).positions == [1, 3, 4]


def test_dedupes_and_sorts():
    assert parse_selection("3,1,3", 5).positions == [1, 3]


def test_out_of_range_returns_error():
    result = parse_selection("1,9", 5)
    assert result.positions is None
    assert result.error is not None
    assert "9" in result.error
    assert "between 1 and 5" in result.error


def test_no_digits_is_not_a_selection():
    result = parse_selection("find vendors in delhi", 5)
    assert result.positions is None
    assert result.error is None
    assert not result.is_reset


def test_reset_phrase_detected():
    assert parse_selection("new search", 5).is_reset
    assert parse_selection("Start Over", 5).is_reset


def test_affirmative_recognized():
    assert is_affirmative("yes")
    assert is_affirmative("Approve")
    assert is_affirmative("confirm")


def test_negative_recognized():
    assert is_negative("no")
    assert is_negative("Change")
    assert is_negative("edit")


def test_non_yes_no_is_neither():
    assert not is_affirmative("find vendors")
    assert not is_negative("find vendors")
