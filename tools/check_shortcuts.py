"""校验桌面快捷方式的属性（只读）。"""
from __future__ import annotations

import pythoncom
import win32com.client
from pathlib import Path


def main() -> int:
    pythoncom.CoInitialize()
    ws = win32com.client.Dispatch("WScript.Shell")
    desk = Path.home() / "Desktop"
    ok = True
    for p in sorted(desk.glob("*.lnk")):
        if "翻译" not in p.name:
            continue
        lnk = ws.CreateShortCut(str(p))
        print(p.name)
        print("  Target:", lnk.TargetPath or "(空)")
        print("  Args  :", lnk.Arguments or "(无)")
        print("  Work  :", lnk.WorkingDirectory or "(空)")
        print("  Icon  :", lnk.IconLocation)
        if not lnk.TargetPath:
            ok = False
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
