/* Scoped transfers: importing never writes the server or touches another site. */
(() => {
  const copy = (x) => JSON.parse(JSON.stringify(x));
  const object = (x) => !!x && typeof x === "object" && !Array.isArray(x);
  const safeName = (x) =>
    typeof x === "string" &&
    !!x.trim() &&
    x.length <= 100 &&
    !["__proto__", "prototype", "constructor"].includes(x);
  function sources(data, fallbackDomain) {
    if (!object(data))
      throw Error("请选择站点或预设 JSON 文件，不是动作数组。");
    if (data.kind === "workflow")
      throw Error("这是工作流片段。请在「请求工作流 → 导入工作流」中使用它。");
    const result = [];
    const add = (domain, site) => {
      if (!object(site)) return;
      const presets = object(site.presets)
        ? site.presets
        : "workflow" in site || "selectors" in site
          ? { [site.preset_name || "主预设"]: site }
          : null;
      if (!presets) return;
      const clean = {};
      for (const [name, config] of Object.entries(presets)) {
        if (!safeName(name) || !object(config))
          throw Error("文件中存在无效的预设名称或配置对象。");
        if ("workflow" in config && !Array.isArray(config.workflow))
          throw Error(name + " 的 workflow 必须是数组。");
        if ("selectors" in config && !object(config.selectors))
          throw Error(name + " 的 selectors 必须是对象。");
        const value = copy(config);
        if (!object(site.presets)) {
          delete value.domain;
          delete value.preset_name;
        }
        Object.defineProperty(clean, name, {
          value,
          writable: true,
          enumerable: true,
          configurable: true,
        });
      }
      if (Object.keys(clean).length)
        result.push({
          domain: domain || fallbackDomain,
          presets: clean,
          settings: object(site.presets)
            ? Object.fromEntries(
                Object.entries(site).filter(
                  ([k]) =>
                    ![
                      "presets",
                      "default_preset",
                      "domain",
                      "preset_name",
                      "format",
                      "version",
                    ].includes(k) &&
                    !k.startsWith("_") &&
                    safeName(k),
                ),
              )
            : {},
        });
    };
    if (data.format === "universal-web-api/preset" && object(data.config))
      add(data.domain, {
        presets: { [data.preset_name || "主预设"]: data.config },
      });
    else if ("presets" in data || "workflow" in data || "selectors" in data)
      add(data.domain, data);
    else
      for (const [domain, site] of Object.entries(data))
        if (!domain.startsWith("_")) add(domain, site);
    if (!result.length)
      throw Error("没有找到可导入的站点预设。请选择站点配置或单预设文件。");
    return result;
  }
  window.PresetTransferSupport = { sources, safeName, copy };
  window.PresetTransfer = {
    name: "PresetTransfer",
    props: {
      domain: String,
      site: Object,
      preset: String,
      prepare: Function,
      disabled: Boolean,
    },
    emits: ["apply"],
    data: () => ({
      open: false,
      mode: "preset",
      fileName: "",
      sourceSites: [],
      sourceIndex: 0,
      sourcePreset: "",
      targetName: "",
      overwrite: false,
      includeSettings: false,
      busy: false,
      error: "",
      message: "",
      generation: 0,
      requestAbort: null,
    }),
    computed: {
      source() {
        return this.sourceSites[this.sourceIndex] || null;
      },
      names() {
        return Object.keys(this.source?.presets || {});
      },
      existing() {
        return this.site?.presets || { 主预设: this.site };
      },
      conflicts() {
        return this.names.filter((n) => Object.hasOwn(this.existing, n));
      },
      importCount() {
        return this.mode === "preset"
          ? this.sourcePreset
            ? 1
            : 0
          : this.names.filter(
              (n) => this.overwrite || !Object.hasOwn(this.existing, n),
            ).length;
      },
    },
    beforeUnmount() {
      this.requestAbort?.abort();
      this.generation++;
    },
    watch: {
      domain() {
        this.reset();
        this.open = false;
        this.message = "";
      },
      sourceIndex() {
        this.selectSource();
      },
      sourcePreset() {
        if (this.mode === "preset") this.suggestName();
      },
    },
    methods: {
      trapFocus(e) {
        const items = [
          ...e.currentTarget.querySelectorAll(
            "button:not(:disabled),input:not(:disabled),select:not(:disabled)",
          ),
        ].filter((n) => n.offsetParent !== null);
        if (e.shiftKey && document.activeElement === items[0]) {
          e.preventDefault();
          items.at(-1)?.focus();
        } else if (!e.shiftKey && document.activeElement === items.at(-1)) {
          e.preventDefault();
          items[0]?.focus();
        }
      },
      reset() {
        this.requestAbort?.abort();
        this.generation++;
        this.fileName = "";
        this.sourceSites = [];
        this.error = "";
        this.busy = false;
        this.sourcePreset = "";
        this.targetName = "";
        this.overwrite = false;
        this.includeSettings = false;
      },
      launch(mode) {
        this.reset();
        this.mode = mode;
        this.open = true;
        this.$refs.menu.open = false;
        this.$nextTick(() => this.$refs.choose.focus());
      },
      close() {
        this.requestAbort?.abort();
        this.generation++;
        this.open = false;
        this.busy = false;
        this.$nextTick(() => this.$refs.menu.querySelector("summary").focus());
      },
      suggestName() {
        let name = this.sourcePreset || "导入预设";
        if (Object.hasOwn(this.existing, name)) {
          const base = name.slice(0, 86) + " · 导入";
          name = base;
          let i = 2;
          while (Object.hasOwn(this.existing, name)) name = base + " " + i++;
        }
        this.targetName = name;
      },
      selectSource() {
        this.sourcePreset = this.names[0] || "";
        this.suggestName();
      },
      async read(event) {
        const file = event.target.files[0];
        event.target.value = "";
        if (!file) return;
        const generation = ++this.generation;
        this.error = "";
        try {
          if (file.size > 4 * 1024 * 1024)
            throw Error("配置文件不能超过 4 MiB。");
          const data = JSON.parse(await file.text());
          if (generation !== this.generation) return;
          this.sourceSites = sources(data, this.domain);
          this.sourceIndex = Math.max(
            0,
            this.sourceSites.findIndex((s) => s.domain === this.domain),
          );
          this.fileName = file.name;
          this.selectSource();
        } catch (e) {
          if (generation === this.generation) {
            this.sourceSites = [];
            this.fileName = "";
            this.error = e.message || "文件无法读取";
          }
        }
      },
      async validate(config) {
        const headers = { "Content-Type": "application/json" },
          token = window.getDashboardAuthToken?.();
        if (token) headers.Authorization = "Bearer " + token;
        const controller = new AbortController();
        this.requestAbort = controller;
        const timeout = setTimeout(() => controller.abort(), 30000);
        try {
          const response = await fetch("/api/workflow/validate", {
            method: "POST",
            headers,
            signal: controller.signal,
            body: JSON.stringify({ workflow: config.workflow || [] }),
          });
          const result = await response.json();
          if (!response.ok)
            throw Error(
              typeof result.detail === "string"
                ? result.detail
                : "工作流校验失败",
            );
        } catch (e) {
          if (e.name === "AbortError")
            throw Error("校验已取消或超过 30 秒，请稍后再试。");
          throw e;
        } finally {
          clearTimeout(timeout);
          if (this.requestAbort === controller) this.requestAbort = null;
        }
      },
      async apply() {
        if (!this.source || this.busy) return;
        const domain = this.domain,
          generation = this.generation;
        this.error = "";
        try {
          const updates = {};
          if (this.mode === "preset") {
            const name = this.targetName.trim();
            if (!safeName(name)) throw Error("请输入 1–100 字的有效预设名称。");
            if (
              Object.hasOwn(this.existing, name) &&
              !confirm(
                "将替换当前站点的「" +
                  name +
                  "」预设。其他预设不变，是否继续？",
              )
            )
              return;
            updates[name] = copy(this.source.presets[this.sourcePreset]);
          } else {
            for (const n of this.names)
              if (this.overwrite || !Object.hasOwn(this.existing, n))
                updates[n] = copy(this.source.presets[n]);
            if (
              this.overwrite &&
              this.conflicts.length &&
              !confirm(
                "将覆盖 " +
                  this.conflicts.length +
                  " 个同名预设，其他预设保留。继续？",
              )
            )
              return;
          }
          if (!Object.keys(updates).length)
            throw Error(
              "没有可导入的预设。同名预设默认跳过，可选择覆盖或改为导入单个预设。",
            );
          this.busy = true;
          for (const config of Object.values(updates)) {
            if (domain !== this.domain || generation !== this.generation)
              return;
            await this.validate(config);
          }
          if (domain !== this.domain || generation !== this.generation) return;
          this.prepare?.();
          this.$emit("apply", {
            domain,
            presets: updates,
            settings:
              this.mode === "site" && this.includeSettings
                ? copy(this.source.settings)
                : null,
            select: this.mode === "preset" ? this.targetName.trim() : null,
          });
          this.message =
            "已导入 " +
            Object.keys(updates).length +
            " 个预设为草稿，请点击「保存配置」。";
          this.close();
        } catch (e) {
          if (generation === this.generation) this.error = e.message;
        } finally {
          if (generation === this.generation) this.busy = false;
        }
      },
      download(scope) {
        this.prepare?.();
        const site = copy(this.site || {}),
          preset = this.preset;
        let payload, file;
        if (scope === "preset") {
          const config =
            site.presets?.[preset] || (!site.presets ? site : null);
          if (!config) {
            this.message = "当前预设不存在，请重新选择。";
            return;
          }
          payload = {
            domain: this.domain,
            default_preset: preset,
            presets: { [preset]: config },
          };
          file = this.domain + "__" + preset + "__preset.json";
        } else {
          payload = { ...site, domain: this.domain };
          file = this.domain + "__site.json";
        }
        const url = URL.createObjectURL(
            new Blob([JSON.stringify(payload, null, 2)], {
              type: "application/json",
            }),
          ),
          a = document.createElement("a");
        a.href = url;
        a.download = file.replace(/[\\/:*?"<>|]/g, "_");
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.$refs.menu.open = false;
        this.message =
          scope === "preset"
            ? "已导出当前预设「" + preset + "」，不含其他预设和站点级设置。"
            : "已导出本站点全部预设与站点级设置。";
      },
    },
    template: `<div class="preset-transfer">
      <details ref="menu" class="preset-more transfer-menu"><summary class="preset-btn" :aria-disabled="disabled" @click="disabled && $event.preventDefault()"><span v-html="$icons.arrowUpTray"></span>导入 / 导出<span v-html="$icons.chevronDown"></span></summary>
        <div class="preset-more-pop"><div class="transfer-menu-caption">当前站点 · {{ domain }}</div>
          <button type="button" @click="launch('preset')"><span v-html="$icons.documentArrowDown"></span><span>导入一个预设<small>新建一份，或明确选择替换</small></span></button>
          <button type="button" @click="launch('site')"><span v-html="$icons.folderOpen"></span><span>合并站点配置<small>从文件导入本站点的多套预设</small></span></button>
          <hr class="preset-more-sep">
          <button type="button" @click="download('preset')"><span v-html="$icons.arrowUpTray"></span><span>导出当前预设<small>仅「{{ preset }}」，方便分享与迁移</small></span></button>
          <button type="button" @click="download('site')"><span v-html="$icons.globe"></span><span>导出整个站点<small>全部预设 + 站点级设置</small></span></button>
        </div>
      </details>
      <div v-if="message" class="transfer-feedback" role="status">{{ message }}<button type="button" @click="message=''" aria-label="关闭导入导出提示">×</button></div>
      <teleport to="body"><div v-if="open" class="preset-transfer-overlay" @click.self="close()" @keydown.esc.stop="close()">
        <section class="preset-transfer-dialog" role="dialog" aria-modal="true" aria-labelledby="preset-transfer-title" @keydown.tab="trapFocus">
          <header><div><span class="transfer-kicker">导入到当前站点</span><h3 id="preset-transfer-title">{{ mode==='preset'?'导入一个预设':'合并站点配置' }}</h3><p>{{ domain }} · 不会影响其他站点</p></div><button type="button" @click="close" aria-label="关闭导入对话框">×</button></header>
          <div class="transfer-body"><button ref="choose" type="button" class="transfer-file" @click="$refs.file.click()" :disabled="busy"><span v-html="$icons.documentArrowDown"></span><strong>{{ fileName || '选择配置 JSON 文件' }}</strong><small>支持单预设、单站点和多站点文件 · 最大 4 MiB</small></button><input ref="file" type="file" accept=".json,application/json" hidden @change="read">
            <template v-if="source"><label v-if="sourceSites.length>1">从文件选择来源站点<select v-model.number="sourceIndex" :disabled="busy"><option v-for="(s,i) in sourceSites" :value="i">{{ s.domain }} · {{ Object.keys(s.presets).length }} 个预设</option></select></label><p v-if="source.domain!==domain" class="transfer-note">来源 {{ source.domain }} → 导入到 {{ domain }}。请确认定位器适用于当前站点。</p>
              <template v-if="mode==='preset'"><label>文件中的预设<select v-model="sourcePreset" :disabled="busy"><option v-for="n in names" :value="n">{{ n }}</option></select></label><label>在本站点保存为<input v-model="targetName" maxlength="100" :disabled="busy"></label><p class="transfer-note">{{ Object.hasOwn(existing,targetName.trim())?'将替换同名预设；确认时会再次提醒。':'将新建预设；现有预设和默认选择均保持不变。' }}</p></template>
              <template v-else><div class="transfer-preview"><strong>{{ names.length }} 个来源预设 · {{ conflicts.length }} 个同名</strong><div v-for="n in names"><span>{{ n }}</span><small>{{ conflicts.includes(n)?(overwrite?'将覆盖':'保留已有，跳过'):'新增' }}</small></div></div><label class="transfer-check"><input type="checkbox" v-model="overwrite" :disabled="busy">覆盖同名预设（默认保留已有）</label><label v-if="Object.keys(source.settings).length" class="transfer-check"><input type="checkbox" v-model="includeSettings" :disabled="busy">同时导入站点级设置</label><p class="transfer-note">保留其他已有预设，不改变默认预设。{{ includeSettings?'文件中的站点级字段将替换当前同名字段。':'站点级设置保持不变。' }}</p></template>
            </template><p v-if="error" class="transfer-error" role="alert">{{ error }}</p>
          </div><footer><span>先校验并载入草稿，再由你保存。</span><button type="button" @click="close">取消</button><button type="button" class="transfer-primary" @click="apply" :disabled="busy||!source||!importCount">{{ busy?'校验中…':'导入为草稿' }}</button></footer>
        </section>
      </div></teleport>
    </div>`,
  };
})();
