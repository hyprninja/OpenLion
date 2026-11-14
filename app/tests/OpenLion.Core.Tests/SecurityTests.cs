using OpenLion.Core.Reports;

namespace OpenLion.Core.Tests;

public class SecurityTests
{
    static readonly string Profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    static readonly string User = Environment.UserName;

    [Fact]
    public void SanitizeRemovesEveryProfilePathForm()
    {
        var forward = Profile.Replace('\\', '/');
        var doubled = Profile.Replace("\\", "\\\\");
        var text = $"a {Profile}\\x b {forward}/osm c {doubled}\\\\y d C:\\Users\\SIMON~1\\AppData e {Profile}";
        var clean = ErrorReport.Sanitize(text);
        Assert.DoesNotContain(Profile, clean, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(forward, clean, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("SIMON~1", clean);
        if (User.Length >= 3)
            Assert.DoesNotContain(User, clean, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void SanitizeReplacesTheUserNameAsAWholeWord()
    {
        if (User.Length < 3)
            return;
        var clean = ErrorReport.Sanitize($"owner {User}, file {User}.txt, other x{User}y");
        Assert.Contains("owner %USERNAME%,", clean);
        Assert.Contains("file %USERNAME%.txt", clean);
        Assert.Contains($"x{User}y", clean); // part of a longer word: left alone
    }

    [Fact]
    public void EnsureInsideAllowsOnlyChildren()
    {
        var root = Path.Combine(Path.GetTempPath(), "openlion-root");
        Assert.Equal(Path.Combine(root, "cards", "x"), AppPaths.EnsureInside(Path.Combine(root, "cards", "x"), root));
        Assert.Throws<InvalidOperationException>(() => AppPaths.EnsureInside(root, root));
        Assert.Throws<InvalidOperationException>(() => AppPaths.EnsureInside(Path.Combine(root, "..", "other"), root));
        Assert.Throws<InvalidOperationException>(() => AppPaths.EnsureInside(root + "-evil", root));
    }

#if WINDOWS
    [Fact]
    public void UnsignedFileIsReportedUnsigned()
    {
        var f = Path.GetTempFileName();
        try
        {
            File.WriteAllText(f, "not signed");
            Assert.NotEqual(SignatureState.Signed, Authenticode.Check(f));
        }
        finally
        {
            File.Delete(f);
        }
    }

    [Fact]
    public void MicrosoftSignedDllIsSigned() => // embedded signature (System32 files use catalogs instead)
        Assert.Equal(SignatureState.Signed, Authenticode.Check(typeof(object).Assembly.Location));
#else
    [Fact]
    public void SanitizeRemovesMacUserFolders() // any user's, on the startup disk or another volume
    {
        var clean = ErrorReport.Sanitize("a /Users/someone/Library/x b /Volumes/Data/Users/other/osm c ~/notes");
        Assert.DoesNotContain("someone", clean);
        Assert.DoesNotContain("other", clean);
        Assert.Contains("a %USERPROFILE%/Library/x", clean);
        Assert.Contains("~/notes", clean);
    }
#endif
}
