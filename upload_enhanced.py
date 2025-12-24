"""
Dify 知识库自动上传脚本（增强版）
支持：配置文件、元数据管理、SQLite 日志、MinerU OCR、PDF分割
"""
import os
import sys
import requests
import json
import time
import shutil
import tempfile
import re
import math
import copy
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 添加 utils 到路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    from utils.config_loader import load_config, get_config_value
    from utils.metadata_manager import MetadataManager
    from utils.upload_logger import UploadLogger
    from utils.logger import log_info, log_success, log_error, log_warning, print_header
    from utils.dify_monitor import DifyMonitor
    # 导入 PyPDF2 用于 PDF 分割
    PdfReader = None
    PdfWriter = None
    try:
        from PyPDF2 import PdfReader as _PdfReader, PdfWriter as _PdfWriter
        PdfReader = _PdfReader
        PdfWriter = _PdfWriter
        PDF_SPLIT_AVAILABLE = True
    except ImportError:
        PDF_SPLIT_AVAILABLE = False
        log_warning("PyPDF2 未安装，PDF 分割功能不可用。安装：pip install PyPDF2")
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    print("请确保安装了必要的依赖: pip install pyyaml watchdog requests PyPDF2")
    sys.exit(1)


def ensure_pdf_split_available():
    """确保 PyPDF2 可用，必要时尝试惰性导入"""
    global PDF_SPLIT_AVAILABLE, PdfReader, PdfWriter
    if PDF_SPLIT_AVAILABLE and PdfReader and PdfWriter:
        return True
    try:
        from PyPDF2 import PdfReader as _PdfReader, PdfWriter as _PdfWriter
        PdfReader = _PdfReader
        PdfWriter = _PdfWriter
        PDF_SPLIT_AVAILABLE = True
        log_info("PyPDF2 已成功加载，PDF 分割功能已启用")
        return True
    except ImportError:
        PDF_SPLIT_AVAILABLE = False
        log_warning("仍未检测到 PyPDF2，PDF 分割不可用。执行 pip install PyPDF2 以启用该功能")
        return False


def normalize_title_for_compare(title):
    if not title:
        return ''
    normalized = title
    replacements = ['《', '》', '（', '）', '(', ')', '[', ']', ' ']
    for ch in replacements:
        normalized = normalized.replace(ch, '')
    normalized = re.sub(r'PDF分段\s*\d+/\d+', '', normalized)
    normalized = re.sub(r'分段\s*\d+/\d+', '', normalized)
    normalized = re.sub(r'子段\s*\d+/\d+', '', normalized)
    normalized = re.sub(r'第\d+部分', '', normalized)
    normalized = re.sub(r'[_-]+chunk\d+', '', normalized, flags=re.IGNORECASE)
    normalized = normalized.replace('（', '').replace('）', '')
    return normalized.strip().lower()


