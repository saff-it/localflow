"""Text insertion into the frontmost app: clipboard + synthetic Cmd+V, clipboard restored after.

Requires Accessibility permission for the process running LocalFlow (System Settings →
Privacy & Security → Accessibility). If the paste keystroke is blocked, the dictated text
is still on the clipboard — nothing is lost.
"""
import subprocess
import time
from typing import Optional

_PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'
_FRONTMOST_SCRIPT = (
    'tell application "System Events" to get name of first application process whose frontmost is true'
)


def get_clipboard() -> str:
    result = subprocess.run(["pbpaste"], capture_output=True)
    return result.stdout.decode("utf-8", "replace")


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))


def frontmost_app() -> str:
    result = subprocess.run(["osascript", "-e", _FRONTMOST_SCRIPT], capture_output=True, text=True)
    return result.stdout.strip()


def paste_into_frontmost(text: str, restore_clipboard: bool = True) -> bool:
    previous: Optional[str] = get_clipboard() if restore_clipboard else None
    set_clipboard(text)
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
        time.sleep(0.5)  # let the paste land before the old clipboard comes back
        set_clipboard(previous)
    return True
