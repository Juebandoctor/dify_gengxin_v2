"""
Dify 知识库实时监控模块
定期检查 Dify 知识库变化，自动同步删除本地元数据
"""
import threading
import time
import requests
from utils.logger import log_info, log_success, log_warning, log_error


class DifyMonitor(threading.Thread):
    """Dify 知识库监控线程"""
    
    def __init__(self, config, upload_logger, metadata_manager, check_interval=60):
        """
        初始化 Dify 监控器
        
        Args:
            config: 配置字典
            upload_logger: 上传日志管理器
            metadata_manager: 元数据管理器
            check_interval: 检查间隔（秒），默认 60 秒
        """
        super().__init__()
        self.daemon = True  # 设为守护线程，主程序退出时自动结束
        self.config = config
        self.upload_logger = upload_logger
        self.metadata_manager = metadata_manager
        self.check_interval = check_interval
        self.running = False
        
        # Dify API 配置
        self.base_url = config['dify']['base_url']
        self.dataset_id = config['dify']['dataset_id']
        self.api_key = config['dify']['api_key']
        self.headers = {'Authorization': f'Bearer {self.api_key}'}
        self.url = f"{self.base_url}/v1/datasets/{self.dataset_id}/documents"
        
        # 缓存当前文档列表（用于检测变化）
        self.current_doc_ids = set()
        self.current_doc_names = set()
        self.current_docs_map = {}  # {doc_id: doc_name} 映射，用于调试
        self.last_documents = []    # 最近一次成功获取的完整文档列表
        
        # 统计信息
        self.check_count = 0
        self.total_deleted = 0
    
    def get_dify_documents(self):
        """从 Dify 获取所有文档信息"""
        try:
            all_documents = []
            page = 1
            
            while True:
                response = None
                for attempt in range(1, 4):
                    try:
                        response = requests.get(
                            self.url,
                            headers=self.headers,
                            params={'page': page, 'limit': 100},
                            timeout=30
                        )
                        if response.status_code == 200:
                            break
                        log_warning(
                            f"[监控] 获取 Dify 文档列表失败: HTTP {response.status_code} (尝试 {attempt}/3)"
                        )
                        log_warning(f"[监控] 响应: {response.text[:200]}")
                    except requests.RequestException as req_err:
                        log_warning(
                            f"[监控] 请求异常 (尝试 {attempt}/3): {str(req_err)[:200]}"
                        )
                    time.sleep(min(5 * attempt, self.check_interval))
                else:
                    if self.last_documents:
                        log_warning("[监控] 多次尝试获取 Dify 文档列表仍失败，使用上一次成功的结果")
                        # 增加冷却时间，避免频繁请求
                        time.sleep(30)
                        return list(self.last_documents)
                    log_error("[监控] 多次尝试获取 Dify 文档列表仍失败，放弃本轮同步")
                    # 增加冷却时间
                    time.sleep(30)
                    return None
                
                if response is None:
                    return None

                data = response.json()
                documents = data.get('data', [])
                
                if not documents:
                    break
                
                for doc in documents:
                    if doc.get('id'):
                        all_documents.append({
                            'id': doc.get('id'),
                            'name': doc.get('name', '')
                        })
                
                if len(documents) < 100:
                    break
                
                page += 1
            
            self.last_documents = list(all_documents)
            return all_documents
            
        except Exception as e:
            log_warning(f"[监控] 获取文档列表出错: {e}")
            if self.last_documents:
                log_warning("[监控] 使用缓存的文档列表继续")
                return list(self.last_documents)
            return None
    
    def process_document_name(self, name):
        """处理文档名称，移除扩展名"""
        if name.endswith('_ocr.md'):
            return name[:-7]
        elif '.' in name:
            import os
            return os.path.splitext(name)[0]
        return name
    
    def sync_deletions(self, deleted_doc_ids, deleted_doc_names):
        """
        同步删除指定的文档记录
        
        Args:
            deleted_doc_ids: 删除的文档 ID 集合
            deleted_doc_names: 删除的文档名称集合
        """
        if not deleted_doc_ids and not deleted_doc_names:
            return 0, 0
        
        # 1. 同步数据库（通过文档 ID 精确删除）
        db_deleted = 0
        if self.upload_logger and deleted_doc_ids:
            for doc_id in deleted_doc_ids:
                if self.upload_logger.delete_by_dify_doc_id(doc_id):
                    db_deleted += 1
            log_info(f"[监控] 数据库删除：{db_deleted}/{len(deleted_doc_ids)} 条")
        
        # 2. 同步元数据表（通过文件名匹配删除）
        csv_deleted = 0
        if self.metadata_manager and deleted_doc_names:
            local_titles = self.metadata_manager.get_all_titles()
            to_delete = []
            
            for deleted_name in deleted_doc_names:
                # 标准化删除的文档名
                normalized_deleted = deleted_name.replace('《', '').replace('》', '').replace('（', '').replace('）', '').strip()
                
                # 在本地元数据中查找匹配的记录
                for title in local_titles:
                    normalized_title = title.replace('《', '').replace('》', '').replace('（', '').replace('）', '').strip()
                    
                    # 匹配规则：完全匹配或包含关系
                    if (normalized_title == normalized_deleted or 
                        normalized_deleted in normalized_title or 
                        normalized_title in normalized_deleted):
                        if title not in to_delete:
                            to_delete.append(title)
                            log_info(f"[监控] 匹配元数据：'{deleted_name}' → '{title}'")
                        break
            
            if to_delete:
                csv_deleted = self.metadata_manager.delete_by_titles(to_delete)
                log_info(f"[监控] 元数据表删除：{csv_deleted} 条")
            else:
                log_warning(f"[监控] 未找到匹配的元数据记录（{len(deleted_doc_names)} 个删除的文档）")
        
        return db_deleted, csv_deleted
    
    def check_for_changes(self):
        """检查 Dify 知识库变化"""
        try:
            # 获取当前文档列表
            current_docs = self.get_dify_documents()
            if current_docs is None:
                return
            
            # 构建当前文档的 ID、名称集合和映射
            current_ids = set()
            current_names = set()
            current_docs_map = {}
            
            for doc in current_docs:
                doc_id = doc['id']
                doc_name = doc['name']
                processed_name = self.process_document_name(doc_name)
                
                current_ids.add(doc_id)
                current_names.add(processed_name)
                current_docs_map[doc_id] = processed_name
            
            # 首次运行，只记录状态
            if self.check_count == 0:
                self.current_doc_ids = current_ids
                self.current_doc_names = current_names
                self.current_docs_map = current_docs_map
                log_info(f"[监控] 初始化完成，当前 {len(current_docs)} 个文档")
                self.check_count += 1
                return
            
            # 检测删除的文档
            deleted_ids = self.current_doc_ids - current_ids
            deleted_names = self.current_doc_names - current_names
            
            if deleted_ids or deleted_names:
                log_warning(f"[监控] 检测到文档删除：{len(deleted_ids)} 个文档")
                
                # 显示删除的文档名（从缓存的映射中获取）
                deleted_doc_info = []
                for doc_id in deleted_ids:
                    doc_name = self.current_docs_map.get(doc_id, '未知')
                    deleted_doc_info.append(f"{doc_name} ({doc_id[:8]}...)")
                
                if deleted_doc_info:
                    log_info(f"[监控] 删除的文档：")
                    for i, info in enumerate(deleted_doc_info[:5], 1):
                        log_info(f"[监控]   {i}. {info}")
                    if len(deleted_doc_info) > 5:
                        log_info(f"[监控]   ... 以及其他 {len(deleted_doc_info) - 5} 个")
                
                # 同步删除本地记录（传入删除的文档信息）
                db_deleted, csv_deleted = self.sync_deletions(deleted_ids, deleted_names)
                
                if db_deleted > 0 or csv_deleted > 0:
                    log_success(f"[监控] ✅ 自动同步：数据库 {db_deleted} 条，元数据表 {csv_deleted} 条")
                    self.total_deleted += db_deleted + csv_deleted
                else:
                    log_warning(f"[监控] ⚠️ 未删除任何本地记录（可能已经同步或未找到匹配）")
                
                # 更新缓存
                self.current_doc_ids = current_ids
                self.current_doc_names = current_names
                self.current_docs_map = current_docs_map
            
            # 检测新增的文档并创建默认元数据
            added_ids = current_ids - self.current_doc_ids
            if added_ids:
                log_info(f"[监控] 检测到新文档：{len(added_ids)} 个")
                
                # 为新增文档创建默认元数据
                added_count = 0
                skipped_count = 0
                for doc in current_docs:
                    if doc['id'] in added_ids:
                        doc_name = doc['name']
                        processed_name = self.process_document_name(doc_name)
                        
                        # 检查元数据是否已存在
                        existing = self.metadata_manager.get_by_title(processed_name)
                        if not existing:
                            # 从配置中获取默认值
                            default_config = self.config.get('metadata', {}).get('default', {})
                            
                            # 创建默认元数据
                            from datetime import datetime
                            default_meta = {
                                'title': processed_name,
                                'source': default_config.get('source', '未知来源'),
                                'keywords': default_config.get('keywords', ''),
                                'year': datetime.now().year,
                                'region': default_config.get('region', '全国'),
                                'type': default_config.get('type', '政策文件'),
                                'category': '其他',
                                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            
                            # 只添加到元数据表（不记录到数据库，因为没有原始文件）
                            if self.metadata_manager.add_metadata(default_meta):
                                added_count += 1
                                log_info(f"[监控]   创建：{processed_name}")
                        else:
                            skipped_count += 1
                
                if added_count > 0:
                    log_success(f"[监控] ✅ 自动创建元数据：{added_count} 条")
                if skipped_count > 0:
                    log_info(f"[监控] 跳过已存在的元数据：{skipped_count} 条")
                
                # 更新缓存
                self.current_doc_ids = current_ids
                self.current_doc_names = current_names
                self.current_docs_map = current_docs_map
            
            self.check_count += 1
            
        except Exception as e:
            log_error(f"[监控] 检查变化时出错: {e}")
    
    def run(self):
        """运行监控线程"""
        self.running = True
        log_info(f"[监控] Dify 知识库监控已启动，间隔 {self.check_interval} 秒")
        
        try:
            while self.running:
                self.check_for_changes()
                time.sleep(self.check_interval)
        except Exception as e:
            log_error(f"[监控] 监控线程异常: {e}")
        finally:
            log_info(f"[监控] 已停止（检查 {self.check_count} 次，同步删除 {self.total_deleted} 条）")
    
    def stop(self):
        """停止监控"""
        self.running = False
        log_info("[监控] 正在停止 Dify 监控...")
