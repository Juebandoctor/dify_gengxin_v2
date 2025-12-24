@echo off
chcp 65001 >nul
title 元数据同步测试工具

echo.
echo ========================================
echo   元数据同步测试工具
echo ========================================
echo.
echo 此工具用于测试和执行元数据同步功能
echo.
echo 同步内容：
echo   1. SQLite 数据库 (upload_log.db)
echo   2. CSV 元数据表 (metadata/source_table.csv)
echo.
echo ========================================
echo.

:MENU
echo 请选择操作：
echo.
echo   [1] 查看当前状态（测试模式）
echo   [2] 模拟同步（--dry-run，不实际删除）
echo   [3] 正式同步（实际删除冗余记录）
echo   [0] 退出
echo.
set /p choice="请输入选项 [0-3]: "

if "%choice%"=="0" goto END
if "%choice%"=="1" goto TEST
if "%choice%"=="2" goto DRYRUN
if "%choice%"=="3" goto SYNC

echo.
echo ❌ 无效选项，请重新输入
echo.
goto MENU

:TEST
echo.
echo ========================================
echo   查看当前状态
echo ========================================
echo.
python test_sync.py
echo.
pause
goto MENU

:DRYRUN
echo.
echo ========================================
echo   模拟同步（不实际删除）
echo ========================================
echo.
python sync_metadata.py --dry-run
echo.
pause
goto MENU

:SYNC
echo.
echo ========================================
echo   正式同步
echo ========================================
echo.
echo ⚠️  警告：此操作将实际删除本地冗余记录！
echo.
set /p confirm="确认执行？(输入 YES 确认): "
if /i not "%confirm%"=="YES" (
    echo.
    echo ❌ 操作已取消
    echo.
    pause
    goto MENU
)
echo.
python sync_metadata.py
echo.
pause
goto MENU

:END
echo.
echo 感谢使用！
echo.
timeout /t 2 /nobreak >nul
