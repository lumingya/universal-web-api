# Universal Web API - 自定义工作流脚本扩展规范 (Custom Workflow Scripts)

本目录 `custom_scripts/` 用于存放用户私有的工作流自定义 JavaScript 脚本文件。该目录下的文件默认受到 `.gitignore` 规则保护，不会随 Git 提交或泄露。

---

## 1. 运行机制与执行环境

当在站点预设的工作流中配置动作 `JS_EXEC` 并指定外部脚本路径（例如 `custom_scripts/my_script.js`）时，后端执行引擎会自动加载该脚本并注入请求上下文与入参。

### 运行时入参传递
脚本在浏览器页面环境中以异步闭包形式执行，可直接访问两个入参：
- `__CONTEXT__`：当前请求上下文对象，包含：
  - `__CONTEXT__.model`：请求的目标模型名称（如 `gpt-4o`, `claude-3-5-sonnet` 等）
  - `__CONTEXT__.prompt`：构造后的文本提示词
  - `__CONTEXT__.session_id`：当前浏览器会话 ID
  - `__CONTEXT__.stream`：是否启用流式输出
  - `__CONTEXT__.model_catalog`：当前预设的模型目录过滤配置（若有）
- `__ARGS__`：在工作流步骤中为该脚本配置的参数对象（JSON 字典）或参数模板。

---

## 2. 宏变量模板插值 (Macro Templating)

在脚本代码或步骤入参配置中，支持使用 `{{context.xxx}}` 语法进行自动插值：
- `{{context.model}}`：替换为请求模型名
- `{{context.prompt}}`：替换为用户 Prompt 字符串
- `{{context.session_id}}`：替换为当前会话 ID
- `{{context.stream}}`：替换为布尔值或布尔字符串

### 示例入参配置
```json
{
  "target_endpoint": "/nextjs-api/stream/create-evaluation",
  "override_model": "{{context.model}}",
  "session_tag": "{{context.session_id}}"
}
```

---

## 3. 编写规范与 JSDoc 元数据

在脚本文件头部添加 JSDoc 注释或描述，控制台工作流面板在扫描脚本时会自动提取并展示描述信息：

```javascript
/**
 * @description 通用请求拦截与模型 Payload 重写脚本
 * @version 1.0.0
 */

(function() {
    const context = typeof __CONTEXT__ !== 'undefined' ? __CONTEXT__ : {};
    const args = typeof __ARGS__ !== 'undefined' ? __ARGS__ : {};

    console.log('[Script] 接收到上下文:', context);
    console.log('[Script] 接收到入参:', args);

    // 在此编写你的页面劫持或 DOM 操作逻辑
    // ...
})();
```

---

## 4. 示例脚本目录

参考 `custom_scripts/examples/` 目录下的示例脚本：
- `examples/arena_payload_interceptor.js`：通用的 Fetch / XHR 请求拦截与 Payload 模型动态重写示例。
