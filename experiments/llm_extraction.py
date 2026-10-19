"""基于 DeepSeek 的契约实体/关系抽取。

流程：
1. 调用 DeepSeek Chat，强制返回 JSON。
2. 校验每个实体和关系的 evidence 是否逐字来自 OCR 文本。
3. 校验关系两端必须引用已通过校验的实体。
4. LLM 失败、JSON 无效或校验结果为空时，回退到规则抽取。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.literature_pipeline import extract_contract, render_graph


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60

ALLOWED_ENTITY_TYPES = {
    "person",
    "organization",
    "land",
    "price",
    "time",
    "location",
    "contract",
    "seal",
    "annotation",
}

ALLOWED_PREDICATES = {
    "sold_land_to",
    "bought_land",
    "witnessed_contract",
    "signed_contract",
    "located_at",
    "paid_price",
    "belongs_to",
    "family_relation_to",
}

SYSTEM_PROMPT = """\
你是古代契约文书的文本结构化助手。请从 OCR 文本中抽取实体和关系。

硬性要求：
1. 只输出 JSON 对象，不要解释，不要 Markdown 代码块。
2. 不得添加 OCR 文本中不存在的信息。
3. 每个实体和关系必须包含 evidence 字段。
4. evidence 必须逐字来自 OCR 文本，可以是其中的连续片段。
5. evidence 中包含 □ 或 ■ 时，对应实体或关系应省略。
6. 无法确定的字段填 null，不要猜测。
7. 不要根据契约常见套语补全文句。
8. 人名、地名、堂号保持 OCR 原文写法，不要转换简繁体。
9. 关系的 subject 和 object 必须是 entities 中已出现的 id。

JSON 格式：
{
  "document_id": "字符串",
  "document_type": "contract",
  "title": "契约标题或空字符串",
  "entities": [
    {
      "id": "person:seller",
      "type": "person",
      "name": "人名",
      "evidence": "OCR原文连续片段"
    }
  ],
  "relations": [
    {
      "subject": "person:seller",
      "predicate": "sold_land_to",
      "object": "organization:buyer",
      "evidence": "OCR原文连续片段"
    }
  ],
  "metadata": {
    "time": null,
    "price": null,
    "area": null,
    "location": null
  }
}

允许的实体类型：
person, organization, land, price, time, location, contract, seal, annotation

