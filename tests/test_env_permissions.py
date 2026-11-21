#!/usr/bin/env python3
import errno
import inspect
import os
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pi_atc_config as config


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class EnvFileAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "env")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_ok_missing_and_unreadable(self):
        self.assertEqual(config.env_file_access(self.path), "missing")
        with open(self.path, "w") as env_file:
            env_file.write("STATIONS=KMYF:126.9\n")
        self.assertEqual(config.env_file_access(self.path), "ok")
        os.chmod(self.path, 0o000)

        try:
            self.assertEqual(config.env_file_access(self.path), "unreadable")

        finally:
            os.chmod(self.path, 0o640)

    def test_permission_error_from_open_is_unreadable(self):
        with patch("builtins.open", side_effect=PermissionError("denied")):
            self.assertEqual(config.env_file_access(self.path), "unreadable")


class InstallingUserTest(unittest.TestCase):
    def test_sudo_user(self):
        with patch.dict(os.environ, {"SUDO_USER": "desktop", "PKEXEC_UID": ""}, clear=False):
            self.assertEqual(config.installing_desktop_user(), "desktop")

    def test_pkexec_uid(self):
        fake = MagicMock()
        fake.pw_name = "desktop"
        with patch.dict(os.environ, {"SUDO_USER": "", "PKEXEC_UID": "1001"}, clear=False):
            with patch("pwd.getpwuid", return_value=fake):
                self.assertEqual(config.installing_desktop_user(), "desktop")

    def test_current_user_when_not_root(self):
        fake = MagicMock()
        fake.pw_name = "desktop"
        with patch.dict(os.environ, {"SUDO_USER": "", "PKEXEC_UID": ""}, clear=False):
            with patch("os.geteuid", return_value=1001):
                with patch("pwd.getpwuid", return_value=fake):
                    self.assertEqual(config.installing_desktop_user(), "desktop")

    def test_unknown_root_returns_none(self):
        with patch.dict(os.environ, {"SUDO_USER": "root", "PKEXEC_UID": ""}, clear=False):
            with patch("os.geteuid", return_value=0):
                self.assertIsNone(config.installing_desktop_user())

    def test_rejects_invalid_names(self):
        self.assertFalse(config._valid_desktop_user("root"))
        self.assertFalse(config._valid_desktop_user("nobody"))
        self.assertFalse(config._valid_desktop_user("atis-relay"))
        self.assertFalse(config._valid_desktop_user("../evil"))
        self.assertFalse(config._valid_desktop_user("desktop;rm"))
        self.assertTrue(config._valid_desktop_user("desktop"))


class AddUserToGroupTest(unittest.TestCase):
    def test_skips_invalid_and_non_root(self):
        with patch("os.geteuid", return_value=0):
            self.assertFalse(config.add_user_to_service_group("root"))
            self.assertFalse(config.add_user_to_service_group("../evil"))

        with patch("os.geteuid", return_value=1001):
            self.assertFalse(config.add_user_to_service_group("desktop"))

    def test_adduser_when_root(self):
        with patch("os.geteuid", return_value=0):
            with patch("pi_atc_config._user_in_service_group", side_effect=[False, True]):
                with patch("subprocess.run") as run:
                    run.return_value = MagicMock(returncode=0)
                    self.assertTrue(config.add_user_to_service_group("desktop"))
                    run.assert_called()
                    self.assertEqual(run.call_args[0][0][:2], ["adduser", "desktop"])


class SystemEnvPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "env")
        with open(self.path, "w") as env_file:
            env_file.write("STATIONS=KMYF:126.9\nSTATION_TOKEN=secret-station-token\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_env_file_mode_is_not_world_readable(self):
        self.assertEqual(config.ENV_FILE_MODE, 0o640)
        self.assertEqual(config.ENV_FILE_MODE & 0o004, 0)

    def test_write_env_file_uses_640(self):
        config.write_env_file(self.path, {"STATIONS": "KMYF:126.9"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)

    def test_system_path_chowns_root_service_group_and_adds_user(self):
        with patch("pi_atc_config.SYSTEM_ENV_PATH", self.path):
            with patch("pi_atc_config._chown_env_file") as chown_env:
                with patch("pi_atc_config.add_user_to_service_group") as add_user:
                    with patch("pi_atc_config.installing_desktop_user", return_value="desktop"):
                        config.write_env_file(self.path, {"STATIONS": "KMYF:126.9"})

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        chown_env.assert_any_call(self.path)
        add_user.assert_called_with("desktop")

    def test_chown_env_file_uses_root_and_service_group(self):
        group = MagicMock()
        group.gr_gid = 42
        with patch("grp.getgrnam", return_value=group):
            with patch("os.chown") as chown:
                self.assertTrue(config._chown_env_file(self.path))

        chown.assert_called_with(self.path, 0, 42)

    def test_apply_permissions_on_existing_file(self):
        os.chmod(self.path, 0o600)
        with patch("pi_atc_config.SYSTEM_ENV_PATH", self.path):
            with patch("pi_atc_config._chown_env_file") as chown_env:
                with patch("pi_atc_config.add_user_to_service_group"):
                    config.apply_system_env_permissions(self.path, user="desktop")

        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        chown_env.assert_any_call(self.path)
        chown_env.assert_any_call(os.path.dirname(self.path))

    def test_apply_permissions_does_not_give_the_service_user_the_env_dir(self):
        with patch("pi_atc_config.SYSTEM_ENV_PATH", self.path):
            with patch("pi_atc_config._chown_env_file"):
                with patch("pi_atc_config._chown_service_user") as chown_user:
                    with patch("pi_atc_config.add_user_to_service_group"):
                        config.apply_system_env_permissions(self.path, user="desktop")

        chown_user.assert_not_called()

    def test_write_env_file_uses_exclusive_nofollow_create(self):
        source = inspect.getsource(config.write_env_file)
        self.assertIn("O_CREAT", source)
        self.assertIn("O_EXCL", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("fsync", source)

    def test_write_env_file_refuses_dest_symlink(self):
        victim = os.path.join(self.tmpdir.name, "victim")
        with open(victim, "w") as handle:
            handle.write("unchanged\n")
        os.remove(self.path)
        os.symlink(victim, self.path)

        with self.assertRaises(OSError) as raised:
            config.write_env_file(self.path, {"STATIONS": "KMYF:126.9"})

        self.assertEqual(raised.exception.errno, errno.ELOOP)
        with open(victim) as handle:
            self.assertEqual(handle.read(), "unchanged\n")
        self.assertTrue(os.path.islink(self.path))

    def test_write_env_file_does_not_follow_tmp_symlink(self):
        victim = os.path.join(self.tmpdir.name, "victim")
        with open(victim, "w") as handle:
            handle.write("unchanged\n")
        os.symlink(victim, self.path + ".tmp")
        config.write_env_file(self.path, {"STATIONS": "KMYF:126.9"})
        with open(victim) as handle:
            self.assertEqual(handle.read(), "unchanged\n")
        self.assertTrue(os.path.isfile(self.path))
        self.assertFalse(os.path.islink(self.path))
        self.assertEqual(config.read_env_file(self.path)["STATIONS"], "KMYF:126.9")
        self.assertTrue(os.path.islink(self.path + ".tmp"))

    def test_postinst_applies_system_env_permissions_on_upgrade(self):
        with open(os.path.join(ROOT, "packaging", "postinst")) as postinst_file:
            postinst = postinst_file.read()

        self.assertIn("apply_system_env_permissions", postinst)
        self.assertNotIn("chmod 600 /etc/atis-relay/env", postinst)
        self.assertIn("chown root:atis-relay /etc/atis-relay", postinst)
        self.assertIn("chown atis-relay:atis-relay /var/lib/atis-relay", postinst)
        self.assertNotIn("chown atis-relay:atis-relay /etc/atis-relay", postinst)

    def test_tmpfiles_env_dir_is_root_service_group(self):
        with open(os.path.join(ROOT, "packaging", "atis-relay.tmpfiles")) as tmpfiles_file:
            tmpfiles = tmpfiles_file.read()

        self.assertIn("d /etc/atis-relay 0750 root atis-relay -", tmpfiles)
        self.assertNotIn("d /etc/atis-relay 0750 atis-relay atis-relay -", tmpfiles)

    def test_recorder_state_stays_out_of_etc(self):
        self.assertEqual(config.SYSTEM_STATE_DIR, "/var/lib/atis-relay")
        self.assertEqual(config.SYSTEM_ENV_PATH, "/etc/atis-relay/env")

        with open(os.path.join(ROOT, "packaging", "atis-relay.service")) as unit_file:
            unit = unit_file.read()

        self.assertIn("Environment=PI_ATC_ENV_FILE=/etc/atis-relay/env", unit)
        self.assertIn("Environment=PI_ATC_STATE_DIR=/var/lib/atis-relay", unit)
        self.assertIn("User=atis-relay", unit)


if __name__ == "__main__":
    unittest.main()
