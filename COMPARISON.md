# 功能对比与升级说明

## 📋 功能对比表

| 功能特性 | 原版脚本 | 增强版脚本 | 说明 |
|---------|---------|-----------|------|
| **配置管理** | ❌ 硬编码 | ✅ YAML 配置文件 | 无需修改代码，配置更灵活 |
| **元数据管理** | ❌ 无 | ✅ CSV 表格 + 自动生成 | 支持文档分类、标签、来源等信息 |
| **上传日志** | ⚠️ 内存集合 | ✅ SQLite 数据库 | 持久化存储，支持查询统计 |
| **重复检测** | ⚠️ 基于文件路径 | ✅ 基于文件哈希 | 更准确，避免重命名后重复上传 |
| **统计功能** | ❌ 无 | ✅ 完整统计 | 上传数量、失败次数、总大小等 |
| **模块化** | ⚠️ 单文件 2000+ 行 | ✅ 多模块架构 | 易维护、易扩展 |
| **日志输出** | ⚠️ print 语句 | ✅ 统一日志工具 | 带时间戳、级别标记 |
| **错误处理** | ⚠️ 基础 | ✅ 增强 | 详细错误信息和失败记录 |
| **MinerU OCR** | ✅ 支持 | ✅ 支持 | 保持原有功能 |
| **文件监控** | ✅ watchdog | ✅ watchdog | 保持原有功能 |
| **Dify 集成** | ✅ 支持 | ✅ 支持 | 保持原有功能 |

## 🎯 核心改进

### 1. 配置文件管理

**原版（硬编码）：**
```python
DIFY_BASE_URL = "http://192.168.40.128"
DATASET_ID = "96e6249f-..."
API_KEY = "dataset-xxx..."
MINERU_API_KEY = "eyJ0eXAi..."
```
❌ 问题：
- 每次修改需要编辑代码
- 不同环境需要维护多个脚本
- 敏感信息暴露在代码中

**增强版（YAML）：**
```yaml
dify:
  base_url: "http://192.168.40.128"
  dataset_id: "96e6249f-..."
  api_key: "dataset-xxx..."
```
✅ 优势：
- 配置与代码分离
- 易于管理和版本控制
- 支持不同环境配置

### 2. 元数据管理

**原版：**
- 无元数据支持
- 文件上传后只有文件名

**增强版：**
```csv
id,title,source,keywords,year,region,type,category
DOC-001,《关于强化国土空间...》,自然资源部,规划;政策,2025,全国,政策文件,国土规划
```
✅ 优势：
- 自动匹配文档元数据
- 找不到时自动创建默认元数据
- 支持模糊匹配（忽略符号、空格）
- 便于后续检索和分类

### 3. SQLite 日志

**原版：**
```python
self.processed_files = set()  # 内存集合，重启后丢失
```
❌ 问题：
- 重启脚本后重新上传所有文件
- 无法查询历史记录
- 无统计功能

**增强版：**
```sql
CREATE TABLE upload_log (
    id INTEGER PRIMARY KEY,
    file_hash TEXT UNIQUE,      -- MD5 哈希
    file_name TEXT,
    upload_time TEXT,
    dify_doc_id TEXT,
    status TEXT,
    metadata TEXT
)
```
✅ 优势：
- 持久化存储
- 基于文件哈希避免重复
- 可查询历史和统计
- 记录失败原因

### 4. 模块化架构

**原版结构：**
```
update_with_ngrok.py  (2000+ 行单文件)
```

**增强版结构：**
```
upload_enhanced.py          (主脚本 ~300 行)
utils/
  ├── config_loader.py      (配置管理)
  ├── metadata_manager.py   (元数据)
  ├── upload_logger.py      (日志)
  └── logger.py             (工具)
```
✅ 优势：
- 职责分离
- 易于测试
- 便于扩展
- 代码复用

## 📊 使用场景对比

### 场景 1：快速测试

**原版：** ✅ 适合
- 改几个变量就能跑
- 无需额外配置

**增强版：** ⚠️ 需要初始化
- 需要创建 config.yaml
- 适合正式使用

**建议：** 测试用原版，生产用增强版

### 场景 2：多环境部署

**原版：** ❌ 不便
- 需要维护多个脚本副本
- 易出错

**增强版：** ✅ 方便
- 只需要不同的 config.yaml
- 代码统一

### 场景 3：团队协作

**原版：** ❌ 困难
- API Key 等敏感信息在代码中
- 难以共享和版本控制

**增强版：** ✅ 友好
- 配置文件可以用 .gitignore 排除
- 可以提供 config.yaml.example 模板

### 场景 4：批量处理历史文件

