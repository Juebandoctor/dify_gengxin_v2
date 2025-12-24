@echo off
chcp 65001 >nul
title Dify 知识库自动上传工具

echo.
echo ╔════════════════════════════════════════╗
echo ║   Dify 知识库自动上传工具（增强版）   ║
echo ╚════════════════════════════════════════╝
echo.

REM 检查配置文件
if not exist config.yaml (
    echo ❌ 配置文件 config.yaml 不存在
    echo 请先创建配置文件
    pause
    exit /b 1
)

REM 检查依赖
python -c "import yaml" 2>nul
if errorlevel 1 (
    echo ⚠️ 检测到缺少依赖包
    echo 是否现在安装？[Y/N]
    set /p install_deps=
    if /i "%install_deps%"=="Y" (
        call install_dependencies.bat
    ) else (
        echo 请手动运行: pip install pyyaml watchdog requests
        pause
        exit /b 1
    )
)

echo ✅ 环境检查通过
echo.
echo 启动监控服务...
echo.

python upload_enhanced.py

pause
