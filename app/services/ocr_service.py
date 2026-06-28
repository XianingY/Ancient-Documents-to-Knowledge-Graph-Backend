
import io
import os
import re
import base64
import json
from datetime import timedelta
from sqlalchemy.orm import Session
from database import SessionLocal, Image, OcrResult, OcrStatus, get_beijing_time
from app.core.config import settings
from app.core.logger import get_logger
from app.services.ocr import OcrBackendUnavailable, OcrPipelineResult, run_paddle_consensus

logger = get_logger(__name__)

# ── OCR 专用 Prompt ───────────────────────────────────────────────────────────
#
# 保守识别优先：只转录图片上能看见的字，避免把契约套话、地名、人名、
# 价格等按领域先验补全成“看起来合理”的文本。
_OCR_SYSTEM_PROMPT = """\
请逐字转录图片中的可见文字。
只写你能从图像中看见的字，不要根据契约格式、常见套语、地名、人名或价格进行推断、补全或修正。
模糊、破损、被遮挡、无法确认的字用 □ 表示；连续无法确认的字可写作 □□□。
保留原文的大致换行。不要添加标点、解释、标题、注释或“图片中的文字是”等前后缀。
如果只能辨认少量文字，也只输出这些可辨认文字和必要的 □，不要编造完整契约。
"""

_OCR_USER_PROMPT = _OCR_SYSTEM_PROMPT


# ── 图像预处理 ────────────────────────────────────────────────────────────────

def _preprocess_image(image_path: str) -> str:
    """
    对原始图片进行自适应增强处理，提升古代契约文书 OCR 质量。
    关键改进（相比固定参数方案）：
      1. EXIF 旋转修正（手机拍照方向适配）
      2. 自适应对比度拉伸（autocontrast，按实际直方图调整而非固定倍数）
      3. 高斯模糊 + UnsharpMask（比 MedianFilter + 固定锐化更好地保留笔画细节）
      4. PNG 无损输出（避免 JPEG 压缩伪影干扰模型识别）
    返回处理后图片的临时路径（调用方负责删除）。
    """
    try:
        from PIL import Image as PILImage, ImageFilter, ImageOps
    except ImportError:
        return image_path

    try:
        img = PILImage.open(image_path)

        # ① EXIF 自动旋转（手机竖拍、横拍时元数据中的方向标记）
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        img = img.convert("RGB")

        # ② 长边限制 3000px — resize BEFORE numpy to reduce memory (~137MB → ~15MB)
        max_side = 3000
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)

        # ①b 红色印章抑制（古代契约常见朱印覆盖文字）
        import numpy as np
        img_array = np.array(img, dtype=np.float32)
        r, g, b = img_array[:, :, 0], img_array[:, :, 1], img_array[:, :, 2]
        red_mask = (r > 150) & (r > g * 1.3) & (r > b * 1.3)
        img_array[:, :, 0] = np.where(red_mask, np.minimum(r, g * 1.1), r)
        img = PILImage.fromarray(img_array.astype(np.uint8))

        # ③ 转灰度
        gray = img.convert("L")

        # ④ 自适应对比度拉伸（去除极端像素后拉伸）
        gray = ImageOps.autocontrast(gray, cutoff=0.5)

        # ⑤ 轻微高斯模糊去噪（radius=0.5，保留笔画细节的同时去除纸张纹理噪声）
        gray = gray.filter(ImageFilter.GaussianBlur(radius=0.5))

        # ⑥ 自适应锐化（UnsharpMask 只增强边缘区域，不影响平滑背景区域）
        gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # ⑧ 转回 RGB（API 要求三通道输入）
        img = gray.convert("RGB")

        # ⑧ 保存为 PNG 无损格式（避免 JPEG 压缩伪影干扰模型识别）
        base, _ = os.path.splitext(image_path)
        tmp_path = f"{base}_ocr_enhanced.png"
        img.save(tmp_path, "PNG")
        return tmp_path

    except Exception as e:
        logger.warning("image_preprocess_failed", extra={"error": str(e)})
        return image_path


