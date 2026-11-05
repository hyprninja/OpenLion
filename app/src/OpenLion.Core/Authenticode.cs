using System.Runtime.InteropServices;

namespace OpenLion.Core;

public enum SignatureState { Signed, Unsigned, Invalid }

/// <summary>Authenticode state of a file, from Windows' own WinVerifyTrust. Offline only:
/// no revocation lookups, no UI. Used to log whether the user runs a signed build.</summary>
public static partial class Authenticode
{
    const uint WTD_UI_NONE = 2, WTD_REVOKE_NONE = 0, WTD_CHOICE_FILE = 1;
    const uint WTD_STATEACTION_VERIFY = 1, WTD_STATEACTION_CLOSE = 2, WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000;
    const int TRUST_E_NOSIGNATURE = unchecked((int)0x800B0100);
    const int TRUST_E_SUBJECT_FORM_UNKNOWN = unchecked((int)0x800B0003);
    const int TRUST_E_PROVIDER_UNKNOWN = unchecked((int)0x800B0001);
    static readonly Guid GenericVerifyV2 = new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");

    [StructLayout(LayoutKind.Sequential)]
    struct FileInfo
    {
        public uint cbStruct;
        public nint pcwszFilePath;
        public nint hFile;
        public nint pgKnownSubject;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct TrustData
    {
        public uint cbStruct;
        public nint pPolicyCallbackData, pSIPClientData;
        public uint dwUIChoice, fdwRevocationChecks, dwUnionChoice;
        public nint pFile;
        public uint dwStateAction;
        public nint hWVTStateData, pwszURLReference;
        public uint dwProvFlags, dwUIContext;
        public nint pSignatureSettings;
    }

    [LibraryImport("wintrust.dll")]
    private static unsafe partial int WinVerifyTrust(nint hwnd, Guid* action, TrustData* data);

    public static unsafe SignatureState Check(string path)
    {
        var namePtr = Marshal.StringToHGlobalUni(path);
        try
        {
            var file = new FileInfo { cbStruct = (uint)sizeof(FileInfo), pcwszFilePath = namePtr };
            var data = new TrustData
            {
                cbStruct = (uint)sizeof(TrustData), dwUIChoice = WTD_UI_NONE, fdwRevocationChecks = WTD_REVOKE_NONE,
                dwUnionChoice = WTD_CHOICE_FILE, pFile = (nint)(&file), dwStateAction = WTD_STATEACTION_VERIFY,
                dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL,
            };
            var action = GenericVerifyV2;
            int result = WinVerifyTrust(-1, &action, &data);
            data.dwStateAction = WTD_STATEACTION_CLOSE;
            WinVerifyTrust(-1, &action, &data);
            return result switch
            {
                0 => SignatureState.Signed,
                TRUST_E_NOSIGNATURE or TRUST_E_SUBJECT_FORM_UNKNOWN or TRUST_E_PROVIDER_UNKNOWN => SignatureState.Unsigned,
                _ => SignatureState.Invalid,
            };
        }
        finally
        {
            Marshal.FreeHGlobal(namePtr);
        }
    }
}
