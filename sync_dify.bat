@echo off
chcp 65001 >nul
echo ========================================
echo   Dify 文档同步工具
echo ========================================
echo.

echo 这个工具将：
echo   1. 从 Dify 获取所有文档 ID
echo   2. 对比本地上传日志
echo   3. 删除已在 Dify 中删除的文档记录
echo.

echo 请选择操作：
echo   1 - 模拟运行（查看将要删除的记录）
echo   2 - 正式同步（实际删除记录）
echo   0 - 退出
echo.

set /p choice=请输入选项 [1/2/0]: 

if "%choice%"=="1" (
    echo.
    echo 执行模拟运行...
    python sync_metadata.py --dry-run
) else if "%choice%"=="2" (
    echo.
    echo 执行正式同步...
    python sync_metadata.py
) else if "%choice%"=="0" (
    echo 已退出
    exit /b 0
) else (
    echo 无效选项
    pause
    exit /b 1
)

echo.
echo ========================================
pause
