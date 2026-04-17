import math
import os
import random
import string
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import urlsplit

from app.core.workflow.text_input import TextInputHandler
from app.utils.human_mouse import cdp_precise_click, idle_drift, smooth_move_mouse, human_scroll


class AutomationCancelledError(RuntimeError):
    pass


HOTMAIL_SIGNUP_URL = "https://signup.live.com/signup?"
HOTMAIL_LOG_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "logs", "hotmail_accounts.txt")
)
MICROSOFT_ORIGINS = [
    "https://signup.live.com",
    "https://login.live.com",
    "https://account.microsoft.com",
    "https://outlook.live.com",
    "https://live.com",
]
MICROSOFT_COOKIE_HOST_KEYWORDS = [
    "live.com",
    "microsoft.com",
    "hotmail.com",
    "outlook.com",
    "hsprotect.net",
]
# 登录态相关 Cookie 前缀 — 仅清除这些，保留追踪类 Cookie 模拟真实用户
_SESSION_COOKIE_NAMES = {
    "MSFT", "ANON", "WLSSC", "MSPAuth", "MSPProf", "MSPSoftVis",
    "RPSSecAuth", "OID", "SRCHHPGUSR", "MUID", "MSNRPSAuth",
    "MSPRequ", "NAP", "PPAuth", "stsservicecookie",
}
# 入口页 URL 池，用于自然导航产生 Referer
_ENTRY_URLS = [
    "https://www.microsoft.com/zh-cn/",
    "https://outlook.live.com/",
    "https://www.hotmail.com/",
]
# 多样化邮箱前缀池
_EMAIL_PREFIXES = [
    "alex", "bella", "chris", "diana", "eric", "fiona",
    "grace", "henry", "iris", "jack", "kelly", "leo",
    "mia", "neil", "olive", "peter", "quinn", "rose",
    "sam", "tina", "victor", "wendy", "yuki", "zoe",
    "mingyu", "xiaoli", "haoran", "yichen", "ziwei",
    "jianhua", "meiling", "wenjie", "xuanyi", "zhihao",
    "sunny", "happy", "lucky", "cloud", "star",
    "river", "sky", "snow", "rain", "wind",
]
_EMAIL_SEPARATORS = ["", ".", "_", ""]
_EMAIL_LOCALPART_MIN_LEN = 14
_EMAIL_LOCALPART_FULL_RANDOM_MIN_LEN = 14
_EMAIL_LOCALPART_FULL_RANDOM_MAX_LEN = 20

CHALLENGE_BUTTON_TEXTS = [
    "可访问性挑战",
    "Accessibility challenge",
]
PRESS_AGAIN_TEXTS = [
    "再次按下",
    "Press again",
]
BLOCKED_TEXTS = [
    "帐户创建已被阻止",
    "账户创建已被阻止",
    "Account creation has been blocked",
]
PASSKEY_BUTTON_TEXTS = [
    "确认创建密钥",
    "创建密钥",
    "创建你的密钥",
    "创建通行密钥",
    "创建密钥对",
    "Create passkey",
    "Create key",
]
NEXT_BUTTON_TEXTS = [
    "下一步",
    "Next",
]
CONSENT_BUTTON_TEXTS = [
    "同意并继续",
    "Agree and continue",
]
PASSKEY_INFO_TEXTS = [
    "有关 Microsoft 帐户的快速说明",
    "快速说明",
]
PASSKEY_INFO_CONFIRM_TEXTS = [
    "确定",
    "继续",
    "Continue",
]
GOOGLE_PASSWORD_MANAGER_TEXTS = [
    "Google 密码管理工具",
    "Google Password Manager",
]
PASSKEY_LOCATION_TEXTS = [
    "选择保存",
    "通行密钥的位置",
    "save login.microsoft.com passkey",
]
PASSKEY_CREATE_TEXTS = [
    "创建",
    "Create",
]
PIN_PROMPT_TEXTS = [
    "输入您的 PIN 码",
    "PIN 码",
    "verify your identity",
]
HOTMAIL_PASSKEY_PIN_ENV_KEYS = (
    "HOTMAIL_PASSKEY_PIN",
    "WINDOWS_HELLO_PIN",
)
HOTMAIL_GOOGLE_PASSWORD_MANAGER_RATIO_POINTS = [
    (0.46, 0.49),
    (0.50, 0.49),
]
HOTMAIL_PASSKEY_CREATE_RATIO_POINTS = [
    (0.67, 0.67),
    (0.62, 0.67),
]
HOTMAIL_PASSKEY_PIN_BOX_RATIO_POINTS = [
    (0.24, 0.31),
    (0.28, 0.31),
]
HOTMAIL_CHALLENGE_IFRAME_SELECTOR = "xpath://iframe[contains(@src, 'iframe.hsprotect.net')]"
HOTMAIL_CHALLENGE_VIEWPORT_POINTS = [
    (516, 502, 10),
]
HOTMAIL_CHALLENGE_PRESS_AGAIN_VIEWPORT_POINTS = [
    (622, 507, 10),
]
HOTMAIL_CHALLENGE_IFRAME_RELATIVE_POINTS = [
    (0.50, 0.23),
    (0.50, 0.34),
    (0.50, 0.46),
    (0.50, 0.58),
    (0.38, 0.46),
    (0.62, 0.46),
    (0.50, 0.70),
]
HOTMAIL_CHALLENGE_VIEWPORT_RATIO_POINTS = [
    (0.50, 0.56),
    (0.50, 0.63),
    (0.50, 0.70),
    (0.42, 0.63),
    (0.58, 0.63),
]
HOTMAIL_CHALLENGE_INITIAL_CLICK_ATTEMPTS = 3
HOTMAIL_CHALLENGE_PRESS_AGAIN_ATTEMPTS = 4
HOTMAIL_CHALLENGE_WAIT_AFTER_CLICK_SEC = 5.0

LAST_NAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝安常乐于时傅皮卞齐康伍余元顾孟平黄和穆萧尹")
FIRST_NAME_CHARS = list("子文宇晨若思雨欣安泽俊浩嘉依清语然一可宁涵诗雅博昊轩瑶琪妍婷悦萌锦瑞宸铭霖舒彤佳林远晴川宁知南星岚婧凡熙程涵洛言楚歆沐芷")

HOTMAIL_PAGE_CHECK_SNAPSHOT_JS = r"""
return (function() {
    var parts = [];
    try { parts.push(document.title || ''); } catch(e) {}
    try { parts.push((document.body ? document.body.innerText || '' : '').slice(0, 3000)); } catch(e) {}
    try {
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
            try { parts.push(iframes[i].src || ''); } catch(e) {}
            try { parts.push(iframes[i].getAttribute('title') || ''); } catch(e) {}
        }
    } catch(e) {}
    try {
        var btns = document.querySelectorAll('button, [role="button"]');
        for (var j = 0; j < btns.length; j++) {
            try { parts.push(btns[j].innerText || ''); } catch(e) {}
            try { parts.push(btns[j].getAttribute('aria-label') || ''); } catch(e) {}
        }
    } catch(e) {}
    return parts.join('\n').toLowerCase();
})();
"""


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _append_line(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(str(text))
        f.write("\n")


def _random_chars(length: int, alphabet: str) -> str:
    return "".join(random.choice(alphabet) for _ in range(max(1, int(length))))


def _generate_localpart() -> str:
    """多样化邮箱本地名生成，避免固定前缀被统计检测，确保足够长不易被占用"""
    mode = random.choice(["prefix_num", "prefix_chars", "full_random", "name_year", "compound"])
    if mode == "prefix_num":
        # 前缀 + 分隔符 + 数字串（总长 14-20）
        prefix = random.choice(_EMAIL_PREFIXES)
        sep = random.choice(_EMAIL_SEPARATORS)
        num_len = random.randint(8, 12)
        result = prefix + sep + _random_chars(num_len, string.digits)
    elif mode == "prefix_chars":
        # 前缀 + 分隔符 + 字母数字混合（总长 14-22）
        prefix = random.choice(_EMAIL_PREFIXES)
        sep = random.choice(_EMAIL_SEPARATORS)
        tail_len = random.randint(9, 13)
        result = prefix + sep + _random_chars(tail_len, string.ascii_lowercase + string.digits)
    elif mode == "name_year":
        # 前缀 + 年份后两位 + 额外随机字符（总长 14-20）
        prefix = random.choice(_EMAIL_PREFIXES)
        year_suffix = str(random.randint(85, 99)) if random.random() < 0.5 else str(random.randint(0, 25)).zfill(2)
        sep = random.choice(_EMAIL_SEPARATORS)
        extra_len = random.randint(7, 10)
        extra = _random_chars(extra_len, string.ascii_lowercase + string.digits)
        result = prefix + sep + year_suffix + extra
    elif mode == "compound":
        # 双前缀组合（totalname_style，总长 14-22）
        p1 = random.choice(_EMAIL_PREFIXES)
        p2 = random.choice(_EMAIL_PREFIXES)
        while p2 == p1:
            p2 = random.choice(_EMAIL_PREFIXES)
        sep = random.choice(_EMAIL_SEPARATORS)
        tail = _random_chars(random.randint(5, 8), string.digits)
        result = p1 + sep + p2 + tail
    else:
        # 完全随机（总长 14-20）
        length = random.randint(_EMAIL_LOCALPART_FULL_RANDOM_MIN_LEN, _EMAIL_LOCALPART_FULL_RANDOM_MAX_LEN)
        result = _random_chars(length, string.ascii_lowercase + string.digits)
    # 最终长度保底：如果不足最小长度则补齐随机尾巴
    while len(result) < _EMAIL_LOCALPART_MIN_LEN:
        result += random.choice(string.ascii_lowercase + string.digits)
    # Hotmail 要求邮箱名必须以字母开头
    if result and not result[0].isalpha():
        result = random.choice(string.ascii_lowercase) + result[1:]
    return result


def _generate_password() -> str:
    """随机化密码结构，保证满足 Hotmail 密码复杂度要求"""
    length = random.randint(12, 18)
    specials = "!@#$%&*?"
    # 先保证各类别至少一个
    chars = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice(specials),
    ]
    # 填充剩余长度
    fill_pool = string.ascii_letters + string.digits + specials
    for _ in range(length - len(chars)):
        chars.append(random.choice(fill_pool))
    # 打乱顺序，但确保首字符为字母（某些系统不接受特殊字符开头）
    random.shuffle(chars)
    if not chars[0].isalpha():
        for i in range(1, len(chars)):
            if chars[i].isalpha():
                chars[0], chars[i] = chars[i], chars[0]
                break
    return "".join(chars)


