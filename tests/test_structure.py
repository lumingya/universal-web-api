"""结构棘轮：把「目录结构修复」的结论固化成回归测试。

覆盖本次结构整理（见 ``docs/STRUCTURE-FIX-CHECKLIST.md``）的每一条约束：

1. ``app/`` 是自洽的包——不得反向依赖仓库根目录模块；
2. 根目录只保留允许的入口/兼容壳，其余脚本归入 ``tools/`` / ``examples/``；
3. 版本号只有一个来源（``VERSION``），其余两处必须与之一致；
4. 不存在「模块与包同名」的死文件（``app/core/browser.py``）；
5. 所有 Python 子包都有 ``__init__.py``；
6. 没有重复内容的 CHANGELOG 副本；
7. 包入口 ``python -m app`` 存在，且不重复执行启动引导；
8. 文档不再引用已删除的路径。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

# 根目录允许存在的 Python 文件：入口 + 刻意保持独立的补丁工具 + 兼容壳。
ALLOWED_ROOT_MODULES = {
    "main.py",                 # 唯一入口
    "patch_drissionpage.py",   # 必须在导入 DrissionPage 之前独立运行
    "updater.py",              # 兼容壳 → app.update.engine
    "update_preserve.py",      # 兼容壳 → app.update.preserve
    "clean_profile.py",        # 兼容壳 → app.update.profile
}

FORBIDDEN_ROOT_IMPORTS = {"updater", "update_preserve", "clean_profile", "patch_drissionpage"}


def _iter_app_modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _top_level_imports(tree: ast.Module) -> set[str]:
    """收集模块顶层 ``import x`` / ``from x import y`` 的根模块名。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_app_does_not_import_root_level_modules():
    """``app`` 必须能脱离仓库根目录被导入（包边界）。"""
    offenders = []
    for path in _iter_app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        hit = _top_level_imports(tree) & FORBIDDEN_ROOT_IMPORTS
        if hit:
            offenders.append(f"{path.relative_to(ROOT)} -> {sorted(hit)}")
    assert not offenders, (
        "app/ 内出现对根目录模块的顶层导入，包边界已被破坏；"
        "请改用 app.update.* / subprocess 调用：\n" + "\n".join(offenders)
    )


def test_root_only_holds_known_modules():
    actual = {p.name for p in ROOT.glob("*.py")}
    unexpected = actual - ALLOWED_ROOT_MODULES
    assert not unexpected, (
        "根目录出现了未归位的 Python 脚本（工具请放 tools/，示例请放 examples/）：\n"
        + "\n".join(sorted(unexpected))
    )


@pytest.mark.parametrize(
    ("shim", "implementation"),
    [
        ("updater.py", "app/update/engine.py"),
        ("update_preserve.py", "app/update/preserve.py"),
        ("clean_profile.py", "app/update/profile.py"),
    ],
)
def test_compat_shims_point_at_new_implementation(shim: str, implementation: str):
    shim_path = ROOT / shim
    assert shim_path.is_file(), f"缺少兼容壳 {shim}（旧文档与外部脚本依赖它）"
    assert (ROOT / implementation).is_file(), f"缺少实现 {implementation}"
    module = implementation[:-3].replace("/", ".")
    assert module in shim_path.read_text(encoding="utf-8"), (
        f"{shim} 应转发到 {module}，否则两处实现会漂移"
    )


def test_version_has_single_source():
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    import tomllib

    pyproject_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert pyproject_version == version_file, "pyproject.toml 的 version 与 VERSION 不一致"

    import app

    assert app.__version__ == version_file, "app.__version__ 与 VERSION 不一致"


def test_no_module_package_shadowing():
    """同名模块会被同名包遮蔽（FileFinder 先命中包），属于死文件。"""
    shadows = []
    for pkg_dir in APP.rglob("*"):
        if not pkg_dir.is_dir() or pkg_dir.name == "__pycache__":
            continue
        twin = pkg_dir.with_suffix(".py")
        if twin.is_file():
            shadows.append(str(twin.relative_to(ROOT)))
    assert not shadows, "模块与同目录下的包同名，模块永远不会被导入：\n" + "\n".join(shadows)


def test_every_python_package_has_init():
    missing = []
    for directory in APP.rglob("*"):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        if any(child.suffix == ".py" for child in directory.iterdir()):
            if not (directory / "__init__.py").is_file():
                missing.append(str(directory.relative_to(ROOT)))
    assert not missing, "这些目录含 Python 文件但不是包（缺 __init__.py）：\n" + "\n".join(missing)


def test_no_duplicate_changelog_copies():
    """CHANGELOG-*.md 只能是历史版本，不能与当前 CHANGELOG.md 逐字节相同。"""
    current = (ROOT / "CHANGELOG.md").read_bytes()
    current_hash = hashlib.sha256(current).hexdigest()
    duplicates = [
        path.name
        for path in ROOT.glob("CHANGELOG-*.md")
        if hashlib.sha256(path.read_bytes()).hexdigest() == current_hash
    ]
    assert not duplicates, "以下文件与 CHANGELOG.md 内容重复，应删除：\n" + "\n".join(duplicates)


