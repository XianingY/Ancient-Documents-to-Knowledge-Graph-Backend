
import io
import os
import re
import base64
from sqlalchemy.orm import Session
from database import SessionLocal, Image, OcrResult, OcrStatus
from app.core.config import settings

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

【阅读顺序】
- 古代契约通常为竖排书写，从右至左逐列阅读
- 若存在多列，请从最右列开始，逐列向左转录

【文字规范】
- 保留所有繁体字、异体字、俗字，不要替换为现代简体字
- 常见大写数字请原样保留：壹贰叁肆伍陆柒捌玖拾佰仟万
- 计量单位请原样保留：亩、分、厘、毫、文、钱、两、升、斗
- 常见契约专用词请原样保留：立契人、凭中人、代书人、知见人、画押、
  花押、钤印、今将、情愿、出卖、出租、佃种、永远为业、恐口无凭、
  立此契约、永不反悔、以上为据

【破损/模糊处理】
- 确定可辨认的字符直接输出
- 无法辨认的字用 □ 表示，连续多字用 □□□ 表示
- 印章、花押、朱批等非正文内容用【印】【押】【朱批：…】标注

【输出格式】
- 只输出转录的文字原文，不添加任何说明、注释或解释
- 不以"图片中的文字是："等句子开头
- 不添加原文中没有的标点符号（如句号、逗号等现代标点）
- 保留原文段落换行，不合并或拆分段落
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

        # ② 长边限制 3000px（兼顾 API 限制与传输效率）
        max_side = 3000
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)

        # ③ 转灰度（去除纸张泛黄、墨迹褪色等色彩干扰）
        gray = img.convert("L")

        # ④ 自适应对比度拉伸
        # cutoff=0.5 去除最暗/最亮各 0.5% 极端像素后拉伸，自动适应不同图片条件
        gray = ImageOps.autocontrast(gray, cutoff=0.5)

        # ⑤ 轻微高斯模糊去噪（radius=0.5，比 MedianFilter(3) 温和，减少对笔画边缘的损伤）
        gray = gray.filter(ImageFilter.GaussianBlur(radius=0.5))

        # ⑥ 自适应锐化（UnsharpMask 只增强边缘区域，不影响平滑背景区域）
        gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # ⑦ 转回 RGB（API 要求三通道输入）
        img = gray.convert("RGB")

        # ⑧ 保存为 PNG 无损格式（避免 JPEG 压缩伪影干扰模型识别）
        base, _ = os.path.splitext(image_path)
        tmp_path = f"{base}_ocr_enhanced.png"
        img.save(tmp_path, "PNG")
        return tmp_path

    except Exception as e:
        print(f"图像预处理失败（使用原图）: {e}")
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

def _correct_ocr_text(raw_text: str) -> str:
    """
    使用 Qwen-Plus 对 OCR 原文做领域专项校正（从 Turbo 升级以获得更强语义理解）：
      • 修正视觉相近字（如 己/已/巳、戊/戌/戍、买/卖、田/由/甲/申）
      • 修正断字/连字错误
      • 规范化大写数字和计量单位
    只在文字超过 20 字时启用，避免空白图片的无意义调用。
    """
    if not settings.DASHSCOPE_API_KEY or len(raw_text.strip()) < 20:
        return raw_text

    try:
        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY

        prompt = (
            "以下是对一份中国古代契约文书进行 OCR 识别后得到的原始文本。\n\n"
            "请根据上下文和古代契约文书的语言规律，对明显的 OCR 识别错误进行最小化校正。\n\n"
            "【常见 OCR 误识别对照】\n"
            "- 形近字：己/已/巳、戊/戌/戍/戎、土/士/壬、大/太/犬/夫、"
            "日/曰/目、末/未、买/卖、田/由/甲/申、丙/两、干/于/千、"
            "亩/畝、钱/銭、两/兩、契/楔、人/入、壹/壶、冬/终\n"
            "- 数字混淆：壹/壶、贰/贰/弐、叁/参、伍/伍\n"
            "- 断字/连字：一个字被误识为两个部件，或两个字被合为一个\n\n"
            "【校正规则】\n"
            "1. 只修正明显的识别错误，不改动语义上可能正确的内容\n"
            "2. 保留所有繁体字、异体字，不要简化为现代简体字\n"
            "3. 保留 □ 占位符，不要擅自填充或删除\n"
            "4. 保留原文的换行和段落结构\n"
            "5. 不添加原文中没有的标点符号\n"
            "6. 不添加任何注释、说明、前缀或后缀\n"
            "7. 直接输出校正后的纯文本\n\n"
            f"OCR 原始文本：\n{raw_text}"
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

            # 长度校验：校正后文本长度不应偏差太大，防止模型生成无关内容
            if corrected and 0.5 < len(corrected) / max(len(raw_text), 1) < 2.0:
                return corrected
            return raw_text
        return raw_text

    except Exception as e:
        print(f"OCR 后校正失败（使用原文）: {e}")
        return raw_text


# ── 主识别函数 ────────────────────────────────────────────────────────────────

def _run_api_predict(input_file: str, max_retries: int = 3) -> str:
    """
    使用 DashScope Qwen-VL-Max 对古代契约文书图片进行 OCR，
    并通过专项 Prompt 引导模型输出高质量转录结果。
    内置重试机制（指数退避），应对 API 瞬时故障。
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
                    print(f"DashScope API Error (attempt {attempt + 1}/{max_retries}): {response.code} - {response.message}")
            except Exception as e:
                last_error = str(e)
                print(f"API OCR attempt {attempt + 1}/{max_retries} failed: {e}")

            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

        return f"Error: {last_error}"

    except Exception as e:
        print(f"API OCR execution failed: {e}")
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
            print(f"错误：未找到图片记录 -> {image_id}")
            return False

        input_file = str(image.path)

        if not os.path.exists(input_file):
            print(f"错误：图片文件不存在 -> {input_file}")
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
            print(f"OCR处理过程中发生错误 -> {e}")
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
        print(f"Image {image.id} OCR indexed to ChromaDB (doc_id=image_{image.id}).")
    except Exception as e:
        print(f"ChromaDB OCR indexing failed (non-fatal): {e}")


async def ocr_image_by_id_async(image_id: int, db: Session = None) -> bool:
    """异步包装器，供 FastAPI 路由直接调用（非Celery场景）"""
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(ocr_image_by_id, image_id, db)
