FROM python:3.11-slim

ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# 设置工作目录
WORKDIR /app

# 安装系统级依赖 (部分图像处理库和 ChromaDB 可能依赖)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 可选：在构建阶段缓存模型，避免第一次 OCR 请求临时下载。
# 默认关闭，避免演示部署因模型源 DNS/网络波动导致镜像构建失败。
COPY scripts/download_ocr_models.py scripts/download_ocr_models.py
ARG PRELOAD_OCR_MODELS=false
RUN if [ "$PRELOAD_OCR_MODELS" = "true" ]; then python scripts/download_ocr_models.py; else echo "Skipping OCR model preload"; fi

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 3000

# 默认启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
