"""跨文档实体消歧核心行为测试。"""
from app.services.analysis_components.entity_resolver import EntityResolver


def _node(name, role="Seller", time_ad=1821, location="武昌府", doc_id="doc-1"):
    return {
        "original_name": name,
        "role": role,
        "doc_id": doc_id,
        "time_ad": time_ad,
        "location": location,
        "data": {},
    }


def test_merges_traditional_and_simplified_name_variants():
    entities = EntityResolver.resolve_entities(
        [
            _node("張三", doc_id="doc-1"),
            _node("张三", role="Buyer", doc_id="doc-2"),
        ]
    )

    assert len(entities) == 1
    assert len(entities[0]["instances"]) == 2
    assert entities[0]["cross_role"] is True


def test_keeps_different_people_separate():
    entities = EntityResolver.resolve_entities(
        [
            _node("張三", doc_id="doc-1"),
            _node("李四", role="Buyer", doc_id="doc-2"),
        ]
    )

    assert len(entities) == 2
