"""updates: the GitHub version check."""
import json
import sys
import unittest
import tempfile
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import netfetch  # noqa: E402
import updates  # noqa: E402


class FakeNet:
    def __init__(self, body=None, err=None):
        self.body, self.err, self.urls = body, err, []

    def get(self, url, kind="demand"):
        self.urls.append((url, kind))
        if self.err:
            raise self.err

        class R:
            pass
        r = R()
        r.body = self.body.encode()
        return r


class UpdatesTest(unittest.TestCase):
    def test_explicit_repository_and_disabled_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(updates.repository(root, {}), "")
            Path(root, "UPDATE_REPO").write_text("owner/fork\n", encoding="utf-8")
            self.assertEqual(updates.repository(root, {}), "owner/fork")
            self.assertEqual(updates.repository(root, {"BNGE_UPDATE_REPO": "another/fork"}), "another/fork")
            for bad in ("", "off", "https://github.com/owner/fork", "owner/fork/other", "../fork", "owner/fork?x=1"):
                self.assertEqual(updates.repository(root, {"BNGE_UPDATE_REPO": bad}), "", bad)

    def test_disabled_channel_does_not_fetch(self):
        net = FakeNet("9.9.9")
        with mock.patch.object(updates, "REPO", ""):
            self.assertIsNone(updates.check("1.0.0", net))
        self.assertEqual(net.urls, [])

    def test_newer(self):
        self.assertTrue(updates.newer("1.4.1-preview", "1.4.0-preview"))
        self.assertTrue(updates.newer("1.4.0", "1.4.0-preview"), "a release is newer than its preview")
        self.assertFalse(updates.newer("1.4.0-preview", "1.4.0"))
        self.assertFalse(updates.newer("1.4.0-preview", "1.4.0-preview"))
        self.assertTrue(updates.newer("1.10.0", "1.9.3"), "numbers, not text")
        self.assertFalse(updates.newer("<html>404</html>", "1.0.0"), "not a version: no update")

    def test_check(self):
        net = FakeNet(json.dumps({"tag_name": "v1.5.0-preview"}))
        r = updates.check("1.4.0-preview", net)
        self.assertEqual(r["latest"], "1.5.0-preview")
        self.assertEqual(r["url"], updates.PAGE_URL)
        self.assertEqual(net.urls[0], (updates.RELEASE_URL, "background"))
        self.assertIsNone(updates.check("1.5.0-preview", net), "up to date")
        self.assertIsNone(updates.check("1.4.0", FakeNet(err=netfetch.FetchError("offline"))), "offline: nothing")
        self.assertIsNone(updates.check(None, FakeNet("2.0.0")), "no local VERSION: nothing")

    def test_published_commit_build_notice(self):
        current = "1.6.1-beta-build.9.gabcdef123456"
        latest = "1.6.1-beta-build.10.g123456abcdef"
        payload = {"tag_name": "v" + latest, "draft": False, "prerelease": False}
        self.assertEqual(updates.check(current, FakeNet(json.dumps(payload)))["latest"], latest)
        for bad in ("", "<html>error</html>", "[]", "{}", json.dumps({**payload, "draft": True}),
                    json.dumps({**payload, "prerelease": True})):
            self.assertIsNone(updates.check(current, FakeNet(bad)))

    def test_commit_builds_order_numerically_and_do_not_loop(self):
        for base in ("1.6.1", "1.6.1-beta"):
            a, b = base + "-build.9.gabcdef123456", base + "-build.10.g123456abcdef"
            self.assertTrue(updates.newer(a, base))
            self.assertTrue(updates.newer(b, a))
            self.assertFalse(updates.newer(a, b))
            self.assertFalse(updates.newer(b, b))
            self.assertFalse(updates.newer(base, b))
            self.assertTrue(updates.newer("1.6.2", b))


if __name__ == "__main__":
    unittest.main()


class OpenPageTest(unittest.TestCase):
    """The update notice's 'Open releases page' (the game's openWebBrowser does nothing on native Linux, 2026-10-18):
    the server opens this project's GitHub pages only."""

    def test_only_the_projects_pages(self):
        calls = []
        with mock.patch.object(updates.os, "startfile", create=True):
            ok = updates.open_page(updates.PAGE_URL, popen=lambda *a, **k: calls.append(a))
        if updates.sys.platform != "win32":
            self.assertTrue(ok)
            self.assertEqual(calls[0][0][1], updates.PAGE_URL)
        for bad in ("https://evil.example/x", "https://github.com/someone/else", None, 5,
                    updates.PAGE_URL + "\" && bad"):
            self.assertFalse(updates.open_page(bad, popen=lambda *a, **k: calls.append(a)), bad)
