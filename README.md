# Matrix Desk for macOS

多平台矩阵工作台的 macOS 开源源码，当前应用版本 **v2.8.3**。主要面向本地账号管理、批量发布、作品与评论采集；Android 设备工具独立运行。

## 核心功能

- **账号工作区**：按账号保存独立登录状态，统一列表切换。macOS 14+ 可在工作台内直接操作 WebKit 网页，总览最多展示 4 个已打开页面；也保留独立 Chromium 浏览器入口。
- **批量发布**：导入 DOCX 文案，保留原标题与正文，按顺序分配给勾选账号并串行处理。小红书文字配图使用平台已有入口和模板；生成或提交结果无法确认时暂停。
- **数据采集**：导入分享文本、作品链接与主页链接，主页默认取最近 20 条作品，保存实际读取到的评论，支持暂停、恢复与失败记录。
- **辅助工具**：消息页、账号指标与每日变化、手动启动的浏览计划；Android 工具提供多设备预览、选中设备实时控制和显式剪贴板同步。

本项目不隶属于小红书、rednote 或抖音。请只操作自己有权管理的账号及数据；遇到登录验证、验证码、访问频率或账号异常提示时暂停，按平台流程人工处理。工具不提供验证码绕过或限制规避。

## 快速开始

推荐 **macOS 14+、Python 3.12**。首次安装需要联网下载 Python 依赖和 Chromium；原生内嵌网页需要 macOS 14+。Apple Silicon 是主要本地验证环境，其他 Mac 硬件需要自行验证。

```sh
git clone https://github.com/qwertyuiop15655750950-lab/matrix-desk-macos.git
cd matrix-desk-macos
bash scripts/install_macos.sh
```

然后双击根目录的 `启动.command`，或按 [Mac 安装说明](docs/macos-install.md) 从终端启动。安装入口只安装运行环境；首次平台登录由用户完成。

手机工具在相邻目录 `android-device-desk` 中独立运行，需要启用 USB 调试并授权电脑，以及自行安装 ADB / scrcpy。实时预览依赖可选的 PyAV，见安装说明。

## 项目结构

```text
xhs-matrix-desk-v2.7.7/   # 桌面矩阵；目录名沿用历史名称，程序版本为 2.8.3
  app/                  # 平台 API、账号存储、发布/采集服务、网页界面
  tests/                # 离线 Python 与前端测试
android-device-desk/    # 独立 Android 设备工具
scripts/                # macOS 安装和启动入口
docs/                   # 安装与开发说明
```

两套工具使用各自的虚拟环境和数据目录。账号数据库、Cookie、网站存储、截图、日志、素材、代理配置、外部工具与 Python 虚拟环境均不随源码发布。更新旧安装时保留原来的 `data/`；不要将空数据目录覆盖到现有账号目录。

## 开发与验证

```sh
cd xhs-matrix-desk-v2.7.7
./venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.js
```

手机工具在自己的目录使用同样的测试命令。根目录安装脚本测试：`python3.12 -m unittest discover -s tests -p test_macos_scripts.py`。Node 仅用于前端测试。`bb-browser` / Crawl4AI 桥接保留为可选开发参考，核心运行不依赖这两个外部项目，未安装时集成测试跳过。

原生窗口切换、独立存储、输入和文件选择曾在本地验证；实际登录迁移、平台模板生成、最终发布、个人号消息读取和多手机长时间同步仍需实站验证。公开源码不代表所有平台流程已通过长期稳定性测试；平台页面变化也可能需要更新选择器。读取到的评论数量不保证等于平台的全部评论。

本机任务接口默认只监听 `127.0.0.1:8901`，保持默认启动行为。它尚无认证和跨机器统一消息能力，不应向公网转发。

## 开源与贡献

自有源码采用 [MIT License](LICENSE)。外部工具各自的许可证与致谢见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。欢迎提交问题与改进；提问题时请隐去账号标识、Cookie、代理订阅、消息内容和其他个人信息。
