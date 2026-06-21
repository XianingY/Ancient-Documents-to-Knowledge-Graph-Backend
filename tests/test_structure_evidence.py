from app.services.llm_client import _validate_extracted_fields


def test_supported_structure_fields_keep_exact_evidence():
    source = "立賣田契人楊大選今將私置田地賣與熊篤敘堂價錢乙串"
    extracted = {
        "Seller": {"value": "楊大選", "evidence": "立賣田契人楊大選"},
        "Buyer": {"value": "熊篤敘堂", "evidence": "賣與熊篤敘堂"},
        "Price": {"value": "乙串", "evidence": "價錢乙串"},
    }

    result = _validate_extracted_fields(extracted, source)

    assert result["Seller"] == "楊大選"
    assert result["Buyer"] == "熊篤敘堂"
    assert result["Price"] == "乙串"
    assert result["Evidence"]["Seller"] == "立賣田契人楊大選"


def test_unsupported_or_placeholder_evidence_is_rejected():
    source = "立賣田契人楊大選賣與熊□堂"
    extracted = {
        "Seller": {"value": "楊大運", "evidence": "立賣田契人楊大選"},
        "Buyer": {"value": "熊篤敘堂", "evidence": "熊□堂"},
        "Price": {"value": "七串零六百文", "evidence": "七串零六百文"},
    }

    result = _validate_extracted_fields(extracted, source)

    assert result["Seller"] == "未识别"
    assert result["Buyer"] == "未识别"
    assert result["Price"] == "未识别"
    assert result["FieldConfidence"]["Seller"] == 0.0
