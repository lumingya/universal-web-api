"""R2-6：API 进程负责启动与关闭 worker 进程。"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from app.core.config import get_logger
from app.worker import MODE_ENV, PORT_ENV, ROLE_ENV, TOKEN_ENV, URL_ENV
from app.worker.forwarding import PROXY_SECRET_ENV

logger = get_logger("WORKER_SUP")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WorkerSupervisor:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None

    def start(self, ready_timeout: float = 90.0) -> str:
        """启动 worker 并等待就绪，返回其地址；同时设置本进程的 UWAPI_WORKER_URL / TOKEN / 代理密钥。"""
        port = int(os.getenv(PORT_ENV) or 0) or _free_port()
        token = secrets.token_urlsafe(32)
        proxy_secret = os.getenv(PROXY_SECRET_ENV) or secrets.token_urlsafe(32)
        env = dict(os.environ)
        env.update({
            MODE_ENV: "process", ROLE_ENV: "worker", TOKEN_ENV: token, PROXY_SECRET_ENV: proxy_secret,
            "APP_HOST": "127.0.0.1", "APP_PORT": str(port), "UWAPI_PUBLIC_BIND_HOST": "127.0.0.1",
        })
        env.pop(URL_ENV, None)
        cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
               "--log-level", "warning"]
        logger.info(f"启动浏览器 worker 进程（127.0.0.1:{port}）")
        self.process = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"worker 进程启动失败（退出码 {self.process.returncode}）")
            try:
                if requests.get(url + "/internal/worker/health", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            self.stop()
            raise RuntimeError(f"worker 进程 {ready_timeout:.0f} 秒内未就绪")
        os.environ[URL_ENV] = url
        os.environ[TOKEN_ENV] = token
        os.environ[PROXY_SECRET_ENV] = proxy_secret
        logger.info(f"浏览器 worker 已就绪：{url}（pid {self.process.pid}）")
        return url

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 15.0) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        logger.info("浏览器 worker 已停止")


supervisor = WorkerSupervisor()