def _generate_name() -> Dict[str, str]:
    surname = random.choice(LAST_NAMES)
    given_len = 1 if random.random() < 0.35 else 2
    given = "".join(random.choice(FIRST_NAME_CHARS) for _ in range(given_len))
    return {
        "last_name": surname,
        "first_name": given,
        "full_name": surname + given,
    }


def _generate_birthday() -> Dict[str, str]:
    """随机生日生成，避免所有账号使用相同生日"""
    year = str(random.randint(1985, 2004))
    month = random.randint(1, 12)
    day = random.randint(1, 28)  # 28 日避免月份天数溢出
    return {
        "year": year,
        "month_text": f"{month}月",
        "day_text": f"{day}日",
    }


def _sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def _resolve_hotmail_passkey_pin() -> Dict[str, Any]:
    for key in HOTMAIL_PASSKEY_PIN_ENV_KEYS:
        value = str(os.getenv(key, "") or "").strip()
        if not value:
            continue
        if not value.isdigit():
            return {
                "ok": False,
                "status": "pin_invalid_format",
                "pin": "",
                "env_key": key,
                "raw_value": value,
            }
        return {
            "ok": True,
            "status": "ok",
            "pin": value,
            "env_key": key,
        }
    return {
        "ok": False,
        "status": "pin_not_configured",
        "pin": "",
        "env_key": "",
    }


def _human_delay(base: float = 1.5, spread: float = 0.5) -> None:
    """对数正态分布延时，模拟真人反应时间的长尾特征"""
    delay = random.lognormvariate(math.log(max(0.1, base)), max(0.1, spread))
    delay = max(0.3, min(delay, base * 4))
    # 5% 概率走神/犹豫，额外停顿 1-4 秒
    if random.random() < 0.05:
        delay += random.uniform(1.0, 4.0)
    time.sleep(delay)


def _human_micro_delay(base: float = 0.08, spread: float = 0.35) -> None:
    """微操作间隔，对数正态分布，用于击键/点击后的小间隔"""
    delay = random.lognormvariate(math.log(max(0.02, base)), max(0.1, spread))
    delay = max(0.02, min(delay, 0.6))
    time.sleep(delay)


def _cdp_type_char(tab: Any, char: str) -> bool:
    """通过 CDP 输入单个可打印字符（非数字），模拟真实击键"""
    try:
        tab.run_cdp(
            "Input.dispatchKeyEvent",
            type="keyDown",
            key=char,
            text=char,
            unmodifiedText=char,
        )
        tab.run_cdp(
            "Input.dispatchKeyEvent",
            type="keyUp",
            key=char,
        )
        return True
    except Exception:
        return False


def _human_type_text(tab: Any, text: str, typo_rate: float = 0.03) -> bool:
    """逐字符击键输入，含击键间隔变化和偶尔 typo + 退格"""
    if not text:
        return True
    for i, char in enumerate(text):
        # 偶尔打错再退格（仅对字母触发）
        if random.random() < typo_rate and char.isalpha():
            wrong_pool = string.ascii_lowercase.replace(char.lower(), "")
            wrong = random.choice(wrong_pool) if wrong_pool else "x"
            _cdp_type_char(tab, wrong)
            _human_micro_delay(0.12, 0.3)
            _cdp_press_key(tab, "Backspace", "Backspace", 8)
            _human_micro_delay(0.08, 0.3)
        # 数字走 _cdp_type_digits 的逻辑
        if char.isdigit():
            keycode_map = {"0": 48, "1": 49, "2": 50, "3": 51, "4": 52,
                           "5": 53, "6": 54, "7": 55, "8": 56, "9": 57}
            code = keycode_map.get(char)
            if code is not None:
                try:
                    tab.run_cdp("Input.dispatchKeyEvent", type="rawKeyDown",
                                windowsVirtualKeyCode=code, nativeVirtualKeyCode=code,
                                code=f"Digit{char}", key=char, text=char, unmodifiedText=char)
                    tab.run_cdp("Input.dispatchKeyEvent", type="keyUp",
                                windowsVirtualKeyCode=code, nativeVirtualKeyCode=code,
                                code=f"Digit{char}", key=char)
                except Exception:
                    return False
        else:
            if not _cdp_type_char(tab, char):
                return False
        # 击键间隔：对数正态分布，中位数约 70ms
        delay = random.lognormvariate(math.log(0.07), 0.4)
        delay = max(0.02, min(delay, 0.45))
        # 偶尔更长的停顿（模拟思考/看键盘）
        if random.random() < 0.08:
            delay += random.uniform(0.15, 0.6)
        time.sleep(delay)
    return True


def _maybe_human_scroll(tab: Any) -> None:
    """30% 概率执行一次随机页面滚动，模拟真人浏览行为"""
    if random.random() < 0.3:
        scroll_amount = random.choice([-180, -120, -80, 80, 120, 180, 250])
        try:
            human_scroll(tab, scroll_amount)
        except Exception:
            pass
        _human_micro_delay(0.4, 0.3)


def _raise_if_cancelled(check_cancelled=None) -> None:
    try:
        if check_cancelled and check_cancelled():
            raise AutomationCancelledError("cancelled")
    except AutomationCancelledError:
        raise
    except Exception:
        return


def _safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _read_body_text(page: Any) -> str:
    try:
        value = page.run_js(
            "return document.body ? String(document.body.innerText || document.body.textContent || '') : '';"
        )
        return str(value or "")
    except Exception:
        return ""


def _hotmail_get_page_check_snapshot(tab: Any) -> str:
    try:
        return str(tab.run_js(HOTMAIL_PAGE_CHECK_SNAPSHOT_JS) or "")
    except Exception:
        return _read_body_text(tab).lower()


def _button_label(ele: Any) -> str:
    for getter in (
        lambda x: _safe_attr(x, "text", ""),
        lambda x: x.attr("value"),
        lambda x: x.attr("aria-label"),
        lambda x: x.attr("title"),
    ):
        try:
            value = getter(ele)
        except Exception:
            value = ""
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _current_url(tab: Any) -> str:
    return str(_safe_attr(tab, "url", "") or "").strip()


def _is_account_landing_redirect(tab: Any) -> bool:
    url = _current_url(tab).lower()
    title = ""
    try:
        title = str(tab.run_js("return document.title || '';") or "").lower()
    except Exception:
        title = ""
    body = _read_body_text(tab).lower()
    return (
        "account.microsoft.com" in url
        or ("account.live.com" in url and "landing" in url)
        or ("账户中心" in body)
        or ("account.microsoft" in title)
    )


def _clear_origin_storage(tab: Any, origin: str) -> None:
    try:
        tab.run_cdp("Storage.clearDataForOrigin", origin=origin, storageTypes="all")
    except Exception:
        pass


def _clear_microsoft_session(tab: Any, logger: Any) -> None:
    """清除所有微软相关 Cookie 和存储"""
    for origin in MICROSOFT_ORIGINS:
        _clear_origin_storage(tab, origin)

    cookie_items: List[Dict[str, Any]] = []
    for urls in (
        MICROSOFT_ORIGINS,
        [_current_url(tab)] if _current_url(tab) else [],
        [],
    ):
        kwargs = {"urls": urls} if urls else {}
        try:
            result = tab.run_cdp("Network.getCookies", **kwargs) or {}
            cookies = result.get("cookies") or []
            if cookies:
                cookie_items.extend(cookies)
        except Exception:
            continue

    seen: Set[tuple] = set()
    deleted = 0
    for cookie in cookie_items:
        name = str(cookie.get("name", "") or "").strip()
        domain = str(cookie.get("domain", "") or "").strip()
        path = str(cookie.get("path", "/") or "/").strip() or "/"
        if not name:
            continue
        normalized_domain = domain.lstrip(".").lower()
        if normalized_domain and not any(key in normalized_domain for key in MICROSOFT_COOKIE_HOST_KEYWORDS):
            continue
        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)
        try:
            delete_kwargs = {"name": name, "path": path}
            if domain:
                delete_kwargs["domain"] = domain
            tab.run_cdp("Network.deleteCookies", **delete_kwargs)
            deleted += 1
        except Exception:
            continue

    try:
        tab.run_js(
            """
            try { localStorage.clear(); } catch (e) {}
            try { sessionStorage.clear(); } catch (e) {}
            """
        )
    except Exception:
        pass

    logger.info(f"[HotmailWorkflow] 已清理微软相关登录态，删除 Cookie {deleted} 个")


