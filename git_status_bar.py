import sublime
import sublime_plugin
import subprocess
import os
import re
from threading import Timer
import json

def debounce(wait):
    """ Decorator that will postpone a functions
        execution until after wait seconds
        have elapsed since the last time it was invoked. """
    def decorator(fn):
        def debounced(*args, **kwargs):
            def call_it():
                fn(*args, **kwargs)
            try:
                debounced.t.cancel()
            except(AttributeError):
                pass
            debounced.t = Timer(wait, call_it)
            debounced.t.start()
        return debounced
    return decorator

def plugin_loaded():
    s = sublime.load_settings("Git.sublime-settings")
    if s.get("statusbar_branch"):
        s.set("statusbar_branch", False)
        sublime.save_settings("Git.sublime-settings")
    if s.get("statusbar_status"):
        s.set("statusbar_status", False)
        sublime.save_settings("Git.sublime-settings")
    ofile = os.path.join(sublime.packages_path(), "User", "Git-StatusBar.sublime-settings")
    nfile = os.path.join(sublime.packages_path(), "User", "GitStatusBar.sublime-settings")
    if os.path.exists(ofile):
        os.rename(ofile, nfile)


def plugin_unloaded():
    """reset sublime-text-git status bar"""
    s = sublime.load_settings("Git.sublime-settings")
    if s.get("statusbar_branch") is False:
        s.set("statusbar_branch", True)
        sublime.save_settings("Git.sublime-settings")
    if s.get("statusbar_status") is False:
        s.set("statusbar_status", True)
        sublime.save_settings("Git.sublime-settings")


class GithubApi:
    def __init__(self):
        s = sublime.load_settings("GitStatusBar.sublime-settings")
        self.token = s.get("github_token", "")

    def run_curl(self, url):
        plat = sublime.platform()
        if type(url) == str:
            url = [url + '&access_token=' + self.token]
        cmd = ['curl'] + url
        if plat == "windows":
            # make sure console does not come up
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 startupinfo=startupinfo)
        else:
            my_env = os.environ.copy()
            my_env["PATH"] = "/usr/local/bin:/usr/bin:" + my_env["PATH"]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 env=my_env)
        p.wait()
        stdoutdata, _ = p.communicate()
        return stdoutdata.decode('utf-8')

    def search_pr(self, sha):
        if self.token == '':
            return [None, None]

        result = self.run_curl('https://api.github.com/search/issues?q=' + sha)
        data = json.loads(result)

        if not data or not 'total_count' in data or data.get('total_count') < 1:
            return [None, None]

        number = data.get('items')[0].get('number')
        pr = data.get('items')[0].get('pull_request')

        return [number, pr.get('html_url')]