**原版：** ⚠️ 问题
- 重启后会重新上传
- 无法知道哪些已处理

**增强版：** ✅ 完善
- 自动跳过已上传文件
- 可查询历史记录
- 失败文件可以重试

## 🔄 迁移步骤

### 第 1 步：安装依赖

```cmd
pip install pyyaml watchdog requests
```

或运行：
```cmd
install_dependencies.bat
```

### 第 2 步：创建配置文件

从 `update_with_ngrok.py` 提取配置项到 `config.yaml`：

```python
# 原版中的配置
DIFY_BASE_URL = "http://192.168.40.128"
DATASET_ID = "96e6249f-..."
API_KEY = "dataset-xxx..."
```

```yaml
# 新建 config.yaml
dify:
  base_url: "http://192.168.40.128"
  dataset_id: "96e6249f-..."
  api_key: "dataset-xxx..."
```

### 第 3 步：准备元数据（可选）

创建 `metadata/source_table.csv`：

```csv
id,title,source,keywords,year,region,type,category
DOC-001,你的文档标题,来源机构,关键词,2025,地区,类型,分类
```

> 如果跳过此步骤，系统会自动生成默认元数据

### 第 4 步：运行增强版

```cmd
python upload_enhanced.py
```

或双击：
```cmd
start_enhanced.bat
```

### 第 5 步：验证

检查生成的文件：
- `upload_log.db` - 上传历史数据库
- `metadata/source_table.csv` - 自动创建的元数据

## 📈 性能对比

| 指标 | 原版 | 增强版 | 说明 |
|------|------|--------|------|
| 启动时间 | ~1s | ~2s | 增强版需要加载配置和数据库 |
| 内存占用 | 较低 | 略高 | 额外的数据结构 |
| 上传速度 | 相同 | 相同 | 核心逻辑未变 |
| 重复检测 | O(n) | O(1) | 哈希表查询更快 |
| 可维护性 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 模块化架构 |

## 🔍 实际案例

### 案例 1：政策文件库

**需求：**
- 100+ 个政策文件
- 需要标注来源、年份、地区
- 避免重复上传

**原版方案：**
- 手动记录已上传文件
- 无法批量管理元数据
- 容易重复上传

**增强版方案：**
```yaml
# 配置默认元数据
metadata:
  default:
    source: "自然资源部"
    type: "政策文件"
    region: "全国"
```
- 自动创建元数据
- 哈希检测避免重复
- 可批量查询统计

### 案例 2：多环境部署

**需求：**
- 开发环境测试
- 生产环境正式使用
- 不同的 API Key 和数据集

**原版方案：**
- 维护两个脚本副本
- 容易搞混

**增强版方案：**
```
config.dev.yaml    # 开发配置
config.prod.yaml   # 生产配置

python upload_enhanced.py --config config.prod.yaml
```

## 💡 最佳实践

### 1. 配置文件管理

```
# .gitignore
config.yaml        # 排除敏感配置
upload_log.db      # 排除数据库

# 提供模板
config.yaml.example  # 可以提交
```

### 2. 元数据维护

- 定期审查自动生成的元数据
- 补充关键词和分类
- 保持 CSV 文件格式正确

### 3. 日志数据库

```sql
-- 定期清理旧记录（可选）
DELETE FROM upload_log 
WHERE upload_time < date('now', '-90 days');

-- 导出统计报告
SELECT 
  source,
  COUNT(*) as count,
  SUM(file_size)/1024/1024 as size_mb
FROM upload_log
WHERE status='success'
GROUP BY source;
```

### 4. 监控和告警

增强版提供更好的日志输出，可以配合其他工具：

```cmd
# 重定向日志到文件
python upload_enhanced.py > upload.log 2>&1

# 监控失败记录
python -c "from utils.upload_logger import UploadLogger; \
           logger = UploadLogger('upload_log.db'); \
           stats = logger.get_statistics(); \
           print(f'Failed: {stats[\"total_failed\"]}')"
```

## 🎓 总结

### 何时使用原版？
- ✅ 快速测试
- ✅ 一次性任务
- ✅ 简单场景

### 何时使用增强版？
- ✅ 生产环境
- ✅ 大批量文件
- ✅ 需要元数据
- ✅ 团队协作
- ✅ 长期维护

### 可以共存吗？
✅ 可以！两个脚本互不影响，可以：
- 保留原版作为备份
- 逐步迁移到增强版
- 根据场景选择使用

---

**推荐做法：**
1. 保留原版 `update_with_ngrok.py`
2. 使用增强版 `upload_enhanced.py` 作为主力
3. 根据实际情况选择使用
