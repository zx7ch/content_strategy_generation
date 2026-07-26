import pytest

from app.content_research.admission.cross_direction import ActionHypothesisRequest


def test_action_hypothesis_request_is_explicit_and_immutable():
    request = ActionHypothesisRequest("测试一个行动。", ("cc_1", "cc_2"))

    assert request.derivation_method == "explicit_action_hypothesis"
    with pytest.raises(AttributeError):
        request.statement = "改变"
