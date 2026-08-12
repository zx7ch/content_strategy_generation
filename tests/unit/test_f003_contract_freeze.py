from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
F003_DIR = ROOT / "docs" / "features" / "f003"


def read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_f003_source_documents_exist_and_are_non_empty():
    required_docs = [
        "F003_content_research_prd.md",
        "F003_content_research_architecture.md",
        "F003_content_research_schema_domain_objects.md",
        "F003_content_research_development_plan.md",
    ]

    for filename in required_docs:
        path = F003_DIR / filename
        assert path.exists(), f"missing F003 source document: {path}"
        assert path.stat().st_size > 1000, f"F003 source document looks empty: {path}"


def test_f003_development_plan_links_sources_and_records_delivery_contract():
    plan = read_doc(F003_DIR / "F003_content_research_development_plan.md")

    for linked_doc in [
        "./F003_content_research_prd.md",
        "./F003_content_research_architecture.md",
        "./F003_content_research_schema_domain_objects.md",
    ]:
        assert linked_doc in plan

    required_sections = [
        "## 1. 计划决定",
        "## 2. 当前基线与切换范围",
        "## 3. 四日交付节奏",
        "## 4. 实施阶段、产物与验收",
        "## 5. E2E 真实性与分支覆盖策略",
        "## 7. 风险、降级与范围纪律",
        "## 8. 最终发布判定",
    ]
    for section in required_sections:
        assert section in plan


def test_v2_specs_do_not_reference_f003_contracts():
    forbidden_terms = [
        "F003",
        "Content Research",
        "content research",
        "内容调研",
        "F003_content_research_development_plan.md",
        "F003_content_research_schema_domain_objects.md",
    ]
    v2_specs = [
        ROOT / "docs" / "v2" / "dev_specv2.md",
        ROOT / "docs" / "v2" / "development_tasks.md",
    ]

    for path in v2_specs:
        content = read_doc(path)
        for term in forbidden_terms:
            assert term not in content, f"{path} should not reference {term!r}"
