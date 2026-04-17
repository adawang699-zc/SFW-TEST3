@echo off
REM 知识库授权工具可执行文件模拟器
REM 调用Python版本的授权工具

set "SCRIPT_DIR=%~dp0"
python "%SCRIPT_DIR%hx_knowledge_license_gender.py" %*
