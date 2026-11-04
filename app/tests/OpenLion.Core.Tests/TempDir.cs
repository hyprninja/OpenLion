namespace OpenLion.Core.Tests;

sealed class TempDir : IDisposable
{
    public string Path { get; } = Directory.CreateTempSubdirectory("openlion-test").FullName;

    public string Sub(params string[] parts) => System.IO.Path.Combine([Path, .. parts]);

    public string File(string rel, string content, DateTime? mtime = null)
    {
        var p = Sub(rel.Split('/'));
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(p)!);
        System.IO.File.WriteAllText(p, content);
        if (mtime is { } t)
            System.IO.File.SetLastWriteTime(p, t);
        return p;
    }

    public void Dispose()
    {
        try { Directory.Delete(Path, true); } catch (IOException) { }
    }
}
