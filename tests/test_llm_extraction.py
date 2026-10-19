"""LLM 契约实体关系抽取的行为测试。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.llm_extraction import (
    call_deepseek,
    extract_with_fallback,
    parse_json_response,
    run_llm_extraction,
    validate_extraction,
)
from experiments.literature_pipeline import render_graph


OCR_TEXT = (
    "3.141：道光十年十月熊德运卖田契\n"
    "立永賣白田约人熊德運，永賣與篤敘堂名下。\n"
    "憑中 孔珍\n"
)


def _api_response(content):
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


def test_call_deepseek_builds_conservative_json_request(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: _api_response(
                json.dumps(
                    {
                        "document_id": "3.141",
                        "entities": [],
                        "relations": [],
                        "metadata": {},
                    }
                )
            )
        )

    monkeypatch.setattr("requests.post", fake_post)

    result = call_deepseek(OCR_TEXT, "3.141", api_key="test-key")

    assert result["document_id"] == "3.141"
    url, kwargs = calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["model"] == "deepseek-chat"
    assert kwargs["json"]["temperature"] == 0.1
    assert kwargs["json"]["response_format"] == {"type": "json_object"}
    assert "不得添加 OCR 文本中不存在的信息" in kwargs["json"]["messages"][0]["content"]
    assert "3.141" in kwargs["json"]["messages"][1]["content"]


def test_parse_json_response_strips_code_fence():
    content = "```json\n{\"ok\": true}\n```"

    assert parse_json_response(content) == {"ok": True}


def test_validate_extraction_rejects_hallucinated_and_broken_references():
    candidate = {
        "document_id": "3.141",
        "document_type": "contract",
        "entities": [
            {
                "id": "person:seller",
                "type": "person",
                "name": "熊德運",
                "evidence": "立永賣白田约人熊德運",
            },
            {
                "id": "organization:buyer",
                "type": "organization",
                "name": "篤敘堂",
                "evidence": "永賣與篤敘堂名下",
            },
            {
                "id": "person:hallucination",
                "type": "person",
                "name": "不存在的人",
                "evidence": "OCR文本中没有这句话",
            },
            {
                "id": "person:uncertain",
                "type": "person",
                "name": "□",
                "evidence": "□□",
            },
        ],
        "relations": [
            {
                "subject": "person:seller",
                "predicate": "sold_land_to",
                "object": "organization:buyer",
                "evidence": "永賣與篤敘堂名下",
            },
            {
                "subject": "person:seller",
                "predicate": "sold_land_to",
                "object": "organization:missing",
                "evidence": "永賣與篤敘堂名下",
            },
            {
                "subject": "person:seller",
                "predicate": "witnessed_contract",
                "object": "person:hallucination",
                "evidence": "OCR文本中没有这句话",
            },
        ],
        "metadata": {"seller": "熊德運"},
    }

    result, report = validate_extraction(candidate, OCR_TEXT)

    assert [entity["id"] for entity in result["entities"]] == [
        "person:seller",
        "organization:buyer",
    ]
    assert len(result["relations"]) == 1
    assert result["relations"][0]["object"] == "organization:buyer"
    assert report == {
        "accepted_entities": 2,
        "rejected_entities": 2,
        "accepted_relations": 1,
        "rejected_relations": 2,
    }


def test_extract_with_fallback_uses_rules_when_llm_fails():
    def failing_caller(text, document_id):
        raise RuntimeError("API unavailable")

    result = extract_with_fallback(
        OCR_TEXT,
        "3.141",
        caller=failing_caller,
    )

    assert result["extraction_method"] == "rules_fallback"
    assert result["extraction"]["metadata"]["seller"] == "熊德運"
    assert result["fallback_reason"] == "API unavailable"


def test_extract_with_fallback_uses_validated_llm_result():
    llm_result = {
        "document_id": "3.141",
        "document_type": "contract",
        "entities": [
            {
                "id": "person:seller",
                "type": "person",
                "name": "熊德運",
                "evidence": "立永賣白田约人熊德運",
            },
            {
                "id": "person:middleman",
                "type": "person",
                "name": "孔珍",
                "evidence": "憑中 孔珍",
            },
        ],
        "relations": [
            {
                "subject": "person:middleman",
                "predicate": "witnessed_contract",
                "object": "person:seller",
                "evidence": "憑中 孔珍",
            }
        ],
        "metadata": {"seller": "熊德運"},
    }

    def successful_caller(text, document_id):
        return llm_result

    result = extract_with_fallback(
        OCR_TEXT,
        "3.141",
        caller=successful_caller,
    )

    assert result["extraction_method"] == "llm"
    assert result["extraction"]["entities"][1]["name"] == "孔珍"
    assert result["validation"]["accepted_entities"] == 2
    assert result["fallback_reason"] is None


def test_run_llm_extraction_writes_json_html_and_summary(tmp_path):
    input_dir = tmp_path / "ocr"
    output_dir = tmp_path / "llm_graphs"
    input_dir.mkdir()
    (input_dir / "3.141.txt").write_text(OCR_TEXT, encoding="utf-8")

    def successful_caller(text, document_id):
        return {
            "document_id": document_id,
            "document_type": "contract",
            "entities": [
                {
                    "id": "person:seller",
                    "type": "person",
                    "name": "熊德運",
                    "evidence": "立永賣白田约人熊德運",
                }
            ],
            "relations": [],
            "metadata": {"seller": "熊德運"},
        }

    result = run_llm_extraction(
        input_dir,
        output_dir,
        caller=successful_caller,
    )

    json_path = output_dir / "3.141.json"
    html_path = output_dir / "3.141.html"
    summary_path = output_dir / "result.json"
    assert json_path.exists()
    assert html_path.exists()
    assert summary_path.exists()
    assert json.loads(summary_path.read_text(encoding="utf-8"))["summary"][
        "documents"
    ] == 1
    assert "熊德運" in html_path.read_text(encoding="utf-8")
    assert result["summary"]["documents"] == 1
    assert result["summary"]["llm_documents"] == 1
    assert result["summary"]["fallback_documents"] == 0
