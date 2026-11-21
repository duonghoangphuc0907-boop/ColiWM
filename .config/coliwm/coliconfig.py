#!/usr/bin/env python3
"""Master-Stack Tiling Window Manager for X11 with 6 Desktops, Top Bar & Window Switcher."""

from datetime import datetime
import os
import select
import subprocess
import sys
from Xlib import X, Xatom, Xcursorfont, XK, display
from Xlib.error import XError
from Xlib.protocol import event


class TilingWM:
    def __init__(self):
        self.dpy = display.Display()
        if not self.dpy:
            raise RuntimeError("Could not open X11 display")

        self.screen = self.dpy.screen()
        self.root = self.screen.root
        self.width = self.screen.width_in_pixels
        self.height = self.screen.height_in_pixels

        # Appearance & layout geometry
        self.num_desktops: int = 6
        self.current_desktop: int = 0  # 0-indexed (0..5 -> Desktops 1..6)
        self.desktops: list[list] = [[] for _ in range(self.num_desktops)]
        self.focused_per_desktop: list[int | None] = [None] * self.num_desktops

        self.master_ratio: float = 0.55
        self.gap: int = 8
        self.border_width: int = 2
        self.bar_height: int = 26
        self.monocle: bool = False
        self._single_move_step: int = 0

        # Colors
        cmap = self.screen.default_colormap
        self.active_color = cmap.alloc_named_color("#61afef").pixel
        self.inactive_color = cmap.alloc_named_color("#3e4451").pixel
        self.bar_bg = cmap.alloc_named_color("#1e2127").pixel
        self.bar_fg = cmap.alloc_named_color("#abb2bf").pixel
        self.bar_dim = cmap.alloc_named_color("#5c6370").pixel
        self.bar_accent_bg = cmap.alloc_named_color("#61afef").pixel
        self.bar_accent_fg = cmap.alloc_named_color("#1e2127").pixel

        # Modifier key (Mod1 = Alt; change to X.Mod4Mask for Super/Win)
        self.mod = X.Mod1Mask

        # Atoms for window titles and polite close
        self.atom_wm_delete = self.dpy.intern_atom("WM_DELETE_WINDOW")
        self.atom_wm_protocols = self.dpy.intern_atom("WM_PROTOCOLS")
        self.atom_net_wm_name = self.dpy.intern_atom("_NET_WM_NAME")
        self.atom_utf8_string = self.dpy.intern_atom("UTF8_STRING")

        self._become_window_manager()
        self._set_root_cursor()
        self._create_bar()
        self._grab_keys()
        self._scan_existing_windows()
        self.tile()
        self.draw_bar()

    # --- Setup ---

    def _become_window_manager(self):
        mask = (
            X.SubstructureRedirectMask
            | X.SubstructureNotifyMask
            | X.StructureNotifyMask
        )
        try:
            self.root.change_attributes(event_mask=mask)
            self.dpy.sync()
        except XError as exc:
            print(
                f"Another window manager is already running on this display: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    def _set_root_cursor(self):
        """Ensure a visible default pointer cursor is set on the root window."""
        try:
            cursor_font = self.dpy.open_font("cursor")
            cursor = cursor_font.create_glyph_cursor(
                cursor_font,
                Xcursorfont.left_ptr,
                Xcursorfont.left_ptr + 1,
                (0xFFFF, 0xFFFF, 0xFFFF),
                (0x0000, 0x0000, 0x0000),
            )
            self.root.change_attributes(cursor=cursor)
            self.dpy.flush()
        except XError:
            pass

    def _create_bar(self):
        """Create an override_redirect top status bar window."""
        self.bar = self.root.create_window(
            0,
            0,
            self.width,
            self.bar_height,
            0,
            self.screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=self.bar_bg,
            override_redirect=True,
            event_mask=X.ExposureMask | X.ButtonPressMask,
        )
        font = self.dpy.open_font("fixed")
        self.gc_text = self.bar.create_gc(
            foreground=self.bar_fg, background=self.bar_bg, font=font
        )
        self.gc_dim = self.bar.create_gc(
            foreground=self.bar_dim, background=self.bar_bg, font=font
        )
        self.gc_accent_bg = self.bar.create_gc(
            foreground=self.bar_accent_bg, background=self.bar_bg
        )
        self.gc_accent_fg = self.bar.create_gc(
            foreground=self.bar_accent_fg, background=self.bar_accent_bg, font=font
        )
        self.gc_clear = self.bar.create_gc(
            foreground=self.bar_bg, background=self.bar_bg
        )
        self.bar.map()

    def _grab_key_combo(self, keysym: int, modifiers: int):
        keycode = self.dpy.keysym_to_keycode(keysym)
        if keycode:
            for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                self.root.grab_key(
                    keycode,
                    modifiers | extra,
                    True,
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                )

    def _grab_keys(self):
        bindings = [
            (XK.XK_Return, self.mod, self.spawn_terminal),
            (XK.XK_d, self.mod, self.spawn_launcher),
            (XK.XK_j, self.mod, lambda: self.cycle_focus(1)),
            (XK.XK_k, self.mod, lambda: self.cycle_focus(-1)),
            (XK.XK_Tab, self.mod, lambda: self.cycle_focus(1)),
            (XK.XK_Tab, self.mod | X.ShiftMask, lambda: self.cycle_focus(-1)),
            (XK.XK_w, self.mod, self.window_switcher_menu),
            (XK.XK_a, self.mod, self.move_window),
            (XK.XK_h, self.mod, lambda: self.adjust_master(-0.05)),
            (XK.XK_l, self.mod, lambda: self.adjust_master(0.05)),
            (XK.XK_c, self.mod, self.close_focused),
            (XK.XK_m, self.mod, self.toggle_monocle),
            (XK.XK_q, self.mod | X.ShiftMask, self.quit),
        ]

        number_keys = [XK.XK_1, XK.XK_2, XK.XK_3, XK.XK_4, XK.XK_5, XK.XK_6]
        for idx, keysym in enumerate(number_keys):
            bindings.append(
                (keysym, self.mod, lambda d=idx: self.switch_desktop(d))
            )
            bindings.append(
                (
                    keysym,
                    self.mod | X.ShiftMask,
                    lambda d=idx: self.move_focused_to_desktop(d),
                )
            )

        self._key_actions = {}
        for keysym, mods, handler in bindings:
            keycode = self.dpy.keysym_to_keycode(keysym)
            self._grab_key_combo(keysym, mods)
            self._key_actions[(keycode, mods)] = handler

    # --- Helpers ---

    @property
    def clients(self) -> list:
        return self.desktops[self.current_desktop]

    @clients.setter
    def clients(self, value: list):
        self.desktops[self.current_desktop] = value

    @property
    def focused(self) -> int | None:
        return self.focused_per_desktop[self.current_desktop]

    @focused.setter
    def focused(self, value: int | None):
        self.focused_per_desktop[self.current_desktop] = value

    def _find_window_desktop(self, win_id: int) -> int | None:
        for d_idx, win_list in enumerate(self.desktops):
            if any(c.id == win_id for c in win_list):
                return d_idx
        return None

    def _get_window_title(self, win) -> str:
        try:
            prop = win.get_full_property(self.atom_net_wm_name, self.atom_utf8_string)
            if prop and prop.value:
                val = prop.value
                return (
                    val.decode("utf-8", errors="replace")
                    if isinstance(val, bytes)
                    else str(val)
                )
            prop = win.get_full_property(Xatom.WM_NAME, X.AnyPropertyType)
            if prop and prop.value:
                val = prop.value
                return (
                    val.decode("latin-1", errors="replace")
                    if isinstance(val, bytes)
                    else str(val)
                )
        except XError:
            pass
        return "untitled"

    def _is_manageable(self, win) -> bool:
        if win.id == self.bar.id:
            return False
        try:
            attrs = win.get_attributes()
            if attrs.override_redirect:
                return False
            geom = win.get_geometry()
            if geom.width <= 1 or geom.height <= 1:
                return False
            return True
        except XError:
            return False

    def _scan_existing_windows(self):
        tree = self.root.query_tree()
        for win in tree.children:
            if self._is_manageable(win):
                self.manage_window(win)

    # --- Window Management ---

    def manage_window(self, win):
        if self._find_window_desktop(win.id) is not None:
            return
        try:
            win.change_attributes(
                event_mask=(
                    X.StructureNotifyMask
                    | X.EnterWindowMask
                    | X.PropertyChangeMask
                )
            )
            win.configure(border_width=self.border_width)
            self.clients.append(win)
            self.focused = win.id
            win.map()
            self.tile()
            self.draw_bar()
        except XError:
            pass

    def unmanage_window(self, win_id: int):
        d_idx = self._find_window_desktop(win_id)
        if d_idx is None:
            return
        self.desktops[d_idx] = [c for c in self.desktops[d_idx] if c.id != win_id]
        if self.focused_per_desktop[d_idx] == win_id:
            self.focused_per_desktop[d_idx] = (
                self.desktops[d_idx][-1].id if self.desktops[d_idx] else None
            )
        if d_idx == self.current_desktop:
            self.tile()
        self.draw_bar()

    def _focus_window(self, win):
        self.focused = win.id
        for c in self.clients:
            try:
                color = self.active_color if c.id == self.focused else self.inactive_color
                c.change_attributes(border_pixel=color)
            except XError:
                pass
        try:
            win.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
            win.raise_window()
        except XError:
            pass
        self.draw_bar()
        self.dpy.flush()

    # --- Desktops & Bar ---

    def switch_desktop(self, target: int):
        if target < 0 or target >= self.num_desktops or target == self.current_desktop:
            return

        for c in self.clients:
            try:
                c.unmap()
            except XError:
                pass

        self.current_desktop = target
        self.tile()
        self.draw_bar()

    def move_focused_to_desktop(self, target: int):
        if (
            target < 0
            or target >= self.num_desktops
            or target == self.current_desktop
            or self.focused is None
        ):
            return

        win = next((c for c in self.clients if c.id == self.focused), None)
        if not win:
            return

        self.clients = [c for c in self.clients if c.id != win.id]
        self.focused = self.clients[-1].id if self.clients else None

        try:
            win.unmap()
        except XError:
            pass

        self.desktops[target].append(win)
        self.focused_per_desktop[target] = win.id

        self.tile()
        self.draw_bar()

    def draw_bar(self):
        try:
            self.bar.fill_rectangle(self.gc_clear, 0, 0, self.width, self.bar_height)
            self.bar.raise_window()

            box_w = 28
            for i in range(self.num_desktops):
                x = i * box_w
                label = str(i + 1)
                if i == self.current_desktop:
                    self.bar.fill_rectangle(
                        self.gc_accent_bg, x, 0, box_w, self.bar_height
                    )
                    self.bar.draw_text(self.gc_accent_fg, x + 11, 17, label)
                elif self.desktops[i]:
                    self.bar.draw_text(self.gc_text, x + 11, 17, label)
                else:
                    self.bar.draw_text(self.gc_dim, x + 11, 17, label)

            cursor_x = self.num_desktops * box_w + 12
            mode_str = "[MONOCLE]" if self.monocle else "[TILE]"
            self.bar.draw_text(self.gc_text, cursor_x, 17, mode_str)
            cursor_x += len(mode_str) * 6 + 14

            title = ""
            if self.focused is not None:
                win = next((c for c in self.clients if c.id == self.focused), None)
                if win:
                    title = self._get_window_title(win)
            if title:
                max_chars = max(10, (self.width - cursor_x - 140) // 6)
                if len(title) > max_chars:
                    title = title[: max_chars - 3] + "..."
                self.bar.draw_text(self.gc_text, cursor_x, 17, title)

            clock_str = datetime.now().strftime("%a %H:%M:%S")
            clock_x = max(cursor_x + 10, self.width - len(clock_str) * 6 - 12)
            self.bar.draw_text(self.gc_text, clock_x, 17, clock_str)

            self.dpy.flush()
        except XError:
            pass

    def _handle_bar_click(self, x: int):
        box_w = 28
        clicked_desktop = x // box_w
        if 0 <= clicked_desktop < self.num_desktops:
            self.switch_desktop(clicked_desktop)

    # --- Tiling Layout ---

    def tile(self):
        n = len(self.clients)
        top_offset = self.bar_height
        usable_total_h = self.height - top_offset

        if n == 0:
            self.dpy.flush()
            return

        if self.monocle:
            active = next(
                (c for c in self.clients if c.id == self.focused), self.clients[0]
            )
            for c in self.clients:
                try:
                    if c.id == active.id:
                        c.configure(
                            x=self.gap,
                            y=top_offset + self.gap,
                            width=max(
                                100, self.width - 2 * self.gap - 2 * self.border_width
                            ),
                            height=max(
                                100,
                                usable_total_h - 2 * self.gap - 2 * self.border_width,
                            ),
                            border_width=self.border_width,
                        )
                        c.map()
                    else:
                        c.unmap()
                except XError:
                    pass
            self._focus_window(active)
            return

        for c in self.clients:
            try:
                c.map()
            except XError:
                pass

        if n == 1:
            self._place(
                self.clients[0],
                x=self.gap,
                y=top_offset + self.gap,
                w=self.width - 2 * self.gap,
                h=usable_total_h - 2 * self.gap,
            )
        else:
            usable_w = self.width - 3 * self.gap
            master_w = int(usable_w * self.master_ratio)
            stack_w = usable_w - master_w

            self._place(
                self.clients[0],
                x=self.gap,
                y=top_offset + self.gap,
                w=master_w,
                h=usable_total_h - 2 * self.gap,
            )

            stack_clients = self.clients[1:]
            k = len(stack_clients)
            total_stack_h = usable_total_h - 2 * self.gap - self.gap * (k - 1)
            each_h = max(60, total_stack_h // k)
            stack_x = 2 * self.gap + master_w

            for idx, client in enumerate(stack_clients):
                y = top_offset + self.gap + idx * (each_h + self.gap)
                h = self.height - self.gap - y if idx == k - 1 else each_h
                self._place(client, x=stack_x, y=y, w=stack_w, h=h)

        focused_client = next(
            (c for c in self.clients if c.id == self.focused), self.clients[0]
        )
        self._focus_window(focused_client)

    def _place(self, win, x: int, y: int, w: int, h: int):
        inner_w = max(50, w - 2 * self.border_width)
        inner_h = max(50, h - 2 * self.border_width)
        try:
            win.configure(
                x=x,
                y=y,
                width=inner_w,
                height=inner_h,
                border_width=self.border_width,
            )
        except XError:
            pass

    # --- Actions & Window Switcher ---

    def spawn_terminal(self):
        subprocess.Popen(["xterm"], env=os.environ.copy())

    def spawn_launcher(self):
        subprocess.Popen(["dmenu_run"], env=os.environ.copy())

    def cycle_focus(self, delta: int):
        if not self.clients:
            return
        idx = next(
            (i for i, c in enumerate(self.clients) if c.id == self.focused), 0
        )
        next_client = self.clients[(idx + delta) % len(self.clients)]
        if self.monocle:
            self.focused = next_client.id
            self.tile()
        else:
            self._focus_window(next_client)

    def window_switcher_menu(self):
        """Interactive window switcher across all 6 desktops using dmenu."""
        entries = []
        mapping = {}
        for d_idx, win_list in enumerate(self.desktops):
            for win in win_list:
                title = self._get_window_title(win)
                label = f"[{d_idx + 1}] {title} (0x{win.id:x})"
                entries.append(label)
                mapping[label] = (d_idx, win)

        if not entries:
            return

        try:
            proc = subprocess.Popen(
                ["dmenu", "-i", "-l", "10", "-p", "Switch window:"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
            )
            choice, _ = proc.communicate("\n".join(entries), timeout=30)
            choice = choice.strip()
            if choice in mapping:
                target_d, target_win = mapping[choice]
                if target_d != self.current_desktop:
                    self.switch_desktop(target_d)
                self.focused = target_win.id
                self.tile()
        except Exception:
            pass

    def move_window(self):
        """Move focused window: cycle single-window position, or reorder when >= 2 windows."""
        if not self.clients or self.focused is None:
            return

        idx = next(
            (i for i, c in enumerate(self.clients) if c.id == self.focused), None
        )
        if idx is None:
            return

        win = self.clients[idx]

        # Single window on current desktop: cycle position/geometry
        if len(self.clients) == 1:
            top_offset = self.bar_height
            usable_h = self.height - top_offset
            usable_w = self.width

            step = self._single_move_step % 4
            self._single_move_step += 1

            if step == 0:
                self._place(
                    win,
                    x=self.gap,
                    y=top_offset + self.gap,
                    w=usable_w - 2 * self.gap,
                    h=usable_h - 2 * self.gap,
                )
            elif step == 1:
                self._place(
                    win,
                    x=self.gap,
                    y=top_offset + self.gap,
                    w=(usable_w - 3 * self.gap) // 2,
                    h=usable_h - 2 * self.gap,
                )
            elif step == 2:
                half_w = (usable_w - 3 * self.gap) // 2
                self._place(
                    win,
                    x=2 * self.gap + half_w,
                    y=top_offset + self.gap,
                    w=half_w,
                    h=usable_h - 2 * self.gap,
                )
            else:
                box_w = max(200, int(usable_w * 0.7))
                box_h = max(150, int(usable_h * 0.7))
                self._place(
                    win,
                    x=self.gap * 2,
                    y=top_offset + self.gap * 2,
                    w=min(box_w, usable_w - 4 * self.gap),
                    h=min(box_h, usable_h - 4 * self.gap),
                )

            self._focus_window(win)
            return

        # Multiple windows: promote from stack to master, or swap master with first stack client
        if idx > 0:
            moved = self.clients.pop(idx)
            self.clients.insert(0, moved)
        else:
            self.clients[0], self.clients[1] = self.clients[1], self.clients[0]

        self.tile()

    def adjust_master(self, delta: float):
        self.master_ratio = min(0.8, max(0.2, self.master_ratio + delta))
        self.tile()

    def promote_to_master(self):
        if len(self.clients) < 2 or self.focused is None:
            return
        idx = next(
            (i for i, c in enumerate(self.clients) if c.id == self.focused), None
        )
        if idx is not None and idx > 0:
            win = self.clients.pop(idx)
            self.clients.insert(0, win)
            self.tile()

    def close_focused(self):
        if self.focused is None:
            return
        win = next((c for c in self.clients if c.id == self.focused), None)
        if not win:
            return
        try:
            protocols = win.get_full_property(
                self.atom_wm_protocols, X.AnyPropertyType
            )
            if protocols and self.atom_wm_delete in protocols.value:
                cm_event = event.ClientMessage(
                    window=win,
                    client_type=self.atom_wm_protocols,
                    data=(32, [self.atom_wm_delete, X.CurrentTime, 0, 0, 0]),
                )
                win.send_event(cm_event)
                self.dpy.flush()
                return
            win.destroy()
            self.dpy.flush()
        except XError:
            pass

    def toggle_monocle(self):
        self.monocle = not self.monocle
        self.tile()

    def quit(self):
        self.running = False

    # --- Event Loop ---

    def run(self):
        self.running = True
        sock = self.dpy.fileno()
        try:
            while self.running:
                if self.dpy.pending_events() == 0:
                    readable, _, _ = select.select([sock], [], [], 1.0)
                    if not readable:
                        self.draw_bar()
                        continue

                while self.dpy.pending_events() > 0:
                    ev = self.dpy.next_event()

                    if ev.type == X.Expose and ev.window.id == self.bar.id:
                        self.draw_bar()

                    elif ev.type == X.ButtonPress and ev.window.id == self.bar.id:
                        self._handle_bar_click(ev.event_x)

                    elif ev.type == X.MapRequest:
                        if self._is_manageable(ev.window):
                            self.manage_window(ev.window)
                        else:
                            ev.window.map()

                    elif ev.type == X.ConfigureRequest:
                        try:
                            ev.window.configure(
                                x=ev.x,
                                y=ev.y,
                                width=ev.width,
                                height=ev.height,
                                border_width=ev.border_width,
                            )
                        except XError:
                            pass
                        if (
                            self._find_window_desktop(ev.window.id)
                            == self.current_desktop
                        ):
                            self.tile()

                    elif ev.type == X.DestroyNotify:
                        if ev.window.id != self.root.id:
                            self.unmanage_window(ev.window.id)

                    elif ev.type == X.UnmapNotify:
                        if (
                            ev.window.id != self.root.id
                            and not getattr(ev, "send_event", False)
                            and any(c.id == ev.window.id for c in self.clients)
                            and not self.monocle
                        ):
                            self.unmanage_window(ev.window.id)

                    elif ev.type == X.PropertyNotify:
                        if ev.atom in (Xatom.WM_NAME, self.atom_net_wm_name):
                            self.draw_bar()

                    elif ev.type == X.EnterNotify:
                        if any(c.id == ev.window.id for c in self.clients):
                            if ev.window.id != self.focused:
                                self._focus_window(ev.window)

                    elif ev.type == X.KeyPress:
                        clean_state = ev.state & ~(X.LockMask | X.Mod2Mask)
                        handler = self._key_actions.get((ev.detail, clean_state))
                        if handler:
                            handler()
        except KeyboardInterrupt:
            pass
        finally:
            self.dpy.close()


if __name__ == "__main__":
    TilingWM().run()
