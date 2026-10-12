# Android 设备工作台（独立源码）

手机控制已从桌面矩阵分开：使用自己的启动入口、Python 环境、设备网络配置数据库及测试。不会读取桌面账号数据库和浏览器 profile；不自动启动投屏或应用手机代理。

## 2026-10-10 实时操作更新

「手机总览」将多台设备放在同一页面，每页最多八台。卡片的「实时操控」在本页显示选中手机的视频；同一时刻只开启一个实时视频连接，其余设备保留按需或低频截图。支持点击、拖动、返回、主页与放大查看。

电脑复制文字后，先点手机中的输入框，再点「粘贴电脑剪贴板」（macOS），或在手机画面中按 ⌘V。文字框也可接收粘贴并显式发送到当前手机。仅同步文字，不同步图片或文件；不持续读取电脑或手机剪贴板。程序仅请求手机粘贴，不向 App 自动提交内容。

实时视频额外安装：`./venv/bin/python -m pip install -r requirements-live.txt`。使用 scrcpy 5.0.1 的 server 和 PyAV 16，版本不匹配时会提示使用原投屏工具。缺少实时解码组件不影响普通设备管理。协议参考 [scrcpy 开发文档](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/develop.md)。

目标视频 24 帧/秒，工作台画面显示最多约 10 帧/秒；仅保留最新一帧，不录制、不缓存视频历史。界面显示的「画面解码后」是主机帧新鲜度，不能视作完整触控到显示延迟。切换手机或退出实时面板会关闭此前连接；连接闲置会停止。

已有本地设备测试覆盖实时画面、主屏触控以及中文电脑剪贴板读取；手机应用内的中文粘贴、完整输入流程和多手机长期运行仍需逐机验证。仓库不包含个人设备标识或测试时的手机画面。

## 运行

使用已安装的 Python 3.10+（推荐 3.12），在本目录创建环境：

```sh
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python run.py
```

已有环境也可通过 `MATRIX_DEVICE_PYTHON` 指定，再双击「启动.command」。首次安装需网络。ADB 和 scrcpy 使用同级 dev-tools，或 `MATRIX_ADB_PATH`、`MATRIX_SCRCPY_PATH` 显式指定。旧电脑已安装的工具可复用，不需要复制账号数据。

保留设备发现、按需截图/投屏、尺寸检测、已预览草稿的明确发送操作和代理配置恢复。手机端浏览计划冻结。此独立应用不自动迁移桌面账号数据库，也不会在启动时修改手机网络。

## 测试

```sh
./venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_android_ui.js tests/test_android_network_ui.js
```

模拟测试通过不代表各机型应用实测通过。实际投屏和聊天发送仍需逐机检查；没有对真实手机执行发送。详情见手机群控使用说明。
