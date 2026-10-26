/* 便签页面逻辑。
 *
 * 与 PyQt5 版最大的区别：不再有「Python 定时 runJavaScript 问 version，变了再取
 * 全文」那套轮询桥接。这里输入一变就直接把 Markdown 推给 Python
 * （pywebview.api.save_content），Python 侧只做防抖落库。少一层跨进程轮询，
 * 历史上那几个换行/塌行 bug 的成因（两套编辑器来回同步）也就不存在了。
 */
(function () {
  var editor = document.getElementById('editor');
  var source = document.getElementById('source');
  var titleInput = document.getElementById('title');
  var statusEl = document.getElementById('status');
  var bar = document.getElementById('bar');

  var loading = false;   // 程序化写入时置位，避免把回填当成用户输入
  var dirtyTimer = null;

  /* ---- Markdown 扩展 ----
   * marked 默认 GFM 不含下标/上标/脚注。这里补三个 inline 扩展和一个块级脚注定义：
   * 渲染前清空 fnDefs，渲染后把收集到的定义拼成文末脚注区。
   * 注意 ~~删除线~~ 仍是 GFM 的 del，单波浪线 ~x~ 才是下标。 */
  var fnDefs = {};      // label -> 原文

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

  marked.use({
    breaks: true,       // 软换行保留为 <br>
    gfm: true,
    extensions: [
      {   // 上标 ^x^
        name: 'sup', level: 'inline',
        start: function (src) { return src.indexOf('^'); },
        tokenizer: function (src) {
          var m = /^\^([^\^\n]+)\^/.exec(src);
          if (m) return { type: 'sup', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
        },
        renderer: function (t) { return '<sup>' + this.parser.parseInline(t.tokens) + '</sup>'; }
      },
      {   // 脚注引用 [^label]
        name: 'footnoteRef', level: 'inline',
        start: function (src) { return src.indexOf('[^'); },
        tokenizer: function (src) {
          var m = /^\[\^([^\]\n]+)\]/.exec(src);
          if (m) return { type: 'footnoteRef', raw: m[0], label: m[1] };
        },
        renderer: function (t) {
          var l = escHtml(t.label);
          return '<sup class="fn-ref" id="fnref:' + l + '">' +
                 '<a href="#fn:' + l + '">' + l + '</a></sup>';
        }
      },
      {   // 下标 ~x~（单波浪线；~~x~~ 归 GFM 的删除线）
        name: 'sub', level: 'inline',
        start: function (src) { return src.indexOf('~'); },
        tokenizer: function (src) {
          var m = /^~([^~\n]+)~/.exec(src);
          if (m) return { type: 'sub', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) };
        },
        renderer: function (t) { return '<sub>' + this.parser.parseInline(t.tokens) + '</sub>'; }
      },
      {   // 脚注定义 [^label]: 正文（可续行）
        name: 'footnoteDef', level: 'block',
        start: function (src) { return src.match(/^\[\^[^\]\n]+\]:/m) ? src.search(/^\[\^[^\]\n]+\]:/m) : undefined; },
        tokenizer: function (src) {
          var m = /^\[\^([^\]\n]+)\]:[ \t]*([^\n]*(?:\n[ \t]+[^\n]*)*)\n?(?:\n|$)/.exec(src);
          if (m) {
            fnDefs[m[1]] = m[2].replace(/\n[ \t]+/g, ' ').trim();
            return { type: 'footnoteDef', raw: m[0], label: m[1] };
          }
        },
        renderer: function () { return ''; }
      }
    ]
  });

  /* GFM 任务列表会把 - [ ] 渲染成 disabled 的 <input type=checkbox>，
   * 在浅色便签上就是个白色小方块，视觉上像误插入的输入框。
   * 这里覆盖 listitem：把 checkbox 换成可点击的 ☐/☑ 符号 span。 */
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
      /* 代码块用 highlight.js 着色。marked 12 的 code renderer 收到 token 对象
       * {text, lang, escaped}。语言别名归一化后传给 hljs，未知语言则不着色。 */
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
          try {
            html = hljs.highlight(alias, text).value;
          } catch (e) { html = escHtml(text); }
        } else if (window.hljs) {
          try {                        // 未标注语言：交给 hljs 自动检测
            html = hljs.highlightAuto(text).value;
          } catch (e) { html = escHtml(text); }
        } else {
          html = escHtml(text);
        }
        if (lang) cls += ' language-' + escHtml(String(lang).toLowerCase());
        return '<pre><code class="' + cls + '">' + html + '</code></pre>\n';
      }
    }
  });

  function footnoteHtml() {
    var labels = Object.keys(fnDefs);
    if (!labels.length) return '';
    var lis = labels.map(function (l) {
      var back = '<a href="#fnref:' + escHtml(l) + '" class="fn-back">↩</a>';
      var body = marked.parseInline(fnDefs[l]);
      return '<li id="fn:' + escHtml(l) + '">' + body + ' ' + back + '</li>';
    }).join('');
    return '<ol class="footnotes">' + lis + '</ol>';
  }

  var turndown = new TurndownService({
    headingStyle: 'atx', hr: '---', bulletListMarker: '-',
    codeBlockStyle: 'fenced', fence: '```', emDelimiter: '*'
  });
  turndown.use(turndownPluginGfm.gfm);

  /* 排版 -> 源码 时的反向规则，保证扩展语法往返不丢。 */
  /* 任务符号 span：☑/☐ -> [x]/[ ]。GFM 插件只认 input[type=checkbox]，
   * 换成符号后要自己补这条规则，否则列表项首部的勾选态会丢。 */
  turndown.addRule('taskMark', {
    filter: function (node) {
      return node.nodeName === 'SPAN' && node.classList.contains('task');
    },
    replacement: function (content, node) {
      return node.dataset.done === '1' ? '[x] ' : '[ ] ';
    }
  });
  turndown.addRule('sup', {
    filter: 'sup',
    replacement: function (content) { return '^' + content + '^'; }
  });
  turndown.addRule('sub', {
    filter: 'sub',
    replacement: function (content) { return '~' + content + '~'; }
  });
  /* 脚注引用：<sup class="fn-ref"><a href="#fn:X">X</a></sup> -> [^X] */
  turndown.addRule('fnRef', {
    filter: function (node) {
      return node.nodeName === 'SUP' && node.classList.contains('fn-ref');
    },
    replacement: function (content, node) {
      var a = node.querySelector('a');
      var label = a ? (a.getAttribute('href') || '').replace(/^#fn:/, '') : content;
      return '[^' + label + ']';
    }
  });
  /* 文末脚注区整体丢弃（定义行由 fnDefs 缓存补回） */
  turndown.addRule('footnotes', {
    filter: function (node) {
      return node.nodeName === 'OL' && node.classList.contains('footnotes');
    },
    replacement: function () { return ''; }
  });
  /* <br>（breaks:true 产生的软换行）转回单个换行，避免排版->源码固化成硬换行 */
  turndown.addRule('softBreak', {
    filter: 'br',
    replacement: function () { return '\n'; }
  });

  function toMarkdown() {
    if (document.body.dataset.mode === 'source') return source.value;
    try {
      var md = turndown.turndown(editor.innerHTML);
      md = md.replace(/^(\s*)(\d+)\.\s{2,}/gm, '$1$2. ');
      md = md.replace(/\n{3,}/g, '\n\n');
      // 脚注定义行在渲染时被块级扩展消耗，DOM 里没有；用缓存的 fnDefs 补回
      var defs = Object.keys(fnDefs).map(function (l) {
        return '[^' + l + ']: ' + fnDefs[l];
      }).join('\n');
      if (defs) md = md.replace(/\s*$/, '') + '\n\n' + defs + '\n';
      return md;
    } catch (e) {
      return editor.textContent || '';
    }
  }

  function render(md) {
    loading = true;
    try {
      fnDefs = {};
      editor.innerHTML = marked.parse(md || '') + footnoteHtml();
      bindTaskMarks();
    } catch (e) {
      editor.textContent = md || '';
    }
    source.value = md || '';
    loading = false;
  }

  /* 点击 ☐/☑ 切换完成状态，并同步回源码文本（- [ ] <-> - [x]）。 */
  function bindTaskMarks() {
    var marks = editor.querySelectorAll('span.task');
    Array.prototype.forEach.call(marks, function (el) {
      el.addEventListener('click', function () {
        var done = el.dataset.done === '1';
        el.dataset.done = done ? '0' : '1';
        el.setAttribute('aria-checked', done ? 'false' : 'true');
        el.textContent = done ? '☐' : '☑';
        source.value = toMarkdown();
        scheduleSave();
      });
    });
  }

  /* 页面加载完成后由 Python 调用，推进初始内容与配色 */
  window.pywebviewInit = function (payload) {
    titleInput.value = payload.title || '';
    render(payload.content || '');
    applyColor(payload.color || '#fff9c4');
    setMode(payload.mode || 'rich');
    resetUndo(payload.content || '');
  };

  /* 按便签底色亮度选前景色：深底白字、浅底黑字。
   * 用感知亮度 (0.299R+0.587G+0.114B)，阈值 140 附近对比度最稳。
   * 同步把整套 --fg* 变量写到 sticker 上，CSS 里引用即可。 */
  function hexLuma(hex) {
    var h = (hex || '').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return 255;
    var r = parseInt(h.slice(0, 2), 16),
        g = parseInt(h.slice(2, 4), 16),
        b = parseInt(h.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return 255;
    return 0.299 * r + 0.587 * g + 0.114 * b;
  }

  function applyColor(color) {
    var sticker = document.getElementById('sticker');
    sticker.style.setProperty('--note-bg', color);
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';

    var dark = hexLuma(color) < 140;   // 底色偏暗 -> 用浅色前景
    var fg = dark ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.85)';
    var fgSoft = dark ? 'rgba(255,255,255,0.72)' : 'rgba(0,0,0,0.62)';
    var barBg = dark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.05)';
    var btnHoverBg = dark ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.90)';
    var btnHoverFg = dark ? '#fff' : '#000';
    var border = dark ? 'rgba(255,255,255,0.22)' : 'rgba(0,0,0,0.12)';
    var codeBg = dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.06)';

    sticker.style.setProperty('--fg', fg);
    sticker.style.setProperty('--fg-soft', fgSoft);
    sticker.style.setProperty('--bar-bg', barBg);
    sticker.style.setProperty('--btn-hover-bg', btnHoverBg);
    sticker.style.setProperty('--btn-hover-fg', btnHoverFg);
    sticker.style.setProperty('--border', border);
    sticker.style.setProperty('--code-bg', codeBg);
    sticker.dataset.tone = dark ? 'dark' : 'light';
    window.__noteColor = color;
  }
  window.pywebviewSetColor = applyColor;

  function setMode(mode) {
    document.body.dataset.mode = mode;
    var rich = mode === 'rich';
    editor.style.display = rich ? '' : 'none';
    source.style.display = rich ? 'none' : '';
    document.getElementById('mode').textContent = rich ? '源码' : '排版';
    if (!rich) source.value = toMarkdown();
  }
  window.pywebviewSetMode = setMode;

  function scheduleSave() {
    if (loading) return;
    if (dirtyTimer) clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(pushNow, 600);
  }

  function pushNow() {
    if (dirtyTimer) { clearTimeout(dirtyTimer); dirtyTimer = null; }
    if (!window.pywebview || !window.pywebview.api) return;
    window.pywebview.api.save_content(toMarkdown()).catch(function () {});
  }

  /* ---- 撤销 / 重做 ----
   * 以 Markdown 文本为快照单位，两种模式共用同一份历史，切模式后也能撤销。
   * 浏览器原生 undo 只在各自的 DOM 树内有效（contenteditable 与 textarea 互不相干），
   * 且 render() 重写 innerHTML 会清空原生栈，所以这里自己维护。 */
  var UNDO_MAX = 200;
  var undoStack = [''];
  var undoPos = 0;
  var undoBase = '';        // 栈顶对应的文本，用于判断内容是否真的变了
  var undoTimer = null;
  var restoring = false;    // 程序化回写时抑制记录

  function resetUndo(text) {
    undoStack = [text || ''];
    undoPos = 0;
    undoBase = text || '';
    if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
  }

  function recordUndo() {
    if (restoring || loading) return;
    var text = toMarkdown();
    if (text === undoBase) return;
    // 在中间位置编辑后产生了新分支，丢弃前面的重做记录
    undoStack = undoStack.slice(0, undoPos + 1);
    undoStack.push(text);
    if (undoStack.length > UNDO_MAX) undoStack.shift();
    undoPos = undoStack.length - 1;
    undoBase = text;
  }

  function scheduleUndo() {
    if (restoring || loading) return;
    if (undoTimer) clearTimeout(undoTimer);
    undoTimer = setTimeout(function () { undoTimer = null; recordUndo(); }, 500);
  }

  function applySnapshot(text) {
    restoring = true;
    try {
      var mode = document.body.dataset.mode;
      source.value = text;
      if (mode === 'rich') render(text);
      else loading = false;      // render() 里置的 loading 在源码模式不会走
      undoBase = text;
    } finally {
      restoring = false;
    }
    pushNow();
  }

  function doUndo() {
    if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
    recordUndo();                 // 先落实尚未入栈的当前输入
    if (undoPos <= 0) return false;
    undoPos--;
    applySnapshot(undoStack[undoPos]);
    return true;
  }

  function doRedo() {
    if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
    if (undoPos >= undoStack.length - 1) return false;
    undoPos++;
    applySnapshot(undoStack[undoPos]);
    return true;
  }

  editor.addEventListener('input', function () { scheduleUndo(); scheduleSave(); });
  source.addEventListener('input', function () { scheduleUndo(); scheduleSave(); });
  titleInput.addEventListener('input', function () {
    if (loading) return;
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.save_title(titleInput.value).catch(function () {});
    }
  });

  document.getElementById('mode').addEventListener('click', function () {
    var next = document.body.dataset.mode === 'rich' ? 'source' : 'rich';
    recordUndo();                    // 切换前把当前编辑内容入栈，保证历史连续
    if (next === 'source') source.value = toMarkdown();
    else render(source.value);
    setMode(next);
    pushNow();
    if (window.pywebview && window.pywebview.api) window.pywebview.api.remember_mode(next);
  });

  document.getElementById('save').addEventListener('click', function () {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.save_now(toMarkdown()).then(function () { flash('✓ 已保存'); });
    }
  });

  document.getElementById('color').addEventListener('click', function () {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.pick_color().then(function (c) {
        if (c) applyColor(c);
      });
    }
  });

  document.getElementById('del').addEventListener('click', function () {
    if (window.pywebview && window.pywebview.api) window.pywebview.api.confirm_delete();
  });

  document.getElementById('close').addEventListener('click', function () {
    if (window.pywebview && window.pywebview.api) window.pywebview.api.close_note();
  });

  function flash(text) {
    statusEl.textContent = text;
    setTimeout(function () { statusEl.textContent = ''; }, 1500);
  }
  window.pywebviewFlash = flash;

  document.addEventListener('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;
    var key = e.key.toLowerCase();
    if (mod && key === 's') {
      e.preventDefault();
      pushNow();
      flash('✓ 已保存');
      return;
    }
    if (mod && key === 'z') {
      e.preventDefault();
      if (e.shiftKey) doRedo(); else doUndo();
      return;
    }
    if (mod && key === 'y') {          // Windows 习惯的重做
      e.preventDefault();
      doRedo();
    }
  });
})();