def _get_element_viewport_pos(ele: Any) -> Optional[tuple]:
    try:
        rect = ele.rect
        for attr in ("viewport_midpoint", "viewport_click_point", "midpoint", "click_point"):
            pos = getattr(rect, attr, None)
            if pos and len(pos) >= 2:
                return (int(pos[0]), int(pos[1]))
        location = getattr(rect, "location", None)
        size = getattr(rect, "size", None)
        if location and size and len(location) >= 2 and len(size) >= 2:
            return (int(location[0] + size[0] / 2), int(location[1] + size[1] / 2))
    except Exception:
        pass
    return None


def _get_tab_viewport_size(tab: Any) -> tuple:
    try:
        rect = tab.rect
        for attr in ("viewport_size", "size"):
            size = getattr(rect, attr, None)
            if size and len(size) >= 2 and size[0] > 100:
                return (int(size[0]), int(size[1]))
    except Exception:
        pass
    return (1200, 800)


def _ensure_mouse_origin(tab: Any, mouse_pos: Optional[tuple]) -> tuple:
    if mouse_pos is not None:
        return mouse_pos
    width, height = _get_tab_viewport_size(tab)
    return (
        int(width * random.uniform(0.35, 0.65)),
        int(height * random.uniform(0.20, 0.45)),
    )


def _stealth_click_element(tab: Any, ele: Any, mouse_pos: Optional[tuple] = None, hold_duration: Optional[float] = None) -> tuple:
    target = _get_element_viewport_pos(ele)
    if target is None:
        try:
            rect = ele.run_js(
                "const r = this.getBoundingClientRect();"
                "return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};"
            )
            if rect and rect.get("x") is not None and rect.get("y") is not None:
                target = (int(rect["x"]), int(rect["y"]))
        except Exception:
            target = None
    if target is None:
        raise RuntimeError("element_viewport_pos_unavailable")

    click_x = target[0] + random.randint(-4, 4)
    click_y = target[1] + random.randint(-3, 3)
    start = _ensure_mouse_origin(tab, mouse_pos)
    end = smooth_move_mouse(tab, start, (click_x, click_y))
    _human_micro_delay(0.06, 0.35)
    if not cdp_precise_click(tab, click_x, click_y, hold_duration=hold_duration):
        # 重试使用不同偏移而非同坐标，避免同位置连续点击
        _human_micro_delay(0.10, 0.30)
        retry_x = target[0] + random.randint(-6, 6)
        retry_y = target[1] + random.randint(-5, 5)
        if not cdp_precise_click(tab, retry_x, retry_y, hold_duration=hold_duration):
            # 最终降级到 ele.click()
            try:
                ele.click()
            except Exception:
                raise RuntimeError("cdp_precise_click_failed")
    return end


def _make_text_input_handler(tab: Any) -> TextInputHandler:
    return TextInputHandler(
        tab,
        stealth_mode=True,
        smart_delay_fn=lambda minimum=0.05, maximum=0.12: time.sleep(random.uniform(minimum, maximum)),
        check_cancelled_fn=lambda: False,
    )


def _iter_targets(root: Any, max_depth: int = 4) -> Iterable[Any]:
    seen: Set[str] = set()

    def _key(page: Any) -> str:
        frame_id = str(_safe_attr(page, "_frame_id", "") or "")
        url = str(_safe_attr(page, "url", "") or "")
        if frame_id or url:
            return f"{frame_id}|{url}"
        return str(id(page))

    def _walk(page: Any, depth: int) -> Iterable[Any]:
        marker = _key(page)
        if marker in seen:
            return
        seen.add(marker)
        yield page
        if depth >= max_depth:
            return
        try:
            children = page.get_frames() or []
        except Exception:
            children = []
        for child in children:
            yield from _walk(child, depth + 1)

    yield from _walk(root, 0)


def _find_first_element(root: Any, selectors: Sequence[str], timeout: float = 1.0, search_frames: bool = False) -> Any:
    targets = list(_iter_targets(root)) if search_frames else [root]
    for page in targets:
        for selector in selectors:
            try:
                ele = page.ele(selector, timeout=timeout)
            except Exception:
                ele = None
            if ele:
                return ele
    return None


def _find_button_by_text(root: Any, texts: Sequence[str], timeout: float = 1.0, search_frames: bool = False) -> Any:
    normalized = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not normalized:
        return None

    button_selectors = [
        "tag:button",
        "xpath://input[@type='button' or @type='submit']",
    ]
    targets = list(_iter_targets(root)) if search_frames else [root]
    for page in targets:
        for selector in button_selectors:
            try:
                items = page.eles(selector, timeout=timeout) or []
            except Exception:
                items = []
            for ele in items:
                label = _button_label(ele)
                if not label:
                    continue
                if any(text in label for text in normalized):
                    return ele
    return None


def _find_clickable_by_text(
    root: Any,
    texts: Sequence[str],
    timeout: float = 1.0,
    search_frames: bool = False,
    selectors: Optional[Sequence[str]] = None,
) -> Any:
    normalized = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not normalized:
        return None

    target_selectors = list(selectors or [])
    if not target_selectors:
        target_selectors = [
            "tag:button",
            "xpath://input[@type='button' or @type='submit']",
            "xpath://*[@role='button']",
            "xpath://*[@role='option']",
            "tag:li",
            "tag:option",
            "tag:div",
            "tag:span",
        ]

    targets = list(_iter_targets(root)) if search_frames else [root]
    for page in targets:
        for selector in target_selectors:
            try:
                items = page.eles(selector, timeout=timeout) or []
            except Exception:
                items = []
            for ele in items:
                label = _button_label(ele)
                if not label:
                    continue
                if any(text == label or text in label for text in normalized):
                    return ele
    return None


def _click_first_button(root: Any, texts: Sequence[str], timeout: float = 1.0, search_frames: bool = False) -> bool:
    ele = _find_button_by_text(root, texts, timeout=timeout, search_frames=search_frames)
    if not ele:
        return False
    try:
        ele.click()
        return True
    except Exception:
        return False


def _stealth_click_by_text(
    tab: Any,
    root: Any,
    texts: Sequence[str],
    timeout: float = 1.0,
    search_frames: bool = False,
    selectors: Optional[Sequence[str]] = None,
    mouse_pos: Optional[tuple] = None,
    hold_duration: Optional[float] = None,
) -> tuple:
    ele = _find_clickable_by_text(
        root,
        texts,
        timeout=timeout,
        search_frames=search_frames,
        selectors=selectors,
    )
    if not ele:
        raise RuntimeError("click_target_not_found")
    return _stealth_click_element(tab, ele, mouse_pos=mouse_pos, hold_duration=hold_duration)


def _has_any_text(root: Any, texts: Sequence[str], search_frames: bool = False) -> bool:
    normalized = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not normalized:
        return False
    targets = list(_iter_targets(root)) if search_frames else [root]
    for page in targets:
        content = _read_body_text(page)
        if not content:
            continue
        if any(text in content for text in normalized):
            return True
    return False


def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.35) -> bool:
    deadline = time.time() + max(0.1, float(timeout))
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        _sleep(interval)
    try:
        return bool(predicate())
    except Exception:
        return False


def _set_input(root: Any, selectors: Sequence[str], value: str, timeout: float = 8.0) -> bool:
    ele = _find_first_element(root, selectors, timeout=timeout, search_frames=False)
    if not ele:
        return False
    try:
        ele.clear()
    except Exception:
        pass
    try:
        ele.input(value)
    except Exception:
        try:
            ele.run_js(
                """
                const value = arguments[0];
                const el = this;
                if (!el) return false;
                if ('value' in el) {
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
                """,
                value,
            )
        except Exception:
            return False
    _sleep(0.4)
    return True


def _read_input_value_loose(ele: Any) -> str:
    candidates: List[str] = []
    try:
        value = ele.run_js(
            """
            return (function () {
                try {
                    const el = this;
                    if (!el) return '';
                    if ('value' in el && el.value != null) return String(el.value);
                    return String(el.innerText || el.textContent || '');
                } catch (error) {
                    return '';
                }
            }).call(this);
            """
        )
        candidates.append(str(value or ""))
    except Exception:
        pass
    for getter in (
        lambda x: x.attr("value"),
        lambda x: _safe_attr(x, "text", ""),
    ):
        try:
            candidates.append(str(getter(ele) or ""))
        except Exception:
            continue
    candidates = [item for item in candidates if item]
    if not candidates:
        return ""
    return max(candidates, key=len)


def _normalize_compare_text(value: str) -> str:
    return "".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split())