class EnhancedFileHandler(FileSystemEventHandler):
    """增强的文件处理器，支持元数据、OCR、切分与上传回退"""

    DEFAULT_SUPPORTED_EXTENSIONS = (
        '.txt', '.md', '.markdown', '.mdx', '.html',
        '.pdf', '.doc', '.docx',
        '.xlsx', '.xls', '.csv',
        '.ppt', '.pptx',
        '.eml', '.msg',
        '.xml', '.vtt', '.properties',
        '.epub',
        '.png', '.jpg', '.jpeg', '.tiff', '.bmp'
    )

    def __init__(self, config, metadata_mgr, upload_logger):
        super().__init__()
        self.config = config
        self.metadata_mgr = metadata_mgr
        self.upload_logger = upload_logger

        # 文档/目录配置
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
        self.pdf_split_retry_limit = max(0, int(doc_config.get('pdf_split_retry_limit', 3)))

        # 双栏 PDF 预处理（可选）：将每页按左右两栏重排为 [左页, 右页]
        self.pdf_double_column_split_enabled = bool(doc_config.get('pdf_double_column_split_enabled', False))
        try:
            self.pdf_column_split_ratio = float(doc_config.get('pdf_column_split_ratio', 0.5))
        except (TypeError, ValueError):
            self.pdf_column_split_ratio = 0.5
        self.pdf_column_split_ratio = min(0.85, max(0.15, self.pdf_column_split_ratio))

        # Dify 文档命名策略：尽量不改用户文件名
        self.preserve_original_filename_as_doc_name = bool(
            doc_config.get('preserve_original_filename_as_doc_name', False)
        )
        self.keep_extension_in_doc_name = bool(
            doc_config.get('keep_extension_in_doc_name', True)
        )
        # 分段/拆分时是否在 Dify 文档名上追加“分段 x/y”后缀（默认保持原行为）
        self.append_chunk_suffix_to_name = bool(
            doc_config.get('append_chunk_suffix_to_name', True)
        )

        # 优先使用 MinerU 的布局 JSON（bbox）来生成文本顺序：可按页自动识别双栏并排序
        self.prefer_layout_json_for_reading_order = bool(
            doc_config.get('prefer_layout_json_for_reading_order', False)
        )

        # Dify 配置
        self.dify_base_url = config['dify']['base_url'].rstrip('/')
        self.dataset_id = config['dify']['dataset_id']
        self.api_key = config['dify']['api_key']
        self.document_create_url = f"{self.dify_base_url}/v1/datasets/{self.dataset_id}/document/create-by-file"

        # MinerU 配置
        self.mineru_config = config['mineru']
        self.mineru_enabled = self.mineru_config.get('enabled', True)
        self.mineru_api_key = self.mineru_config.get('api_key')
        self.mineru_base = self.mineru_config.get('base_url', 'https://mineru.net')
        self.mineru_max_filename_length = int(self.mineru_config.get('max_filename_length', 120))

        # 索引配置
        self.indexing_config = config['indexing']

        os.makedirs(self.ocr_output_dir, exist_ok=True)
        self._recent_events = {}
        self._last_mineru_error = ''

    # ------------------------------------------------------------------
    # 事件与元数据处理
    # ------------------------------------------------------------------

    def _mineru_url(self, path: str) -> str:
        base = (self.mineru_base or '').rstrip('/')
        if not base:
            return path
        if not path.startswith('/'):
            path = '/' + path
        return f"{base}{path}"

    def _resolve_document_name(self, file_path, metadata=None):
        # 1) 用户要求：尽量保持与本地文件名一致
        if self.preserve_original_filename_as_doc_name:
            base = os.path.basename(file_path)
            if not self.keep_extension_in_doc_name:
                base = os.path.splitext(base)[0]
            base = (base or "未命名文档").strip()
            return base or "未命名文档"

        # 2) 默认策略：优先元数据 title，否则用文件名（不含扩展名）
        document_name = (metadata or {}).get('title') if metadata else None
        if not document_name:
            document_name = os.path.splitext(os.path.basename(file_path))[0]
        document_name = (document_name or "未命名文档").strip()
        return document_name[:180] or "未命名文档"

    def _extract_part_label(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0]
        match = re.search(r'_part(\d+)', base, re.IGNORECASE)
        if match:
            return f"(第{int(match.group(1))}部分)"
        return ''

    def _get_file_size_mb(self, file_path):
        try:
            return os.path.getsize(file_path) / (1024 * 1024)
        except OSError:
            return 0

    def _format_size(self, size_mb):
        return f"{size_mb:.2f} MB"

    def _is_recently_processed(self, file_path, window=5):
        now_ts = time.time()
        last_ts = self._recent_events.get(file_path)
        if last_ts and now_ts - last_ts < window:
            return True
        self._recent_events[file_path] = now_ts
        return False

    def _get_metadata(self, file_path):
        if not self.metadata_mgr:
            return None
        try:
            metadata = self.metadata_mgr.get_metadata(file_path)
            if metadata:
                log_info(f"匹配到元数据: {metadata.get('title', '未知标题')}")
            else:
                log_warning("未找到元数据，将使用文件名作为标题")
            return metadata
        except Exception as exc:
            log_warning(f"获取元数据失败: {exc}")
            return None

    def _record_upload_success(self, file_path, doc_id, metadata):
        if self.upload_logger:
            self.upload_logger.log_upload(file_path, dify_doc_id=doc_id, status='success', metadata=metadata)

    def _record_upload_failure(self, file_path, error_code, metadata=None):
        if self.upload_logger:
            failure_meta = {'error': error_code}
            if metadata:
                failure_meta.update({k: v for k, v in metadata.items() if v not in (None, '')})
            self.upload_logger.log_upload(file_path, dify_doc_id=None, status='failed', metadata=failure_meta)

    def _build_chunk_metadata(self, base_metadata, chunk_file, chunk_index, total_chunks, extra_note=None):
        chunk_meta = dict(base_metadata) if base_metadata else {}
        chunk_meta['chunk_file'] = os.path.basename(chunk_file)
        chunk_meta['chunk_index'] = chunk_index
        chunk_meta['chunk_total'] = total_chunks
        if extra_note:
            chunk_meta['chunk_note'] = extra_note
        return chunk_meta

    def _limit_filename_length(self, filename, max_length):
        if not filename or len(filename) <= max_length:
            return filename
        name, ext = os.path.splitext(filename)
        available = max_length - len(ext)
        if available <= 0:
            return filename[:max_length]
        trimmed = name[:available]
        return f"{trimmed}{ext}"

    def _build_upload_filename(self, original_filename):
        original_filename = original_filename or f"doc_{int(time.time())}"
        filename = self._limit_filename_length(original_filename, self.upload_filename_max_length)
        cleaned = self._sanitize_upload_filename(filename)
        if cleaned:
            return cleaned
        timestamp = int(time.time())
        _, ext = os.path.splitext(filename)
        fallback = f"doc_{timestamp}{ext}" if ext else f"doc_{timestamp}"
        return self._limit_filename_length(fallback, self.upload_filename_max_length)

    def _sanitize_upload_filename(self, filename):
        if not filename:
            return ''
        invalid_chars = set('<>:"/\\|?*')
        sanitized = []
        for ch in filename:
            if ch in invalid_chars or ord(ch) < 32:
                sanitized.append('_')
            else:
                sanitized.append(ch)
        result = ''.join(sanitized).strip()
        return result

    def _is_internal_pdf_chunk(self, filename):
        if not filename:
            return False
        name_lower = filename.lower()
        return bool(re.search(r'_pdfchunk\d{3}', name_lower))

    def _is_internal_text_chunk(self, filename):
        if not filename:
            return False
        name_lower = filename.lower()
        patterns = [
            r'_ocr_chunk\d{3}',
            r'_chunk\d{3}',
            r'_sub\d{2,}'
        ]
        return any(re.search(pattern, name_lower) for pattern in patterns)

    def _cleanup_internal_chunk_file(self, file_path):
        if not file_path:
            return
        if self._is_internal_pdf_chunk(os.path.basename(file_path)) and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    def _is_mineru_page_limit_error(self):
        if not getattr(self, '_last_mineru_error', ''):
            return False
        lowered = self._last_mineru_error.lower()
        keywords = ['pages exceeds limit', 'page limit', 'please split the file']
        return any(key in lowered for key in keywords)

    # ------------------------------------------------------------------
    # 文件系统事件
    # ------------------------------------------------------------------

    def on_created(self, event):
        if event.is_directory:
            return
        log_info(f"检测到新文件: {event.src_path}")
        self.process_file(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        log_info(f"检测到文件修改: {event.src_path}")
        self.process_file(event.src_path)

    def process_file(self, file_path, force=False):
        try:
            if not os.path.exists(file_path) or os.path.isdir(file_path):
                return

            if not force and self._is_recently_processed(file_path):
                log_info(f"短时间内重复触发，忽略: {os.path.basename(file_path)}")
                return

            filename = os.path.basename(file_path)
            if self._is_internal_pdf_chunk(filename) or self._is_internal_text_chunk(filename):
                log_info(f"检测到自动生成的临时分段文件，跳过事件: {filename}")
                return

            ext = os.path.splitext(file_path)[1].lower()
            if ext and ext not in self.supported_extensions:
                log_warning(f"不支持的文件类型，跳过: {file_path}")
                return

            if self.skip_uploaded and self.upload_logger and self.upload_logger.is_uploaded(file_path):
                log_info(f"文件已在上传记录中，跳过: {os.path.basename(file_path)}")
                return

            metadata = self._get_metadata(file_path)

            if ext in ['.md', '.markdown', '.txt']:
                display_name = self._resolve_document_name(file_path, metadata)
                self._handle_markdown_file(file_path, metadata, display_name)
            elif ext in self.ocr_extensions:
                self._handle_ocr_file(file_path, metadata)
            else:
                display_name = self._resolve_document_name(file_path, metadata)
                self._handle_regular_file(file_path, metadata, display_name)

        except Exception as exc:
            log_error(f"处理文件时出错: {exc}")

    # ------------------------------------------------------------------
    # 文件类型处理
    # ------------------------------------------------------------------

    def _handle_ocr_file(self, file_path, metadata):
        size_mb = self._get_file_size_mb(file_path)
        log_info(f"检测到 OCR 文件: {os.path.basename(file_path)} ({self._format_size(size_mb)})")

        if size_mb > self.max_file_size_mb:
            log_error(f"文件超过限制 {self.max_file_size_mb} MB: {file_path}")
            self._record_upload_failure(file_path, 'file_too_large', metadata)
            return

        display_name = self._resolve_document_name(file_path, metadata)

        if not self.mineru_enabled:
            log_warning("MinerU OCR 已禁用，直接上传原文件")
            self._handle_regular_file(file_path, metadata, display_name)
            return
        is_pdf = file_path.lower().endswith('.pdf')

        ocr_input_path = file_path
        temp_reflow_pdf = None
        if is_pdf and self.pdf_double_column_split_enabled:
            temp_reflow_pdf = self._create_double_column_reflow_pdf(file_path, self.pdf_column_split_ratio)
            if temp_reflow_pdf:
                ocr_input_path = temp_reflow_pdf

        try:
            if is_pdf and self.pdf_split_enabled:
                pdf_chunks = self._split_pdf_file(ocr_input_path, self.pdf_chunk_size_mb)
            if pdf_chunks:
                total = len(pdf_chunks)
                log_success(f"PDF 自动分段完成，共 {total} 个文件")
                success_all = True
                for idx, chunk_file in enumerate(pdf_chunks, start=1):
                    # 分段信息写入元数据；文档名是否追加后缀由配置决定
                    chunk_meta = self._build_chunk_metadata(
                        metadata,
                        chunk_file,
                        idx,
                        total,
                        extra_note=f"PDF分段 {idx}/{total}"
                    )
                    chunk_display = display_name
                    if self.append_chunk_suffix_to_name:
                        chunk_display = f"{display_name} (PDF分段 {idx}/{total})"

                    if not self._process_single_ocr_input(chunk_file, chunk_meta, chunk_display, split_depth=0):
                        success_all = False
                        break
                for chunk_file in pdf_chunks:
                    self._cleanup_internal_chunk_file(chunk_file)
                if not success_all:
                    log_error("部分 PDF 分段上传失败")
                return
            else:
                log_warning("PDF 分段失败，继续尝试整体上传")

            self._process_single_ocr_input(ocr_input_path, metadata, display_name, split_depth=0)
        finally:
            if temp_reflow_pdf and os.path.exists(temp_reflow_pdf):
                try:
                    os.remove(temp_reflow_pdf)
                except OSError:
                    pass

    def _create_double_column_reflow_pdf(self, file_path, split_ratio=0.5):
        """将双栏 PDF 重排为 [左栏, 右栏] 的新 PDF（临时文件）。

        说明：仅做裁剪并重排页序，不做图像化渲染；适用于多数双栏排版的扫描/排版 PDF。
        """
        if not ensure_pdf_split_available():
            log_warning("未检测到 PyPDF2，无法启用双栏 PDF 重排")
            return None

        try:
            reader = PdfReader(file_path)
        except Exception as exc:
            log_warning(f"双栏重排：读取 PDF 失败，跳过该预处理: {exc}")
            return None

        if not getattr(reader, 'pages', None):
            return None

        ratio = split_ratio
        ratio = min(0.85, max(0.15, float(ratio)))

        try:
            writer = PdfWriter()
            total_pages = len(reader.pages)
            log_info(f"检测到双栏模式已启用：将每页重排为左/右两页（共 {total_pages} 页）")

            for page_index in range(total_pages):
                page = reader.pages[page_index]

                # 获取页面边界
                mb = page.mediabox
                x0 = float(mb.left)
                y0 = float(mb.bottom)
                x1 = float(mb.right)
                y1 = float(mb.top)
                width = max(1.0, x1 - x0)
                mid_x = x0 + width * ratio

                try:
                    left_page = copy.deepcopy(page)
                    right_page = copy.deepcopy(page)
                except Exception:
                    left_page = copy.copy(page)
                    right_page = copy.copy(page)

                # 按左右裁剪（PDF 坐标系原点在左下）
                left_page.cropbox.lower_left = (x0, y0)
                left_page.cropbox.upper_right = (mid_x, y1)

                right_page.cropbox.lower_left = (mid_x, y0)
                right_page.cropbox.upper_right = (x1, y1)

                writer.add_page(left_page)
                writer.add_page(right_page)

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_colsplit.pdf')
            tmp_path = tmp.name
            tmp.close()
            with open(tmp_path, 'wb') as fh:
                writer.write(fh)

            log_success(f"双栏重排 PDF 已生成（临时）：{tmp_path}")
            return tmp_path

        except Exception as exc:
            log_warning(f"双栏重排失败，回退为原始 PDF: {exc}")
            return None

    def _retry_pdf_with_further_split(self, file_path, metadata, display_name, split_depth):
        if self.pdf_split_retry_limit and split_depth >= self.pdf_split_retry_limit:
            log_error("已达到 PDF 拆分重试上限，停止进一步处理")
            return False

        next_depth = split_depth + 1
        base_target = self.pdf_chunk_size_mb or 50
        new_target = max(5, base_target / (2 ** next_depth))
        override_pages = None
        if self.pdf_max_pages_per_chunk:
            override_pages = max(20, self.pdf_max_pages_per_chunk // (2 ** next_depth))

        log_info(
            f"尝试第 {next_depth} 层 PDF 拆分：目标 {new_target:.2f} MB，页数上限 {override_pages or '不限'}"
        )

        sub_chunks = self._split_pdf_file(
            file_path,
            target_size_mb=new_target,
            force=True,
            max_pages_override=override_pages
        )

        if not sub_chunks:
            log_error("递归拆分失败，无法继续处理 PDF")
            return False

        total = len(sub_chunks)
        success_all = True
        for idx, chunk_path in enumerate(sub_chunks, start=1):
            chunk_display = f"{display_name} (PDF分段 {idx}/{total}，深度 {next_depth})"
            if not self._process_single_ocr_input(
                    chunk_path,
                    metadata,
                    chunk_display,
                    split_depth=next_depth):
                success_all = False
                break

        for temp_path in sub_chunks:
            self._cleanup_internal_chunk_file(temp_path)

        if success_all:
            self._cleanup_internal_chunk_file(file_path)

        return success_all

    def _process_single_ocr_input(self, file_path, metadata, display_name=None, split_depth=0):
        if not display_name:
            display_name = self._resolve_document_name(file_path, metadata)

        ocr_result = self.upload_file_via_mineru(file_path)
        if ocr_result:
            if self._handle_markdown_file(ocr_result, metadata, display_name=display_name):
                return True

        if self._is_mineru_page_limit_error() and file_path.lower().endswith('.pdf'):
            log_warning("MinerU 返回页数超限，尝试进一步拆分 PDF 并重试")
            if self._retry_pdf_with_further_split(file_path, metadata, display_name, split_depth):
                return True

        log_error("OCR 解析失败，无法继续上传")
        self._record_upload_failure(file_path, 'ocr_failed', metadata)
        return False

    def _handle_regular_file(self, file_path, metadata, display_name=None):
        doc_id, error_code = self.upload_to_dify(file_path, metadata=metadata, display_name=display_name)
        if doc_id:
            log_success(f"上传成功: {os.path.basename(file_path)}")
            self._record_upload_success(file_path, doc_id, metadata)
            return True
        else:
            log_error(f"上传失败: {os.path.basename(file_path)} (错误: {error_code})")
            self._record_upload_failure(file_path, error_code, metadata)
            return False

    def _handle_markdown_file(self, file_path, metadata, display_name=None):
        size_mb = self._get_file_size_mb(file_path)
        log_info(f"处理 Markdown 文件: {os.path.basename(file_path)} ({self._format_size(size_mb)})")

        if size_mb <= self.markdown_chunk_size_mb:
            return self._handle_regular_file(file_path, metadata, display_name)

        log_warning(
            f"文件 {os.path.basename(file_path)} 大小 {self._format_size(size_mb)} 超过 {self.markdown_chunk_size_mb} MB，开始自动切分"
        )
        base_display = display_name or self._resolve_document_name(file_path, metadata)
        if self._upload_with_chunking(file_path, metadata, base_display=base_display):
            log_success(f"分割上传完成: {os.path.basename(file_path)}")
            return True
        log_error(f"分割文件上传失败: {os.path.basename(file_path)}")
        return False

    # ------------------------------------------------------------------
    # 切分与分段上传
    # ------------------------------------------------------------------

    def _split_text_file(self, file_path, chunk_size_mb, depth_tag="chunk"):
        chunk_size_bytes = max(1, int(chunk_size_mb * 1024 * 1024))
        try:
            with open(file_path, 'r', encoding='utf-8') as src:
                content = src.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                content = src.read()

        encoded_size = len(content.encode('utf-8'))
        if encoded_size <= chunk_size_bytes:
            return []

        base_dir, base_filename = os.path.split(file_path)
        name, ext = os.path.splitext(base_filename)

        chunks = []
        accumulator = []
        accumulator_bytes = 0
        chunk_idx = 1

        for line in content.splitlines(keepends=True):
            line_bytes = len(line.encode('utf-8'))
            if accumulator and accumulator_bytes + line_bytes > chunk_size_bytes:
                chunk_text = ''.join(accumulator)
                chunk_path = os.path.join(base_dir, f"{name}_{depth_tag}{chunk_idx:03d}{ext}")
                with open(chunk_path, 'w', encoding='utf-8') as dst:
                    dst.write(chunk_text)
                log_info(f"已生成分段文件 {os.path.basename(chunk_path)}（约 {accumulator_bytes/1024/1024:.2f} MB）")
                chunks.append(chunk_path)
                chunk_idx += 1
                accumulator = [line]
                accumulator_bytes = line_bytes
            else:
                accumulator.append(line)
                accumulator_bytes += line_bytes

        if accumulator:
            chunk_text = ''.join(accumulator)
            chunk_path = os.path.join(base_dir, f"{name}_{depth_tag}{chunk_idx:03d}{ext}")
            with open(chunk_path, 'w', encoding='utf-8') as dst:
                dst.write(chunk_text)
            log_info(f"已生成分段文件 {os.path.basename(chunk_path)}（约 {accumulator_bytes/1024/1024:.2f} MB）")
            chunks.append(chunk_path)

        return chunks

    def _split_pdf_file(self, file_path, target_size_mb, force=False, max_pages_override=None):
        if not ensure_pdf_split_available():
            log_warning("PDF 分割功能不可用，无法拆分大文件")
            return []

        try:
            reader = PdfReader(file_path)
        except Exception as exc:
            log_error(f"读取 PDF 失败，无法分割: {exc}")
            return []

        total_pages = len(reader.pages)
        if total_pages == 0:
            log_warning("PDF 无页面，跳过分割")
            return []

        file_size_mb = max(0.01, self._get_file_size_mb(file_path))
        page_limit = max_pages_override if max_pages_override is not None else self.pdf_max_pages_per_chunk

        need_split = bool(force)
        if not need_split and target_size_mb:
            need_split = file_size_mb > target_size_mb
        if not need_split and page_limit:
            need_split = total_pages > page_limit

        if not need_split:
            return []

        approx_pages = total_pages
        if file_size_mb and target_size_mb:
            approx_pages = max(1, math.ceil(total_pages * min(target_size_mb, file_size_mb) / file_size_mb))
        if page_limit:
            approx_pages = min(approx_pages, max(1, int(page_limit)))
        approx_pages = max(1, approx_pages)
        base_dir = os.path.dirname(file_path)
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        chunk_paths = []

        for chunk_idx, start_page in enumerate(range(0, total_pages, approx_pages), start=1):
            writer = PdfWriter()
            end_page = min(start_page + approx_pages, total_pages)
            for page_num in range(start_page, end_page):
                writer.add_page(reader.pages[page_num])
            chunk_path = os.path.join(base_dir, f"{base_name}_pdfchunk{chunk_idx:03d}.pdf")
            try:
                with open(chunk_path, 'wb') as chunk_file:
                    writer.write(chunk_file)
                chunk_size = self._get_file_size_mb(chunk_path)
                log_info(f"生成 PDF 分段 {chunk_idx}: {os.path.basename(chunk_path)} （约 {chunk_size:.2f} MB）")
                chunk_paths.append(chunk_path)
            except Exception as exc:
                log_error(f"写入 PDF 分段失败: {exc}")
                return []

        return chunk_paths

    def _upload_with_chunking(self, file_path, metadata, base_display=None):
        chunks = self._split_text_file(file_path, self.markdown_chunk_size_mb, depth_tag="chunk")
        if not chunks:
            log_warning("切分结果为空，回退为直接上传")
            self._handle_regular_file(file_path, metadata, base_display)
            return False

        log_success(f"Markdown 切分完成，共生成 {len(chunks)} 个文件")
        base_display = base_display or self._resolve_document_name(file_path, metadata)
        part_label = self._extract_part_label(file_path)
        total = len(chunks)

        for idx, chunk_path in enumerate(chunks, start=1):
            log_info(f"开始上传分段 {idx}/{total}: {os.path.basename(chunk_path)}")
            if self.append_chunk_suffix_to_name:
                chunk_label_parts = [base_display]
                if part_label:
                    chunk_label_parts.append(part_label)
                chunk_label_parts.append(f"(分段 {idx}/{total})")
                chunk_label = ' '.join(filter(None, chunk_label_parts))
            else:
                chunk_label = base_display

            success = self._upload_chunk_recursive(
                chunk_path,
                metadata,
                chunk_label,
                chunk_index=idx,
                total_chunks=total,
                current_limit_mb=self.markdown_chunk_size_mb,
                depth=0
            )

            if not success:
                log_error(f"❌ 分段 {idx}/{total} 上传失败，终止剩余分段")
                return False

        return True

    def _upload_chunk_recursive(self, chunk_file, metadata, chunk_label, chunk_index, total_chunks,
                                 current_limit_mb, depth):
        chunk_meta = self._build_chunk_metadata(metadata, chunk_file, chunk_index, total_chunks, chunk_label)
        doc_id, error_code = self.upload_to_dify(chunk_file, metadata=chunk_meta, display_name=chunk_label)

        if doc_id:
            self._record_upload_success(chunk_file, doc_id, chunk_meta)
            return True

        if error_code == 'invalid_param' and current_limit_mb > self.markdown_min_chunk_size_mb:
            new_limit = max(self.markdown_min_chunk_size_mb, current_limit_mb / 2)
            log_warning(
                f"⚠️ 检测到 invalid_param，尝试将 {os.path.basename(chunk_file)} 降到 {new_limit:.2f} MB 并重试"
            )
            sub_tag = f"sub{depth + 1:02d}"
            sub_chunks = self._split_text_file(chunk_file, new_limit, depth_tag=sub_tag)
            if not sub_chunks:
                log_warning("无法进一步切分该分段，上传终止")
                self._record_upload_failure(chunk_file, error_code, chunk_meta)
                return False

            sub_total = len(sub_chunks)
            success_all = True
            for sub_idx, sub_chunk in enumerate(sub_chunks, start=1):
                sub_label = f"{chunk_label} - 子段 {sub_idx}/{sub_total}"
                if not self._upload_chunk_recursive(
                        sub_chunk,
                        metadata,
                        sub_label,
                        chunk_index,
                        total_chunks,
                        new_limit,
                        depth + 1):
                    success_all = False
                    break

            # 递归切分生成的子文件是临时文件，上传结束后可清理
            for temp_path in sub_chunks:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

            return success_all

        self._record_upload_failure(chunk_file, error_code or 'upload_failed', chunk_meta)
        return False

    def _build_session(self):
        """构建带重试的 requests Session"""
        session = requests.Session()
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry_strategy = Retry(
            total=5,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*"
        })
        session.verify = True
        session.trust_env = False
        return session

    def _download_and_extract_zip(self, zip_url):
        """下载并解析 MinerU 结果压缩包"""
        try:
            session = self._build_session()
            rzip = session.get(zip_url, stream=True, timeout=120)

            if rzip.status_code != 200:
                log_warning(f"下载压缩包失败: HTTP {rzip.status_code}")
                return ''

            import tempfile
            import zipfile

            tmpf = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            for chunk in rzip.iter_content(1024 * 1024):
                tmpf.write(chunk)
            tmpf.close()

            def _safe_decode(blob: bytes) -> str:
                if not blob:
                    return ''
                for enc in ('utf-8', 'utf-8-sig', 'gbk'):
                    try:
                        return blob.decode(enc)
                    except Exception:
                        continue
                try:
                    return blob.decode('utf-8', errors='ignore')
                except Exception:
                    return ''

            def _looks_like_layout_json(text: str) -> bool:
                if not text:
                    return False
                head = text.lstrip()[:2000]
                return ('"bbox"' in head and ('"spans"' in head or '"page_idx"' in head or '"text_level"' in head))

            def _strip_html(text: str) -> str:
                if not text:
                    return ''
                # 非严格 HTML 清理：去标签并压缩空白
                text = re.sub(r'<\s*br\s*/?>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'</p\s*>', '\n', text, flags=re.IGNORECASE)
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'\n{3,}', '\n\n', text)
                return text.strip()

            def _bbox_center(bbox):
                try:
                    x0, y0, x1, y1 = bbox
                    return (float(x0) + float(x1)) / 2.0, (float(y0) + float(y1)) / 2.0
                except Exception:
                    return 0.0, 0.0

            def _bbox_sort_key(block):
                bbox = block.get('bbox') or []
                try:
                    x0, y0, x1, y1 = bbox
                    return (float(y0), float(x0))
                except Exception:
                    return (0.0, 0.0)

            def _block_text(block: dict) -> str:
                # MinerU 常见两种结构：
                # 1) {type,text,text_level,bbox,page_idx}
                # 2) {bbox,spans:[{content,type},...],index}
                if not isinstance(block, dict):
                    return ''

                if 'text' in block:
                    text = (block.get('text') or '').strip()
                    level = block.get('text_level')
                    if text and isinstance(level, int) and 1 <= level <= 6:
                        return ('#' * level) + ' ' + text
                    return text

                spans = block.get('spans') or []
                parts = []
                for sp in spans:
                    if not isinstance(sp, dict):
                        continue
                    content = (sp.get('content') or '').strip()
                    if not content:
                        continue
                    if sp.get('type') == 'inline_equation':
                        parts.append(f"${content}$")
                    else:
                        parts.append(content)
                return ''.join(parts).strip()

            def _group_blocks_by_page(blocks):
                pages = {}
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    page = b.get('page_idx')
                    if page is None:
                        page = b.get('page')
                    if page is None:
                        page = 0
                    pages.setdefault(int(page), []).append(b)
                return [pages[k] for k in sorted(pages.keys())]

            def _is_two_column(page_blocks) -> bool:
                xs = []
                min_x0 = None
                max_x1 = None
                for b in page_blocks:
                    bbox = b.get('bbox')
                    if not bbox:
                        continue
                    try:
                        x0, y0, x1, y1 = bbox
                        min_x0 = float(x0) if min_x0 is None else min(min_x0, float(x0))
                        max_x1 = float(x1) if max_x1 is None else max(max_x1, float(x1))
                        cx, _ = _bbox_center(bbox)
                        xs.append(cx)
                    except Exception:
                        continue

                if len(xs) < 20 or min_x0 is None or max_x1 is None:
                    return False
                page_width = max(1.0, max_x1 - min_x0)
                xs_sorted = sorted(xs)
                mid = xs_sorted[len(xs_sorted) // 2]
                left = [x for x in xs if x < mid]
                right = [x for x in xs if x >= mid]
                if len(left) < 8 or len(right) < 8:
                    return False
                gap = (sum(right) / len(right)) - (sum(left) / len(left))
                return gap > (page_width * 0.2)

            def _layout_json_to_text(text: str) -> str:
                try:
                    data = json.loads(text)
                except Exception:
                    return ''

                if isinstance(data, dict):
                    # 尝试兼容外层包一层的结构
                    for key in ('data', 'result', 'pages', 'blocks'):
                        if key in data and isinstance(data[key], list):
                            data = data[key]
                            break

                if not isinstance(data, list):
                    return ''

                blocks = [b for b in data if isinstance(b, dict)]
                if not blocks:
                    return ''

                out_lines = []
                for page_blocks in _group_blocks_by_page(blocks):
                    # 过滤空文本块
                    page_blocks = [b for b in page_blocks if _block_text(b)]
                    if not page_blocks:
                        continue

                    if _is_two_column(page_blocks):
                        # 左列 -> 右列
                        xs = [_bbox_center(b.get('bbox') or [0, 0, 0, 0])[0] for b in page_blocks]
                        xs_sorted = sorted(xs)
                        split_x = xs_sorted[len(xs_sorted) // 2]
                        left_blocks = []
                        right_blocks = []
                        for b in page_blocks:
                            cx, _ = _bbox_center(b.get('bbox') or [])
                            (left_blocks if cx < split_x else right_blocks).append(b)
                        left_blocks.sort(key=_bbox_sort_key)
                        right_blocks.sort(key=_bbox_sort_key)
                        ordered = left_blocks + right_blocks
                    else:
                        page_blocks.sort(key=_bbox_sort_key)
                        ordered = page_blocks

                    for b in ordered:
                        line = _block_text(b)
                        if line:
                            out_lines.append(line)

                    out_lines.append('')

                return '\n'.join(out_lines).strip()

            # 读取 zip 内候选文本
            prefer_json = bool(self.prefer_layout_json_for_reading_order)
            candidates = []
            with zipfile.ZipFile(tmpf.name, 'r') as z:
                for zi in z.namelist():
                    lower = zi.lower()
                    if not lower.endswith(('.md', '.txt', '.json', '.html')):
                        continue
                    with z.open(zi) as fh:
                        raw = fh.read()
                    content = _safe_decode(raw).strip()
                    if not content:
                        continue

                    # 选择策略：
                    # - 默认：优先 md/txt，其次 json(转文本)，最后 html(去标签)
                    # - 若 prefer_json=true：优先 json(转文本) 以获得更好的阅读顺序（含按页双栏判断）

                    # 先判断是否是布局 JSON（有些 .md 其实也是 JSON）
                    is_layout = _looks_like_layout_json(content)

                    if lower.endswith('.json') or is_layout:
                        parsed = _layout_json_to_text(content)
                        if parsed:
                            # prefer_json 时给更高权重
                            rank = 10 if prefer_json else 1
                            candidates.append((rank, len(parsed), parsed))
                        continue

                    if lower.endswith('.md'):
                        rank = 3
                        candidates.append((rank, len(content), content))
                        continue

                    if lower.endswith('.txt'):
                        rank = 2
                        candidates.append((rank, len(content), content))
                        continue

                    if lower.endswith('.html'):
                        cleaned = _strip_html(content)
                        if cleaned:
                            candidates.append((0, len(cleaned), cleaned))
                        continue

            extracted_text = ''
            if candidates:
                # rank(高优先) -> length(长优先)
                candidates.sort(key=lambda x: (x[0], x[1]))
                extracted_text = candidates[-1][2].strip() + '\n'

            try:
                os.unlink(tmpf.name)
            except Exception:
                pass

            return extracted_text

        except Exception as e:
            log_warning(f"下载/解析压缩包出错: {e}")
            return ''

    def upload_file_via_mineru(self, file_path):
        """通过 MinerU 批量上传接口直传文件并获取解析结果"""
        if not self.mineru_api_key:
            log_warning("未配置 MinerU API Key")
            return None

        self._last_mineru_error = ''

        # 构造安全的 ASCII 文件名
        orig_basename = os.path.basename(file_path)
        payload_basename = self._limit_filename_length(orig_basename, self.mineru_max_filename_length)
        if payload_basename != orig_basename:
            log_info(f"MinerU 文件名过长（{len(orig_basename)} 个字符），截断为: {payload_basename}")

        name, ext = os.path.splitext(orig_basename)
        safe_name = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '_' for ch in name)
        if not safe_name:
            safe_name = 'file'
        safe_basename = (safe_name[:200] + ext)
        use_temp_copy = (orig_basename != safe_basename)
        temp_path = None

        def _cleanup_tmp():
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        # 业务级重试：最多 3 次
        for attempt in range(1, 4):
            try:
                upload_file = file_path
                uploaded_basename = orig_basename

                if use_temp_copy:
                    import tempfile
                    import shutil
                    tmpdir = tempfile.gettempdir()
                    temp_path = os.path.join(tmpdir, safe_basename)
                    shutil.copyfile(file_path, temp_path)
                    upload_file = temp_path
                    uploaded_basename = safe_basename

                # 使用 SHA256 哈希作为 data_id，避免超长文件名问题
                import hashlib
                data_id_short = hashlib.sha256(payload_basename.encode('utf-8')).hexdigest()[:32]

                url = self._mineru_url("/api/v4/file-urls/batch")
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.mineru_api_key}"
                }

                # name 保持完整文件名（显示用），data_id 使用短哈希（避免超长）
                payload = {
                    "enable_formula": self.mineru_config.get('enable_formula', False),
                    "language": self.mineru_config.get('language', 'ch'),
                    "enable_table": self.mineru_config.get('enable_table', True),
                    "files": [
                        {"name": payload_basename, "is_ocr": True, "data_id": data_id_short}
                    ]
                }

                log_info(f"(尝试 {attempt}/3) 向 MinerU 申请上传链接: {payload_basename}")
                log_info(f"data_id: {data_id_short} (长度: {len(data_id_short)})")
                session = self._build_session()
                resp = session.post(url, headers=headers, json=payload, timeout=30)

                if resp.status_code != 200:
                    log_error(f"申请上传链接失败: HTTP {resp.status_code}")
                    raise RuntimeError("apply upload url failed")

                resj = resp.json()
                if resj.get('code') != 0:
                    log_error(f"申请上传链接返回错误: {resj}")
                    raise RuntimeError("apply upload url error code")

                data = resj.get('data', {})
                batch_id = data.get('batch_id')
                file_urls = data.get('file_urls') or []

                if not file_urls:
                    log_error("未获取到上传链接")
                    raise RuntimeError("no upload url")

                upload_url = file_urls[0]
                log_info(f"(尝试 {attempt}/3) 上传文件到 MinerU")

                with open(upload_file, 'rb') as fh:
                    rput = session.put(upload_url, data=fh, timeout=180)

                if rput.status_code not in (200, 201):
                    log_error(f"上传文件失败: HTTP {rput.status_code}")
                    raise RuntimeError("upload file failed")

                if not batch_id:
                    log_warning("未返回 batch_id，无法轮询结果")
                    raise RuntimeError("no batch id")

                log_info(f"等待 MinerU 处理（batch_id: {batch_id}）")
                batch_url = self._mineru_url(f"/api/v4/extract-results/batch/{batch_id}")

                waited = 0
                max_wait = 600
                poll_interval = 5
                extracted_text = ''

                while waited < max_wait:
                    try:
                        rb = session.get(
                            batch_url, 
                            headers={"Authorization": f"Bearer {self.mineru_api_key}"}, 
                            timeout=30
                        )

                        if rb.status_code != 200:
                            log_warning(f"轮询 batch 状态 HTTP {rb.status_code}")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        jr = rb.json()
                        if jr.get('code') != 0:
                            log_warning(f"batch 状态返回错误: {jr}")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        results = jr.get('data', {}).get('extract_result', [])
                        if not results:
                            log_info(f"batch 尚未返回结果，等待 {poll_interval}s...")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        # 查找匹配的文件
                        target = None
                        match_candidates = {payload_basename, orig_basename, uploaded_basename, safe_basename}
                        for r in results:
                            if r.get('file_name') in match_candidates:
                                target = r
                                break

                        if not target:
                            log_info(f"未在 batch 结果中找到目标文件")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        state = target.get('state')

                        if state == 'done':
                            full_zip = target.get('full_zip_url')
                            if full_zip:
                                log_info(f"下载解析结果压缩包")
                                extracted_text = self._download_and_extract_zip(full_zip)

                                if extracted_text:
                                    base_name = os.path.splitext(orig_basename)[0]
                                    txt_path = os.path.join(self.ocr_output_dir, f"{base_name}_ocr.md")
                                    with open(txt_path, 'w', encoding='utf-8') as fo:
                                        fo.write(extracted_text)
                                    log_success(f"批量上传解析成功: {txt_path}")
                                    _cleanup_tmp()
                                    return txt_path
                            else:
                                log_warning("任务完成但未返回 full_zip_url")
                            _cleanup_tmp()
                            return None

                        elif state == 'failed':
                            err_msg = target.get('err_msg') or ''
                            self._last_mineru_error = err_msg
                            log_error(f"批量解析任务失败: {err_msg}")
                            _cleanup_tmp()
                            return None
                        else:
                            log_info(f"批量任务状态: {state}，等待 {poll_interval}s...")

                    except Exception as e:
                        log_warning(f"轮询 batch 结果异常: {e}")

                    time.sleep(poll_interval)
                    waited += poll_interval

                self._last_mineru_error = 'batch_timeout'
                log_error("批量解析超时或未返回结果")
                _cleanup_tmp()
                return None

            except Exception as e:
                self._last_mineru_error = str(e)
                log_warning(f"(尝试 {attempt}/3) 上传过程异常: {e}")
                if attempt < 3:
                    wait_s = 2 ** attempt
                    log_info(f"等待 {wait_s}s 后重试...")
                    time.sleep(wait_s)
                else:
                    log_error("多次尝试直传均失败")
            finally:
                _cleanup_tmp()

        return None

    def _build_process_rule(self):
        """根据配置构建 Dify 所需的 process_rule"""
        config = self.indexing_config or {}
        mode = config.get('mode', 'custom') or 'custom'
        separator = config.get('separator', '###') or '###'
        max_tokens = int(config.get('max_tokens', 1500))
        chunk_overlap = config.get('chunk_overlap')
        remove_extra_spaces = bool(config.get('remove_extra_spaces', True))
        remove_urls_emails = bool(config.get('remove_urls_emails', False))
        process_rule = {"mode": mode}
        if mode == 'custom':
            segmentation = {
                "separator": separator,
                "max_tokens": max_tokens
            }
            if chunk_overlap is not None:
                try:
                    segmentation["chunk_overlap"] = int(chunk_overlap)
                except (TypeError, ValueError):
                    log_warning("chunk_overlap 配置无效，已忽略该字段")
            process_rule["rules"] = {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": remove_extra_spaces},
                    {"id": "remove_urls_emails", "enabled": remove_urls_emails}
                ],
                "segmentation": segmentation
            }
        return config.get('technique', 'high_quality'), process_rule

    def upload_to_dify(self, file_path, metadata=None, display_name=None):
        """上传文件到 Dify，返回 (doc_id, error_code)"""
        try:
            indexing_technique, process_rule = self._build_process_rule()
            headers = {'Authorization': f'Bearer {self.api_key}'}

            # 获取原始文件相关信息
            original_filename = os.path.basename(file_path)
            log_info(f"正在上传文件到 Dify: {original_filename}")
            name, ext = os.path.splitext(original_filename)

            # 构建文档名称（展示用）
            if display_name:
                document_name = display_name.strip() or "未命名文档"
            else:
                document_name = self._resolve_document_name(file_path, metadata)
            document_name = document_name[:180]
            log_info(f"Dify 文档名称: {document_name}")

            # 构建 metadata payload
            metadata_payload = {}
            doc_language = None
            if metadata:
                metadata_payload = {
                    k: v for k, v in metadata.items()
                    if v not in (None, '')
                }
                doc_language = metadata_payload.get('language') or metadata_payload.get('lang')
            if not doc_language:
                doc_language = self.mineru_config.get('language') or 'ch'

            primary_payload = {
                "name": document_name,
                "source": "upload_file",
                "doc_type": "text",
                "doc_language": doc_language,
                "indexing_technique": indexing_technique,
                "process_rule": process_rule
            }
            if metadata_payload:
                primary_payload["metadata"] = metadata_payload
            if metadata and metadata.get('id'):
                primary_payload["external_document_id"] = metadata.get('id')

            minimal_payload = {
                "doc_language": doc_language,
                "indexing_technique": indexing_technique,
                "process_rule": process_rule
            }

            log_info(f"process_rule: {json.dumps(process_rule, ensure_ascii=False)[:200]}")
            log_info(f"primary data payload: {json.dumps(primary_payload, ensure_ascii=False)[:200]}")
            log_info(f"minimal data payload: {json.dumps(minimal_payload, ensure_ascii=False)[:200]}")

            # 尽量保留用户原始文件名（不改名）。
            # 仅在服务端因参数/文件名问题拒绝时，才回退使用清理后的文件名重试一次。
            fallback_filename = self._build_upload_filename(original_filename)
            filename_candidates = [original_filename]
            if fallback_filename and fallback_filename != original_filename:
                filename_candidates.append(fallback_filename)

            mime_type = 'text/markdown' if ext.lower() in ['.md', '.markdown'] else 'application/octet-stream'
            if ext.lower() == '.pdf':
                mime_type = 'application/pdf'
            elif ext.lower() in ['.txt']:
                mime_type = 'text/plain'

            payloads = [("primary", primary_payload), ("minimal", minimal_payload)]
            need_fallback = False
            last_error_code = None
            for idx, (label, payload) in enumerate(payloads):
                if label == "minimal" and not need_fallback:
                    break
                json_payload = json.dumps(payload, ensure_ascii=False)
                log_info(f"尝试使用 {label} payload 上传 (第 {idx + 1} 次)")
                # 文件名候选：优先原始文件名，不行再用回退文件名
                response = None
                used_upload_filename = None
                for name_try_index, upload_filename in enumerate(filename_candidates, start=1):
                    used_upload_filename = upload_filename
                    name_label = "原始文件名" if name_try_index == 1 else "回退文件名"
                    log_info(f"上传文件名（{name_label}）: {upload_filename}")
                    with open(file_path, 'rb') as fh:
                        multipart_files = [
                            ('file', (upload_filename, fh, mime_type)),
                            ('data', (None, json_payload, 'application/json'))
                        ]
                        response = requests.post(
                            self.document_create_url,
                            headers=headers,
                            files=multipart_files,
                            timeout=300
                        )

                    if response.status_code in (200, 201):
                        break

                    # 如果服务端提示参数问题，才尝试回退文件名；否则不多试避免重复请求
                    if name_try_index == 1:
                        try:
                            error_json = response.json()
                        except ValueError:
                            try:
                                error_json = json.loads(response.text)
                            except Exception:
                                error_json = None
                        error_code_try = None
                        if error_json:
                            error_code_try = error_json.get('code') or error_json.get('error')
                        if not error_code_try:
                            error_code_try = f"http_{response.status_code}"
                        if error_code_try not in ('invalid_param', 'http_400'):
                            break

                if response.status_code in (200, 201):
                    result = response.json()
                    doc_info = result.get('document', {})
                    doc_id = doc_info.get('id')
                    log_success(f"上传成功: {os.path.basename(file_path)} (使用 {label} payload)")
                    log_info(f"  文档 ID: {doc_id}")
                    log_info(f"  状态: {doc_info.get('indexing_status', 'N/A')}")
                    log_info(f"  字数: {doc_info.get('word_count', 0)}")
                    return doc_id, None

                error_body = response.text[:500]
                error_code = None
                try:
                    error_json = response.json()
                except ValueError:
                    try:
                        error_json = json.loads(response.text)
                    except Exception:
                        error_json = None
                if error_json:
                    error_code = error_json.get('code') or error_json.get('error')
                if not error_code:
                    error_code = f"http_{response.status_code}"

                last_error_code = error_code
                log_error(f"上传失败: {response.status_code} - {error_body}")
                if label == "primary" and error_code == 'invalid_param':
                    log_warning("检测到 invalid_param，尝试使用最小化 payload 重试")
                    need_fallback = True
                    continue
                else:
                    return None, error_code
            return None, last_error_code or 'upload_failed'

        except Exception as e:
            log_error(f"上传过程出错: {str(e)}")
            return None, 'exception'


