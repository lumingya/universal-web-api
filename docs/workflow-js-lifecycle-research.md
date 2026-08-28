# Workflow JavaScript Lifecycle Research

## Executive findings

Removing a `<script>` element is not a general-purpose unload mechanism. The HTML Standard defines removal steps for a script element, but those steps govern script preparation/execution state; it does not specify reversal of JavaScript side effects that already happened (global variables, monkey patches, event listeners, timers, DOM mutations, open sockets, etc.). That last point is an engineering inference from the standard's narrowly scoped removal algorithm, not a browser API guarantee. Therefore each workflow script needs an explicit disposer, or it should run in a disposable execution context such as a dedicated iframe/Worker when isolation is acceptable.

For the reported `arena_payload_interceptor.js` conflict, the main executor should treat the script as a scoped resource: acquire it only while that workflow is active, run its disposer before switching workflows, and keep a single owner/token so stale instances cannot remain active. A `resident`/常驻 mode can opt out of disposal, but should still be idempotent and reference-counted.

## Patterns from primary sources

### 1. Descriptor-aware, reversible monkey patches

[`@mswjs/interceptors` `PatchesRegistry`](https://raw.githubusercontent.com/mswjs/interceptors/main/src/utils/patches-registry.ts) stores replacements in a `Map<object, Map<PropertyKey, () => void>>`. `applyPatch()` captures the deep property descriptor, rejects duplicate replacement for the same owner/key, handles configurable versus non-configurable-but-writable properties, and returns a `restorePatch()` closure. Restoration reinstates the original descriptor (or deletes an owner-level proxy property) and is idempotent. `restoreAllPatches()` attempts every disposer and aggregates failures.

Applicable design:

- Keep one patch registry per workflow instance (or a process-wide registry keyed by owner/key).
- Capture the exact original descriptor/value before replacing `window.fetch`, `XMLHttpRequest.prototype.open/send`, or other globals.
- Refuse or safely compose duplicate patches; do not blindly restore a value that another owner installed after this workflow.
- Return an idempotent disposer and run disposers in reverse acquisition order (LIFO).

The same project exposes a lifecycle-level contract: `Interceptor.apply()` enables interception and `Interceptor.dispose()` “cleans up any side-effects ... and disables interception” ([project API docs](https://github.com/mswjs/interceptors#api)). Its browser preset combines Fetch and XHR interceptors and can be disposed as one unit ([browser preset docs](https://github.com/mswjs/interceptors#batchinterceptor)).

### 2. Abort-scoped listeners and asynchronous work

MDN documents that passing an [`AbortSignal` to `addEventListener`](https://developer.mozilla.org/en-US/docs/Web/API/EventTarget/addEventListener) removes the listener when its controller is aborted; this avoids losing the original callback/options needed by `removeEventListener`. [`AbortController.abort()`](https://developer.mozilla.org/en-US/docs/Web/API/AbortController/abort) can also abort `fetch`, response-body consumption, and streams. Use one controller per workflow activation and abort it from the workflow disposer. XHR calls that do not accept a signal should be tracked explicitly and `.abort()`ed during disposal.

### 3. Disposable execution contexts

[`Worker.terminate()`](https://developer.mozilla.org/en-US/docs/Web/API/Worker/terminate) immediately stops a dedicated Worker. This is the strongest browser primitive for stopping timers, listeners, and in-flight code owned by that worker, at the cost of message-passing and inability to directly patch page globals.

The HTML Standard says removing an iframe destroys its child navigable ([iframe removing steps](https://html.spec.whatwg.org/multipage/iframe-embed-object.html#the-iframe-element)), and explicitly notes that this happens without unload events because the content document is destroyed, not unloaded. This makes a sandboxed, same-origin iframe a viable hard boundary for scripts that do not need direct page-world access. Do not rely on `unload` for cleanup; explicitly message a graceful `dispose` first, then remove the iframe as a final containment step.

### 4. Scoped/dynamic script registration

Chrome's official [`chrome.scripting` API](https://developer.chrome.com/docs/extensions/reference/api/scripting) provides `registerContentScripts()`, `updateContentScripts()`, `getRegisteredContentScripts()`, and `unregisterContentScripts()`. Dynamic registrations have stable IDs and can be removed by ID; registrations can also set `persistAcrossSessions: false`. This is a useful model for the executor's bookkeeping: every injected workflow script gets a stable workflow ID, registration metadata, and an explicit unregister/dispose operation.

The Chrome content-script guide shows the complete register/get/update/unregister flow and notes that programmatic injection is appropriate for scripts that run only in response to events ([dynamic declarations](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts#dynamic)). This prevents a workflow-only interceptor from being declared globally.

## Recommended executor contract

Represent each script activation as a handle:

```ts
type ScriptHandle = {
  workflowId: string
  mode: 'scoped' | 'resident'
  dispose: () => void | Promise<void>
}
```

Activation should be serialized: dispose the current scoped handle, await completion, then inject/start the next workflow. The disposer should:

1. mark the handle inactive so late callbacks become no-ops;
2. abort its `AbortController` (listeners/fetch/streams);
3. abort tracked XHR/WebSocket/Worker resources;
4. restore monkey patches from the descriptor registry;
5. remove only DOM nodes tagged with the workflow's private data attribute or `Symbol` token;
6. optionally remove the script element itself (for diagnostics and to prevent pending loads), while recognizing this does not undo executed side effects;
7. aggregate/log cleanup errors without preventing remaining disposers from running.

For `arena_payload_interceptor.js`, default to `scoped` and give it a required exported/global `dispose` entry point. If the legacy script cannot supply one, run it in a disposable iframe/Worker or add a narrowly scoped compatibility shim that records every patch/listener/timer it creates. Avoid “restore by assigning a guessed original” because another workflow or the page may have legitimately changed that property.

## Source links

- MSW Interceptors README/API: <https://github.com/mswjs/interceptors#api>
- MSW descriptor-aware patch registry: <https://raw.githubusercontent.com/mswjs/interceptors/main/src/utils/patches-registry.ts>
- Chrome Scripting API: <https://developer.chrome.com/docs/extensions/reference/api/scripting>
- Chrome dynamic content scripts: <https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts#dynamic>
- WHATWG script processing/removal: <https://html.spec.whatwg.org/multipage/scripting.html>
- WHATWG iframe removal/destroyed navigable: <https://html.spec.whatwg.org/multipage/iframe-embed-object.html#the-iframe-element>
- MDN AbortSignal event listeners: <https://developer.mozilla.org/en-US/docs/Web/API/EventTarget/addEventListener>
- MDN AbortController: <https://developer.mozilla.org/en-US/docs/Web/API/AbortController/abort>
- MDN Worker termination: <https://developer.mozilla.org/en-US/docs/Web/API/Worker/terminate>
- Violentmonkey injection contexts (main/content isolation trade-off): <https://violentmonkey.github.io/posts/inject-into-context/>
