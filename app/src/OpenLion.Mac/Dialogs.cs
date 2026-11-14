using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;

namespace OpenLion;

/// <summary>A plain message box (Avalonia has none built in).</summary>
public static class Dialogs
{
    /// <summary>Shows the message with one button per label; returns the index of the button pressed,
    /// or -1 if the window was closed another way. The last button is the default.</summary>
    public static async Task<int> ShowAsync(Window? owner, string message, params string[] buttons)
    {
        int result = -1;
        var dialog = new Window
        {
            Title = "OpenLion",
            Width = 460,
            SizeToContent = SizeToContent.Height,
            CanResize = false,
            WindowStartupLocation = owner is null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner,
            ShowInTaskbar = false,
        };
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Spacing = 10 };
        for (int i = 0; i < buttons.Length; i++)
        {
            int index = i;
            var b = new Button { Content = buttons[i], MinWidth = 90, IsDefault = i == buttons.Length - 1 };
            b.Click += (_, _) => { result = index; dialog.Close(); };
            row.Children.Add(b);
        }
        dialog.Content = new StackPanel
        {
            Margin = new Thickness(20),
            Spacing = 16,
            Children =
            {
                new SelectableTextBlock { Text = message, TextWrapping = TextWrapping.Wrap },
                row,
            },
        };
        if (owner is not null)
            await dialog.ShowDialog(owner);
        else
        {
            var closed = new TaskCompletionSource();
            dialog.Closed += (_, _) => closed.TrySetResult();
            dialog.Show();
            await closed.Task;
        }
        return result;
    }
}
