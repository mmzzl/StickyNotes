"""系统托盘（pystray）。

为什么单独一层：pywebview **没有**托盘 API，得外接 pystray。而 pystray 在 Linux
上要一个可用的 Gtk 后台（或 AppIndicator），在无显示环境 / 缺依赖时会直接抛
异常。托盘挂不上不该让整个客户端起不来 —— 关窗收进托盘是加分项，不是核心功能。
所以这里所有失败都吞掉并返回 None，上层据此走「关窗即退出」的降级路径。

Windows 上 pystray 走 win32 后台，一般都能用。
"""
import threading

from sticky_log import get_logger

log = get_logger("tray")


def _make_icon_image():
    """画一张 64x64 的黄色便签图标。

    不用 Pillow —— 只为画一个图标就往产物里塞 18MB（PIL + pillow.libs 里的
    libavif 等）不划算。这里直接用 pystray 支持的格式：PIL 可用就用 PIL，
    否则退回 GTK 的 GdkPixbuf。两者都没有就放弃托盘（降级为关窗即退出）。
    """
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([12, 8, 52, 52], radius=8, fill=(255, 213, 79, 255))
        d.polygon([(52, 8), (42, 8), (52, 18)], fill=(224, 172, 31, 255))
        d.rounded_rectangle([18, 26, 46, 29], radius=1, fill=(160, 120, 20, 255))
        d.rounded_rectangle([18, 33, 38, 36], radius=1, fill=(160, 120, 20, 255))
        return img
    except Exception as e:  # noqa: BLE001
        log.info("PIL 不可用（%s），尝试 GTK 绘图标", e)
    return _make_icon_gtk()


def _make_icon_gtk():
    """用 GdkPixbuf 画同一张图，避免依赖 Pillow。"""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        size = 64
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
        pb.fill(0x00000000)
        def px(x, y, rgba):
            pb.fill_area(x, y, 1, 1, bytes(rgba))
        body = (0xFF, 0xD5, 0x4F, 0xFF)
        fold = (0xE0, 0xAC, 0x1F, 0xFF)
        line = (0xA0, 0x78, 0x14, 0xFF)
        for y in range(8, 52):
            for x in range(12, 52):
                if x < 16 and y < 12:
                    continue
                px(x, y, fold if (x > 42 and y < 18) else body)
        for y in range(26, 29):
            for x in range(18, 47):
                px(x, y, line)
        for y in range(33, 36):
            for x in range(18, 39):
                px(x, y, line)
        return pb
    except Exception as e:  # noqa: BLE001
        log.warning("GTK 画图标也失败：%s", e)
        return None


def create_tray(desk):
    """创建托盘并后台启动。返回 pystray.Icon 或 None（不可用）。"""
    try:
        import pystray
    except Exception as e:  # noqa: BLE001
        log.warning("pystray 不可用，跳过托盘：%s", e)
        return None

    image = _make_icon_image()
    if image is None:
        return None

    def show_desk(_icon=None, _item=None):
        _call(lambda: desk.window.show())

    def new_note(_icon=None, _item=None):
        _call(desk.new_note)

    def restore_all(_icon=None, _item=None):
        _call(desk.restore_all)

    def quit_app(_icon=None, _item=None):
        _call(desk.quit_app)

    try:
        menu = pystray.Menu(
            pystray.MenuItem("打开便签桌", show_desk, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("新建便签", new_note),
            pystray.MenuItem("恢复全部便签", restore_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        )
        icon = pystray.Icon("sticky_notes", image, "便签", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        log.info("托盘已启动")
        return icon
    except Exception as e:  # noqa: BLE001
        log.warning("托盘启动失败，关窗将直接退出：%s", e)
        return None


def _call(fn):
    """托盘回调在自己的线程里跑，失败只记日志，不牵连托盘本身。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        log.warning("托盘动作失败：%s", e)


def stop(icon):
    if icon is None:
        return
    try:
        icon.stop()
    except Exception:  # noqa: BLE001
        pass
