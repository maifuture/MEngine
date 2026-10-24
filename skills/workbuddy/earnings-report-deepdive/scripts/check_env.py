#!/usr/bin/env python3
"""环境自检：确认跑本 skill 所需的解释器、pip 与第三方库是否就位。

用法:
  python3 check_env.py                        # 检查 pypdf（本 skill 的默认依赖）
  python3 check_env.py pdfplumber             # 检查指定模块
  python3 check_env.py pdfplumber pypdf --any  # 任一可用即算通过
  python3 check_env.py --json                  # 机器可读输出

退出码: 0 = 依赖齐全；1 = 有缺失；2 = 环境异常（无 pip 等）。

本脚本**只诊断、不安装**。它会打印一条可直接执行的安装命令，
是否执行由用户授权后决定——pip install 会改动用户的 Python 环境，
未经同意不要替用户跑。
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import sysconfig

VENV_NAME = "ashare-skills"


def module_version(mod):
    """已安装则返回版本号字符串，未安装返回 None。"""
    try:
        if importlib.util.find_spec(mod) is None:
            return None
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    try:
        from importlib import metadata

        return metadata.version(mod)
    except Exception:  # noqa: BLE001 - 版本拿不到不影响"已安装"的判定
        return "unknown"


def pip_version():
    """返回 pip 版本；不可用返回 None。用当前解释器跑，保证装到同一环境。"""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return None
    parts = line[0].split()
    return parts[1] if len(parts) > 1 else "unknown"


def pep668_markers():
    """返回 PEP 668 的 EXTERNALLY-MANAGED 标记文件路径列表。

    存在即表示该解释器属于"系统托管"，pip 会拒绝直接安装
    （Debian/Ubuntu 的 python3、Homebrew 的 Python、macOS 的 CLT python3 都如此）。
    """
    major, minor = sys.version_info.major, sys.version_info.minor
    candidates = []
    for key in ("stdlib", "platstdlib", "data", "prefix"):
        try:
            path = sysconfig.get_path(key)
        except (KeyError, AttributeError):
            continue
        if not path:
            continue
        candidates.append(os.path.join(path, "EXTERNALLY-MANAGED"))
        candidates.append(os.path.join(path, "lib", f"python{major}.{minor}", "EXTERNALLY-MANAGED"))
    seen, found = set(), []
    for p in candidates:
        if p not in seen and os.path.exists(p):
            seen.add(p)
            found.append(p)
    return found


def venv_hint():
    """给出建虚拟环境的命令与实际路径（按平台区分）。"""
    if os.name == "nt":
        base = os.path.join(os.path.expanduser("~"), ".venvs", VENV_NAME)
        exe = os.path.join(base, "Scripts", "python.exe")
    else:
        base = os.path.join(os.path.expanduser("~"), ".venvs", VENV_NAME)
        exe = os.path.join(base, "bin", "python")
    return base, exe


def build_advice(modules, missing, any_mode, in_venv, pip_ok, markers):
    """按环境类型给出**一条**最合适的安装路径，而不是罗列所有可能。"""
    if not missing:
        return []
    if not pip_ok:
        return [
            "当前解释器没有可用的 pip，先补 pip 再装依赖：",
            f"  {sys.executable} -m ensurepip --upgrade",
            "若仍失败，改用系统包管理器安装 python3-pip，或直接建虚拟环境。",
        ]

    pkgs = " ".join(missing) if not any_mode else " ".join(missing)
    if in_venv:
        return [
            "当前已在虚拟环境中，直接安装即可（只影响这个环境）：",
            f"  {sys.executable} -m pip install {pkgs}",
        ]

    if markers:
        base, exe = venv_hint()
        return [
            "当前解释器受 PEP 668 保护（系统托管），直接 pip install 会被拒绝。",
            "推荐建一个专用虚拟环境，装完用它来跑脚本：",
            f"  {sys.executable} -m venv \"{base}\"",
            f"  \"{exe}\" -m pip install {pkgs}",
            "之后把命令里的 python3 换成该解释器（上面的完整路径）。",
        ]

    return [
        "当前解释器不是系统托管，建议装到用户目录（不需要管理员权限）：",
        f"  {sys.executable} -m pip install --user {pkgs}",
        "若报 externally-managed-environment，改走虚拟环境：",
        f"  {sys.executable} -m venv \"{venv_hint()[0]}\"",
        f"  \"{venv_hint()[1]}\" -m pip install {pkgs}",
    ]


def main():
    ap = argparse.ArgumentParser(description="运行本 skill 的环境自检（只诊断，不安装）")
    ap.add_argument("modules", nargs="*", default=None,
                    help="要检查的第三方模块，默认 pypdf")
    ap.add_argument("--any", action="store_true",
                    help="所列模块任一可用即算通过（用于 pdfplumber/pypdf 这类可互相替代的依赖）")
    ap.add_argument("--json", action="store_true", help="输出 JSON，便于程序解析")
    ap.add_argument("--quiet", action="store_true", help="只在有缺失时输出，用于装完复验")
    args = ap.parse_args()

    mods = args.modules or ["pypdf"]

    in_venv = sys.prefix != sys.base_prefix
    pip_ver = pip_version()
    pip_ok = pip_ver is not None
    markers = pep668_markers()

    state = {m: module_version(m) for m in mods}
    present = [m for m, v in state.items() if v is not None]
    absent = [m for m, v in state.items() if v is None]

    ok = bool(present) if args.any else not absent
    missing = [] if ok else (absent if not args.any else list(absent))

    advice = build_advice(mods, missing, args.any, in_venv, pip_ok, markers)

    if args.json:
        print(json.dumps({
            "interpreter": sys.executable,
            "version": platform_version(),
            "in_venv": in_venv,
            "pip": pip_ver,
            "pep668": bool(markers),
            "pep668_markers": markers,
            "modules": {m: {"present": v is not None, "version": v} for m, v in state.items()},
            "any_mode": args.any,
            "ok": ok,
            "missing": missing,
            "advice": advice,
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    if args.quiet and ok:
        return 0

    print("== 环境自检 ==")
    print(f"解释器    {sys.executable}")
    print(f"版本      {platform_version()}")
    print(f"虚拟环境  {'是' if in_venv else '否'}")
    print(f"pip       {pip_ver or '不可用'}")
    print(f"PEP 668   {'是（系统托管，禁止直接 pip install）' if markers else '否'}")

    print("\n依赖状态")
    for m in mods:
        v = state[m]
        if v is None:
            print(f"  {m:<14}缺失")
        else:
            print(f"  {m:<14}已安装 {v}")
    if args.any:
        print(f"  （{'、'.join(mods)} 任一可用即可）")

    if ok:
        print("\n结论：依赖齐全，可以直接运行。")
        return 0

    print(f"\n结论：缺少 {', '.join(missing)}，PDF 文本提取无法工作。")
    print("\n建议（需用户授权后再执行，本脚本不会自行安装）")
    for line in advice:
        print(f"  {line}" if not line.startswith("  ") else line)
    return 1


def platform_version():
    return "{}.{}.{}".format(*sys.version_info[:3])


if __name__ == "__main__":
    sys.exit(main())
