@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ===============================
REM Universal Web-to-API 启动脚本
REM v2.3 - DrissionPage 反检测补丁
REM ===============================

cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo.
echo ========================================
echo   Universal Web-to-API 启动脚本
echo ========================================
echo.

REM ---------- 1) 加载 .env ----------
echo [STEP] 加载配置
echo ----------------------------------------

if exist ".env" (
    echo [INFO] 读取 .env 配置文件...
    set "ENV_LOADED=0"
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do call :SetEnvVar "%%A" "%%B"
    echo [OK] 配置加载完成
) else (
    echo [WARN] 未找到 .env 文件，使用默认配置
)

REM 默认值兜底
if not defined APP_HOST set "APP_HOST=127.0.0.1"
if not defined APP_PORT set "APP_PORT=8199"
if not defined BROWSER_PORT set "BROWSER_PORT=9222"
if not defined AUTO_UPDATE_ENABLED set "AUTO_UPDATE_ENABLED=true"
if not defined GITHUB_REPO set "GITHUB_REPO=lumingya/universal-web-api"
if not defined PROXY_ENABLED set "PROXY_ENABLED=false"
if not defined PROXY_ADDRESS set "PROXY_ADDRESS="
if not defined PROXY_BYPASS set "PROXY_BYPASS=localhost,127.0.0.1"

echo.
echo   当前配置:
echo     APP_HOST     : %APP_HOST%
echo     APP_PORT     : %APP_PORT%
echo     BROWSER_PORT : %BROWSER_PORT%
echo     AUTO_UPDATE  : %AUTO_UPDATE_ENABLED%
if /I "%PROXY_ENABLED%"=="true" (
    echo     PROXY        : %PROXY_ADDRESS%
) else (
    echo     PROXY        : 已禁用
)
echo.

REM ---------- 2) 检查 Python（增强版） ----------
echo [STEP] 检查 Python 环境
echo ----------------------------------------

REM 检查 python 命令是否存在
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python 命令
    echo.
    echo   可能的原因:
    echo     1. 尚未安装 Python
    echo     2. Python 未添加到系统 PATH 环境变量
    echo.
    echo   解决方案:
    echo     1. 从 https://www.python.org/downloads/ 下载安装 Python 3.8+
    echo     2. 安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

REM 获取 python 命令的实际路径（只取第一个结果）
set "PYTHON_PATH="
for /f "tokens=*" %%i in ('where python 2^>nul') do (
    if not defined PYTHON_PATH set "PYTHON_PATH=%%i"
)

REM 检测 Windows Store 占位符
set "IS_STORE_PYTHON=0"
echo "!PYTHON_PATH!" | findstr /i "WindowsApps" >nul 2>&1
if !errorlevel! equ 0 set "IS_STORE_PYTHON=1"