def start_monitoring(config, metadata_mgr, upload_logger):
    """启动文件夹监控和 Dify 监控"""
    folder_path = config['document']['watch_folder']
    
    # 启动文件系统监控
    event_handler = EnhancedFileHandler(config, metadata_mgr, upload_logger)
    file_observer = Observer()
    file_observer.schedule(event_handler, folder_path, recursive=True)
    file_observer.start()
    
    log_info(f"开始监控文件夹: {folder_path}")
    
    # 启动 Dify 监控（可选）
    dify_monitor = None
    monitor_config = config.get('monitor', {})
    if monitor_config.get('enabled', True):
        check_interval = monitor_config.get('check_interval', 60)
        dify_monitor = DifyMonitor(config, upload_logger, metadata_mgr, check_interval)
        dify_monitor.start()
    
    try:
        # 处理现有文件
        log_info("扫描现有文件...")
        existing_files = []
        
        supported_ext = tuple(config['document'].get('supported_extensions', [
            '.txt', '.md', '.markdown', '.mdx', '.html',
            '.pdf', '.doc', '.docx',
            '.xlsx', '.xls', '.csv',
            '.ppt', '.pptx',
            '.eml', '.msg',
            '.xml', '.vtt', '.properties',
            '.epub',
            '.png', '.jpg', '.jpeg', '.tiff', '.bmp'
        ]))
        
        for root, _, files in os.walk(folder_path):
            for filename in files:
                if filename.lower().endswith(supported_ext):
                    file_path = os.path.join(root, filename)
                    existing_files.append(file_path)
        
        log_info(f"找到 {len(existing_files)} 个支持的文件")
        
        for file_path in existing_files:
            print("\n" + "=" * 60)
            event_handler.process_file(file_path)
        
        print("\n" + "=" * 60)
        log_success("现有文件处理完成")
        log_info("监控服务已启动，按 Ctrl+C 停止...")
        
        if dify_monitor:
            log_info(f"✨ Dify 实时监控已启动（每 {check_interval} 秒检查一次）")
        
        while True:
            file_observer.join(1)
    
    except KeyboardInterrupt:
        log_info("收到停止信号，正在停止监控...")
        if dify_monitor:
            dify_monitor.stop()
        file_observer.stop()
    except Exception as e:
        log_error(f"监控过程出错: {str(e)}")
    finally:
        if dify_monitor:
            dify_monitor.stop()
        file_observer.join()
        log_info("监控服务已停止")


