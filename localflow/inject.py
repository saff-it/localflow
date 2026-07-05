"""Text insertion into the frontmost app: clipboard + synthetic Cmd+V, clipboard restored after.

Requires Accessibility permission for the process running LocalFlow (System Settings →
Privacy & Security → Accessibility). If the paste keystroke is blocked, the dictated text
is still on the clipboard — nothing is lost.
"""
import subprocess
import threading
import time
from typing import Optional

_PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'
_FRONTMOST_SCRIPT = (
    'tell application "System Events" to get name of first application process whose frontmost is true'
)


def get_clipboard() -> str:
    try:  # in-process NSPasteboard: instant, no pbpaste spawn
        from AppKit import NSPasteboard, NSPasteboardTypeString

        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString) or ""
    except Exception:
        result = subprocess.run(["pbpaste"], capture_output=True)
        return result.stdout.decode("utf-8", "replace")


def set_clipboard(text: str) -> None:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        board = NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(text, NSPasteboardTypeString)
    except Exception:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"))


def frontmost_app() -> str:
    result = subprocess.run(["osascript", "-e", _FRONTMOST_SCRIPT], capture_output=True, text=True)
    return result.stdout.strip()


def _ax_systemwide():
    """Systemwide AX element with a HARD 0.25s messaging timeout: sluggish
    Electron apps used to block this for seconds, delaying the recorder past
    the key release and spawning zombie sessions."""
    import ApplicationServices as AS

    system = AS.AXUIElementCreateSystemWide()
    AS.AXUIElementSetMessagingTimeout(system, 0.25)
    return system


def focused_is_editable() -> bool:
    """True if the focused UI element looks like a text field. FAIL-OPEN: any
    doubt (apps with poor accessibility exposure) returns True — dictation must
    never be blocked by a lazy app; we only stop when it's clearly not a field."""
    try:
        import ApplicationServices as AS  # already present: pynput depends on it

        system = _ax_systemwide()
        err, focused = AS.AXUIElementCopyAttributeValue(system, AS.kAXFocusedUIElementAttribute, None)
        if err != 0 or focused is None:
            return True
        err, role = AS.AXUIElementCopyAttributeValue(focused, AS.kAXRoleAttribute, None)
        if err == 0 and role in ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"):
            return True
        err, names = AS.AXUIElementCopyAttributeNames(focused, None)
        if err == 0 and names is not None and "AXSelectedTextRange" in list(names):
            return True  # editable/selectable text views expose a selection range
        return False
    except Exception:
        return True


def _paste_keystroke_quartz() -> bool:
    """Native ⌘V via CGEvent: ~10ms vs ~250ms of an osascript spawn."""
    try:
        import Quartz  # already present: pynput depends on it

        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        for is_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, 9, is_down)  # 9 = 'v'
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True
    except Exception:
        return False


def _restore_later(previous: str) -> None:
    """Put the old clipboard back AFTER the paste has landed — in the background,
    so the done-sound isn't delayed by this bookkeeping."""

    def restore():
        time.sleep(0.5)
        set_clipboard(previous)

    threading.Thread(target=restore, daemon=True).start()


def paste_into_frontmost(text: str, restore_clipboard: bool = True) -> bool:
    previous: Optional[str] = get_clipboard() if restore_clipboard else None
    set_clipboard(text)
    if _paste_keystroke_quartz():
        if previous is not None:
            _restore_later(previous)
        return True
    result = subprocess.run(["osascript", "-e", _PASTE_SCRIPT], capture_output=True, text=True)
    if result.returncode != 0:
        # Paste blocked — almost always a missing Accessibility grant. Keep the text
        # in the clipboard (skip the restore) so the dictation isn't lost.
        print("⚠️  Incolla automatico FALLITO: manca il permesso Accessibilità per questa app.")
        print("    Il testo dettato è negli appunti — premi Cmd+V per incollarlo tu.")
        if result.stderr.strip():
            print("    Dettaglio macOS: " + result.stderr.strip().splitlines()[-1])
        return False
    if previous is not None:
        _restore_later(previous)
    return True
