namespace OpenLion.Core;

/// <summary>Plain-text log in the working folder (app.log), kept small; included in error reports.</summary>
public static class AppLog
{
    static readonly object Gate = new();
    const long MaxBytes = 1 << 20;

    public static string FilePath => Path.Combine(AppPaths.Root, "app.log");

    public static void Write(string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(AppPaths.Root);
                var f = new FileInfo(FilePath);
                if (f.Exists && f.Length > MaxBytes)
                    File.Move(FilePath, FilePath + ".old", overwrite: true);
                File.AppendAllText(FilePath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            // logging must never take the app down
        }
    }
}
