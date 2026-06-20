#!/usr/bin/env python3
"""Analyze OCR error patterns to understand root causes."""
import os, sys, json, re, difflib

os.chdir('/Users/byzantium/github/Ancient-Documents-to-Knowledge-Graph-Backend')
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv(override=True)
from database import SessionLocal, Image, OcrResult, OcrStatus

with open("docs/ground_truth.json", "r", encoding="utf-8") as f:
    gt = json.load(f)

db = SessionLocal()
ocr_results = db.query(OcrResult).filter(OcrResult.status == OcrStatus.DONE).all()
for r in ocr_results:
    r._image = db.query(Image).filter(Image.id == r.image_id).first()

def normalize(t):
    return re.sub(r'[\s□■]', '', t)

def match_files():
    matched = []
    for gt_num, gt_data in gt.items():
        gt_num_part = gt_num.replace("·", ".")
        for r in ocr_results:
            if not r._image:
                continue
            fname = r._image.filename.lower()
            m = re.search(r'(\d+\.\d+)', fname)
            if m and m.group(1) == gt_num_part:
                matched.append((gt_num, gt_data, r))
                break
    return matched

matched = match_files()
print(f"Matched files: {len(matched)}")

# 分类差异模式
error_patterns = {
    "missing_chars": 0,
    "extra_chars": 0,
    "wrong_chars": 0,
    "simplified": 0,
    "number_variant": 0,
    "other": 0,
}

simplified_pairs = {
    '宝':'寶', '号':'號', '钱':'錢', '银':'銀', '卖':'賣', '买':'買',
    '约':'約', '凭':'憑', '粮':'糧', '亩':'畝', '厘':'釐', '丝':'絲',
    '亲':'親', '说':'說', '笔':'筆', '书':'書', '见':'見', '归':'歸',
    '从':'從', '与':'與', '头':'頭', '处':'處', '无':'無', '据':'據',
    '为':'為', '两':'兩', '万':'萬', '团':'團', '东':'東', '苏':'蘇',
    '门':'門', '关':'關', '间':'間', '开':'開', '买':'買', '卖':'賣',
    '学':'學', '尽':'盡', '发':'發', '变':'變', '龙':'龍', '风':'鳳',
    '国':'國', '时':'時', '车':'車', '马':'馬', '鱼':'魚', '鸟':'鳥',
}

number_variants = {
    '九':'玖', '十':'拾', '七':'柒', '八':'捌', '百':'佰', '千':'仟',
    '一':'壹', '二':'貳', '三':'叁', '四':'肆', '五':'伍', '六':'陸',
}

sample = matched[:100]

per_file_stats = []
for gt_num, gt_data, r in sample:
    gt_text = normalize(gt_data["body"])
    ocr_text = normalize(r.raw_text or "")
    
    gt_chars = list(gt_text)
    ocr_chars = list(ocr_text)
    
    file_errors = {"missing": 0, "extra": 0, "wrong": 0, "simplified": 0, "number_variant": 0}
    
    matcher = difflib.SequenceMatcher(None, gt_chars, ocr_chars)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'delete':
            error_patterns["missing_chars"] += (i2 - i1)
            file_errors["missing"] += (i2 - i1)
        elif tag == 'insert':
            error_patterns["extra_chars"] += (j2 - j1)
            file_errors["extra"] += (j2 - j1)
        elif tag == 'replace':
            for gi, oi in zip(range(i1, i2), range(j1, j2)):
                gc, oc = gt_chars[gi], ocr_chars[oi]
                if gc in simplified_pairs and simplified_pairs[gc] == oc:
                    error_patterns["simplified"] += 1
                    file_errors["simplified"] += 1
                elif oc in simplified_pairs and simplified_pairs[oc] == gc:
                    error_patterns["simplified"] += 1
                    file_errors["simplified"] += 1
                elif gc in number_variants and number_variants[gc] == oc:
                    error_patterns["number_variant"] += 1
                    file_errors["number_variant"] += 1
                elif oc in number_variants and number_variants[oc] == gc:
                    error_patterns["number_variant"] += 1
                    file_errors["number_variant"] += 1
                else:
                    error_patterns["wrong_chars"] += 1
                    file_errors["wrong"] += 1
    
    total_file_err = sum(file_errors.values())
    per_file_stats.append((gt_num, total_file_err, file_errors, gt_text[:20], ocr_text[:20]))

db.close()

print("\n=== 差异模式分析 (前100个文档) ===")
total = sum(error_patterns.values())
for pattern, count in sorted(error_patterns.items(), key=lambda x: -x[1]):
    if count > 0:
        print(f"  {pattern:20s}: {count:5d} ({count/total*100:.1f}%)")

print(f"\n总差异字符数: {total}")

# 找出最差的文件
print("\n=== 最差文档 TOP 20 ===")
per_file_stats.sort(key=lambda x: -x[1])
for i, (gt_num, total_err, errs, gt_start, ocr_start) in enumerate(per_file_stats[:20]):
    print(f"\n{i+1}. {gt_num} (总差异: {total_err})")
    print(f"   GT开头: {gt_start}")
    print(f"   OCR开头: {ocr_start}")
    print(f"   缺失={errs['missing']}, 多余={errs['extra']}, 错字={errs['wrong']}, 简繁={errs['simplified']}, 数字={errs['number_variant']}")

# 找出最常见的单字符错误
print("\n=== 最常见的单字符替换错误 TOP 20 ===")
char_errors = {}
for gt_num, gt_data, r in matched[:100]:
    gt_text = normalize(gt_data["body"])
    ocr_text = normalize(r.raw_text or "")
    gt_chars = list(gt_text)
    ocr_chars = list(ocr_text)
    matcher = difflib.SequenceMatcher(None, gt_chars, ocr_chars)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace' and (i2-i1) == 1 and (j2-j1) == 1:
            gc, oc = gt_chars[i1], ocr_chars[j1]
            key = f"{gc}→{oc}"
            char_errors[key] = char_errors.get(key, 0) + 1

for err, count in sorted(char_errors.items(), key=lambda x: -x[1])[:20]:
    print(f"  {err}: {count}次")
