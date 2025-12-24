@echo off
chcp 65001 >nul
echo ========================================
echo   Dify 知识库工具 - 依赖安装
echo ========================================
echo.

echo [1/3] 检查 Python 环境...
python --version
if errorlevel 1 (
    echo ❌ Python 未安装或未添加到 PATH
    echo 请先安装 Python 3.7+
    pause
    exit /b 1
)
echo ✅ Python 环境正常
echo.

echo [2/3] 安装必需依赖...
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ^
    pyyaml ^
    watchdog ^
    requests ^
    urllib3
    
if errorlevel 1 (
    echo.
    echo ⚠️ 使用清华源安装失败，尝试默认源...
    pip install pyyaml watchdog requests urllib3
)

echo.
echo [3/3] 验证安装...
python -c "import yaml; import watchdog; import requests; print('✅ 所有依赖安装成功')"

if errorlevel 1 (
    echo ❌ 依赖验证失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 下一步：
echo   1. 编辑 config.yaml 配置文件
echo   2. 运行: python upload_enhanced.py
echo.
pause
