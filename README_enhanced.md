# Dify 知识库自动上传工具 - 使用说明

## 项目结构

```
dify-gengxin/
├── config.yaml                    # 配置文件
├── upload_enhanced.py             # 增强版主脚本
├── update_with_ngrok.py           # 原版脚本（保留）
├── metadata/
│   └── source_table.csv          # 文档元数据表
├── utils/
│   ├── config_loader.py          # 配置加载器
│   ├── metadata_manager.py       # 元数据管理器
│   ├── upload_logger.py          # SQLite 日志管理
│   └── logger.py                 # 日志工具
├── ocr_output/                    # OCR 输出目录
├── upload_log.db                  # 上传历史数据库（自动生成）
└── README_enhanced.md             # 本文档
```

## 新增功能

### 1. 配置文件管理 (config.yaml)

所有配置集中管理，无需修改代码：

- **Dify 配置**: API 地址、数据集 ID、API Key
- **MinerU 配置**: OCR 服务设置
- **文档处理**: 支持的文件类型、大小限制
- **分段索引**: 分隔符、token 数、预处理规则
- **元数据**: CSV 路径、自动创建规则
- **数据库**: SQLite 日志设置

### 2. 元数据管理

#### 自动匹配

脚本会自动从 `metadata/source_table.csv` 中匹配文件元数据：

```csv
id,title,source,keywords,year,region,type,category
DOC-001,2025-《关于强化国土空间规划...》,自然资源部,"规划,政策",2025,全国,政策文件,国土规划
```

#### 自动创建

如果文件在 CSV 中找不到，系统会：
- 自动生成唯一 ID
- 从文件名提取年份
- 根据关键词智能分类
- 自动追加到 CSV 文件


### 文档处理与文件名控制

```yaml
document:
  markdown_chunk_size_mb: 20              # Markdown/TXT 分段上传阈值
  markdown_min_chunk_size_mb: 2           # 自动降级时的最小阈值
  upload_filename_max_length: 120         # 上传到 Dify 时的 ASCII 文件名最大长度
  pdf_split_enabled: true                 # 自动拆分超大 PDF
  pdf_chunk_size_mb: 80                   # PDF 分段尺寸（目标值）
  pdf_max_pages_per_chunk: 200            # 每个 PDF 分段允许的最大页数
  pdf_split_retry_limit: 3                # MinerU 提示超限时的最大递归层数

mineru:
  max_filename_length: 120                # MinerU 直传时允许的最大文件名长度
```

- **保留原始文件名**：当文件名长度在阈值内时，上传到 Dify 会使用原始名称；超出时自动截断并保留扩展名。
- **全 ASCII 保障**：非 ASCII 字符会被智能转义为安全字符，确保 API 不再因为特殊字符报错。
- **超大 PDF 自动拆分**：PDF 文件超过 `pdf_chunk_size_mb` 或 `pdf_max_pages_per_chunk` 时，会使用 PyPDF2 自动生成多个小文件并逐个 OCR + 上传。
- **多级分段降级**：Markdown/纯文本在超过阈值时自动切块，并在遇到 `invalid_param` 时递归继续细分，直至满足 Dify 限制。
- **MinerU 页数超限自愈**：当 MinerU 报错 “number of pages exceeds limit” 时，脚本会自动以更小的体积/页数继续拆分，同一份 PDF 最多递归 `pdf_split_retry_limit` 层。
#### 智能匹配

支持模糊匹配，会自动忽略：
- 《》、（）等符号
- 空格、下划线
- 大小写差异

### 3. SQLite 日志系统

#### 上传历史追踪

- 自动计算文件 MD5 哈希
- 记录上传时间、文件信息
- 保存 Dify 文档 ID
- 自动跳过已上传文件

#### 自动同步功能

**自动同步（推荐）：**
```yaml
database:
  auto_sync: true  # 启动时自动清理已删除文档的记录
```

**手动同步：**
```cmd
# 模拟运行（查看将要删除的记录）
python sync_metadata.py --dry-run

# 正式同步
python sync_metadata.py

# 或使用批处理脚本
sync_dify.bat
```

#### 统计功能

```python
stats = upload_logger.get_statistics()
# {
#   'total_success': 15,
#   'total_failed': 2,
#   'total_size_mb': 156.8
# }
```

## 安装依赖

```cmd
pip install pyyaml watchdog requests
```

## 快速开始

### 1. 配置文件

编辑 `config.yaml`，填入你的 Dify 信息：

```yaml
dify:
  base_url: "http://你的Dify地址"
  dataset_id: "你的数据集ID"
  api_key: "你的API密钥"
```

### 2. 准备元数据（可选）

编辑 `metadata/source_table.csv`，添加你的文档信息。

> 如果不添加，系统会自动创建默认元数据。

### 3. 运行脚本

```cmd
# 使用增强版脚本
python upload_enhanced.py

# 或继续使用原版脚本
python update_with_ngrok.py
```

## 配置说明

### Dify 配置

```yaml
dify:
  base_url: "http://192.168.40.128"     # Dify API 地址
  dataset_id: "96e6249f-..."             # 知识库 ID
  api_key: "dataset-xxx..."              # API 密钥
```

### MinerU OCR 配置