def _snapshot_has_any(snapshot: str, texts: Sequence[str]) -> bool:
    normalized_snapshot = _normalize_compare_text(str(snapshot or "").lower())
    if not normalized_snapshot:
        return False
    for text in texts:
        normalized_text = _normalize_compare_text(str(text or "").lower())
        if normalized_text and normalized_text in normalized_snapshot:
            return True
    return False


def _input_matches_expected(ele: Any, expected: str) -> bool:
    actual = _read_input_value_loose(ele)
    if actual == expected:
        return True
    actual_norm = _normalize_compare_text(actual)
    expected_norm = _normalize_compare_text(expected)
    return bool(expected_norm) and actual_norm == expected_norm


def _wait_input_match(ele: Any, expected: str, timeout: float = 2.2) -> bool:
    return _wait_until(lambda: _input_matches_expected(ele, expected), timeout=timeout, interval=0.25)


def _hotmail_email_domain_is_split(tab: Any) -> bool:
    return _find_first_element(
        tab,
        [
            "xpath://button[contains(@id, 'Domain') or contains(@aria-label, '域') or contains(@aria-label, 'domain')]",
            "xpath://button[contains(., '@hotmail.com') or contains(., '@outlook.com') or contains(., '@live.com')]",
            "xpath://*[@role='combobox' and (contains(., '@hotmail.com') or contains(., '@outlook.com') or contains(., '@live.com'))]",
        ],
        timeout=1.0,
        search_frames=False,
    ) is not None


def _find_hotmail_email_input(tab: Any, timeout: float = 1.0) -> Any:
    return _find_first_element(
        tab,
        [
            "xpath://input[@type='email' or @name='email' or @name='MemberName']",
        ],
        timeout=timeout,
        search_frames=False,
    )


def _hotmail_email_input_matches(tab: Any, email_local: str, email_full: str) -> bool:
    ele = _find_hotmail_email_input(tab, timeout=0.3)
    if not ele:
        return False
    actual = _read_input_value_loose(ele)
    actual_norm = _normalize_compare_text(actual)
    local_norm = _normalize_compare_text(email_local)
    full_norm = _normalize_compare_text(email_full)
    if actual_norm and (actual_norm == local_norm or actual_norm == full_norm):
        return True

    if _hotmail_email_domain_is_split(tab):
        return bool(local_norm) and actual_norm == local_norm

    return bool(full_norm) and actual_norm == full_norm


def _wait_hotmail_email_match(tab: Any, email_local: str, email_full: str, timeout: float = 2.2) -> bool:
    return _wait_until(
        lambda: _hotmail_email_input_matches(tab, email_local, email_full),
        timeout=timeout,
        interval=0.25,
    )


def _fill_hotmail_email_field(
    tab: Any,
    email_local: str,
    email_full: str,
    mouse_pos: Optional[tuple] = None,
    timeout: float = 8.0,
) -> tuple:
    selectors = ["xpath://input[@type='email' or @name='email' or @name='MemberName']"]
    ele = _find_hotmail_email_input(tab, timeout=timeout)
    if not ele:
        raise RuntimeError("email_input_not_found")

    email_value = email_local if _hotmail_email_domain_is_split(tab) else email_full
    end = _stealth_click_element(tab, ele, mouse_pos=mouse_pos)

    # 邮箱字段关闭 typo 模拟，优先确保完整写入
    typed_ok = _human_type_text(tab, email_value, typo_rate=0.0)

    if not typed_ok:
        # 降级到剪贴板粘贴
        handler = _make_text_input_handler(tab)
        try:
            handler.fill_via_clipboard_no_click(ele, email_value)
        except Exception:
            pass

    if not _wait_hotmail_email_match(tab, email_local, email_full, timeout=3.2):
        fallback_value = email_local if _hotmail_email_domain_is_split(tab) else email_full
        current_ele = _find_hotmail_email_input(tab, timeout=0.8)
        current_value = _read_input_value_loose(current_ele) if current_ele else ""
        if not _set_input(tab, selectors, fallback_value, timeout=2.0):
            raise RuntimeError(f"hotmail_email_verify_failed:{current_value}")
        if not _wait_hotmail_email_match(tab, email_local, email_full, timeout=1.8):
            raise RuntimeError(f"hotmail_email_verify_failed:{current_value}")

    _human_micro_delay(0.25, 0.35)
    return end


def _stealth_set_input(tab: Any, selectors: Sequence[str], value: str, timeout: float = 8.0, mouse_pos: Optional[tuple] = None) -> tuple:
    ele = _find_first_element(tab, selectors, timeout=timeout, search_frames=False)
    if not ele:
        raise RuntimeError("input_not_found")
    end = _stealth_click_element(tab, ele, mouse_pos=mouse_pos)

    # 优先逐字符击键，避免剪贴板粘贴的 inputType 检测
    typed_ok = _human_type_text(tab, value)

    if typed_ok and _wait_input_match(ele, value, timeout=1.4):
        _human_micro_delay(0.25, 0.35)
        return end

    # 降级到剪贴板粘贴
    handler = _make_text_input_handler(tab)
    try:
        handler.fill_via_clipboard_no_click(ele, value)
    except Exception as error:
        if _wait_input_match(ele, value, timeout=2.6):
            _human_micro_delay(0.25, 0.35)
            return end
        try:
            if _set_input(tab, selectors, value, timeout=2.0):
                _human_micro_delay(0.25, 0.35)
                return end
        except Exception:
            pass
        raise RuntimeError(f"stealth_paste_failed:{error}") from error

    if not _wait_input_match(ele, value, timeout=1.4):
        if not _set_input(tab, selectors, value, timeout=2.0):
            raise RuntimeError("stealth_input_verify_failed")
    _human_micro_delay(0.25, 0.35)
    return end


def _select_birth_date(tab: Any, month_text: str = "4月", day_text: str = "13日") -> bool:
    try:
        tab.run_js(
            """
            const monthBtn = document.getElementById('BirthMonthDropdown');
            if (monthBtn) monthBtn.click();
            return true;
            """
        )
        _sleep(0.5)
        month_ok = bool(
            tab.run_js(
                f"""
                const opt = Array.from(document.querySelectorAll('[role="option"]'))
                    .find(el => (el.innerText || '').trim() === {month_text!r});
                if (opt) opt.click();
                return !!opt;
                """
            )
        )
        _sleep(0.5)
        tab.run_js(
            """
            const dayBtn = document.getElementById('BirthDayDropdown');
            if (dayBtn) dayBtn.click();
            return true;
            """
        )
        _sleep(0.5)
        day_ok = bool(
            tab.run_js(
                f"""
                const opt = Array.from(document.querySelectorAll('[role="option"]'))
                    .find(el => (el.innerText || '').trim() === {day_text!r});
                if (opt) opt.click();
                return !!opt;
                """
            )
        )
        _sleep(0.5)
        return month_ok and day_ok and _birth_dropdown_matches(tab, "BirthMonthDropdown", month_text) and _birth_dropdown_matches(tab, "BirthDayDropdown", day_text)
    except Exception:
        return False


def _birth_dropdown_matches(tab: Any, button_id: str, expected_text: str) -> bool:
    button = _find_first_element(
        tab,
        [f"xpath://button[@id='{button_id}']"],
        timeout=0.8,
        search_frames=False,
    )
    if not button:
        return False
    actual = _button_label(button)
    actual_norm = _normalize_compare_text(actual)
    expected_norm = _normalize_compare_text(expected_text)
    return bool(expected_norm) and actual_norm == expected_norm


def _select_birth_dropdown_stealth(
    tab: Any,
    button_selector: str,
    button_id: str,
    option_text: str,
    mouse_pos: Optional[tuple] = None,
    attempts: int = 3,
) -> tuple:
    last_mouse = mouse_pos
    selectors = ["xpath://*[@role='option']", "tag:li", "tag:option", "tag:div", "tag:span"]

    for _ in range(max(1, int(attempts))):
        if _birth_dropdown_matches(tab, button_id, option_text):
            return last_mouse

        button = _find_first_element(tab, [button_selector], timeout=2.5, search_frames=False)
        if not button:
            raise RuntimeError(f"{button_id}_not_found")

        last_mouse = _stealth_click_element(tab, button, mouse_pos=last_mouse)
        _human_micro_delay(0.45, 0.35)

        last_mouse = _stealth_click_by_text(
            tab,
            tab,
            [option_text],
            timeout=2.4,
            selectors=selectors,
            mouse_pos=last_mouse,
        )
        _human_micro_delay(0.50, 0.35)

        if _wait_until(lambda: _birth_dropdown_matches(tab, button_id, option_text), timeout=1.6, interval=0.2):
            return last_mouse

    raise RuntimeError(f"{button_id}_select_failed")


