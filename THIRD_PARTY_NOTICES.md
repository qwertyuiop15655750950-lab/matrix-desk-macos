# Third-party notices

本仓库不打包以下项目的源码、可执行文件或账号数据。安装器通过各自的包分发渠道安装依赖；使用外部工具时请遵守该工具的许可证。

| 项目 | 用途 | 许可证 / 上游 |
| --- | --- | --- |
| pywebview | 本地桌面窗口 | BSD-3-Clause；[上游](https://github.com/r0x0r/pywebview/blob/master/LICENSE) |
| Playwright Python / Chromium | 独立账号浏览器 | Apache-2.0 / Chromium 各组件许可证；[Playwright](https://github.com/microsoft/playwright-python/blob/main/LICENSE)、[Chromium](https://chromium.googlesource.com/chromium/src/+/main/LICENSE) |
| Pillow | 图片处理 | HPND；[上游](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| PyObjC | macOS 原生 WebKit 桥接（pywebview 依赖） | MIT；[上游](https://github.com/ronaldoussoren/pyobjc/blob/main/pyobjc-framework-Cocoa/LICENSE.txt) |
| PyAV / FFmpeg | 可选实时手机画面解码 | BSD-3-Clause / 构建配置对应许可证；[PyAV](https://github.com/PyAV-Org/PyAV/blob/master/LICENSE.txt)、[FFmpeg](https://ffmpeg.org/legal.html) |
| scrcpy | 手机屏幕与控制协议 | Apache-2.0；[v5.0.1](https://github.com/Genymobile/scrcpy/blob/v5.0.1/LICENSE) |
| Android Platform Tools (ADB) | USB 设备连接 | Android 工具各组件许可证；[官方说明](https://developer.android.com/tools/releases/platform-tools) |
| bb-browser | 可选桥接参考 | MIT；[上游](https://github.com/epiral/bb-browser/blob/main/LICENSE) |
| Crawl4AI | 可选桥接参考 | Apache-2.0，附上游 Attribution Requirement；[完整许可证](https://github.com/unclecode/crawl4ai/blob/main/LICENSE) |

感谢 **UncleCode / Crawl4AI** 与 **epiral / bb-browser** 提供开源浏览器与采集工具。核心采集实现独立运行；可选桥接的部署依赖和许可证不由本仓库的 MIT 许可证替代。
