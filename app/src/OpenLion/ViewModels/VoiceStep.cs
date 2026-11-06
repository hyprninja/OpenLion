using System.IO;
using System.Media;
using System.Net.Http;
using System.Windows;
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
public sealed class VoiceStep : StepViewModel
{
    readonly Session _session;
    readonly VoicePackDownloader _downloader;
    readonly SoundPlayer _player = new();
    CancellationTokenSource? _cts;
    VoiceOption _choice;
    bool _busy;
    double? _percent;
    string _status = "", _detail = "", _error = "";

    public VoiceStep(Session session) : base("Voice")
    {
        _session = session;
        _downloader = new VoicePackDownloader(OsmDownloader.CreateClient(ErrorReport.AppVersion), AppPaths.VoicesFolder);
        Options = [new VoiceOption(this, null), .. VoicePacks.All.Select(p => new VoiceOption(this, p))];
        _choice = Options.FirstOrDefault(o => o.Pack?.Id == session.State.VoicePack && o.Pack is not null) ?? Options[0];
        _choice.IsSelected = true;
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && !Choice.IsReady);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        StopCommand = new RelayCommand(_player.Stop);
    }

    public IReadOnlyList<VoiceOption> Options { get; }
    public ICommand DownloadCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand StopCommand { get; }

    public VoiceOption Choice => _choice;
    public bool CustomChosen => !Choice.IsStock;
    public bool ShowDownload => !IsBusy && !Choice.IsReady;
    public string DownloadLabel => Choice.Pack is { } p ? $"Download {p.Name} ({p.Bytes / 1e6:N0} MB)" : "";
    public string LanguageNote => VoicePacks.LanguageNote;

    public bool IsBusy
    {
        get => _busy;
        private set { if (Set(ref _busy, value)) { Raise(nameof(IsIdle)); Raise(nameof(CanGoNext)); Raise(nameof(ShowDownload)); } }
    }
    public bool IsIdle => !IsBusy;
    public bool IsIndeterminate => _percent is null;
    public double Percent => _percent ?? 0;
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string Error { get => _error; private set { if (Set(ref _error, value)) Raise(nameof(HasError)); } }
    public bool HasError => Error.Length > 0;

    /// <summary>The factory voice needs nothing; a pack must be downloaded first.</summary>
    public override bool CanGoNext => !IsBusy && Choice.IsReady;
    public override bool IsWorking => IsBusy;

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
        Error = "";
        Publish();
    }

    void Publish()
    {
        _session.Voice = Choice.Pack is { } p && Choice.IsReady ? (p, _downloader.PathFor(p)) : null;
        Raise(nameof(Choice)); Raise(nameof(CustomChosen)); Raise(nameof(ShowDownload)); Raise(nameof(DownloadLabel));
        Raise(nameof(CanGoNext));
        CommandManager.InvalidateRequerySuggested();
    }

    public override void OnEnter()
    {
        foreach (var o in Options.Where(o => o.Pack is not null))
            o.IsReady = _downloader.Cached(o.Pack!) is not null;
        Publish();
    }

    public override void OnLeave()
    {
        _cts?.Cancel();
        _player.Stop();
        Publish();
    }

    internal bool CanPlay(VoiceOption o) => !o.IsStock || _session.FactoryNavigation is { } nav && Directory.Exists(nav);

    internal void Play(VoiceOption o)
    {
        Error = "";
        try
        {
            Stream? sample;
            if (o.Pack is { } p)
                sample = Application.GetResourceStream(new Uri($"pack://application:,,,/Assets/Voices/{p.Id}-sample.wav"))?.Stream;
            else
                sample = _session.FactoryNavigation is { } nav && VoiceSamples.Stock(nav) is { } wav ? new MemoryStream(wav) : null;
            if (sample is null)
            {
                Error = o.IsStock ? "The factory voice's clips aren't in your factory card copy, so there's no sample to play."
                                  : $"There's no sample of {o.Name} in this version of OpenLion.";
                return;
            }
            _player.Stop();
            _player.Stream = sample;
            _player.Play();
        }
        catch (Exception e) when (e is IOException or InvalidOperationException or UriFormatException)
        {
            AppLog.Write("voice: sample failed: " + e.Message);
            Error = "The sample couldn't be played: " + e.Message;
        }
    }

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }

    async Task DownloadAsync()
    {
        if (IsBusy || Choice.Pack is not { } pack)
            return;
        var option = Choice;
        _cts = new CancellationTokenSource();
        IsBusy = true;
        Error = "";
        Status = $"Downloading {pack.Name}…";
        Detail = "";
        SetPercent(0);
        try
        {
            var progress = new Progress<DownloadProgress>(p =>
            {
                if (p.Phase == DownloadPhase.Verifying)
                {
                    Status = "Checking the download…";
                    Detail = "";
                    SetPercent(null);
                    return;
                }
                SetPercent(p.Fraction * 100);
                Detail = $"{p.Done / 1e6:N1} of {pack.Bytes / 1e6:N1} MB";
            });
            var path = await _downloader.DownloadAsync(pack, progress, _cts.Token);
            option.IsReady = true;
            AppLog.Write($"voice: downloaded {pack.Id} to {path}");
        }
        catch (OperationCanceledException)
        {
            Error = "Stopped. Press Download to start again.";
        }
        catch (Exception e) when (e is HttpRequestException or IOException or VoicePackException or UnauthorizedAccessException)
        {
            AppLog.Write("voice: download failed: " + e);
            Error = e is HttpRequestException ? "The download failed: " + e.Message + " Press Download to try again." : e.Message;
        }
        finally
        {
            IsBusy = false;
            Status = "";
            Detail = "";
            Publish();
        }
    }
}