class GitManager:
    def __init__(self, view):
        self.view = view
        s = sublime.load_settings("GitStatusBar.sublime-settings")
        self.git = s.get("git", "git")
        self.prefix = s.get("prefix", "")
        self.github_api = GithubApi()

    def run_git(self, cmd, cwd=None):
        plat = sublime.platform()
        if not cwd:
            cwd = self.getcwd()
        if cwd:
            if type(cmd) == str:
                cmd = [cmd]
            cmd = [self.git] + cmd
            if plat == "windows":
                # make sure console does not come up
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     cwd=cwd, startupinfo=startupinfo)
            else:
                my_env = os.environ.copy()
                my_env["PATH"] = "/usr/local/bin:/usr/bin:" + my_env["PATH"]
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     cwd=cwd, env=my_env)
            p.wait()
            stdoutdata, _ = p.communicate()
            return stdoutdata.decode('utf-8')

    def getcwd(self):
        f = self.view.file_name()
        cwd = None
        if f:
            cwd = os.path.dirname(f)
        if not cwd:
            window = self.view.window()
            if window:
                pd = window.project_data()
                if pd:
                    cwd = pd.get("folders")[0].get("path")
        return cwd

    def branch(self):
        ret = self.run_git(["symbolic-ref", "HEAD", "--short"])
        if ret:
            ret = ret.strip()
        else:
            output = self.run_git("branch")
            if output:
                m = re.search(r"\* *\(detached from (.*?)\)", output, flags=re.MULTILINE)
                if m:
                    ret = m.group(1)
        return ret

    def is_dirty(self):
        output = self.run_git("status")
        if not output:
            return False
        ret = re.search(r"working (tree|directory) clean", output)
        if ret:
            return False
        else:
            return True

    def unpushed_info(self):
        branch = self.branch()
        a, b = 0, 0
        if branch:
            output = self.run_git(["branch", "-v"])
            if output:
                m = re.search(r"\* .*?\[behind ([0-9])+\]", output, flags=re.MULTILINE)
                if m:
                    a = int(m.group(1))
                m = re.search(r"\* .*?\[ahead ([0-9])+\]", output, flags=re.MULTILINE)
                if m:
                    b = int(m.group(1))
        return (a, b)

    def badge(self):
        branch = self.branch()
        if not branch:
            return ""
        ret = branch
        if self.is_dirty():
            ret = ret + "*"
        a, b = self.unpushed_info()
        if a:
            ret = ret + "-%d" % a
        if b:
            ret = ret + "+%d" % b
        return self.prefix + ret

    def blame_sha(self):
        file = self.view.file_name()

        if not file:
            return ''

        (row, col) = self.view.rowcol(self.view.sel()[0].begin())

        blame = self.run_git(["blame", "-s", "-w", "-M", "-L " + str(row + 1) + ",+1", file])
        sha = blame.split('\n')[0].split(' ')[0]

        # remove special mark
        sha = sha.replace('^', '')

        return sha

    def blame_pr(self, sha):
        return self.github_api.search_pr(sha)

    def blame_badge(self):
        sha = self.blame_sha()

        # uncommitted line
        if sha == '00000000':
            return '[uncommitted]'

        text = "%h: %s (%an) %ad"
        pr, url = self.blame_pr(sha)

        if pr:
            text = text +  " #" + str(pr)

        return self.run_git(["log", "-1", "--date=relative", "--format=" + text, sha])[:-1]

class GitBlameStatusBarCommand(sublime_plugin.TextCommand):
    def open_url(self, url):
        sublime.active_window().run_command('open_url', {'url': url})

    def run(self, edit, page='/'):
        gm = GitManager(self.view)
        sha = gm.blame_sha()

        if sha == '00000000':
            return

        pr, url = gm.blame_pr(sha)

        if pr:
            self.open_url(url + page)

class GitBlameStatusBarFile(sublime_plugin.TextCommand):
    def open_url(self, url):
        sublime.active_window().run_command('open_url', {'url': url})

    def run(self, edit, branch=None):
        # opened folders
        folders = self.view.window().folders()
        # TODO: probably not the best way
        active_folder = self.view.window().folders()[0]
        relpath = self.view.file_name().replace(active_folder, '')

        # TODO: might not be stable? :shrug:
        (row, col) = self.view.rowcol(self.view.sel()[0].begin())

        if row:
            row = '#L' + str(row + 1)

        # TODO: strong assumption here
        repo = active_folder.split('/')[-1]
        org = 'productboard'

        if branch == None:
            gm = GitManager(self.view)
            branch = gm.branch()

        self.open_url('https://github.com/' + org + '/' + repo + '/blob/' + branch + relpath + row)

class GitStatusBarHandler(sublime_plugin.EventListener):
    @debounce(0.5)
    def update_status_bar(self, view):
        sublime.set_timeout_async(lambda: self._update_status_bar(view))

    def _update_status_bar(self, view):
        if not view or view.is_scratch() or view.settings().get('is_widget'):
            return
        gm = GitManager(view)
        badge = gm.blame_badge()
        if badge:
            view.set_status("git-statusbar", badge)
        else:
            view.erase_status("git-statusbar")

    def on_new(self, view):
        self.update_status_bar(view)

    def on_load(self, view):
        self.update_status_bar(view)

    def on_activated(self, view):
        self.update_status_bar(view)

    def on_deactivated(self, view):
        self.update_status_bar(view)

    def on_post_save(self, view):
        self.update_status_bar(view)

    def on_pre_close(self, view):
        self.update_status_bar(view)

    def on_selection_modified_async(self, view):
        self.update_status_bar(view)

    def on_window_command(self, window, command_name, args):
        if command_name == "hide_panel":
            self.update_status_bar(window.active_view())

