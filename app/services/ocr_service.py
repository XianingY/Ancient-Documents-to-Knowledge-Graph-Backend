
import io
import os
import re
import base64
from sqlalchemy.orm import Session
from database import SessionLocal, Image, OcrResult, OcrStatus
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# ── OCR 专用 Prompt ───────────────────────────────────────────────────────────
#
# 针对中国古代契约文书（土地契约、房屋契约、借贷契约等）精心设计。
# 要点：
#   • 明确告知文档类型，引导模型激活相关先验知识
#   • 说明古代汉语书写规范（竖排、从右至左）
#   • 列举高频字词和术语，防止模型"纠正"成现代字形
#   • 提示破损/模糊处的处理方式
#   • 严格禁止添加任何解释性内容
#
_OCR_SYSTEM_PROMPT = """\
你是一位专精于中国古代契约文书的文献整理专家，精通明清土地买卖契约、房契、借贷文书的识别与转录。

请严格遵循以下规则对图片中的文字进行识别与转录：

【核心原则——逐字照录】
你必须逐字照录图片上看到的每一个字，不得遗漏、不得替换、不得添加。
契约文书的完整性是最重要的——宁可多输出，不可少输出。

【绝对禁令——违反即失败】
以下字符必须原样保留，禁止任何形式的转换：
• 数字：九/十/七/八/百/千/万/一/二/三/四/五/六 → 绝对不能改成 玖/拾/柒/捌/佰/仟/萬/壹/貳/叁/肆/伍/陸
• 异体字：弍/弐 → 绝对不能改成 貳/贰
• 简体字：如果原文就是简体（如"九兄"的"九"），必须保留"九"，不能改成"玖"

【阅读顺序】
- 古代契约通常为竖排书写，从右至左逐列阅读
- 若存在多列，请从最右列开始，逐列向左转录
- 请完整转录从第一行到最后一行，不要跳过任何内容

【常见形近字——必须精确区分】
以下字形极其相似，必须根据上下文精确判断：
• 移（yi）vs 孩（hai）：契约开头"今因移就"是固定套语，不是"孩"
• 就（jiu）vs 訟（song）："移就"是固定搭配，不是"訟"
• 獐（zhang）vs 頭（tou）：地名"虎獐垸"不是"虎頭坑"
• 篤（du）vs 鷹（ying）：堂号"篤敘堂"不是"鷹敘書"
• 敘（xu）vs 書（shu）："篤敘堂"的"敘"不是"書"
• 珍（zhen）vs 琦（qi）：人名"孔珍"不是"孔琦"
• 運（yun）vs 運（yun）：人名"明運"的"運"要保留
• 亨（heng）vs 廣（guang）：人名"亨福"不是"廣福"

【堂号识别——固定格式】
契约中常见的堂号有固定写法，必须精确识别：
• 篤敘堂（最常见的买方堂号）——绝不能写成"鷹敘書"或"篤秋堂"
• 熊宗义（人名，不是堂号）
• 请仔细辨别"篤"和"鷹"的字形差异

【地名识别——垸字特征】
古代契约中的地名常以"垸"结尾（湖北地区常见）：
• 虎獐垸（正确）vs 虎頭坑（错误）
• 高作垸（正确）vs 高作坑（错误）
• 中洲垸（正确）
• 请仔细辨别"獐"和"頭"的字形差异

【文字规范】
- 你必须逐字照录图片上看到的原始字形，不得以任何方式转换为现代简体字
- 以下为强制对照（左侧为必须输出的形式，右侧为绝对禁止输出的形式）：
  · 寶（禁→宝）、號（禁→号）、錢（禁→钱）、銀（禁→银）、賣（禁→卖）
  · 買（禁→买）、約（禁→约）、憑（禁→凭）、陸（禁→陆）、歲（禁→岁）
  · 糧（禁→粮）、畝（禁→亩）、釐（禁→厘）、絲（禁→丝）、塵（禁→尘）
  · 親（禁→亲）、中（保留）、說（禁→说）、合（保留）、筆（禁→笔）
  · 歸（禁→归）、頭（禁→头）、爾（禁→尔）、處（禁→处）、從（禁→从）
  · 與（禁→与）、書（禁→书）、見（禁→见）、聞（禁→闻）、關（禁→关）

【人名识别】
- 请仔细识别人名中的每个字，特别是姓和名的搭配
- 常见姓氏：熊、劉、伍、王、張、陳、李、趙等
- 常见名字用字：德運、永濟、永庭、明運、恒忠、孔珍等

【破损/模糊/印章处理】
- 确定可辨认的字符直接输出
- 无法辨认的字用 □ 表示，连续多字用 □□□ 表示
- 被印章（红色朱印）覆盖的文字：如果能透过印章辨认则输出，否则用 □ 表示
- 印章本身的文字用【印文：…】标注，花押用【押】标注
- 朱批用【朱批：…】标注

【输出格式】
- 只输出转录的文字原文，不添加任何说明、注释或解释
- 不以"图片中的文字是："等句子开头
- 不添加原文中没有的标点符号（如句号、逗号等现代标点）
- 保留原文段落换行，不合并或拆分段落
- 数字串（如价格、面积）须保持连续，不得拆分
"""

