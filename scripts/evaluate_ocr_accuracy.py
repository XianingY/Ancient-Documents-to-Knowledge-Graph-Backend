"""
OCR 准确率对比脚本
基于 ground_truth.json（参考原文）和数据库中的 OCR 结果，自动计算字符级准确率。

用法：
    .venv/bin/python3.12 scripts/evaluate_ocr_accuracy.py
"""
import os, sys, json, re, difflib

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if os.path.dirname(os.path.abspath(__file__)) != os.getcwd()
         else os.getcwd())
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv(override=True)
from database import SessionLocal, Image, OcrResult, OcrStatus


def normalize_text(text: str) -> str:
    """标准化文本用于比较：去除空白和占位符"""
    return re.sub(r'[\s□■]', '', text)


def load_ground_truth() -> dict:
    with open("docs/ground_truth.json", "r", encoding="utf-8") as f:
        return json.load(f)


def match_ocr_to_ground_truth(ocr_results, ground_truth):
    """将 OCR 结果与 ground truth 按编号匹配"""
    matched = []

    for gt_num, gt_data in ground_truth.items():
        gt_title = gt_data["title"]

        best_match = None
        best_score = 0

        for r in ocr_results:
            img = r._image if hasattr(r, '_image') else None
            if not img:
                continue
            fname = img.filename.lower()

            num_match = re.search(r'(\d+\.\d+)', fname)
            if not num_match:
                continue
            img_num_part = num_match.group(1)
            gt_num_part = gt_num.replace("3·", "3.")

            if img_num_part == gt_num_part:
                best_match = r
                best_score = 1.0
                break

            gt_title_num = re.search(r'(\d+)：', gt_num)
            if gt_title_num:
                expected = gt_title_num.group(1)
                if expected in fname:
                    score = 0.8
                    if score > best_score:
                        best_match = r
                        best_score = score

        if best_match:
            matched.append({
                "gt_num": gt_num,
                "gt_title": gt_title,
                "gt_text": gt_data["body"],
                "ocr_text": best_match.raw_text or "",
                "image_id": best_match.image_id,
                "match_score": best_score,
            })

    return matched


def calculate_accuracy(gt_text: str, ocr_text: str) -> dict:
    gt_norm = normalize_text(gt_text)
    ocr_norm = normalize_text(ocr_text)

    if not gt_norm:
        return {"precision": 0, "recall": 0, "f1": 0, "common_chars": 0, "gt_chars": 0, "ocr_chars": 0}

    matcher = difflib.SequenceMatcher(None, gt_norm, ocr_norm)
    common = sum(block.size for block in matcher.get_matching_blocks())

    precision = common / len(ocr_norm) if ocr_norm else 0
    recall = common / len(gt_norm)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "common_chars": common,
        "gt_chars": len(gt_norm),
        "ocr_chars": len(ocr_norm),
    }


def main():
    gt = load_ground_truth()
    db = SessionLocal()

    ocr_results = db.query(OcrResult).filter(
        OcrResult.status == OcrStatus.DONE
    ).all()

    for r in ocr_results:
        img = db.query(Image).filter(Image.id == r.image_id).first()
        r._image = img

    matched = match_ocr_to_ground_truth(ocr_results, gt)

    print(f"Ground truth entries: {len(gt)}")
    print(f"OCR results: {len(ocr_results)}")
    print(f"Matched pairs: {len(matched)}")
    print()

    if not matched:
        print("No matched pairs found. Check filename patterns.")
        db.close()
        return

    total_f1 = 0
    total_precision = 0
    total_recall = 0
    results = []

    for m in matched:
        acc = calculate_accuracy(m["gt_text"], m["ocr_text"])
        m["accuracy"] = acc
        results.append(m)
        total_f1 += acc["f1"]
        total_precision += acc["precision"]
        total_recall += acc["recall"]

    n = len(results)
    avg_f1 = total_f1 / n
    avg_precision = total_precision / n
    avg_recall = total_recall / n

    print("=" * 70)
    print("  OCR 准确率评估报告")
    print("=" * 70)
    print()
    print(f"  对比文档数:     {n}")
    print(f"  平均 Precision:  {avg_precision:.1%}")
    print(f"  平均 Recall:     {avg_recall:.1%}")
    print(f"  平均 F1:         {avg_f1:.1%}")
    print()

    results.sort(key=lambda x: x["accuracy"]["f1"], reverse=True)

    print("--- 最佳识别 (Top 10) ---")
    for m in results[:10]:
        acc = m["accuracy"]
        print(f"  {m['gt_num']:12s}  F1={acc['f1']:.1%}  P={acc['precision']:.1%}  R={acc['recall']:.1%}  {m['gt_title'][:20]}")

    print()
    print("--- 最差识别 (Bottom 10) ---")
    for m in results[-10:]:
        acc = m["accuracy"]
        print(f"  {m['gt_num']:12s}  F1={acc['f1']:.1%}  P={acc['precision']:.1%}  R={acc['recall']:.1%}  {m['gt_title'][:20]}")

    print()

    buckets = {"90%+": 0, "70-89%": 0, "50-69%": 0, "30-49%": 0, "<30%": 0}
    for m in results:
        f1 = m["accuracy"]["f1"]
        if f1 >= 0.9:
            buckets["90%+"] += 1
        elif f1 >= 0.7:
            buckets["70-89%"] += 1
        elif f1 >= 0.5:
            buckets["50-69%"] += 1
        elif f1 >= 0.3:
            buckets["30-49%"] += 1
        else:
            buckets["<30%"] += 1

    print("--- F1 分布 ---")
    for bucket, count in buckets.items():
        bar = "█" * (count * 40 // max(n, 1))
        print(f"  {bucket:10s}: {count:4d} ({count/n*100:5.1f}%) {bar}")

    db.close()


if __name__ == "__main__":
    main()
