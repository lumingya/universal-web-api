"""R2-6：API 进程负责启动、监控与关闭 worker。"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from app.core.config import get_logger
from app.worker import MODE_ENV, PORT_ENV, ROLE_ENV, TOKEN_ENV, TOKEN_HEADER, URL_ENV
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
        self.url: Optional[str] = None
        self._lifecycle_lock = threading.RLock()
        self._shutdown = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self, ready_timeout: float = 90.0) -> str:
        """启动 worker 并等待就绪，返回其地址；同时设置本进程的 UWAPI_WORKER_URL / TOKEN / 代理密钥。"""
        monitor = self._monitor_thread
        if monitor is None or not monitor.is_alive():
            self._shutdown.clear()

        with self._lifecycle_lock:
            if self.process is not None and self.process.poll() is None and self.url:
                return self.url

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
            process = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env)
            self.process = process
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + ready_timeout
            while time.monotonic() < deadline:
                if self._shutdown.is_set():
                    self._terminate_process(process, timeout=3)
                    raise RuntimeError("worker 启动被取消（服务正在关闭）")
                if process.poll() is not None:
                    raise RuntimeError(f"worker 进程启动失败（退出码 {process.returncode}）")
                try:
                    response = requests.get(url + "/internal/worker/health", headers={TOKEN_HEADER: token}, timeout=2)
                    if response.status_code == 200:
                        health = response.json()
                        if isinstance(health, dict) and health.get("role") == "worker":
                            break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.5)
            else:
                self._terminate_process(process, timeout=3)
                raise RuntimeError(f"worker 进程 {ready_timeout:.0f} 秒内未就绪")
            os.environ[URL_ENV] = url
            os.environ[TOKEN_ENV] = token
            os.environ[PROXY_SECRET_ENV] = proxy_secret
            self.url = url
            worker_pid = health.get("pid")
            logger.info(f"浏览器 worker 已就绪：{url}（worker pid {worker_pid}，启动器 pid {process.pid}）")
            return url

    def start_monitoring(
        self,
        check_interval: float = 1.0,
        ready_timeout: float = 90.0,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
    ) -> None:
        """监控子进程；意外退出时以指数退避重启，关闭 API 时停止监控。"""
        if check_interval <= 0 or initial_retry_delay <= 0 or max_retry_delay < initial_retry_delay:
            raise ValueError("worker 监控间隔与退避时长必须为正数，且最大退避不能小于初始值")
        with self._lifecycle_lock:
            if self.process is None:
                raise RuntimeError("worker 尚未启动，不能启动监控")
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._shutdown.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor,
                args=(check_interval, ready_timeout, initial_retry_delay, max_retry_delay),
                name="worker-supervisor",
                daemon=True,
            )
            self._monitor_thread.start()

    def _monitor(
        self,
        check_interval: float,
        ready_timeout: float,
        initial_retry_delay: float,
        max_retry_delay: float,
    ) -> None:
        retry_delay = 0.0
        while not self._shutdown.wait(check_interval):
            process = self.process
            if process is None or process.poll() is None:
                retry_delay = 0.0
                continue

            logger.error(f"浏览器 worker 意外退出（exit={process.returncode}），准备自动重启")
            while not self._shutdown.is_set():
                if retry_delay and self._shutdown.wait(retry_delay):
                    return
                try:
                    self.start(ready_timeout=ready_timeout)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                    if self._shutdown.is_set():
                        return
                    logger.error(f"浏览器 worker 自动重启失败，将退避重试：{exc}")
                    retry_delay = min(initial_retry_delay if not retry_delay else retry_delay * 2,
                                      max_retry_delay)
                else:
                    logger.info("浏览器 worker 已由监控器自动重启")
                    retry_delay = 0.0
                    break

    @staticmethod
    def _terminate_process(process: Optional[subprocess.Popen], timeout: float) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            if process.poll() is None:
                raise
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def alive(self) -> bool:
        with self._lifecycle_lock:
            return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 15.0) -> None:
        self._shutdown.set()
        monitor = self._monitor_thread
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=5)
        with self._lifecycle_lock:
            process = self.process
            self._terminate_process(process, timeout=timeout)
            if self.process is process:
                self.process = None
            self.url = None
            if monitor is not None and not monitor.is_alive():
                self._monitor_thread = None
            if process is not None:
                logger.info("浏览器 worker 已停止")


supervisor = WorkerSupervisor()
