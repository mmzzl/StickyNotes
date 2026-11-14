/* 便签正文编辑器逻辑（Go 原生客户端内嵌 webview 用）。
 *
 * 是 desktop_client_web/web/note.js 的精简移植：去掉标题栏、颜色、模板、
 * 导出等原生外壳已有的部分，只保留 Markdown 排版 <-> 源码 的编辑核心。
 * 与 Go 的通信走 mygo 桥：JS 侧 window.mygo.call("Editor.Changed", md)，
 * Go 侧 Page().Eval("window.__setMarkdown(...)") 反向写入。
 */
(function () {
  var editor = document.getElementById('editor');
  var loading = false;      // 程序化写入时置位，避免把回填当成用户输入
  var dirtyTimer = null;

  /* 常见简写 -> highlight.js 的语言名 */
  var CODE_ALIAS = {
    js: 'javascript', ts: 'typescript', sh: 'bash', shell: 'bash',
    zsh: 'bash', py: 'python', rb: 'ruby', rs: 'rust', yml: 'yaml',
    'c++': 'cpp', cs: 'csharp', 'c#': 'csharp', golang: 'go',
    htm: 'xml', vue: 'xml', ps1: 'powershell', bat: 'dos'
  };

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---- Markdown 扩展：上标 ^x^、下标 ~x~（单波浪线，~~x~~ 仍是 GFM 删除线）----
     脚注暂不支持：marked 会把 [^x] 当普通文本显示，不丢内容。 */
  marked.use({
    breaks: true,       // 软换行保留为 <br>
    gfm: true,
    extensions: [
      {
        name: 'sup', level: 'inline',
        start: function (src) { return src.indexOf('^'); },
        tokenizer: function (src) {
          var m = /^\^([^\^\n]+)\^/.exec(src);
          if (m) return { type: 'sup', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
        },
        renderer: function (t) { return '<sup>' + this.parser.parseInline(t.tokens) + '</sup>'; }
      },
      {
        name: 'sub', level: 'inline',
        start: function (src) { return src.indexOf('~'); },
        tokenizer: function (src) {
          var m = /^~([^~\n]+)~/.exec(src);
          if (m) return { type: 'sub', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
        },
        renderer: function (t) { return '<sub>' + this.parser.parseInline(t.tokens) + '</sub>'; }
      }
    ]
  });

  /* GFM 任务列表默认渲染成 disabled 的 <input type=checkbox>，浅色便签上
   * 像误插入的白方块。覆盖 listitem：换成可点击的 ☐/☑ 符号。 */
  var _liRenderer = new marked.Renderer();
  marked.use({
    renderer: {
      listitem: function (text) {
        var m = /^<input (checked="" )?disabled="" type="checkbox">\s*/.exec(text);
        if (!m) return _liRenderer.listitem.call(this, text);
        var done = !!m[1];
        var body = text.slice(m[0].length);
        var mark = '<span class="task" role="checkbox" aria-checked="' +
                   (done ? 'true' : 'false') + '" data-done="' + (done ? '1' : '0') + '">' +
                   (done ? '☑' : '☐') + '</span> ';
        return '<li>' + mark + body + '</li>\n';
      },
      /* 代码块用 highlight.js 着色；hljs 未加载时先出纯文本，到位后补高亮。 */
      code: function (token) {
        var text, lang;
        if (typeof token === 'object' && token !== null) {
          text = token.text; lang = token.lang;
        } else {                       // 兼容旧式 (text, lang) 调用
          text = arguments[0]; lang = arguments[1];
        }
        var cls = 'hljs';
        var html;
        var alias = CODE_ALIAS[String(lang || '').toLowerCase()] || String(lang || '').toLowerCase();
        if (alias && window.hljs && hljs.getLanguage(alias)) {
          try { html = hljs.highlight(alias, text).value; }
          catch (e) { html = escHtml(text); }
        } else if (window.hljs) {
          try { html = hljs.highlightAuto(text).value; }
          catch (e) { html = escHtml(text); }
        } else {
          html = escHtml(text);
        }
        if (lang) cls += ' language-' + escHtml(String(lang).toLowerCase());
        return '<pre><code class="' + cls + '">' + html + '</code></pre>\n';
      }
    }
  });

  var td = new TurndownService({
    headingStyle: 'atx', hr: '---', bulletListMarker: '-',
    codeBlockStyle: 'fenced', fence: '```', emDelimiter: '*'
  });
  td.use(turndownPluginGfm.gfm);

  /* 排版 -> 源码 的反向规则，保证扩展语法往返不丢。 */
  td.addRule('taskMark', {
    filter: function (node) {
      return node.nodeName === 'SPAN' && node.classList.contains('task');
    },
    replacement: function (content, node) {
      return node.dataset.done === '1' ? '[x] ' : '[ ] ';
    }
  });
  td.addRule('sup', {
    filter: 'sup',
    replacement: function (content) { return '^' + content + '^'; }
  });
  td.addRule('sub', {
    filter: 'sub',
    replacement: function (content) { return '~' + content + '~'; }
  });
  /* <br>（breaks:true 的软换行）转回单个换行，避免排版->源码固化成硬换行 */
  td.addRule('softBreak', {
    filter: 'br',
    replacement: function () { return '\n'; }
  });

  function toMarkdown() {
    try {
      var md = td.turndown(editor.innerHTML);
      // turndown 会输出 "1.  " / "-   [ ]  " 这类多余空格，若不归一，
      // 每次 排版->源码 往返都会再加一层，正文会慢慢走样。
      md = md.replace(/^(\s*)(\d+)\.\s{2,}/gm, '$1$2. ');
      md = md.replace(/^(\s*)[-*+]\s{2,}(\[[ xX]\])\s+/gm, '$1- $2 ');
      return md.replace(/\n{3,}/g, '\n\n');
    } catch (e) {
      return editor.textContent || '';
    }
  }

  function render(md) {
    loading = true;
    try {
      editor.innerHTML = marked.parse(md || '');
      bindTaskMarks();
      syncHScroll();
    } catch (e) {
      editor.textContent = md || '';
    }
    loading = false;
  }

  /* 点击 ☐/☑ 切换完成状态并回推源码给 Go。 */
  function bindTaskMarks() {
    Array.prototype.forEach.call(editor.querySelectorAll('span.task'), function (el) {
      el.addEventListener('click', function () {
        var done = el.dataset.done === '1';
        el.dataset.done = done ? '0' : '1';
        el.setAttribute('aria-checked', done ? 'false' : 'true');
        el.textContent = done ? '☐' : '☑';
        pushNow();
      });
    });
  }

  function schedulePush() {
    if (loading) return;
    if (dirtyTimer) clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(pushNow, 400);
  }

  function pushNow() {
    if (dirtyTimer) { clearTimeout(dirtyTimer); dirtyTimer = null; }
    if (window.mygo) {
      window.mygo.call('Editor.Changed', toMarkdown()).catch(function () {});
    }
  }

  /* Go 侧写入正文（初始化 / 切回排版视图）。Loaded 只在首帧就绪时报一次，
     避免 Go 收到 Loaded 再回填时形成回环。 */
  window.__setMarkdown = function (md) {
    render(md || '');
  };

  /* 便签底色：部分环境下 webview 页面透明不生效，直接把页面刷成便签色。
     开窗时由 URL 参数 c 带入，改色时 Go 侧再调 __setColor。 */
  window.__setColor = function (c) {
    document.documentElement.style.background = c;
    document.body.style.background = c;
  };

  /* Go 侧保存/导出前同步取最新源码。 */
  window.__getMarkdown = function () { return toMarkdown(); };

  /* 输入即防抖回推；失焦（点了原生按钮栏/切了源码模式）立刻回推，
     让 Go 侧的 700ms 自动保存总能拿到最新内容。 */
  editor.addEventListener('input', schedulePush);
  window.addEventListener('blur', pushNow);

  /* 宽内容（代码块、超宽表格）的横向移动。WebKitGTK 的覆盖式滚动条
     不占位、不主动滚动就完全看不见，所以给三条路：
     拖动（主路径）、触控板横向滑动、Shift+滚轮。 */
  function hscrollable(target) {
    var el = target && target.closest ? target.closest('pre') : null;
    if (!el) return null;
    return el.scrollWidth > el.clientWidth ? el : null;
  }

  /* 只在真的横向溢出时挂 .hscroll：CSS 靠它决定要不要画滚动阴影和抓手
     光标，不加判断的话窄代码块也会显示"还能滚"的假提示。 */
  function syncHScroll() {
    Array.prototype.forEach.call(editor.querySelectorAll('pre'), function (el) {
      el.classList.toggle('hscroll', el.scrollWidth > el.clientWidth + 1);
    });
  }
  window.addEventListener('resize', syncHScroll);

  document.addEventListener('wheel', function (e) {
    var el = hscrollable(e.target);
    if (!el) return;
    var dx = e.shiftKey ? (e.deltaY || e.deltaX) : e.deltaX;
    if (!dx) return;
    e.preventDefault();
    el.scrollLeft += dx;
  }, { passive: false });

  var drag = null;
  editor.addEventListener('pointerdown', function (e) {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    var el = hscrollable(e.target);
    if (!el) return;
    drag = { el: el, id: e.pointerId, x: e.clientX, left: el.scrollLeft, moved: false };
    el.classList.add('dragging');
  });
  editor.addEventListener('pointermove', function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var dx = e.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 4) return;
    if (!drag.moved) {
      drag.moved = true;
      // mousedown 已经可能起了一段选区，拖动前先丢掉，否则松手时
      // 文字被选中、插入点也被拖乱了。
      var sel = window.getSelection && window.getSelection();
      if (sel) sel.removeAllRanges();
      try { drag.el.setPointerCapture(drag.id); } catch (err) {}
    }
    drag.el.scrollLeft = drag.left - dx;
    e.preventDefault();
  });
  function endDrag(e) {
    if (!drag) return;
    drag.el.classList.remove('dragging');
    // 拖动过就别再落插入点了（否则松手会把选区清掉）
    if (drag.moved && e) e.preventDefault();
    drag = null;
  }
  editor.addEventListener('pointerup', endDrag);
  editor.addEventListener('pointercancel', function () { drag = null; });

  /* highlight.js 全语言包较大：window.load 之后再取，不参与首屏。 */
  window.__hljsReady = function () {
    if (!window.hljs) return;
    Array.prototype.forEach.call(document.querySelectorAll('pre code'), function (el) {
      try { hljs.highlightElement(el); } catch (e) { /* 未知语言跳过 */ }
    });
    syncHScroll();
  };
  function loadHljsLater() {
    if (window.hljs || window.__hljsLoading) return;
    window.__hljsLoading = true;
    var s = document.createElement('script');
    s.src = 'highlight.pack.js';
    s.onload = function () { window.__hljsReady && window.__hljsReady(); };
    document.head.appendChild(s);
  }
  if (document.readyState === 'complete') setTimeout(loadHljsLater, 30);
  else window.addEventListener('load', function () { setTimeout(loadHljsLater, 30); });

  /* 注册给 editor.html 首屏引导：库就绪后升级排版视图，并告知 Go 可以回填了。
     哪怕 Go 的第一次写入早于本页脚本而丢失，这里的 Loaded 也会让 Go 重发。 */
  window.__editorReady = function (md) {
    render(md || '');
    if (window.mygo) window.mygo.call('Editor.Loaded').catch(function () {});
  };
  window.__editorReady(window.__pendingMD || '');
})();
