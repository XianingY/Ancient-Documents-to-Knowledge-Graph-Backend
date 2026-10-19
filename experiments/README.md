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

## 高分辨率分块 OCR

整图 OCR 会把 `12800×9600` 行传图压缩到最长边 2400。可用以下脚本对原图做高分辨率局部识别：

```bash
python experiments/tiled_ocr.py \
  "文献图片数据-6契-5行传/2.行传图-5张/25.png" \
  --output-dir experiment_outputs/tiled_ocr \
  --tile-width 6400 \
  --tile-height 4800 \
  --overlap 256 \
  --engine consensus
```

也可以只保留 PP-OCRv6 medium 的原始识别结果，并按置信度过滤：

```bash
python experiments/tiled_ocr.py \
  "文献图片数据-6契-5行传/2.行传图-5张/25.png" \
  --output-dir experiment_outputs/tiled_ocr_medium \
  --tile-width 6400 \
  --tile-height 4800 \
  --overlap 256 \
  --engine medium \
  --min-confidence 0.8
```

输出：

```text
experiment_outputs/tiled_ocr_medium/
  25.tiled.txt
  25.tiled.json
```

### 实验结论

以占位符最多的 `25.png` 为样本：

| 方式 | 切片数 | 可见字符 | `□` 数量 | `□` 占比 |
|---|---:|---:|---:|---:|
| 整图 OCR | 1 | 496 | 44 | 8.87% |
| 3200×2400 共识分块 | 25 | 284 | 124 | 43.66% |
| 6400×4800 共识分块 | 9 | 698 | 113 | 16.19% |
| medium raw，不过滤 | 9 | 1265 | 0 | 0% |
| medium raw，置信度 ≥ 0.8 | 9 | 247 | 0 | 0% |

结论：

- 高分辨率分块配合当前双模型共识管线，会放大 medium/small 模型的分歧，`□` 显著增加。
- raw medium 可以把 `□` 降为 0，但低置信度片段包含明显乱码，不能视为准确率提升。
- 置信度 ≥ 0.8 后文本较可靠，但覆盖量大幅下降。
- 因此，当前不建议用高分辨率分块替代整图 OCR；它适合作为对比实验或人工校对辅助。

## DeepSeek 实体关系抽取

默认管线中的实体关系抽取是规则抽取。可使用 `experiments/llm_extraction.py` 切换为 DeepSeek LLM 抽取，并保留规则抽取作为回退。

### 1. 配置 API Key

不要把密钥写入代码或 `.env` 提交到 Git：

```bash
export DEEPSEEK_API_KEY="你的DeepSeek API Key"
```

### 2. 运行

```bash
python experiments/llm_extraction.py \
  --input-dir experiment_outputs/ocr \
  --output-dir experiment_outputs/llm_graphs \
  --model deepseek-chat
```

如果还没有 OCR 输出目录，先运行规则主实验生成：

```bash
python experiments/literature_pipeline.py \
  --data-dir "文献图片数据-6契-5行传" \
  --output-dir experiment_outputs
```

### 3. 输出

```text
experiment_outputs/llm_graphs/
  3.141.json
  3.141.html
  ...
```

每个 JSON 文件包含：

```json
{
  "document_id": "3.141",
  "extraction_method": "llm",
  "extraction": {
    "entities": [],
    "relations": [],
    "metadata": {}
  },
  "validation": {
    "accepted_entities": 0,
    "rejected_entities": 0,
    "accepted_relations": 0,
    "rejected_relations": 0
  },
  "fallback_reason": null
}
```

### 4. 校验规则

LLM 输出不会直接被信任。脚本会：

1. 要求模型返回 JSON。
2. 要求每个实体和关系都有 `evidence`。
3. 检查 `evidence` 是否逐字来自 OCR 文本。
4. 拒绝包含 `□` / `■` 的 evidence。
5. 检查关系两端是否引用已通过校验的实体。
6. 检查实体类型和关系类型是否在白名单内。
7. LLM 失败、JSON 无效或没有可接受实体时，回退到规则抽取。

### 5. 无 API Key 模式

未设置 `DEEPSEEK_API_KEY` 时，脚本会自动回退到规则抽取，适合课堂离线演示：

```bash
python experiments/llm_extraction.py \
  --input-dir experiment_outputs/ocr \
  --output-dir experiment_outputs/llm_graphs
```

如果希望没有 Key 时直接报错，而不是回退：

```bash
python experiments/llm_extraction.py \
  --input-dir experiment_outputs/ocr \
  --output-dir experiment_outputs/llm_graphs \
  --no-fallback
```

### 6. 推荐实验任务

学生可以对比规则抽取和 LLM 抽取：

| 项目 | 规则抽取 | LLM 抽取 |
|---|---|---|
| 中人 | 通常未抽取 | 可识别 `憑中` 人物 |
| 四至 | 通常未抽取 | 可尝试识别方位和邻地 |
| 契约类型 | 需继续补规则 | 可从标题和正文判断 |
| 稳定性 | 完全可复现 | 受模型和 prompt 影响 |
| 幻觉风险 | 无 | 有，需要 evidence 校验 |
| 离线能力 | 可离线 | 需要 API |

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
