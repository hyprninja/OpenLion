using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Voices;

/// <summary>A voice pack OpenLion knows about: a new UK English voice for the unit's prompts,
/// installed by the engine (openlion/voicepack.py, docs/26_voice_packs.md).</summary>
/// <param name="Id">Engine and file id ("elodie-v1"); also the sample's resource name.</param>
/// <param name="Url">The pack zip, a GitHub release asset.</param>
/// <param name="Sha256">The zip's SHA-256: a download is only used when it matches.</param>
/// <param name="Credit">Shown next to the voice (the licence asks for it).</param>
/// <param name="Explicit">Swears: the app says so next to it.</param>
public sealed record VoicePack(string Id, string Name, string Description, Uri Url, string Sha256, long Bytes, string Credit,
    bool Explicit = false)
{
    public string FileName => $"openlion-voice-{Id}.zip";
}

public static class VoicePacks
{
    public static readonly VoicePack Elodie = new("elodie-v1", "Elodie",
        "A warm, relaxed Australian woman's voice, named as a nod to DankPods' EFM Toledo. She says common " +
        "instructions in longer phrases (\"Turn left\", \"300 meters ahead\"), so they sound smoother than the factory voice.",
        new Uri("https://github.com/hyprninja/OpenLion/releases/download/voice-elodie-v1/openlion-voice-elodie-v1.zip"),
        "f7e2f7ff35140298a6725bc2fd42330022568978acc0726e10077b31cc2e68a3", 11_205_539,
        "Voice made with ElevenLabs. CC BY 4.0, OpenLion.");

    public static readonly VoicePack Jimbo = new("jimbo-v1", "Jimbo",
        "An 11/10, over-the-top bogan with the broadest accent in the country. He still tells you exactly " +
        "where to go (\"Turn left\", \"300 meters ahead\"), then adds his two cents.",
        new Uri("https://github.com/hyprninja/OpenLion/releases/download/voice-jimbo-v1/openlion-voice-jimbo-v1.zip"),
        "d1b5e4d58ebb988ea76720e3bb69a5d64196d1e4e7b6b862283f523a946a81b8", 14_347_012,
        "Voice made with ElevenLabs. CC BY 4.0, OpenLion.", Explicit: true);

    public static readonly IReadOnlyList<VoicePack> All = [Elodie, Jimbo];

    /// <summary>A GitHub release download redirects to GitHub's file servers.</summary>
    public static readonly IReadOnlyList<string> AllowedHosts =
        ["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"];

    public const string LanguageNote =
        "Custom voices are UK English only. After you put the new card in, set the unit's language to " +
        "English (UK) in its settings, or you'll keep hearing the factory voice.";

    public static VoicePack? ById(string? id) =>
        All.FirstOrDefault(p => string.Equals(p.Id, id, StringComparison.OrdinalIgnoreCase));

    public static bool Allowed(Uri url) =>
        url.Scheme == Uri.UriSchemeHttps && AllowedHosts.Contains(url.Host, StringComparer.OrdinalIgnoreCase);
}

public class VoicePackException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Voice packs on this PC (one zip each in <paramref name="folder"/>), and downloading them.</summary>
public sealed class VoicePackDownloader(HttpClient http, string folder)
{
    public string Folder => folder;
    public string PathFor(VoicePack p) => Path.Combine(folder, p.FileName);

    /// <summary>The pack's zip when it's here and its checksum matches, else null.</summary>
    public string? Cached(VoicePack p)
    {
        var path = PathFor(p);
        try
        {
            if (!File.Exists(path) || new FileInfo(path).Length != p.Bytes)
                return null;
            using var f = File.OpenRead(path);
            return Convert.ToHexStringLower(SHA256.HashData(f)) == p.Sha256 ? path : null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return null; }
    }

    /// <summary>Downloads the pack and keeps it only if its size and SHA-256 match.</summary>
    public async Task<string> DownloadAsync(VoicePack p, IProgress<DownloadProgress>? progress = null, CancellationToken ct = default)
    {
        if (!VoicePacks.Allowed(p.Url))
            throw new VoicePackException($"Refusing to download from {p.Url.Host}.");
        Directory.CreateDirectory(folder);
        var final = PathFor(p);
        var part = final + ".part";
        using (var req = new HttpRequestMessage(HttpMethod.Get, p.Url))
        using (var r = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct))
        {
            if (r.RequestMessage?.RequestUri is { } where && !VoicePacks.Allowed(where))
                throw new VoicePackException($"The download was redirected to {where.Host}; OpenLion only downloads voices from GitHub.");
            if (r.StatusCode == HttpStatusCode.NotFound)
                throw new VoicePackException($"The {p.Name} voice isn't available for download yet.");
            r.EnsureSuccessStatusCode();
            if (r.Content.Headers.ContentLength is { } len && len != p.Bytes)
                throw new VoicePackException($"The {p.Name} voice on GitHub isn't the one this OpenLion expects. Update OpenLion.");
            await using var src = await r.Content.ReadAsStreamAsync(ct);
            await using var dst = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16, FileOptions.Asynchronous);
            var buf = new byte[1 << 16];
            long done = 0;
            int n;
            while ((n = await src.ReadAsync(buf, ct)) > 0)
            {
                await dst.WriteAsync(buf.AsMemory(0, n), ct);
                done += n;
                if (done > p.Bytes)
                    throw new VoicePackException("The download is bigger than the voice pack should be.");
                progress?.Report(new DownloadProgress(DownloadPhase.Downloading, done, p.Bytes, 0));
            }
        }
        progress?.Report(new DownloadProgress(DownloadPhase.Verifying, 0, null, 0));
        string digest;
        await using (var f = File.OpenRead(part))
            digest = Convert.ToHexStringLower(await SHA256.HashDataAsync(f, ct));
        if (digest != p.Sha256 || new FileInfo(part).Length != p.Bytes)
        {
            File.Delete(part);
            throw new VoicePackException("The downloaded voice pack is damaged (checksum differs). Press Download to try again.");
        }
        File.Move(part, final, overwrite: true);
        return final;
    }
}