def sync_with_dify(config, upload_logger, metadata_manager=None):
    """与 Dify 同步，清理已删除文档的日志和元数据"""
    try:
        log_info("开始与 Dify 同步...")
        
        # 获取 Dify 中的所有文档
        base_url = config['dify']['base_url']
        dataset_id = config['dify']['dataset_id']
        api_key = config['dify']['api_key']
        
        headers = {'Authorization': f'Bearer {api_key}'}
        url = f"{base_url}/v1/datasets/{dataset_id}/documents"
        
        all_documents = []
        page = 1
        
        while True:
            response = requests.get(
                url,
                headers=headers,
                params={'page': page, 'limit': 100},
                timeout=30
            )
            
            if response.status_code != 200:
                log_warning(f"获取 Dify 文档列表失败: {response.status_code}")
                return
            
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
        
        # 1. 同步数据库（通过文档 ID）
        all_doc_ids = [doc['id'] for doc in all_documents]
        db_deleted = upload_logger.sync_with_dify(all_doc_ids)
        
        # 2. 同步元数据表（通过文件名）
        csv_deleted = 0
        if metadata_manager:
            # 提取 Dify 中的文件名（不含扩展名）
            dify_names = set()
            for doc in all_documents:
                name = doc['name']
                if name.endswith('_ocr.md'):
                    name = name[:-7]
                elif '.' in name:
                    name = os.path.splitext(name)[0]
                normalized_name = normalize_title_for_compare(name)
                if normalized_name:
                    dify_names.add(normalized_name)

            # 找出需要删除的元数据
            local_titles = metadata_manager.get_all_titles()
            to_delete = []

            for title in local_titles:
                normalized_title = normalize_title_for_compare(title)
                if not normalized_title:
                    continue
                if normalized_title not in dify_names:
                    to_delete.append(title)
            
            if to_delete:
                csv_deleted = metadata_manager.delete_by_titles(to_delete)
        
        # 输出结果
        total_deleted = db_deleted + csv_deleted
        if total_deleted > 0:
            log_success(f"✅ 同步完成：数据库 {db_deleted} 条，元数据表 {csv_deleted} 条")
        else:
            log_info("✅ 本地记录与 Dify 已同步")
    
    except Exception as e:
        log_warning(f"同步过程出错（可忽略）: {e}")


