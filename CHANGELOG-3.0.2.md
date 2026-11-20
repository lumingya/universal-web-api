# Changelog - v3.0.2

发布日期：2026-11-20

## Fixed

- 更新 Arena.ai 新版输入框、模型选择器和 `/chat` 新建会话选择器，并保留旧选择器回退。
- 修复 DeepSeek XHR SSE `response/fragments` 累计快照解析，正确输出 RESPONSE 增量并忽略 THINK 内容。
- 修复发布索引误收录 `*.local.json` 本地覆盖文件。

## Tests

- 增加脱敏的真实 DeepSeek 流形状样本与片段类型切换回归测试。
