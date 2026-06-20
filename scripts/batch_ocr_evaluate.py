#!/usr/bin/env python3
"""
批量 OCR 评估脚本：对比 ground truth 计算字符级准确率。
用法: python scripts/batch_ocr_evaluate.py [--limit N] [--output report.json]
"""
import json
import os
import sys
import time
import argparse
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, Image, OcrResult, OcrStatus


def load_ground_truth():
    """加载 ground truth 索引"""
    gt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "ground_truth.json")
    with open(gt_path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_images_to_gt(db, gt_index):
    """将数据库图片与 ground truth 匹配"""
    matched = []
    all_images = db.query(Image).filter(Image.filename.like("3.%")).all()
    for img in all_images:
        filename = img.filename
        parts = filename.split("_")
        num_part = parts[0].replace(".", "·")
        if num_part in gt_index:
            gt_text = gt_index[num_part]["body"]
            matched.append({
                "image_id": img.id,
                "filename": filename,
                "gt_key": num_part,
                "gt_title": gt_index[num_part]["title"],
                "gt_text": gt_text,
                "gt_char_count": len(gt_text),
            })
    return matched


def char_level_metrics(pred: str, gt: str) -> dict:
    """计算字符级指标"""
    # 清理文本：去除空白差异
    pred_clean = "".join(pred.split())
    gt_clean = "".join(gt.split())

    # 字符级统计
    pred_chars = Counter(pred_clean)
    gt_chars = Counter(gt_clean)

    # 逐字符对齐（简单 LCS 方法）
    m, n = len(gt_clean), len(pred_clean)
    # 使用空间优化的 LCS
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if gt_clean[i - 1] == pred_clean[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(curr[j - 1], prev[j])
        prev = curr
    lcs_len = prev[n]

    # 精确匹配字符数
    exact_matches = sum((pred_chars & gt_chars).values())

    # 指标
    precision = exact_matches / max(len(pred_clean), 1)
    recall = exact_matches / max(len(gt_clean), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)

    # 缺失字符
    missing_chars = []
    for char in gt_clean:
        if gt_chars[char] > pred_chars.get(char, 0):
            missing_chars.append(char)

    # 多余字符
    extra_chars = []
    for char in pred_clean:
        if pred_chars[char] > gt_chars.get(char, 0):
            extra_chars.append(char)

    return {
        "gt_len": len(gt_clean),
        "pred_len": len(pred_clean),
        "lcs_len": lcs_len,
        "exact_matches": exact_matches,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "missing_count": len(missing_chars),
        "extra_count": len(extra_chars),
        "missing_chars": "".join(missing_chars[:20]),
        "extra_chars": "".join(extra_chars[:20]),
    }


def run_ocr_on_image(image_path: str) -> str:
    """对单张图片运行完整 OCR pipeline"""
    from app.services.ocr_service import (
        _preprocess_image,
        _run_api_predict,
        _clean_vl_output,
        _correct_ocr_text,
    )

    enhanced = _preprocess_image(image_path)
    raw = _run_api_predict(enhanced)
    if raw.startswith("Error:"):
        return raw
    cleaned = _clean_vl_output(raw)
    corrected = _correct_ocr_text(cleaned)

    # 清理临时文件
    if enhanced != image_path and os.path.exists(enhanced):
        try:
            os.remove(enhanced)
        except OSError:
            pass

    return corrected


def main():
    parser = argparse.ArgumentParser(description="批量 OCR 评估")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量 (0=全部)")
    parser.add_argument("--output", type=str, default="ocr_eval_report.json", help="输出报告文件")
    parser.add_argument("--delay", type=float, default=1.0, help="每次API调用间隔(秒)")
    args = parser.parse_args()

    import dashscope
    from app.core.config import settings
    dashscope.api_key = settings.DASHSCOPE_API_KEY

    print("=" * 60)
    print("批量 OCR 准确率评估")
    print("=" * 60)

    # 加载数据
    gt_index = load_ground_truth()
    db = SessionLocal()
    matched = match_images_to_gt(db, gt_index)
    print(f"Ground truth 总数: {len(gt_index)}")
    print(f"匹配图片数: {len(matched)}")

    if args.limit > 0:
        matched = matched[:args.limit]
        print(f"限制处理: {args.limit} 张")

    # 加载旧结果
    old_results = {}
    old_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "old_ocr_results.json")
    if os.path.exists(old_path):
        with open(old_path, "r", encoding="utf-8") as f:
            old_results = json.load(f)

    results = []
    total_metrics = {
        "gt_len": 0, "pred_len": 0, "exact_matches": 0,
        "lcs_len": 0, "total_gt_chars": 0, "total_pred_chars": 0,
    }
    old_total_metrics = {
        "gt_len": 0, "pred_len": 0, "exact_matches": 0,
        "lcs_len": 0, "total_gt_chars": 0, "total_pred_chars": 0,
    }

    start_time = time.time()

    for idx, item in enumerate(matched):
        image_path = str(db.query(Image).filter(Image.id == item["image_id"]).first().path)
        gt_text = item["gt_text"]

        print(f"\n[{idx + 1}/{len(matched)}] {item['gt_key']}: {item['gt_title']}")
        print(f"  图片: {item['filename']}")

        # 运行 OCR
        try:
            pred_text = run_ocr_on_image(image_path)
            if pred_text.startswith("Error:"):
                print(f"  ❌ OCR 失败: {pred_text}")
                continue
        except Exception as e:
            print(f"  ❌ OCR 异常: {e}")
            continue

        # 计算指标
        metrics = char_level_metrics(pred_text, gt_text)
        results.append({
            "image_id": item["image_id"],
            "filename": item["filename"],
            "gt_key": item["gt_key"],
            "gt_title": item["gt_title"],
            "gt_len": metrics["gt_len"],
            "pred_len": metrics["pred_len"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "exact_matches": metrics["exact_matches"],
            "missing_count": metrics["missing_count"],
            "extra_count": metrics["extra_count"],
            "missing_chars": metrics["missing_chars"],
            "extra_chars": metrics["extra_chars"],
        })

        # 累加
        total_metrics["gt_len"] += metrics["gt_len"]
        total_metrics["pred_len"] += metrics["pred_len"]
        total_metrics["exact_matches"] += metrics["exact_matches"]
        total_metrics["lcs_len"] += metrics["lcs_len"]
        total_metrics["total_gt_chars"] += metrics["gt_len"]
        total_metrics["total_pred_chars"] += metrics["pred_len"]

        print(f"  GT: {metrics['gt_len']} | Pred: {metrics['pred_len']} | "
              f"P: {metrics['precision']:.2%} | R: {metrics['recall']:.2%} | F1: {metrics['f1']:.2%}")

        # 限速
        time.sleep(args.delay)

    elapsed = time.time() - start_time

    # 计算总体指标
    overall_precision = total_metrics["exact_matches"] / max(total_metrics["total_pred_chars"], 1)
    overall_recall = total_metrics["exact_matches"] / max(total_metrics["total_gt_chars"], 1)
    overall_f1 = 2 * overall_precision * overall_recall / max(overall_precision + overall_recall, 1e-10)

    report = {
        "summary": {
            "total_images": len(matched),
            "processed_images": len(results),
            "elapsed_seconds": round(elapsed, 1),
            "overall_precision": round(overall_precision, 4),
            "overall_recall": round(overall_recall, 4),
            "overall_f1": round(overall_f1, 4),
            "total_gt_chars": total_metrics["total_gt_chars"],
            "total_pred_chars": total_metrics["total_pred_chars"],
            "total_exact_matches": total_metrics["exact_matches"],
        },
        "per_image": results,
    }

    # 输出报告
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("评估结果")
    print("=" * 60)
    print(f"处理图片数: {len(results)}/{len(matched)}")
    print(f"总耗时: {elapsed:.1f}s ({elapsed / max(len(results), 1):.1f}s/张)")
    print(f"总 GT 字符: {total_metrics['total_gt_chars']}")
    print(f"总识别字符: {total_metrics['total_pred_chars']}")
    print(f"精确匹配字符: {total_metrics['exact_matches']}")
    print(f"字符级 Precision: {overall_precision:.2%}")
    print(f"字符级 Recall:    {overall_recall:.2%}")
    print(f"字符级 F1:        {overall_f1:.2%}")
    print(f"\n报告已保存: {output_path}")

    db.close()


if __name__ == "__main__":
    main()
