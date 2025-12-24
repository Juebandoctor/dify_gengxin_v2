"""
日志工具模块
提供统一的日志输出格式
"""
import datetime


def log_info(msg, prefix="INFO"):
    """输出信息日志"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{prefix}] {timestamp} - {msg}")


def log_success(msg):
    """输出成功日志"""
    log_info(f"✅ {msg}", "SUCCESS")


def log_error(msg):
    """输出错误日志"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ERROR] {timestamp} - ❌ {msg}")


def log_warning(msg):
    """输出警告日志"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[WARNING] {timestamp} - ⚠️ {msg}")


def log_debug(msg):
    """输出调试日志"""
    log_info(msg, "DEBUG")


def print_separator(char="=", length=60):
    """打印分隔线"""
    print(char * length)


def print_header(title):
    """打印标题"""
    print_separator()
    print(f"  {title}")
    print_separator()
