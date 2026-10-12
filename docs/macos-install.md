# macOS 安装与启动

本仓库是源码，首次安装需要网络。建议 macOS 14 或更新版本、Python 3.12；内嵌账号浏览器依赖 macOS 14 的 WebKit 会话隔离功能。未在所有 Mac 型号上验证。

1. 下载整个仓库并解压，保持两个程序目录与根目录 `scripts` 相邻。
2. 安装 Python 3.12。已有 Homebrew 时可执行 `brew install python@3.12`，也可以使用 Python 官网的 3.12 安装程序。
3. 双击根目录 `安装.command`。安装两个独立的 Python 环境，以及桌面矩阵所需的 Playwright Chromium。
4. 双击 `启动.command` 打开矩阵工作台；手机工具使用 `启动手机工具.command`。首次平台登录需要你在平台页面完成。

若 Finder 提示没有执行权限，可在终端进入仓库目录后执行：

```bash
chmod +x *.command scripts/*.sh
bash scripts/install_macos.sh
bash scripts/launch_macos.sh desktop
```

安装脚本不会修改 `data`、账号数据库或浏览器登录目录。已有虚拟环境必须是可用的 Python 3.12；版本不匹配时请先将 `venv` 改名备份，再重新安装，不要改动 `data`。

## 选择安装范围

```bash
bash scripts/install_macos.sh --check          # 系统、Python 和目录预检，不联网
bash scripts/install_macos.sh --desktop        # 只安装矩阵工作台
bash scripts/install_macos.sh --phone          # 只安装手机工具
bash scripts/install_macos.sh --phone --with-live  # 手机工具及可选的 PyAV 解码
```

Python 没有在常见路径时，用实际路径指定：

```bash
MATRIX_PYTHON="/path/to/python3.12" bash scripts/install_macos.sh
```

## 手机工具

仓库不携带 ADB、scrcpy 或其他第三方二进制。已有 Homebrew 时可自行安装：

```bash
brew install android-platform-tools scrcpy
```

启动脚本会从 Homebrew 的常见路径查找 `adb` 与 `scrcpy`，也支持显式设置 `MATRIX_ADB_PATH`、`MATRIX_SCRCPY_PATH`。手机需开启 USB 调试并允许本电脑；请先用 `adb devices` 检查连接。

普通设备管理与外部 scrcpy 投屏不依赖 PyAV。**工作台内实时画面目前只适配 scrcpy 5.0.1**，并要求其 `scrcpy-server` 与指定的 `scrcpy` 可执行文件在同一目录；Homebrew 安装的版本或目录布局可能不符合这一要求。此时使用外部投屏，不应把安装成功视为实时控制已验证。需要内嵌实时画面时，请自行从 [scrcpy 官方发布页](https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1) 获取匹配版本，并设置工具路径。

剪贴板同步通过界面中的明确操作发送文本，不会自动向聊天对象发送消息。平台验证码、账号限制和登录过期需要人工处理。

## 更新源码

退出程序后再更新源码。保留两个程序自己的 `data`、`venv` 和本机配置文件。依赖变化时重新运行安装脚本；请勿将个人账号数据提交到 GitHub。
