#!/usr/bin/env python3
import os
import subprocess

from pi_atc_config import user_config_dir

UNIT_NAME = "atis-relay.service"
AUTOSTART_NAME = "atis-relay.desktop"


def autostart_dir():
    config_home = os.environ.get("XDG_CONFIG_HOME")

    if isinstance(config_home, str) and config_home.strip():
        base = config_home.strip()

    else:
        base = os.path.join(os.path.expanduser("~"), ".config")

    return os.path.join(base, "autostart")


def autostart_path():
    return os.path.join(autostart_dir(), AUTOSTART_NAME)


def autostart_desktop_text():
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=ATIS Relay",
        "Comment=Start ATIS Relay when you sign in",
        "Exec=atis-relay --worker",
        "Icon=atis-relay",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


def _systemctl_user(args):
    env = os.environ.copy()
    runtime = env.get("XDG_RUNTIME_DIR")

    if not runtime:
        env["XDG_RUNTIME_DIR"] = "/run/user/%s" % os.getuid()

    try:
        return subprocess.run(
            ["systemctl", "--user"] + list(args),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    except OSError:
        return None


def is_autostart_enabled():
    if os.path.isfile(autostart_path()):
        return True

    res = _systemctl_user(["is-enabled", UNIT_NAME])

    if res is None:
        return False

    return res.returncode == 0


def is_worker_active():
    res = _systemctl_user(["is-active", UNIT_NAME])

    if res is None:
        return False

    return res.returncode == 0 and (res.stdout or "").strip() == "active"


def enable_autostart():
    os.makedirs(autostart_dir(), exist_ok=True)
    path = autostart_path()
    with open(path, "w") as f:
        f.write(autostart_desktop_text())

    os.chmod(path, 0o644)
    _systemctl_user(["daemon-reload"])
    res = _systemctl_user(["enable", "--now", UNIT_NAME])
    return res is None or res.returncode == 0 or os.path.isfile(path)


def disable_autostart():
    try:
        os.remove(autostart_path())

    except OSError:
        pass

    _systemctl_user(["disable", "--now", UNIT_NAME])
    return True


def start_worker():
    res = _systemctl_user(["start", UNIT_NAME])

    if res is not None and res.returncode == 0:
        return True

    return _spawn_worker()


def stop_worker():
    _systemctl_user(["stop", UNIT_NAME])
    return True


def restart_worker():
    res = _systemctl_user(["restart", UNIT_NAME])

    if res is not None and res.returncode == 0:
        return True

    return start_worker()


def _spawn_worker():
    try:
        subprocess.Popen(
            ["atis-relay", "--worker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    except OSError:
        return False


def user_unit_dropin_dir():
    return os.path.join(user_config_dir(), "systemd")
