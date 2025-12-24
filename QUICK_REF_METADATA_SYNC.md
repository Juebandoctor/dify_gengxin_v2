# 快速参考：元数据同步

## 问题
在 Dify 知识库中删除文件后，本地元数据表格（CSV）不会同步删除。

## 解决方案 ✅

### 自动同步（推荐）
```yaml
# config.yaml
database:
  auto_sync: true
```
```cmd
python upload_enhanced.py
```

### 手动同步
```cmd
# 查看状态
python test_sync.py

# 模拟运行
python sync_metadata.py --dry-run

# 正式同步
python sync_metadata.py
```

### 批处理工具
```cmd
test_metadata_sync.bat
```

## 同步内容
- ✅ SQLite 数据库 (`upload_log.db`)
- ✅ CSV 元数据表 (`metadata/source_table.csv`)

## 输出示例
```
[SUCCESS] ✅ 同步完成：数据库 3 条，元数据表 6 条
```

## 文档
- [METADATA_SYNC_GUIDE.md](METADATA_SYNC_GUIDE.md) - 完整指南
- [METADATA_SYNC_COMPLETE.md](METADATA_SYNC_COMPLETE.md) - 实现细节

---
**状态**: ✅ 已完成 | **日期**: 2025-11-17
