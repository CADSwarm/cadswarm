// Windows shortcuts that carry an AppUserModelID, for install.ps1 (loaded with Add-Type, no compiler needed).
//
// WScript.Shell can make a .lnk but can't set System.AppUserModel.ID. Without it Windows groups the running
// CADSwarm window (which sets "CADSwarm.Desktop" on its process) separately from a pinned shortcut, and the
// taskbar shows python's icon. Also used by the tests to read a shortcut back.
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace CadSwarmSetup
{
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, int fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
        void Resolve(IntPtr hwnd, int fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey
    {
        public Guid fmtid;
        public uint pid;
    }

    // Only the VT_LPWSTR case is used. 24 bytes covers the union on both 32- and 64-bit.
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant
    {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pointer;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PropertyKey pkey);
        void GetValue(ref PropertyKey key, out PropVariant pv);
        void SetValue(ref PropertyKey key, ref PropVariant pv);
        void Commit();
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class CShellLink { }

    public static class Shortcut
    {
        const ushort VT_LPWSTR = 31;
        static PropertyKey AppUserModelId = new PropertyKey {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };

        [DllImport("ole32.dll")]
        static extern int PropVariantClear(ref PropVariant pvar);

        [DllImport("shell32.dll")]
        static extern void SHChangeNotify(int wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);

        public static void Create(string path, string target, string arguments, string workingDirectory,
                                  string iconPath, int iconIndex, string description, string appId)
        {
            var link = (IShellLinkW)new CShellLink();
            link.SetPath(target);
            link.SetArguments(arguments ?? "");
            link.SetWorkingDirectory(workingDirectory ?? "");
            if (!string.IsNullOrEmpty(iconPath)) link.SetIconLocation(iconPath, iconIndex);
            link.SetDescription(description ?? "");
            if (!string.IsNullOrEmpty(appId))
            {
                var store = (IPropertyStore)link;
                var key = AppUserModelId;
                var value = new PropVariant { vt = VT_LPWSTR, pointer = Marshal.StringToCoTaskMemUni(appId) };
                try
                {
                    store.SetValue(ref key, ref value);
                    store.Commit();
                }
                finally
                {
                    PropVariantClear(ref value);
                }
            }
            ((IPersistFile)link).Save(path, true);
            Marshal.FinalReleaseComObject(link);
        }

        // [target, arguments, working directory, "icon,index", description, AppUserModelID]
        public static string[] Read(string path)
        {
            var link = (IShellLinkW)new CShellLink();
            ((IPersistFile)link).Load(path, 0);
            var sb = new StringBuilder(1024);
            var result = new string[6];
            link.GetPath(sb, sb.Capacity, IntPtr.Zero, 0x4 /* SLGP_RAWPATH */); result[0] = sb.ToString();
            sb.Clear(); link.GetArguments(sb, sb.Capacity); result[1] = sb.ToString();
            sb.Clear(); link.GetWorkingDirectory(sb, sb.Capacity); result[2] = sb.ToString();
            int index;
            sb.Clear(); link.GetIconLocation(sb, sb.Capacity, out index); result[3] = sb.ToString() + "," + index;
            sb.Clear(); link.GetDescription(sb, sb.Capacity); result[4] = sb.ToString();
            var store = (IPropertyStore)link;
            var key = AppUserModelId;
            PropVariant value;
            store.GetValue(ref key, out value);
            result[5] = value.vt == VT_LPWSTR ? Marshal.PtrToStringUni(value.pointer) : "";
            PropVariantClear(ref value);
            Marshal.FinalReleaseComObject(link);
            return result;
        }

        // Tell Explorer that file associations changed, so "Open with" picks up CADSwarm without a sign-out.
        public static void NotifyAssociationsChanged()
        {
            SHChangeNotify(0x08000000 /* SHCNE_ASSOCCHANGED */, 0 /* SHCNF_IDLIST */, IntPtr.Zero, IntPtr.Zero);
        }
    }
}