def _select_birth_date_stealth(tab: Any, mouse_pos: Optional[tuple] = None, month_text: str = "4月", day_text: str = "13日") -> tuple:
    mouse_pos = _select_birth_dropdown_stealth(
        tab,
        "xpath://button[@id='BirthMonthDropdown' or @name='BirthMonth']",
        "BirthMonthDropdown",
        month_text,
        mouse_pos=mouse_pos,
        attempts=3,
    )
    _human_micro_delay(0.40, 0.35)

    mouse_pos = _select_birth_dropdown_stealth(
        tab,
        "xpath://button[@id='BirthDayDropdown' or @name='BirthDay']",
        "BirthDayDropdown",
        day_text,
        mouse_pos=mouse_pos,
        attempts=4,
    )
    _human_micro_delay(0.40, 0.35)

    if not _birth_dropdown_matches(tab, "BirthMonthDropdown", month_text):
        raise RuntimeError("birth_month_verify_failed")
    if not _birth_dropdown_matches(tab, "BirthDayDropdown", day_text):
        raise RuntimeError("birth_day_verify_failed")
    return mouse_pos


def _click_iframe_relative(tab: Any, iframe_selector: str, relative_x: float, relative_y: float, mouse_pos: Optional[tuple] = None, hold_duration: Optional[float] = None) -> tuple:
    iframe = _find_first_element(tab, [iframe_selector], timeout=1.2, search_frames=False)
    if not iframe:
        raise RuntimeError("iframe_not_found")
    target = _get_element_viewport_pos(iframe)
    if target is None:
        raise RuntimeError("iframe_viewport_pos_unavailable")
    rect = getattr(iframe, "rect", None)
    size = getattr(rect, "size", None) if rect is not None else None
    if not size or len(size) < 2:
        raise RuntimeError("iframe_size_unavailable")
    width, height = int(size[0]), int(size[1])
    click_x = int(target[0] - width / 2 + width * relative_x)
    click_y = int(target[1] - height / 2 + height * relative_y)
    start = _ensure_mouse_origin(tab, mouse_pos)
    end = smooth_move_mouse(tab, start, (click_x, click_y))
    _human_micro_delay(0.06, 0.35)
    if not cdp_precise_click(tab, click_x, click_y, hold_duration=hold_duration):
        _human_micro_delay(0.10, 0.30)
        retry_x = click_x + random.randint(-4, 4)
        retry_y = click_y + random.randint(-3, 3)
        if not cdp_precise_click(tab, retry_x, retry_y, hold_duration=hold_duration):
            raise RuntimeError("iframe_relative_click_failed")
    return end


def _click_viewport_point(tab: Any, x: int, y: int, mouse_pos: Optional[tuple] = None, hold_duration: Optional[float] = None, jitter_radius: int = 0) -> tuple:
    radius = max(0, int(jitter_radius))
    click_x = int(x) + (random.randint(-radius, radius) if radius else 0)
    click_y = int(y) + (random.randint(-radius, radius) if radius else 0)
    start = _ensure_mouse_origin(tab, mouse_pos)
    end = smooth_move_mouse(tab, start, (click_x, click_y))
    _human_micro_delay(0.06, 0.35)
    if not cdp_precise_click(tab, click_x, click_y, hold_duration=hold_duration):
        _human_micro_delay(0.10, 0.30)
        retry_x = click_x + random.randint(-4, 4)
        retry_y = click_y + random.randint(-3, 3)
        if not cdp_precise_click(tab, retry_x, retry_y, hold_duration=hold_duration):
            raise RuntimeError("viewport_click_failed")
    return end


def _click_viewport_ratio(tab: Any, ratio_x: float, ratio_y: float, mouse_pos: Optional[tuple] = None, hold_duration: Optional[float] = None) -> tuple:
    width, height = _get_tab_viewport_size(tab)
    click_x = int(width * float(ratio_x))
    click_y = int(height * float(ratio_y))
    return _click_viewport_point(tab, click_x, click_y, mouse_pos=mouse_pos, hold_duration=hold_duration)


def _get_element_rect_summary(ele: Any) -> Dict[str, int]:
    rect = getattr(ele, "rect", None)
    location = getattr(rect, "location", None) if rect is not None else None
    size = getattr(rect, "size", None) if rect is not None else None
    x = int(location[0]) if location and len(location) >= 2 else -1
    y = int(location[1]) if location and len(location) >= 2 else -1
    width = int(size[0]) if size and len(size) >= 2 else -1
    height = int(size[1]) if size and len(size) >= 2 else -1
    return {"x": x, "y": y, "width": width, "height": height}


def _log_challenge_geometry(tab: Any, logger: Any) -> None:
    viewport_width, viewport_height = _get_tab_viewport_size(tab)
    iframe = _find_first_element(tab, [HOTMAIL_CHALLENGE_IFRAME_SELECTOR], timeout=0.8, search_frames=False)
    if not iframe:
        logger.info(f"[HotmailWorkflow] 挑战几何信息：viewport={viewport_width}x{viewport_height} iframe=not_found")
        return
    rect_info = _get_element_rect_summary(iframe)
    logger.info(
        "[HotmailWorkflow] 挑战几何信息："
        f"viewport={viewport_width}x{viewport_height} "
        f"iframe=({rect_info['x']},{rect_info['y']},{rect_info['width']}x{rect_info['height']})"
    )


def _attempt_hotmail_challenge_click(tab: Any, logger: Any, mouse_pos: Optional[tuple] = None) -> tuple:
    try:
        top_level_button = _find_clickable_by_text(
            tab,
            CHALLENGE_BUTTON_TEXTS,
            timeout=0.8,
            search_frames=False,
            selectors=["tag:button", "xpath://*[@role='button']"],
        )
        if top_level_button:
            end = _stealth_click_element(tab, top_level_button, mouse_pos=mouse_pos)
            logger.info("[HotmailWorkflow] 挑战点击方式：top_level_text")
            return True, end
    except Exception:
        pass

    for index, (x, y, radius) in enumerate(HOTMAIL_CHALLENGE_VIEWPORT_POINTS, start=1):
        try:
            end = _click_viewport_point(tab, x, y, mouse_pos=mouse_pos, jitter_radius=radius)
            logger.info(f"[HotmailWorkflow] 挑战点击方式：viewport_point#{index} ({x},{y},r={radius})")
            return True, end
        except Exception:
            continue

    for index, (rel_x, rel_y) in enumerate(HOTMAIL_CHALLENGE_IFRAME_RELATIVE_POINTS, start=1):
        try:
            end = _click_iframe_relative(
                tab,
                HOTMAIL_CHALLENGE_IFRAME_SELECTOR,
                rel_x,
                rel_y,
                mouse_pos=mouse_pos,
            )
            logger.info(f"[HotmailWorkflow] 挑战点击方式：iframe_relative#{index} ({rel_x:.2f},{rel_y:.2f})")
            return True, end
        except Exception:
            continue

    for index, (ratio_x, ratio_y) in enumerate(HOTMAIL_CHALLENGE_VIEWPORT_RATIO_POINTS, start=1):
        try:
            end = _click_viewport_ratio(tab, ratio_x, ratio_y, mouse_pos=mouse_pos)
            logger.info(f"[HotmailWorkflow] 挑战点击方式：viewport_ratio#{index} ({ratio_x:.2f},{ratio_y:.2f})")
            return True, end
        except Exception:
            continue

    return False, mouse_pos


def _attempt_hotmail_press_again_click(tab: Any, logger: Any, mouse_pos: Optional[tuple] = None) -> tuple:
    try:
        top_level_button = _find_clickable_by_text(
            tab,
            PRESS_AGAIN_TEXTS,
            timeout=0.8,
            search_frames=False,
            selectors=["tag:button", "xpath://*[@role='button']"],
        )
        if top_level_button:
            end = _stealth_click_element(tab, top_level_button, mouse_pos=mouse_pos)
            logger.info("[HotmailWorkflow] 再次按下点击方式：top_level_text")
            return True, end
    except Exception:
        pass

    for index, (x, y, radius) in enumerate(HOTMAIL_CHALLENGE_PRESS_AGAIN_VIEWPORT_POINTS, start=1):
        try:
            end = _click_viewport_point(tab, x, y, mouse_pos=mouse_pos, jitter_radius=radius)
            logger.info(f"[HotmailWorkflow] 再次按下点击方式：viewport_point#{index} ({x},{y},r={radius})")
            return True, end
        except Exception:
            continue

    return _attempt_hotmail_challenge_click(tab, logger, mouse_pos=mouse_pos)


def _human_wait_with_mouse(tab: Any, seconds: float, mouse_pos: Optional[tuple] = None, check_cancelled=None) -> tuple:
    wait_seconds = max(0.0, float(seconds))
    if wait_seconds <= 0:
        return mouse_pos or _ensure_mouse_origin(tab, mouse_pos)
    center = _ensure_mouse_origin(tab, mouse_pos)
    _raise_if_cancelled(check_cancelled)

    # 30% 概率完全静止（手离开鼠标）
    if random.random() < 0.3:
        _sleep(wait_seconds)
        _raise_if_cancelled(check_cancelled)
        return center

    try:
        return idle_drift(
            tab,
            duration=wait_seconds,
            center_pos=center,
            drift_radius=random.uniform(2.0, 15.0),
            freq_hz=random.uniform(0.3, 2.0),
            check_cancelled=check_cancelled,
        )
    except Exception:
        _raise_if_cancelled(check_cancelled)
        _sleep(wait_seconds)
        _raise_if_cancelled(check_cancelled)
        return center


