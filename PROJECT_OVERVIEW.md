# 📚 Dify 知识库自动上传工具 - 项目总览

## 🎯 项目简介

这是一个为 Dify 知识库设计的自动化上传工具，专门针对自然资源、国土空间、生态修复等领域的文档管理需求。支持 OCR 文字提取、元数据管理、自动分段索引等功能。

## 📦 项目结构

```
dify-gengxin/
│
├── 📄 配置文件
│   └── config.yaml                    # 主配置文件
│
├── 🚀 主脚本
│   ├── upload_enhanced.py            # 增强版脚本（推荐）
│   └── update_with_ngrok.py          # 原版脚本（保留）
│
├── 📊 元数据
│   └── metadata/
│       └── source_table.csv          # 文档元数据表
│
├── 🛠️ 工具模块
│   └── utils/
│       ├── config_loader.py          # 配置加载器
│       ├── metadata_manager.py       # 元数据管理
│       ├── upload_logger.py          # SQLite 日志
│       └── logger.py                 # 日志工具
│
├── 📁 输出目录
│   ├── ocr_output/                   # OCR 提取的文本
│   └── upload_log.db                 # 上传历史数据库（自动生成）
│
├── 🔧 辅助脚本
│   ├── install_dependencies.bat      # 依赖安装
│   └── start_enhanced.bat            # 快速启动
│
└── 📖 文档
    ├── README_enhanced.md            # 使用说明
    ├── README_segmentation.md        # 分段说明
    ├── COMPARISON.md                 # 功能对比
    └── PROJECT_OVERVIEW.md           # 本文档
```

## ✨ 核心功能

### 1. 文件监控与自动上传
- ✅ 实时监控指定文件夹
- ✅ 支持 20+ 种文件格式
- ✅ 自动检测新文件和修改
- ✅ 批量处理现有文件

### 2. OCR 文字提取（MinerU）
- ✅ PDF、图片 OCR 识别
- ✅ 支持中英文混排
- ✅ 表格识别
- ✅ 公式识别（可选）
- ✅ 直传模式（绕过网络限制）

### 3. 智能分段与索引
- ✅ 自定义分段规则
- ✅ 高质量/经济模式
- ✅ Token 数控制
- ✅ 预处理规则配置

### 4. 元数据管理
- ✅ CSV 表格管理文档信息
- ✅ 自动匹配与模糊搜索
- ✅ 自动生成默认元数据
- ✅ 支持文档分类、标签、来源等

### 5. SQLite 日志系统
- ✅ 基于文件哈希的重复检测
- ✅ 上传历史持久化
- ✅ 失败记录与重试
- ✅ 统计功能（数量、大小、成功率）

### 6. 配置文件管理
- ✅ YAML 格式配置
- ✅ 配置与代码分离
- ✅ 支持多环境部署
- ✅ 敏感信息保护

## 🎓 使用场景

### 场景 1：政策文件库建设
```
政策文件-国土空间生态修复-2025.11.04.docx
历史遗留废弃矿山生态修复示范工程项目实施方案.pdf
安徽省全域土地综合整治工程实施方案编制大纲.pdf
...
```
**适用功能：**
- 元数据自动分类（政策/技术指南/标准）
- 按年份、地区、来源组织
- OCR 处理扫描件

### 场景 2：大批量文档迁移
```
待处理文档：500+ 个
已处理文档：自动跳过
失败文档：记录并重试
```
**适用功能：**
- SQLite 日志避免重复
- 批量处理现有文件
- 统计和监控

### 场景 3：多环境部署
```
开发环境：测试数据集
生产环境：正式知识库
```
**适用功能：**
- 配置文件隔离
- 不同的 API Key
- 环境切换方便

### 场景 4：团队协作
```
团队成员 A：负责上传政策文件
团队成员 B：负责上传技术标准
团队成员 C：维护元数据
```
**适用功能：**
- 配置文件模板
- 元数据 CSV 协作
- 上传日志共享

