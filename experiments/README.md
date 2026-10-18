# 文献图片处理实验

本实验面向“契约文书”和“族谱/行传图”两类材料，提供一条可复现的离线实验管线：

```text
图片/OCR文本
  -> 数据清单
  -> OCR 文本归一化
  -> 实体与关系抽取
  -> JSON
  -> ECharts 图谱 HTML
```

## 数据

默认数据目录为仓库根目录下的：

```text
文献图片数据-6契-5行传/
```

其中包含：

- 6 张契约 JPG，已有对应 `.txt` OCR 文本
- 5 张 `12800×9600` 的行传 PNG
- 1 份契约点校规范 `.doc`

原始数据和生成输出都不纳入 Git。

## 运行契约主实验

```bash
python experiments/literature_pipeline.py \
  --data-dir "文献图片数据-6契-5行传" \
  --output-dir experiment_outputs
```

输出：

```text
experiment_outputs/
  manifest.json
  ocr/
    3.141.txt
    ...
  graphs/
    3.141.json
    3.141.html
    ...
  result.json
```

`graphs/*.json` 包含：

- 实体 `entities`
- 关系 `relations`
- 元数据 `metadata`
- 每个实体和关系的 `evidence`

`graphs/*.html` 为可直接打开的 ECharts 力导向图。

## 运行行传图高级实验

行传图为超大幅 PNG，不适合课堂直接并发 OCR。可先生成低分辨率预览：

```bash
python experiments/literature_pipeline.py \
  --data-dir "文献图片数据-6契-5行传" \
  --output-dir experiment_outputs \
  --preprocess-genealogy \
  --max-long-side 2400
```

输出：

```text
experiment_outputs/genealogy_previews/
  24.jpg
  25.jpg
  26.jpg
  27.jpg
  28.jpg
```

这些预览可用于：

- 后续 OCR 实验
- 版式分析
- 人工点校
- 族谱实体抽取

## 实验拆分

建议拆成三部分：

1. **OCR 实验**：5 人一组，每组 1 张图，错峰执行。
2. **实体抽取实验**：直接使用已有 OCR 文本，不依赖 OCR 现场。
3. **图谱展示实验**：读取 JSON，生成并解释图谱。

这样可以避免 1000+ 学生同时压 OCR，同时保证 b/c 环节不被 OCR 阻塞。

## 并发建议

- 每组 5 人。
- 每组只处理 1 张图片。
- 班级错峰。
- 同一张图片使用缓存。
- 本地 PaddleOCR 与云端 DashScope 可混合使用。
- 契约图优先本地 OCR。
- 行传图建议先降采样或分块，再走云端 OCR。

