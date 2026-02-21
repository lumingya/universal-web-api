"""
app/utils/human_mouse.py - 人类化鼠标行为模拟

职责：
- 平滑鼠标移动（多中间点 + 噪声 + 弧度）
- 空闲微漂移（等待期间的无意识手部抖动）
- 自然化滚轮滚动（多步小增量）
- 精确 CDP 点击（含 pressure 修复 + 按压微移）

所有函数通过 CDP Input.dispatchMouseEvent 直接操作，
不注入任何 JS，不触发页面脚本。

v5.10 改进：
- 移除 _dispatch_mouse_move 中的 tab.actions 降级（触发 CF）
- cdp_precise_click 按压期间增加微移事件（消灭事件沙漠）
- human_scroll 改用 CDP Input.dispatchMouseEvent(mouseWheel)
- idle_drift 频率更不规则（偶尔静止、偶尔连续微动）
"""

import time
import math
import random
from typing import Tuple, Optional, Callable

from app.core.config import logger


# ================= CDP 鼠标事件派发 =================

def _dispatch_mouse_move(tab, x: int, y: int, buttons: int = 0) -> bool:
    """
    通过 CDP 派发 mouseMoved 事件（绝对视口坐标）
    
    不注入 JS，不触发页面脚本的 Runtime.evaluate。
    不降级到 tab.actions（会触发 CF 检测）。
    
    Args:
        tab: DrissionPage 标签页
        x, y: 视口坐标
        buttons: 按钮位掩码（0=无按钮，1=左键按住）
    
    Returns:
        是否成功
    """
    try:
        tab.run_cdp(
            'Input.dispatchMouseEvent',
            type='mouseMoved',
            x=int(x),
            y=int(y),
            button='none',
            buttons=buttons,
            modifiers=0,
            pointerType='mouse'
        )
        return True
    except Exception as e:
        # 🔴 不降级到 tab.actions.move_to()（测试7证明 actions 系列触发 CF）
        logger.warning(f"[MOUSE_CDP] mouseMoved 派发失败: {e}")
        return False


# ================= 平滑鼠标移动 =================