def _cdp_type_digits(tab: Any, digits: str, delay_range: tuple = (0.10, 0.22)) -> bool:
    text = "".join(ch for ch in str(digits or "") if ch.isdigit())
    if not text:
        return False
    keycode_map = {
        "0": 48,
        "1": 49,
        "2": 50,
        "3": 51,
        "4": 52,
        "5": 53,
        "6": 54,
        "7": 55,
        "8": 56,
        "9": 57,
    }
    for ch in text:
        code = keycode_map.get(ch)
        if code is None:
            continue
        try:
            tab.run_cdp(
                "Input.dispatchKeyEvent",
                type="rawKeyDown",
                windowsVirtualKeyCode=code,
                nativeVirtualKeyCode=code,
                code=f"Digit{ch}",
                key=ch,
                text=ch,
                unmodifiedText=ch,
            )
            tab.run_cdp(
                "Input.dispatchKeyEvent",
                type="keyUp",
                windowsVirtualKeyCode=code,
                nativeVirtualKeyCode=code,
                code=f"Digit{ch}",
                key=ch,
            )
        except Exception:
            return False
        _sleep(random.uniform(*delay_range))
    return True


def _cdp_press_key(tab: Any, key: str, code: str, virtual_code: int, text: str = "") -> bool:
    try:
        down_payload = {
            "type": "rawKeyDown",
            "windowsVirtualKeyCode": virtual_code,
            "nativeVirtualKeyCode": virtual_code,
            "code": code,
            "key": key,
        }
        if text:
            down_payload["text"] = text
            down_payload["unmodifiedText"] = text
        tab.run_cdp("Input.dispatchKeyEvent", **down_payload)
        tab.run_cdp(
            "Input.dispatchKeyEvent",
            type="keyUp",
            windowsVirtualKeyCode=virtual_code,
            nativeVirtualKeyCode=virtual_code,
            code=code,
            key=key,
        )
        return True
    except Exception:
        return False


def _cdp_press_enter(tab: Any) -> bool:
    return _cdp_press_key(tab, key="Enter", code="Enter", virtual_code=13, text="\r")


def _click_viewport_ratio_points(tab: Any, points: Sequence[tuple], mouse_pos: Optional[tuple] = None, jitter_radius: int = 8) -> tuple:
    last_mouse = mouse_pos
    for ratio_x, ratio_y in points:
        try:
            width, height = _get_tab_viewport_size(tab)
            click_x = int(width * float(ratio_x))
            click_y = int(height * float(ratio_y))
            last_mouse = _click_viewport_point(
                tab,
                click_x,
                click_y,
                mouse_pos=last_mouse,
                jitter_radius=jitter_radius,
            )
            return True, last_mouse
        except Exception:
            continue
    return False, last_mouse


def _has_passkey_signal(tab: Any) -> bool:
    if _has_any_text(tab, PASSKEY_INFO_TEXTS, search_frames=False):
        return True
    if _has_any_text(tab, GOOGLE_PASSWORD_MANAGER_TEXTS, search_frames=False):
        return True
    if _has_any_text(tab, PASSKEY_LOCATION_TEXTS, search_frames=False):
        return True
    if _has_any_text(tab, PASSKEY_CREATE_TEXTS, search_frames=False):
        return True
    if _has_any_text(tab, PIN_PROMPT_TEXTS, search_frames=False):
        return True
    if _find_button_by_text(tab, PASSKEY_BUTTON_TEXTS, timeout=0.4, search_frames=False) is not None:
        return True
    return False


def _handle_hotmail_post_passkey_flow(
    tab: Any,
    logger: Any,
    mouse_pos: Optional[tuple] = None,
    check_cancelled=None,
    passkey_pin: str = "",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": True,
        "status": "post_passkey_completed",
        "mouse_pos": mouse_pos,
        "steps": [],
    }
    _raise_if_cancelled(check_cancelled)

    passkey_ready = _wait_until(
        lambda: _has_passkey_signal(tab),
        timeout=4.5,
        interval=0.5,
    )
    if not passkey_ready:
        logger.info("[HotmailWorkflow] 未检测到 passkey 阶段信号，停止后置流程，避免误点空气")
        return {
            "ok": False,
            "status": "post_passkey_ui_not_present",
            "mouse_pos": mouse_pos,
            "steps": result["steps"],
        }

    if _has_any_text(tab, PASSKEY_INFO_TEXTS, search_frames=False):
        try:
            mouse_pos = _stealth_click_by_text(
                tab,
                tab,
                PASSKEY_INFO_CONFIRM_TEXTS,
                timeout=1.2,
                search_frames=False,
                mouse_pos=mouse_pos,
            )
            logger.info("[HotmailWorkflow] 已点击 Microsoft 快速说明页“确定”")
            result["steps"].append("passkey_info_confirmed")
            mouse_pos = _human_wait_with_mouse(tab, random.uniform(1.2, 2.0), mouse_pos=mouse_pos, check_cancelled=check_cancelled)
        except Exception:
            return {
                "ok": False,
                "status": "passkey_info_confirm_not_found",
                "mouse_pos": mouse_pos,
                "steps": result["steps"],
            }

    location_ready = _wait_until(
        lambda: (
            _has_any_text(tab, GOOGLE_PASSWORD_MANAGER_TEXTS, search_frames=False)
            or _has_any_text(tab, PASSKEY_LOCATION_TEXTS, search_frames=False)
        ),
        timeout=4,
        interval=0.5,
    )
    clicked_manager = False
    if location_ready:
        try:
            mouse_pos = _stealth_click_by_text(
                tab,
                tab,
                GOOGLE_PASSWORD_MANAGER_TEXTS,
                timeout=1.2,
                search_frames=False,
                selectors=["xpath://*[@role='button']", "tag:button", "tag:div"],
                mouse_pos=mouse_pos,
            )
            logger.info("[HotmailWorkflow] 已点击 Google 密码管理工具（网页可见）")
            clicked_manager = True
        except Exception:
            clicked_manager = False

    if not clicked_manager and location_ready:
        clicked_manager, mouse_pos = _click_viewport_ratio_points(
            tab,
            HOTMAIL_GOOGLE_PASSWORD_MANAGER_RATIO_POINTS,
            mouse_pos=mouse_pos,
        )
        if clicked_manager:
            logger.info("[HotmailWorkflow] 已通过坐标点击 Google 密码管理工具")

    if not clicked_manager:
        logger.warning("[HotmailWorkflow] 未能触发 Google 密码管理工具选择")
        return {
            "ok": False,
            "status": "google_password_manager_click_failed",
            "mouse_pos": mouse_pos,
            "steps": result["steps"],
        }

    result["steps"].append("google_password_manager_clicked")
    mouse_pos = _human_wait_with_mouse(tab, random.uniform(1.5, 2.5), mouse_pos=mouse_pos, check_cancelled=check_cancelled)

    create_ready = _wait_until(
        lambda: _has_any_text(tab, PASSKEY_CREATE_TEXTS, search_frames=False),
        timeout=4,
        interval=0.5,
    )
    clicked_create = False
    if create_ready:
        try:
            mouse_pos = _stealth_click_by_text(
                tab,
                tab,
                PASSKEY_CREATE_TEXTS,
                timeout=1.2,
                search_frames=False,
                selectors=["tag:button", "xpath://*[@role='button']"],
                mouse_pos=mouse_pos,
            )
            logger.info("[HotmailWorkflow] 已点击“创建”（网页可见）")
            clicked_create = True
        except Exception:
            clicked_create = False

    if not clicked_create and create_ready:
        clicked_create, mouse_pos = _click_viewport_ratio_points(
            tab,
            HOTMAIL_PASSKEY_CREATE_RATIO_POINTS,
            mouse_pos=mouse_pos,
        )
        if clicked_create:
            logger.info("[HotmailWorkflow] 已通过坐标点击“创建”")

    if not clicked_create:
        logger.warning("[HotmailWorkflow] 未能触发“创建”")
        return {
            "ok": False,
            "status": "passkey_create_click_failed",
            "mouse_pos": mouse_pos,
            "steps": result["steps"],
        }

    result["steps"].append("passkey_create_clicked")
    mouse_pos = _human_wait_with_mouse(tab, random.uniform(1.2, 2.0), mouse_pos=mouse_pos, check_cancelled=check_cancelled)

    pin_ready = _wait_until(
        lambda: _has_any_text(tab, PIN_PROMPT_TEXTS, search_frames=False),
        timeout=4,
        interval=0.5,
    )
    if not pin_ready:
        clicked_pin_box, mouse_pos = _click_viewport_ratio_points(
            tab,
            HOTMAIL_PASSKEY_PIN_BOX_RATIO_POINTS,
            mouse_pos=mouse_pos,
            jitter_radius=6,
        )
        if clicked_pin_box:
            logger.info("[HotmailWorkflow] 已通过坐标激活 PIN 输入框")
            mouse_pos = _human_wait_with_mouse(tab, random.uniform(0.4, 0.8), mouse_pos=mouse_pos, check_cancelled=check_cancelled)

    resolved_pin = str(passkey_pin or "").strip()
    if not resolved_pin:
        pin_info = _resolve_hotmail_passkey_pin()
        if pin_info.get("ok"):
            resolved_pin = str(pin_info.get("pin", "") or "").strip()
        else:
            status = str(pin_info.get("status", "") or "pin_not_configured")
            if status == "pin_invalid_format":
                logger.warning(
                    f"[HotmailWorkflow] Windows Hello PIN 配置格式无效: env={pin_info.get('env_key')}, "
                    f"value={pin_info.get('raw_value')!r}"
                )
            else:
                logger.warning("[HotmailWorkflow] 未配置 Windows Hello PIN，无法继续通行密钥确认")
            return {
                "ok": False,
                "status": status,
                "mouse_pos": mouse_pos,
                "steps": result["steps"],
            }

    if not resolved_pin:
        return {
            "ok": False,
            "status": "pin_not_configured",
            "mouse_pos": mouse_pos,
            "steps": result["steps"],
        }

    if not _cdp_type_digits(tab, resolved_pin):
        return {
            "ok": False,
            "status": "pin_input_failed",
            "mouse_pos": mouse_pos,
            "steps": result["steps"],
        }

    logger.info("[HotmailWorkflow] 已输入 Google 密码管理工具 PIN")
    result["steps"].append("pin_entered")
    mouse_pos = _human_wait_with_mouse(tab, random.uniform(2.0, 3.2), mouse_pos=mouse_pos, check_cancelled=check_cancelled)
    result["mouse_pos"] = mouse_pos
    return result


