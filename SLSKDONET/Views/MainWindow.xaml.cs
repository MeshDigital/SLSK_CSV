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

    public MainWindow(MainViewModel viewModel, INavigationService navigationService)
    {
        try
        {
            InitializeComponent();
            _viewModel = viewModel;
            DataContext = viewModel;
            
            // Initialize navigation (this will navigate to Search page)
            InitializeNavigation(navigationService);
            
            // Ensure initial library load (async, won't block UI)
            _viewModel.OnViewLoaded();
        }
        catch (Exception ex)
        {
            System.Windows.MessageBox.Show($"MainWindow initialization failed: {ex.Message}\n\n{ex.StackTrace}", 
                "Initialization Error", 
                System.Windows.MessageBoxButton.OK, 
                System.Windows.MessageBoxImage.Error);
            throw;
        }
    }

    private void InitializeNavigation(INavigationService navigationService)
    {
        // Register all pages before navigating so the frame can resolve them.
        navigationService.RegisterPage("Search", typeof(SearchPage));
        navigationService.RegisterPage("Imported", typeof(ImportedPage));
        navigationService.RegisterPage("Library", typeof(LibraryPage));
        navigationService.RegisterPage("Downloads", typeof(DownloadsPage));
        navigationService.RegisterPage("Settings", typeof(SettingsPage));

        navigationService.SetFrame(RootFrame);
        navigationService.NavigateTo("Search"); // Set the startup page
    }


}
