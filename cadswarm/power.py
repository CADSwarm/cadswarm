"""Keep a background CADSwarm process out of Windows' efficiency throttling.

Windows can drop a background process (a helper runner, a watcher) into an "Efficiency mode" execution
speed class. Work still happens, just slowly enough to look stuck. This opts the calling process out;
child processes the runner starts inherit the normal class.

Stdlib only: this runs in FreeCAD's Python too. No effect outside Windows.
"""
import os


def keep_running_in_background():
    """Ask Windows not to throttle this process. Returns True when the opt-out was accepted."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class ThrottlingState(ctypes.Structure):
            _fields_ = [("Version", wintypes.DWORD), ("ControlMask", wintypes.DWORD),
                        ("StateMask", wintypes.DWORD)]

        PROCESS_POWER_THROTTLING = 4
        EXECUTION_SPEED = 0x1
        state = ThrottlingState(1, EXECUTION_SPEED, 0)  # control the class, clear the bit: never throttled
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   wintypes.DWORD]
        kernel32.SetProcessInformation.restype = wintypes.BOOL
        ok = kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), PROCESS_POWER_THROTTLING,
                                            ctypes.byref(state), ctypes.sizeof(state))
        return bool(ok)
    except (OSError, AttributeError, ValueError):
        return False
