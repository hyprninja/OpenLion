using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace OpenLion.Core.Drives;

/// <summary>Win32 calls for reading drive identity. Every handle is opened with access 0
/// (query only): no read or write access to the volume's data is ever requested here.</summary>
static partial class NativeMethods
{
    public const uint OPEN_EXISTING = 3;
    public const uint FILE_SHARE_READ = 1, FILE_SHARE_WRITE = 2;
    public const uint FILE_READ_ONLY_VOLUME = 0x00080000;
    public const uint IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000;
    public const uint IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400;
    public const uint IOCTL_DISK_IS_WRITABLE = 0x00070024;
    public const int ERROR_WRITE_PROTECT = 19;

    public const int BusTypeUsb = 7, BusTypeSd = 12, BusTypeMmc = 13;

    [LibraryImport("kernel32.dll", EntryPoint = "CreateFileW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    public static partial SafeFileHandle CreateFile(string name, uint access, uint share, nint security,
        uint disposition, uint flags, nint template);

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static unsafe partial bool DeviceIoControl(SafeFileHandle h, uint code, void* inBuf, int inLen,
        void* outBuf, int outLen, out int returned, nint overlapped);

    [LibraryImport("kernel32.dll", EntryPoint = "GetVolumeNameForVolumeMountPointW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static unsafe partial bool GetVolumeNameForVolumeMountPoint(string mountPoint, char* name, int len);

    [LibraryImport("kernel32.dll", EntryPoint = "GetVolumeInformationW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static unsafe partial bool GetVolumeInformation(string root, char* label, int labelLen,
        out uint serial, out uint maxComponent, out uint flags, char* fs, int fsLen);
}