允许的关系类型：
sold_land_to, bought_land, witnessed_contract, signed_contract,
located_at, paid_price, belongs_to, family_relation_to
"""


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def evidence_supported(evidence: str, source_text: str) -> bool:
    """校验 evidence 是否为 OCR 文本的连续片段。"""
    if not evidence:
        return False
    normalized_evidence = _normalize_text(evidence)
    if not normalized_evidence or "□" in normalized_evidence or "■" in normalized_evidence:
        return False
    return normalized_evidence in _normalize_text(source_text)


def parse_json_response(content: str) -> dict:
    """解析模型输出，兼容偶发的 Markdown 代码块。"""
    cleaned = (content or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def call_deepseek(
    text: str,
    document_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEEPSEEK_DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
    http_post: Callable[..., requests.Response] | None = None,
) -> dict:
    """调用 DeepSeek Chat API，并返回解析后的 JSON 对象。"""
    if not api_key:
        raise ValueError("DeepSeek API key is required")

    post = http_post or requests.post
    response = post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"document_id: {document_id}\n"
                        f"OCR 文本：\n{text}"
                    ),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    return parse_json_response(content)


def _valid_entity(entity: dict, source_text: str) -> bool:
    return (
        isinstance(entity, dict)
        and bool(str(entity.get("id", "")).strip())
        and entity.get("type") in ALLOWED_ENTITY_TYPES
        and bool(str(entity.get("name", "")).strip())
        and evidence_supported(str(entity.get("evidence", "")), source_text)
    )


def _valid_relation(
    relation: dict,
    entity_ids: set[str],
    source_text: str,
) -> bool:
    return (
        isinstance(relation, dict)
        and relation.get("subject") in entity_ids
        and relation.get("object") in entity_ids
        and relation.get("predicate") in ALLOWED_PREDICATES
        and evidence_supported(str(relation.get("evidence", "")), source_text)
    )


def _validated_metadata(metadata: dict, source_text: str) -> dict:
    result = {}
    for key, value in (metadata or {}).items():
        if value is None:
            result[key] = None
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
            continue
        if isinstance(value, str) and evidence_supported(value, source_text):
            result[key] = value
    return result


def validate_extraction(
    candidate: dict,
    source_text: str,
    document_id: str | None = None,
) -> tuple[dict, dict]:
    """过滤幻觉实体、坏引用关系和不受 OCR 支持的 metadata。"""
    if not isinstance(candidate, dict):
        candidate = {}

    accepted_entities = []
    rejected_entities = 0
    seen_entity_ids = set()
    for entity in candidate.get("entities", []):
        entity_id = str(entity.get("id", "")) if isinstance(entity, dict) else ""
        if _valid_entity(entity, source_text) and entity_id not in seen_entity_ids:
            accepted_entities.append(entity)
            seen_entity_ids.add(entity_id)
        else:
            rejected_entities += 1

    entity_ids = {str(entity["id"]) for entity in accepted_entities}
    accepted_relations = []
    rejected_relations = 0
    for relation in candidate.get("relations", []):
        if _valid_relation(relation, entity_ids, source_text):
            accepted_relations.append(relation)
        else:
            rejected_relations += 1

    resolved_document_id = str(
        document_id or candidate.get("document_id") or "unknown"
    )
    title = str(candidate.get("title") or "")
    if title and not evidence_supported(title, source_text):
        title = ""
    result = {
        "document_id": resolved_document_id,
        "document_type": candidate.get("document_type") or "contract",
        "title": title,
        "entities": accepted_entities,
        "relations": accepted_relations,
        "metadata": _validated_metadata(candidate.get("metadata", {}), source_text),
    }
    report = {
        "accepted_entities": len(accepted_entities),
        "rejected_entities": rejected_entities,
        "accepted_relations": len(accepted_relations),
        "rejected_relations": rejected_relations,
    }
    return result, report


def extract_with_fallback(
    text: str,
    document_id: str,
    caller: Callable[[str, str], dict],
) -> dict:
    """优先使用通过校验的 LLM 结果；失败时回退到规则抽取。"""
    try:
        candidate = caller(text, document_id)
        if not isinstance(candidate, dict):
            raise ValueError("LLM response is not a JSON object")
        extraction, validation = validate_extraction(
            candidate,
            text,
            document_id=document_id,
        )
        if validation["accepted_entities"] == 0:
            raise ValueError("validated LLM result has no supported entities")
        return {
            "document_id": document_id,
            "extraction_method": "llm",
            "extraction": extraction,
            "validation": validation,
            "fallback_reason": None,
        }
    except Exception as exc:
        extraction = extract_contract(text, document_id)
        return {
            "document_id": document_id,
            "extraction_method": "rules_fallback",
            "extraction": extraction,
            "validation": {
                "accepted_entities": len(extraction["entities"]),
                "rejected_entities": 0,
                "accepted_relations": len(extraction["relations"]),
                "rejected_relations": 0,
            },
            "fallback_reason": str(exc),
        }


def run_llm_extraction(
    input_dir: Path | str,
    output_dir: Path | str,
    caller: Callable[[str, str], dict],
) -> dict:
    """批量处理 OCR 文本，输出 LLM JSON 和 ECharts 图谱。"""
    source_dir = Path(input_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    results = []
    for text_path in sorted(source_dir.glob("*.txt")):
        document_id = text_path.stem
        text = text_path.read_text(encoding="utf-8")
        result = extract_with_fallback(text, document_id, caller=caller)

        json_path = destination / f"{document_id}.json"
        html_path = destination / f"{document_id}.html"
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        render_graph(result["extraction"], html_path)
        results.append(result)

    llm_documents = sum(
        1 for item in results if item["extraction_method"] == "llm"
    )
    result = {
        "results": results,
        "summary": {
            "documents": len(results),
            "llm_documents": llm_documents,
            "fallback_documents": len(results) - llm_documents,
            "accepted_entities": sum(
                item["validation"]["accepted_entities"] for item in results
            ),
            "rejected_entities": sum(
                item["validation"]["rejected_entities"] for item in results
            ),
            "accepted_relations": sum(
                item["validation"]["accepted_relations"] for item in results
            ),
            "rejected_relations": sum(
                item["validation"]["rejected_relations"] for item in results
            ),
        },
    }
    (destination / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek 契约实体关系抽取")
    parser.add_argument("--input-dir", default="experiment_outputs/ocr")
    parser.add_argument("--output-dir", default="experiment_outputs/llm_graphs")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEEPSEEK_DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="LLM 失败时直接报错，而不是回退到规则抽取",
    )
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        if args.no_fallback:
            raise RuntimeError(f"Environment variable {args.api_key_env} is required")
        reason = f"{args.api_key_env} is not configured"

        def caller(_text: str, _document_id: str) -> dict:
            raise RuntimeError(reason)

    else:

        def caller(text: str, document_id: str) -> dict:
            return call_deepseek(
                text,
                document_id,
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
                timeout=args.timeout,
            )

    result = run_llm_extraction(args.input_dir, args.output_dir, caller=caller)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
