"""族谱/契约文献图片实验管线。

当前实现面向两类数据：
1. 契约文书：已有 OCR 文本，直接抽取实体、关系并生成图谱。
2. 行传图：超大 PNG，默认仅生成低分辨率预览，供高级 OCR 实验使用。
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


Image.MAX_IMAGE_PIXELS = None


CONTRACT_IMAGE_SUFFIXES = {".jpg", ".jpeg"}
GENEALOGY_IMAGE_SUFFIXES = {".png"}
UNCERTAIN_CHARACTERS = {"囗": "□", "■": "□"}


@dataclass(frozen=True)
class DatasetPaths:
    data_dir: Path
    contract_dir: Path
    genealogy_dir: Path


def _safe_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _image_metadata(path: Path, root: Path) -> dict:
    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
    return {
        "image": path.name,
        "relative_path": _safe_relative(path, root),
        "width": width,
        "height": height,
        "mode": mode,
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
    }


def _discover_dirs(data_dir: Path) -> DatasetPaths:
    contract_dirs = [
        path
        for path in data_dir.iterdir()
        if path.is_dir() and "契约" in path.name
    ]
    genealogy_dirs = [
        path
        for path in data_dir.iterdir()
        if path.is_dir() and "行传" in path.name
    ]
    if len(contract_dirs) != 1:
        raise FileNotFoundError("契约数据目录不存在或数量不唯一")
    if len(genealogy_dirs) != 1:
        raise FileNotFoundError("行传图数据目录不存在或数量不唯一")
    return DatasetPaths(data_dir, contract_dirs[0], genealogy_dirs[0])


def scan_dataset(data_dir: Path | str) -> dict:
    """扫描契约与行传数据目录，返回实验数据清单。"""
    data_path = Path(data_dir)
    paths = _discover_dirs(data_path)

    contracts = []
    for image_path in sorted(paths.contract_dir.iterdir()):
        if image_path.suffix.lower() not in CONTRACT_IMAGE_SUFFIXES:
            continue
        text_path = image_path.with_suffix(".txt")
        item = _image_metadata(image_path, data_path)
        item.update(
            {
                "has_ocr_text": text_path.exists(),
                "ocr_text": text_path.name if text_path.exists() else None,
            }
        )
        contracts.append(item)

    genealogies = [
        _image_metadata(path, data_path)
        for path in sorted(paths.genealogy_dir.iterdir())
        if path.suffix.lower() in GENEALOGY_IMAGE_SUFFIXES
    ]

    return {
        "dataset": data_path.name,
        "contracts": contracts,
        "genealogies": genealogies,
        "summary": {
            "contract_images": len(contracts),
            "contract_images_with_ocr": sum(
                1 for item in contracts if item["has_ocr_text"]
            ),
            "genealogy_images": len(genealogies),
            "ocr_texts": sum(1 for item in contracts if item["has_ocr_text"]),
        },
    }


def normalize_ocr_text(text: str) -> str:
    """按契约点校规范归一化不确定字符。"""
    for source, target in UNCERTAIN_CHARACTERS.items():
        text = text.replace(source, target)
    return text


def prepare_outputs(data_dir: Path | str, output_dir: Path | str) -> dict:
    """复制契约 OCR 文本到输出目录，并生成统一 manifest。"""
    data_path = Path(data_dir)
    output_path = Path(output_dir)
    paths = _discover_dirs(data_path)
    ocr_dir = output_path / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    manifest = scan_dataset(data_path)
    ocr_files = []
    for item in manifest["contracts"]:
        source = paths.contract_dir / item["ocr_text"]
        if not source.exists():
            continue
        text = normalize_ocr_text(source.read_text(encoding="utf-8"))
        destination = ocr_dir / source.name
        destination.write_text(text, encoding="utf-8")
        ocr_files.append(
            {
                "document_id": source.stem,
                "source": _safe_relative(source, data_path),
                "output": _safe_relative(destination, output_path),
                "characters": len(text.replace("\n", "").replace(" ", "")),
            }
        )

    manifest["outputs"] = {
        "ocr_files": ocr_files,
    }
    manifest["summary"]["ocr_files"] = len(ocr_files)
    (output_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _entity(entity_id: str, entity_type: str, name: str, evidence: str) -> dict:
    return {
        "id": entity_id,
        "type": entity_type,
        "name": name,
        "evidence": evidence,
    }


def _relation(subject: str, predicate: str, obj: str, evidence: str) -> dict:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "evidence": evidence,
    }


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def extract_contract(text: str, document_id: str) -> dict:
    """从契约 OCR 文本中抽取实体、关系和元数据。"""
    normalized = normalize_ocr_text(text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    title = next((line for line in lines if "：" in line), "")

    seller = _first_match(
        r"(?:立永賣白田约人|立永賣白田約人|立永賣白約人|"
        r"立永賣白田契約人|立絕賣田契弟)"
        r"(.{2,12}?)(?=，|。|今|將|将|請|请|因)",
        normalized,
    )
    buyer = _first_match(
        r"(?:永賣與|賣與|永卖与|卖与|賣与)([^，。,；;\n]{1,12}?)名下",
        normalized,
    )
    if buyer in {"叔", "兄", "弟", "侄"}:
        buyer = _first_match(
            r"名下([\u4e00-\u9fff]{2,8})(?:為業|为业)",
            normalized,
        ) or buyer
    land = _first_match(
        r"(?:受分|置)([^，。,；;\n]+?)白田",
        normalized,
    )
    if not land:
        land = _first_match(
            r"(?:受分|置)([^，。,；;\n]{2,12}?)"
            r"(?=[一二三四五六七八九十百零〇壹贰貳叁參肆伍陆陸柒捌玖拾佰仟萬\d]+[畝亩])",
            normalized,
        )
    if not land:
        land = _first_match(
            r"將(?:本己|自己)([^，。,；;\n]{2,12}?)"
            r"(?=[一二三四五六七八九十百零〇壹贰貳叁參肆伍陆陸柒捌玖拾佰仟萬\d]+[畝亩])",
            normalized,
        )
    price = _first_match(
        r"(?:時值價錢|时值價錢|實值價錢|实值價錢|價紋銀|价纹银)"
        r"([^，。,；;\n整]+)",
        normalized,
    )
    date = _first_match(
        r"((?:康熙|雍正|乾隆|嘉庆|道光|咸丰|同治|光绪|宣统|民国)"
        r"[一二三四五六七八九十百零〇\d□]+年[一二三四五六七八九十冬腊□]+月)",
        normalized,
    )

    entities = []
    relations = []

    seller_id = f"person:{document_id}:seller"
    buyer_id = f"organization:{document_id}:buyer"
    land_id = f"land:{document_id}"
    price_id = f"price:{document_id}"
    date_id = f"time:{document_id}"

    if seller:
        entities.append(
            _entity(
                seller_id,
                "person",
                seller,
                next(
                    (
                        line
                        for line in lines
                        if "立" in line and ("約人" in line or "约人" in line)
                    ),
                    title,
                ),
            )
        )
    if buyer:
        entities.append(
            _entity(
                buyer_id,
                "organization",
                buyer,
                next((line for line in lines if "名下" in line), title),
            )
        )
    if land:
        entities.append(
            _entity(
                land_id,
                "land",
                land,
                next((line for line in lines if "白田" in line), title),
            )
        )
    if price:
        entities.append(
            _entity(
                price_id,
                "price",
                price,
                next((line for line in lines if "價錢" in line), title),
            )
        )
    if date:
        entities.append(
            _entity(date_id, "time", date, title or normalized[:40])
        )

    if seller and buyer:
        relations.append(
            _relation(
                seller_id,
                "sold_land_to",
                buyer_id,
                next((line for line in lines if "名下" in line), title),
            )
        )
    if seller and land:
        relations.append(
            _relation(
                seller_id,
                "sold_land",
                land_id,
                next((line for line in lines if "白田" in line), title),
            )
        )
    if buyer and land:
        relations.append(
            _relation(
                buyer_id,
                "bought_land",
                land_id,
                next((line for line in lines if "名下" in line), title),
            )
        )
    if date and seller:
        relations.append(
            _relation(
                seller_id,
                "contract_signed_on",
                date_id,
                title or date,
            )
        )
    if price and buyer:
        relations.append(
            _relation(
                buyer_id,
                "paid_price",
                price_id,
                next((line for line in lines if "價錢" in line), title),
            )
        )

    return {
        "document_id": document_id,
        "document_type": "contract",
        "title": title,
        "entities": entities,
        "relations": relations,
        "metadata": {
            "seller": seller,
            "buyer": buyer,
            "land": land,
            "price": price,
            "date": date,
        },
    }


def render_graph(extraction: dict, output_path: Path | str) -> None:
    """把抽取结果渲染为 ECharts 力导向图 HTML。"""
    nodes = [
        {
            "id": entity["id"],
            "name": entity["name"],
            "category": entity["type"],
            "symbolSize": 42 if entity["type"] == "person" else 34,
            "value": entity["type"],
        }
        for entity in extraction["entities"]
    ]
    links = [
        {
            "source": relation["subject"],
            "target": relation["object"],
            "value": relation["predicate"],
        }
        for relation in extraction["relations"]
    ]

    option = {
        "title": {
            "text": extraction.get("title") or extraction["document_id"],
            "left": "center",
        },
        "tooltip": {"trigger": "item", "formatter": "{b}<br>{c}"},
        "legend": [
            {
                "data": sorted({node["category"] for node in nodes}),
                "bottom": 10,
            }
        ],
        "series": [
            {
                "type": "graph",
                "layout": "force",
                "roam": True,
                "draggable": True,
                "data": nodes,
                "links": links,
                "categories": [
                    {"name": name}
                    for name in sorted({node["category"] for node in nodes})
                ],
                "label": {"position": "right", "show": True},
                "force": {"repulsion": 220, "edgeLength": [70, 150]},
                "emphasis": {"focus": "adjacency"},
            }
        ],
    }

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{extraction["document_id"]} 契约图谱</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@6/dist/echarts.min.js"></script>
  <style>
    html, body, #chart {{ width: 100%; height: 100%; margin: 0; }}
  </style>
</head>
<body>
  <div id="chart"></div>
  <script>
    const chart = echarts.init(document.getElementById('chart'));
    chart.setOption({json.dumps(option, ensure_ascii=False, indent=2)});
    window.addEventListener('resize', () => chart.resize());
  </script>
</body>
</html>
"""
    Path(output_path).write_text(html, encoding="utf-8")


