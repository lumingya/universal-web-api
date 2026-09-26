"""R2-3：拆分后的大类由多个 mixin 组合而成，同名方法只能定义一次，否则会被 MRO 静默遮蔽。"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("module", "class_name", "min_mixins"),
    [
        ("app.core.tab_pool_parts.manager", "TabPoolManager", 9),
        ("app.services.command_engine", "CommandEngine", 9),
        ("app.services.config.engine", "ConfigEngine", 6),
    ],
)
def test_no_method_is_defined_twice_across_mixins(module, class_name, min_mixins):
    klass = getattr(importlib.import_module(module), class_name)
    owners = {}
    duplicates = []
    for base in klass.__mro__:
        if base is object:
            continue
        for name, value in vars(base).items():
            if callable(value) or isinstance(value, (staticmethod, classmethod, property)):
                if name.startswith("__") and name.endswith("__"):
                    continue
                if name in owners:
                    duplicates.append(f"{name}: {owners[name]} 与 {base.__name__}")
                else:
                    owners[name] = base.__name__
    assert not duplicates, "同名方法被多个类定义（后者被静默遮蔽）：\n" + "\n".join(duplicates)
    assert len(klass.__mro__) - 2 >= min_mixins
