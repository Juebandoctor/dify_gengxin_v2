import os
import requests
import json
import time
import shutil
import tempfile
from urllib.parse import quote
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Dify配置
DIFY_BASE_URL = "http://192.168.40.128/"
DATASET_ID = "96e6249f-955e-4898-857f-3161be086064"
API_KEY = "dataset-jvlyBUTx1nV5bvaKNFuyiXQe"
DOCUMENT_CREATE_URL = f"{DIFY_BASE_URL}/v1/datasets/{DATASET_ID}/document/create-by-file"

# MinerU OCR 配置
MINERU_API_KEY = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiI3NzUwMDgyMiIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc2Mjc2MDc2OSwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTk4MzY1MDA5MDUiLCJvcGVuSWQiOm51bGwsInV1aWQiOiI0MTI2ZTRmYi0wZWY3LTRkODQtYjBmZS04MGE5NGY4ZWVkZjgiLCJlbWFpbCI6IiIsImV4cCI6MTc2Mzk3MDM2OX0.UBSU_P9EGpB6jh8Lf34r8ogorIpfgvIrAJuL8Xa-B7lzeklIs-RBlRwXXEWyGOdMBSTJ0_ohFXmKwKcWgNklUQ"
MINERU_BASE = "https://mineru.net"
MINERU_PATH_EXTRACT_TASK = "/api/v4/extract/task"
MINERU_PATH_FILE_URLS = "/api/v4/file-urls/batch"
MINERU_PATH_BATCH_RESULTS = "/api/v4/extract-results/batch/{batch_id}"
MINERU_API_URL = f"{MINERU_BASE}{MINERU_PATH_EXTRACT_TASK}"
OCR_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.bmp'}
OCR_OUTPUT_DIR = "ocr_output"
MAX_OCR_FILE_SIZE = 600 * 1024 * 1024  # 600MB

# MinerU 行为与参数配置
PREFER_MINERU_UPLOAD = True  # True: 优先走批量上传直传到 MinerU；False: 先用 URL 拉取，失败再直传
MINERU_LANGUAGE = "ch"       # 文档语言，默认中文
MINERU_ENABLE_TABLE = True   # 开启表格识别
MINERU_ENABLE_FORMULA = False  # 公式识别，默认关闭
DISABLE_URL_FALLBACK = True  # True: 直传失败后不再回退 URL 拉取，直接返回 None（推荐在跨境不稳时开启）

# 是否启用 MinerU OCR
ENABLE_MINERU_OCR = True

# ⚠️ 重要：修改这里为你的 ngrok URL
# 1. 先在命令行运行：ngrok http 8000
# 2. 复制显示的 Forwarding URL（类似 https://xxxx.ngrok-free.app）
# 3. 粘贴到下面
FILE_SERVER_URL = "https://thora-unconical-cattily.ngrok-free.dev"  # <-- 你的 ngrok URL

# 如果还没配置 ngrok，可以先禁用 MinerU OCR
if FILE_SERVER_URL == "https://your-ngrok-url.ngrok-free.app":
    print("⚠️ 检测到未配置 ngrok URL，已自动禁用 MinerU OCR")
    print("   请先阅读 setup_ngrok.md 配置 ngrok，或直接禁用 MinerU")
    ENABLE_MINERU_OCR = False

