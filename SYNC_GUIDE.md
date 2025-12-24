# Dify 文档同步功能说明

## 问题背景

当你在 Dify 知识库界面中删除文档后，本地的 `upload_log.db` 数据库中仍然保留着该文档的上传记录。这会导致：

1. ❌ 数据不一致（本地记录显示已上传，但 Dify 中已删除）
2. ❌ 如果你想重新上传该文件，脚本会认为已上传而跳过
3. ❌ 统计信息不准确

## 解决方案

我们添加了**自动同步功能**，可以自动清理已在 Dify 中删除的文档记录。

## 使用方法

### 方式 1：自动同步（推荐）⭐

**配置文件设置：**

```yaml
# config.yaml
database:
  enabled: true
  auto_sync: true  # 启用自动同步
```

**效果：**
- ✅ 每次启动 `upload_enhanced.py` 时自动同步
- ✅ 自动清理 Dify 中已删除的文档记录
- ✅ 无需手动干预

**日志输出：**
```
[INFO] 2025-11-17 10:30:00 - 开始与 Dify 同步...
[SUCCESS] 2025-11-17 10:30:02 - ✅ 同步完成，清理了 3 条已删除文档的记录
```

### 方式 2：手动同步

**使用命令行工具：**

```cmd
# 1. 模拟运行（查看将要删除什么，不实际删除）
python sync_metadata.py --dry-run

# 2. 确认无误后，正式同步
python sync_metadata.py
```

**使用批处理脚本：**

```cmd
# 双击运行
sync_dify.bat

# 选择操作：
#   1 - 模拟运行
#   2 - 正式同步
#   0 - 退出
```

**输出示例：**

```
========================================
  Dify 文档同步工具
========================================

[INFO] 本地数据库中有 25 条上传记录
[INFO] 正在从 Dify 获取文档列表...
[INFO]   第 1 页: 22 个文档
[SUCCESS] 成功获取 22 个文档
[INFO] Dify 知识库中有 22 个文档
[WARNING] 发现 3 条需要清理的记录

待删除的文档 ID：
  1. 0a09c548-6232-4a...
  2. 9f8f3bc4-b8fc-4f...
  3. 1234abcd-5678-ef...

是否继续删除这些记录？[y/N]: y

[SUCCESS] ✅ 成功删除 3 条记录

同步后统计：
[INFO]   成功上传: 22 个文件
[INFO]   失败记录: 0 个
[INFO]   总大小: 245.6 MB
```

### 方式 3：直接删除数据库（不推荐）

如果需要完全重置：

```cmd
# 删除数据库文件
del upload_log.db

# 下次运行时会重新上传所有文件
python upload_enhanced.py
```

⚠️ **警告**：这会导致所有文件重新上传！

## 工作原理

```
┌─────────────────┐
│  本地数据库      │
│  upload_log.db  │
│                 │
│  25 条记录      │
└────────┬────────┘
         │
         │ 对比
         ↓
┌─────────────────┐
│  Dify 知识库    │
│                 │
│  22 个文档      │
└────────┬────────┘
         │
         ↓
    删除差异记录
     (3 条记录)
```

**同步逻辑：**

1. 从本地数据库获取所有已记录的 Dify 文档 ID
2. 调用 Dify API 获取知识库中实际存在的文档 ID
3. 计算差集：`本地记录 - Dify实际存在 = 需要删除的记录`
4. 删除这些"孤儿记录"

**安全性：**
- ✅ 只删除数据库记录，不影响本地文件
- ✅ 只删除已在 Dify 中删除的记录
- ✅ 模拟运行可以预览将要删除的内容

## 配置选项

### config.yaml

```yaml
database:
  enabled: true              # 启用日志数据库
  sqlite_path: "./upload_log.db"
  skip_uploaded: true        # 跳过已上传文件
  auto_sync: true            # ⭐ 自动同步开关
```

### 命令行参数

```cmd
python sync_metadata.py --help

选项：
  --config CONFIG   配置文件路径（默认: config.yaml）
  --dry-run         模拟运行，不实际删除
```

## 使用场景

### 场景 1：定期维护

**问题：**
- 每周都会在 Dify 中删除一些过期文档
- 本地记录越来越多，但实际文档已删除

**解决：**
```yaml
# 启用自动同步
database:
  auto_sync: true
```

每次启动脚本都会自动清理。

### 场景 2：批量清理后

**问题：**
- 在 Dify 中批量删除了 100+ 个文档
- 想立即同步本地记录

**解决：**
```cmd
# 立即执行同步
python sync_metadata.py
```

### 场景 3：测试环境

**问题：**
- 测试时反复上传、删除文档
- 不想每次都自动同步

**解决：**
```yaml
# 关闭自动同步
database:
  auto_sync: false
```

