"""TTS text preparation (FR-16)."""
from supplier_caller.speech import speakable


def test_codes_are_spelled() -> None:
    assert speakable("The code is K0001.") == "The code is K zero zero zero one."


def test_zip_is_digit_by_digit() -> None:
    assert speakable("deliver to 60640?") == "deliver to six zero six four zero?"


def test_phone_numbers_untouched_and_other_numbers_untouched() -> None:
    assert speakable("about 2 days, she's 72") == "about 2 days, she's 72"


def test_stage_directions_are_never_spoken() -> None:
    from supplier_caller.speech import is_stage_direction

    for sd in ["(Silence)", "(silence).", "[no response]", "*stays quiet*", " (pause) "]:
        assert is_stage_direction(sd), sd
    for real in ["Sure, take your time.", "(K zero zero zero one) is the code", "Yes."]:
        assert not is_stage_direction(real), real