# ── VL 输出清洗 ───────────────────────────────────────────────────────────────

def _clean_vl_output(text: str) -> str:
    """
    清理 VL 模型输出中常见的幻觉与格式问题：
      • 去除模型擅自添加的解释性前缀/后缀
      • 合并多余空行
    在后校正之前执行，确保校正模型拿到干净文本。
    """
    if not text:
        return text

    prefix_patterns = [
        r'^图片中的文字[是为内容如下：:\s]*',
        r'^以下是[图片中的]*文字[内容：:\s]*',
        r'^识别结果[如下为：:\s]*',
        r'^转录[结果内容如下为：:\s]*',
        r'^文字内容[如下为：:\s]*',
        r'^原文[内容如下为：:\s]*',
    ]
    for pattern in prefix_patterns:
        text = re.sub(pattern, '', text, count=1)

    suffix_patterns = [
        r'\n注[：:].*$',
        r'\n说明[：:].*$',
        r'\n备注[：:].*$',
        r'\n以上[是为].*转录.*$',
        r'\n以上[是为].*识别.*$',
    ]
    for pattern in suffix_patterns:
        text = re.sub(pattern, '', text, flags=re.DOTALL)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── OCR 后校正 Pass ───────────────────────────────────────────────────────────

# 简体→繁体映射（仅限契约文书中的通用文字，不含数字和异体字）
# 注意：不转换数字（九/十/七/八/百/千/万等），因为图片中可能写的就是简体形式
# 如"九兄"就是"九兄"，不强制转为"玖兄"；"弍拾柒"保留原写法不改为"貳拾柒"
_TRADITIONAL_MAP = {
    "宝": "寶", "号": "號", "钱": "錢", "银": "銀", "卖": "賣",
    "买": "買", "约": "約", "凭": "憑", "陆": "陸", "岁": "歲",
    "粮": "糧", "亩": "畝", "厘": "釐",
    "丝": "絲", "亲": "親", "说": "說", "笔": "筆", "书": "書",
    "见": "見", "归": "歸", "从": "從", "与": "與", "头": "頭",
    "处": "處", "尔": "爾", "闻": "聞", "关": "關", "两": "兩",
    "麦": "麥", "马": "馬", "鱼": "魚", "鸟": "鳥", "龙": "龍",
    "华": "華", "门": "門", "风": "風", "凤": "鳳", "尽": "盡",
    "层": "層", "学": "學", "对": "對", "导": "導", "实": "實",
    "开": "開", "间": "間", "业": "業", "义": "義", "东": "東",
    "团": "團", "湾": "灣", "岭": "嶺",
    "坝": "壩", "沟": "溝",
    "为": "為", "无": "無", "据": "據",
}


def _ensure_traditional_chinese(text: str) -> str:
    """
    最终保底：逐字检测并还原简体→繁体。
    只处理明确的简体字（宝/号/钱/银等），不触碰数字和异体字。
    """
    result = []
    for ch in text:
        result.append(_TRADITIONAL_MAP.get(ch, ch))
    return "".join(result)


# ── V3+V4 融合 OCR ────────────────────────────────────────────────────────────

_COMMON_CONTRACT_CHARS = set(
    "立永賣卖買买田地白水契約约人今因移就不便將将本己自祖私置受分"
    "形畝亩分厘釐毫毛絲丝載载糧粮銀银錢钱文串整乙壹貳叁參肆伍陸"
    "柒捌玖拾佰仟零〇請请憑凭親亲中說说合出筆笔與与名下為为業业"
    "三面言定備备時时值價价係系仝同手領领訖讫之後后任從从主"
    "管收撥拨佃耕種种陰阴陽阳兩两百無无阻並并準准折抬情弊"
    "相干恐口有此據据止界東东西南北道光年月日"
)


