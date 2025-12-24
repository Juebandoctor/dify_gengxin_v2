"""
SQLite 日志管理模块
记录文件上传历史，避免重复处理
"""
import os
import sqlite3
import hashlib
from datetime import datetime


class UploadLogger:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        # 创建上传日志表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS upload_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT,
                file_size INTEGER,
                upload_time TEXT NOT NULL,
                dify_doc_id TEXT,
                status TEXT DEFAULT 'success',
                metadata TEXT
            )
        """)
        
        # 创建索引
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_hash 
            ON upload_log(file_hash)
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_time 
            ON upload_log(upload_time)
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_dify_doc_id 
            ON upload_log(dify_doc_id)
        """)
        
        conn.commit()
        conn.close()
    
    def calculate_file_hash(self, file_path):
        """计算文件的 MD5 哈希"""
        try:
            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                # 分块读取，避免大文件内存溢出
                while chunk := f.read(8192):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            print(f"⚠️ 计算文件哈希失败: {e}")
            return None
    
    def is_uploaded(self, file_path):
        """检查文件是否已上传"""
        file_hash = self.calculate_file_hash(file_path)
        if not file_hash:
            return False
        
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute(
            "SELECT 1 FROM upload_log WHERE file_hash = ? AND status = 'success'",
            (file_hash,)
        )
        result = cur.fetchone()
        conn.close()
        
        return bool(result)
    
    def log_upload(self, file_path, dify_doc_id=None, status='success', metadata=None):
        """记录上传日志"""
        file_hash = self.calculate_file_hash(file_path)
        if not file_hash:
            return False
        
        try:
            file_size = os.path.getsize(file_path)
        except:
            file_size = 0
        
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        try:
            cur.execute("""
                INSERT OR REPLACE INTO upload_log 
                (file_hash, file_name, file_path, file_size, upload_time, dify_doc_id, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_hash,
                os.path.basename(file_path),
                file_path,
                file_size,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                dify_doc_id,
                status,
                str(metadata) if metadata else None
            ))
            conn.commit()
            return True
        except Exception as e:
            print(f"⚠️ 记录上传日志失败: {e}")
            return False
        finally:
            conn.close()
    
    def get_upload_history(self, limit=100):
        """获取上传历史"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT file_name, file_path, upload_time, status, dify_doc_id
            FROM upload_log
            ORDER BY upload_time DESC
            LIMIT ?
        """, (limit,))
        
        results = cur.fetchall()
        conn.close()
        
        return results
    
    def get_statistics(self):
        """获取统计信息"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        # 总上传数
        cur.execute("SELECT COUNT(*) FROM upload_log WHERE status='success'")
        total_success = cur.fetchone()[0]
        
        # 失败数
        cur.execute("SELECT COUNT(*) FROM upload_log WHERE status!='success'")
        total_failed = cur.fetchone()[0]
        
        # 总文件大小
        cur.execute("SELECT SUM(file_size) FROM upload_log WHERE status='success'")
        total_size = cur.fetchone()[0] or 0
        
        conn.close()
        
        return {
            'total_success': total_success,
            'total_failed': total_failed,
            'total_size_mb': round(total_size / 1024 / 1024, 2)
        }
    
    def delete_by_dify_doc_id(self, doc_id):
        """根据 Dify 文档 ID 删除日志记录"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM upload_log WHERE dify_doc_id=?", (doc_id,))
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"⚠️ 删除日志记录失败: {e}")
            return False
        finally:
            conn.close()
    
    def delete_by_file_path(self, file_path):
        """根据文件路径删除日志记录"""
        file_hash = self.calculate_file_hash(file_path)
        if not file_hash:
            return False
        
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM upload_log WHERE file_hash=?", (file_hash,))
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            print(f"⚠️ 删除日志记录失败: {e}")
            return False
        finally:
            conn.close()
    
    def get_all_dify_doc_ids(self):
        """获取所有已记录的 Dify 文档 ID"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute("SELECT dify_doc_id FROM upload_log WHERE dify_doc_id IS NOT NULL AND status='success'")
        results = [row[0] for row in cur.fetchall()]
        conn.close()
        
        return results
    
    def sync_with_dify(self, existing_doc_ids):
        """
        与 Dify 同步，删除在日志中但不在 Dify 中的记录
        
        Args:
            existing_doc_ids: Dify 中实际存在的文档 ID 列表
        
        Returns:
            删除的记录数
        """
        local_doc_ids = self.get_all_dify_doc_ids()
        to_delete = set(local_doc_ids) - set(existing_doc_ids)
        
        if not to_delete:
            return 0
        
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        deleted_count = 0
        try:
            for doc_id in to_delete:
                cur.execute("DELETE FROM upload_log WHERE dify_doc_id=?", (doc_id,))
                deleted_count += cur.rowcount
            conn.commit()
        except Exception as e:
            print(f"⚠️ 同步删除失败: {e}")
            conn.rollback()
        finally:
            conn.close()
        
        return deleted_count
    
    def mark_failed(self, file_path, error_msg=None):
        """标记上传失败"""
        return self.log_upload(file_path, status='failed', metadata={'error': error_msg})