def smooth_move_mouse(
    tab,
    from_pos: Tuple[int, int],
    to_pos: Tuple[int, int],
    duration: float = None,
    noise_scale: float = None,
    check_cancelled: Callable[[], bool] = None
) -> Tuple[int, int]:
    """
    人类化平滑鼠标移动
    
    使用二次贝塞尔插值 + 高斯噪声 + 正弦缓动。
    
    Args:
        tab: DrissionPage 标签页
        from_pos: 起始坐标 (x, y)
        to_pos: 目标坐标 (x, y)
        duration: 移动总时长（秒），None 则自动计算
        noise_scale: 噪声幅度（像素），None 则按距离自动
        check_cancelled: 取消检查函数
    
    Returns:
        最终鼠标坐标 (x, y)
    """
    x0, y0 = from_pos
    x1, y1 = to_pos
    
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)
    
    # 距离太短，直接移动
    if dist < 15:
        _dispatch_mouse_move(tab, x1, y1)
        return (x1, y1)
    
    # 自动计算移动时长（Fitts's Law 启发）
    if duration is None:
        duration = 0.15 + 0.12 * math.log2(1 + dist / 50)
        duration *= random.uniform(0.85, 1.15)
        duration = max(0.12, min(duration, 0.8))
    
    # 步数：模拟 55-75Hz 采样率
    sample_rate = random.uniform(55, 75)
    steps = max(8, int(duration * sample_rate))
    step_interval = duration / steps
    
    # 噪声幅度：与距离成正比，上限 12px
    if noise_scale is None:
        noise_scale = min(dist * 0.03, 12.0)
    
    # 贝塞尔控制点：在路径中垂线方向偏移，产生弧度
    perp_x = -dy / dist if dist > 0 else 0
    perp_y = dx / dist if dist > 0 else 0
    arc_offset = random.gauss(0, dist * 0.06)
    ctrl_x = (x0 + x1) / 2 + perp_x * arc_offset
    ctrl_y = (y0 + y1) / 2 + perp_y * arc_offset
    
    # 逐步移动
    for i in range(1, steps + 1):
        if check_cancelled and check_cancelled():
            return (x0, y0)
        
        # 正弦缓动 (ease-in-out)
        raw_t = i / steps
        t = 0.5 - 0.5 * math.cos(raw_t * math.pi)
        
        # 二次贝塞尔插值
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * ctrl_x + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * ctrl_y + t ** 2 * y1
        
        # 高斯噪声（中段最大，首尾趋零）
        envelope = math.sin(raw_t * math.pi)
        nx = random.gauss(0, noise_scale * envelope)
        ny = random.gauss(0, noise_scale * envelope)
        
        fx = int(bx + nx)
        fy = int(by + ny)
        
        _dispatch_mouse_move(tab, fx, fy)
        
        # 步间延迟（带微随机）
        time.sleep(step_interval * random.uniform(0.7, 1.3))
    
    # 最终精确到达目标
    _dispatch_mouse_move(tab, x1, y1)
    
    # 20% 概率轻微过冲（距离 > 100px 时）
    if dist > 100 and random.random() < 0.2:
        angle = math.atan2(dy, dx)
        overshoot_dist = random.uniform(3, 8)
        ox = int(x1 + math.cos(angle) * overshoot_dist)
        oy = int(y1 + math.sin(angle) * overshoot_dist)
        _dispatch_mouse_move(tab, ox, oy)
        time.sleep(random.uniform(0.04, 0.10))
        _dispatch_mouse_move(tab, x1, y1)
    
    return (x1, y1)


# ================= 空闲微漂移 =================

def idle_drift(
    tab,
    duration: float,
    center_pos: Tuple[int, int],
    check_cancelled: Callable[[], bool] = None,
    drift_radius: float = 3.0,
    freq_hz: float = 1.5
) -> Tuple[int, int]:
    """
    空闲时的无意识手部微抖动
    
    模拟人类在"等待/阅读/思考"时鼠标的微小漂移。
    使用偏向中心的布朗运动，不会越漂越远。
    
    v5.10：频率更不规则，偶尔静止较长时间，偶尔连续快速微动。
    
    Args:
        tab: DrissionPage 标签页
        duration: 漂移持续时间（秒）
        center_pos: 中心坐标
        check_cancelled: 取消检查函数
        drift_radius: 最大漂移半径（像素）
        freq_hz: 基准移动频率（Hz），实际频率会大幅波动
    
    Returns:
        最终鼠标坐标
    """
    cx, cy = center_pos
    cur_x, cur_y = float(cx), float(cy)
    
    base_interval = 1.0 / freq_hz
    elapsed = 0.0
    
    while elapsed < duration:
        if check_cancelled and check_cancelled():
            break
        
        # 不规则间隔：30% 概率短间隔（连续微动），70% 概率长间隔（静止）
        if random.random() < 0.3:
            # 连续微动模式：快速连续 2-4 次小移动
            burst_count = random.randint(2, 4)
            for _ in range(burst_count):
                if elapsed >= duration:
                    break
                if check_cancelled and check_cancelled():
                    break
                
                burst_sleep = random.uniform(0.08, 0.25)
                burst_sleep = min(burst_sleep, duration - elapsed)
                if burst_sleep <= 0:
                    break
                
                time.sleep(burst_sleep)
                elapsed += burst_sleep
                
                # 微小移动（0.3-1.5px）
                angle = random.uniform(0, 2 * math.pi)
                step = random.uniform(0.3, 1.5)
                cur_x += math.cos(angle) * step
                cur_y += math.sin(angle) * step
                
                # 回弹力
                dist_from_center = math.hypot(cur_x - cx, cur_y - cy)
                if dist_from_center > drift_radius * 0.6:
                    pull = 0.3
                    cur_x += (cx - cur_x) * pull
                    cur_y += (cy - cur_y) * pull
                
                _dispatch_mouse_move(tab, int(cur_x), int(cur_y))
        else:
            # 静止模式：较长时间不动
            sleep_time = base_interval * random.uniform(0.8, 2.5)
            sleep_time = min(sleep_time, duration - elapsed)
            
            if sleep_time <= 0:
                break
            
            time.sleep(sleep_time)
            elapsed += sleep_time
            
            # 单次微移（0.5-2px）
            angle = random.uniform(0, 2 * math.pi)
            step = random.uniform(0.5, 2.0)
            cur_x += math.cos(angle) * step
            cur_y += math.sin(angle) * step
            
            # 回弹力
            dist_from_center = math.hypot(cur_x - cx, cur_y - cy)
            if dist_from_center > drift_radius * 0.6:
                pull = 0.3
                cur_x += (cx - cur_x) * pull
                cur_y += (cy - cur_y) * pull
            
            _dispatch_mouse_move(tab, int(cur_x), int(cur_y))
    
    return (int(cur_x), int(cur_y))


