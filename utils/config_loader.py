"""
配置管理模块
负责加载和验证 config.yaml
"""
import os
import yaml


def load_config(config_path="config.yaml"):
    """加载配置文件"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 验证必需配置项
    _validate_config(config)
    
    return config


def _validate_config(config):
    """验证配置完整性"""
    required = ["dify", "mineru", "document", "indexing"]
    for key in required:
        if key not in config:
            raise ValueError(f"配置文件缺少必需项: {key}")
    
    # 验证 Dify 配置
    dify = config["dify"]
    if not all(k in dify for k in ["base_url", "dataset_id", "api_key"]):
        raise ValueError("Dify 配置不完整")
    
    return True


def get_config_value(config, *keys, default=None):
    """安全获取嵌套配置值"""
    result = config
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key, default)
        else:
            return default
    return result
