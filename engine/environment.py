"""Startup checks, so a missing prerequisite is reported before a run, not during it.

Detection is read-only: the Word check resolves a registry ProgID and never starts
an Office process. A passing check means the pieces are installed, not that a
particular document will convert cleanly.
"""
import sys

WORD_PROGID = 'Word.Application'
AUTO = object()  # distinct from None, which means "checked and not present"


def registered_progid(progid=WORD_PROGID):
    """Resolve a COM ProgID from the registry without launching the application."""
    if sys.platform != 'win32':
        return None
    import winreg
    for suffix in ('CurVer', 'CLSID'):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + '\\' + suffix) as key:
                value = winreg.QueryValueEx(key, '')[0]
        except OSError:
            continue
        if suffix == 'CurVer' and isinstance(value, str) and value:
            return value
        if suffix == 'CLSID':
            return progid
    return None


def pywin32_available():
    try:
        import win32com.client  # noqa: F401
    except Exception:
        return False
    return True


def environment_report(platform=AUTO, progid=AUTO, pywin32=AUTO):
    """One entry per prerequisite: what was found, and what to do when it is missing."""
    platform = sys.platform if platform is AUTO else platform
    checks = []

    windows = platform == 'win32'
    checks.append({
        'name': 'Windows',
        'ok': windows,
        'detail': platform,
        'fix': '本工具通过 COM 驱动桌面版 Microsoft Word 写入修订，只能在 Windows 上运行。',
    })

    if windows:
        available = pywin32_available() if pywin32 is AUTO else pywin32
        checks.append({
            'name': 'pywin32',
            'ok': available,
            'detail': '已安装' if available else '未安装',
            'fix': '在项目虚拟环境中运行 pip install -r requirements.txt。',
        })
        found = registered_progid() if progid is AUTO else progid
        checks.append({
            'name': 'Microsoft Word',
            'ok': bool(found),
            'detail': found or '未在注册表中找到 Word.Application',
            'fix': '需要本机安装桌面版 Microsoft Word（Office 365 或 2016 以上）。'
                   '网页版 Word、WPS 与 LibreOffice 无法提供所需的修订接口。',
        })

    return checks


def blocking_problems(checks):
    return [check for check in checks if not check['ok']]


def word_available():
    """True when desktop Word is registered on this machine."""
    return sys.platform == 'win32' and pywin32_available() and bool(registered_progid())
