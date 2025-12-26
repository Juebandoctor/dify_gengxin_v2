import sys
import traceback

print("--- 诊断开始 ---")

print("1. 正在尝试导入 PyMuPDF (fitz)...")
try:
    import fitz
    print("✅ PyMuPDF (fitz) 导入成功")
except Exception:
    print("❌ PyMuPDF 导入失败:")
    traceback.print_exc()

print("\n2. 正在尝试导入 PaddleOCR 核心 (PPStructure)...")
try:
    # 这里是主程序报错的地方
    from paddleocr import PPStructure
    print("✅ PaddleOCR (PPStructure) 导入成功")
except Exception:
    print("❌ PaddleOCR 导入失败 (这就是问题所在):")
    traceback.print_exc()

print("\n3. 正在尝试导入版面分析依赖...")
try:
    from paddleocr.ppstructure.recovery.recovery_to_doc import sorted_layout_boxes
    print("✅ 依赖库全 OK")
except Exception:
    print("❌ 依赖库导入失败:")
    traceback.print_exc()

print("--- 诊断结束 ---")