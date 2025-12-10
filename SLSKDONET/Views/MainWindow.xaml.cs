using System.Windows;
using System.Windows.Input;
using System.Windows.Controls;
using Microsoft.Win32;
using Microsoft.VisualBasic;
using System.Windows.Forms; // For FolderBrowserDialog
using Wpf.Ui.Controls;
using System.Windows.Navigation;
using System;
using SLSKDONET.Services;

namespace SLSKDONET.Views;

/// <summary>
/// Interaction logic for MainWindow.xaml
/// </summary>
public partial class MainWindow : FluentWindow
{
    private readonly MainViewModel _viewModel;
    private bool _isDraggingLoginCard;
    private System.Windows.Point _loginCardDragStart;
    private System.Windows.Point _cardPositionStart;
    private Grid? _loginCardElement;


    public MainWindow(MainViewModel viewModel, INavigationService navigationService)
    {
        InitializeComponent();
        _viewModel = viewModel;
        DataContext = viewModel;
        InitializeNavigation(navigationService);
        
        // Cache the LoginCard element after InitializeComponent
        _loginCardElement = LoginCard;

        // Ensure initial library load
        _viewModel.OnViewLoaded();

        // Set the DataContext for the entire window, so pages can inherit it.
        // RootNavigationView.DataContext = viewModel; // This is redundant, DataContext is inherited.
    }

    private void InitializeNavigation(INavigationService navigationService)
    {
        navigationService.SetFrame(RootFrame);
        navigationService.NavigateTo("Search"); // Set the startup page
    }

    private void LoginCard_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (e.LeftButton == MouseButtonState.Pressed && _loginCardElement != null)
        {
            _isDraggingLoginCard = true;
            _loginCardDragStart = e.GetPosition(this);
            
            // If card is currently centered, convert to absolute position before dragging
            if (_loginCardElement.HorizontalAlignment == System.Windows.HorizontalAlignment.Center 
                || _loginCardElement.VerticalAlignment == System.Windows.VerticalAlignment.Center)
            {
                double cardWidth = _loginCardElement.ActualWidth > 0 ? _loginCardElement.ActualWidth : 420;
                double cardHeight = _loginCardElement.ActualHeight > 0 ? _loginCardElement.ActualHeight : 200;
                
                double centerLeft = (this.ActualWidth - cardWidth) / 2;
                double centerTop = (this.ActualHeight - cardHeight) / 2;
                
                _cardPositionStart = new System.Windows.Point(centerLeft, centerTop);
                
                // Switch from centered to absolute positioning
                _loginCardElement.HorizontalAlignment = System.Windows.HorizontalAlignment.Left;
                _loginCardElement.VerticalAlignment = System.Windows.VerticalAlignment.Top;
                _loginCardElement.Margin = new Thickness(centerLeft, centerTop, 0, 0);
            }
            else
            {
                // Already positioned absolutely, use current margin
                var margin = _loginCardElement.Margin;
                _cardPositionStart = new System.Windows.Point(margin.Left, margin.Top);
            }
            
            _loginCardElement.CaptureMouse();
        }
    }

    protected override void OnMouseMove(System.Windows.Input.MouseEventArgs e)
    {
        base.OnMouseMove(e);

        if (_isDraggingLoginCard && _loginCardElement != null)
        {
            System.Windows.Point currentPos = e.GetPosition(this);
            double offsetX = currentPos.X - _loginCardDragStart.X;
            double offsetY = currentPos.Y - _loginCardDragStart.Y;

            // Get login card dimensions
            double cardWidth = _loginCardElement.ActualWidth > 0 ? _loginCardElement.ActualWidth : 420;
            double cardHeight = _loginCardElement.ActualHeight > 0 ? _loginCardElement.ActualHeight : 200;

            // Calculate new position from stored start position
            double newLeft = _cardPositionStart.X + offsetX;
            double newTop = _cardPositionStart.Y + offsetY;

            // Clamp to window bounds
            newLeft = System.Math.Max(0, System.Math.Min(newLeft, this.ActualWidth - cardWidth));
            newTop = System.Math.Max(0, System.Math.Min(newTop, this.ActualHeight - cardHeight));

            // Update position
            _loginCardElement.Margin = new Thickness(newLeft, newTop, 0, 0);
            _loginCardElement.HorizontalAlignment = System.Windows.HorizontalAlignment.Left;
            _loginCardElement.VerticalAlignment = System.Windows.VerticalAlignment.Top;
        }
    }

    protected override void OnMouseUp(MouseButtonEventArgs e)
    {
        base.OnMouseUp(e);
        if (_isDraggingLoginCard)
        {
            _isDraggingLoginCard = false;
            if (_loginCardElement != null)
                _loginCardElement.ReleaseMouseCapture();
        }
    }

    private void RootNavigationView_OnNavigated(object sender, NavigationEventArgs e)
    {
        // Navigation event handler placeholder. Implement UI sync here if needed.
    }
}