```yaml
mineru:
  enabled: true                          # 是否启用 OCR
  api_key: "eyJ0eXAiOiJKV1Qi..."        # MinerU API Key
  language: "ch"                         # 文档语言
  enable_table: true                     # 表格识别
  prefer_upload: true                    # 直传优先
  max_file_size_mb: 200                  # 最大文件大小
```

### 分段与索引

```yaml
indexing:
  technique: "high_quality"              # 索引模式：high_quality/economy
  separator: "###"                       # 分段分隔符
  max_tokens: 1500                       # 每段最大 token 数
  remove_extra_spaces: true              # 移除多余空格
  remove_urls_emails: false              # 保留 URL 和邮箱
```

**分隔符选项**：
- `"###"` - 按 Markdown 三级标题分段（推荐）
- `"\n\n"` - 按自然段落分段
- `"\n"` - 按行分段（细粒度）

**索引模式**：
- `high_quality` - 高质量模式，适合专业文档
- `economy` - 经济模式，速度更快

### 元数据配置

```yaml
metadata:
  enabled: true                          # 启用元数据
  csv_path: "./metadata/source_table.csv"
  auto_create: true                      # 自动创建缺失的元数据
  default:                               # 默认元数据模板
    source: "自然资源部"
    keywords: "国土空间,生态修复"
    region: "全国"
    type: "政策文件"
```

### 数据库配置

```yaml
database:
  enabled: true                          # 启用日志数据库
  sqlite_path: "./upload_log.db"
  skip_uploaded: true                    # 跳过已上传文件
```

## 元数据表格字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| id | 文档唯一标识 | DOC-001 |
| title | 文档标题 | 2025-《关于强化国土空间...》 |
| source | 来源机构 | 自然资源部 |
| keywords | 关键词（逗号分隔） | 国土空间,生态修复,政策 |
| year | 年份 | 2025 |
| region | 适用地区 | 全国/安徽/... |
| type | 文档类型 | 政策文件/技术指南/... |
| category | 分类 | 国土规划/生态修复/... |

## 日志数据库查询

使用 SQLite 工具查询上传历史：

```sql
-- 查看最近上传
SELECT file_name, upload_time, status 
FROM upload_log 
ORDER BY upload_time DESC 
LIMIT 10;

-- 查看失败记录
SELECT file_name, upload_time, metadata 
FROM upload_log 
WHERE status = 'failed';

-- 统计上传量
SELECT COUNT(*) as total, SUM(file_size)/1024/1024 as size_mb
FROM upload_log 
WHERE status = 'success';
```

## 常见问题

### Q: 如何禁用 OCR？

编辑 `config.yaml`：
```yaml
mineru:
  enabled: false
```

### Q: 如何修改分段策略？

编辑 `config.yaml` 的 `indexing` 部分：
```yaml
indexing:
  separator: "\n\n"    # 改为按段落分段
  max_tokens: 800      # 减小每段大小
```

### Q: 如何查看上传历史？

使用 SQLite 客户端打开 `upload_log.db`，或运行：

```python
from utils.upload_logger import UploadLogger
logger = UploadLogger("upload_log.db")
history = logger.get_upload_history(limit=20)
for item in history:
    print(item)
```

### Q: 删除 Dify 中的文档后，本地日志没有同步怎么办？

**方式 1：自动同步（推荐）**

在 `config.yaml` 中启用自动同步：
```yaml
database:
  auto_sync: true
```
每次启动脚本时会自动清理已删除文档的记录。

**方式 2：手动同步**

使用同步工具：
```cmd
# 查看将要删除的记录
python sync_metadata.py --dry-run

# 确认后执行同步
python sync_metadata.py

# 或使用批处理脚本
sync_dify.bat
```

**方式 3：直接删除数据库**

如果需要完全重置：
```cmd
del upload_log.db
```
下次运行时会重新上传所有文件。

### Q: 元数据匹配不上怎么办？

系统会自动创建默认元数据。你也可以：

1. 检查 CSV 文件中的 `title` 字段是否与文件名一致
2. 系统支持模糊匹配，会忽略特殊符号
3. 查看自动生成的元数据是否需要手动修正

## 与原版脚本的区别

| 特性 | 原版 (update_with_ngrok.py) | 增强版 (upload_enhanced.py) |
|------|----------------------------|----------------------------|
| 配置方式 | 硬编码在脚本中 | YAML 配置文件 |
| 元数据 | 无 | CSV 表格管理 + 自动创建 |
| 上传日志 | 内存 set | SQLite 数据库 |
| 重复检测 | 基于路径 | 基于文件哈希 |
| 统计功能 | 无 | 完整的统计信息 |
| 模块化 | 单文件 | 多模块架构 |

## 迁移指南

从原版脚本迁移到增强版：

1. **保留原脚本**：`update_with_ngrok.py` 继续可用
2. **创建配置文件**：从原脚本复制参数到 `config.yaml`
3. **准备元数据**：创建 `metadata/source_table.csv`（可选）
4. **运行增强版**：`python upload_enhanced.py`

## 技术支持

如遇问题，请检查：

1. 配置文件格式是否正确（YAML 语法）
2. Dify API 地址和密钥是否有效
3. 文件权限是否正确
4. 依赖包是否完整安装

参考日志输出中的详细错误信息。
