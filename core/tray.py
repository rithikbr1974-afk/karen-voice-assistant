"""System tray icon: show or hide Karen's window, mute or unmute the mic, quit."""
import threading


def _icon_image(size=64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=(60, 20, 110, 255), outline=(157, 78, 255, 255), width=4)
    d.ellipse((size * 0.34, size * 0.34, size * 0.66, size * 0.66), fill=(220, 190, 255, 255))
    return img


class Tray:
    def __init__(self, show, hide, set_muted, is_muted, quit_app):
        import pystray
        self.visible = True
        self._show, self._hide = show, hide
        menu = pystray.Menu(
            pystray.MenuItem("Show / hide Karen", self.toggle, default=True),  # default = left click
            pystray.MenuItem("Mute microphone", lambda: set_muted(not is_muted()), checked=lambda _: is_muted()),
            pystray.MenuItem("Quit Karen", lambda: quit_app()),
        )
        self.icon = pystray.Icon("karen", _icon_image(), "Karen", menu)

    def toggle(self):
        (self._hide if self.visible else self._show)()
        self.visible = not self.visible

    def start(self, visible=True):
        self.visible = visible
        threading.Thread(target=self.icon.run, daemon=True).start()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
