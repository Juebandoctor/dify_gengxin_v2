"""
Dify 文档同步工具
用于同步删除本地日志中已在 Dify 删除的文档记录
"""
import sys
import os
import requests

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from utils.config_loader import load_config
    from utils.upload_logger import UploadLogger
    from utils.metadata_manager import MetadataManager
    from utils.logger import log_info, log_success, log_warning, log_error, print_header
except ImportError:
    print("❌ 请先安装依赖: pip install pyyaml requests")
    sys.exit(1)


def get_dify_documents(config):
    """从 Dify 获取所有文档信息（ID 和名称）"""
    base_url = config['dify']['base_url']
    dataset_id = config['dify']['dataset_id']
    api_key = config['dify']['api_key']
    
    headers = {
        'Authorization': f'Bearer {api_key}'
    }
    
    url = f"{base_url}/v1/datasets/{dataset_id}/documents"
    
    try:
        log_info("正在从 Dify 获取文档列表...")
        
        all_documents = []
        page = 1
        limit = 100
        
        while True:
            params = {
                'page': page,
                'limit': limit
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                log_error(f"获取文档列表失败: {response.status_code}")
                log_error(f"响应: {response.text[:200]}")
                return None
            
            data = response.json()
            documents = data.get('data', [])
            
            if not documents:
                break
            
            for doc in documents:
                doc_id = doc.get('id')
                doc_name = doc.get('name', '')
                if doc_id:
                    all_documents.append({
                        'id': doc_id,
                        'name': doc_name
                    })
            
            log_info(f"  第 {page} 页: {len(documents)} 个文档")
            
            # 检查是否还有更多页
            if len(documents) < limit:
                break
            
            page += 1
        
        log_success(f"成功获取 {len(all_documents)} 个文档")
        return all_documents
    
    except Exception as e:
        log_error(f"获取文档列表出错: {e}")
        return None


def sync_metadata(config_path="config.yaml", dry_run=False):
    """
    同步元数据
    
    Args:
        config_path: 配置文件路径
        dry_run: 是否仅模拟运行（不实际删除）
    """
    print_header("Dify 文档同步工具")
    
    # 加载配置
    try:
        config = load_config(config_path)
        log_success("配置文件加载成功")
    except Exception as e:
        log_error(f"加载配置失败: {e}")
        return False
    
    # 初始化日志管理器
    db_path = config.get('database', {}).get('sqlite_path', './upload_log.db')
    upload_logger = UploadLogger(db_path)
    
    # 初始化元数据管理器
    metadata_config = config.get('metadata', {})
    csv_path = metadata_config.get('csv_path', './metadata/source_table.csv')
    metadata_manager = MetadataManager(
        csv_path=csv_path,
        auto_create=metadata_config.get('auto_create', True),
        default_meta=metadata_config.get('default', {})
    )
    
    # 获取本地记录的文档 ID
    local_doc_ids = upload_logger.get_all_dify_doc_ids()
    log_info(f"本地数据库中有 {len(local_doc_ids)} 条上传记录")
    
    # 获取本地元数据表中的记录
    local_metadata_titles = metadata_manager.get_all_titles()
    log_info(f"本地元数据表中有 {len(local_metadata_titles)} 条记录")
    
    # 获取 Dify 中的文档
    dify_documents = get_dify_documents(config)
    
    if dify_documents is None:
        log_error("无法获取 Dify 文档列表，同步终止")
        return False
    
    log_info(f"Dify 知识库中有 {len(dify_documents)} 个文档")
    
    # 提取 Dify 文档 ID 和文件名（不含扩展名）
    dify_doc_ids = [doc['id'] for doc in dify_documents]
    dify_doc_names = set()
    for doc in dify_documents:
        name = doc['name']
        # 移除扩展名（包括 _ocr.md）
        if name.endswith('_ocr.md'):
            name = name[:-7]  # 移除 _ocr.md
        elif '.' in name:
            name = os.path.splitext(name)[0]  # 移除普通扩展名
        dify_doc_names.add(name)
    
    # 找出需要从数据库删除的记录（通过文档 ID）
    db_to_delete = set(local_doc_ids) - set(dify_doc_ids)
    
    # 找出需要从元数据表删除的记录（通过文件名）
    csv_to_delete = []
    for title in local_metadata_titles:
        # 尝试多种匹配方式
        normalized_title = title.replace('《', '').replace('》', '').replace('（', '').replace('）', '').strip()
        
        # 检查是否在 Dify 中存在
        found = False
        for dify_name in dify_doc_names:
            normalized_dify = dify_name.replace('《', '').replace('》', '').replace('（', '').replace('）', '').strip()
            if normalized_title == normalized_dify or normalized_title in normalized_dify or normalized_dify in normalized_title:
                found = True
                break
        
        if not found:
            csv_to_delete.append(title)
    
    # 显示同步结果
    if not db_to_delete and not csv_to_delete:
        log_success("✅ 本地记录与 Dify 完全同步，无需清理")
        return True
    
    print("\n" + "="*50)
    if db_to_delete:
        log_warning(f"数据库：发现 {len(db_to_delete)} 条需要清理的记录")
        print("\n待删除的数据库记录（文档 ID）：")
        for i, doc_id in enumerate(list(db_to_delete)[:10], 1):
            print(f"  {i}. {doc_id}")
        if len(db_to_delete) > 10:
            print(f"  ... 以及其他 {len(db_to_delete) - 10} 条记录")
    
    if csv_to_delete:
        log_warning(f"元数据表：发现 {len(csv_to_delete)} 条需要清理的记录")
        print("\n待删除的元数据记录（标题）：")
        for i, title in enumerate(csv_to_delete[:10], 1):
            print(f"  {i}. {title}")
        if len(csv_to_delete) > 10:
            print(f"  ... 以及其他 {len(csv_to_delete) - 10} 条记录")
    
    print("="*50 + "\n")
    
    if dry_run:
        log_warning("⚠️ 这是模拟运行，不会实际删除记录")
        return True
    
    # 确认删除
    print("是否继续删除这些记录？[y/N]: ", end='')
    confirm = input().strip().lower()
    
    if confirm != 'y':
        log_info("操作已取消")
        return False
    
    # 执行同步删除
    total_deleted = 0
    
    # 1. 删除数据库记录
    if db_to_delete:
        db_deleted = upload_logger.sync_with_dify(dify_doc_ids)
        if db_deleted > 0:
            log_success(f"✅ 数据库：成功删除 {db_deleted} 条记录")
            total_deleted += db_deleted
    
    # 2. 删除元数据表记录
    if csv_to_delete:
        csv_deleted = metadata_manager.delete_by_titles(csv_to_delete)
        if csv_deleted > 0:
            log_success(f"✅ 元数据表：成功删除 {csv_deleted} 条记录")
            total_deleted += csv_deleted
    
    if total_deleted > 0:
        log_success(f"\n🎉 同步完成！总共删除 {total_deleted} 条记录")
    else:
        log_warning("未删除任何记录")
    
    # 显示同步后统计
    stats = upload_logger.get_statistics()
    print("\n同步后统计：")
    log_info(f"  成功上传: {stats['total_success']} 个文件")
    log_info(f"  失败记录: {stats['total_failed']} 个")
    log_info(f"  总大小: {stats['total_size_mb']} MB")
    
    return True


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Dify 文档同步工具')
    parser.add_argument('--config', default='config.yaml', help='配置文件路径')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行，不实际删除')
    
    args = parser.parse_args()
    
    try:
        success = sync_metadata(args.config, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log_warning("\n操作已取消")
        sys.exit(1)
    except Exception as e:
        log_error(f"同步过程出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