def preprocess_genealogy(
    data_dir: Path | str,
    output_dir: Path | str,
    max_long_side: int = 2400,
) -> dict:
    """为超大行传图生成低分辨率预览，供后续 OCR 实验使用。"""
    data_path = Path(data_dir)
    output_path = Path(output_dir) / "genealogy_previews"
    output_path.mkdir(parents=True, exist_ok=True)
    paths = _discover_dirs(data_path)

    results = []
    for image_path in sorted(paths.genealogy_dir.iterdir()):
        if image_path.suffix.lower() not in GENEALOGY_IMAGE_SUFFIXES:
            continue
        with Image.open(image_path) as image:
            image.thumbnail((max_long_side, max_long_side))
            preview_path = output_path / f"{image_path.stem}.jpg"
            image.convert("RGB").save(preview_path, format="JPEG", quality=90)
        results.append(
            {
                "source": _safe_relative(image_path, data_path),
                "preview": _safe_relative(preview_path, output_path.parent),
                "max_long_side": max_long_side,
            }
        )

    return {
        "preprocessed": results,
        "summary": {"preprocessed_images": len(results)},
    }


def run(
    data_dir: Path | str,
    output_dir: Path | str,
    preprocess_genealogy_images: bool = False,
    max_long_side: int = 2400,
) -> dict:
    """执行契约实验主流程，可选生成行传图预览。"""
    output_path = Path(output_dir)
    manifest = prepare_outputs(data_dir, output_path)
    graph_dir = output_path / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)

    extractions = []
    for item in manifest["contracts"]:
        document_id = Path(item["image"]).stem
        text_path = output_path / "ocr" / f"{document_id}.txt"
        if not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        extraction = extract_contract(text, document_id)
        extraction_path = graph_dir / f"{document_id}.json"
        extraction_path.write_text(
            json.dumps(extraction, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        render_graph(extraction, graph_dir / f"{document_id}.html")
        extractions.append(
            {
                "document_id": extraction["document_id"],
                "json": _safe_relative(extraction_path, output_path),
                "html": _safe_relative(
                    graph_dir / f"{document_id}.html", output_path
                ),
                "entity_count": len(extraction["entities"]),
                "relation_count": len(extraction["relations"]),
            }
        )

    result = {
        **manifest,
        "extractions": extractions,
        "summary": {
            **manifest["summary"],
            "extractions": len(extractions),
            "graph_html_files": len(extractions),
        },
    }
    if preprocess_genealogy_images:
        result["genealogy_preprocessing"] = preprocess_genealogy(
            data_dir, output_path, max_long_side=max_long_side
        )
        result["summary"]["preprocessed_images"] = result[
            "genealogy_preprocessing"
        ]["summary"]["preprocessed_images"]

    (output_path / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="文献图片实验管线")
    parser.add_argument(
        "--data-dir",
        default="文献图片数据-6契-5行传",
        help="数据目录",
    )
    parser.add_argument(
        "--output-dir",
        default="experiment_outputs",
        help="输出目录",
    )
    parser.add_argument(
        "--preprocess-genealogy",
        action="store_true",
        help="同时生成行传图低分辨率预览",
    )
    parser.add_argument(
        "--max-long-side",
        type=int,
        default=2400,
        help="行传图预览最长边，默认 2400",
    )
    args = parser.parse_args()

    result = run(
        args.data_dir,
        args.output_dir,
        preprocess_genealogy_images=args.preprocess_genealogy,
        max_long_side=args.max_long_side,
    )
    print(
        json.dumps(
            {
                "summary": result["summary"],
                "output_dir": args.output_dir,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
