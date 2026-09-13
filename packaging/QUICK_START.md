# 文献书架 · 免环境版

Windows：解压完整 ZIP，双击 LiteratureShelf.exe。程序已经包含 Python、Qt 和 PDF 读取组件，无需安装开发环境。

macOS：打开 DMG，将 LiteratureShelf.app 拖到 Applications，再打开应用。分别提供 Apple Silicon 和 Intel 版本。

Linux：解压 tar.gz，在桌面文件管理器中双击 LiteratureShelf；也可双击“启动文献书架.sh”。面向 Ubuntu 22.04/24.04 等现代 x86_64 桌面系统；不是面向所有发行版、服务器或 ARM 的通用二进制。

第一次打开默认使用本地规则和 BM25 搜索，可以离线添加、管理和搜索文献。需要大模型整理或 Agent 搜索时，在设置中填写自己的接口地址、模型名称和 API Key。安装包不包含任何人的 API 密钥或文献库。

程序升级不会清空数据。数据及生成的分类目录位于：
- Windows：%LOCALAPPDATA%\LiteratureShelf
- macOS：~/Library/Application Support/LiteratureShelf
- Linux：~/.local/share/LiteratureShelf（设置了 XDG_DATA_HOME 时使用其对应目录）

原版项目中的 data/library.sqlite3 和 模型接口/api.json 不会打入发布包。迁移时关闭程序，将它们分别复制到以上用户数据目录下同名子目录中；旧配置的 API 文件路径需要在设置中重新选择。原文依然按原路径引用，换电脑后可重新关联。

这些构建未使用付费代码签名证书，也未经过 Apple 公证，因此系统可能要求首次确认来源。请仅运行自己仓库构建的文件，不要全局关闭系统安全保护。

磁盘分类：Windows 使用 .url 原文快捷方式；macOS、Linux 使用符号链接，原文件不复制。分类操作请通过应用完成。

全文分析：选择文献后，在总结面板或“…”菜单点击“全文 Agent 分析 / 再次分析”。程序按段读取全部可提取文字，并按论文十问生成总结，Agent 可回读关键段落。设置的“全文分析”页可修改问题和每篇调用上限。默认复用本地阅读笔记减少消耗，取消勾选可重新阅读全文。此模式需要模型 API，输入和输出都可能计费；普通整理仍使用摘要、结论等有限信息。扫描件需先 OCR，图片与复杂公式需要人工核对。

总结历史：重新生成或编辑总结会保存旧版本，点击“总结历史版本”可恢复。分析失败不会覆盖原总结。

编辑：支持 Markdown，工具栏提供加粗、斜体和公式；Ctrl+B / Ctrl+I 可快速编辑。使用 $x_i^2$ 或 $$\frac{a}{b}$$ 输入 LaTeX 数学公式。“排版预览”在默认浏览器中离线排版，组件已包含在安装包内。支持数学语法，不编译完整 LaTeX 文档。
