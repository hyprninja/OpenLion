using System.Windows.Input;
using OpenLion.Core.Flow;

namespace OpenLion.ViewModels;

/// <summary>States of a download step's foreground job (see <see cref="Flows.Activity"/>).</summary>
public enum ActivityState { Idle, Working, Failed, Stopped }

/// <summary>
/// The foreground job of a download step, and the status shown for it. One object instead of the same
/// busy / error / progress fields in every step, so the states and what each one shows can't drift apart.
/// Checking what's online (<see cref="IsChecking"/>) runs alongside and has its own flag.
/// </summary>
public sealed class Activity : ObservableObject
{
    readonly StateMachine<ActivityState> _machine;
    CancellationTokenSource? _cts;
    bool _checking;
    double? _percent;
    string _status = "", _detail = "", _error = "", _notice = "";

    public Activity(string name)
    {
        _machine = new StateMachine<ActivityState>(Flows.Activity, name);
        _machine.Moved += _ =>
        {
            Raise(nameof(State)); Raise(nameof(IsBusy)); Raise(nameof(IsIdle));
            Platform.RequeryCommands();
        };
    }

    public ActivityState State => _machine.State;
    public bool IsBusy => State == ActivityState.Working;
    public bool IsIdle => !IsBusy;
    /// <summary>Checking what's online, alongside the job.</summary>
    public bool IsChecking => _checking;
    public bool IsIndeterminate => _percent is null;
    public double Percent => _percent ?? 0;
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    /// <summary>What went wrong (Failed only).</summary>
    public string Error { get => _error; private set { if (Set(ref _error, value)) Raise(nameof(HasError)); } }
    public bool HasError => Error.Length > 0;
    /// <summary>What happened when the user stopped the job (Stopped only). Not an error.</summary>
    public string Notice { get => _notice; private set { if (Set(ref _notice, value)) Raise(nameof(HasNotice)); } }
    public bool HasNotice => Notice.Length > 0;

    /// <summary>Starts the job: clears the last error or notice. Returns the token that <see cref="Cancel"/> trips.</summary>
    public CancellationToken Begin(string status, double? percent = null)
    {
        _cts = new CancellationTokenSource();
        Error = Notice = Detail = "";
        Status = status;
        SetPercent(percent);
        _machine.Move(ActivityState.Working, status);
        return _cts.Token;
    }

    /// <param name="percent">0 to 100, or null for "working, can't say how far".</param>
    public void Report(double? percent, string detail, string? status = null)
    {
        SetPercent(percent);
        Detail = detail;
        if (status is not null)
            Status = status;
    }

    public void Finish() => End(ActivityState.Idle, null);

    /// <summary>Safety net for a finally block: a job that ended without Finish, Fail or Stop (an
    /// unexpected exception) goes back to Idle, as the steps always did, so the step can't stay busy.</summary>
    public void Settle()
    {
        if (IsBusy)
            End(ActivityState.Idle, "ended without a result");
    }
    public void Fail(string error) { Error = error; End(ActivityState.Failed, error); }
    public void Stop(string notice) { Notice = notice; End(ActivityState.Stopped, null); }

    /// <summary>Clears an error or notice when the user moves on (ticks something else, checks again).</summary>
    public void Dismiss()
    {
        Error = Notice = "";
        if (State is ActivityState.Failed or ActivityState.Stopped)
            _machine.Move(ActivityState.Idle);
    }

    public void Cancel() => _cts?.Cancel();

    /// <summary>Starts a check of what's online; false if one is already running.</summary>
    public bool BeginCheck()
    {
        if (_checking)
            return false;
        _checking = true;
        Raise(nameof(IsChecking));
        Dismiss();
        return true;
    }

    public void EndCheck()
    {
        _checking = false;
        Raise(nameof(IsChecking));
        Platform.RequeryCommands();
    }

    void End(ActivityState state, string? why)
    {
        Status = Detail = "";
        if (!_machine.Move(state, why))
            Platform.RequeryCommands();
    }

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }
}

/// <summary>A wizard step whose work runs through an <see cref="Activity"/>. Its status properties are
/// the activity's, under the names both windows bind to.</summary>
public abstract class ActivityStep : StepViewModel
{
    protected ActivityStep(string title, string logName) : base(title)
    {
        Activity = new Activity(logName);
        CancelCommand = new RelayCommand(Activity.Cancel, () => IsBusy);
        Activity.PropertyChanged += (_, e) =>
        {
            Raise(e.PropertyName);
            if (e.PropertyName == nameof(Activity.State))
            {
                Raise(nameof(CanGoNext));
                OnActivityChanged();
            }
        };
    }

    protected Activity Activity { get; }
    public ICommand CancelCommand { get; }

    public bool IsBusy => Activity.IsBusy;
    public bool IsIdle => Activity.IsIdle;
    public bool IsChecking => Activity.IsChecking;
    public bool IsIndeterminate => Activity.IsIndeterminate;
    public double Percent => Activity.Percent;
    public string Status => Activity.Status;
    public string Detail => Activity.Detail;
    public string Error => Activity.Error;
    public bool HasError => Activity.HasError;
    public string Notice => Activity.Notice;
    public bool HasNotice => Activity.HasNotice;
    public override bool IsWorking => IsBusy;

    /// <summary>The job started or ended: refresh whatever else depends on it.</summary>
    protected virtual void OnActivityChanged() { }
}