def main():
    """主函数"""
    print_header("Dify 知识库自动上传工具（增强版）")
    
    # 加载配置
    try:
        config = load_config("config.yaml")
        log_success("配置文件加载成功")
    except Exception as e:
        log_error(f"加载配置失败: {e}")
        sys.exit(1)
    
    # 初始化元数据管理器
    if config.get('metadata', {}).get('enabled', True):
        metadata_mgr = MetadataManager(
            csv_path=config['metadata']['csv_path'],
            auto_create=config['metadata'].get('auto_create', True),
            default_meta=config['metadata'].get('default', {}),
            config=config  # 传递完整配置
        )
        log_success(f"元数据管理器初始化成功，已加载 {len(metadata_mgr.metadata_map)} 条记录")
    else:
        metadata_mgr = None
        log_warning("元数据功能已禁用")
    
    # 初始化上传日志
    if config.get('database', {}).get('enabled', True):
        upload_logger = UploadLogger(config['database']['sqlite_path'])
        stats = upload_logger.get_statistics()
        log_success(f"日志数据库初始化成功")
        log_info(f"  历史上传: {stats['total_success']} 个文件")
        log_info(f"  失败记录: {stats['total_failed']} 个")
        log_info(f"  总大小: {stats['total_size_mb']} MB")
        
        # 自动同步 Dify 文档（包括数据库和元数据表）
        if config.get('database', {}).get('auto_sync', True):
            sync_with_dify(config, upload_logger, metadata_mgr)
    else:
        upload_logger = None
        log_warning("日志数据库功能已禁用")
    
    # 启动监控
    try:
        start_monitoring(config, metadata_mgr, upload_logger)
    except Exception as e:
        log_error(f"程序运行出错: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
