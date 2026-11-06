using System.Security.Cryptography;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;
using OpenLion.Core.Voices;

namespace OpenLion.Core.Tests;

public class VoicePackTests
{
    static byte[] Tone(int samples, short value = 100) =>
        VoiceSamples.Wav(Enumerable.Range(0, samples).SelectMany(_ => BitConverter.GetBytes(value)).ToArray());

    [Fact]
    public void BuildPassesTheVoicePack()
    {
        var req = new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out", VoicePack: @"C:\v\elodie.zip");
        var args = MapBuild.Arguments(req);
        Assert.Equal(@"C:\v\elodie.zip", args[args.IndexOf("--voice-pack") + 1]);
        Assert.DoesNotContain("--voice-pack", MapBuild.Arguments(new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out")));
    }

    [Fact]
    public void BuildKey_ChangesWithTheVoice()
    {
        var osm = new[] { new OsmExtract("C:/osm/australia-261104.osm.pbf", "australia-261104.osm.pbf", null, 1, null, DateTime.UtcNow, "test") };
        Assert.Equal(BuildRecord.OsmKey(osm), BuildRecord.Key(osm, null, null, null));   // factory voice: key as before
        Assert.Equal("australia-261104.osm.pbf|voice:elodie-v1", BuildRecord.Key(osm, null, null, "elodie-v1"));
    }

    [Fact]
    public void KnownPacksDownloadFromGitHubOnly()
    {
        foreach (var p in VoicePacks.All)
        {
            Assert.True(VoicePacks.Allowed(p.Url), p.Id);
            Assert.Matches("^[0-9a-f]{64}$", p.Sha256);
            Assert.Equal(p, VoicePacks.ById(p.Id.ToUpperInvariant()));
        }
        Assert.False(VoicePacks.Allowed(new Uri("https://example.com/voice.zip")));
        Assert.False(VoicePacks.Allowed(new Uri("http://github.com/voice.zip")));
    }

    [Fact]
    public void WavRoundTripAndFormatCheck()
    {
        var wav = Tone(1600);
        Assert.Equal(3200, VoiceSamples.ReadPcm(wav)!.Length);
        var stereo = (byte[])wav.Clone();
        stereo[22] = 2;                                   // channels
        Assert.Null(VoiceSamples.ReadPcm(stereo));
        Assert.Null(VoiceSamples.ReadPcm([1, 2, 3]));
    }

    [Fact]
    public void StockSampleIsJoinedFromTheCardCopy()
    {
        using var t = new TempDir();
        var folder = t.Sub("navigation", "Voices", "EN_GB");      // cards differ in folder case
        Directory.CreateDirectory(folder);
        var clips = VoiceSamples.StockSentences.SelectMany(s => s).Distinct().ToList();
        foreach (var c in clips)
            File.WriteAllBytes(Path.Combine(folder, c + ".wav"), Tone(160));
        var sample = VoiceSamples.Stock(t.Sub("navigation"))!;
        int clipCount = VoiceSamples.StockSentences.Sum(s => s.Count);
        int gaps = (int)(0.6 * 16000) * 2 * (VoiceSamples.StockSentences.Count - 1);
        Assert.Equal(clipCount * 320 + gaps, VoiceSamples.ReadPcm(sample)!.Length);
        File.Delete(Path.Combine(folder, clips[0] + ".wav"));
        Assert.Null(VoiceSamples.Stock(t.Sub("navigation")));
    }

    [Fact]
    public void CachedOnlyWhenTheChecksumMatches()
    {
        using var t = new TempDir();
        var bytes = new byte[] { 1, 2, 3, 4 };
        var p = new VoicePack("test-v1", "Test", "", new Uri("https://github.com/x/y.zip"),
            Convert.ToHexStringLower(SHA256.HashData(bytes)), bytes.Length, "");
        var d = new VoicePackDownloader(new HttpClient(), t.Path);
        Assert.Null(d.Cached(p));
        File.WriteAllBytes(d.PathFor(p), bytes);
        Assert.Equal(d.PathFor(p), d.Cached(p));
        File.WriteAllBytes(d.PathFor(p), [1, 2, 3, 5]);
        Assert.Null(d.Cached(p));
    }
}
