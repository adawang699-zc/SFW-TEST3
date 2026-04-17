@echo off
REM 知识库授权工具演示脚本
REM 用于测试授权管理功能

setlocal enabledelayedexpansion

REM 解析命令行参数
set "action="
set "json_data="
set "output_file="
set "input_file="

:parse_args
if "%~1"=="" goto end_parse
if "%~1"=="gen" (
    set "action=gen"
    shift
    goto parse_args
)
if "%~1"=="dec" (
    set "action=dec"
    shift
    goto parse_args
)
if "%~1"=="--json" (
    set "json_data=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="-o" (
    set "output_file=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="-i" (
    set "input_file=%~2"
    shift
    shift
    goto parse_args
)
if "%~1"=="--help" (
    goto show_help
)
shift
goto parse_args

:end_parse

REM 执行相应操作
if "%action%"=="gen" goto generate_license
if "%action%"=="dec" goto decrypt_license
goto show_help

:generate_license
echo [INFO] 生成知识库授权文件
echo [INFO] JSON数据: %json_data%
echo [INFO] 输出文件: %output_file%

REM 解析JSON数据（简化版本）
for /f "tokens=2 delims=:" %%a in ('echo %json_data% ^| findstr "machinecode"') do (
    set "temp=%%a"
    set "machinecode=!temp:"=!"
    set "machinecode=!machinecode:,=!"
)

REM 创建授权文件内容
echo # 知识库授权文件 > "%output_file%"
echo # 生成时间: %date% %time% >> "%output_file%"
echo # 机器码: %machinecode% >> "%output_file%"
echo. >> "%output_file%"
echo [LICENSE] >> "%output_file%"
echo machinecode=%machinecode% >> "%output_file%"
echo vul_expire=30 >> "%output_file%"
echo virus_expire=60 >> "%output_file%"
echo rules_expire=50 >> "%output_file%"
echo generated_time=%date% %time% >> "%output_file%"
echo status=valid >> "%output_file%"

echo [SUCCESS] 授权文件已生成: %output_file%
exit /b 0

:decrypt_license
echo [INFO] 解密知识库授权文件
echo [INFO] 输入文件: %input_file%

if not exist "%input_file%" (
    echo [ERROR] 授权文件不存在: %input_file%
    exit /b 1
)

echo [INFO] 授权文件内容:
echo ----------------------------------------
type "%input_file%"
echo ----------------------------------------
echo [SUCCESS] 授权文件解密完成
exit /b 0

:show_help
echo 知识库授权工具演示版本
echo.
echo 使用方法:
echo   生成授权: %~nx0 gen --json "JSON数据" -o "输出文件"
echo   解密授权: %~nx0 dec -i "输入文件"
echo.
echo 示例:
echo   %~nx0 gen --json "{\"machinecode\":\"123456\"}" -o "test.lic"
echo   %~nx0 dec -i "test.lic"
echo.
exit /b 0
