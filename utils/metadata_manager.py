"""
元数据管理模块
负责加载、匹配和更新文档元数据
"""
import os
import csv
import re
from datetime import datetime
from uuid import uuid4


class MetadataManager:
    def __init__(self, csv_path, auto_create=True, default_meta=None, config=None):
        self.csv_path = csv_path
        self.auto_create = auto_create
        self.default_meta = default_meta or {}
        self.config = config  # 保存完整配置，用于监控功能
        self.metadata_map = {}
        self._lookup_map = {}
        self._existing_ids = set()
        
        # 确保目录存在
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        
        # 如果文件不存在则创建
        if not os.path.exists(csv_path):
            self._create_empty_csv()
        
        self.load()
    
    def _create_empty_csv(self):
        """创建空的 CSV 文件"""
        with open(self.csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'title', 'source', 'keywords', 'year', 'region', 'type', 'category', 'created_at'])
    
    def load(self):
        """加载元数据表格"""
        self.metadata_map = {}
        self._lookup_map = {}
        self._existing_ids = set()
        dirty = False
        try:
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    title = (row.get('title') or '').strip()
                    if not title:
                        continue

                    if self._should_ignore_title(title):
                        dirty = True
                        continue

                    meta_id = (row.get('id') or '').strip()
                    if not meta_id or meta_id in self._existing_ids:
                        row['id'] = self._generate_unique_id()
                        dirty = True
                    self._existing_ids.add(row['id'])

                    canonical_title = self._canonicalize_title(title)
                    if canonical_title and canonical_title != title:
                        row['title'] = canonical_title
                        title = canonical_title
                        dirty = True

                    normalized_title = self._normalize_title(title)
                    self.metadata_map[title] = row
                    self._register_lookup_keys(title, normalized_title)
        except Exception as e:
            print(f"⚠️ 加载元数据失败: {e}")

        if dirty:
            self._save_all()
    
    def get_metadata(self, file_path):
        """
        获取文件的元数据
        如果找不到，根据配置自动创建
        """
        # 提取文件名（不含扩展名）
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        
        candidates = self._generate_title_candidates(base_name)

        for candidate in candidates:
            if candidate in self.metadata_map:
                return self.metadata_map[candidate]
            mapped = self._lookup_map.get(candidate)
            if mapped and mapped in self.metadata_map:
                return self.metadata_map[mapped]
        
        # 如果启用自动创建，生成默认元数据
        if self.auto_create:
            preferred_title = candidates[0] if candidates else base_name
            return self._create_default_metadata(preferred_title, file_path)
        
        return None
    
    def _normalize_title(self, title):
        """标准化标题用于匹配"""
        # 移除常见的特殊字符
        chars_to_remove = ['《', '》', '（', '）', '(', ')', '[', ']', '-', '_', ' ']
        normalized = title
        for char in chars_to_remove:
            normalized = normalized.replace(char, '')
        normalized = re.sub(r'(pdfchunk|chunk|ocr|sub|part)\d+', '', normalized, flags=re.IGNORECASE)
        return normalized.lower()

    def _canonicalize_title(self, title):
        if not title:
            return ''
        pattern = re.compile(r'(?:_ocr(?:_chunk\d+)?|_chunk\d+|_pdfchunk\d+|_sub\d+|_part\d+|_split\d+)+$', re.IGNORECASE)
        canonical = title
        while True:
            new_value = pattern.sub('', canonical)
            if new_value == canonical:
                break
            canonical = new_value
        return canonical.strip('_- ')

    def _generate_title_candidates(self, base_name):
        if not base_name:
            return []
        candidates = [base_name]
        canonical = self._canonicalize_title(base_name)
        if canonical and canonical not in candidates:
            candidates.append(canonical)
        normalized = self._normalize_title(base_name)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        return candidates

    def _register_lookup_keys(self, title, normalized_title=None):
        if title:
            self._lookup_map[title] = title
            canonical = self._canonicalize_title(title)
            if canonical:
                self._lookup_map.setdefault(canonical, title)
        if normalized_title:
            self._lookup_map.setdefault(normalized_title, title)

    def _rebuild_lookup(self):
        self._lookup_map = {}
        for title in self.metadata_map.keys():
            self._register_lookup_keys(title, self._normalize_title(title))

    def _should_ignore_title(self, title):
        return bool(re.match(r'^doc_\d{6,}$', title or '', re.IGNORECASE))

    def _generate_unique_id(self):
        base = f"AUTO-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        if base not in self._existing_ids:
            self._existing_ids.add(base)
            return base
        while True:
            candidate = f"AUTO-{uuid4().hex[:12]}"
            if candidate not in self._existing_ids:
                self._existing_ids.add(candidate)
                return candidate
    
    def _create_default_metadata(self, title, file_path):
        """创建默认元数据"""
        # 提取年份（如果标题中包含）
        year_match = re.search(r'(20\d{2})', title)
        year = year_match.group(1) if year_match else str(datetime.now().year)
        
        canonical_title = self._canonicalize_title(title) or title
        if self._should_ignore_title(canonical_title):
            print(f"⚠️ 检测到占位标题 {title}，跳过自动创建元数据")
            return None

        # 生成唯一 ID
        doc_id = self._generate_unique_id()
        
        meta = {
            'id': doc_id,
            'title': canonical_title,
            'source': self.default_meta.get('source', '未知来源'),
            'keywords': self.default_meta.get('keywords', ''),
            'year': year,
            'region': self.default_meta.get('region', '全国'),
            'type': self.default_meta.get('type', '文档'),
            'category': self._guess_category(title),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 添加到映射并保存
        self.metadata_map[canonical_title] = meta
        self._register_lookup_keys(canonical_title, self._normalize_title(canonical_title))
        self._append_to_csv(meta)
        
        print(f"✨ 自动创建元数据: {canonical_title}")
        return meta
    
    def _guess_category(self, title):
        """根据标题猜测分类"""
        keywords_map = {
            '生态修复': '生态修复',
            '矿山': '矿山修复',
            '土地整治': '土地整治',
            '国土空间': '国土规划',
            '政策': '政策文件',
            '指南': '技术指南',
            '大纲': '编制大纲',
            '规范': '行业标准',
            '评估': '评估指南'
        }
        
        for keyword, category in keywords_map.items():
            if keyword in title:
                return category
        
        return '其他'
    
    def _append_to_csv(self, meta):
        """追加新元数据到 CSV"""
        try:
            with open(self.csv_path, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    meta.get('id', ''),
                    meta.get('title', ''),
                    meta.get('source', ''),
                    meta.get('keywords', ''),
                    meta.get('year', ''),
                    meta.get('region', ''),
                    meta.get('type', ''),
                    meta.get('category', ''),
                    meta.get('created_at', '')
                ])
        except Exception as e:
            print(f"⚠️ 保存元数据失败: {e}")
    
    def update_metadata(self, title, **kwargs):
        """更新指定文档的元数据"""
        if title in self.metadata_map:
            self.metadata_map[title].update(kwargs)
            self._save_all()
            return True
        return False
    
    def add_metadata(self, meta):
        """
        添加新的元数据记录
        
        Args:
            meta: 元数据字典，必须包含 'title' 字段
        
        Returns:
            是否添加成功
        """
        title = meta.get('title')
        if not title:
            return False
        
        # 如果已存在，则更新
        if title in self.metadata_map:
            self.metadata_map[title].update(meta)
            self._save_all()
            return True
        
        # 生成 ID（如果没有）
        if 'id' not in meta or not meta['id']:
            meta['id'] = self._generate_unique_id()
        
        # 生成创建时间（如果没有）
        if 'created_at' not in meta or not meta['created_at']:
            meta['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 添加到映射
        self.metadata_map[title] = meta
        self._register_lookup_keys(title, self._normalize_title(title))
        
        # 追加到 CSV
        self._append_to_csv(meta)
        
        return True
    
    def get_by_title(self, title):
        """根据标题获取元数据"""
        return self.metadata_map.get(title)
    
    def _save_all(self):
        """保存所有元数据到 CSV"""
        try:
            with open(self.csv_path, 'w', encoding='utf-8', newline='') as f:
                fieldnames = ['id', 'title', 'source', 'keywords', 'year', 'region', 'type', 'category', 'created_at']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for meta in self.metadata_map.values():
                    writer.writerow(meta)
            self._rebuild_lookup()
        except Exception as e:
            print(f"⚠️ 保存元数据失败: {e}")
    
    def delete_by_title(self, title):
        """根据标题删除元数据"""
        if title in self.metadata_map:
            del self.metadata_map[title]
            self._save_all()
            return True
        return False
    
    def delete_by_titles(self, titles):
        """批量删除元数据"""
        deleted_count = 0
        for title in titles:
            if title in self.metadata_map:
                del self.metadata_map[title]
                deleted_count += 1
        
        if deleted_count > 0:
            self._save_all()
        
        return deleted_count
    
    def get_all_titles(self):
        """获取所有元数据标题"""
        return list(self.metadata_map.keys())
    
    def count(self):
        """获取元数据总数"""
        return len(self.metadata_map)