## 🚀 快速开始

### 1 分钟快速体验

```cmd
# 1. 安装依赖
pip install pyyaml watchdog requests

# 2. 使用默认配置（适合测试）
python update_with_ngrok.py
```

### 5 分钟完整配置

```cmd
# 1. 安装依赖
install_dependencies.bat

# 2. 配置 Dify 信息
notepad config.yaml

# 3. 启动增强版
start_enhanced.bat
```

### 详细配置流程

1. **编辑配置文件**
   ```yaml
   dify:
     base_url: "http://你的Dify地址"
     dataset_id: "你的数据集ID"
     api_key: "你的API密钥"
   ```

2. **准备元数据（可选）**
   ```csv
   id,title,source,keywords,year,region,type,category
   DOC-001,文档标题,来源,关键词,2025,地区,类型,分类
   ```

3. **放置文档**
   ```
   将文档放到监控文件夹
   （配置文件中的 document.watch_folder）
   ```

4. **启动监控**
   ```cmd
   python upload_enhanced.py
   ```

## 📖 文档索引

| 文档 | 内容 | 适合人群 |
|------|------|----------|
| [README_enhanced.md](README_enhanced.md) | 增强版使用说明 | 所有用户 |
| [README_segmentation.md](README_segmentation.md) | 分段与索引配置 | 需要自定义分段策略的用户 |
| [COMPARISON.md](COMPARISON.md) | 原版vs增强版对比 | 选择版本参考 |
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | 项目总览（本文档） | 了解项目全貌 |

## 🔧 配置参考

### 最小化配置
```yaml
dify:
  base_url: "http://localhost"
  dataset_id: "your-dataset-id"
  api_key: "your-api-key"

document:
  watch_folder: "C:\\Documents"
```

### 推荐配置
```yaml
dify:
  base_url: "http://192.168.40.128"
  dataset_id: "96e6249f-..."
  api_key: "dataset-xxx..."

mineru:
  enabled: true
  prefer_upload: true

metadata:
  enabled: true
  auto_create: true

database:
  enabled: true
  skip_uploaded: true

indexing:
  technique: "high_quality"
  separator: "###"
  max_tokens: 1500
```

### 高级配置
```yaml
# 完整配置参见 config.yaml
# 支持所有功能的详细定制
```

## 📊 支持的文件格式

### 文本文档
- ✅ `.txt` - 纯文本
- ✅ `.md`, `.markdown`, `.mdx` - Markdown
- ✅ `.html` - HTML 文档

### Office 文档
- ✅ `.doc`, `.docx` - Word 文档
- ✅ `.xls`, `.xlsx` - Excel 表格
- ✅ `.ppt`, `.pptx` - PowerPoint 演示
- ✅ `.csv` - CSV 数据

### PDF 与图片（需 OCR）
- ✅ `.pdf` - PDF 文档
- ✅ `.png`, `.jpg`, `.jpeg` - 图片
- ✅ `.tiff`, `.bmp` - 其他图片格式

### 其他格式
- ✅ `.eml`, `.msg` - 邮件
- ✅ `.xml` - XML 文档
- ✅ `.vtt` - 字幕文件
- ✅ `.properties` - 配置文件
- ✅ `.epub` - 电子书

## 🎯 最佳实践

### 1. 文件命名规范
```
推荐：2025-省级国土空间生态修复规划实施评估指南（试行）.pdf
推荐：安徽省全域土地综合整治工程实施方案编制大纲（试行）.pdf
避免：文档1.pdf, temp.docx, 新建文件夹.zip
```

### 2. 元数据维护
```csv
# 定期审查和完善元数据
# 保持分类一致性
# 使用标准化的关键词
```

### 3. 分段策略
```yaml
# 政策文件：按标题分段
separator: "###"
max_tokens: 1500

# 技术文档：按段落分段
separator: "\n\n"
max_tokens: 800

# 长篇文档：大段落
separator: "\n\n"
max_tokens: 2000
```

