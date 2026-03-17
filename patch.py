import os

filepath = 'app/services/command_engine.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Try to find the insertion point
target = '''        elif action_type in {"execute_preset", "switch_preset"}:
            return self._execute_preset_action(action, session)'''

insertion = '''

        elif action_type == "click_element":
            selector = action.get("selector", "")
            if selector:
                try:
                    ele = tab.ele(selector, timeout=3)
                    if ele:
                        ele.click()
                        import time
                        time.sleep(1)
                        logger.debug(f"[CMD] 元素已点击: {selector}")
                        return f"element_clicked:{selector}"
                    else:
                        logger.warning(f"[CMD] 待点击的元素未找到: {selector}")
                        return f"element_not_found:{selector}"
                except Exception as e:
                    logger.warning(f"[CMD] 点击元素失败: {e}")
                    return f"click_element_failed:{e}"
            return "click_element_skipped_no_selector"

        elif action_type == "click_coordinates":
            try:
                x = int(action.get("x", 0))
                y = int(action.get("y", 0))
                from app.utils.human_mouse import cdp_precise_click
                # cdp_precise_click handles its own debug logging and execution securely via CDP
                success = cdp_precise_click(tab, x, y)
                if success:
                    import time
                    time.sleep(0.5)
                    logger.debug(f"[CMD] 坐标已点击: ({x}, {y})")
                    return f"coordinates_clicked:({x},{y})"
                else:
                    logger.warning(f"[CMD] 坐标点击失败: ({x}, {y})")
                    return f"coordinates_click_failed:({x},{y})"
            except Exception as e:
                logger.warning(f"[CMD] 坐标点击失败，参数异常: {e}")
                return f"click_coordinates_failed:{e}"'''

if target in content:
    new_content = content.replace(target, target + insertion)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Patch applied successfully.")
else:
    print("Target string not found in file.")
