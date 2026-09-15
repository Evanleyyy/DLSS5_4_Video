using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

// All removals are individual files inside an identified, non-linked cache folder.
internal static class RuntimeCache
{
    internal const string Owner = "runtime-owner.txt";
    internal const string Request = "cleanup-request.txt";

    internal static string Root()
    {
        string root = Environment.GetEnvironmentVariable("DLSS5_CACHE_ROOT");
        if (String.IsNullOrEmpty(root))
            root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                "DLSS5Standalone", "runtime");
        return Path.GetFullPath(root);
    }

    internal static void CheckPath(string path)
    {
        for (string item = Path.GetFullPath(path); !String.IsNullOrEmpty(item); item = Path.GetDirectoryName(item))
            if ((Directory.Exists(item) || File.Exists(item)) &&
                (File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("缓存路径含链接或联接，已跳过：" + item);
    }

    internal static string Folder(string root, string id)
    {
        if (!Regex.IsMatch(id, "\\A[0-9a-f]{20}\\z")) throw new IOException("缓存版本标识无效。");
        string folder = Path.GetFullPath(Path.Combine(root, id));
        if (!String.Equals(Path.GetDirectoryName(folder), Path.GetFullPath(root).TrimEnd('\\', '/'),
                           StringComparison.OrdinalIgnoreCase)) throw new IOException("缓存路径越界。");
        CheckPath(folder);
        return folder;
    }

    internal static Mutex Guard(string id)
    {
        return new Mutex(false, "Local\\DLSS5_Prepare_" + id);
    }

    internal static bool Enter(Mutex mutex, int milliseconds)
    {
        try { return mutex.WaitOne(milliseconds); }
        catch (AbandonedMutexException) { return true; }
    }

    internal sealed class Lease : IDisposable
    {
        private readonly string path;
        private readonly FileStream stream;
        internal Lease(string folder)
        {
            path = Path.Combine(folder, ".lease-" + Guid.NewGuid().ToString("N"));
            stream = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
        }
        public void Dispose()
        {
            stream.Dispose();
            try { File.Delete(path); } catch (IOException) { }
        }
    }

    private static bool Owned(string folder, string id)
    {
        foreach (string name in new string[] { Owner, "ready.txt" })
        {
            string path = Path.Combine(folder, name);
            CheckPath(path);
            if (File.Exists(path) && File.ReadAllText(path, Encoding.UTF8).Trim() == id) return true;
        }
        return false;
    }

    private static bool HasLease(string folder)
    {
        foreach (string path in Directory.GetFiles(folder, ".lease-*"))
        {
            CheckPath(path);
            try { using (FileStream stream = File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { } }
            catch (IOException) { return true; }
            catch (UnauthorizedAccessException) { return true; }
        }
        return false;
    }

    private static bool LegacyAppRunning(string folder)
    {
        // Older launchers have no lease files, so also inspect their application process.
        string prefix = folder.TrimEnd('\\') + "\\";
        foreach (Process process in Process.GetProcessesByName("DLSS5_App"))
        {
            using (process)
            {
                try
                {
                    if (process.MainModule.FileName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return true;
                }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception)
                {
                    if (!process.HasExited) return true; // Unable to inspect: keep the files.
                }
            }
        }
        return false;
    }

    private static void Inventory(string folder, List<string> files, List<string> directories)
    {
        CheckPath(folder);
        foreach (string path in Directory.GetFiles(folder)) { CheckPath(path); files.Add(path); }
        foreach (string path in Directory.GetDirectories(folder)) Inventory(path, files, directories);
        directories.Add(folder);
    }

    private static void DeleteFile(string path)
    {
        for (int attempt = 0; ; attempt++)
        {
            CheckPath(path);
            try { File.Delete(path); return; }
            catch (IOException) { if (attempt >= 2) throw; Thread.Sleep(150); }
        }
    }

    // 0: removed/cancelled, 2: explicitly requested cleanup deferred, 3: in use.
    internal static int Clean(string root, string id, bool manual, bool cancel, out string message)
    {
        string folder = Folder(root, id);
        using (Mutex mutex = Guard(id))
        {
            if (!Enter(mutex, 1000)) { message = "其他进程正在准备或清理此版本，请稍后重试。"; return 3; }
            try
            {
                if (!Directory.Exists(folder)) { message = "缓存已清理。"; return 0; }
                if (!Owned(folder, id)) throw new IOException("缺少有效的软件缓存标记，未删除任何文件。");
                string request = Path.Combine(folder, Request);
                CheckPath(request);
                if (cancel) { DeleteFile(request); message = "已取消关闭后的清理任务。"; return 0; }
                if (!manual && (!File.Exists(request) || File.ReadAllText(request, Encoding.UTF8).Trim() != id))
                { message = "未请求清理，保留运行缓存。"; return 0; }
                if (HasLease(folder))
                {
                    if (manual) File.WriteAllText(request, id, Encoding.UTF8);
                    message = "已安排在使用此版本的所有窗口关闭后清理。";
                    return 2;
                }
                if (LegacyAppRunning(folder)) { message = "此版本仍在使用，请关闭对应窗口后重试。"; return 3; }
                List<string> files = new List<string>();
                List<string> directories = new List<string>();
                Inventory(folder, files, directories); // Reject links before the first removal.
                string owner = Path.Combine(folder, Owner);
                File.WriteAllText(owner, id, Encoding.UTF8); // Retain identity if a removal fails.
                DeleteFile(Path.Combine(folder, "ready.txt")); // A partial cleanup must never be reused.
                foreach (string path in files)
                    if (path != owner && path != request) DeleteFile(path);
                foreach (string path in directories)
                    if (path != folder) { CheckPath(path); Directory.Delete(path, false); }
                DeleteFile(request);
                DeleteFile(owner);
                CheckPath(folder);
                Directory.Delete(folder, false);
                message = "运行缓存已清理；下次启动将重新准备。";
                return 0;
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    internal static void WriteLog(string text)
    {
        try
        {
            string folder = Environment.GetEnvironmentVariable("DLSS5_LOG_DIR");
            if (String.IsNullOrEmpty(folder))
                folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                      "DLSS5Standalone", "logs");
            Directory.CreateDirectory(folder);
            File.AppendAllText(Path.Combine(folder, "launcher.log"),
                DateTime.Now.ToString("s") + " " + text + Environment.NewLine, Encoding.UTF8);
        }
        catch (Exception) { }
    }
}
