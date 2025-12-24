# Dify 分段与索引说明

本文概述 `update_with_ngrok.py` 在将文件上传到 Dify 时使用的分段（segmentation）与索引（indexing）策略，并给出常见调整方法。

## 默认处理流程

1. **文件检查与 OCR**
   - 支持多种格式（PDF、Office、Markdown、邮件等）。
   - 对于包含在 `OCR_EXTENSIONS` 内的文件（如 PDF、图片），优先调用 MinerU OCR 并生成 `*.md` 文本，再上传至 Dify。
   - 其他文件则直接上传原始内容。

2. **上传调用**
   - 目标接口：`POST {DIFY_BASE_URL}/v1/datasets/{DATASET_ID}/document/create-by-file`。
   - 上传表单除了文件本体，还包含 `process_rule` 字段用于传入自定义索引策略。

## 默认索引规则

脚本中构造的 `process_rule` 如下：

```python
process_rule = {
    "indexing_technique": "high_quality",
    "process_rule": {
        "rules": {
            "pre_processing_rules": [
                {"id": "remove_extra_spaces", "enabled": True},
                {"id": "remove_urls_emails", "enabled": False}
            ],
            "segmentation": {
                "separator": "###",
                "max_tokens": 1500
            }
        },
        "mode": "custom"
    }
}
```

说明：
- `indexing_technique = "high_quality"`：使用高质量模式，适合需要更精准拆分与嵌入的场景。
- `mode = "custom"`：告诉 Dify 使用自定义规则而非默认策略。
- `remove_extra_spaces`：移除多余空格，保持文本紧凑。
- `remove_urls_emails`：保持为 `False`，保留原始 URL 与邮箱地址。
- `separator = "###"`：按 Markdown 三级标题切块；若文本中没有 `###`，Dify 会退化为按照段落拆分。
- `max_tokens = 1500`：单块最大 tokens，超限时 Dify 会自动进一步拆分。

## 自定义分段或预处理

如需调整，可在 `process_file()` 中修改上述字段。常见需求示例：

1. **改用自然段拆分**
   ```python
   "segmentation": {
       "separator": "\n\n",  # 双换行
       "max_tokens": 800
   }
   ```

2. **更细粒度切分**（如聊天机器人场景）：
   ```python
   "segmentation": {
       "separator": "\n",
       "max_tokens": 400
   }
   ```

3. **全量移除 URL / 邮箱**（若数据隐私要求更高）：
   ```python
   "pre_processing_rules": [
       {"id": "remove_extra_spaces", "enabled": True},
       {"id": "remove_urls_emails", "enabled": True}
   ]
   ```

4. **切换经济模式以加快索引**：
   ```python
   "indexing_technique": "economy"
   ```

> 修改后保存脚本并重新运行，新的上传任务会按最新规则执行。已上传的文档需要在 Dify 控制台中重新索引或删除后重新导入。

## 验证变更是否生效

1. 运行脚本，上传任意文件，观察终端输出的 `process_rule` 设置是否与期望一致（必要时可临时 `print(process_rule)` 进行调试）。
2. 在 Dify 控制台查看文档详情：
   - “分段方式”应显示为自定义模式。
   - 如启用 `###` 分隔，可在索引结果中看到各章节被独立切块。
3. 若 Dify 长时间显示 `waiting` 或 `parsing`，请确认后台 Worker 服务正常。

## 常见问题

- **分段过大导致召回困难**：降低 `max_tokens` 或缩小 `separator`。
- **文本缺少结构化标题**：可在 OCR 输出或原文中插入 `###` 标记，或切换至 `\n\n` 等更通用的分隔符。
- **索引失败（error/failed）**：检查 Dify Worker 日志以及上传文件的编码、体积、格式是否符合要求。

如需脚本层面增加配置项（例如通过环境变量或命令行参数控制），可以将 `process_rule` 的常量抽到文件顶部定义，并根据需求读取外部配置。