### 4. 日志管理
```sql
-- 定期备份数据库
cp upload_log.db upload_log.backup.db

-- 定期清理旧记录（可选）
DELETE FROM upload_log 
WHERE upload_time < date('now', '-180 days');
```

## 🐛 故障排查

### 问题 1：上传失败
```
检查清单：
☐ Dify 服务是否运行
☐ API Key 是否正确
☐ 网络连接是否正常
☐ 文件格式是否支持
☐ 文件大小是否超限
```

### 问题 2：OCR 不工作
```
检查清单：
☐ MinerU API Key 是否有效
☐ mineru.enabled 是否为 true
☐ 文件是否在 ocr_extensions 列表中
☐ 网络能否访问 MinerU 服务
```

### 问题 3：重复上传
```
解决方案：
1. 检查 database.enabled 是否为 true
2. 检查 database.skip_uploaded 是否为 true
3. 清空数据库后重新上传：删除 upload_log.db
```

### 问题 4：元数据不匹配
```
解决方案：
1. 检查 CSV 文件的 title 字段
2. 尝试简化文件名（去除特殊符号）
3. 启用 auto_create 让系统自动创建
4. 查看日志中的匹配信息
```

## 📈 性能优化

### 大批量上传优化
```yaml
# 调整并发设置（如果使用多线程版本）
performance:
  max_workers: 4
  chunk_size: 10
  
# 使用经济模式加快索引
indexing:
  technique: "economy"
```

### 网络优化
```yaml
# 启用 MinerU 直传
mineru:
  prefer_upload: true
  disable_url_fallback: true

# 调整重试策略（在代码中）
retry_times: 3
retry_delay: 2
```

## 🔐 安全建议

### 配置文件保护
```
# .gitignore
config.yaml          # 不要提交包含密钥的配置
upload_log.db        # 不要提交上传历史
*.key                # 不要提交密钥文件

# 提供模板
config.yaml.example  # 可以提交的配置模板
```

### API Key 管理
```yaml
# 使用环境变量（推荐）
api_key: "${DIFY_API_KEY}"

# 或使用文件引用
api_key_file: "./secrets/dify_key.txt"
```

## 🚧 开发路线图

### 已完成 ✅
- [x] 基础文件监控和上传
- [x] MinerU OCR 集成
- [x] 配置文件支持
- [x] 元数据管理
- [x] SQLite 日志系统
- [x] 智能分段与索引

### 计划中 🔄
- [ ] Web 控制面板
- [ ] 多线程并发上传
- [ ] 增量更新检测
- [ ] 文档版本管理
- [ ] 导出功能（导出元数据、日志）
- [ ] 更多 OCR 服务支持

### 未来展望 💡
- [ ] Docker 容器化部署
- [ ] RESTful API 接口
- [ ] 定时任务调度
- [ ] 邮件/钉钉通知
- [ ] 文档去重检测
- [ ] 智能标签推荐

## 🤝 贡献与支持

### 报告问题
如遇到问题，请提供：
1. 错误日志
2. 配置文件（脱敏）
3. 文件类型和大小
4. 运行环境（Python 版本、操作系统）

### 功能建议
欢迎提出改进建议：
1. 描述使用场景
2. 说明期望功能
3. 提供示例（如果有）

## 📚 相关资源

### 官方文档
- [Dify 官方文档](https://docs.dify.ai/)
- [MinerU 文档](https://mineru.net/docs)

### 相关项目
- watchdog: 文件系统监控
- pyyaml: YAML 配置解析
- requests: HTTP 请求库

### 社区资源
- Dify 社区论坛
- GitHub Issues

## 📄 许可证

本项目仅供学习和内部使用。

---

**最后更新：** 2025-11-17  
**版本：** 2.0 (Enhanced Version)  
**维护者：** Dify-Gengxin Team