def test_package_entry_point_exists_and_is_thin():
    entry = APP / "__main__.py"
    assert entry.is_file(), "缺少 app/__main__.py，`python -m app` 不可用"
    source = entry.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "runpy" in _top_level_imports(tree)
    bootstrap_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "bootstrap_runtime")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "bootstrap_runtime")
        )
    ]
    assert not bootstrap_calls, (
        "app/__main__.py 不应自行调用 bootstrap_runtime：main.py 的 __main__ 守卫已经做过，"
        "重复调用会二次执行自动更新/补丁/浏览器拉起"
    )
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in main_source
    assert "bootstrap_runtime()" in main_source


def test_deleted_paths_are_not_referenced():
    """已删除/已迁移的路径不应继续出现在文档里。"""
    removed = ("CHANGELOG-3.0.1.md",)
    docs = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "README.en.md"]
    offenders = [
        f"{path.name}: {name}"
        for path in docs
        for name in removed
        if name in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "文档仍引用已删除的文件：\n" + "\n".join(offenders)


def test_parser_registry_points_at_existing_module():
    """解析器注册表与包导出必须指向真实存在的模块（mimo 曾有一份完全重复的副本）。"""
    import app.core.parsers as parsers

    assert parsers.MimoParser.__module__ == "app.core.parsers.mimo_runtime_parser"
    for path in APP.rglob("*.py"):
        if path.name == "mimo_parser.py":
            pytest.fail(f"重复的 MiMo 解析器副本仍然存在：{path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# C1：路径必须来自 app.paths，不能依赖当前工作目录
# ---------------------------------------------------------------------------

CWD_LITERAL_PATTERNS = (
    'Path("download_images")',
    "Path('download_images')",
    'Path("static")',
    "Path('static')",
    'directory="static"',
    'directory="download_images"',
)


def test_app_code_does_not_use_cwd_relative_literals():
    """``Path("static")`` 这类写法只在「CWD 恰好是仓库根」时正确。

    生产代码必须走 :mod:`app.paths`（绝对路径）；唯一的例外是 ``app/paths.py``
    自身（文档字符串里会举例说明旧写法）。
    """
    offenders = []
    for path in _iter_app_modules():
        if path.name == "paths.py" and path.parent == APP:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in CWD_LITERAL_PATTERNS:
            if pattern in text:
                offenders.append(f"{path.relative_to(ROOT)}: {pattern}")
    assert not offenders, (
        "以下位置仍按当前工作目录解析路径，请改用 app.paths：\n" + "\n".join(offenders)
    )


def test_main_mounts_absolute_media_and_static_dirs():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'StaticFiles(directory=str(paths.STATIC_DIR))' in source
    assert "paths.download_dir()" in source
    assert 'HardenedMediaStaticFiles(directory="download_images")' not in source


def test_runtime_bootstrap_pins_cwd_to_project_root():
    """脚本启动时把工作目录固定到项目根，避免第三方/旧代码的相对路径走偏。"""
    source = (ROOT / "app/runtime/bootstrap.py").read_text(encoding="utf-8")
    assert "def ensure_project_cwd()" in source
    assert "os.chdir(PROJECT_ROOT)" in source
    assert "ensure_project_cwd()" in source.split("def bootstrap_runtime", 1)[1]


def test_background_downloader_has_no_cwd_default():
    """后台图片下载器是进程级单例，保存目录不能固化成启动时的 CWD。"""
    source = (ROOT / "app/core/background_image_downloader.py").read_text(encoding="utf-8")
    assert 'save_dir: str | Path = "download_images"' not in source
    assert "def set_save_dir(" in source
    assert "def clear_cache(" in source


# ---------------------------------------------------------------------------
# C2：面板管理端点收敛到 app/api/admin/，且依赖方向单向
# ---------------------------------------------------------------------------

ADMIN_MODULES = ("app/api/admin/pool.py", "app/api/admin/presets.py")


def test_admin_package_exists_and_is_wired():
    import app.api.admin as admin

    assert hasattr(admin, "router")
    paths = {
        getattr(route, "path", "")
        for route in admin.router.routes
    }
    assert "/api/pool/status" in paths
    assert "/api/tab-pool/tabs" in paths
    assert "/api/presets/{domain}" in paths


def test_panel_endpoints_are_not_defined_in_protocol_modules():
    """``/api/*`` 面板端点不应再写在协议/编排路由文件里。"""
    offenders = []
    checks = {
        "app/api/chat.py": ("/api/",),
        "app/api/tab_routes.py": ("/api/tab-pool/", "/api/presets/"),
    }
    for rel, prefixes in checks.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("@router."):
                continue
            if any(f'"{prefix}' in stripped for prefix in prefixes):
                offenders.append(f"{rel}: {stripped}")
    assert not offenders, (
        "面板管理端点应放在 app/api/admin/ 下：\n" + "\n".join(offenders)
    )


def test_admin_layer_dependency_direction_is_one_way():
    """admin -> tab_routes 允许（复用未拆分的辅助），反向导入则是循环依赖。"""
    admin_sources = {
        rel: (ROOT / rel).read_text(encoding="utf-8") for rel in ADMIN_MODULES
    }
    assert any("from app.api.tab_routes import" in src for src in admin_sources.values()), (
        "admin 模块应显式复用 tab_routes 的辅助函数（若已下沉，请同步更新此测试）"
    )
    tab_source = (ROOT / "app/api/tab_routes.py").read_text(encoding="utf-8")
    assert "from app.api.admin" not in tab_source, "tab_routes 不得反向导入 admin（会形成循环依赖）"