# ================= 自然化滚轮滚动 =================

def human_scroll(
    tab,
    total_dy: int,
    mouse_x: int = None,
    mouse_y: int = None,
    check_cancelled: Callable[[], bool] = None
):
    """
    模拟人类滚轮滚动（多步小增量，CDP 直接派发）
    
    v5.10：改用 CDP Input.dispatchMouseEvent(mouseWheel) 替代 tab.actions.scroll()，
    避免 actions 系列的参数组装差异。
    
    真实鼠标滚轮每格约 100-120px（标准 deltaY=100），间隔 20-80ms。
    
    Args:
        tab: DrissionPage 标签页
        total_dy: 总滚动量（像素，正=向下，负=向上）
        mouse_x: 鼠标 X 坐标（滚动时鼠标位置），None 则默认视口中心
        mouse_y: 鼠标 Y 坐标，None 则默认视口中心
        check_cancelled: 取消检查函数
    """
    if total_dy == 0:
        return
    
    # 默认鼠标在视口中心
    if mouse_x is None:
        mouse_x = 600
    if mouse_y is None:
        mouse_y = 400
    
    direction = 1 if total_dy > 0 else -1
    remaining = abs(total_dy)
    
    while remaining > 0:
        if check_cancelled and check_cancelled():
            return
        
        # 每步 80-120px（匹配标准鼠标滚轮一格）
        step = min(remaining, random.randint(80, 120))
        
        try:
            tab.run_cdp(
                'Input.dispatchMouseEvent',
                type='mouseWheel',
                x=int(mouse_x),
                y=int(mouse_y),
                deltaX=0,
                deltaY=step * direction,
                button='none',
                buttons=0,
                pointerType='mouse'
            )
        except Exception as e:
            logger.debug(f"[SCROLL_CDP] mouseWheel 派发失败: {e}")
            # 降级到 actions（滚动触发 CF 的风险低于点击）
            try:
                tab.actions.scroll(0, step * direction)
            except Exception:
                break
        
        remaining -= step
        
        if remaining > 0:
            time.sleep(random.uniform(0.02, 0.08))


# ================= 精确 CDP 点击 =================