def _wait_for_form_page(tab: Any, email: str) -> bool:
    return _wait_until(
        lambda: _set_input(
            tab,
            [
                "xpath://input[@type='email' or @name='email' or @name='MemberName']",
            ],
            email,
            timeout=1,
        ),
        timeout=20,
        interval=0.6,
    )


def _hotmail_challenge_still_present(tab: Any) -> bool:
    snapshot = _hotmail_get_page_check_snapshot(tab)
    if _snapshot_has_any(snapshot, BLOCKED_TEXTS):
        return False
    if _find_button_by_text(tab, PASSKEY_BUTTON_TEXTS, timeout=0.5, search_frames=False) is not None:
        return False
    if _snapshot_has_any(snapshot, CHALLENGE_BUTTON_TEXTS):
        return True
    if _snapshot_has_any(snapshot, PRESS_AGAIN_TEXTS):
        return True
    return _find_first_element(tab, [HOTMAIL_CHALLENGE_IFRAME_SELECTOR], timeout=0.4, search_frames=False) is not None


def _hotmail_detect_challenge_state(tab: Any) -> Dict[str, Any]:
    snapshot = _hotmail_get_page_check_snapshot(tab)
    if _snapshot_has_any(snapshot, BLOCKED_TEXTS):
        return {"state": "blocked", "snapshot": snapshot}
    if _find_button_by_text(tab, PASSKEY_BUTTON_TEXTS, timeout=0.4, search_frames=False) is not None:
        return {"state": "passkey", "snapshot": snapshot}
    if _snapshot_has_any(snapshot, PRESS_AGAIN_TEXTS):
        return {"state": "press_again", "snapshot": snapshot}
    if _hotmail_challenge_still_present(tab):
        return {"state": "challenge_present", "snapshot": snapshot}
    return {"state": "challenge_gone", "snapshot": snapshot}


def _wait_for_hotmail_challenge_entry(tab: Any, timeout: float = 12.0) -> Dict[str, Any]:
    last_state: Dict[str, Any] = {"state": "challenge_gone", "snapshot": ""}
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        current = _hotmail_detect_challenge_state(tab)
        current_state = str(current.get("state", "") or "")
        last_state = current
        if current_state in {"blocked", "passkey", "press_again", "challenge_present"}:
            return current
        _sleep(0.5)
    return last_state


def _handle_accessibility_challenge(tab: Any, logger: Any, max_rounds: int = 6, check_cancelled=None) -> Dict[str, Any]:
    if _has_any_text(tab, BLOCKED_TEXTS, search_frames=False):
        return {"ok": False, "status": "blocked_before_challenge"}

    entry_state = _wait_for_hotmail_challenge_entry(tab, timeout=12.0)
    entry_state_name = str(entry_state.get("state", "") or "")
    logger.info(f"[HotmailWorkflow] 挑战入口状态：{entry_state_name or 'unknown'}")
    if entry_state_name == "blocked":
        return {"ok": False, "status": "blocked_before_challenge"}
    if entry_state_name == "passkey":
        return {"ok": True, "status": "passkey_direct_without_challenge"}
    if entry_state_name not in {"press_again", "challenge_present"}:
        return {"ok": False, "status": "challenge_stage_not_detected", "entry_state": entry_state_name}

    mouse_pos: Optional[tuple] = None
    mouse_pos = _human_wait_with_mouse(tab, random.uniform(0.35, 0.85), mouse_pos=mouse_pos, check_cancelled=check_cancelled)
    _log_challenge_geometry(tab, logger)

    for round_index in range(1, max_rounds + 1):
        _raise_if_cancelled(check_cancelled)
        if _has_any_text(tab, BLOCKED_TEXTS, search_frames=False):
            return {"ok": False, "status": "blocked", "round": round_index}

        for initial_attempt in range(1, HOTMAIL_CHALLENGE_INITIAL_CLICK_ATTEMPTS + 1):
            clicked, mouse_pos = _attempt_hotmail_challenge_click(tab, logger, mouse_pos=mouse_pos)
            if clicked:
                logger.info(f"[HotmailWorkflow] 已执行挑战点击，第 {round_index} 轮，第 {initial_attempt} 次首击")
                mouse_pos = _human_wait_with_mouse(tab, HOTMAIL_CHALLENGE_WAIT_AFTER_CLICK_SEC, mouse_pos=mouse_pos, check_cancelled=check_cancelled)
            else:
                logger.warning(f"[HotmailWorkflow] 第 {round_index} 轮，第 {initial_attempt} 次首击未找到可用点击点位")
                mouse_pos = _human_wait_with_mouse(tab, HOTMAIL_CHALLENGE_WAIT_AFTER_CLICK_SEC, mouse_pos=mouse_pos, check_cancelled=check_cancelled)

            transition = _hotmail_detect_challenge_state(tab)
            transition_state = str(transition.get("state", "") or "")
            logger.info(
                f"[HotmailWorkflow] 页面检查状态：{transition_state or 'unknown'}，"
                f"第 {round_index} 轮，第 {initial_attempt} 次首击"
            )
            if transition_state in {"blocked", "passkey", "challenge_gone", "press_again", "challenge_present"}:
                break
            logger.warning(f"[HotmailWorkflow] 首击未触发状态变化，准备重试，第 {round_index} 轮，第 {initial_attempt} 次")
            _sleep(0.8)

        if transition_state == "blocked":
            return {"ok": False, "status": "blocked_after_challenge", "round": round_index}
        if transition_state in {"passkey", "challenge_gone"}:
            return {"ok": True, "status": "challenge_passed_or_page_changed", "round": round_index}

        for press_attempt in range(1, HOTMAIL_CHALLENGE_PRESS_AGAIN_ATTEMPTS + 1):
            logger.info(f"[HotmailWorkflow] 进入“再次按下”阶段，第 {round_index} 轮，第 {press_attempt} 次尝试")
            if press_attempt > 1:
                logger.info(f"[HotmailWorkflow] “再次按下”重试前，重新点击可访问性挑战，第 {round_index} 轮，第 {press_attempt} 次")
                rearm_clicked, mouse_pos = _attempt_hotmail_challenge_click(tab, logger, mouse_pos=mouse_pos)
                if rearm_clicked:
                    logger.info(f"[HotmailWorkflow] 已重新点击可访问性挑战，第 {round_index} 轮，第 {press_attempt} 次")
                else:
                    logger.warning(f"[HotmailWorkflow] 重新点击可访问性挑战失败，第 {round_index} 轮，第 {press_attempt} 次")
                mouse_pos = _human_wait_with_mouse(tab, HOTMAIL_CHALLENGE_WAIT_AFTER_CLICK_SEC, mouse_pos=mouse_pos, check_cancelled=check_cancelled)
                transition = _hotmail_detect_challenge_state(tab)
                transition_state = str(transition.get("state", "") or "")
                logger.info(
                    f"[HotmailWorkflow] 重新激活后的页面检查状态：{transition_state or 'unknown'}，"
                    f"第 {round_index} 轮，第 {press_attempt} 次"
                )
                if transition_state == "blocked":
                    return {"ok": False, "status": "blocked_after_challenge", "round": round_index}
                if transition_state in {"passkey", "challenge_gone"}:
                    return {"ok": True, "status": "challenge_passed_or_page_changed", "round": round_index}

            clicked_again, mouse_pos = _attempt_hotmail_press_again_click(tab, logger, mouse_pos=mouse_pos)
            if not clicked_again:
                logger.warning(f"[HotmailWorkflow] 第 {round_index} 轮，第 {press_attempt} 次“再次按下”未找到可用点击点位")
            mouse_pos = _human_wait_with_mouse(tab, HOTMAIL_CHALLENGE_WAIT_AFTER_CLICK_SEC, mouse_pos=mouse_pos, check_cancelled=check_cancelled)
            transition = _hotmail_detect_challenge_state(tab)
            transition_state = str(transition.get("state", "") or "")
            logger.info(
                f"[HotmailWorkflow] 再次按下后的页面检查状态：{transition_state or 'unknown'}，"
                f"第 {round_index} 轮，第 {press_attempt} 次"
            )
            if transition_state == "blocked":
                return {"ok": False, "status": "blocked_after_challenge", "round": round_index}
            if transition_state in {"passkey", "challenge_gone"}:
                return {"ok": True, "status": "challenge_passed_or_page_changed", "round": round_index}
            logger.warning(f"[HotmailWorkflow] 5秒后仍未进入下一步，准备重试完整挑战链，第 {round_index} 轮，第 {press_attempt} 次")

        _sleep(1.5)

    return {"ok": False, "status": "challenge_retry_exhausted", "rounds": max_rounds}


