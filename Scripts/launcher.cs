// Mini-lanceur natif pour TSOG.
// Il ne contient AUCUN code du jeu : il se contente d'executer
// "TSOG Game.py" avec le Python de Spyder (qui a pygame installe).
// Le .py reste la source : toute modif du script prend effet sans recompiler.
//
// Recompiler :
//   csc /target:winexe /reference:System.Windows.Forms.dll /out:TSOG.exe launcher.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class Launcher
{
    // Trouve le pythonw.exe de l'environnement Spyder (celui qui a pygame).
    static string TrouverPython()
    {
        string fixe = @"C:\Users\User\AppData\Local\spyder-6\envs\spyder-runtime\pythonw.exe";
        if (File.Exists(fixe)) return fixe;
        try
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            foreach (string d in Directory.GetDirectories(local, "spyder-*"))
            {
                string p = Path.Combine(d, @"envs\spyder-runtime\pythonw.exe");
                if (File.Exists(p)) return p;
            }
        }
        catch { }
        return "pythonw.exe"; // dernier recours : depuis le PATH
    }

    static void Erreur(string msg)
    {
        MessageBox.Show(msg, "TSOG - The Siege of Grimgate",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    static void Main()
    {
        // Dossier de l'exe = dossier ou doit se trouver "TSOG Game.py".
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(dir, "TSOG Game.py");

        if (!File.Exists(script))
        {
            Erreur("Fichier introuvable :\n" + script +
                   "\n\nPlace TSOG.exe dans le MEME dossier que 'TSOG Game.py'.");
            return;
        }

        string py = TrouverPython();
        var psi = new ProcessStartInfo
        {
            FileName = py,
            Arguments = "\"" + script + "\"",
            WorkingDirectory = dir,   // pour que les chemins "assets/..." marchent
            UseShellExecute = false
        };

        // IMPORTANT : l'env de Spyder est un environnement conda. Lance directement,
        // son pythonw ne trouve pas les DLL (pygame, etc.) car les dossiers de l'env
        // ne sont pas dans le PATH. On les prepend ici (equivaut a "activer" l'env).
        string env = Path.GetDirectoryName(py);
        if (!string.IsNullOrEmpty(env) && Directory.Exists(env))
        {
            string[] dossiers = {
                env,
                Path.Combine(env, @"Library\mingw-w64\bin"),
                Path.Combine(env, @"Library\usr\bin"),
                Path.Combine(env, @"Library\bin"),
                Path.Combine(env, "Scripts"),
                Path.Combine(env, "bin"),
                Path.Combine(env, "DLLs")
            };
            string ancien = Environment.GetEnvironmentVariable("PATH");
            psi.EnvironmentVariables["PATH"] = string.Join(";", dossiers) + ";" + (ancien == null ? "" : ancien);
        }

        try
        {
            Process.Start(psi);
        }
        catch (Exception e)
        {
            Erreur("Impossible de lancer Python (" + py + ") :\n" + e.Message);
        }
    }
}
