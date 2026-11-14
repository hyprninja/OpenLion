using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml.Linq;
using OpenLion.Core.Flow;
using OpenLion.ViewModels;

namespace OpenLion.Core.Tests;

/// <summary>The wizard's state machines (ViewModels/Flows.cs), their doc, and the view models run headless.</summary>
public partial class AppFlowTests
{
    static string RepoRoot()
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d is not null; d = d.Parent)
            if (File.Exists(Path.Combine(d.FullName, "docs", "reference", "manifest_sha256.tsv")))
                return d.FullName;
        throw new DirectoryNotFoundException("repository root not found");
    }

    public static TheoryData<string> FlowNames() => [.. Flows.All.Select(f => f.Name)];
    static IStateFlow Flow(string name) => Flows.All.Single(f => f.Name == name);

    [Theory]
    [MemberData(nameof(FlowNames))]
    public void EveryStateIsReachable(string name) => Assert.Empty(Flow(name).Unreachable);

    [Theory]
    [MemberData(nameof(FlowNames))]
    public void NoStateIsADeadEnd(string name) => Assert.Empty(Flow(name).Terminal); // the user can always go on

    // ---- the doc ----

    const string DocPath = "docs/28_app_states.md";

    static string Doc()
    {
        var sb = new StringBuilder();
        sb.Append("""
            # 28. App states

            Generated from [`app/src/OpenLion/ViewModels/Flows.cs`](../app/src/OpenLion/ViewModels/Flows.cs) by
            `AppFlowTests`: don't edit it by hand. After changing a flow, run the app tests with `OPENLION_UPDATE_DOCS=1`
            to rewrite it; without that, the tests fail while it's out of date.

            Each machine below is the table the app runs from. Every move is written to `app.log` as
            `state: <machine> <from> -> <to>`, and every step change as `state: wizard <step> -> <step>`, so a log
            reads as one timeline. A move a table doesn't list is still made (the app never stops the user over it)
            but is logged as `UNDECLARED`, which is a bug to fix here or in the step.

            Not machines, on purpose: what a download row shows (nothing yet / latest / newer online / couldn't
            check) is worked out from what's on disk and what the last check found, so it can't get out of step; and
            the engine's build stages are the ordered `STAGES` list in `openlion/make_card.py`.

            """).Append('\n');
        foreach (var f in Flows.All)
        {
            sb.Append($"## {f.Name}\n\n{f.Description}\n\n");
            if (f.Name == "activity")
                sb.Append("Logged as `osm`, `speed`, `gnaf` and `voice`, one for each step that runs it.\n\n");
            sb.Append("```mermaid\n").Append(f.ToMermaid()).Append("```\n\n");
        }
        // The raw string above takes this file's line endings, which are CRLF in a Windows checkout.
        return sb.ToString().ReplaceLineEndings("\n").TrimEnd() + "\n";
    }

    [Fact]
    public void StatesDocIsUpToDate()
    {
        var path = Path.Combine(RepoRoot(), DocPath);
        var want = Doc();
        if (Environment.GetEnvironmentVariable("OPENLION_UPDATE_DOCS") == "1")
            File.WriteAllText(path, want);
        Assert.True(File.Exists(path) && File.ReadAllText(path).ReplaceLineEndings("\n") == want,
            $"{DocPath} is out of date: run the tests with OPENLION_UPDATE_DOCS=1 to regenerate it.");
    }

    // ---- the machine itself ----

    [Fact]
    public void UndeclaredMovesAreLoggedButStillMade()
    {
        var log = new List<string>();
        var m = new StateMachine<CopyPhase>(Flows.Copy, "copy", log.Add);
        Assert.True(m.Move(CopyPhase.Copying, "start"));
        Assert.True(m.Move(CopyPhase.Verified)); // Copying -> Verified skips the check: not in the table
        Assert.False(m.Move(CopyPhase.Verified)); // already there
        Assert.Equal(CopyPhase.Verified, m.State);
        Assert.Equal(["state: copy Waiting -> Copying: start", "state: copy Copying -> Verified (UNDECLARED)"], log);
    }

    // ---- Activity: the download steps' shared lifecycle ----

    [Fact]
    public void ActivityRunsItsFlow()
    {
        var a = new Activity("test");
        var ct = a.Begin("Downloading…");
        Assert.True(a.IsBusy);
        Assert.False(ct.IsCancellationRequested);
        a.Report(42, "4 of 10 MB");
        Assert.Equal((42.0, false, "4 of 10 MB"), (a.Percent, a.IsIndeterminate, a.Detail));
        a.Report(null, "", "Checking the file…");
        Assert.True(a.IsIndeterminate);
        Assert.Equal("Checking the file…", a.Status);

        a.Cancel();
        Assert.True(ct.IsCancellationRequested);
        a.Stop("Stopped. It picks up where it left off.");
        Assert.Equal(ActivityState.Stopped, a.State);
        Assert.True(a.HasNotice);
        Assert.False(a.HasError); // stopping isn't failing
        Assert.Equal("", a.Status);

        a.Begin("Again");
        Assert.False(a.HasNotice);
        a.Fail("No space");
        Assert.Equal((ActivityState.Failed, "No space", false), (a.State, a.Error, a.IsBusy));
        a.Dismiss();
        Assert.Equal((ActivityState.Idle, ""), (a.State, a.Error));
    }

    [Fact]
    public void ActivityNeverStaysBusyAfterAnUnexpectedError()
    {
        var a = new Activity("test");
        a.Begin("Downloading…");
        a.Settle(); // a finally block after an exception nobody caught
        Assert.Equal(ActivityState.Idle, a.State);
        a.Settle(); // and does nothing otherwise
        Assert.Equal(ActivityState.Idle, a.State);
    }

    [Fact]
    public void OnlyOneCheckRunsAtATime()
    {
        var a = new Activity("test");
        a.Fail("old error");
        Assert.True(a.BeginCheck());
        Assert.False(a.HasError); // checking again clears it
        Assert.False(a.BeginCheck());
        a.EndCheck();
        Assert.True(a.BeginCheck());
    }

    // ---- the wizard, headless ----

    [Fact]
    public void WizardStartsAtWelcomeAndWaitsForTheWarning()
    {
        var vm = new MainViewModel();
        var welcome = Assert.IsType<WelcomeStep>(vm.Current);
        Assert.False(vm.NextCommand.CanExecute(null));
        Assert.False(vm.BackCommand.CanExecute(null));
        welcome.Accepted = true;
        Assert.True(vm.NextCommand.CanExecute(null));
        vm.NextCommand.Execute(null);
        Assert.IsType<FactoryCardStep>(vm.Current);
        Assert.Contains("state: wizard Welcome -> Factory card", File.ReadAllText(AppLog.FilePath));
    }

    [Fact]
    public void DownloadStepsShowTheirActivityUnderTheNamesTheWindowsBindTo()
    {
        var steps = new MainViewModel().Steps.OfType<ActivityStep>().ToList();
        Assert.Equal(["GetOsmStep", "SpeedDataStep", "HouseNumbersStep", "VoiceStep"], steps.Select(s => s.GetType().Name));
        foreach (var s in steps)
            Assert.Equal((false, true, "", false, false), (s.IsBusy, s.IsIdle, s.Error, s.HasNotice, s.IsWorking));
    }

    // ---- the Windows window's bindings ----

    // The path is the first argument unless that's a named one (StringFormat=...).
    [GeneratedRegex(@"\{Binding(?:\s+(?:Path=)?(?<path>[!A-Za-z_][\w.]*)(?![\w.]|\s*=))?(?<rest>[^}]*)\}")]
    private static partial Regex BindingRegex();

    /// <summary>WPF only finds a binding to a missing property at run time, on Windows. This finds it here:
    /// every {Binding} in MainWindow.xaml must name a property of its template's view model (or, in an item
    /// template, of some view model).</summary>
    [Fact]
    public void WindowsWindowBindsOnlyToPropertiesThatExist()
    {
        var xaml = XDocument.Load(Path.Combine(RepoRoot(), "app", "src", "OpenLion", "MainWindow.xaml"));
        var vmTypes = typeof(MainViewModel).Assembly.GetTypes()
            .Where(t => t.Namespace == "OpenLion.ViewModels" && t.IsPublic).ToDictionary(t => t.Name);
        var missing = new List<string>();
        int checkedCount = 0;

        void Walk(XElement e, Type? context)
        {
            if (e.Name.LocalName == "ControlTemplate")
                context = null; // a list item's template: bound to the item
            else if (e.Name.LocalName == "DataTemplate")
                context = e.Attribute("DataType")?.Value is { } dt && Regex.Match(dt, @"vm:(\w+)") is { Success: true } m
                    ? vmTypes[m.Groups[1].Value]
                    : null; // an item template: some view model
            foreach (var a in e.Attributes())
                foreach (Match b in BindingRegex().Matches(a.Value))
                {
                    if (!b.Groups["path"].Success || b.Groups["rest"].Value.Contains("RelativeSource") ||
                        b.Groups["rest"].Value.Contains("ElementName"))
                        continue;
                    var name = b.Groups["path"].Value.TrimStart('!').Split('.')[0];
                    checkedCount++;
                    bool found = context is not null
                        ? context.GetProperty(name, BindingFlags.Public | BindingFlags.Instance) is not null
                        : vmTypes.Values.Any(t => t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance) is not null);
                    if (!found)
                        missing.Add($"{context?.Name ?? "item"}.{name}");
                }
            foreach (var c in e.Elements())
                Walk(c, context);
        }

        Walk(xaml.Root!, typeof(MainViewModel));
        Assert.True(checkedCount > 100, $"only {checkedCount} bindings found: is the parser still right?");
        Assert.Empty(missing);
    }
}
