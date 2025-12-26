"""
Dify 知识库自动上传脚本（PaddleOCR-VL 专用版 - 绝对静音拦截版）
"""
import os
import sys
import logging

# ==============================================================================
# 🔇 核心静音区：使用过滤器进行物理拦截
# ==============================================================================

# 1. 设置环境变量屏蔽 C++ 和 HuggingFace 默认日志
os.environ['GLOG_minloglevel'] = '2'
os.environ['PADDLEocr_LOG_LEVEL'] = 'ERROR'
os.environ['TRANSFORMERS_VERBOSITY'] = 'error'  # 直接告诉 HF 闭嘴

# 2. 定义拦截过滤器 (专门杀掉 pad_token_id 这行日志)
class IgnorePadTokenLog(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        # 只要日志里包含这两个关键词，直接丢弃
        if "pad_token_id" in msg and "eos_token_id" in msg:
            return False
        if "Non compatible API" in msg:
            return False
        return True

# 3. 将过滤器挂载到所有关键 Logger 上
# 无论它从哪里冒出来，都会被拦截
loggers_to_silence = [
    "transformers", 
    "transformers.generation.utils", 
    "ppocr", 
    "paddle", 
    "root"
]

for name in loggers_to_silence:
    logger = logging.getLogger(name)
    logger.setLevel(logging.ERROR)
    logger.addFilter(IgnorePadTokenLog())

# 强制对根 Logger 也应用拦截（防止漏网之鱼）
logging.getLogger().addFilter(IgnorePadTokenLog())

# ==============================================================================

import warnings
warnings.filterwarnings("ignore")

import requests
import json
import time
import re
import math
import copy
import tempfile
from tqdm import tqdm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- PaddleOCR-VL 依赖检测 ---
try:
    from paddleocr import PaddleOCRVL
    import fitz  # PyMuPDF
    PADDLE_AVAILABLE = True
except ImportError as e:
    PADDLE_AVAILABLE = False
    print(f"⚠️ PaddleOCR-VL 依赖缺失: {e}")

sys.path.insert(0, os.path.dirname(__file__))

try:
    from utils.config_loader import load_config, get_config_value
    from utils.metadata_manager import MetadataManager
    from utils.upload_logger import UploadLogger
    from utils.logger import log_info, log_success, log_error, log_warning, print_header
    from utils.dify_monitor import DifyMonitor
    
    PdfReader = None
    PdfWriter = None
    try:
        from PyPDF2 import PdfReader as _PdfReader, PdfWriter as _PdfWriter
        PdfReader = _PdfReader
        PdfWriter = _PdfWriter
        PDF_SPLIT_AVAILABLE = True
    except ImportError:
        PDF_SPLIT_AVAILABLE = False
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    sys.exit(1)


def ensure_pdf_split_available():
    global PDF_SPLIT_AVAILABLE
    return PDF_SPLIT_AVAILABLE


def normalize_title_for_compare(title):
    if not title: return ''
    normalized = re.sub(r'[\《\》\（\）\(\)\[\]\ ]', '', title)
    normalized = re.sub(r'PDF分段\s*\d+/\d+', '', normalized)
    normalized = re.sub(r'[_-]+chunk\d+', '', normalized, flags=re.IGNORECASE)
    return normalized.strip().lower()


class EnhancedFileHandler(FileSystemEventHandler):
    DEFAULT_SUPPORTED_EXTENSIONS = ('.txt', '.md', '.markdown', '.pdf', '.doc', '.docx', '.png', '.jpg', '.jpeg')
    
    def __init__(self, config, metadata_mgr, upload_logger):
        super().__init__()
        self.paddle_config = config.get('paddleocr', {})
        self.paddle_enabled = self.paddle_config.get('enabled', False) and PADDLE_AVAILABLE
        self.ocr_engine = None

        if self.paddle_enabled:
            log_info("🚀 正在加载 PaddleOCR-VL (0.9B)...")
            try:
                self.ocr_engine = PaddleOCRVL()
                log_success("✅ 模型加载完成")
            except Exception as e:
                log_error(f"❌ 初始化失败: {e}")
                self.paddle_enabled = False
        
        self.config = config
        self.metadata_mgr = metadata_mgr
        self.upload_logger = upload_logger
        doc_config = config['document']
        self.watch_dir = doc_config['watch_folder']
        self.ocr_output_dir = doc_config['output_dir']
        self.ocr_extensions = set(doc_config['ocr_extensions'])
        self.supported_extensions = tuple(doc_config.get('supported_extensions', self.DEFAULT_SUPPORTED_EXTENSIONS))
        self.max_file_size_mb = doc_config.get('max_file_size_mb', 600)
        self.skip_uploaded = get_config_value(config, 'database', 'skip_uploaded', default=True)
        self.markdown_chunk_size_mb = doc_config.get('markdown_chunk_size_mb', 20)
        self.markdown_min_chunk_size_mb = max(1, doc_config.get('markdown_min_chunk_size_mb', 2))
        self.upload_filename_max_length = doc_config.get('upload_filename_max_length', 120)
        
        self.pdf_split_enabled = doc_config.get('pdf_split_enabled', True)
        self.pdf_chunk_size_mb = doc_config.get('pdf_chunk_size_mb', 80)
        self.pdf_max_pages_per_chunk = doc_config.get('pdf_max_pages_per_chunk', 0)
        self.pdf_double_column_split_enabled = bool(doc_config.get('pdf_double_column_split_enabled', False))
        self.pdf_column_split_ratio = min(0.85, max(0.15, float(doc_config.get('pdf_column_split_ratio', 0.5))))

        self.preserve_original_filename_as_doc_name = bool(doc_config.get('preserve_original_filename_as_doc_name', False))
        self.keep_extension_in_doc_name = bool(doc_config.get('keep_extension_in_doc_name', True))
        self.append_chunk_suffix_to_name = bool(doc_config.get('append_chunk_suffix_to_name', True))

        self.dify_base_url = config['dify']['base_url'].rstrip('/')
        self.dataset_id = config['dify']['dataset_id']
        self.api_key = config['dify']['api_key']
        self.document_create_url = f"{self.dify_base_url}/v1/datasets/{self.dataset_id}/document/create-by-file"
        self.indexing_config = config['indexing']

        os.makedirs(self.ocr_output_dir, exist_ok=True)
        self._recent_events = {}

    def process_via_paddleocr(self, file_path):
        """核心处理：带进度条的 VL 解析"""
        if not self.ocr_engine: return None

        print(f"\n🚀 正在解析: {os.path.basename(file_path)}")
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_path = os.path.join(self.ocr_output_dir, f"{base_name}_ocr.md")
        
        if os.path.exists(output_path):
            log_warning(f"覆盖旧结果: {output_path}")

        try:
            result = self.ocr_engine.predict(file_path)
            full_markdown = ""
            
            try:
                total = len(result)
            except:
                total = None

            # 进度条显示逻辑
            with tqdm(result, total=total, unit="页", desc="⏳ 识别进度", ncols=90) as pbar:
                for res in pbar:
                    page_lines = []
                    parsing_list = None
                    
                    if isinstance(res, dict):
                        parsing_list = res.get('parsing_res_list')
                    elif hasattr(res, 'parsing_res_list'):
                        parsing_list = res.parsing_res_list
                    
                    if parsing_list:
                        for item in parsing_list:
                            text = ""
                            label = ""
                            if isinstance(item, dict):
                                text, label = item.get('content', ''), item.get('label', '')
                            else:
                                text, label = getattr(item, 'content', ''), getattr(item, 'label', '')
                            
                            text = str(text).strip()
                            if not text or label in ['footer', 'number', 'page_no']:
                                continue
                                
                            if label == 'doc_title': page_lines.append(f"# {text}")
                            elif label == 'paragraph_title': page_lines.append(f"\n## {text}")
                            elif label == 'table': page_lines.append(f"\n{text}\n")
                            elif label == 'figure': page_lines.append("> [图片]")
                            elif label != 'header': page_lines.append(text)
                    
                    elif hasattr(res, 'markdown') and res.markdown:
                         content = res.markdown
                         full_markdown += str(content.get('text', '') if isinstance(content, dict) else content) + "\n\n"
                         continue

                    if page_lines:
                        full_markdown += "\n\n".join(page_lines) + "\n\n"

            if not full_markdown.strip():
                log_error("❌ 未提取到文本")
                return None

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(full_markdown)
            
            log_success(f"✅ 解析完成: {output_path}")
            return output_path

        except Exception as e:
            log_error(f"处理失败: {e}")
            return None

    # --- 辅助方法 ---
    def _resolve_document_name(self, file_path, metadata=None):
        if self.preserve_original_filename_as_doc_name:
            base = os.path.basename(file_path)
            if not self.keep_extension_in_doc_name: base = os.path.splitext(base)[0]
            return base.strip() or "未命名"
        name = (metadata or {}).get('title') or os.path.splitext(os.path.basename(file_path))[0]
        return name.strip()[:180] or "未命名"

    def _get_file_size_mb(self, path):
        try: return os.path.getsize(path) / (1024*1024)
        except: return 0

    def _format_size(self, size): return f"{size:.2f} MB"

    def _is_recently_processed(self, path, window=5):
        now = time.time()
        if now - self._recent_events.get(path, 0) < window: return True
        self._recent_events[path] = now
        return False

    def _get_metadata(self, path):
        if not self.metadata_mgr: return None
        return self.metadata_mgr.get_metadata(path)

    def _record_upload_success(self, path, doc_id, meta):
        if self.upload_logger: self.upload_logger.log_upload(path, doc_id, 'success', meta)

    def _record_upload_failure(self, path, err, meta):
        if self.upload_logger: 
            m = {'error': err}
            if meta: m.update({k:v for k,v in meta.items() if v})
            self.upload_logger.log_upload(path, None, 'failed', m)

    def _is_internal_chunk(self, name):
        return bool(re.search(r'(_pdfchunk|_ocr_chunk|_chunk)\d{3}', name.lower()))

    def on_created(self, event):
        if not event.is_directory: self.process_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory: self.process_file(event.src_path)

    def process_file(self, file_path, force=False):
        try:
            if not os.path.exists(file_path) or os.path.isdir(file_path): return
            if not force and self._is_recently_processed(file_path): return
            if self._is_internal_chunk(os.path.basename(file_path)): return

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in self.supported_extensions: return
            if self.skip_uploaded and self.upload_logger and self.upload_logger.is_uploaded(file_path):
                log_info(f"跳过已上传: {os.path.basename(file_path)}")
                return

            meta = self._get_metadata(file_path)
            if ext in self.ocr_extensions: self._handle_ocr_file(file_path, meta)
            elif ext in ['.md', '.txt']: self._handle_markdown_file(file_path, meta)
            else: self._handle_regular_file(file_path, meta)
        except Exception as e:
            log_error(f"处理出错: {e}")

    def _handle_ocr_file(self, file_path, meta):
        size = self._get_file_size_mb(file_path)
        log_info(f"处理文件: {os.path.basename(file_path)} ({self._format_size(size)})")
        
        if not self.paddle_enabled:
            return self._handle_regular_file(file_path, meta)

        is_pdf = file_path.lower().endswith('.pdf')
        ocr_input = file_path
        
        pdf_chunks = []
        if is_pdf and self.pdf_split_enabled:
            pdf_chunks = self._split_pdf_file(file_path, self.pdf_chunk_size_mb)
        
        if pdf_chunks:
            log_success(f"PDF 已切分为 {len(pdf_chunks)} 个部分")
            for idx, chunk in enumerate(pdf_chunks, 1):
                chunk_meta = meta.copy() if meta else {}
                chunk_meta.update({'chunk_index': idx, 'chunk_total': len(pdf_chunks)})
                display = f"{self._resolve_document_name(file_path, meta)} (分段 {idx}/{len(pdf_chunks)})"
                
                res_path = self.process_via_paddleocr(chunk)
                if res_path: self._handle_markdown_file(res_path, chunk_meta, display)
                try: os.remove(chunk) 
                except: pass
        else:
            res_path = self.process_via_paddleocr(ocr_input)
            if res_path: self._handle_markdown_file(res_path, meta)

    def _handle_markdown_file(self, file_path, meta, display=None):
        if self._get_file_size_mb(file_path) > self.markdown_chunk_size_mb:
            self._upload_with_chunking(file_path, meta, display)
        else:
            self._handle_regular_file(file_path, meta, display)

    def _handle_regular_file(self, file_path, meta, display=None):
        doc_id, err = self.upload_to_dify(file_path, meta, display)
        if doc_id: 
            log_success(f"上传成功: {os.path.basename(file_path)}")
            self._record_upload_success(file_path, doc_id, meta)
        else:
            log_error(f"上传失败: {err}")
            self._record_upload_failure(file_path, err, meta)

    def _split_pdf_file(self, file_path, target_mb):
        if not ensure_pdf_split_available(): return []
        try:
            reader = PdfReader(file_path)
            total = len(reader.pages)
            if total == 0: return []
            
            size = max(0.01, self._get_file_size_mb(file_path))
            if size <= target_mb: return []

            step = max(1, math.ceil(total * target_mb / size))
            chunks = []
            base = os.path.splitext(file_path)[0]
            
            print(f"📦 切分 PDF (共 {total} 页)...")
            with tqdm(total=total, unit="页", desc="✂️ 切分进度", ncols=90) as pbar:
                for i in range(0, total, step):
                    writer = PdfWriter()
                    end = min(i + step, total)
                    for p in range(i, end): writer.add_page(reader.pages[p])
                    
                    out = f"{base}_pdfchunk{(i//step)+1:03d}.pdf"
                    with open(out, 'wb') as f: writer.write(f)
                    chunks.append(out)
                    pbar.update(end - i)
            return chunks
        except Exception as e:
            log_error(f"PDF 切分失败: {e}")
            return []

    def _upload_with_chunking(self, file_path, meta, display):
        return False

    def _build_process_rule(self):
        c = self.indexing_config
        return c.get('technique', 'high_quality'), {
            "mode": "custom",
            "rules": {"pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": True}],
                      "segmentation": {"separator": "###", "max_tokens": 1000, "chunk_overlap": 50}}
        }

    def upload_to_dify(self, file_path, meta, display_name):
        try:
            tech, rule = self._build_process_rule()
            headers = {'Authorization': f'Bearer {self.api_key}'}
            name = display_name or self._resolve_document_name(file_path, meta)
            
            data = {"name": name, "source": "upload_file", "doc_type": "text", 
                    "doc_language": "ch", "indexing_technique": tech, "process_rule": rule}
            if meta: data["metadata"] = {k:v for k,v in meta.items() if v}

            files = [('file', (os.path.basename(file_path), open(file_path, 'rb'), 'text/markdown')),
                     ('data', (None, json.dumps(data), 'application/json'))]
            
            resp = requests.post(self.document_create_url, headers=headers, files=files, timeout=300)
            if resp.status_code in (200, 201): return resp.json().get('document', {}).get('id'), None
            return None, resp.json().get('code', f"http_{resp.status_code}")
        except Exception as e:
            return None, str(e)


