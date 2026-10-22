#!/usr/bin/env python3
"""Build with the installed SDK/JDK; no Gradle, downloads, or third-party app libs."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import zipfile
import tempfile
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent
sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
jdk = Path(os.environ["JAVA_HOME"])

def version_key(path):
    parts = re.findall(r"\d+", path.name)
    return [int(part) for part in parts] or [0]

# CI images rotate their preinstalled toolchain; prefer the pinned 34.0.0 and fall back to the
# newest usable one instead of failing on a path that no longer exists.
bt = sdk / "build-tools/34.0.0"
if not (bt / "aapt").exists():
    tools = [path for path in sdk.joinpath("build-tools").glob("*") if (path / "aapt").exists()] \
        if sdk.joinpath("build-tools").is_dir() else []
    if not tools:
        raise RuntimeError("No build-tools with aapt found under " + str(sdk / "build-tools"))
    bt = max(tools, key=version_key)
android = sdk / "platforms/android-34/android.jar"
if not android.is_file():
    platforms = sorted((path / "android.jar" for path in sdk.joinpath("platforms").glob("android-*")
                        if (path / "android.jar").is_file()), key=lambda path: version_key(path.parent)) \
        if sdk.joinpath("platforms").is_dir() else []
    if not platforms:
        raise RuntimeError("No android.jar found under " + str(sdk / "platforms"))
    android = platforms[-1]
print("SDK:", sdk, "| build-tools:", bt.name, "| android.jar:", android.parent.name)
build_root = root / "build"
build_root.mkdir(parents=True, exist_ok=True)
build = Path(tempfile.mkdtemp(prefix="v2-", dir=build_root))
dist = root / "dist"
for directory in (build / "classes", build / "dex", dist):
    directory.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, JAVA_HOME=str(jdk), PATH=str(jdk / "bin") + os.pathsep + os.environ.get("PATH", ""))
libraries = []
for aar in sorted((root / "libs").glob("*-13.1.5.aar")):
    jar = build / (aar.stem + ".jar")
    with zipfile.ZipFile(aar) as archive:
        jar.write_bytes(archive.read("classes.jar"))
    libraries.append(jar)
if len(libraries) != 4:
    raise RuntimeError("Expected pinned Shizuku api/provider/aidl/shared 13.1.5 AARs in libs/")

def run(*args, cwd=root):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)

run(jdk / "bin/javac", "-encoding", "UTF-8", "--release", "8",
    "-classpath", os.pathsep.join(str(p) for p in [android, *libraries]), "-d", build / "classes", *sorted((root / "src").rglob("*.java")))
run(jdk / "bin/jar", "cf", build / "classes.jar", "-C", build / "classes", ".")
run(bt / "d8", "--min-api", "26", "--lib", android, "--output", build / "dex", build / "classes.jar", *libraries)
run(bt / "aapt", "package", "-f", "-M", root / "AndroidManifest.xml", "-S", root / "res",
    "-A", root / "assets", "-I", android, "-F", build / "unsigned.apk")
with zipfile.ZipFile(build / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as archive:
    archive.write(build / "dex/classes.dex", "classes.dex")
run(bt / "zipalign", "-f", "4", build / "unsigned.apk", build / "aligned.apk")
key = build_root / "debug.keystore"
if not key.exists():
    run(jdk / "bin/keytool", "-genkeypair", "-keystore", key, "-storepass", "android", "-keypass", "android",
        "-alias", "qpolllab", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=QPoll Lab Local Debug", "-noprompt")
version = ET.parse(root / "AndroidManifest.xml").getroot().attrib["{http://schemas.android.com/apk/res/android}versionName"]
apk = dist / ("QPollLab-" + version + ".apk")
run(bt / "apksigner", "sign", "--ks", key, "--ks-key-alias", "qpolllab", "--ks-pass", "pass:android",
    "--key-pass", "pass:android", "--out", apk, build / "aligned.apk")
run(bt / "apksigner", "verify", "--verbose", apk)
run(bt / "aapt", "dump", "badging", apk)
digest = hashlib.sha256(apk.read_bytes()).hexdigest()
(dist / (apk.name + ".sha256")).write_text(digest + "  " + apk.name + "\n", encoding="utf-8")
print("APK:", apk)
print("SHA256:", digest)
