"""
app/core/workflow_editor.py - 可视化工作流编辑器注入管理

职责：
- 读取注入脚本
- 向目标页面注入编辑器
"""

import json
import os
from pathlib import Path
from typing import Optional
from app.core.config import BrowserConstants
from app.core.config import logger


class WorkflowEditorInjector:
    """工作流编辑器注入器"""
    
    _script_cache: Optional[str] = None
    _script_mtime: float = 0

    @staticmethod
    def _build_js_assignment(var_name: str, value) -> str:
        """生成安全的 JS 变量赋值语句。"""
        return f"window.{var_name} = {json.dumps(value, ensure_ascii=False)};"
    
    @classmethod
    def _load_script(cls) -> str:
        """加载注入脚本（自动检测文件变化）"""
        script_path = Path(__file__).parent.parent.parent / "static" / "js" / "workflow-editor-inject.js"
        
        if not script_path.exists():
            raise FileNotFoundError(f"编辑器脚本不存在: {script_path}")
        
        # 检查文件是否有变化
        current_mtime = script_path.stat().st_mtime
        
        if cls._script_cache is None or current_mtime != cls._script_mtime:
            with open(script_path, 'r', encoding='utf-8') as f:
                cls._script_cache = f.read()
            cls._script_mtime = current_mtime
            logger.info(f"已加载编辑器脚本: {len(cls._script_cache)} 字符 (mtime: {current_mtime})")
        
        return cls._script_cache
    
    @classmethod
    def inject(
        cls,
        tab,
        site_config: dict = None,
        target_domain: str = None,
        preset_name: str = None
    ) -> dict:
        """
        向标签页注入编辑器
        
        Args:
            tab: DrissionPage 标签页对象
            site_config: 站点配置数据（可选）
            target_domain: 目标站点域名（用于校验，可选）
            
        Returns:
            dict: {"success": bool, "message": str}
        """
        try:
            script = cls._load_script()
            
            # 🆕 在 Python 端进行域名校验（更可靠）
            current_domain = tab.run_js("return window.location.hostname")
            
            if target_domain and target_domain != current_domain:
                logger.warning(f"域名不匹配: 期望 {target_domain}, 实际 {current_domain}")
                return {
                    "success": False,
                    "message": f"域名不匹配！配置目标: {target_domain}，当前页面: {current_domain}。请先导航到正确的网站。",
                    "domain_mismatch": True,
                    "expected_domain": target_domain,
                    "actual_domain": current_domain
                }
            
            # 检查是否已注入
            already_injected = tab.run_js("return !!window.__WORKFLOW_EDITOR_INJECTED__")
            
            if already_injected:
                # 已存在：销毁旧实例后强制重注入，避免继续运行旧脚本
                logger.info(f"编辑器已存在，执行强制重注入: domain={target_domain}")
                try:
                    tab.run_js("window.WorkflowEditor?.destroy?.();")
                except Exception as destroy_error:
                    logger.debug(f"销毁旧编辑器失败（忽略）: {destroy_error}")

                api_port = os.getenv("PORT", "9099")
                api_base = f"http://127.0.0.1:{api_port}"

                reinject_parts = [
                    cls._build_js_assignment("__WORKFLOW_EDITOR_API_BASE__", api_base),
                    cls._build_js_assignment("__WORKFLOW_EDITOR_TAB_ID__", str(getattr(tab, "tab_id", "") or ""))
                ]

                if target_domain:
                    reinject_parts.append(
                        cls._build_js_assignment("__WORKFLOW_EDITOR_TARGET_DOMAIN__", target_domain)
                    )

                if preset_name:
                    reinject_parts.append(
                        cls._build_js_assignment("__WORKFLOW_EDITOR_PRESET_NAME__", preset_name)
                    )

                if site_config:
                    reinject_parts.append(
                        cls._build_js_assignment("__WORKFLOW_EDITOR_CONFIG__", site_config)
                    )

                full_script = "\n".join(reinject_parts) + "\n\n" + script
                tab.run_js(full_script)

                return {
                    "success": True,
                    "message": "编辑器已存在，已强制更新到最新版本",
                    "already_existed": True,
                    "config_updated": True,
                    "reinject": True
                }
            
            # 🆕 构建完整注入脚本（变量 + 编辑器代码）
            api_port = os.getenv("PORT", "9099")
            api_base = f"http://127.0.0.1:{api_port}"
            
            injection_parts = []
            
            # 1. 注入 API 地址
            injection_parts.append(
                cls._build_js_assignment("__WORKFLOW_EDITOR_API_BASE__", api_base)
            )
            injection_parts.append(
                cls._build_js_assignment("__WORKFLOW_EDITOR_TAB_ID__", str(getattr(tab, "tab_id", "") or ""))
            )
            
            # 2. 注入目标域名
            if target_domain:
                injection_parts.append(
                    cls._build_js_assignment("__WORKFLOW_EDITOR_TARGET_DOMAIN__", target_domain)
                )

            if preset_name:
                injection_parts.append(
                    cls._build_js_assignment("__WORKFLOW_EDITOR_PRESET_NAME__", preset_name)
                )
            
            # 3. 注入站点配置
            if site_config:
                injection_parts.append(
                    cls._build_js_assignment("__WORKFLOW_EDITOR_CONFIG__", site_config)
                )
            
            # 4. 拼接完整脚本（变量声明 + 编辑器代码）
            full_script = "\n".join(injection_parts) + "\n\n" + script
            
            # 5. 一次性注入
            tab.run_js(full_script)
            
            logger.info(f"编辑器已注入到: {tab.url[:50]}... (domain: {target_domain or 'unknown'})")
            
            return {
                "success": True,
                "message": "编辑器注入成功",
                "already_existed": False
            }
            
        except FileNotFoundError as e:
            logger.error(f"编辑器脚本加载失败: {e}")
            return {"success": False, "message": str(e)}
            
        except Exception as e:
            logger.error(f"编辑器注入失败: {e}")
            return {"success": False, "message": f"注入失败: {str(e)}"}    
    @classmethod
    def clear_cache(cls):
        """清除脚本缓存（开发调试用）"""
        cls._script_cache = None
        logger.debug("编辑器脚本缓存已清除")


# 单例导出
workflow_editor_injector = WorkflowEditorInjector()