class FileHandler(FileSystemEventHandler):
    """监控文件变化并上传到Dify知识库"""
    
    def __init__(self, watch_dir):
        super().__init__()
        self.watch_dir = watch_dir
        os.makedirs(OCR_OUTPUT_DIR, exist_ok=True)
        self.processed_files = set()

    def _build_session(self):
        """构建带重试的 requests Session，提高 MinerU 网络稳定性"""
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
        session.trust_env = False  # 忽略系统代理，减少被企业代理/SSL检查干扰
        return session

    def _mineru_url(self, path: str):
        """拼接 MinerU 完整 URL，并做基本形态校验，避免出现 /api/v44 或 file-uurls 的低级错误"""
        if not path.startswith("/api/v4/") and not path.startswith("http"):
            print(f"⚠️ 非预期的 MinerU 路径: {path}，期望以 /api/v4/ 开头")
        return f"{MINERU_BASE}{path}" if not path.startswith("http") else path
    
    def on_created(self, event):
        """处理新文件创建事件"""
        if not event.is_directory:
            print(f"🆕 检测到新文件: {os.path.basename(event.src_path)}")
            self.process_file(event.src_path)
    
    def on_modified(self, event):
        """处理文件修改事件"""
        if not event.is_directory:
            print(f"📝 检测到文件修改: {os.path.basename(event.src_path)}")
            self.process_file(event.src_path)
    
    def extract_text_with_mineru(self, file_path, file_url):
        """使用 MinerU API 进行 OCR 文字提取"""
        if not MINERU_API_KEY:
            print(f"⚠️ 未配置 MinerU API Key，跳过 OCR 处理")
            return None
        
        if not ENABLE_MINERU_OCR:
            print(f"ℹ️ MinerU OCR 已禁用，将直接上传原文件")
            return None
        
        try:
            file_size = os.path.getsize(file_path)
            if file_size > MAX_OCR_FILE_SIZE:
                print(f"⚠️ 文件过大 ({file_size / 1024 / 1024:.2f} MB)，超过 200MB 限制，跳过 OCR")
                return None
        except Exception as e:
            print(f"⚠️ 无法获取文件大小: {e}")
            return None
        
        try:
            print(f"🔍 使用 MinerU 进行 OCR 文字提取: {os.path.basename(file_path)} ({file_size / 1024 / 1024:.2f} MB)")

            # 优先走直传（批量上传）到 MinerU，绕过 URL 拉取的跨境/访问限制
            if PREFER_MINERU_UPLOAD:
                print("🚚 优先使用 MinerU 批量上传接口（直传）进行 OCR ...")
                uploaded_txt = self.upload_file_via_mineru(file_path)
                if uploaded_txt:
                    return uploaded_txt
                if DISABLE_URL_FALLBACK:
                    print("⚠️ 直传未成功，且已开启仅直传模式（禁用 URL 回退），将跳过 MinerU OCR")
                    return None
                print("⚠️ 直传未成功，回退到 URL 拉取模式继续尝试")

            print(f"🌐 文件 URL: {file_url}")
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MINERU_API_KEY}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            data = {
                "url": file_url,
                "is_ocr": True,
                "enable_formula": MINERU_ENABLE_FORMULA,
                "enable_table": MINERU_ENABLE_TABLE,
                "language": MINERU_LANGUAGE,
            }
            
            # 添加重试机制
            max_retries = 3
            retry_count = 0
            response = None
            
            while retry_count < max_retries:
                try:
                    print(f"🔄 发送 OCR 请求... (尝试 {retry_count + 1}/{max_retries})")
                    
                    # 使用统一的带重试 Session
                    session = self._build_session()
                    
                    response = session.post(
                        MINERU_API_URL,
                        headers=headers,
                        json=data,
                        timeout=180  # 增加超时时间到 3 分钟
                    )
                    
                    # 如果成功，跳出循环
                    if response.status_code == 200:
                        break
                    
                except requests.exceptions.SSLError as ssl_err:
                    retry_count += 1
                    print(f"⚠️ SSL 错误 (尝试 {retry_count}/{max_retries}): {str(ssl_err)[:100]}")
                    if retry_count < max_retries:
                        import time
                        wait_time = retry_count * 2
                        print(f"   等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                    else:
                        print(f"❌ SSL 连接失败，已重试 {max_retries} 次")
                        print(f"💡 可能的原因：")
                        print(f"   1. MinerU 服务器繁忙或维护中")
                        print(f"   2. 网络连接不稳定")
                        print(f"   3. 防火墙或代理设置问题")
                        return None
                        
                except requests.exceptions.Timeout:
                    retry_count += 1
                    print(f"⚠️ 请求超时 (尝试 {retry_count}/{max_retries})")
                    if retry_count < max_retries:
                        print(f"   等待后重试...")
                        import time
                        time.sleep(2)
                    else:
                        print(f"❌ 请求超时，已重试 {max_retries} 次")
                        return None
                        
                except Exception as req_err:
                    retry_count += 1
                    print(f"⚠️ 请求错误 (尝试 {retry_count}/{max_retries}): {str(req_err)[:100]}")
                    if retry_count >= max_retries:
                        return None
                    import time
                    time.sleep(2)
            
            if not response:
                print(f"❌ 无法连接到 MinerU 服务器")
                return None
            
            if response.status_code == 200:
                result = response.json()
                print(f"📊 MinerU 响应: {result}")
                
                # 检查是否有错误
                if result.get('code') != 0:
                    error_msg = result.get('msg', '未知错误')
                    print(f"❌ MinerU 返回错误: {error_msg}")
                    
                    # 提供针对性建议
                    if 'failed to read file' in error_msg:
                        print(f"💡 建议：")
                        print(f"   1. 检查 ngrok 是否正在运行")
                        print(f"   2. 在浏览器中访问 {file_url} 确认文件可访问")
                        print(f"   3. 如果 ngrok URL 变化了，请更新 FILE_SERVER_URL 配置")
                    return None
                
                task_data = result.get('data', {})
                task_id = task_data.get('task_id')
                if not task_id:
                    print(f"❌ 未获取到 MinerU 任务ID，无法轮询结果")
                    return None
                print(f"⏳ 正在轮询 MinerU OCR 结果... (task_id: {task_id})")
                # 按官方文档使用 /api/v4/extract/task/{task_id} 查询任务状态
                import time
                task_url = self._mineru_url(f"/api/v4/extract/task/{task_id}")
                headers_poll = {
                    "Authorization": f"Bearer {MINERU_API_KEY}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                max_wait = 300  # 最长等待秒数（可根据文件大小调整）
                poll_interval = 3
                waited = 0
                extracted_text = ''
                session_poll = self._build_session()
                while waited < max_wait:
                    try:
                        resp = session_poll.get(task_url, headers=headers_poll, timeout=30)
                        # 如果 404，视为任务尚未就绪或未同步，继续等待
                        if resp.status_code == 404:
                            print(f"⏳ 轮询返回 404，任务尚未就绪，等待 {poll_interval} 秒...")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        if resp.status_code != 200:
                            print(f"⚠️ MinerU 结果接口 HTTP 错误: {resp.status_code}, 将在 {poll_interval}s 后重试")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        data = resp.json()
                        if data.get('code') != 0:
                            print(f"❌ MinerU 返回错误: {data}")
                            return None

                        # 按文档，state 字段表明任务进度: done/pending/running/failed/converting
                        state = data.get('data', {}).get('state') or data.get('data', {}).get('status')
                        if state == 'done':
                            # 优先尝试直接读取文本字段（若存在），否则下载 full_zip_url
                            extracted_text = data.get('data', {}).get('text') or data.get('data', {}).get('content')
                            if not extracted_text:
                                full_zip = data.get('data', {}).get('full_zip_url')
                                if full_zip:
                                    print(f"⬇️ 任务完成，正在下载结果压缩包: {full_zip}")
                                    try:
                                        rzip = requests.get(full_zip, stream=True, timeout=120)
                                        if rzip.status_code == 200:
                                            import tempfile, zipfile
                                            tmpf = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                                            for chunk in rzip.iter_content(1024 * 1024):
                                                tmpf.write(chunk)
                                            tmpf.close()
                                            # 解压并尝试读取文本文件
                                            with zipfile.ZipFile(tmpf.name, 'r') as z:
                                                for zi in z.namelist():
                                                    if zi.lower().endswith(('.txt', '.json', '.md', '.html')):
                                                        with z.open(zi) as fh:
                                                            try:
                                                                content = fh.read().decode('utf-8')
                                                            except Exception:
                                                                try:
                                                                    content = fh.read().decode('gbk')
                                                                except Exception:
                                                                    content = ''
                                                            if content:
                                                                extracted_text += content + '\n'
                                            try:
                                                os.unlink(tmpf.name)
                                            except Exception:
                                                pass
                                        else:
                                            print(f"⚠️ 下载结果压缩包失败: HTTP {rzip.status_code}")
                                    except Exception as e:
                                        print(f"⚠️ 下载/解析结果压缩包出错: {e}")
                            break

                        elif state == 'failed':
                                    err_msg = data.get('data', {}).get('err_msg', '')
                                    print(f"❌ MinerU OCR 任务失败: {err_msg}")
                                    # 如果是读取文件失败，尝试通过批量上传接口直接上传文件到 MinerU（绕过公网拉取）
                                    if 'failed to read file' in err_msg or data.get('code') == -60003:
                                        print("🔁 检测到文件读取失败，尝试通过 MinerU 批量上传接口上传并解析文件（上传后会自动提交解析任务）")
                                        try:
                                            uploaded_txt = self.upload_file_via_mineru(file_path)
                                            if uploaded_txt:
                                                return uploaded_txt
                                            else:
                                                print("⚠️ 通过批量上传接口尝试解析未成功，回退并上传原文件到 Dify")
                                                return None
                                        except Exception as e:
                                            print(f"⚠️ 批量上传并解析时出错: {e}")
                                            return None
                                    return None
                        else:
                            print(f"⏳ OCR 任务状态: {state}，等待 {poll_interval} 秒...")
                    except Exception as e:
                        print(f"⚠️ 轮询 MinerU 结果异常: {e}")
                    time.sleep(poll_interval)
                    waited += poll_interval

                if extracted_text:
                    base_name = os.path.splitext(os.path.basename(file_path))[0]
                    md_path = os.path.join(OCR_OUTPUT_DIR, f"{base_name}_ocr.md")
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(extracted_text)
                    print(f"✅ OCR 提取成功，文本长度: {len(extracted_text)} 字符")
                    print(f"📝 提取文本已保存到: {md_path}")
                    return md_path
                else:
                    print(f"⚠️ OCR 未提取到文本内容 (轮询超时或无内容) 或 未生成 full_zip_url")
                    return None
            else:
                print(f"❌ MinerU OCR 失败: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"⚠️ OCR 处理出错: {str(e)[:200]}")
            return None
    
    def process_file(self, file_path):
        """处理文件上传到Dify知识库"""
        supported_ext = (
            '.txt', '.md', '.markdown', '.mdx', '.html', 
            '.pdf', '.doc', '.docx', 
            '.xlsx', '.xls', 
            '.csv', 
            '.ppt', '.pptx', 
            '.eml', '.msg', 
            '.xml', '.vtt', '.properties',
            '.epub',
            '.png', '.jpg', '.jpeg', '.tiff', '.bmp'
        )
        
        if not file_path.lower().endswith(supported_ext):
            return
        
        try:
            print(f"📄 开始处理文件: {os.path.basename(file_path)}")
            
            if not self.check_file_validity(file_path):
                return
            
            if file_path in self.processed_files:
                print(f"⏭️ 文件已处理过，跳过: {os.path.basename(file_path)}")
                return
            
            file_ext = os.path.splitext(file_path)[1].lower()
            upload_file_path = file_path
            
            if file_ext in OCR_EXTENSIONS and ENABLE_MINERU_OCR:
                print(f"🔄 检测到需要 OCR 的文件类型: {file_ext}")
                
                relative_path = os.path.relpath(file_path, self.watch_dir)
                file_url = f"{FILE_SERVER_URL}/{quote(relative_path.replace(os.sep, '/'))}"
                
                ocr_result_path = self.extract_text_with_mineru(file_path, file_url)
                
                if ocr_result_path and os.path.exists(ocr_result_path):
                    upload_file_path = ocr_result_path
                    print(f"📤 将上传 OCR 提取的文本文件: {os.path.basename(upload_file_path)}")
                else:
                    print(f"⚠️ OCR 失败，将上传原文件")
            elif file_ext in OCR_EXTENSIONS:
                print(f"📄 检测到 {file_ext} 文件，将直接上传到 Dify")
            
            process_rule = {
                "indexing_technique": "high_quality",
                "process_rule": {
                    "rules": {
                        "pre_processing_rules": [
                            {"id": "remove_extra_spaces", "enabled": True},
                            {"id": "remove_urls_emails", "enabled": False}
                        ],
                        "segmentation": {
                            "separator": "###",
                            "max_tokens": 1500
                        }
                    },
                    "mode": "custom"
                }
            }
            data = {
                'data': (None, json.dumps(process_rule), 'text/plain')
            }
            
            headers = {
                'Authorization': f'Bearer {API_KEY}'
            }

            print(f"📤 正在上传文件到Dify知识库...")
            with open(upload_file_path, 'rb') as fh:
                files = {'file': (os.path.basename(upload_file_path), fh)}
                response = requests.post(
                    DOCUMENT_CREATE_URL,
                    headers=headers,
                    files=files,
                    data=data
                )

            print(f"📥 响应状态码: {response.status_code}")
            if response.status_code == 201 or response.status_code == 200:
                print(f"✅ 文件上传成功: {os.path.basename(file_path)}")
                result = response.json()
                doc_info = result.get('document', {})
                if doc_info:
                    print(f"   📄 文档 ID: {doc_info.get('id', 'N/A')[:16]}...")
                    print(f"   📊 状态: {doc_info.get('indexing_status', 'N/A')}")
                    print(f"   📝 字数: {doc_info.get('word_count', 0)}")
                self.processed_files.add(file_path)
            else:
                # 处理常见的 413 Request Entity Too Large
                if response.status_code == 413:
                    print("❌ 服务器返回 413 Request Entity Too Large（请求体过大）")

                    # 如果是 OCR 提取的文本文件，尝试分片上传每个小文本片段
                    lower_path = upload_file_path.lower()
                    if lower_path.endswith('_ocr.txt') or lower_path.endswith('.txt'):
                        try:
                            print("🔧 尝试将文本分片后逐个上传（每片约 300KB）以规避大小限制...")

                            def _split_text_to_chunks(text, max_bytes=300 * 1024):
                                chunks = []
                                cur = []
                                cur_bytes = 0
                                for para in text.split('\n\n'):
                                    if not para:
                                        # 保留空行分隔，但不要造成无限增长
                                        seg = '\n\n'
                                    else:
                                        seg = para + '\n\n'
                                    seg_b = seg.encode('utf-8')
                                    if cur_bytes + len(seg_b) > max_bytes and cur:
                                        chunks.append(''.join(cur))
                                        cur = [seg]
                                        cur_bytes = len(seg_b)
                                    else:
                                        cur.append(seg)
                                        cur_bytes += len(seg_b)
                                if cur:
                                    chunks.append(''.join(cur))
                                return chunks

                            with open(upload_file_path, 'r', encoding='utf-8', errors='ignore') as rf:
                                full_text = rf.read()

                            parts = _split_text_to_chunks(full_text, max_bytes=300 * 1024)
                            total = len(parts)
                            if total == 0:
                                print("⚠️ 文本为空，无法分片")
                            else:
                                base_name = os.path.splitext(os.path.basename(upload_file_path))[0]
                                for idx, part in enumerate(parts, start=1):
                                    part_name = f"{base_name}_part{idx}.txt"
                                    print(f"📤 上传分片 {idx}/{total}: {part_name} (约 {len(part.encode('utf-8'))} 字节)")
                                    files_part = {'file': (part_name, part.encode('utf-8'))}
                                    try:
                                        resp_part = requests.post(
                                            DOCUMENT_CREATE_URL,
                                            headers=headers,
                                            files=files_part,
                                            data=data,
                                            timeout=120
                                        )
                                        print(f"  ↪️ 分片响应: {resp_part.status_code}")
                                        if resp_part.status_code not in (200, 201):
                                            print(f"  ❌ 分片上传失败: {resp_part.status_code} {resp_part.text[:200]}")
                                        else:
                                            try:
                                                j = resp_part.json()
                                                did = j.get('document', {}).get('id')
                                                if did:
                                                    print(f"   ✅ 分片已在 Dify 创建，文档 ID: {did[:16]}...")
                                            except Exception:
                                                pass
                                    except Exception as e:
                                        print(f"  ⚠️ 分片上传异常: {e}")

                                print("✅ 分片上传尝试完成，请在 Dify 控制台检查各分片文档")

                        except Exception as ex:
                            print(f"⚠️ 分片上传失败: {ex}")
                    else:
                        # 如果不是文本文件，给出操作建议
                        print("建议：")
                        print("  1) 将原始文件拆分为多个更小的文件后重试；")
                        print("  2) 或在服务器端（Dify/nginx）增加 client_max_body_size 配置以允许更大的上传；")
                        print("  3) 或把文件托管到一个可公网访问的 URL（例如通过 ngrok + http.server），然后使用 URL 创建文档（避免直接上传大文件）。")
                else:
                    print(f"❌ 上传失败: {response.text[:200]}")
                
        except Exception as e:
            print(f"⚠️ 处理文件出错: {str(e)}")
            import traceback
            traceback.print_exc()

    def upload_file_via_mineru(self, file_path):
        """通过 MinerU 的 /file-urls/batch 接口申请上传链接，PUT 上传文件，轮询 batch 结果并下载解析结果。返回文本文件路径或 None。"""
        if not MINERU_API_KEY:
            print("⚠️ 未配置 MinerU API Key，无法使用批量上传接口")
            return None
        # 构造安全的 ASCII 文件名副本，避免部分后端/存储对非 ASCII 名的兼容问题
        orig_basename = os.path.basename(file_path)
        name, ext = os.path.splitext(orig_basename)
        safe_name = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '_' for ch in name)
        if not safe_name:
            safe_name = 'file'
        safe_basename = (safe_name[:80] + ext)  # 控制长度
        use_temp_copy = (orig_basename != safe_basename)
        temp_path = None

        def _cleanup_tmp():
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        # 业务级重试：整体流程最多 3 次，每次指数退避
        for attempt in range(1, 4):
            try:
                # 每次尝试都准备文件（如果需要临时复制）
                upload_file = file_path
                basename = orig_basename
                if use_temp_copy:
                    tmpdir = tempfile.gettempdir()
                    temp_path = os.path.join(tmpdir, safe_basename)
                    shutil.copyfile(file_path, temp_path)
                    upload_file = temp_path
                    basename = safe_basename

                url = self._mineru_url(MINERU_PATH_FILE_URLS)
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {MINERU_API_KEY}"
                }
                # 为避免 MinerU 接口对 files.data_id 长度的限制（<=128）而失败，
                # 这里使用基于原始安全文件名的短哈希作为 data_id（只包含 ASCII 十六进制），
                # 同时保留可读的 name 字段供后台展示。
                import hashlib
                # data_id 只保留 32 字节的十六进制串（长度 32），远小于 128 限制
                data_id_short = hashlib.sha256(basename.encode('utf-8')).hexdigest()[:32]
                payload = {
                    "enable_formula": MINERU_ENABLE_FORMULA,
                    "language": MINERU_LANGUAGE,
                    "enable_table": MINERU_ENABLE_TABLE,
                    "files": [
                        {"name": basename, "is_ocr": True, "data_id": data_id_short}
                    ]
                }

                print(f"📨 (尝试 {attempt}/3) 向 MinerU 申请上传链接: {basename}")
                print(f"   POST {url}")
                session = self._build_session()
                resp = session.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code != 200:
                    print(f"❌ 申请上传链接失败: HTTP {resp.status_code} {resp.text[:200]}")
                    raise RuntimeError("apply upload url failed")
                resj = resp.json()
                if resj.get('code') != 0:
                    print(f"❌ 申请上传链接返回错误: {resj}")
                    raise RuntimeError("apply upload url error code")

                data = resj.get('data', {})
                batch_id = data.get('batch_id')
                file_urls = data.get('file_urls') or []
                if not file_urls:
                    print("❌ 未获取到上传链接")
                    raise RuntimeError("no upload url")

                upload_url = file_urls[0]
                print(f"⬆️ (尝试 {attempt}/3) 上传文件到 MinerU: {upload_url}")
                with open(upload_file, 'rb') as fh:
                    rput = session.put(upload_url, data=fh, timeout=180)
                if rput.status_code not in (200, 201):
                    print(f"❌ 上传文件到 MinerU 失败: HTTP {rput.status_code}")
                    raise RuntimeError("upload file failed")

                if not batch_id:
                    print("⚠️ 未返回 batch_id，无法轮询结果")
                    raise RuntimeError("no batch id")

                print(f"⏳ 等待 MinerU 处理上传的文件（batch_id: {batch_id}）")
                batch_url = self._mineru_url(f"/api/v4/extract-results/batch/{batch_id}")
                waited = 0
                max_wait = 600
                poll_interval = 5
                extracted_text = ''
                while waited < max_wait:
                    try:
                        rb = session.get(batch_url, headers={"Authorization": f"Bearer {MINERU_API_KEY}"}, timeout=30)
                        if rb.status_code != 200:
                            print(f"⚠️ 轮询 batch 状态 HTTP {rb.status_code}, 等待 {poll_interval}s 重试")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue
                        jr = rb.json()
                        if jr.get('code') != 0:
                            print(f"⚠️ batch 状态返回错误: {jr}")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        results = jr.get('data', {}).get('extract_result', [])
                        if not results:
                            print(f"⏳ batch 尚未返回结果，等待 {poll_interval}s...")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        # 查找与文件名匹配的结果记录
                        target = None
                        for r in results:
                            if r.get('file_name') == basename or r.get('file_name') == orig_basename:
                                target = r
                                break
                        if not target:
                            print(f"⏳ 未在 batch 结果中找到目标文件，等待 {poll_interval}s...")
                            time.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        state = target.get('state')
                        if state == 'done':
                            full_zip = target.get('full_zip_url')
                            if full_zip:
                                print(f"⬇️ 下载解析结果压缩包: {full_zip}")
                                try:
                                    rzip = session.get(full_zip, stream=True, timeout=180)
                                    if rzip.status_code == 200:
                                        import zipfile
                                        tmpf = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                                        for chunk in rzip.iter_content(1024 * 1024):
                                            tmpf.write(chunk)
                                        tmpf.close()
                                        with zipfile.ZipFile(tmpf.name, 'r') as z:
                                            for zi in z.namelist():
                                                if zi.lower().endswith(('.txt', '.json', '.md', '.html')):
                                                    with z.open(zi) as fh:
                                                        try:
                                                            content = fh.read().decode('utf-8')
                                                        except Exception:
                                                            try:
                                                                content = fh.read().decode('gbk')
                                                            except Exception:
                                                                content = ''
                                                        if content:
                                                            extracted_text += content + '\n'
                                        try:
                                            os.unlink(tmpf.name)
                                        except Exception:
                                            pass
                                        if extracted_text:
                                            base_name = os.path.splitext(orig_basename)[0]
                                            txt_path = os.path.join(OCR_OUTPUT_DIR, f"{base_name}_ocr.txt")
                                            with open(txt_path, 'w', encoding='utf-8') as fo:
                                                fo.write(extracted_text)
                                            print(f"✅ 批量上传后解析成功，文本已保存: {txt_path}")
                                            _cleanup_tmp()
                                            return txt_path
                                    else:
                                        print(f"⚠️ 下载结果压缩包失败: HTTP {rzip.status_code}")
                                except Exception as e:
                                    print(f"⚠️ 下载/解析批量结果出错: {e}")
                            else:
                                print("⚠️ 任务完成但未返回 full_zip_url")
                            _cleanup_tmp()
                            return None

                        elif state == 'failed':
                            print(f"❌ 批量解析任务失败: {target.get('err_msg')}")
                            _cleanup_tmp()
                            return None
                        else:
                            print(f"⏳ 批量任务状态: {state}，等待 {poll_interval}s...")

                    except Exception as e:
                        print(f"⚠️ 轮询 batch 结果异常: {e}")
                    time.sleep(poll_interval)
                    waited += poll_interval

                print("❌ 批量解析超时或未返回结果")
                _cleanup_tmp()
                return None

            except Exception as e:
                print(f"⚠️ (尝试 {attempt}/3) 申请上传或上传过程异常: {e}")
                # 指数退避
                if attempt < 3:
                    wait_s = 2 ** attempt
                    print(f"   等待 {wait_s}s 后重试...")
                    time.sleep(wait_s)
                else:
                    print("❌ 多次尝试直传均失败")
            finally:
                _cleanup_tmp()
        return None
    
    def check_file_validity(self, file_path):
        """检查文件是否有效"""
        try:
            if not os.path.exists(file_path):
                print(f"❌ 文件不存在: {file_path}")
                return False
            
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                print(f"❌ 文件为空: {file_path}")
                return False
            
            if file_size > 600 * 1024 * 1024:  # 600MB 限制，与业务需求对齐
                print(f"❌ 文件过大 ({file_size / 1024 / 1024:.2f} MB): {file_path}")
                return False
            
            print(f"✅ 文件检查通过: {os.path.basename(file_path)} ({file_size / 1024 / 1024:.2f} MB)")
            return True
            
        except Exception as e:
            print(f"❌ 文件检查失败: {e}")
            return False

def start_monitoring(folder_path):
    """启动文件夹监控"""
    event_handler = FileHandler(folder_path)
    observer = Observer()
    observer.schedule(event_handler, folder_path, recursive=True)
    observer.start()
    print(f"🔍 开始监控文件夹: {folder_path}")
    
    try:
        print("📋 开始处理现有文件...")
        existing_files = []
        
        for root, dirs, files in os.walk(folder_path):
            for filename in files:
                if filename.lower().endswith((
                    '.txt', '.md', '.markdown', '.mdx', '.html', 
                    '.pdf', '.doc', '.docx', 
                    '.xlsx', '.xls', 
                    '.csv', 
                    '.ppt', '.pptx', 
                    '.eml', '.msg', 
                    '.xml', '.vtt', '.properties',
                    '.epub',
                    '.png', '.jpg', '.jpeg', '.tiff', '.bmp'
                )):
                    file_path = os.path.join(root, filename)
                    existing_files.append(file_path)
        
        print(f"📄 找到 {len(existing_files)} 个支持的文件")
        
        for file_path in existing_files:
            print(f"\n{'='*60}")
            print(f"🔄 处理现有文件: {os.path.relpath(file_path, folder_path)}")
            event_handler.process_file(file_path)
        
        print(f"\n{'='*60}")
        print("✅ 现有文件处理完成")
        print("🎯 监控服务已启动，按 Ctrl+C 停止监控...")
        
        while True:
            observer.join(1)
            
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号，正在停止监控...")
        observer.stop()
    except Exception as e:
        print(f"❌ 监控过程中出错: {str(e)}")
    finally:
        observer.join()
        print("👋 监控服务已停止")

if __name__ == "__main__":
    DOCS_FOLDER = "C:\\Users\\Moon\\Desktop\\0-政策文件-国土空间生态修复"
    
    if not os.path.exists(DOCS_FOLDER):
        print(f"❌ 文件夹不存在: {DOCS_FOLDER}")
        exit(1)
    
    if not os.path.isdir(DOCS_FOLDER):
        print(f"❌ 路径不是文件夹: {DOCS_FOLDER}")
        exit(1)
    
    print(f"📁 监控文件夹: {DOCS_FOLDER}")
    print("=" * 60)
    
    if ENABLE_MINERU_OCR:
        print(f"✅ MinerU OCR 已启用")
        print(f"🌐 文件服务器 URL: {FILE_SERVER_URL}")
        print(f"")
        print(f"⚠️ 重要提示：")
        print(f"   1. 请确保 ngrok 正在运行：ngrok http 8000")
        print(f"   2. 请确保 HTTP 服务器正在运行：python -m http.server 8000")
        print(f"   3. FILE_SERVER_URL 已设置为 ngrok 的公网 URL")
        print(f"")
        print(f"   如果没有配置 ngrok，请先阅读 setup_ngrok.md")
        print("=" * 60)
    else:
        print(f"ℹ️ MinerU OCR 已禁用，将直接上传文件到 Dify")
        print("=" * 60)
    
    try:
        start_monitoring(DOCS_FOLDER)
    except Exception as e:
        print(f"💥 程序运行出错: {str(e)}")
        import traceback
        traceback.print_exc()
