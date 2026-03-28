FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统级依赖 (部分图像处理库和 ChromaDB 可能依赖)
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 3000

# 默认启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
