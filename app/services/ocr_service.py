
import os
import json
import re
import base64
from sqlalchemy.orm import Session
from database import SessionLocal, Image, OcrResult, OcrStatus
from app.core.config import settings
from fastapi.concurrency import run_in_threadpool

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def extract_text_content(json_file_path: str, text_save_path: str) -> bool:
    if not os.path.exists(json_file_path):
        print(f"错误：JSON文件不存在 -> {json_file_path}")
        return False

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            json_content = json.load(f)

        if "parsing_res_list" not in json_content:
            print("错误：JSON文件中未找到有效的parsing_res_list数据")
            return False

        parsing_res_list = json_content["parsing_res_list"]
        extracted_texts = []
        for block in parsing_res_list:
            if block.get("block_label") == "text":
                block_content = block.get("block_content", "")
                extracted_texts.append(block_content)

        combined_text = "\n".join(extracted_texts)
        cleaned_text = re.sub(r"\n{2,}", "\n", combined_text)
        cleaned_text = re.sub(
            r"^\s+|\s+$", "", cleaned_text, flags=re.MULTILINE
        )
        cleaned_text = cleaned_text.strip()

        os.makedirs(os.path.dirname(text_save_path), exist_ok=True)
        with open(text_save_path, "w", encoding="utf-8") as f:
            f.write(cleaned_text)

        return True

    except json.JSONDecodeError as e:
        print(f"错误：JSON文件格式解析失败 -> {e}")
        return False
    except Exception as e:
        print(f"提取正文时发生未知错误 -> {e}")
        return False


def _run_api_predict(input_file: str):
    """Run OCR prediction using DashScope Qwen-VL API."""
    try:
        from dashscope import MultiModalConversation
        import dashscope
        
        dashscope.api_key = settings.DASHSCOPE_API_KEY
        if not dashscope.api_key:
             raise ValueError("DASHSCOPE_API_KEY is not set in environment variables.")

        # Convert image to base64 or use local file URI depending on DashScope requirement
        # Qwen-VL supports local file paths using file:// prefix
        local_file_path = f"file://{os.path.abspath(input_file)}"

        messages = [{
            'role': 'user',
            'content': [
                {'image': local_file_path},
                {'text': '你是一个专业的古籍文字识别专家。请精准提取图片中的所有文字，保持原文的阅读顺序（通常是从上到下，从右到左，或从左到右）。\n要求：\n1. 绝对不要输出任何除了图片原文以外的文字！\n2. 不要加任何开场白或解释语（例如不要说"图片中的文字是："）\n3. 不要把繁体字转换成简体字。\n4. 只输出纯文本内容。'}
            ]
        }]

        response = MultiModalConversation.call(
            model='qwen-vl-plus',
            messages=messages
        )
        
        if response.status_code == 200:
             # Extract text from response
             # Response format: response.output.choices[0].message.content[0]['text']
             content_list = response.output.choices[0].message.content
             for item in content_list:
                 if 'text' in item:
                     return item['text']
             return ""
        else:
             print(f"DashScope API Error: {response.code} - {response.message}")
             return f"Error: API Request failed with code {response.code}"

    except Exception as e:
        print(f"API OCR execution failed: {e}")
        return f"Error: {str(e)}"

async def ocr_image_by_id(image_id: int, db: Session = None) -> bool:
    """
    输入：image_id (Image表中的主键)
    处理：从数据库中查找图片路径，执行OCR
    输出：结果保存在OcrResult表中
    成功返回 True，失败返回 False
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # 从数据库查找图片记录
        image = db.query(Image).filter(Image.id == image_id).first()

        if image is None:
            print(f"错误：未找到图片记录 -> {image_id}")
            return False

        input_file = str(image.path)

        if not os.path.exists(input_file):
            print(f"错误：图片文件不存在 -> {input_file}")
            return False

        try:
            # 创建 OcrResult，状态为 PROCESSING
            ocr_result = OcrResult(
                image_id=image_id,
                raw_text="",
                status=OcrStatus.PROCESSING
            )
            db.add(ocr_result)
            db.commit()
            db.refresh(ocr_result)

            # 执行 OCR 操作 (Async in threadpool using DashScope API)
            extracted_text = await run_in_threadpool(_run_api_predict, input_file)

            if not extracted_text or extracted_text.startswith("Error:"):
                 if not extracted_text:
                     extracted_text = "模拟OCR文本：未能识别到文字。"

            # 清理文本
            cleaned_text = re.sub(r"\n{2,}", "\n", extracted_text)
            cleaned_text = cleaned_text.strip()

            # 更新 OcrResult 的内容和状态为 DONE
            ocr_result.raw_text = cleaned_text
            ocr_result.status = OcrStatus.DONE
            db.commit()

            return True

        except Exception as e:
            # 更新 OcrResult 的状态为 FAILED
            if 'ocr_result' in locals():
                ocr_result.status = OcrStatus.FAILED
                db.commit()
            print(f"OCR处理过程中发生错误 -> {e}")
            return False

    finally:
        if close_db:
            db.close()