def _is_common_contract_char(ch: str) -> bool:
    """Return True for generic contract characters; excludes names/places as facts."""
    return ch in _COMMON_CONTRACT_CHARS or ch in {"□", "囗", "■"}


def _clean_ocr_candidate(text: str) -> str:
    """Normalize failed API responses into empty candidates before fusion."""
    if not text or text.startswith("Error:"):
        return ""
    return _clean_vl_output(text)


def _next_non_gap(chars, start: int) -> str:
    for ch in chars[start:]:
        if ch != "_":
            return ch
    return ""


def _can_insert_v3_segment(segment: list[str], left_anchor: str, right_anchor: str) -> bool:
    """
    Conservative V3 supplement rule.

    V3 may fill only very short gaps surrounded by V4 context and made of
    generic contract characters. Names, places and long phrase insertions stay out.
    """
    if not segment or len(segment) > 3:
        return False
    if not left_anchor or not right_anchor:
        return False
    return all(_is_common_contract_char(ch) for ch in segment)


def _agreement_only_text(v3_text: str, v4_text: str) -> str:
    """Keep only aligned model agreements; collapse uncertain spans to □."""
    v3_text = _clean_ocr_candidate(v3_text)
    v4_text = _clean_ocr_candidate(v4_text)
    if not v3_text or not v4_text:
        return v4_text

    from sequence_align.pairwise import needleman_wunsch

    aligned_v3, aligned_v4 = needleman_wunsch(
        list("".join(v3_text.split())),
        list("".join(v4_text.split())),
        "_",
        match_score=2.0,
        mismatch_score=-1.0,
        indel_score=-1.0,
    )
    result = []
    uncertain = False
    for a, b in zip(aligned_v3, aligned_v4):
        if a == b and a != "_":
            if uncertain:
                result.append("□")
                uncertain = False
            result.append(a)
        else:
            uncertain = True
    if uncertain:
        result.append("□")
    return "".join(result)


def _fuse_v3_v4(v3_text: str, v4_text: str) -> tuple:
    """
    Conservative fusion using v4 as the trusted baseline.
    
    Strategy:
    - v4-only text is kept as-is after cleanup
    - mismatches prefer v4
    - v3-only content is kept only for tiny, anchored, generic gaps
    - v3 API errors or empty v4 do not let v3 become the final text
    
    Returns: (fused_text, confidence_score)
    """
    v3_text = _clean_ocr_candidate(v3_text)
    v4_text = _clean_ocr_candidate(v4_text)

    if not v3_text and not v4_text:
        return "", 0.0
    if not v4_text:
        return "", 0.0
    if not v3_text:
        return v4_text, 1.0
    
    from sequence_align.pairwise import needleman_wunsch
    
    v3_clean = "".join(v3_text.split())
    v4_clean = "".join(v4_text.split())
    
    aligned_v3, aligned_v4 = needleman_wunsch(
        list(v3_clean), list(v4_clean), "_",
        match_score=2.0, mismatch_score=-1.0, indel_score=-1.0,
    )
    
    fused_chars = []
    agree_count = 0
    v4_only_count = 0
    v3_only_validated = 0
    v3_only_dropped = 0
    mismatch_count = 0
    
    i = 0
    while i < len(aligned_v3):
        a = aligned_v3[i]
        b = aligned_v4[i]
        
        if a == "_" and b == "_":
            i += 1
            continue
        elif a == "_":
            fused_chars.append(b)
            v4_only_count += 1
        elif b == "_":
            segment = []
            start = i
            while i < len(aligned_v3) and aligned_v4[i] == "_" and aligned_v3[i] != "_":
                segment.append(aligned_v3[i])
                i += 1
            left_anchor = fused_chars[-1] if fused_chars else ""
            right_anchor = _next_non_gap(aligned_v4, i)
            if _can_insert_v3_segment(segment, left_anchor, right_anchor):
                fused_chars.extend(segment)
                v3_only_validated += len(segment)
            else:
                v3_only_dropped += len(segment)
            continue
        else:
            fused_chars.append(b)
            if a == b:
                agree_count += 1
            else:
                mismatch_count += 1
        
        i += 1
    
    compared_total = agree_count + mismatch_count + v4_only_count + v3_only_validated
    confidence = agree_count / compared_total if compared_total > 0 else 0.0
    if v3_only_dropped or mismatch_count:
        logger.info("ocr_conservative_fusion", extra={
            "agree": agree_count,
            "v4_only": v4_only_count,
            "v3_inserted": v3_only_validated,
            "v3_dropped": v3_only_dropped,
            "mismatches_prefer_v4": mismatch_count,
            "confidence": confidence,
        })
    
    return "".join(fused_chars), confidence


