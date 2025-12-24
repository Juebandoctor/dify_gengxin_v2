"""
测试元数据同步功能
"""
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.config_loader import load_config
from utils.metadata_manager import MetadataManager
from utils.logger import log_info, log_success, log_warning

def test_metadata_sync():
    """测试元数据管理器的同步功能"""
    
    print("=" * 60)
    print("测试元数据同步功能")
    print("=" * 60)
    
    # 加载配置
    config = load_config('config.yaml')
    
    # 初始化元数据管理器
    metadata_config = config.get('metadata', {})
    csv_path = metadata_config.get('csv_path', './metadata/source_table.csv')
    
    metadata_mgr = MetadataManager(
        csv_path=csv_path,
        auto_create=metadata_config.get('auto_create', True),
        default_meta=metadata_config.get('default', {})
    )
    
    # 显示当前状态
    log_info(f"元数据文件: {csv_path}")
    log_info(f"当前记录数: {metadata_mgr.count()}")
    
    # 显示所有标题
    titles = metadata_mgr.get_all_titles()
    print("\n当前元数据记录：")
    for i, title in enumerate(titles[:20], 1):
        print(f"  {i}. {title}")
    
    if len(titles) > 20:
        print(f"  ... 以及其他 {len(titles) - 20} 条记录")
    
    # 测试删除功能（不实际删除）
    print("\n" + "=" * 60)
    log_warning("以下是测试模式，不会实际删除")
    print("=" * 60)
    
    # 模拟：假设 Dify 中有这些文档
    dify_doc_names = set(titles[:5])  # 假设只保留前 5 个
    
    print(f"\n假设 Dify 中只有 {len(dify_doc_names)} 个文档")
    to_delete = [t for t in titles if t not in dify_doc_names]
    
    if to_delete:
        print(f"\n模拟删除 {len(to_delete)} 条记录：")
        for i, title in enumerate(to_delete[:10], 1):
            print(f"  {i}. {title}")
        
        if len(to_delete) > 10:
            print(f"  ... 以及其他 {len(to_delete) - 10} 条记录")
    
    print("\n" + "=" * 60)
    log_success("✅ 测试完成！实际使用请运行：python sync_metadata.py")
    print("=" * 60)

if __name__ == '__main__':
    try:
        test_metadata_sync()
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
