using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

// The payload uses lossless 7z compression. No Python installation is required.
internal static class LargePackageLauncher
{
    private static Form preparation;
    private static ProgressBar progress;

    [STAThread]
    private static int Main(string[] args)
    {
        bool verify = Array.IndexOf(args, "--verify-package") >= 0;
        string cacheBase = Environment.GetEnvironmentVariable("DLSS5_CACHE_ROOT");
        if (String.IsNullOrEmpty(cacheBase))
            cacheBase = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                     "DLSS5Standalone", "runtime");
        string cache = Path.Combine(cacheBase, BuildInfo.Id);
        string app = Path.Combine(cache, "app", "DLSS5_App.exe");
        string complete = Path.Combine(cache, "ready.txt");
        Directory.CreateDirectory(cache);
        try
        {
            using (Mutex mutex = new Mutex(false, "Local\\DLSS5_Prepare_" + BuildInfo.Id))
            {
                bool acquired = false;
                try
                {
                    try { acquired = mutex.WaitOne(600000); }
                    catch (AbandonedMutexException) { acquired = true; }
                    if (!acquired) throw new IOException("另一个启动进程仍在准备文件，请稍后重试。");
                    if (!File.Exists(complete) || !File.Exists(app))
                    {
                        if (!verify) ShowPreparation();
                        Prepare(cache);
                        if (!File.Exists(app)) throw new IOException("应用文件未能正确解压。");
                        File.WriteAllText(complete, BuildInfo.Id, Encoding.UTF8);
                    }
                }
                finally
                {
                    if (preparation != null) preparation.Dispose();
                    if (acquired) mutex.ReleaseMutex();
                }
            }
            ProcessStartInfo start = new ProcessStartInfo(app);
            start.WorkingDirectory = Path.GetDirectoryName(app);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            StringBuilder arguments = new StringBuilder();
            foreach (string argument in args)
            {
                if (arguments.Length > 0) arguments.Append(' ');
                arguments.Append(Quote(argument));
            }
            start.Arguments = arguments.ToString();
            using (Process child = Process.Start(start))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception exception)
        {
            File.WriteAllText(Path.Combine(cache, "launcher-error.log"), exception.ToString(), Encoding.UTF8);
            if (!verify) MessageBox.Show("启动失败：" + exception.Message + "\n\n日志：" + cache,
                                        "DLSS5", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static void ShowPreparation()
    {
        Application.EnableVisualStyles();
        preparation = new Form();
        preparation.Text = "DLSS5 · 首次运行准备";
        preparation.Width = 460;
        preparation.Height = 145;
        preparation.StartPosition = FormStartPosition.CenterScreen;
        preparation.FormBorderStyle = FormBorderStyle.FixedDialog;
        preparation.ControlBox = false;
        Label label = new Label();
        label.Text = "正在准备运行环境，完成后将自动打开工具。";
        label.Dock = DockStyle.Top;
        label.Height = 40;
        label.Padding = new Padding(12);
        progress = new ProgressBar();
        progress.Dock = DockStyle.Bottom;
        progress.Height = 24;
        preparation.Controls.Add(label);
        preparation.Controls.Add(progress);
        preparation.Show();
        Application.DoEvents();
    }

    private static void Prepare(string cache)
    {
        string extractor = Path.Combine(cache, "7za.exe");
        using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("extractor"))
        using (FileStream target = File.Create(extractor)) resource.CopyTo(target);
        string payload = Path.Combine(cache, "payload.7z");
        string executable = Assembly.GetExecutingAssembly().Location;
        using (FileStream source = File.OpenRead(executable))
        using (BinaryReader reader = new BinaryReader(source, Encoding.UTF8, true))
        {
            source.Seek(-64, SeekOrigin.End);
            byte[] magic = reader.ReadBytes(16);
            if (Encoding.ASCII.GetString(magic) != "DLSS5PACKv1!!!!!")
                throw new IOException("程序包尾部标记不正确。");
            long offset = reader.ReadInt64();
            long length = reader.ReadInt64();
            byte[] expected = reader.ReadBytes(32);
            if (offset < 0 || length < 0 || offset + length != source.Length - 64)
                throw new IOException("程序包长度不正确。");
            source.Position = offset;
            byte[] buffer = new byte[1048576];
            long remaining = length;
            using (SHA256 hash = SHA256.Create())
            using (FileStream target = File.Create(payload))
            {
                while (remaining > 0)
                {
                    int read = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                    if (read == 0) throw new EndOfStreamException();
                    target.Write(buffer, 0, read);
                    hash.TransformBlock(buffer, 0, read, buffer, 0);
                    remaining -= read;
                    if (progress != null)
                    {
                        progress.Value = (int)((length - remaining) * 100 / length);
                        Application.DoEvents();
                    }
                }
                hash.TransformFinalBlock(new byte[0], 0, 0);
                for (int i = 0; i < expected.Length; i++)
                    if (expected[i] != hash.Hash[i]) throw new IOException("程序包完整性校验失败。");
            }
        }
        if (progress != null) progress.Style = ProgressBarStyle.Marquee;
        string appDirectory = Path.Combine(cache, "app");
        Directory.CreateDirectory(appDirectory);
        ProcessStartInfo start = new ProcessStartInfo(extractor,
            "x " + Quote(payload) + " -o" + Quote(appDirectory) + " -y -bd -bso0 -bsp0");
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardError = true;
        using (Process child = Process.Start(start))
        {
            var errorRead = child.StandardError.ReadToEndAsync();
            while (!child.WaitForExit(100)) Application.DoEvents();
            string errors = errorRead.Result;
            if (child.ExitCode != 0) throw new IOException("解压失败：" + errors);
        }
        // Delete only this explicitly named temporary archive, never a directory tree.
        File.Delete(payload);
    }

    private static string Quote(string value)
    {
        StringBuilder quoted = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') quoted.Append('\\', slashes * 2 + 1);
            else quoted.Append('\\', slashes);
            quoted.Append(c);
            slashes = 0;
        }
        quoted.Append('\\', slashes * 2);
        return quoted.Append('"').ToString();
    }
}