def _length_ratio_gate(text: str) -> tuple[bool, str | None]:
    visible_len = len(re.sub(r"\s", "", text or ""))
    if visible_len == 0:
        return True, None
    max_len = settings.EXPECTED_TEXT_MAX
    if visible_len > max_len * 1.4:
        return False, f"hard_reject:too_long:{visible_len}>{int(max_len * 1.4)}"
    return True, None


_TEMPLATE_PHRASES = [
    "今因移就", "三面言定", "親手領訖", "任從買主", "陰陽兩便",
    "百為無阻", "恐口無憑", "立此為據", "永遠為業",
]


def _template_phrase_density(text: str) -> float:
    clean = re.sub(r"\s", "", text or "")
    if not clean:
        return 0.0
    hits = sum(1 for phrase in _TEMPLATE_PHRASES if phrase in clean)
    return hits / max(len(clean) / 120, 1)


def _v3_only_contribution_ratio(fused_text: str, v4_text: str) -> float:
    fused_clean = re.sub(r"\s", "", fused_text or "")
    v4_clean = re.sub(r"\s", "", _clean_ocr_candidate(v4_text) or "")
    if not fused_clean:
        return 0.0
    extra = max(len(fused_clean) - len(v4_clean), 0)
    return extra / len(fused_clean)


def _filter_hallucinations(
    fused_text: str,
    v3_text: str,
    v4_text: str,
    confidence: float,
) -> tuple[str, float, list[str]]:
    """Apply deterministic post-fusion hallucination filters."""
    if not settings.HALLUCINATION_FILTER_ENABLED:
        return fused_text, confidence, []

    reasons: list[str] = []
    passed, reason = _length_ratio_gate(fused_text)
    if not passed and reason:
        reasons.append(reason)

    v3_ratio = _v3_only_contribution_ratio(fused_text, v4_text)
    if v3_ratio > 0.12:
        reasons.append(f"hard_reject:v3_only_ratio:{v3_ratio:.2f}>0.12")

    density = _template_phrase_density(fused_text)
    if density >= 5 and confidence < 0.75:
        reasons.append(f"hard_reject:template_density:{density:.2f}")

    fallback = _clean_ocr_candidate(v4_text)
    if not reasons and v3_text and v4_text and confidence < 0.45:
        reasons.append(f"hard_reject:low_model_agreement:{confidence:.2f}<0.45")
        fallback = _agreement_only_text(v3_text, v4_text)

    if any(r.startswith("hard_reject") for r in reasons):
        return fallback, confidence, reasons

    return fused_text, confidence, reasons


def _run_api_predict_v4(input_file: str, max_retries: int = 5) -> str:
    """v4 model (qwen-vl-ocr-latest) - high precision, low recall."""
    return _run_api_predict(input_file, model="qwen-vl-ocr-latest", max_retries=max_retries)