def cdp_precise_click(
    tab,
    x: int,
    y: int,
    hold_duration: float = None,
    check_cancelled: Callable[[], bool] = None
) -> bool:
    """
    通过 CDP 派发完整的鼠标点击事件序列
    
    v5.10 改进：
    - mousePressed 设置 force=0.5（PointerEvent.pressure=0.5，匹配真实鼠标）
    - mouseReleased 设置 force=0（释放时 pressure 归零）
    - 按压期间插入 1-2 个 mouseMoved(buttons=1) 微移事件（消灭事件沙漠）
    - 点击前派发一次 mouseMoved 到精确坐标（匹配真实硬件行为）
    
    完整事件序列（匹配真实硬件）：
    1. mouseMoved(buttons=0)         — 鼠标到达位置
    2. mousePressed(force=0.5)       — 按下
    3. mouseMoved(buttons=1) × 1-2   — 按压期间手指微移
    4. mouseReleased(force=0)        — 释放
    
    Args:
        tab: DrissionPage 标签页
        x, y: 视口坐标
        hold_duration: 按压时长（秒），None 则随机 60-140ms
        check_cancelled: 取消检查函数
    
    Returns:
        是否成功
    """
    if check_cancelled and check_cancelled():
        return False
    
    x, y = int(x), int(y)
    
    if hold_duration is None:
        hold_duration = random.uniform(0.06, 0.14)
    
    try:
        # 1. 点击前确认鼠标位置（真实硬件在按下前一帧必有 mouseMoved）
        _dispatch_mouse_move(tab, x, y, buttons=0)
        time.sleep(random.uniform(0.008, 0.025))  # 1 帧间隔
        
        if check_cancelled and check_cancelled():
            return False
        
        # 2. mousePressed（force=0.5 → pressure=0.5）
        tab.run_cdp(
            'Input.dispatchMouseEvent',
            type='mousePressed',
            x=x,
            y=y,
            button='left',
            buttons=1,
            clickCount=1,
            force=0.5,
            pointerType='mouse'
        )
        
        # 3. 按压期间微移（模拟手指按压导致鼠标轻微位移）
        #    真实硬件在 pressed→released 之间通常有 1-3 个 mouseMoved(buttons=1)
        micro_move_count = random.randint(1, 2)
        micro_interval = hold_duration / (micro_move_count + 1)
        
        press_x, press_y = x, y
        for _ in range(micro_move_count):
            if check_cancelled and check_cancelled():
                # 取消时仍需释放按钮
                _release_mouse(tab, press_x, press_y)
                return False
            
            time.sleep(micro_interval * random.uniform(0.7, 1.3))
            
            # 微小位移：1-2px（手指按压力导致）
            press_x = x + random.randint(-2, 2)
            press_y = y + random.randint(-1, 1)
            _dispatch_mouse_move(tab, press_x, press_y, buttons=1)
        
        # 剩余按压时间
        remaining_hold = hold_duration - micro_interval * micro_move_count
        if remaining_hold > 0:
            time.sleep(max(0.01, remaining_hold * random.uniform(0.6, 1.0)))
        
        if check_cancelled and check_cancelled():
            _release_mouse(tab, press_x, press_y)
            return False
        
        # 4. mouseReleased（在最终微移位置释放，而非精确回到原点）
        #    释放坐标允许和按下坐标有 1-2px 偏差（真实行为）
        release_x = x + random.randint(-1, 1)
        release_y = y + random.randint(-1, 1)
        
        tab.run_cdp(
            'Input.dispatchMouseEvent',
            type='mouseReleased',
            x=release_x,
            y=release_y,
            button='left',
            buttons=0,
            clickCount=1,
            force=0,
            pointerType='mouse'
        )
        
        return True
    
    except Exception as e:
        logger.warning(f"[CDP_CLICK] 精确点击失败: {e}")
        return False


def _release_mouse(tab, x: int, y: int):
    """安全释放鼠标按钮（防止状态泄漏）"""
    try:
        tab.run_cdp(
            'Input.dispatchMouseEvent',
            type='mouseReleased',
            x=int(x),
            y=int(y),
            button='left',
            buttons=0,
            clickCount=1,
            force=0,
            pointerType='mouse'
        )
    except Exception:
        pass


__all__ = ['smooth_move_mouse', 'idle_drift', 'human_scroll', 'cdp_precise_click']