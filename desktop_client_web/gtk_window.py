"""GTK 原生窗口微调：给 pywebview 的贴纸窗补两样它自己给不了的东西。

为什么需要这一层：

1. 拖动只能从标题栏开始
   pywebview 的 ``easy_drag=True`` 在 GTK 下是「整窗拖动」——它的
   ``on_mouse_press`` 直接挂在 webview 上（platforms/gtk.py），正文里按下鼠标就
   开始拖窗口，文本永远选不中。带选择器的那套（``.pywebview-drag-region``）只在
   edgechromium 生效（util.py 里 ``easy_drag`` 参数写着 ``platform ==
   'edgechromium'``），Linux 用不上。所以自己挂 GTK 事件：按下点落在标题栏高度
   之内才调 ``start_system_move``，其余位置一律放行给 WebKit 处理选择。

2. 置顶保持
   pywebview 的 ``on_top`` 走 ``set_keep_above``，但便签被点一下之后部分窗口管理器
   会把层序压回去。显示和获得焦点时补一次。

这一层只在 GTK 后端下工作（Linux）。Windows/edgechromium 走 pywebview 自己的
easy_drag + drag region，不需要这些代码。
"""
from sticky_log import get_logger

log = get_logger("gtk")

# 标题栏高度（CSS 像素）：拖动只在这个区域内生效
DRAG_BAR_HEIGHT = 34


def attach(native_window, title_bar_height=DRAG_BAR_HEIGHT):
    """给原生 Gtk.Window 挂上「标题栏拖动」。

    参数 native_window 是 pywebview 每窗口的 ``events.shown`` 时能拿到的
    ``window.native``（Gtk.Window）。失败不抛异常——最坏情况是拖不动窗口，
    不应连累便签本身打不开。
    """
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except Exception:  # noqa: BLE001  # pragma: no cover - 取决于平台
        return False

    try:
        webview = native_window.get_child()
        while webview is not None and not _is_webview(webview):
            child = getattr(webview, "get_child", lambda: None)()
            if child is None:
                break
            webview = child
        if webview is None:
            log.warning("没能找到 WebView，标题栏拖动未挂上")
            return False

        state = {"dragging": False}

        def on_press(_widget, event):
            if event.button != 1 or event.type != Gdk.EventType.BUTTON_PRESS:
                return False
            if event.y > title_bar_height:
                return False          # 不在标题栏：交还给 WebKit 做文本选择
            state["dragging"] = True
            try:
                native_window.begin_move_drag(
                    event.button, int(event.x_root), int(event.y_root), event.time)
            except Exception:  # noqa: BLE001
                state["dragging"] = False
            return True

        def on_release(_widget, _event):
            state["dragging"] = False
            return False

        webview.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        webview.connect("button-press-event", on_press)
        webview.connect("button-release-event", on_release)
        log.info("已挂上标题栏拖动（高度 %dpx）", title_bar_height)
        return True
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        log.warning("挂标题栏拖动失败：%s", e)
        return False


def keep_on_top(native_window):
    """重新置顶。pywebview 的 on_top 在 focus 后会掉，显示时补一次。"""
    try:
        native_window.set_keep_above(True)
        return True
    except Exception:  # noqa: BLE001  # pragma: no cover
        return False


def move_to(native_window, x, y):
    """把窗口移到屏幕坐标 (x, y)。"""
    try:
        native_window.move(int(x), int(y))
        return True
    except Exception:  # noqa: BLE001  # pragma: no cover
        return False


def get_position(native_window):
    """返回 (x, y)；拿不到返回 None。"""
    try:
        return tuple(native_window.get_position())
    except Exception:  # noqa: BLE001  # pragma: no cover
        return None


def _is_webview(widget):
    return widget.__class__.__name__ == "WebView"