def _correct_ocr_text(raw_text: str) -> str:
    """
    保守后处理：默认只做明确的简繁字形归一化。

    LLM 后校正会按契约上下文补全缺字，容易把 OCR 阶段的 raw text
    变成“合理文本”而非“可见文本”，因此默认关闭。
    """
    text = _ensure_traditional_chinese(raw_text or "")

    if (
        not settings.OCR_LLM_POST_CORRECTION_ENABLED
        or not settings.DASHSCOPE_API_KEY
        or len(text.strip()) < 20
    ):
        return text

    try:
        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY

        prompt = (
            "以下是对一份中国古代契约文书进行 OCR 识别后得到的原始文本。\n\n"
            "请只修正确定的简繁字形问题，不要根据上下文补全地名、人名、价格或契约套语。\n\n"
            "【绝对禁令——违反即失败】\n"
            "以下字符必须原样保留，禁止任何形式的转换或\"纠正\"：\n"
            "• 数字：九/十/七/八/百/千/万/一/二/三/四/五/六 → 绝对不能改成 玖/拾/柒/捌/佰/仟/萬/壹/貳/叁/肆/伍/陸\n"
            "• 异体字：弍/弐 → 绝对不能改成 貳/贰\n"
            "• 简体字：如果原文就是简体（如\"九兄\"的\"九\"），必须保留\"九\"，不能改成\"玖\"\n"
            "• 任何数字组合：\"十柒\"不能改成\"拾柒\"，\"九百\"不能改成\"玖佰\"\n\n"
            "【校正规则】\n"
            "1. 只修正简繁字形中的明确错误\n"
            "2. 不确定的内容一律保留原样\n"
            "3. 保留 □ 占位符\n"
            "4. 保留原文换行和段落结构\n"
            "5. 不添加标点、注释、说明，不补全文本\n"
            "6. 直接输出校正后的纯文本\n\n"
            f"OCR 原始文本：\n{text}"
        )

        response = dashscope.Generation.call(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            result_format="message",
            max_tokens=4096,
            temperature=0.1,
            top_p=0.3,
        )

        if response.status_code == 200:
            try:
                corrected = response.output.choices[0].message.content
            except (AttributeError, IndexError, TypeError):
                corrected = response.output["choices"][0]["message"]["content"]

            corrected = re.sub(
                r'^(校正后[的文本内容：:\s]*|修正后[的文本内容：:\s]*|'
                r'以下是校正后[的文本：:\s]*|校正[结果如下：:\s]*)',
                '', corrected
            ).strip()

            # 简体→繁体还原：只处理明确的简体字（宝/号/钱等），不触碰数字和异体字
            corrected = _ensure_traditional_chinese(corrected)

            # 长度校验：校正后文本长度不应偏差太大，防止模型生成无关内容
            if corrected and 0.5 < len(corrected) / max(len(text), 1) < 2.0:
                return corrected
            return text
        return text

    except Exception as e:
        logger.error("ocr_post_correct_failed", extra={"error": str(e)})
        return text


# ── Multi-pass Ensemble OCR ──────────────────────────────────────────────────

def _augment_image(img, num_variants=3):
    """
    生成多份图像变体，通过不同增强策略提高 OCR 集成效果。
    策略包括：膨胀、高斯模糊、高斯噪声、缩放重采样。
    """
    import numpy as np
    from PIL import Image as PILImage
    augmented = []
    rng = np.random.RandomState(42)
    original_array = np.array(img)
    for i in range(num_variants):
        variant = original_array.copy().astype(np.float32)
        strategy = i % 4
        if strategy == 0:
            from PIL import ImageFilter
            dilated = img.filter(ImageFilter.MaxFilter(size=3))
            variant = np.array(dilated).astype(np.float32)
        elif strategy == 1:
            from scipy.ndimage import gaussian_filter
            sigma = 0.8 + rng.random() * 0.4
            variant = gaussian_filter(variant, sigma=[sigma, sigma, 0])
        elif strategy == 2:
            sigma = 2.0 + rng.random() * 3.0
            noise = rng.normal(0, sigma, variant.shape)
            variant = np.clip(variant + noise, 0, 255)
        elif strategy == 3:
            scale = 0.85 + rng.random() * 0.1
            h, w = variant.shape[:2]
            small = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)
            variant = np.array(small.resize((w, h), PILImage.LANCZOS)).astype(np.float32)
        augmented.append(PILImage.fromarray(variant.astype(np.uint8)))
    return augmented


