using System.IO;
using System.Net.Http;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;
using OpenLion.Core.Voices;

namespace OpenLion.ViewModels;

/// <summary>One choice on the Voice step: the factory voice (Pack null) or a voice pack.</summary>
public sealed class VoiceOption : ObservableObject
{
    readonly VoiceStep _step;
    bool _selected, _ready;

    public VoiceOption(VoiceStep step, VoicePack? pack)
    {
        _step = step;
        Pack = pack;
        PlayCommand = new RelayCommand(() => _step.Play(this), () => _step.CanPlay(this));
    }

    public VoicePack? Pack { get; }
    public bool IsStock => Pack is null;
    public string Name => Pack?.Name ?? "Factory voice";
    public string Description => Pack?.Description ??
        "The voice your unit came with. Nothing on the card's voice changes.";
    public string Credit => Pack?.Credit ?? "";
    public bool HasCredit => Credit.Length > 0;
    public bool IsExplicit => Pack?.Explicit == true;
    public ICommand PlayCommand { get; }

    public bool IsSelected
    {
        get => _selected;
        set { if (Set(ref _selected, value) && value) _step.Choose(this); }
    }

    /// <summary>On this PC and checked (always true for the factory voice).</summary>
    public bool IsReady
    {
        get => IsStock || _ready;
        set { if (Set(ref _ready, value)) Raise(nameof(StatusText)); }
    }

    public string StatusText => IsStock ? "" : IsReady ? "Downloaded and checked." :
        $"Not downloaded yet ({Pack!.Bytes / 1e6:N0} MB).";
}

/// <summary>Optional step: keep the factory voice or install a voice pack (UK English prompts).</summary>
public sealed class VoiceStep : ActivityStep
{
    readonly Session _session;
    readonly VoicePackDownloader _downloader;
    VoiceOption _choice;

    public VoiceStep(Session session) : base("Voice", "voice")
    {
        _session = session;
        _downloader = new VoicePackDownloader(OsmDownloader.CreateClient(ErrorReport.AppVersion), AppPaths.VoicesFolder);
        Options = [new VoiceOption(this, null), .. VoicePacks.All.Select(p => new VoiceOption(this, p))];
        _choice = Options.FirstOrDefault(o => o.Pack?.Id == session.State.VoicePack && o.Pack is not null) ?? Options[0];
        _choice.IsSelected = true;
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && !Choice.IsReady);
        StopCommand = new RelayCommand(Platform.StopSound);
    }

    public IReadOnlyList<VoiceOption> Options { get; }
    public ICommand DownloadCommand { get; }
    public ICommand StopCommand { get; }

    public VoiceOption Choice => _choice;
    public bool CustomChosen => !Choice.IsStock;
    public bool ShowDownload => !IsBusy && !Choice.IsReady;
    public string DownloadLabel => Choice.Pack is { } p ? $"Download {p.Name} ({p.Bytes / 1e6:N0} MB)" : "";
    public string LanguageNote => VoicePacks.LanguageNote;

    /// <summary>The factory voice needs nothing; a pack must be downloaded first.</summary>
    public override bool CanGoNext => !IsBusy && Choice.IsReady;

    protected override void OnActivityChanged() => Raise(nameof(ShowDownload));

    internal void Choose(VoiceOption o)
    {
        if (ReferenceEquals(o, _choice))
            return;
        foreach (var other in Options.Where(x => !ReferenceEquals(x, o)))
            other.IsSelected = false;
        _choice = o;
        _session.State.VoicePack = o.Pack?.Id;
        try { _session.State.Save(); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { AppLog.Write("voice: state not saved: " + e.Message); }
        AppLog.Write("voice: chose " + (o.Pack?.Id ?? "factory"));
        Activity.Dismiss();
        Publish();
    }

    void Publish()
    {
        _session.Voice = Choice.Pack is { } p && Choice.IsReady ? (p, _downloader.PathFor(p)) : null;
        Raise(nameof(Choice)); Raise(nameof(CustomChosen)); Raise(nameof(ShowDownload)); Raise(nameof(DownloadLabel));
        Raise(nameof(CanGoNext));
        Platform.RequeryCommands();
    }

    public override void OnEnter()
    {
        foreach (var o in Options.Where(o => o.Pack is not null))
            o.IsReady = _downloader.Cached(o.Pack!) is not null;
        Publish();
    }

    public override void OnLeave()
    {
        Activity.Cancel();
        Platform.StopSound();
        Publish();
    }

    internal bool CanPlay(VoiceOption o) => !o.IsStock || _session.FactoryNavigation is { } nav && Directory.Exists(nav);

    internal void Play(VoiceOption o)
    {
        Activity.Dismiss();
        try
        {
            Stream? sample;
            if (o.Pack is { } p)
                sample = Platform.OpenVoiceSample(p.Id);
            else
                sample = _session.FactoryNavigation is { } nav && VoiceSamples.Stock(nav) is { } wav ? new MemoryStream(wav) : null;
            if (sample is null)
            {
                Activity.Fail(o.IsStock ? "The factory voice's clips aren't in your factory card copy, so there's no sample to play."
                                        : $"There's no sample of {o.Name} in this version of OpenLion.");
                return;
            }
            Platform.PlaySound(sample);
        }
        catch (Exception e) when (e is IOException or InvalidOperationException or UriFormatException)
        {
            AppLog.Write("voice: sample failed: " + e.Message);
            Activity.Fail("The sample couldn't be played: " + e.Message);
        }
    }

    async Task DownloadAsync()
    {
        if (IsBusy || Choice.Pack is not { } pack)
            return;
        var option = Choice;
        var ct = Activity.Begin($"Downloading {pack.Name}…", percent: 0);
        try
        {
            var progress = new Progress<DownloadProgress>(p =>
            {
                if (p.Phase == DownloadPhase.Verifying)
                    Activity.Report(null, "", "Checking the download…");
                else
                    Activity.Report(p.Fraction * 100, $"{p.Done / 1e6:N1} of {pack.Bytes / 1e6:N1} MB");
            });
            var path = await _downloader.DownloadAsync(pack, progress, ct);
            option.IsReady = true;
            AppLog.Write($"voice: downloaded {pack.Id} to {path}");
            Activity.Finish();
        }
        catch (OperationCanceledException)
        {
            Activity.Stop("Stopped. Press Download to start again.");
        }
        catch (Exception e) when (e is HttpRequestException or IOException or VoicePackException or UnauthorizedAccessException)
        {
            AppLog.Write("voice: download failed: " + e);
            Activity.Fail(e is HttpRequestException ? "The download failed: " + e.Message + " Press Download to try again." : e.Message);
        }
        finally
        {
            Activity.Settle();
            Publish();
        }
    }
}
