import pytest

from app.content_research.admission.brand_activity import (
    brand_activity_boundary_reason,
    build_brand_activity_candidates,
)
from app.content_research.admission.competitor_discovery import (
    build_competitor_discovery_candidates,
    competitor_discovery_boundary_reason,
)
from app.content_research.admission.content_performance import (
    build_content_performance_candidates,
    content_performance_boundary_reason,
)
from app.content_research.admission.keyword_growth import (
    build_keyword_growth_candidates,
    keyword_growth_boundary_reason,
)
from app.content_research.admission.product_marketing import (
    build_product_marketing_candidates,
    product_marketing_boundary_reason,
)
from app.content_research.admission.registry import (
    DEFAULT_ADMISSION_STRATEGIES,
    AdmissionStrategyRegistry,
)
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.admission.ugc_community import (
    build_ugc_candidates,
    ugc_boundary_reason,
)
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


class ExampleStrategy(AdmissionStrategy):
    def build_candidates(self, packet):
        return []

    def boundary_reason(self, candidate):
        return None


@pytest.mark.parametrize(
    ("direction_id", "builder", "validator"),
    [
        ("product_marketing", build_product_marketing_candidates, product_marketing_boundary_reason),
        ("content_performance", build_content_performance_candidates, content_performance_boundary_reason),
        ("competitor_discovery", build_competitor_discovery_candidates, competitor_discovery_boundary_reason),
        ("brand_activity", build_brand_activity_candidates, brand_activity_boundary_reason),
        ("keyword_growth", build_keyword_growth_candidates, keyword_growth_boundary_reason),
        ("ugc_community", build_ugc_candidates, ugc_boundary_reason),
    ],
)
def test_default_registry_preserves_each_specialist_strategy(direction_id, builder, validator):
    strategy = DEFAULT_ADMISSION_STRATEGIES.get(direction_id)
    packet = DirectionalEvidencePacketRecord(
        "dep_1", "v1", {"field_projection": {}, "retrieval_context": {}},
        workflow_run_id="run_1", research_direction_id=direction_id,
        canonical_source_id="cs_1", field_projection_hash="packet-hash",
    )
    candidate = ClaimCandidateRecord(
        "cc_1", "v1", {}, workflow_run_id="run_1",
        research_direction_id=direction_id, evidence_packet_id="dep_1",
        statement="sample", intent_id="intent", claim_type="invalid",
    )

    assert strategy is not None
    assert strategy.direction_id == direction_id
    assert strategy.build_candidates(packet) == builder(packet)
    assert strategy.boundary_reason(candidate) == validator(candidate)


def test_default_registry_exposes_no_strategy_for_unimplemented_direction():
    assert DEFAULT_ADMISSION_STRATEGIES.get("not_implemented") is None


def test_registry_rejects_duplicate_direction_registration():
    strategy = ExampleStrategy("example")

    with pytest.raises(ValueError, match="duplicate admission strategy"):
        AdmissionStrategyRegistry((("example", strategy), ("example", strategy)))


def test_registry_rejects_empty_direction_registration():
    with pytest.raises(ValueError, match="cannot be empty"):
        ExampleStrategy(" ")


def test_registry_rejects_registration_key_that_differs_from_strategy_direction():
    strategy = ExampleStrategy("example")

    with pytest.raises(ValueError, match="must match"):
        AdmissionStrategyRegistry((("different", strategy),))