def _nw_consensus(texts):
    """
    使用 Needleman-Wunsch 全局对齐算法，对多份 OCR 文本进行投票共识。
    逐轮合并：取第一份文本为基线，依次与后续文本对齐并合并，
    遇到 gap 时取另一方的字符，非 gap 时取多数投票（首方优先）。
    """
    from sequence_align.pairwise import needleman_wunsch
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]
    consensus = list(texts[0])
    for i in range(1, len(texts)):
        aligned_a, aligned_b = needleman_wunsch(
            consensus, list(texts[i]), "_",
            match_score=2.0, mismatch_score=-1.0, indel_score=-1.0,
        )
        new_consensus = []
        for a, b in zip(aligned_a, aligned_b):
            if a == "_":
                new_consensus.append(b)
            elif b == "_":
                new_consensus.append(a)
            else:
                new_consensus.append(a)
        consensus = new_consensus
    return "".join(c for c in consensus if c != "_")


def _ensemble_ocr(img, num_passes=None):
    """
    多轮 OCR 集成：对图像生成多个增强变体，每个变体独立 OCR，
    最后用 Needleman-Wunsch 共识算法合并结果，提高识别准确率。
    跳过返回 Error 的 API 调用，全部失败时回退到单次 OCR。
    """
    if num_passes is None:
        num_passes = settings.ENSEMBLE_PASSES
    variants = _augment_image(img, num_variants=num_passes)
    import tempfile, os
    results = []
    for i, variant in enumerate(variants):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            variant.save(f.name, "PNG")
            try:
                text = _run_api_predict(f.name)
                if text and not text.startswith("Error:"):
                    results.append(text)
            except Exception as e:
                logger.warning("ensemble_pass_error", extra={"pass": i + 1, "error": str(e)})
            finally:
                try:
                    os.remove(f.name)
                except OSError:
                    pass
    if not results:
        return _run_api_predict("")
    if len(results) == 1:
        return results[0]
    return _nw_consensus(results)


# ── 主识别函数 ────────────────────────────────────────────────────────────────

def _run_api_predict(input_file: str, model: str = "qwen-vl-ocr-latest", max_retries: int = 5) -> str:
    """
    使用 DashScope Qwen-VL 模型对古代契约文书图片进行 OCR。
    
    Args:
        input_file: Image file path
        model: OCR model name; production fallback uses qwen-vl-ocr-latest only
        max_retries: Maximum number of retries
    
    Returns:
        OCR text or error message
    """
    try:
        from dashscope import MultiModalConversation
        import dashscope
        import time

        dashscope.api_key = settings.DASHSCOPE_API_KEY
        if not dashscope.api_key:
            raise ValueError("DASHSCOPE_API_KEY is not set in environment variables.")

        local_file_path = f"file://{os.path.abspath(input_file)}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"image": local_file_path},
                    {"text": _OCR_USER_PROMPT},
                ],
            },
        ]

        last_error = None
        for attempt in range(max_retries):
            try:
                response = MultiModalConversation.call(
                    model=model,
                    messages=messages,
                    temperature=0.01,
                    top_p=0.001,
                    top_k=1,
                )

                if response.status_code == 200:
                    content_list = response.output.choices[0].message.content
                    for item in content_list:
                        if "text" in item:
                            return item["text"]
                    return ""
                else:
                    last_error = f"API Error: {response.code} - {response.message}"
                    logger.warning("dashscope_api_error", extra={"attempt": attempt + 1, "max_retries": max_retries, "code": response.code, "message": response.message})
            except Exception as e:
                last_error = str(e)
                logger.warning("api_ocr_attempt_failed", extra={"attempt": attempt + 1, "max_retries": max_retries, "error": str(e)})

            if attempt < max_retries - 1:
                import random
                backoff = min(2 ** attempt, 16)
                jitter = random.uniform(0, backoff * 0.5)
                time.sleep(backoff + jitter)

        return f"Error: {last_error}"

    except Exception as e:
        logger.error("api_ocr_execution_failed", extra={"error": str(e)})
        return f"Error: {str(e)}"