if "!IS_STORE_PYTHON!"=="1" (
    echo [ERROR] 检测到 Windows Store Python 占位符
    echo.
    echo   路径: !PYTHON_PATH!
    echo.
    echo   这不是真正的 Python，而是 Windows Store 的跳转链接。
    echo   它会导致虚拟环境创建失败。
    echo.
    echo   解决方案:
    echo     1. 按 Win+I 打开设置
    echo     2. 搜索 "应用执行别名" 或 "管理应用执行别名"
    echo     3. 找到 python.exe 和 python3.exe，将它们关闭
    echo     4. 从 https://www.python.org/downloads/ 安装完整版 Python
    echo        安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

REM 尝试获取版本号（方法1: sys.version_info）
set "PYTHON_VERSION="
for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do set "PYTHON_VERSION=%%i"

REM 方法2: 如果方法1失败，尝试解析 --version 输出
if not defined PYTHON_VERSION (
    for /f "tokens=2 delims= " %%i in ('python --version 2^>^&1') do (
        for /f "tokens=1,2 delims=." %%a in ("%%i") do set "PYTHON_VERSION=%%a.%%b"
    )
)

REM 检查版本号是否获取成功
if not defined PYTHON_VERSION (
    echo [ERROR] 无法获取 Python 版本信息
    echo.
    echo   检测到的 Python 路径: !PYTHON_PATH!
    echo.
    echo   可能的原因:
    echo     1. Python 安装不完整或已损坏
    echo     2. Python 解释器无法正常执行
    echo.
    echo   诊断步骤 - 请手动运行以下命令:
    echo     python --version
    echo     python -c "print('hello')"
    echo.
    echo   如果上述命令报错，请重新安装 Python:
    echo     https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM 检查版本是否满足要求 (>= 3.8)
set "PY_MAJOR="
set "PY_MINOR="
for /f "tokens=1,2 delims=." %%a in ("!PYTHON_VERSION!") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)

set "VERSION_OK=0"
if defined PY_MAJOR if defined PY_MINOR (
    if !PY_MAJOR! gtr 3 set "VERSION_OK=1"
    if !PY_MAJOR! equ 3 if !PY_MINOR! geq 8 set "VERSION_OK=1"
)

if "!VERSION_OK!"=="0" (
    echo [ERROR] Python 版本过低
    echo.
    echo   当前版本: Python !PYTHON_VERSION!
    echo   最低要求: Python 3.8+
    echo.
    echo   请从 https://www.python.org/downloads/ 下载最新版本
    echo.
    pause
    exit /b 1
)

echo [OK] Python !PYTHON_VERSION!
echo     路径: !PYTHON_PATH!
echo.

REM ---------- 3) 自动更新检查 ----------
if /I "%AUTO_UPDATE_ENABLED%"=="true" (
    echo [STEP] 自动更新检查
    echo ----------------------------------------
    echo.
    echo   +----------------------------------------------+
    echo   ^|  [WARN] 自动更新已启用                       ^|
    echo   ^|                                              ^|
    echo   ^|  更新会覆盖以下内容:                         ^|
    echo   ^|    - config/*.json                           ^|
    echo   ^|    - app/                                    ^|
    echo   ^|    - static/                                 ^|
    echo   ^|                                              ^|
    echo   ^|  原配置会自动备份到 backup_* 目录            ^|
    echo   +----------------------------------------------+
    echo.
    
    if exist "updater.py" (
        echo [INFO] 检查 GitHub 最新版本...
        python updater.py
        
        if !errorlevel! equ 0 (
            echo [INFO] 更新已应用，建议重新启动脚本
            echo.
            set /p RESTART_CHOICE="是否立即重启? [Y/n]: "
            if /I "!RESTART_CHOICE!"=="" set "RESTART_CHOICE=Y"
            if /I "!RESTART_CHOICE!"=="Y" (
                echo [INFO] 重新启动...
                start "" "%~f0"
                exit /b 0
            )
        )
    ) else (
        echo [WARN] 未找到 updater.py，跳过自动更新
    )
    echo.
) else (
    echo [INFO] 自动更新已禁用
    echo       如需启用，请修改 .env 中的 AUTO_UPDATE_ENABLED=true
    echo.
)

REM ---------- 4) 检查目录结构 ----------
echo [STEP] 检查项目结构
echo ----------------------------------------

set "STRUCTURE_OK=1"

if not exist "app\core\browser.py" (
    echo [ERROR] 缺失: app\core\browser.py
    set "STRUCTURE_OK=0"
)
if not exist "app\services\config_engine.py" (
    echo [ERROR] 缺失: app\services\config_engine.py
    set "STRUCTURE_OK=0"
)
if not exist "config\sites.json" (
    echo [WARN] 缺失: config\sites.json，将自动创建
    if not exist "config" mkdir "config"
    echo {"_global": {"selector_definitions": []}} > "config\sites.json"
    echo [INFO] 已创建空配置文件
) else (
    echo [OK] 找到: config\sites.json
)
if not exist "main.py" (
    echo [ERROR] 缺失: main.py
    set "STRUCTURE_OK=0"
)

if "!STRUCTURE_OK!"=="0" (
    echo.
    echo [ERROR] 项目结构不完整，请检查文件是否齐全
    pause
    exit /b 1
)

echo [OK] 项目结构检查通过
echo.

REM ---------- 5) 虚拟环境（增强版） ----------
echo [STEP] 准备虚拟环境
echo ----------------------------------------

if not exist "venv" (
    echo [INFO] 创建虚拟环境...
    python -m venv venv 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] 创建虚拟环境失败
        echo.
        echo   可能的原因:
        echo     1. Python 安装不完整（缺少 venv 模块）
        echo     2. 当前目录没有写入权限
        echo     3. 磁盘空间不足
        echo     4. 杀毒软件阻止
        echo.
        echo   解决方案:
        echo     1. 确保安装了完整版 Python（非精简版）
        echo     2. 尝试以管理员身份运行此脚本
        echo     3. 尝试运行: python -m ensurepip --upgrade
        echo     4. 临时关闭杀毒软件后重试
        echo.
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境创建成功
) else (
    echo [OK] 虚拟环境已存在
)

REM 检查虚拟环境完整性
if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERROR] 虚拟环境损坏，缺少 activate.bat
    echo.
    echo   解决方案:
    echo     1. 删除 venv 文件夹: rmdir /s /q venv
    echo     2. 重新运行此脚本
    echo.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo.
    echo [ERROR] 虚拟环境损坏，缺少 python.exe
    echo.
    echo   解决方案:
    echo     1. 删除 venv 文件夹: rmdir /s /q venv
    echo     2. 重新运行此脚本
    echo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo [OK] 虚拟环境已激活
echo.

REM ---------- 6) 安装依赖（增强版） ----------
echo [STEP] 检查依赖
echo ----------------------------------------

REM 检查 requirements.txt 是否存在
if not exist "requirements.txt" (
    echo [ERROR] 缺少 requirements.txt 文件
    echo.
    echo   请确保项目文件完整，或从 GitHub 重新下载
    echo.
    pause
    exit /b 1
)

REM 检查 requirements.txt 的 hash 是否变化
set "REQ_HASH_FILE=venv\.req_hash"
set "CURRENT_HASH="
for /f "tokens=*" %%i in ('certutil -hashfile requirements.txt MD5 2^>nul ^| findstr /v ":"') do (
    if not defined CURRENT_HASH set "CURRENT_HASH=%%i"
)

if not defined CURRENT_HASH (
    echo [WARN] 无法计算依赖文件哈希，将强制安装
    set "NEED_INSTALL=1"
) else (
    set "NEED_INSTALL=0"
    if not exist "!REQ_HASH_FILE!" set "NEED_INSTALL=1"
    if exist "!REQ_HASH_FILE!" (
        set /p OLD_HASH=<"!REQ_HASH_FILE!"
        if not "!OLD_HASH!"=="!CURRENT_HASH!" set "NEED_INSTALL=1"
    )
)

if "!NEED_INSTALL!"=="1" (
    echo [INFO] 安装 Python 依赖包...
    echo.
    pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] 依赖安装失败
        echo.
        echo   可能的原因:
        echo     1. 网络连接问题（无法访问 PyPI）
        echo     2. pip 版本过低
        echo     3. 某些包需要 C++ 编译器
        echo.
        echo   解决方案:
        echo     1. 检查网络连接，尝试访问 https://pypi.org
        echo     2. 升级 pip: python -m pip install --upgrade pip
        echo     3. 使用国内镜像:
        echo        pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        echo.
        pause
        exit /b 1
    )
    echo !CURRENT_HASH!> "!REQ_HASH_FILE!"
    echo.
    echo [OK] 依赖安装完成
) else (
    echo [OK] 依赖已是最新
)
echo.

REM ---------- 6.5) DrissionPage 反检测补丁 ----------
echo [STEP] 应用 DrissionPage 补丁
echo ----------------------------------------

if exist "patch_drissionpage.py" (
    python patch_drissionpage.py
    if !errorlevel! neq 0 (
        echo [WARN] 补丁应用失败，网络监听模式可能触发 CF 检测
        echo        项目仍可正常运行（DOM 模式不受影响）
    )
) else (
    echo [WARN] 未找到 patch_drissionpage.py，跳过补丁
)
echo.

REM ---------- 7) 启动浏览器 ----------
echo [STEP] 准备 Chromium 内核浏览器
echo ----------------------------------------

set "PROFILE_DIR=%PROJECT_DIR%\chrome_profile"
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%" >nul 2>&1

REM 每次启动前自动清理配置文件
if exist "clean_profile.py" (
    echo [INFO] 执行浏览器配置瘦身...
    python clean_profile.py "%PROFILE_DIR%"
    echo.
) else (
    echo [WARN] 未找到 clean_profile.py，跳过清理
    echo.
)

REM 检查调试端口
call :check_debug_port
if "!DEBUG_PORT_OK!"=="1" (
    echo [OK] 调试端口已就绪 - 端口 %BROWSER_PORT%
    goto :BROWSER_READY
)

echo [INFO] 正在查找可用的 Chromium 内核浏览器...

REM 初始化变量
set "BROWSER_EXE="
set "BROWSER_NAME="

REM ========== 优先级1: 用户自定义路径 ==========
if defined BROWSER_PATH call :CheckCustomBrowser
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 优先级2: Chrome ==========
call :CheckBrowser "C:\Program Files\Google\Chrome\Application\chrome.exe" "Chrome"
if defined BROWSER_EXE goto :BROWSER_FOUND

call :CheckBrowser "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" "Chrome"
if defined BROWSER_EXE goto :BROWSER_FOUND

set "TEST_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
call :CheckBrowser "!TEST_PATH!" "Chrome"
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 优先级3: Microsoft Edge ==========
call :CheckBrowser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" "Edge"
if defined BROWSER_EXE goto :BROWSER_FOUND

call :CheckBrowser "C:\Program Files\Microsoft\Edge\Application\msedge.exe" "Edge"
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 优先级4: Brave ==========
set "TEST_PATH=%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"
call :CheckBrowser "!TEST_PATH!" "Brave"
if defined BROWSER_EXE goto :BROWSER_FOUND

call :CheckBrowser "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" "Brave"
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 优先级5: Vivaldi ==========
set "TEST_PATH=%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe"
call :CheckBrowser "!TEST_PATH!" "Vivaldi"
if defined BROWSER_EXE goto :BROWSER_FOUND

call :CheckBrowser "C:\Program Files\Vivaldi\Application\vivaldi.exe" "Vivaldi"
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 优先级6: Opera ==========
set "TEST_PATH=%LOCALAPPDATA%\Programs\Opera\opera.exe"
call :CheckBrowser "!TEST_PATH!" "Opera"
if defined BROWSER_EXE goto :BROWSER_FOUND

call :CheckBrowser "C:\Program Files\Opera\opera.exe" "Opera"
if defined BROWSER_EXE goto :BROWSER_FOUND

REM ========== 未找到任何浏览器 ==========
echo.
echo [ERROR] 找不到任何可用的 Chromium 内核浏览器
echo.
echo   已检测以下浏览器 (按优先级排序):
echo     1. Chrome
echo     2. Microsoft Edge
echo     3. Brave
echo     4. Vivaldi
echo     5. Opera
echo.
echo   解决方案:
echo     - 安装上述任一浏览器
echo     - 或在 .env 文件中设置 BROWSER_PATH=你的浏览器完整路径
echo.
pause
exit /b 1

:BROWSER_FOUND
echo [OK] 检测到 !BROWSER_NAME!
echo [INFO] 路径: !BROWSER_EXE!

REM 构造浏览器启动参数
set "BROWSER_ARGS=--remote-debugging-port=%BROWSER_PORT% --user-data-dir="%PROFILE_DIR%" --no-first-run --no-default-browser-check --disable-backgrounding-occluded-windows --disable-background-timer-throttling --disable-renderer-backgrounding"

REM 添加代理参数（如果启用）
if /I "%PROXY_ENABLED%"=="true" (
    if defined PROXY_ADDRESS (
        set "BROWSER_ARGS=!BROWSER_ARGS! --proxy-server=%PROXY_ADDRESS%"
        if defined PROXY_BYPASS (
            set "BROWSER_ARGS=!BROWSER_ARGS! --proxy-bypass-list=%PROXY_BYPASS%"
        )
        echo [INFO] 代理已启用: %PROXY_ADDRESS%
    )
)

REM 启动浏览器
start "" "!BROWSER_EXE!" !BROWSER_ARGS! about:blank

REM 等待端口就绪
echo [INFO] 等待 !BROWSER_NAME! 就绪...
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
    echo [OK] !BROWSER_NAME! 启动成功 - 端口 %BROWSER_PORT%
) else (
    echo [WARN] !BROWSER_NAME! 启动超时，但会继续尝试
)

:BROWSER_READY
echo.

REM ---------- 8) 显示版本信息 ----------
if exist "VERSION" (
    echo   版本信息:
    echo   ----------------------------------------
    type VERSION
    echo.
    echo   ----------------------------------------
)

REM ---------- 9) 启动服务 ----------
echo ========================================
echo   服务启动中...
echo ========================================
echo.
echo   API 地址:    http://%APP_HOST%:%APP_PORT%
echo   控制面板:    http://%APP_HOST%:%APP_PORT%/
echo   API 文档:    http://%APP_HOST%:%APP_PORT%/docs
echo.
echo   项目结构:
echo     配置目录:  %PROJECT_DIR%\config
echo     静态资源:  %PROJECT_DIR%\static
echo.
if /I "%AUTO_UPDATE_ENABLED%"=="true" (
    echo   [WARN] 自动更新: 已启用
) else (
    echo   自动更新: 已禁用
)
echo.
echo   按 Ctrl+C 停止服务
echo   配置修改后会自动重启
echo ========================================
echo.

REM ========== 循环重启机制 ==========
:SERVICE_LOOP

python main.py
set "EXIT_CODE=!errorlevel!"

if !EXIT_CODE! equ 0 (
    REM 正常退出（用户按 Ctrl+C）
    echo.
    echo [INFO] 服务已停止
    pause
    exit /b 0
)

if !EXIT_CODE! equ 3 (
    REM 退出码 3 = 配置更新需要重启
    echo.
    echo ========================================
    echo   检测到配置更新，正在重启服务...
    echo ========================================
    timeout /t 2 /nobreak >nul
    goto :SERVICE_LOOP
)

REM 其他退出码（异常退出）
echo.
echo [ERROR] 服务异常退出 (退出码: !EXIT_CODE!)
echo [INFO] 3 秒后自动重启...
timeout /t 3 /nobreak >nul
goto :SERVICE_LOOP

REM ===============================
REM 子程序区域
REM ===============================

:check_debug_port
set "DEBUG_PORT_OK=0"
powershell -NoProfile -Command "try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', %BROWSER_PORT%); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 set "DEBUG_PORT_OK=1"
goto :eof

:SetEnvVar
REM 安全设置环境变量（处理包含括号的路径）
if not "%~1"=="" (
    set "%~1=%~2"
    set "ENV_LOADED=1"
)
goto :eof

:CheckCustomBrowser
REM 检查用户自定义浏览器路径
if exist "!BROWSER_PATH!" (
    set "BROWSER_EXE=!BROWSER_PATH!"
    set "BROWSER_NAME=自定义浏览器"
    echo [INFO] 使用自定义浏览器路径
) else (
    echo [WARN] BROWSER_PATH 指定的路径不存在: !BROWSER_PATH!
)
goto :eof

:CheckBrowser
REM 参数: %~1=路径, %~2=浏览器名称
if exist "%~1" (
    set "BROWSER_EXE=%~1"
    set "BROWSER_NAME=%~2"
)
goto :eof