def run_hotmail_signup_workflow(tab: Any, session: Any, logger: Any, check_cancelled=None) -> Dict[str, Any]:
    try:
        pin_info = _resolve_hotmail_passkey_pin()
        if not pin_info.get("ok"):
            status = str(pin_info.get("status", "") or "pin_not_configured")
            if status == "pin_invalid_format":
                logger.warning(
                    f"[HotmailWorkflow] Windows Hello PIN 配置格式无效: env={pin_info.get('env_key')}, "
                    f"value={pin_info.get('raw_value')!r}"
                )
            else:
                logger.warning("[HotmailWorkflow] 未配置 Windows Hello PIN，停止 Hotmail 注册流程")
            return {
                "ok": False,
                "status": status,
                "required_env_keys": list(HOTMAIL_PASSKEY_PIN_ENV_KEYS),
                "env_key": pin_info.get("env_key", ""),
            }

        passkey_pin = str(pin_info.get("pin", "") or "")
        email_local = _generate_localpart()
        email = f"{email_local}@hotmail.com"
        password = _generate_password()
        names = _generate_name()
        birthday = _generate_birthday()
        birth_year = birthday["year"]

        logger.info(f"[HotmailWorkflow] 开始执行 Hotmail 注册测试流，目标账号={email}")
        _raise_if_cancelled(check_cancelled)
        _clear_microsoft_session(tab, logger)
        _sleep(0.6)
        _raise_if_cancelled(check_cancelled)

        # 先导航到入口页产生自然 Referer，再跳转到注册页
        entry_url = random.choice(_ENTRY_URLS)
        try:
            tab.get(entry_url)
            _human_delay(2.0, 0.4)
            _maybe_human_scroll(tab)
        except Exception:
            pass
        tab.get(HOTMAIL_SIGNUP_URL)
        _human_delay(2.5, 0.5)

        if _is_account_landing_redirect(tab):
            logger.warning("[HotmailWorkflow] 首次打开被重定向到账户中心，准备清理登录态后重试一次")
            _clear_microsoft_session(tab, logger)
            _sleep(0.8)
            tab.get(HOTMAIL_SIGNUP_URL)
            _human_delay(2.8, 0.5)
            if _is_account_landing_redirect(tab):
                return {
                    "ok": False,
                    "status": "redirected_to_account_portal",
                    "url": _current_url(tab),
                }

        mouse_pos: Optional[tuple] = None

        try:
            mouse_pos = _stealth_click_by_text(tab, tab, CONSENT_BUTTON_TEXTS, timeout=1, mouse_pos=mouse_pos)
            logger.info("[HotmailWorkflow] 已点击同意并继续")
            _human_delay(2.5, 0.5)
        except Exception:
            pass

        # 偶尔在页面上游览一下（模拟真人阅读）
        _maybe_human_scroll(tab)

        try:
            mouse_pos = _fill_hotmail_email_field(
                tab,
                email_local=email_local,
                email_full=email,
                timeout=12,
                mouse_pos=mouse_pos,
            )
        except Exception:
            return {"ok": False, "status": "email_input_not_found", "email": email}
        _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 账号：{email}")

        try:
            mouse_pos = _stealth_click_by_text(tab, tab, NEXT_BUTTON_TEXTS, timeout=1, mouse_pos=mouse_pos)
        except Exception:
            return {"ok": False, "status": "next_after_email_not_found", "email": email}
        _human_delay(2.2, 0.5)
        _maybe_human_scroll(tab)

        try:
            mouse_pos = _stealth_set_input(
                tab,
                ["xpath://input[@type='password']"],
                password,
                timeout=12,
                mouse_pos=mouse_pos,
            )
        except Exception:
            return {"ok": False, "status": "password_input_not_found", "email": email}
        _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 密码：{password}")

        try:
            mouse_pos = _stealth_click_by_text(tab, tab, NEXT_BUTTON_TEXTS, timeout=1, mouse_pos=mouse_pos)
        except Exception:
            return {"ok": False, "status": "next_after_password_not_found", "email": email}
        _human_delay(2.0, 0.5)

        try:
            mouse_pos = _stealth_set_input(
                tab,
                ["xpath://input[@name='BirthYear']"],
                birth_year,
                timeout=12,
                mouse_pos=mouse_pos,
            )
            mouse_pos = _select_birth_date_stealth(tab, mouse_pos=mouse_pos, month_text=birthday["month_text"], day_text=birthday["day_text"])
        except Exception:
            year_ok = _set_input(tab, ["xpath://input[@name='BirthYear']"], birth_year, timeout=12)
            date_ok = _select_birth_date(tab, month_text=birthday["month_text"], day_text=birthday["day_text"])
            if not year_ok or not date_ok:
                return {
                    "ok": False,
                    "status": "birthdate_input_failed",
                    "email": email,
                    "birth_year_ok": year_ok,
                    "birth_date_ok": date_ok,
                }

        try:
            mouse_pos = _stealth_click_by_text(tab, tab, NEXT_BUTTON_TEXTS, timeout=1, mouse_pos=mouse_pos)
        except Exception:
            return {
                "ok": False,
                "status": "next_after_birthdate_not_found",
                "email": email,
            }
        _human_delay(2.2, 0.5)
        _maybe_human_scroll(tab)

        try:
            mouse_pos = _stealth_set_input(
                tab,
                ["xpath://input[@id='lastNameInput' or @name='lastNameInput']"],
                names["last_name"],
                timeout=12,
                mouse_pos=mouse_pos,
            )
            mouse_pos = _stealth_set_input(
                tab,
                ["xpath://input[@id='firstNameInput' or @name='firstNameInput']"],
                names["first_name"],
                timeout=12,
                mouse_pos=mouse_pos,
            )
        except Exception:
            return {
                "ok": False,
                "status": "name_input_failed",
                "email": email,
            }
        _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 姓名：{names['full_name']}")

        try:
            mouse_pos = _stealth_click_by_text(tab, tab, NEXT_BUTTON_TEXTS, timeout=1, mouse_pos=mouse_pos)
        except Exception:
            return {"ok": False, "status": "next_after_name_not_found", "email": email}
        _human_delay(2.8, 0.5)

        if _has_any_text(tab, BLOCKED_TEXTS, search_frames=False):
            _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 结果：创建被阻止")
            _append_line(HOTMAIL_LOG_FILE, "----------------")
            return {
                "ok": False,
                "status": "blocked_after_form",
                "email": email,
                "password": password,
                "name": names["full_name"],
            }

        challenge_result = _handle_accessibility_challenge(tab, logger, check_cancelled=check_cancelled)
        if not challenge_result.get("ok"):
            _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 挑战结果：{challenge_result.get('status')}")
            _append_line(HOTMAIL_LOG_FILE, "----------------")
            return {
                "ok": False,
                "status": challenge_result.get("status", "challenge_failed"),
                "email": email,
                "password": password,
                "name": names["full_name"],
                "challenge": challenge_result,
            }

        post_passkey_result = _handle_hotmail_post_passkey_flow(
            tab,
            logger,
            mouse_pos=mouse_pos,
            check_cancelled=check_cancelled,
            passkey_pin=passkey_pin,
        )
        mouse_pos = post_passkey_result.get("mouse_pos")
        if not post_passkey_result.get("ok"):
            _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 后置流程结果：{post_passkey_result.get('status')}")
            _append_line(HOTMAIL_LOG_FILE, "----------------")
            return {
                "ok": False,
                "status": post_passkey_result.get("status", "post_passkey_failed"),
                "email": email,
                "password": password,
                "name": names["full_name"],
                "challenge": challenge_result,
                "post_passkey": post_passkey_result,
            }

        _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 结果：已完成通行密钥后置流程")
        _append_line(HOTMAIL_LOG_FILE, "----------------")
        return {
            "ok": True,
            "status": "completed",
            "email": email,
            "password": password,
            "name": names["full_name"],
            "challenge": challenge_result,
            "post_passkey": post_passkey_result,
            "log_file": HOTMAIL_LOG_FILE,
            "tab_id": getattr(session, "id", ""),
        }
    except AutomationCancelledError:
        logger.info("[HotmailWorkflow] 已收到取消信号，停止当前注册流程")
        _append_line(HOTMAIL_LOG_FILE, f"[{_now_text()}] 结果：用户取消")
        _append_line(HOTMAIL_LOG_FILE, "----------------")
        return {
            "ok": False,
            "status": "cancelled",
            "email": email,
            "password": password,
            "name": names["full_name"],
            "tab_id": getattr(session, "id", ""),
        }