def _run_qwen_conservative_pipeline(input_file: str) -> OcrPipelineResult:
    """Single-model fallback. Generated auxiliary text never enters the output."""
    enhanced_file = _preprocess_image(input_file)
    try:
        raw_text = _run_api_predict_v4(enhanced_file)
        if not raw_text or raw_text.startswith("Error:"):
            raise RuntimeError(raw_text or "Qwen OCR returned no text")
        text = _correct_ocr_text(_clean_vl_output(raw_text))
        visible = len(re.sub(r"[\s□■]", "", text))
        return OcrPipelineResult(
            text=text,
            confidence=0.0,
            coverage=1.0 if visible else 0.0,
            engine="qwen_conservative",
            model_versions="qwen-vl-ocr-latest",
            segments=[{
                "bbox": None,
                "text": text,
                "status": "fallback",
                "medium_text": "",
                "medium_score": 0.0,
                "small_text": "",
                "small_score": 0.0,
                "similarity": 0.0,
                "rejection_reasons": ["fallback:unverified_qwen_output"],
            }],
            rejection_reasons=["fallback:unverified_qwen_output"],
        )
    finally:
        if enhanced_file != input_file and os.path.exists(enhanced_file):
            try:
                os.remove(enhanced_file)
            except OSError:
                pass


def _run_configured_ocr(input_file: str) -> OcrPipelineResult:
    if settings.OCR_ENGINE == "paddle_v6_consensus":
        try:
            return run_paddle_consensus(input_file)
        except Exception as exc:
            logger.warning(
                "paddle_ocr_fallback",
                extra={"error": str(exc), "fallback": settings.OCR_FALLBACK_ENGINE},
            )
            if settings.OCR_FALLBACK_ENGINE != "qwen_conservative":
                raise
            if not settings.DASHSCOPE_API_KEY:
                raise OcrBackendUnavailable(
                    "PaddleOCR failed and DASHSCOPE_API_KEY is unavailable"
                ) from exc
            return _run_qwen_conservative_pipeline(input_file)
    if settings.OCR_ENGINE == "qwen_conservative":
        return _run_qwen_conservative_pipeline(input_file)
    raise ValueError(f"Unsupported OCR_ENGINE: {settings.OCR_ENGINE}")


def _recent_processing_result(db: Session, image_id: int) -> OcrResult | None:
    active = (
        db.query(OcrResult)
        .filter(OcrResult.image_id == image_id, OcrResult.status == OcrStatus.PROCESSING)
        .order_by(OcrResult.id.desc())
        .first()
    )
    if not active:
        return None
    created_at = active.created_at
    now = get_beijing_time()
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    if now - created_at <= timedelta(minutes=30):
        return active
    active.status = OcrStatus.FAILED
    db.commit()
    return None