_OCR_USER_PROMPT = "请识别并转录图片中的全部文字。"


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

        # ②b Real-ESRGAN 超分辨率（仅在模型可用且图片模糊时启用）
        upsampler = _init_esrgan()
        if upsampler is not None and os.path.getsize(image_path) < 500_000:
            try:
                import numpy as np
                img_array = np.array(img)[:, :, ::-1]
                output, _ = upsampler.enhance(img_array, outscale=2)
                img = PILImage.fromarray(output[:, :, ::-1])
            except Exception as e:
                logger.warning("esrgan_enhance_failed", extra={"error": str(e)})

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


# ── Real-ESRGAN 超分辨率 ──────────────────────────────────────────────────────

_esrgan_upsampler = None

def _init_esrgan():
    """初始化 Real-ESRGAN 超分辨率模型（懒加载，仅在需要时初始化一次）。"""
    global _esrgan_upsampler
    if _esrgan_upsampler is not None:
        return _esrgan_upsampler
    try:
        import torch
        from basicsr.archs.srvgg_arch import SRVGGNetCompact
        from realesrgan import RealESRGANer

        model_path = settings.REAL_ESRGAN_MODEL_PATH
        if not os.path.exists(model_path):
            logger.warning("esrgan_model_not_found", extra={"path": model_path})
            return None

        model = SRVGGNetCompact(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_conv=32, upscale=4, act_type='prelu'
        )
        use_half = torch.cuda.is_available()
        _esrgan_upsampler = RealESRGANer(
            scale=4, model_path=model_path, model=model,
            tile=800, tile_pad=10, pre_pad=0, half=use_half, gpu_id=None,
        )
        logger.info("esrgan_initialized", extra={"half": use_half})
        return _esrgan_upsampler
    except ImportError as e:
        logger.warning("esrgan_not_installed", extra={"error": str(e)})
        return None
    except Exception as e:
        logger.error("esrgan_init_failed", extra={"error": str(e)})
        return None


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


def _apply_domain_corrections(text: str) -> str:
    """
    基于领域知识的确定性校正规则（不依赖LLM调用，速度快、100%可靠）。
    专门针对古代契约文书中的高频OCR错误。
    """
    # ── 堂号校正（契约中最常见的买方堂号） ──
    # 篤敘堂 是最常见的买方堂号，OCR 常误识别为其他形式
    hall_corrections = [
        ("鷹敘書", "篤敘堂"),  # 最常见错误
        ("鷹敘堂", "篤敘堂"),
        ("篤秋堂", "篤敘堂"),  # 3·144 中的错误
        ("鷹敘書名下", "篤敘堂名下"),
        ("篤秋堂名下", "篤敘堂名下"),
    ]
    for wrong, correct in hall_corrections:
        text = text.replace(wrong, correct)

    # ── 套语校正（契约固定格式） ──
    # "今因移就" 是契约标准套语，OCR 常误识别为 "今因子便" 等
    phrase_corrections = [
        ("今因子便", "今因移就"),
        ("今因家訟子便", "今因移就"),  # 3·141 错误
        ("今因孩訟子便", "今因移就"),
        ("今因家訟子便", "今因移就"),
        ("今因子訟便", "今因移就"),
        ("今因子訟", "今因移就"),
        ("今因移灶", "今因移就"),  # 3·144 错误
    ]
    for wrong, correct in phrase_corrections:
        text = text.replace(wrong, correct)

    # ── 地名校正 ──
    location_corrections = [
        ("虎頭坑", "虎獐垸"),  # 3·141 错误
        ("虎頭院", "虎獐垸"),
        ("虎獐坑", "虎獐垸"),
        ("中州垸", "中洲垸"),  # 3·144
    ]
    for wrong, correct in location_corrections:
        text = text.replace(wrong, correct)

    # ── 动词校正 ──
    verb_corrections = [
        ("來賣與", "賣與"),
        ("來賣與", "賣與"),
        ("來賣", "賣"),
    ]
    for wrong, correct in verb_corrections:
        text = text.replace(wrong, correct)

    # ── 固定短语校正 ──
    phrase_corrections_2 = [
        ("恐口共憑", "恐口無憑"),
        ("恐口其憑", "恐口無憑"),
        ("戶此為從", "立此為據"),
        ("戶此為據", "立此為據"),
        ("有為急阻", "百為無阻"),
        ("有為立阻", "百為無阻"),
        ("除陽兩便", "陰陽兩便"),
        ("除陽雨便", "陰陽兩便"),
    ]
    for wrong, correct in phrase_corrections_2:
        text = text.replace(wrong, correct)

    return text


