@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ===============================
REM Universal Web-to-API 启动脚本
REM ===============================

cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo.
echo ========================================
echo   Universal Web-to-API 启动脚本
echo   模式A: 独立Chrome（不影响日常Chrome）
echo ========================================
echo.

REM ---------- 1) 加载 .env ----------
echo [STEP] 加载配置
echo ----------------------------------------

if exist ".env" (
    echo [INFO] 读取 .env 配置文件...
    
    REM 使用临时文件避免解析问题
    set "ENV_LOADED=0"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        set "ENV_KEY=%%A"
        set "ENV_VAL=%%B"
        if defined ENV_KEY (
            REM 跳过空行
            call set "%%A=%%B" 2>nul
            set "ENV_LOADED=1"
        )
    )
    echo [OK] 配置加载完成
) else (
    echo [WARN] 未找到 .env 文件，使用默认配置
)

REM 默认值兜底
if not defined APP_HOST set "APP_HOST=127.0.0.1"
if not defined APP_PORT set "APP_PORT=8199"
if not defined BROWSER_PORT set "BROWSER_PORT=9222"

echo.
echo   当前配置:
echo     APP_HOST     : %APP_HOST%
echo     APP_PORT     : %APP_PORT%
echo     BROWSER_PORT : %BROWSER_PORT%
echo.

REM ---------- 2) 检查 Python ----------
echo [STEP] 检查 Python 环境
echo ----------------------------------------

REM 检查 Python 命令是否存在
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 系统中未找到 Python
    echo.
    echo 解决方案:
    echo   1. 下载 Python: https://www.python.org/downloads/
    echo   2. 安装时勾选 "Add Python to PATH"
    echo   3. 重启命令行窗口
    echo.
    pause
    exit /b 1
)

REM 测试 Python 是否能运行
python -c "import sys" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 已安装但无法正常运行
    echo.
    echo 建议:
    echo   - 以管理员身份运行此脚本
    echo   - 重新安装 Python
    echo.
    pause
    exit /b 1
)

REM 获取版本信息（多种方法）
set "PYTHON_VERSION="
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%v"

if defined PYTHON_VERSION (
    echo [OK] 检测到 Python %PYTHON_VERSION%
) else (
    REM 降级显示
    echo [OK] Python 已安装并可用
    set "PYTHON_VERSION=未知版本"
)

REM 检查版本要求（不阻塞）
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [警告] 当前 Python 版本可能低于 3.8
    echo        某些功能可能无法正常使用
    echo        建议升级到 Python 3.8 或更高版本
    echo.
    timeout /t 5 /nobreak >nul
)

echo.
REM ---------- 3) 虚拟环境 ----------
echo [STEP] 准备虚拟环境
echo ----------------------------------------

if not exist "venv" (
    echo [INFO] 创建虚拟环境...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
echo [OK] 虚拟环境已激活
echo.

REM ---------- 4) 安装依赖 ----------
echo [STEP] 检查并安装依赖
echo ----------------------------------------

REM 检查 pip 是否需要升级
python -m pip install --upgrade pip -q

if exist "requirements.txt" (
    echo [INFO] 正在根据 requirements.txt 安装/更新依赖...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    echo [WARN] 未找到 requirements.txt，安装默认核心依赖...
    pip install fastapi uvicorn DrissionPage beautifulsoup4 python-dotenv pydantic -i https://pypi.tuna.tsinghua.edu.cn/simple
)

if %errorlevel% equ 0 (
    echo [OK] 依赖检查完成
) else (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)
echo.

REM ---------- 5) 启动 Chrome ----------
echo [STEP] 准备 Chrome 浏览器
echo ----------------------------------------

set "PROFILE_DIR=%PROJECT_DIR%\chrome_profile"
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%" >nul 2>&1

REM 检查调试端口
call :check_debug_port
if "!DEBUG_PORT_OK!"=="1" (
    echo [OK] 调试端口已就绪 - 端口 %BROWSER_PORT%
    goto :CHROME_READY
)

echo [INFO] 启动 Chrome 浏览器...

REM 查找 Chrome
set "CHROME_EXE="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
)
if not defined CHROME_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
if not defined CHROME_EXE if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if not defined CHROME_EXE (
    echo [ERROR] 找不到 Chrome 浏览器
    pause
    exit /b 1
)

echo [INFO] Chrome 路径: %CHROME_EXE%

REM 启动 Chrome
start "" "%CHROME_EXE%" --remote-debugging-port=%BROWSER_PORT% --user-data-dir="%PROFILE_DIR%" --no-first-run --no-default-browser-check about:blank

REM 等待端口就绪
echo [INFO] 等待 Chrome 就绪...
set "WAIT_COUNT=0"
:WAIT_LOOP
if !WAIT_COUNT! geq 15 goto :WAIT_DONE
call :check_debug_port
if "!DEBUG_PORT_OK!"=="1" goto :WAIT_DONE
set /a WAIT_COUNT+=1
timeout /t 1 /nobreak >nul
goto :WAIT_LOOP

:WAIT_DONE
if "!DEBUG_PORT_OK!"=="1" (
    echo [OK] Chrome 启动成功 - 端口 %BROWSER_PORT%
) else (
    echo [WARN] Chrome 启动超时，但会继续尝试
)

:CHROME_READY
echo.

REM ---------- 6) 启动服务 ----------
echo ========================================
echo   服务启动中...
echo ========================================
echo.
echo   API 地址:    http://%APP_HOST%:%APP_PORT%
echo   Dashboard:   http://%APP_HOST%:%APP_PORT%/dashboard
echo   API 文档:    http://%APP_HOST%:%APP_PORT%/docs
echo.
echo   按 Ctrl+C 停止服务
echo ========================================
echo.

python main.py

echo.
echo [INFO] 服务已停止
pause
exit /b 0

REM ===============================
REM 检查调试端口函数
REM ===============================
:check_debug_port
set "DEBUG_PORT_OK=0"
powershell -NoProfile -Command "try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', %BROWSER_PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 set "DEBUG_PORT_OK=1"
goto :eof