def ocr_image_by_id(
    image_id: int,
    db: Session = None,
    raise_errors: bool = False,
) -> bool:
    """
    输入：image_id (Image表中的主键)
    处理：从数据库中查找图片路径，执行OCR（同步版本，供 Celery Worker 调用）
    输出：结果保存在OcrResult表中
    成功返回 True，失败返回 False
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        image = db.query(Image).filter(Image.id == image_id).first()

        if image is None:
            logger.error("image_record_not_found", extra={"image_id": image_id})
            return False

        input_file = str(image.path)

        if not os.path.exists(input_file):
            logger.error("image_file_not_exist", extra={"path": input_file})
            return False

        active = _recent_processing_result(db, image_id)
        if active:
            logger.info(
                "ocr_already_processing",
                extra={"image_id": image_id, "ocr_result_id": active.id},
            )
            return False

        try:
            ocr_result = OcrResult(
                image_id=image_id,
                raw_text="",
                status=OcrStatus.PROCESSING,
                engine=settings.OCR_ENGINE,
            )
            db.add(ocr_result)
            db.commit()
            db.refresh(ocr_result)

            pipeline_result = _run_configured_ocr(input_file)
            cleaned_text = pipeline_result.text.strip() or "□"

            ocr_result.raw_text = cleaned_text
            ocr_result.original_raw_text = cleaned_text
            ocr_result.confidence = pipeline_result.confidence
            ocr_result.coverage = pipeline_result.coverage
            ocr_result.engine = pipeline_result.engine
            ocr_result.model_versions = pipeline_result.model_versions
            ocr_result.segments_json = json.dumps(
                pipeline_result.segments,
                ensure_ascii=False,
            ) if pipeline_result.segments else None
            ocr_result.rejection_reasons = json.dumps(
                pipeline_result.rejection_reasons,
                ensure_ascii=False,
            ) if pipeline_result.rejection_reasons else None
            ocr_result.crop_bbox_json = json.dumps(
                pipeline_result.crop_bbox,
                ensure_ascii=False,
            ) if pipeline_result.crop_bbox else None
            ocr_result.image_size_json = json.dumps(
                pipeline_result.image_size,
                ensure_ascii=False,
            ) if pipeline_result.image_size else None
            ocr_result.status = OcrStatus.DONE
            db.commit()

            _index_ocr_to_chroma(
                ocr_result.id,
                cleaned_text,
                image,
                pipeline_result,
            )

            return True

        except Exception as e:
            if 'ocr_result' in locals():
                ocr_result.status = OcrStatus.FAILED
                db.commit()
            logger.error("ocr_processing_error", extra={"error": str(e)})
            if raise_errors:
                raise
            return False

    finally:
        if close_db:
            db.close()


def _index_ocr_to_chroma(
    ocr_result_id: int,
    text: str,
    image,
    pipeline_result: OcrPipelineResult,
) -> None:
    """
    将 OCR 文本写入 ChromaDB 向量索引（基础版，无结构化元数据）。
    doc_id = image_{image_id}，保证每张图片在向量库中只有一条记录，
    重新 OCR 时 upsert 会自动覆盖旧结果。
    """
    if not image:
        return
    try:
        from app.services.rag_service import _get_text_embeddings_sync
        from app.services.vector_store.chroma import upsert_document
        embedding = _get_text_embeddings_sync(text)
        metadata = {
            "user_id": image.user_id,
            "ocr_result_id": ocr_result_id,
            "image_id": image.id,
            "filename": image.filename or "",
            "ocr_confidence": pipeline_result.confidence,
            "ocr_coverage": pipeline_result.coverage,
            "ocr_engine": pipeline_result.engine,
            "structured_result_id": "",
            "time": "",
            "location": "",
            "seller": "",
            "buyer": "",
            "price": "",
            "subject": "",
        }
        upsert_document(
            doc_id=f"image_{image.id}",
            text=text,
            embedding=embedding,
            metadata=metadata,
        )
        logger.info("ocr_indexed_to_chromadb", extra={"image_id": image.id})
    except Exception as e:
        logger.warning("chromadb_ocr_indexing_failed", extra={"error": str(e)})


async def ocr_image_by_id_async(image_id: int, db: Session = None) -> bool:
    """异步包装器，供 FastAPI 路由直接调用（非Celery场景）"""
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(ocr_image_by_id, image_id, db)
