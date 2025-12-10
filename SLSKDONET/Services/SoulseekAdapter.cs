using System.IO;
using System.Reactive.Subjects;
using Microsoft.Extensions.Logging;
using SLSKDONET.Configuration;
using SLSKDONET.Models;

namespace SLSKDONET.Services;

/// <summary>
/// Lightweight placeholder adapter for Soulseek interactions.
/// This implementation avoids compile-time dependency on a specific Soulseek.NET API
/// and provides simple, deterministic behavior suitable for development and testing.
/// Replace with a proper integration when the target Soulseek.NET API is known.
/// </summary>
public class SoulseekAdapter : IDisposable
{
    private readonly ILogger<SoulseekAdapter> _logger;
    private readonly AppConfig _config;
    private bool _isConnected;

    // Events as observables for reactive programming
    public Subject<(string eventType, object data)> EventBus { get; } = new();

    public SoulseekAdapter(ILogger<SoulseekAdapter> logger, AppConfig config)
    {
        _logger = logger;
        _config = config;
    }

    public Task ConnectAsync(string? password = null, CancellationToken ct = default)
    {
        _isConnected = true;
        _logger.LogInformation("(placeholder) Connected to Soulseek network as {Username}", _config.Username);
        EventBus.OnNext(("connection_status", new { status = "connected", username = _config.Username }));
        return Task.CompletedTask;
    }

    public Task DisconnectAsync()
    {
        _isConnected = false;
        _logger.LogInformation("(placeholder) Disconnected from Soulseek");
        return Task.CompletedTask;
    }

    public async Task<int> SearchAsync(
        string query,
        IEnumerable<string>? formatFilter,
        (int? Min, int? Max) bitrateFilter,
        Action<Track> onTrackFound,
        CancellationToken ct = default)
    {
        _logger.LogInformation("(placeholder) Search requested: {Query}", query);
        // Placeholder: no real network search. Return zero results.
        await Task.Delay(1, ct);
        return 0;
    }

    public async Task<bool> DownloadAsync(
        string username,
        string filename,
        string outputPath,
        long? size,
        IProgress<double> progress,
        CancellationToken ct = default)
    {
        try
        {
            _logger.LogInformation("(placeholder) Downloading {Filename} from {Username} to {OutputPath}", filename, username, outputPath);

            var directory = Path.GetDirectoryName(outputPath);
            if (directory != null)
                Directory.CreateDirectory(directory);

            // Create a small placeholder file to simulate a download.
            await File.WriteAllTextAsync(outputPath, "This is a placeholder file created by SoulseekAdapter.", ct);

            progress?.Report(1.0);
            EventBus.OnNext(("transfer_finished", new { filename, username }));
            return true;
        }
        catch (OperationCanceledException)
        {
            _logger.LogWarning("(placeholder) Download cancelled: {Filename}", filename);
            EventBus.OnNext(("transfer_cancelled", new { filename, username }));
            return false;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "(placeholder) Download failed: {Message}", ex.Message);
            EventBus.OnNext(("transfer_failed", new { filename, username, error = ex.Message }));
            return false;
        }
    }

    public void Dispose()
    {
        EventBus?.Dispose();
    }

    public bool IsConnected => _isConnected;
}
