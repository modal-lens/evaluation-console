from tools.entity_extraction import add_entity_ids, clean_extraction_result, merge_results


def test_reverse_full_name_resolution_uses_full_name_as_canonical() -> None:
    cleaned = clean_extraction_result(
        {
            "entities": [
                {
                    "name": "星河智能",
                    "type": "企业",
                    "attributes": {"简称": "星河智能科技有限公司"},
                    "description": "星河智能科技有限公司位于深圳市南山区。",
                }
            ],
            "relationships": [],
            "resolutions": [
                {
                    "mention": "星河智能科技有限公司",
                    "canonical_entity": "星河智能",
                }
            ],
        },
        entity_attribute_schema={"企业": ["简称"]},
        entity_type=["企业"],
        relation_types=[],
        invalid_relation_evidence_keywords={},
        invalid_established_sources=set(),
    )

    merged = merge_results([cleaned])
    result = add_entity_ids(merged)

    assert result["entities"][0]["name"] == "星河智能科技有限公司"
    assert result["entities"][0]["attributes"]["简称"] == "星河智能"
    assert result["resolutions"][0]["mention"] == "星河智能"
    assert result["resolutions"][0]["canonical_entity"] == "星河智能科技有限公司"
    assert result["resolutions"][0]["entity_id"] == result["entities"][0]["entity_id"]
