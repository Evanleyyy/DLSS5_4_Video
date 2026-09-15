using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class RuntimeCacheTests
{
    private static void Check(bool result, string name)
    {
        if (!result) throw new Exception(name);
        Console.WriteLine("通过：" + name);
    }

    private static string Make(string root, string id)
    {
        string folder = RuntimeCache.Folder(root, id);
        Directory.CreateDirectory(Path.Combine(folder, "app", "nested"));
        File.WriteAllText(Path.Combine(folder, "ready.txt"), id, Encoding.UTF8);
        File.WriteAllText(Path.Combine(folder, "app", "nested", "model.bin"), "test");
        return folder;
    }

    private static void Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        string root = Path.GetFullPath(args[0]);
        Directory.CreateDirectory(root);
        string message;
        string id = "aaaaaaaaaaaaaaaaaaaa";
        string folder = Make(root, id);
        string sentinel = Path.Combine(root, "手动导出.png");
        File.WriteAllText(sentinel, "keep");
        RuntimeCache.Clean(root, id, false, false, out message);
        Check(Directory.Exists(folder), "普通退出保留缓存");
        using (RuntimeCache.Lease first = new RuntimeCache.Lease(folder))
        {
            using (RuntimeCache.Lease second = new RuntimeCache.Lease(folder))
            {
                Check(RuntimeCache.Clean(root, id, true, false, out message) == 2, "正在使用时仅登记清理请求");
                Check(File.Exists(Path.Combine(folder, "app", "nested", "model.bin")), "多开时运行文件保留");
            }
            Check(RuntimeCache.Clean(root, id, false, false, out message) == 2, "关闭一个窗口仍不删除另一窗口的缓存");
        }
        Check(RuntimeCache.Clean(root, id, false, false, out message) == 0 && !Directory.Exists(folder),
              "最后一个窗口退出后执行已请求的清理");
        Check(File.ReadAllText(sentinel) == "keep", "缓存目录之外的导出文件保留");
        folder = Make(root, id);
        using (RuntimeCache.Lease lease = new RuntimeCache.Lease(folder))
        {
            RuntimeCache.Clean(root, id, true, false, out message);
            RuntimeCache.Clean(root, id, true, true, out message);
        }
        RuntimeCache.Clean(root, id, false, false, out message);
        Check(Directory.Exists(folder) && !File.Exists(Path.Combine(folder, RuntimeCache.Request)), "取消后退出仍保留缓存");
        Check(RuntimeCache.Clean(root, id, true, false, out message) == 0 && !Directory.Exists(folder), "历史版本手动清理");
        folder = Make(root, id);
        File.WriteAllText(Path.Combine(folder, "ready.txt"), "other");
        bool rejected = false;
        try { RuntimeCache.Clean(root, id, true, false, out message); } catch (IOException) { rejected = true; }
        Check(rejected && Directory.Exists(folder), "标记不匹配时拒绝删除");
        rejected = false;
        try { RuntimeCache.Clean(root, "..", true, false, out message); } catch (IOException) { rejected = true; }
        Check(rejected && File.Exists(sentinel), "路径越界请求被拒绝");
        id = "bbbbbbbbbbbbbbbbbbbb";
        folder = Make(root, id);
        string locked = Path.Combine(folder, "app", "nested", "model.bin");
        using (FileStream stream = new FileStream(locked, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            rejected = false;
            try { RuntimeCache.Clean(root, id, true, false, out message); } catch (IOException) { rejected = true; }
            Check(rejected && !File.Exists(Path.Combine(folder, "ready.txt")) && File.Exists(Path.Combine(folder, RuntimeCache.Owner)),
                  "文件占用后保留身份标记且禁止复用半清理缓存");
        }
        Check(RuntimeCache.Clean(root, id, true, false, out message) == 0 && !Directory.Exists(folder), "解除占用后可重试清理");
        id = "cccccccccccccccccccc";
        folder = Make(root, id);
        string external = Path.Combine(root, "用户素材");
        Directory.CreateDirectory(external);
        File.WriteAllText(Path.Combine(external, "保留.png"), "keep");
        string junction = Path.Combine(folder, "linked");
        ProcessStartInfo start = new ProcessStartInfo("cmd.exe", "/c mklink /J \"" + junction + "\" \"" + external + "\"");
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        using (Process process = Process.Start(start))
        {
            process.StandardOutput.ReadToEnd();
            process.WaitForExit();
            Check(process.ExitCode == 0, "已创建目录联接测试夹具");
        }
        rejected = false;
        try { RuntimeCache.Clean(root, id, true, false, out message); } catch (IOException) { rejected = true; }
        Check(rejected && File.ReadAllText(Path.Combine(external, "保留.png")) == "keep" &&
              File.Exists(Path.Combine(folder, "app", "nested", "model.bin")), "目录联接在任何删除前被拒绝，外部文件保留");
    }
}