def start_monitoring(config, mgr, logger):
    path = config['document']['watch_folder']
    handler = EnhancedFileHandler(config, mgr, logger)
    obs = Observer()
    obs.schedule(handler, path, recursive=True)
    obs.start()
    
    log_info(f"监控启动: {path}")
    monitor = None
    if config.get('monitor', {}).get('enabled', True):
        monitor = DifyMonitor(config, logger, mgr, 60)
        monitor.start()
    
    try:
        log_info("扫描现有文件...")
        # 确保这里使用全局导入的 os
        for root, _, files in os.walk(path):
            for f in files:
                if f.lower().endswith(handler.supported_extensions):
                    print("-" * 40)
                    handler.process_file(os.path.join(root, f))
        
        log_success("扫描完成，等待新文件...")
        while True: obs.join(1)
    except KeyboardInterrupt:
        print("\n")
        log_warning("🛑 强制停止...")
        if monitor: monitor.stop()
        obs.stop()
        # 确保这里使用全局导入的 os
        os._exit(0)
    except Exception as e:
        log_error(f"错误: {e}")

def main():
    print_header("Dify 上传工具 (PaddleOCR-VL 拦截版)")
    try:
        config = load_config("config.yaml")
        if config.get('metadata', {}).get('enabled', True):
            mgr = MetadataManager(config['metadata']['csv_path'], True, {}, config)
        else: mgr = None
        
        logger = None
        if config.get('database', {}).get('enabled', True):
            logger = UploadLogger(config['database']['sqlite_path'])
            
        start_monitoring(config, mgr, logger)
    except Exception as e:
        log_error(f"启动失败: {e}")

if __name__ == "__main__":
    main()