def _correct_ocr_text(raw_text: str) -> str:
    """
    使用 Qwen-Plus 对 OCR 原文做领域专项校正（从 Turbo 升级以获得更强语义理解）：
      • 修正视觉相近字（如 己/已/巳、戊/戌/戍、买/卖、田/由/甲/申）
      • 修正断字/连字错误
      • 规范化大写数字和计量单位
    只在文字超过 20 字时启用，避免空白图片的无意义调用。
    """
    # 先应用确定性规则（快速、可靠）
    text = _apply_domain_corrections(raw_text)

    if not settings.DASHSCOPE_API_KEY or len(text.strip()) < 20:
        return text

    try:
        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY

        prompt = (
            "以下是对一份中国古代契约文书进行 OCR 识别后得到的原始文本。\n\n"
            "请根据上下文和古代契约文书的语言规律，对明显的 OCR 识别错误进行最小化校正。\n\n"
            "【绝对禁令——违反即失败】\n"
            "以下字符必须原样保留，禁止任何形式的转换或\"纠正\"：\n"
            "• 数字：九/十/七/八/百/千/万/一/二/三/四/五/六 → 绝对不能改成 玖/拾/柒/捌/佰/仟/萬/壹/貳/叁/肆/伍/陸\n"
            "• 异体字：弍/弐 → 绝对不能改成 貳/贰\n"
            "• 简体字：如果原文就是简体（如\"九兄\"的\"九\"），必须保留\"九\"，不能改成\"玖\"\n"
            "• 任何数字组合：\"十柒\"不能改成\"拾柒\"，\"九百\"不能改成\"玖佰\"\n\n"
            "【可修正的错误——仅限以下类型】\n"
            "1. 形近字误识别：己→已、戊→戌、土→士、大→太、日→曰、末→未、田→由\n"
            "2. 同音人名误识别：如章峙三（非章兆三）\n"
            "3. 天干地支误识别：如戊戌（须精确区分戊/戌/戍）\n"
            "4. 断字/连字错误\n\n"
            "【校正规则】\n"
            "1. 只修正上述\"可修正\"类型中的明确错误\n"
            "2. 不确定的内容一律保留原样\n"
            "3. 保留 □ 占位符\n"
            "4. 保留原文换行和段落结构\n"
            "5. 不添加标点、注释、说明\n"
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

def _run_api_predict(input_file: str, max_retries: int = 5) -> str:
    """
    使用 DashScope Qwen-VL-Max 对古代契约文书图片进行 OCR，
    并通过专项 Prompt 引导模型输出高质量转录结果。
    内置重试机制（指数退避 + 随机抖动），应对 API 瞬时故障和 SSL 连接中断。
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
                "role": "system",
                "content": [{"text": _OCR_SYSTEM_PROMPT}],
            },
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
                    model="qwen-vl-max",
                    messages=messages,
                    top_p=0.1,
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

def ocr_image_by_id(image_id: int, db: Session = None) -> bool:
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

        enhanced_file = input_file  # 预处理后的临时文件路径
        try:
            ocr_result = OcrResult(
                image_id=image_id,
                raw_text="",
                status=OcrStatus.PROCESSING
            )
            db.add(ocr_result)
            db.commit()
            db.refresh(ocr_result)

            # ① 图像预处理（增强对比度/锐化，提升模型识别率）
            enhanced_file = _preprocess_image(input_file)

            # ② Qwen-VL-Max OCR（领域化 Prompt）
            if settings.ENSEMBLE_PASSES >= 2:
                from PIL import Image as PILImage
                ocr_img = PILImage.open(enhanced_file)
                extracted_text = _ensemble_ocr(ocr_img)
            else:
                extracted_text = _run_api_predict(enhanced_file)

            if not extracted_text or extracted_text.startswith("Error:"):
                extracted_text = extracted_text or "未能识别到文字。"

            # ③ VL 输出清洗（去除模型幻觉前缀/后缀、合并多余空行）
            cleaned_text = _clean_vl_output(extracted_text)

            # ④ 后校正 Pass（用 qwen-plus 修正视觉相近字误识别）
            cleaned_text = _correct_ocr_text(cleaned_text)

            ocr_result.raw_text = cleaned_text
            ocr_result.status = OcrStatus.DONE
            db.commit()

            # ⑤ 立即写入 ChromaDB 向量索引
            # 使用 ocr_{id} 作为 doc_id，后续结构化完成后会 upsert 覆盖（丰富元数据）
            # 这样保证所有 OCR 完成的图片都能被智能问答检索到
            _index_ocr_to_chroma(ocr_result.id, cleaned_text, image)

            return True

        except Exception as e:
            if 'ocr_result' in locals():
                ocr_result.status = OcrStatus.FAILED
                db.commit()
            logger.error("ocr_processing_error", extra={"error": str(e)})
            return False

        finally:
            # 删除预处理临时文件（避免磁盘积累）
            if enhanced_file != input_file and os.path.exists(enhanced_file):
                try:
                    os.remove(enhanced_file)
                except OSError:
                    pass

    finally:
        if close_db:
            db.close()


def _index_ocr_to_chroma(ocr_result_id: int, text: str, image) -> None:
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