/// <summary>Listening samples. A pack's sample ships with the app; the factory voice's is joined
/// from the user's own factory card copy, so no factory audio is ever part of OpenLion.</summary>
public static class VoiceSamples
{
    /// <summary>The factory clips for the three sample sentences (openlion/voicepack.py SAMPLE_SENTENCES),
    /// as the unit joins them: "The navigation system is ready." / "Turn left 300 meters ahead." /
    /// "Turn right 300 meters ahead and then immediately turn left."</summary>
    public static readonly IReadOnlyList<IReadOnlyList<string>> StockSentences =
    [
        ["navready"],
        ["iturn", "mleft2", "md300", "mmeters", "fahead"],
        ["iturn", "mright", "md300", "mmeters", "mahead", "mandthen", "mimmedia", "mturn", "fleft"],
    ];

    const int Rate = 16000;
    const double Gap = 0.6;

    /// <summary>The factory voice's sample from a card copy's navigation/ folder, or null when its
    /// voices/en_GB clips aren't there.</summary>
    public static byte[]? Stock(string navigation)
    {
        var folder = FindFolder(navigation, "voices", "en_GB");
        if (folder is null)
            return null;
        var pcm = new MemoryStream();
        for (int i = 0; i < StockSentences.Count; i++)
        {
            if (i > 0)
                pcm.Write(new byte[(int)(Gap * Rate) * 2]);
            foreach (var clip in StockSentences[i])
            {
                var path = Path.Combine(folder, clip + ".wav");
                if (!File.Exists(path) || ReadPcm(File.ReadAllBytes(path)) is not { } data)
                    return null;
                pcm.Write(data);
            }
        }
        return Wav(pcm.ToArray());
    }

    static string? FindFolder(string root, params string[] parts)
    {
        var cur = root;
        foreach (var part in parts)
        {
            if (!Directory.Exists(cur))
                return null;
            cur = Directory.EnumerateDirectories(cur).FirstOrDefault(d =>
                string.Equals(Path.GetFileName(d), part, StringComparison.OrdinalIgnoreCase));
            if (cur is null)
                return null;
        }
        return cur;
    }

    /// <summary>The samples of a 16 kHz mono 16-bit PCM WAV, or null for any other file.</summary>
    public static byte[]? ReadPcm(byte[] wav)
    {
        if (wav.Length < 12 || BitConverter.ToUInt32(wav, 0) != 0x46464952 || BitConverter.ToUInt32(wav, 8) != 0x45564157)
            return null;     // "RIFF" ... "WAVE"
        bool fmtOk = false;
        for (int p = 12; p + 8 <= wav.Length;)
        {
            uint id = BitConverter.ToUInt32(wav, p);
            int size = BitConverter.ToInt32(wav, p + 4);
            if (size < 0 || p + 8 + size > wav.Length)
                return null;
            if (id == 0x20746D66 && size >= 16)       // "fmt "
                fmtOk = BitConverter.ToUInt16(wav, p + 8) == 1 && BitConverter.ToUInt16(wav, p + 10) == 1 &&
                        BitConverter.ToUInt32(wav, p + 12) == Rate && BitConverter.ToUInt16(wav, p + 22) == 16;
            else if (id == 0x61746164)                // "data"
                return fmtOk ? wav.AsSpan(p + 8, size).ToArray() : null;
            p += 8 + size + (size & 1);
        }
        return null;
    }

    public static byte[] Wav(byte[] pcm)
    {
        var o = new MemoryStream(44 + pcm.Length);
        var w = new BinaryWriter(o);
        w.Write(0x46464952u); w.Write(36 + pcm.Length); w.Write(0x45564157u);
        w.Write(0x20746D66u); w.Write(16); w.Write((short)1); w.Write((short)1); w.Write(Rate); w.Write(Rate * 2);
        w.Write((short)2); w.Write((short)16);
        w.Write(0x61746164u); w.Write(pcm.Length); w.Write(pcm);
        w.Flush();
        return o.ToArray();
    }
}
