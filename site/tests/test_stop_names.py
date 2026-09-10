import pytest

from app.services.stop_names import clean_stop_name


@pytest.mark.parametrize("code", ["sglmtmg", "sglmtma", "sglmtmd", "sglpwdj", "sglpwdm"])
def test_yate_shopping_centre(code):
    assert clean_stop_name("Shopping Centre", code) == "Yate Shopping Centre"
    assert clean_stop_name("Shopping Centre", code.upper()) == "Yate Shopping Centre"
    assert clean_stop_name("Renamed Stop", code) == "Renamed Stop"


def test_unknown_shopping_centre_does_not_infer_thornbury():
    for code in ("sglmtxx", "sglpmxx", "sglpwxx"):
        assert clean_stop_name("Shopping Centre", code) == "Shopping Centre"
