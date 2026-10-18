"""文献图片实验管线的行为测试。"""
import importlib.util
import sys
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "experiments" / "literature_pipeline.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("literature_pipeline", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_dataset(tmp_path: Path):
    contract_dir = tmp_path / "1.契约文书"
    genealogy_dir = tmp_path / "2.行传图"
    contract_dir.mkdir()
    genealogy_dir.mkdir()
    Image.new("RGB", (12, 18), "white").save(contract_dir / "3.141.JPG")
    Image.new("RGB", (20, 16), "white").save(genealogy_dir / "24.png")
    (contract_dir / "3.141.txt").write_text(
        "3.141：道光十年十月熊德运卖田契\n"
        "立永賣白田约人熊德運，永賣與篤敘堂名下，"
        "将本己受分虎囗垸白田一形，時值價錢四串捌拾文整。\n"
        "憑中 孔珍\n",
        encoding="utf-8",
    )
    return tmp_path


def test_scan_dataset_reports_contract_and_genealogy_assets(tmp_path):
    pipeline = load_pipeline()
    data_dir = make_dataset(tmp_path)

    manifest = pipeline.scan_dataset(data_dir)

    assert manifest["summary"]["contract_images"] == 1
    assert manifest["summary"]["genealogy_images"] == 1
    assert manifest["summary"]["ocr_texts"] == 1
    assert manifest["contracts"][0]["image"] == "3.141.JPG"
    assert manifest["contracts"][0]["has_ocr_text"] is True
    assert manifest["genealogies"][0]["image"] == "24.png"


def test_prepare_outputs_normalizes_uncertain_character(tmp_path):
    pipeline = load_pipeline()
    data_dir = make_dataset(tmp_path)

    result = pipeline.prepare_outputs(data_dir, tmp_path / "out")

    normalized = (tmp_path / "out" / "ocr" / "3.141.txt").read_text(encoding="utf-8")
    assert "虎□垸" in normalized
    assert "虎囗垸" not in normalized
    assert result["summary"]["ocr_files"] == 1


def test_extract_entities_and_relations_with_evidence():
    pipeline = load_pipeline()
    text = (
        "3.141：道光十年十月熊德运卖田契\n"
        "立永賣白田约人熊德運，永賣與篤敘堂名下，"
        "将本己受分虎囗垸白田一形，時值價錢四串捌拾文整。\n"
        "憑中 孔珍\n"
    )

    result = pipeline.extract_contract(text, "3.141")

    assert result["document_id"] == "3.141"
    assert result["document_type"] == "contract"
    entity_names = {item["name"] for item in result["entities"]}
    assert {"熊德運", "篤敘堂", "虎□垸", "四串捌拾文", "道光十年十月"}.issubset(
        entity_names
    )
    assert all("evidence" in item for item in result["entities"])
    assert all("evidence" in item for item in result["relations"])
    assert any(
        relation["predicate"] == "sold_land_to"
        and relation["subject"].startswith("person:")
        and relation["object"].startswith("organization:")
        for relation in result["relations"]
    )


def test_extract_contract_handles_variants():
    pipeline = load_pipeline()

    result = pipeline.extract_contract(
        "3.143：道光十二年二月熊恒忠卖田赤契\n"
        "立永賣白約人姪恒忠，永賣與叔名下篤敘堂為業，"
        "將本己受分小堤團虎獐垸肆畝捌分玖厘壹毫。\n",
        "3.143",
    )

    assert result["metadata"]["seller"] == "姪恒忠"
    assert result["metadata"]["buyer"] == "篤敘堂"
    assert result["metadata"]["land"] == "小堤團虎獐垸"
    assert result["metadata"]["date"] == "道光十二年二月"


def test_extract_contract_handles_simplified_buyer_and_missing_month():
    pipeline = load_pipeline()

    result = pipeline.extract_contract(
        "3.145：康熙十年□月熊君锡卖田契\n"
        "立絕賣田契弟君錫，出筆絕賣與兄敷言名下，"
        "將自己受分漁粮田貳畝伍分。\n",
        "3.145",
    )

    assert result["metadata"]["seller"] == "君錫"
    assert result["metadata"]["buyer"] == "兄敷言"
    assert result["metadata"]["land"] == "漁粮田"
    assert result["metadata"]["date"] == "康熙十年□月"


def test_render_graph_creates_echarts_html(tmp_path):
    pipeline = load_pipeline()
    extraction = pipeline.extract_contract(
        "3.141：道光十年十月熊德运卖田契\n"
        "立永賣白田约人熊德運，永賣與篤敘堂名下。\n",
        "3.141",
    )
    output = tmp_path / "graph.html"

    pipeline.render_graph(extraction, output)

    html = output.read_text(encoding="utf-8")
    assert "echarts" in html
    assert "sold_land_to" in html
    assert "熊德運" in html


def test_preprocess_genealogy_creates_preview(tmp_path):
    pipeline = load_pipeline()
    data_dir = make_dataset(tmp_path)

    result = pipeline.preprocess_genealogy(data_dir, tmp_path / "out", max_long_side=8)

    preview = tmp_path / "out" / "genealogy_previews" / "24.jpg"
    assert preview.exists()
    with Image.open(preview) as image:
        assert max(image.size) <= 8
    assert result["summary"]["preprocessed_images"] == 1