需要时手动执行 `python sync_metadata.py`。

### 场景 4：检查状态

**问题：**
- 想看看有多少"孤儿记录"
- 但不想立即删除

**解决：**
```cmd
# 模拟运行
python sync_metadata.py --dry-run
```

## 常见问题

### Q: 会删除本地文件吗？

**A:** 不会！只删除数据库中的记录，不影响本地文件。

### Q: 会影响 Dify 中的文档吗？

**A:** 不会！只同步本地数据库，不修改 Dify。

### Q: 如果同步出错怎么办？

**A:** 同步失败不会影响主功能：
- 自动同步失败：会显示警告但继续运行
- 手动同步失败：可以稍后重试
- 最坏情况：删除 `upload_log.db` 重新开始

### Q: 多久需要同步一次？

**A:** 取决于你的使用习惯：
- 启用 `auto_sync: true` → 无需手动，每次启动自动同步
- 频繁删除文档 → 每周手动同步一次
- 很少删除 → 几个月同步一次即可

### Q: 同步会很慢吗？

**A:** 通常很快：
- 小知识库（< 100 文档）：1-2 秒
- 中等知识库（100-500 文档）：5-10 秒
- 大知识库（> 500 文档）：10-30 秒

如果知识库很大，可以关闭自动同步，改为定期手动同步。

### Q: 可以恢复被删除的记录吗？

**A:** 不能直接恢复，但有备份方案：

**方案 1：定期备份数据库**
```cmd
copy upload_log.db upload_log.backup.db
```

**方案 2：使用版本控制**
```cmd
git add upload_log.db
git commit -m "备份上传日志"
```

## 技术细节

### 数据库结构

```sql
-- upload_log 表
CREATE TABLE upload_log (
    id INTEGER PRIMARY KEY,
    file_hash TEXT UNIQUE,    -- 文件哈希（用于重复检测）
    file_name TEXT,
    file_path TEXT,
    upload_time TEXT,
    dify_doc_id TEXT,         -- ⭐ Dify 文档 ID（用于同步）
    status TEXT,
    metadata TEXT
);

-- 索引
CREATE INDEX idx_dify_doc_id ON upload_log(dify_doc_id);
```

### Dify API 调用

```python
# 获取文档列表
GET /v1/datasets/{dataset_id}/documents
    ?page=1&limit=100

# 返回示例
{
    "data": [
        {"id": "doc-id-1", "name": "文档1.pdf"},
        {"id": "doc-id-2", "name": "文档2.pdf"},
        ...
    ]
}
```

### 同步算法

```python
# 1. 获取本地所有 Dify 文档 ID
local_doc_ids = upload_logger.get_all_dify_doc_ids()

# 2. 获取 Dify 实际文档 ID
dify_doc_ids = get_dify_documents(config)

# 3. 计算差集
to_delete = set(local_doc_ids) - set(dify_doc_ids)

# 4. 删除孤儿记录
for doc_id in to_delete:
    upload_logger.delete_by_dify_doc_id(doc_id)
```

## 最佳实践

### 推荐配置

```yaml
database:
  enabled: true
  auto_sync: true          # ⭐ 启用自动同步
  skip_uploaded: true
```

### 定期维护

```cmd
# 每月检查一次
python sync_metadata.py --dry-run

# 查看统计
python -c "from utils.upload_logger import UploadLogger; \
           logger = UploadLogger('upload_log.db'); \
           print(logger.get_statistics())"
```

### 备份策略

```cmd
# 每周备份
copy upload_log.db backups\upload_log_%date%.db
```

## 故障排查

### 问题：同步失败，提示网络错误

**解决：**
1. 检查 Dify 服务是否运行
2. 检查网络连接
3. 检查 API Key 是否有效

### 问题：同步后仍然跳过某些文件

**原因：** 可能是基于文件哈希的检测，而不是 Dify ID

**解决：**
```python
# 查看该文件的记录
from utils.upload_logger import UploadLogger
logger = UploadLogger('upload_log.db')
print(logger.is_uploaded('文件路径'))

# 手动删除
logger.delete_by_file_path('文件路径')
```

### 问题：模拟运行显示很多记录，但不确定是否删除

**解决：**
1. 先执行模拟运行，查看列表
2. 在 Dify 界面确认这些文档确实已删除
3. 再执行正式同步

## 总结

✅ **推荐设置：**
```yaml
database:
  auto_sync: true  # 启用自动同步，省心省力
```

✅ **偶尔手动检查：**
```cmd
python sync_metadata.py --dry-run  # 查看状态
```

✅ **定期备份：**
```cmd
copy upload_log.db upload_log.backup.db
```

这样可以确保本地记录与 Dify 保持一致，避免数据混乱！
